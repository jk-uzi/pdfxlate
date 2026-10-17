"""``providers.models``: the catalogue probe behind the import dialog."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import sidecar  # noqa: E402


def _params(**kw):
    base = {
        "provider": "p",
        "kind": "openai_compat",
        "base_url": "https://example.invalid/v1",
    }
    base.update(kw)
    return base


def test_is_registered():
    assert "providers.models" in sidecar.METHODS


def test_missing_base_url_is_rejected():
    with pytest.raises(ValueError, match="base_url"):
        sidecar.method_providers_models(_params(base_url=""))


def test_missing_key_and_none_saved_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "providers:\n  p:\n    base_url: https://x.invalid/v1\n", encoding="utf-8"
    )
    from pdfxlate import config as config_mod

    real_load = config_mod.load
    monkeypatch.setattr(config_mod, "load", lambda p=None: real_load(path))
    with pytest.raises(ValueError, match="api_key"):
        sidecar.method_providers_models(_params())


def test_returns_the_ids_from_the_catalog(monkeypatch):
    from pdfxlate.models import catalog

    seen = {}

    def fake_list(kind, base_url, api_key, timeout=None):
        seen.update(kind=kind, base_url=base_url, api_key=api_key)
        return ["a", "b"]

    monkeypatch.setattr(catalog, "list_models", fake_list)
    result = sidecar.method_providers_models(_params(api_key="k"))
    assert result == {"models": ["a", "b"]}
    assert seen["kind"] == "openai_compat"
    assert seen["api_key"] == "k"


def test_saved_key_is_used_when_the_draft_leaves_it_blank(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
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
    monkeypatch.setattr(config_mod, "load", lambda p=None: real_load(path))

    from pdfxlate.models import catalog

    seen = {}
    monkeypatch.setattr(
        catalog, "list_models", lambda *a, **k: seen.setdefault("key", a[2]) or []
    )
    sidecar.method_providers_models(_params(api_key=None))
    assert seen["key"] == "saved-key"


def test_catalog_error_becomes_a_value_error(monkeypatch):
    from pdfxlate.models import catalog

    def boom(*args, **kwargs):
        raise catalog.CatalogError("HTTP 404 from https://x/models: not found")

    monkeypatch.setattr(catalog, "list_models", boom)
    with pytest.raises(ValueError, match="HTTP 404"):
        sidecar.method_providers_models(_params(api_key="k"))


def test_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(sidecar, "_PROBE_TIMEOUT_SECONDS", 0.05)

    from pdfxlate.models import catalog

    def slow(*args, **kwargs):
        time.sleep(0.5)
        return ["late"]

    monkeypatch.setattr(catalog, "list_models", slow)
    with pytest.raises(TimeoutError, match="no answer"):
        sidecar.method_providers_models(_params(api_key="k"))
    time.sleep(0.6)  # let the daemon thread finish
