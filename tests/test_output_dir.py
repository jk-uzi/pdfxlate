"""Where a job with no output directory writes.

The desktop app no longer asks: outputs go into a ``translated/`` subfolder
beside each source document.  That has to work per file, not per job — a batch
may span several folders — and an explicit directory must still win, because
the CLI and the MCP tools pass one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import api as api_mod  # noqa: E402
from pdfxlate import config as config_mod  # noqa: E402
from pdfxlate.pdf import pipeline  # noqa: E402


@pytest.fixture(autouse=True)
def pipeline_stub(monkeypatch):
    """Replace the PDF work with a recorder; only the paths are under test."""
    calls: list[dict] = []

    def fake_stream(*args, **kwargs):
        return b"%PDF-mono", b"%PDF-dual"

    monkeypatch.setattr(pipeline, "translate_stream", fake_stream)
    return calls


def _run(tmp_path, files, output, **kwargs):
    translator = object()
    return pipeline.translate(
        [str(f) for f in files],
        translator=translator,
        output=output,
        ultrafast=True,  # skips the layout model, which is not under test here
        **kwargs,
    )


def test_empty_output_writes_beside_each_source(tmp_path):
    src = tmp_path / "papers"
    src.mkdir()
    first = src / "first.pdf"
    second = src / "second.pdf"
    for path in (first, second):
        path.write_bytes(b"%PDF-1.4")

    results = _run(tmp_path, [first, second], "")

    expected = src / pipeline.OUTPUT_SUBDIR
    assert results == [
        (str(expected / "first-mono.pdf"), str(expected / "first-dual.pdf")),
        (str(expected / "second-mono.pdf"), str(expected / "second-dual.pdf")),
    ]
    assert (expected / "first-mono.pdf").exists()
    assert (expected / "second-mono.pdf").exists()


def test_empty_output_keeps_documents_in_their_own_folders(tmp_path):
    """A batch over two folders must not merge their outputs into one."""
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    files = []
    for folder, name in ((alpha, "a"), (beta, "b")):
        path = folder / f"{name}.pdf"
        path.write_bytes(b"%PDF-1.4")
        files.append(path)

    results = _run(tmp_path, files, "")

    assert Path(results[0][0]).parent == alpha / pipeline.OUTPUT_SUBDIR
    assert Path(results[1][0]).parent == beta / pipeline.OUTPUT_SUBDIR


def test_explicit_output_directory_still_wins(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    source = src / "paper.pdf"
    source.write_bytes(b"%PDF-1.4")
    chosen = tmp_path / "elsewhere"

    results = _run(tmp_path, [source], str(chosen))

    assert results == [
        (str(chosen / "paper-mono.pdf"), str(chosen / "paper-dual.pdf"))
    ]
    assert not (src / pipeline.OUTPUT_SUBDIR).exists()


def test_output_directory_is_created(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    source = src / "paper.pdf"
    source.write_bytes(b"%PDF-1.4")

    _run(tmp_path, [source], "")

    assert (src / pipeline.OUTPUT_SUBDIR).is_dir()


def test_api_leaves_output_empty_rather_than_using_cwd(monkeypatch, tmp_path):
    """`api.translate` must not invent a directory: the pipeline resolves it."""
    seen: dict = {}

    def fake_build_translator(*args, **kwargs):
        return object()

    def fake_pipeline(*args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(api_mod, "build_translator", fake_build_translator)
    monkeypatch.setattr(api_mod.pipeline, "translate", fake_pipeline)

    cfg = config_mod.AppConfig(
        providers={"prov": {"models": {"chat": {"model": "m"}}}}
    )
    api_mod.translate(["a.pdf"], config=cfg, mode="ultrafast")

    assert seen["output"] == ""


def test_api_forwards_an_explicit_output(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_build_translator(*args, **kwargs):
        return object()

    def fake_pipeline(*args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(api_mod, "build_translator", fake_build_translator)
    monkeypatch.setattr(api_mod.pipeline, "translate", fake_pipeline)

    cfg = config_mod.AppConfig(
        providers={"prov": {"models": {"chat": {"model": "m"}}}}
    )
    api_mod.translate(["a.pdf"], config=cfg, output=str(tmp_path), mode="ultrafast")

    assert seen["output"] == str(tmp_path)
