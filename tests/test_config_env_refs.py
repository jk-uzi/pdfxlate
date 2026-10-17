"""Saving must never turn a ``${VAR}`` reference into the expanded secret.

The desktop UI posts back the whole config document, which necessarily contains
*expanded* values (``config.get`` returns what the engine resolved).  The save
path puts the references back — but it used to do that by dotted path, and a
renamed provider no longer has the path the file was read from.  That would
write the secret in clear.  These tests pin the fallback that prevents it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import config as config_mod  # noqa: E402

SECRET = "sk-live-abcdef123456"

BASE = """
providers:
  deepseek:
    kind: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${PDFXLATE_TEST_KEY}
    models:
      chat:
        model: deepseek-chat
"""


def _write(tmp_path, text: str = BASE) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def key_in_env(monkeypatch):
    monkeypatch.setenv("PDFXLATE_TEST_KEY", SECRET)


def _saved(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_unchanged_save_keeps_the_reference(tmp_path):
    path = _write(tmp_path)
    cfg = config_mod.load(path)
    config_mod.save(cfg, path)
    assert _saved(path)["providers"]["deepseek"]["api_key"] == "${PDFXLATE_TEST_KEY}"


def test_renamed_provider_keeps_the_reference(tmp_path):
    """The regression: a key move must not leak the expanded secret."""
    path = _write(tmp_path)
    cfg = config_mod.load(path)
    # Rebuild in place, exactly as the renderer does when renaming.
    providers = cfg.model_dump()
    providers["providers"] = {
        ("remote" if name == "deepseek" else name): value
        for name, value in providers["providers"].items()
    }
    rebuilt = config_mod.AppConfig.model_validate(providers)

    config_mod.save(rebuilt, path)

    saved = _saved(path)
    assert saved["providers"]["remote"]["api_key"] == "${PDFXLATE_TEST_KEY}"
    assert SECRET not in path.read_text(encoding="utf-8")


def test_reordering_providers_keeps_the_reference(tmp_path):
    """MCP / UI edits can reorder entries; the path must not matter."""
    path = _write(
        tmp_path,
        BASE
        + """
  other:
    kind: openai_compat
    base_url: https://other.invalid/v1
    models:
      m: {model: m}
""",
    )
    cfg = config_mod.load(path)
    data = cfg.model_dump()
    # Swap the two providers around.
    first, second = list(data["providers"].items())
    data["providers"] = {second[0]: second[1], first[0]: first[1]}
    config_mod.save(config_mod.AppConfig.model_validate(data), path)

    saved = _saved(path)
    assert saved["providers"]["deepseek"]["api_key"] == "${PDFXLATE_TEST_KEY}"
    assert SECRET not in path.read_text(encoding="utf-8")


def test_a_typed_key_still_wins_over_the_reference(tmp_path):
    """The fallback must not resurrect a reference over a deliberate edit."""
    path = _write(tmp_path)
    cfg = config_mod.load(path)
    cfg.providers["deepseek"].api_key = "sk-user-typed"
    config_mod.save(cfg, path)

    saved = _saved(path)
    assert saved["providers"]["deepseek"]["api_key"] == "sk-user-typed"


def test_the_fallback_does_not_touch_unrelated_values(tmp_path):
    path = _write(tmp_path)
    cfg = config_mod.load(path)
    cfg.translation.lang_out = "ja"
    config_mod.save(cfg, path)
    saved = _saved(path)
    assert saved["translation"]["lang_out"] == "ja"
    assert saved["providers"]["deepseek"]["base_url"] == "https://api.deepseek.com/v1"


def test_fallback_walks_nested_values_when_the_path_is_gone(tmp_path):
    """After a rename the whole document is scanned, nested maps included.

    A value that happens to equal the expanded secret is written back as the
    reference. That is the safe direction: the reference expands to the same
    thing on the next load, whereas leaving it literal would persist a secret.
    """
    path = _write(tmp_path)
    cfg = config_mod.load(path)
    cfg.providers["deepseek"].models["chat"].extra_body = {"token": SECRET}
    data = cfg.model_dump()
    data["providers"] = {"renamed": data["providers"]["deepseek"]}
    config_mod.save(config_mod.AppConfig.model_validate(data), path)

    saved = _saved(path)
    assert saved["providers"]["renamed"]["api_key"] == "${PDFXLATE_TEST_KEY}"
    assert saved["providers"]["renamed"]["models"]["chat"]["extra_body"]["token"] == (
        "${PDFXLATE_TEST_KEY}"
    )
    assert SECRET not in path.read_text(encoding="utf-8")
