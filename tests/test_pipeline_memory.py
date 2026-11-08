"""Memory behaviour of the PDF pipeline.

Two full documents used to be held in RAM unnecessarily: the serialized
working copy handed to ``translate_patch``, and the finished mono/dual
documents captured as bytes before being written.  These tests pin their
replacements — compact page masks and output written straight to its final
path — while keeping the public byte-returning API intact.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import assets  # noqa: E402
from pdfxlate.pdf import pipeline, ultrafast  # noqa: E402


# --- mask width ------------------------------------------------------------


@pytest.mark.parametrize(
    ("regions", "dtype"),
    [
        (0, np.uint8),
        (7, np.uint8),
        (254, np.uint8),
        (255, np.uint16),
        (300, np.uint16),
    ],
)
def test_mask_dtype_switches_before_uint8_overflows(regions, dtype):
    # Labels run 2..regions+1, so 254 regions still fit uint8 (max label 255)
    # and 255 regions would overflow it.
    assert pipeline._mask_dtype(regions) == np.dtype(dtype)


class _TextItem:
    def __init__(self, x, y, width=5, height=2, font_size=2, rotation=0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.font_size = font_size
        self.rotation = rotation


class _Page:
    def __init__(self, width, height, pageno=0):
        self.cropbox = pymupdf.Rect(0, 0, width, height)
        self.pageno = pageno


def _items(count):
    # One item every 5pt, entirely separate under layout_mask's grouping rules.
    return [_TextItem(10, 10 + 5 * index) for index in range(count)]


def test_layout_mask_keeps_labels_and_narrows_the_dtype():
    items = _items(3)

    mask = ultrafast.layout_mask(_Page(100, 40), items)

    assert mask.dtype == np.uint8
    # The untouched background plus one label per region.
    assert set(np.unique(mask)) == {0, 2, 3, 4}
    # Items are sorted top-down, so the lowest one carries the highest label.
    assert [int(mask[10 + 5 * index, 10]) for index in range(3)] == [4, 3, 2]


def test_layout_mask_uses_uint16_from_255_regions():
    items = _items(255)

    mask = ultrafast.layout_mask(_Page(100, 20 + 5 * len(items)), items)

    assert mask.dtype == np.uint16
    assert int(mask.max()) == 256, "the label wrapped around in a narrow dtype"
    assert len(np.unique(mask)) == 256


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
def test_converter_class_comparisons_hold_for_compact_masks(dtype):
    """converter.py compares a mask value with 0, 2 and the previous class."""
    mask = np.zeros((2, 2), dtype=dtype)
    mask[0, 0] = 0  # preserved region
    mask[0, 1] = 2  # first translatable region

    preserved = mask[0, 0]
    translatable = mask[0, 1]
    assert bool(preserved == 0) is True
    assert bool(translatable == 0) is False
    assert bool(translatable >= 2) is True

    # The converter starts with xt_cls = -1, then carries a mask value over.
    xt_cls = -1
    assert bool(translatable == xt_cls) is False
    xt_cls = int(translatable)
    assert bool(translatable == xt_cls) is True


# --- end-to-end over a real (tiny) PDF ------------------------------------


class _FlakyDocument:
    """Fails the first save, succeeds on the retry, recording both options."""

    def __init__(self):
        self.calls: list[dict] = []

    def save(self, target, **options):
        self.calls.append(options)
        if len(self.calls) == 1:
            raise RuntimeError("cannot parse object")
        target.write(b"%PDF-fallback")


def test_save_document_retries_with_garbage_4_without_duplicate_keywords():
    """Direct-to-disk saves pass garbage=3, so the fallback must override it."""
    doc = _FlakyDocument()
    sink = io.BytesIO()
    sink.write(b"partial")

    pipeline._save_document(doc, sink, deflate=True, garbage=3, use_objstms=1)

    assert doc.calls == [
        {"deflate": True, "garbage": 3, "use_objstms": 1},
        {"deflate": True, "garbage": 4, "use_objstms": 1, "clean": True},
    ]
    assert sink.getvalue() == b"%PDF-fallback"


class _IdentityTranslator:
    lang_in = "en"
    lang_out = "zh-cn"

    def translate(self, text: str) -> str:
        return text


class _PageLayout:
    boxes: list = []
    names: list = []


class _NoRegionModel:
    """Stand-in for the ONNX layout model: no protected regions."""

    def predict(self, image, imgsz=0):
        return [_PageLayout()]


@pytest.fixture
def font(monkeypatch):
    """Use the already-downloaded asset instead of touching the network."""
    target = assets.font_asset("zh-cn").target
    if not target.exists():
        pytest.skip("target-language font is not available offline")
    monkeypatch.setattr(pipeline, "download_font", lambda lang: str(target))
    return target


def _make_pdf(path: Path, pages: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    try:
        for index in range(pages):
            page = doc.new_page(width=400, height=300)
            page.insert_text((40, 80), f"Hello page {index + 1}. A small paragraph.")
        doc.save(path)
    finally:
        doc.close()
    return path


def _translate(source: Path, output: Path):
    return pipeline.translate(
        [str(source)],
        translator=_IdentityTranslator(),
        model=_NoRegionModel(),
        output=str(output),
        thread=1,
    )


def test_translate_writes_straight_into_the_output_files(tmp_path, font, monkeypatch):
    source = _make_pdf(tmp_path / "src" / "paper.pdf", pages=3)
    seen: dict = {}
    real = pipeline.translate_stream

    def spy(stream, **kwargs):
        seen["mono_sink"] = kwargs.get("_mono_sink")
        seen["dual_sink"] = kwargs.get("_dual_sink")
        seen["result"] = real(stream, **kwargs)
        return seen["result"]

    monkeypatch.setattr(pipeline, "translate_stream", spy)

    [(mono_path, dual_path)] = _translate(source, tmp_path / "out")

    assert Path(mono_path).stat().st_size > 0
    assert Path(dual_path).stat().st_size > 0
    with pymupdf.open(mono_path) as doc:
        assert doc.page_count == 3
    with pymupdf.open(dual_path) as doc:
        assert doc.page_count == 6

    # The files themselves were the sinks; nothing was returned as bytes for
    # translate() to copy to disk afterwards.
    assert isinstance(seen["mono_sink"], pipeline._FileSink)
    assert isinstance(seen["dual_sink"], pipeline._FileSink)
    assert not isinstance(seen["mono_sink"], io.BytesIO)
    assert not isinstance(seen["dual_sink"], io.BytesIO)
    assert seen["result"] == (b"", b"")


def test_translate_stream_still_returns_openable_bytes(tmp_path, font):
    source = _make_pdf(tmp_path / "paper.pdf", pages=2)

    mono, dual = pipeline.translate_stream(
        source.read_bytes(),
        translator=_IdentityTranslator(),
        model=_NoRegionModel(),
        thread=1,
    )

    assert isinstance(mono, bytes) and isinstance(dual, bytes)
    assert mono.startswith(b"%PDF-") and dual.startswith(b"%PDF-")
    with pymupdf.open(stream=mono, filetype="pdf") as doc:
        assert doc.page_count == 2
    with pymupdf.open(stream=dual, filetype="pdf") as doc:
        assert doc.page_count == 4


def test_second_run_adds_a_numbered_pair(tmp_path, font):
    source = _make_pdf(tmp_path / "paper.pdf", pages=1)
    output = tmp_path / "out"

    first = _translate(source, output)[0]
    second = _translate(source, output)[0]

    assert Path(first[0]).name == "paper-mono.pdf"
    assert Path(first[1]).name == "paper-dual.pdf"
    assert Path(second[0]).name == "paper-mono (2).pdf"
    assert Path(second[1]).name == "paper-dual (2).pdf"
    assert all(Path(path).exists() for path in (*first, *second))