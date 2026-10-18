"""Output shape: which document is built, which pages survive, and overwriting.

Three behaviours live here because they are decided in the same place:

* ``output_mode`` picks the document instead of always producing both.
* a page range cuts the *output* down to those pages, not only the translation.
* an existing result is never replaced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.pdf import pipeline  # noqa: E402


# --- page selection --------------------------------------------------------


def test_no_pages_means_every_page():
    assert pipeline._selected_pages(None, 10) is None
    assert pipeline._selected_pages([], 10) is None


def test_every_page_selected_is_the_same_as_none():
    """A full range must not go through page deletion for nothing."""
    assert pipeline._selected_pages(list(range(10)), 10) is None


def test_selection_is_sorted_and_deduplicated():
    assert pipeline._selected_pages([2, 0, 2, 1], 10) == [0, 1, 2]


def test_out_of_range_pages_are_dropped():
    """'1-99' on a ten-page paper is a normal thing to type."""
    assert pipeline._selected_pages([0, 50, 2], 10) == [0, 2]


def test_a_selection_entirely_out_of_range_keeps_everything():
    """Nothing valid to keep would silently produce an empty PDF."""
    assert pipeline._selected_pages([99], 10) is None


# --- unique names ----------------------------------------------------------


def test_unique_paths_uses_the_plain_name_when_free(tmp_path):
    paths = pipeline._unique_paths(tmp_path, "paper", ["-mono.pdf", "-dual.pdf"])
    assert paths == {
        "-mono.pdf": tmp_path / "paper-mono.pdf",
        "-dual.pdf": tmp_path / "paper-dual.pdf",
    }


def test_unique_paths_numbers_both_sides_together(tmp_path):
    """The pair must be recognisable as a pair: one number, not two."""
    (tmp_path / "paper-mono.pdf").write_bytes(b"x")
    (tmp_path / "paper-dual.pdf").write_bytes(b"x")

    paths = pipeline._unique_paths(tmp_path, "paper", ["-mono.pdf", "-dual.pdf"])

    assert paths["-mono.pdf"] == tmp_path / "paper-mono (2).pdf"
    assert paths["-dual.pdf"] == tmp_path / "paper-dual (2).pdf"


def test_unique_paths_skips_to_the_next_free_number(tmp_path):
    (tmp_path / "paper-mono.pdf").write_bytes(b"x")
    (tmp_path / "paper-mono (2).pdf").write_bytes(b"x")

    paths = pipeline._unique_paths(tmp_path, "paper", ["-mono.pdf", "-dual.pdf"])

    assert paths["-mono.pdf"] == tmp_path / "paper-mono (3).pdf"


def test_unique_paths_respects_a_taken_dual_only(tmp_path):
    """A stale dual must not make the next mono collide with it."""
    (tmp_path / "paper-dual (2).pdf").write_bytes(b"x")

    paths = pipeline._unique_paths(tmp_path, "paper", ["-mono.pdf", "-dual.pdf"])

    assert paths["-mono.pdf"] == tmp_path / "paper-mono.pdf"
    assert paths["-dual.pdf"] == tmp_path / "paper-dual.pdf"


# --- translate() wiring ----------------------------------------------------


class _Stream:
    """Records the arguments and returns the bytes each side should get."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, stream, **kwargs):
        self.calls.append(kwargs)
        mode = kwargs.get("output_mode", pipeline.OUTPUT_BOTH)
        mono = b"%PDF-mono" if mode in (pipeline.OUTPUT_MONO, pipeline.OUTPUT_BOTH) else b""
        dual = b"%PDF-dual" if mode in (pipeline.OUTPUT_DUAL, pipeline.OUTPUT_BOTH) else b""
        return mono, dual


@pytest.fixture
def stream(monkeypatch):
    stub = _Stream()
    monkeypatch.setattr(pipeline, "translate_stream", stub)
    return stub


