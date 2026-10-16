"""Tests for the CLI typesetting options.

The options must reach the right place (config fields / adapter) *and* must
leave the config untouched when omitted, which is the easy thing to get wrong
with ``store_true``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import api as api_mod  # noqa: E402
from pdfxlate import cli as cli_mod  # noqa: E402
from pdfxlate import config as config_mod  # noqa: E402


# --- parser level ----------------------------------------------------------


def test_defaults_leave_config_alone():
    """Omitted options must not carry a value that would override the config."""
    args = cli_mod.build_parser().parse_args([])
    assert args.backend is None
    assert args.onnx is None
    # The trap: a plain store_true would make this False and silently override
    # a config that sets skip_subset_fonts: true.
    assert args.skip_subset_fonts is None
    assert args.ignore_cache is False


def test_backend_accepts_every_literal():
    parser = cli_mod.build_parser()
    for value in ("auto", "cpu", "cuda", "dml"):
        assert parser.parse_args(["--backend", value]).backend == value


def test_backend_rejects_unknown_value():
    with pytest.raises(SystemExit):
        cli_mod.build_parser().parse_args(["--backend", "tpu"])


def test_onnx_path_parsed():
    assert cli_mod.build_parser().parse_args(["--onnx", "m.onnx"]).onnx == "m.onnx"


def test_skip_subset_fonts_tristate():
    parser = cli_mod.build_parser()
    assert parser.parse_args(["--skip-subset-fonts"]).skip_subset_fonts is True
    assert parser.parse_args(["--no-skip-subset-fonts"]).skip_subset_fonts is False


def test_ignore_cache_flag():
    assert cli_mod.build_parser().parse_args(["--ignore-cache"]).ignore_cache is True


def test_provider_and_model_parsed():
    args = cli_mod.build_parser().parse_args(["--provider", "deepseek", "--model", "chat"])
    assert args.provider == "deepseek"
    assert args.model == "chat"


def test_profile_is_a_deprecated_alias_for_provider():
    """Old command lines pass --profile; it must land in the provider slot."""
    args = cli_mod.build_parser().parse_args(["--profile", "legacy"])
    assert args.provider == "legacy"


def test_provider_defaults_to_none():
    args = cli_mod.build_parser().parse_args([])
    assert args.provider is None
    assert args.model is None


def test_list_models_flag_parsed():
    assert cli_mod.build_parser().parse_args(["--list-models"]).list_models is True
    # --list-profiles predates providers and maps to the same dest.
    assert cli_mod.build_parser().parse_args(["--list-profiles"]).list_models is True


# --- main() wiring ---------------------------------------------------------


class _Recorder:
    """Capture the cfg and kwargs handed to ``translate``."""

    def __init__(self):
        self.cfg = None
        self.kwargs = None

    def __call__(self, files, **kwargs):
        self.kwargs = kwargs
        self.cfg = kwargs.get("config")
        return []


@pytest.fixture
def run_main(monkeypatch, tmp_path):
    """Run ``cli.main`` with config loading and the pipeline stubbed out."""
    rec = _Recorder()
    cfg = config_mod.AppConfig(
        default_provider="prov",
        default_model="chat",
        providers={"prov": {"models": {"chat": {"model": "m"}}}},
    )
    monkeypatch.setattr(cli_mod.paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli_mod.config_mod, "load", lambda path=None: cfg)
    monkeypatch.setattr(cli_mod, "translate", rec)
    return rec, cfg


def _main(argv):
    return cli_mod.main(argv)


def test_omitted_skip_subset_fonts_keeps_config_true(run_main):
    """Regression: not passing the flag must not reset a config true to false."""
    rec, cfg = run_main
    cfg.translation.skip_subset_fonts = True
    assert _main(["a.pdf"]) == 0
    assert rec.cfg.translation.skip_subset_fonts is True


def test_skip_subset_fonts_flag_overrides_config_false(run_main):
    rec, cfg = run_main
    cfg.translation.skip_subset_fonts = False
    assert _main(["a.pdf", "--skip-subset-fonts"]) == 0
    assert rec.cfg.translation.skip_subset_fonts is True


def test_no_skip_subset_fonts_flag_overrides_config_true(run_main):
    rec, cfg = run_main
    cfg.translation.skip_subset_fonts = True
    assert _main(["a.pdf", "--no-skip-subset-fonts"]) == 0
    assert rec.cfg.translation.skip_subset_fonts is False


def test_backend_overrides_config(run_main):
    rec, cfg = run_main
    cfg.layout.backend = "auto"
    assert _main(["a.pdf", "--backend", "cuda"]) == 0
    assert rec.cfg.layout.backend == "cuda"


def test_omitted_backend_keeps_config(run_main):
    rec, cfg = run_main
    cfg.layout.backend = "dml"
    assert _main(["a.pdf"]) == 0
    assert rec.cfg.layout.backend == "dml"


def test_onnx_overrides_config_model_path(run_main):
    rec, cfg = run_main
    cfg.layout.model_path = "old.onnx"
    assert _main(["a.pdf", "--onnx", "new.onnx"]) == 0
    assert rec.cfg.layout.model_path == "new.onnx"


def test_ignore_cache_forwarded_to_api(run_main):
    rec, _ = run_main
    assert _main(["a.pdf", "--ignore-cache"]) == 0
    assert rec.kwargs["ignore_cache"] is True


def test_ignore_cache_defaults_to_false(run_main):
    rec, _ = run_main
    assert _main(["a.pdf"]) == 0
    assert rec.kwargs["ignore_cache"] is False


def test_provider_and_model_forwarded_to_api(run_main):
    rec, _ = run_main
    assert _main(["a.pdf", "--provider", "prov", "--model", "chat"]) == 0
    assert rec.kwargs["provider"] == "prov"
    assert rec.kwargs["model"] == "chat"


def test_profile_flag_lands_in_provider_slot(run_main):
    """--profile is the deprecated spelling; it must forward as provider."""
    rec, _ = run_main
    assert _main(["a.pdf", "--profile", "prov"]) == 0
    assert rec.kwargs["provider"] == "prov"
    assert rec.kwargs["model"] is None


def test_omitted_provider_and_model_stay_none(run_main):
    """Not passing them lets api.translate fall back to the config defaults."""
    rec, _ = run_main
    assert _main(["a.pdf"]) == 0
    assert rec.kwargs["provider"] is None
    assert rec.kwargs["model"] is None


# --- --list-models / --list-profiles ---------------------------------------


@pytest.fixture
def list_main(monkeypatch):
    """Load a two-provider config; the caller captures stdout via capsys."""
    cfg = config_mod.AppConfig(
        default_provider="deepseek",
        default_model="chat",
        providers={
            "deepseek": {
                "base_url": "https://api.deepseek.com/v1",
                "models": {"chat": {"model": "deepseek-chat"}, "reason": {}},
            },
            "local": {"models": {"qwen": {"model": "Qwen3"}}},
        },
    )
    monkeypatch.setattr(cli_mod.paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(cli_mod.config_mod, "load", lambda path=None: cfg)
    return cfg


def test_list_models_shows_provider_tree_and_default(list_main, capsys):
    assert _main(["--list-models"]) == 0
    out = capsys.readouterr().out
    assert "deepseek" in out
    assert "local" in out
    assert "chat (default): deepseek-chat" in out
    # A model relying on its alias keeps the alias as its name.
    assert "reason: reason" in out
    assert "qwen: Qwen3" in out


def test_list_profiles_is_a_compatible_alias(list_main, capsys):
    """The pre-provider flag prints the same tree, so old docs still work."""
    assert _main(["--list-models"]) == 0
    models_out = capsys.readouterr().out
    assert _main(["--list-profiles"]) == 0
    assert capsys.readouterr().out == models_out


def test_list_models_without_config_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(cli_mod.paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(
        cli_mod.config_mod, "load", lambda path=None: config_mod.AppConfig()
    )
    assert _main(["--list-models"]) == 1
    assert "No models configured" in capsys.readouterr().out


# --- api wiring ------------------------------------------------------------


def test_build_translator_passes_ignore_cache(monkeypatch):
    captured = {}

    def fake_build_adapter(provider, profile, lang_in, lang_out, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(api_mod, "build_adapter", fake_build_adapter)
    cfg = config_mod.AppConfig(
        default_provider="prov",
        default_model="chat",
        providers={"prov": {"models": {"chat": {"model": "m"}}}},
    )
    api_mod.build_translator(app_config=cfg, ignore_cache=True)
    assert captured["ignore_cache"] is True
    assert captured["cache_enabled"] is True


def test_translate_forwards_ignore_cache_to_translator(monkeypatch, tmp_path):
    captured = {}

    def fake_build_translator(provider=None, model=None, app_config=None, prompt=None, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(api_mod, "build_translator", fake_build_translator)
    monkeypatch.setattr(api_mod.pipeline, "translate", lambda *a, **k: [])
    cfg = config_mod.AppConfig(
        default_provider="prov",
        default_model="chat",
        providers={"prov": {"models": {"chat": {"model": "m"}}}},
    )
    api_mod.translate(
        ["a.pdf"], mode="ultrafast", config=cfg, output=str(tmp_path), ignore_cache=True
    )
    assert captured["ignore_cache"] is True
