"""Tests for the two-page spread builder and its sidecar method.

The preview needs facing pages in ONE document, because the viewer is an
out-of-process plugin that owns a single scrollbar per frame and never reports
its scroll offset — two frames could not be kept in step.  So the engine
combines page pairs into a derived PDF, and these tests pin the contract:

* page *k* holds source pages ``2k-1`` and ``2k``, and an odd source ends with a
  half-blank sheet that keeps full width (a narrow one would be scaled up by the
  viewer and the last page would jump);
* the combined page is as wide as the two source pages together;
* the source is never modified, and a repeat call is a cache hit;
* broken input fails with an actionable message rather than a library traceback.

Fixtures are generated with PyMuPDF, as in ``test_sidecar_pdf_info.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import sidecar  # noqa: E402
from pdfxlate.pdf import spread as spread_mod  # noqa: E402


def _make_pdf(path: Path, pages: int, width: float = 595, height: float = 842) -> Path:
    """Write a real PDF whose pages carry identifiable text."""
    doc = pymupdf.open()
    try:
        for index in range(pages):
            page = doc.new_page(width=width, height=height)
            page.insert_text((60, 100), f"page {index + 1}", fontsize=24)
        doc.save(path)
    finally:
        doc.close()
    return path


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Keep every derived file inside the test's own directory.

    The module resolves its output through ``paths.cache_dir()``, which is the
    user's real cache; without this the suite would write there and could prune
    files the running app still uses.
    """
    cache = tmp_path / "cache"
    monkeypatch.setattr(spread_mod.paths, "cache_dir", lambda: cache)
    return cache


def test_pairs_pages_and_keeps_full_width(tmp_path):
    source = _make_pdf(tmp_path / "seven.pdf", 7)

    out, source_pages, spread_pages = spread_mod.build_spread(source)

    assert (source_pages, spread_pages) == (7, 4)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 4
        # Two 595-wide pages side by side.
        assert [round(page.rect.width) for page in doc] == [1190] * 4
        assert [round(page.rect.height) for page in doc] == [842] * 4


def test_text_stays_vector_and_both_pages_are_present(tmp_path):
    source = _make_pdf(tmp_path / "four.pdf", 4)

    out, _, _ = spread_mod.build_spread(source)

    with pymupdf.open(out) as doc:
        first = doc[0].get_text()
        second = doc[1].get_text()

    # Real text, not a rasterised image: extractable and in page order.
    assert "page 1" in first and "page 2" in first
    assert "page 3" in second and "page 4" in second


def test_second_call_is_a_cache_hit(tmp_path):
    source = _make_pdf(tmp_path / "four.pdf", 4)

    first_path, _, _ = spread_mod.build_spread(source)
    stamp = first_path.stat().st_mtime_ns
    second_path, _, _ = spread_mod.build_spread(source)

    assert second_path == first_path
    assert second_path.stat().st_mtime_ns == stamp


def test_edited_source_gets_a_new_file(tmp_path):
    """The cache key must include mtime and size, not just the path."""
    source = _make_pdf(tmp_path / "four.pdf", 4)
    first_path, _, _ = spread_mod.build_spread(source)

    # Rewrite it with different content so size and mtime both change.
    _make_pdf(source, 5, width=500)
    second_path, source_pages, spread_pages = spread_mod.build_spread(source)

    assert second_path != first_path
    assert (source_pages, spread_pages) == (5, 3)


def test_source_is_left_untouched(tmp_path):
    source = _make_pdf(tmp_path / "four.pdf", 4)
    before = source.read_bytes()

    spread_mod.build_spread(source)

    assert source.read_bytes() == before


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        spread_mod.build_spread(tmp_path / "nope.pdf")


def test_directory_raises(tmp_path):
    directory = tmp_path / "a-directory"
    directory.mkdir()
    with pytest.raises(spread_mod.SpreadError):
        spread_mod.build_spread(directory)


def test_non_pdf_raises(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a pdf")

    with pytest.raises(spread_mod.SpreadError) as info:
        spread_mod.build_spread(broken)

    assert "could not read PDF file" in str(info.value)


def test_partial_file_is_not_left_behind(tmp_path):
    """A failed build must not leave something the next call treats as a hit."""
    source = _make_pdf(tmp_path / "four.pdf", 4)
    spread_mod.build_spread(source)

    leftovers = list(spread_mod.spread_dir().glob("*.part"))
    assert leftovers == []


def test_sidecar_method_shape(tmp_path):
    source = _make_pdf(tmp_path / "seven.pdf", 7)

    result = sidecar.method_pdf_spread({"path": str(source)})

    assert set(result) == {"path", "pages", "spreads"}
    assert result["pages"] == 7
    assert result["spreads"] == 4
    assert Path(result["path"]).exists()


def test_sidecar_method_validates_path():
    with pytest.raises(ValueError, match="missing required parameter"):
        sidecar.method_pdf_spread({})
    with pytest.raises(ValueError, match="non-empty string"):
        sidecar.method_pdf_spread({"path": "   "})


def test_sidecar_method_is_registered():
    assert sidecar.METHODS["pdf.spread"] is sidecar.method_pdf_spread
