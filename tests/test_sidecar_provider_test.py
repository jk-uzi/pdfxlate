"""The ``providers.test`` probe: one tiny request, no cache, no side effects."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import sidecar  # noqa: E402


def _params(**kw):
    base = {
        "provider": "p",
        "kind": "openai_compat",
        "base_url": "https://example.invalid/v1",
        "model": "m",
    }
    base.update(kw)
    return base


def test_is_registered():
    assert "providers.test" in sidecar.METHODS


def test_unknown_kind_is_rejected_before_any_request():
    with pytest.raises(ValueError, match="unknown kind"):
        sidecar.method_providers_test(_params(kind="not_a_thing"))


def test_missing_base_url_is_rejected():
    with pytest.raises(ValueError, match="base_url"):
        sidecar.method_providers_test(_params(base_url=""))


def test_missing_model_is_rejected():
    with pytest.raises(ValueError, match="model is required"):
        sidecar.method_providers_test(_params(model=""))


def test_missing_key_and_none_saved_is_rejected(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("providers:\n  p:\n    base_url: https://x.invalid/v1\n", encoding="utf-8")
    from pdfxlate import config as config_mod

    real_load = config_mod.load
    monkeypatch.setattr(config_mod, "load", lambda p=None: real_load(cfg))
    with pytest.raises(ValueError, match="api_key"):
        sidecar.method_providers_test(_params(api_key=None))


def test_success_reports_latency_and_sample(monkeypatch):
    """The probe goes through do_translate, so no cache is involved."""
    calls = {}

    class FakeAdapter:
        def __init__(self, profile):
            calls["profile"] = profile

        def do_translate(self, text):
            calls["text"] = text
            return "你好"

    import pdfxlate.models.base as base

    real_build = base.build_adapter

    def fake_build(kind, profile, lang_in, lang_out, **kw):
        calls["cache_enabled"] = kw.get("cache_enabled")
        return FakeAdapter(profile)

    monkeypatch.setattr(base, "build_adapter", fake_build)
    try:
        result = sidecar.method_providers_test(_params(api_key="k"))
    finally:
        monkeypatch.setattr(base, "build_adapter", real_build)

    assert result["ok"] is True
    assert result["sample"] == "你好"
    assert isinstance(result["latency_ms"], int)
    # A probe must not create or touch the translation cache.
    assert calls["cache_enabled"] is False
    # The text carries no {vN}, so nothing can trip the placeholder check.
    assert "{v" not in calls["text"]
    assert calls["profile"].model == "m"


def test_saved_key_is_used_when_the_draft_leaves_it_blank(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "providers:\n"
        "  p:\n"
        "    base_url: https://x.invalid/v1\n"
        "    api_key: saved-key\n"
        "    models:\n"
        "      m: {model: m}\n",
        encoding="utf-8",
    )
    from pdfxlate import config as config_mod

    real_load = config_mod.load
    monkeypatch.setattr(config_mod, "load", lambda p=None: real_load(cfg))

    seen = {}

    class FakeAdapter:
        def __init__(self, profile):
            seen["key"] = profile.api_key

        def do_translate(self, text):
            return "ok"

    import pdfxlate.models.base as base

    real_build = base.build_adapter
    monkeypatch.setattr(base, "build_adapter", lambda *a, **k: FakeAdapter(a[1]))
    try:
        sidecar.method_providers_test(_params(api_key=None))
    finally:
        monkeypatch.setattr(base, "build_adapter", real_build)
    assert seen["key"] == "saved-key"


def test_adapter_error_becomes_a_value_error(monkeypatch):
    class FailingAdapter:
        def __init__(self, profile):
            pass

        def do_translate(self, text):
            raise ValueError("Anthropic API error (HTTP 404): not found")

    import pdfxlate.models.base as base

    real_build = base.build_adapter
    monkeypatch.setattr(base, "build_adapter", lambda *a, **k: FailingAdapter(a[1]))
    try:
        with pytest.raises(ValueError, match="HTTP 404"):
            sidecar.method_providers_test(_params(api_key="k"))
    finally:
        monkeypatch.setattr(base, "build_adapter", real_build)


def test_timeout_is_reported(tmp_path, monkeypatch):
    """A hanging endpoint must not hang the sidecar's stdin loop forever."""
    monkeypatch.setattr(sidecar, "_PROBE_TIMEOUT_SECONDS", 0.05)

    import threading
    import time

    class HangingAdapter:
        def __init__(self, profile):
            pass

        def do_translate(self, text):
            time.sleep(0.5)
            return "late"

    import pdfxlate.models.base as base

    real_build = base.build_adapter
    monkeypatch.setattr(base, "build_adapter", lambda *a, **k: HangingAdapter(a[1]))
    try:
        with pytest.raises(TimeoutError, match="no answer"):
            sidecar.method_providers_test(_params(api_key="k"))
    finally:
        monkeypatch.setattr(base, "build_adapter", real_build)
    # Let the daemon thread finish before the test process moves on.
    time.sleep(0.6)
