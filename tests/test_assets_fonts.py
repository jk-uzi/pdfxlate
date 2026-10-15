"""Tests for the language→font mapping and the asset status report.

These are pure-local: :func:`font_asset` only builds an :class:`Asset` and
:func:`status` only stats ``Asset.target``, so nothing here touches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import assets  # noqa: E402
from pdfxlate import paths  # noqa: E402

GO_NOTO = "GoNotoKurrent-Regular.ttf"


@pytest.fixture
def empty_assets_dir(tmp_path, monkeypatch):
    """Redirect the asset cache to an empty directory so no real file leaks in."""
    monkeypatch.setattr(paths, "assets_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("lang", "filename"),
    [
        ("zh", "SourceHanSerifCN-Regular.ttf"),
        ("zh-cn", "SourceHanSerifCN-Regular.ttf"),
        ("zh-hans", "SourceHanSerifCN-Regular.ttf"),
        ("zh-tw", "SourceHanSerifTW-Regular.ttf"),
        ("zh-hant", "SourceHanSerifTW-Regular.ttf"),
        ("ja", "SourceHanSerifJP-Regular.ttf"),
        ("ko", "SourceHanSerifKR-Regular.ttf"),
    ],
)
def test_cjk_variants_map_to_region_faces(lang, filename):
    assert assets.font_asset(lang).filename == filename
    assert assets.font_asset(lang).subdir == "fonts"


def test_cjk_lookup_is_case_insensitive():
    assert assets.font_asset("ZH-TW").filename == "SourceHanSerifTW-Regular.ttf"


@pytest.mark.parametrize("lang", ["ru", "ar", "th", "hi", "uk"])
def test_known_non_cjk_languages_use_gonoto(lang):
    assert assets.font_asset(lang).filename == GO_NOTO


@pytest.mark.parametrize("lang", ["xx", "klingon", "", "zz-ZZ"])
def test_unknown_language_falls_back_to_gonoto(lang, caplog):
    """Lenient fallback: user-supplied codes must not abort a translation."""
    with caplog.at_level("DEBUG", logger="pdfxlate.assets"):
        asset = assets.font_asset(lang)
    assert asset.filename == GO_NOTO
    # The fallback is logged so a mis-rendered script can be traced later.
    assert any("falling back to GoNotoKurrent" in r.message for r in caplog.records)


def test_noto_langs_is_not_dead_code():
    """Every enumerated GoNoto language must resolve without a warning."""
    for lang in assets._NOTO_LANGS:
        assert assets.font_asset(lang).filename == GO_NOTO


def test_supported_languages_covers_both_sources():
    supported = assets.supported_languages()
    assert "zh-tw" in supported
    assert "ja" in supported
    assert "ru" in supported
    assert "xx" not in supported


def test_status_keeps_legacy_keys(empty_assets_dir):
    result = assets.status()
    assert set(result) == {"layout_model", "font_zh", "font_ja", "font_ko"}


def test_status_without_lang_is_unchanged(empty_assets_dir):
    """Backwards compatibility: no-arg call behaves exactly as before."""
    result = assets.status()
    assert result == {
        "layout_model": False,
        "font_zh": False,
        "font_ja": False,
        "font_ko": False,
    }
    assert "font_ready_for" not in result


def test_status_reports_present_cjk_font(empty_assets_dir):
    (empty_assets_dir / "fonts").mkdir()
    (empty_assets_dir / "fonts" / "SourceHanSerifTW-Regular.ttf").write_bytes(b"x")

    assert assets.status("zh-tw")["font_ready_for"] is True
    # A different CJK region is a different file, so it stays missing.
    assert assets.status("ja")["font_ready_for"] is False


def test_status_reports_present_non_cjk_font(empty_assets_dir):
    (empty_assets_dir / "fonts").mkdir()
    (empty_assets_dir / "fonts" / GO_NOTO).write_bytes(b"x")

    assert assets.status("ru")["font_ready_for"] is True
    assert assets.status("ar")["font_ready_for"] is True
    # The CJK legacy keys are still False, which is the correct signal for a
    # GoNoto target: no SourceHanSerif face is needed.
    assert assets.status("ru")["font_zh"] is False