def _source(tmp_path, name="paper.pdf"):
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4")
    return path


def _run(files, output="", **kwargs):
    return pipeline.translate([str(f) for f in files], translator=object(), output=output, **kwargs)


def test_mono_mode_writes_only_the_translated_file(tmp_path, stream):
    source = _source(tmp_path)
    results = _run([source], output_mode=pipeline.OUTPUT_MONO)

    target = source.parent / pipeline.OUTPUT_SUBDIR
    assert results == [(str(target / "paper-mono.pdf"), "")]
    assert (target / "paper-mono.pdf").read_bytes() == b"%PDF-mono"
    assert not (target / "paper-dual.pdf").exists()


def test_dual_mode_writes_only_the_alternating_file(tmp_path, stream):
    source = _source(tmp_path)
    results = _run([source], output_mode=pipeline.OUTPUT_DUAL)

    target = source.parent / pipeline.OUTPUT_SUBDIR
    assert results == [("", str(target / "paper-dual.pdf"))]
    assert (target / "paper-dual.pdf").read_bytes() == b"%PDF-dual"
    assert not (target / "paper-mono.pdf").exists()


def test_both_mode_is_the_default_and_writes_both(tmp_path, stream):
    source = _source(tmp_path)
    results = _run([source])

    target = source.parent / pipeline.OUTPUT_SUBDIR
    assert results == [
        (str(target / "paper-mono.pdf"), str(target / "paper-dual.pdf"))
    ]
    assert (target / "paper-mono.pdf").exists()
    assert (target / "paper-dual.pdf").exists()


def test_mode_reaches_the_stream(tmp_path, stream):
    source = _source(tmp_path)
    _run([source], output_mode=pipeline.OUTPUT_MONO)
    assert stream.calls[0]["output_mode"] == pipeline.OUTPUT_MONO


def test_pages_reach_the_stream(tmp_path, stream):
    source = _source(tmp_path)
    _run([source], pages=[1])
    assert stream.calls[0]["pages"] == [1]


def test_unknown_mode_is_rejected_before_any_work(tmp_path, stream):
    source = _source(tmp_path)
    with pytest.raises(ValueError, match="output_mode"):
        _run([source], output_mode="both-ish")
    assert stream.calls == []


def test_a_second_run_does_not_replace_the_first(tmp_path, stream):
    source = _source(tmp_path)
    first = _run([source], output_mode=pipeline.OUTPUT_MONO)[0][0]
    original = Path(first).read_bytes()

    second = _run([source], output_mode=pipeline.OUTPUT_MONO)[0][0]

    assert second != first
    assert second.endswith("paper-mono (2).pdf")
    assert Path(first).read_bytes() == original, "the earlier result was overwritten"


def test_numbering_covers_a_mono_only_run(tmp_path, stream):
    """A dual left over from another mode must not shift the mono numbering."""
    source = _source(tmp_path)
    _run([source], output_mode=pipeline.OUTPUT_DUAL)
    first_mono = _run([source], output_mode=pipeline.OUTPUT_MONO)[0][0]

    assert first_mono.endswith("paper-mono.pdf"), "the stale dual forced a suffix"


# --- translate_stream's mode handling -------------------------------------


def test_stream_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="output_mode"):
        pipeline.translate_stream(b"", translator=object(), output_mode="nope")


def test_write_outputs_does_not_replace_an_earlier_result(tmp_path):
    from pdfxlate.pdf import two_phase

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4")

    mono1, dual1 = two_phase.write_outputs(str(source), b"m1", b"d1")
    mono2, dual2 = two_phase.write_outputs(str(source), b"m2", b"d2")

    assert Path(mono1).read_bytes() == b"m1"
    assert Path(dual1).read_bytes() == b"d1"
    assert mono2 != mono1 and dual2 != dual1
    assert Path(mono2).read_bytes() == b"m2"
    assert "(2)" in Path(mono2).name and "(2)" in Path(dual2).name
