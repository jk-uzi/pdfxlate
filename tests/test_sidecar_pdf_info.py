"""Tests for the sidecar's ``pdf.info`` metadata probe.

The preview toolbar renders "page N / M", so the renderer is written against
an exact ``{"pages": int, "path": str}`` shape.  That shape is the contract
under test here, along with clear errors for the cases a user actually hits:
a path that is missing, and a file that is not a readable PDF.

The PDF fixtures are generated with PyMuPDF rather than checked in, so the
test stays self-contained (``references/`` is not present in a worktree).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import sidecar  # noqa: E402


def _make_pdf(path: Path, pages: int = 1) -> Path:
    """Write a real ``pages``-page PDF to ``path`` and return it."""
    doc = pymupdf.open()
    try:
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
    finally:
        doc.close()
    return path


# --- happy path ------------------------------------------------------------


def test_pdf_info_reports_page_count(tmp_path):
    pdf = _make_pdf(tmp_path / "three.pdf", pages=3)
    result = sidecar.method_pdf_info({"path": str(pdf)})
    assert result["pages"] == 3


def test_pdf_info_single_page(tmp_path):
    pdf = _make_pdf(tmp_path / "one.pdf", pages=1)
    assert sidecar.method_pdf_info({"path": str(pdf)})["pages"] == 1


def test_pdf_info_result_shape_is_exact(tmp_path):
    """The renderer reads ``pages`` and ``path``; nothing else may appear."""
    pdf = _make_pdf(tmp_path / "shape.pdf", pages=2)
    result = sidecar.method_pdf_info({"path": str(pdf)})
    assert set(result) == {"pages", "path"}
    assert type(result["pages"]) is int
    assert type(result["path"]) is str


def test_pdf_info_path_is_resolved(tmp_path):
    pdf = _make_pdf(tmp_path / "resolved.pdf")
    result = sidecar.method_pdf_info({"path": str(pdf)})
    assert result["path"] == str(pdf.resolve())


def test_pdf_info_does_not_modify_the_file(tmp_path):
    """A metadata read must be side-effect free."""
    pdf = _make_pdf(tmp_path / "untouched.pdf", pages=2)
    before = pdf.read_bytes()
    sidecar.method_pdf_info({"path": str(pdf)})
    assert pdf.read_bytes() == before


# --- error cases -----------------------------------------------------------


def test_pdf_info_missing_path_parameter_errors_clearly():
    with pytest.raises(ValueError, match="path"):
        sidecar.method_pdf_info({})


def test_pdf_info_blank_path_errors_clearly():
    with pytest.raises(ValueError, match="path"):
        sidecar.method_pdf_info({"path": "   "})


def test_pdf_info_nonexistent_path_errors_clearly(tmp_path):
    missing = tmp_path / "nope.pdf"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        sidecar.method_pdf_info({"path": str(missing)})


def test_pdf_info_directory_path_errors_clearly(tmp_path):
    with pytest.raises(ValueError, match="not a file"):
        sidecar.method_pdf_info({"path": str(tmp_path)})


def test_pdf_info_non_pdf_text_file_errors_clearly(tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("this is plainly not a PDF\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a PDF"):
        sidecar.method_pdf_info({"path": str(text)})


def test_pdf_info_corrupt_file_errors_clearly(tmp_path):
    """Garbage bytes: PyMuPDF's FileDataError becomes a readable ValueError."""
    garbage = tmp_path / "corrupt.pdf"
    garbage.write_bytes(bytes(range(256)) * 64)
    with pytest.raises(ValueError, match="could not read PDF file"):
        sidecar.method_pdf_info({"path": str(garbage)})


# --- registration and protocol --------------------------------------------


def test_pdf_info_is_registered():
    assert sidecar.METHODS["pdf.info"] is sidecar.method_pdf_info


def test_handle_wraps_pdf_info_errors_as_json(tmp_path, capsys):
    """handle() must turn the exception into ``{"ok": false, ...}``."""
    sidecar.handle({"id": 7, "method": "pdf.info", "params": {"path": str(tmp_path)}})
    payload = capsys.readouterr().out.strip()
    assert '"ok": false' in payload
    assert '"type": "ValueError"' in payload


def test_handle_pdf_info_success(tmp_path, capsys):
    pdf = _make_pdf(tmp_path / "handled.pdf", pages=4)
    sidecar.handle(
        {"id": 1, "method": "pdf.info", "params": {"path": str(pdf)}}
    )
    payload = capsys.readouterr().out.strip()
    assert '"ok": true' in payload
    assert '"pages": 4' in payload
