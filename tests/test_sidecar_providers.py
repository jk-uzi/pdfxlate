"""Tests for the sidecar's provider listing and translate.start plumbing.

The renderer (``src/renderer/app.ts``) is written against an exact JSON shape
for ``providers.list``, so the shape itself is the contract under test here —
in particular that an ``api_key`` never leaves the process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import api as api_mod  # noqa: E402
from pdfxlate import config as config_mod  # noqa: E402
from pdfxlate import sidecar  # noqa: E402


NESTED = """\
default_provider: deepseek
default_model: chat

providers:
  deepseek:
    kind: openai_compat
    base_url: https://api.deepseek.com/v1/
    api_key: super-secret-key
    models:
      chat:
        model: deepseek-chat
        temperature: 0
        reasoning_effort: high
      reason: {}
  bare:
    models:
      m: {}
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    """Point ``config_mod.load`` at a temp config for the sidecar to read."""
    cfg = config_mod.load(_write(tmp_path, NESTED))
    monkeypatch.setattr(config_mod, "load", lambda path=None: cfg)
    return cfg


# --- providers.list shape --------------------------------------------------


def test_providers_list_top_level_shape(loaded):
    result = sidecar.method_providers_list({})
    assert set(result) == {"default_provider", "default_model", "providers"}
    assert result["default_provider"] == "deepseek"
    assert result["default_model"] == "chat"


def test_providers_list_never_leaks_api_key(loaded):
    """The renderer gets has_api_key, never the key itself."""
    result = sidecar.method_providers_list({})
    deepseek = result["providers"]["deepseek"]
    assert deepseek["has_api_key"] is True
    assert "api_key" not in deepseek
    # And the secret must not appear anywhere in the serialized payload.
    assert "super-secret-key" not in repr(result)


def test_providers_list_has_api_key_false_when_unset(loaded):
    bare = sidecar.method_providers_list({})["providers"]["bare"]
    assert bare["has_api_key"] is False
    assert "api_key" not in bare


def test_providers_list_provider_fields(loaded):
    deepseek = sidecar.method_providers_list({})["providers"]["deepseek"]
    assert deepseek["kind"] == "openai_compat"
    # base_url is normalized (no trailing slash) on load.
    assert deepseek["base_url"] == "https://api.deepseek.com/v1"
    assert set(deepseek["models"]) == {"chat", "reason"}


def test_providers_list_model_entry_fields(loaded):
    chat = sidecar.method_providers_list({})["providers"]["deepseek"]["models"]["chat"]
    assert set(chat) == {"model", "reasoning_effort", "temperature"}
    assert chat["model"] == "deepseek-chat"
    assert chat["reasoning_effort"] == "high"
    assert chat["temperature"] == 0.0


def test_providers_list_nullable_fields(loaded):
    """base_url may be null; the alias-only model keeps its alias as name."""
    bare = sidecar.method_providers_list({})["providers"]["bare"]
    assert bare["base_url"] is None
    assert bare["models"]["m"] == {
        "model": "m",
        "reasoning_effort": None,
        "temperature": 0.0,
    }


def test_providers_list_defaults_may_be_null(tmp_path, monkeypatch):
    cfg = config_mod.load(_write(tmp_path, "providers:\n  p:\n    models:\n      m: {}\n"))
    monkeypatch.setattr(config_mod, "load", lambda path=None: cfg)
    result = sidecar.method_providers_list({})
    assert result["default_provider"] is None
    assert result["default_model"] is None


def test_providers_list_is_registered(monkeypatch):
    """The METHOD table must expose the new name and not the old one."""
    assert "providers.list" in sidecar.METHODS
    assert "profiles.list" not in sidecar.METHODS


def test_config_get_carries_nested_providers(loaded):
    dumped = sidecar.method_config_get({})
    assert "providers" in dumped
    assert "models" in dumped["providers"]["deepseek"]


# --- translate.start plumbing ----------------------------------------------


class _TranslateRecorder:
    def __init__(self):
        self.kwargs = None

    def __call__(self, files, **kwargs):
        self.kwargs = kwargs
        return [("a-mono.pdf", "a-dual.pdf")]


@pytest.fixture
def translate_stub(monkeypatch):
    rec = _TranslateRecorder()
    # method_translate_start imports translate inside the function, so patch
    # the source attribute on the api module.
    monkeypatch.setattr(api_mod, "translate", rec)
    return rec


def test_translate_start_forwards_provider_and_model(translate_stub):
    sidecar.method_translate_start(
        {"files": ["a.pdf"], "provider": "deepseek", "model": "chat"}
    )
    assert translate_stub.kwargs["provider"] == "deepseek"
    assert translate_stub.kwargs["model"] == "chat"


def test_translate_start_forwards_cache_and_font_flags(translate_stub):
    sidecar.method_translate_start(
        {
            "files": ["a.pdf"],
            "ignore_cache": True,
            "skip_subset_fonts": True,
        }
    )
    assert translate_stub.kwargs["ignore_cache"] is True
    assert translate_stub.kwargs["skip_subset_fonts"] is True


def test_translate_start_skip_subset_fonts_absent_is_none(translate_stub):
    """Absent means "use the config"; None must reach api.translate, not False."""
    sidecar.method_translate_start({"files": ["a.pdf"]})
    assert translate_stub.kwargs["skip_subset_fonts"] is None
    assert translate_stub.kwargs["ignore_cache"] is False


def test_translate_start_returns_outputs(translate_stub):
    result = sidecar.method_translate_start({"files": ["a.pdf"]})
    assert result["outputs"] == [{"mono": "a-mono.pdf", "dual": "a-dual.pdf"}]
    assert "job_id" in result
