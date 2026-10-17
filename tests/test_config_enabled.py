"""The per-model ``enabled`` switch.

Disabling is how a model is taken out of rotation without losing its settings.
The behaviour has to be enforced where models are *resolved*, not only in the
UI, or the CLI and MCP would keep using a model the user switched off.
"""

from __future__ import annotations

import pytest

from pdfxlate import config as config_mod


def _write(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


NESTED = """
providers:
  deepseek:
    kind: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: k
    models:
      chat:
        model: deepseek-chat
      reason:
        model: deepseek-reasoner
  local:
    base_url: http://localhost:11434/v1
    models:
      qwen:
        model: Qwen3
"""


def test_enabled_defaults_to_true(tmp_path):
    """Existing configs have no such key; they must keep working."""
    cfg = config_mod.load(_write(tmp_path, NESTED))
    assert all(
        entry.enabled for provider in cfg.providers.values() for entry in provider.models.values()
    )


def test_disabled_model_survives_a_save_and_reload(tmp_path):
    path = _write(tmp_path, NESTED)
    cfg = config_mod.load(path)
    cfg.providers["deepseek"].models["reason"].enabled = False
    config_mod.save(cfg, path)

    again = config_mod.load(path)
    assert again.providers["deepseek"].models["reason"].enabled is False
    assert again.providers["deepseek"].models["chat"].enabled is True


def test_no_argument_resolve_skips_disabled(tmp_path):
    """The fallback is the first *enabled* model, not the first model."""
    cfg = config_mod.load(_write(tmp_path, NESTED))
    cfg.providers["deepseek"].models["chat"].enabled = False
    resolved = cfg.resolve()
    assert (resolved.provider, resolved.alias) == ("deepseek", "reason")


def test_no_argument_resolve_raises_when_everything_is_disabled(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    for provider in cfg.providers.values():
        for entry in provider.models.values():
            entry.enabled = False
    with pytest.raises(ValueError, match="No enabled models configured"):
        cfg.resolve()


def test_naming_a_disabled_model_is_an_error(tmp_path):
    """Silently translating with a model the user turned off would be worse."""
    cfg = config_mod.load(_write(tmp_path, NESTED))
    cfg.providers["deepseek"].models["reason"].enabled = False
    with pytest.raises(ValueError, match="disabled"):
        cfg.resolve("deepseek", "reason")


def test_naming_a_disabled_model_by_old_profile_name_is_an_error(tmp_path):
    """The back-compat alias path must enforce the switch too."""
    cfg = config_mod.load(_write(tmp_path, NESTED))
    cfg.providers["deepseek"].models["reason"].enabled = False
    with pytest.raises(ValueError, match="disabled"):
        cfg.resolve("reason")


def test_provider_only_resolve_picks_first_enabled_model(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    cfg.providers["deepseek"].models["chat"].enabled = False
    assert cfg.resolve("deepseek").alias == "reason"


def test_provider_only_resolve_raises_when_all_of_them_are_disabled(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    cfg.providers["local"].models["qwen"].enabled = False
    with pytest.raises(ValueError, match="no enabled models"):
        cfg.resolve("local")


def test_all_models_keeps_disabled_by_default(tmp_path):
    """The editor and --list-models must still see a switched-off model."""
    cfg = config_mod.load(_write(tmp_path, NESTED))
    cfg.providers["deepseek"].models["reason"].enabled = False
    assert cfg.all_models() == [
        ("deepseek", "chat"),
        ("deepseek", "reason"),
        ("local", "qwen"),
    ]


def test_all_models_can_filter_to_enabled(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    cfg.providers["deepseek"].models["reason"].enabled = False
    assert cfg.all_models(only_enabled=True) == [
        ("deepseek", "chat"),
        ("local", "qwen"),
    ]


def test_resolved_model_carries_the_flag(tmp_path):
    """`_flatten` dumps every entry field, so ResolvedModel must accept it."""
    cfg = config_mod.load(_write(tmp_path, NESTED))
    cfg.providers["deepseek"].models["chat"].enabled = False
    # Resolved directly rather than through resolve(), which now refuses it.
    resolved = cfg._flatten("deepseek", "chat")
    assert resolved.enabled is False


def test_sidecar_reports_enabled(tmp_path, monkeypatch):
    path = _write(tmp_path, NESTED.replace("        model: deepseek-reasoner", "        model: deepseek-reasoner\n        enabled: false"))
    monkeypatch.setenv("PDFXLATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config_mod.paths, "config_file", lambda: path)

    from pdfxlate import sidecar

    models = sidecar.method_providers_list({})["providers"]["deepseek"]["models"]
    assert models["chat"]["enabled"] is True
    assert models["reason"]["enabled"] is False


def test_cli_marks_disabled_models(tmp_path, monkeypatch, capsys):
    path = _write(
        tmp_path,
        NESTED.replace("        model: deepseek-chat", "        model: deepseek-chat\n        enabled: false"),
    )
    monkeypatch.setenv("PDFXLATE_CONFIG_DIR", str(tmp_path))

    from pdfxlate import cli as cli_mod

    # `cli_mod.config_mod` *is* `config_mod`, so the loader has to be captured
    # before patching or the replacement calls itself.
    real_load = config_mod.load
    monkeypatch.setattr(cli_mod.config_mod, "load", lambda p=None: real_load(path))
    monkeypatch.setattr(cli_mod.paths, "config_file", lambda: path)
    monkeypatch.setattr(cli_mod.paths, "ensure_dirs", lambda: None)

    assert cli_mod.main(["--list-models"]) == 0
    out = capsys.readouterr().out
    assert "chat: deepseek-chat (disabled)" in out
    # The fallback marker moved to the first enabled model.
    assert "reason (used when unspecified): deepseek-reasoner" in out
