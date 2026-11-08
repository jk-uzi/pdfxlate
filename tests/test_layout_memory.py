"""Memory behaviour of the layout model: the arena switch and release.

The real model is 71 MB on disk and inference is slow, so the session is built
against a fake onnxruntime.  What matters here is that the configured value
reaches ``SessionOptions`` and that a finished job hands the cached model back
instead of pinning it for the life of the process.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import api as api_mod  # noqa: E402
from pdfxlate import config as config_mod  # noqa: E402
from pdfxlate.pdf import layout  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_module_state(monkeypatch):
    """Reset the module-level switches and the cached model for every test."""
    monkeypatch.setattr(layout, "_preferred_backend", None)
    monkeypatch.setattr(layout, "_memory_arena", False)
    monkeypatch.setattr(layout.ModelInstance, "value", None)
    yield


# --- configuration ---------------------------------------------------------


def test_cpu_mem_arena_is_off_by_default():
    assert config_mod.LayoutSettings().cpu_mem_arena is False
    assert config_mod.AppConfig().layout.cpu_mem_arena is False


@pytest.mark.parametrize("value", [False, True])
def test_cpu_mem_arena_round_trips_through_yaml(tmp_path, value):
    path = tmp_path / "config.yaml"
    config_mod.save(config_mod.AppConfig(layout={"cpu_mem_arena": value}), path)

    written = path.read_text(encoding="utf-8")
    assert f"cpu_mem_arena: {'true' if value else 'false'}" in written
    assert config_mod.load(path).layout.cpu_mem_arena is value


# --- SessionOptions wiring -------------------------------------------------


class _FakeSessionOptions:
    """Stands in for onnxruntime.SessionOptions without native code."""

    def __init__(self):
        # The runtime's own default; the code under test must override it.
        self.enable_cpu_mem_arena = True
        self.graph_optimization_level = None
        self.optimized_model_filepath = None


class _FakeInferenceSession:
    created: list["_FakeInferenceSession"] = []

    def __init__(self, model_path, sess_options, providers=None):
        self.model_path = model_path
        self.sess_options = sess_options
        self.providers = providers or []
        _FakeInferenceSession.created.append(self)

    def get_providers(self):
        return self.providers


def _fake_onnx_load(path, **kwargs):
    """Metadata is all OnnxModel reads before building the session."""
    return types.SimpleNamespace(
        metadata_props=[
            types.SimpleNamespace(key="stride", value="32"),
            types.SimpleNamespace(key="names", value="['title', 'plain text']"),
        ]
    )


@pytest.fixture
def fake_ort(monkeypatch):
    _FakeInferenceSession.created.clear()
    monkeypatch.setattr(layout.onnx, "load", _fake_onnx_load)
    monkeypatch.setattr(layout.onnxruntime, "SessionOptions", _FakeSessionOptions)
    monkeypatch.setattr(layout.onnxruntime, "InferenceSession", _FakeInferenceSession)
    return _FakeInferenceSession


def _build_session() -> _FakeInferenceSession:
    layout.set_backend("cpu")
    layout.OnnxModel("not-a-real-model.onnx")
    return _FakeInferenceSession.created[-1]


def test_session_arena_is_disabled_when_switched_off(fake_ort):
    layout.set_memory_arena(False)
    assert _build_session().sess_options.enable_cpu_mem_arena is False


def test_session_arena_is_enabled_when_switched_on(fake_ort):
    layout.set_memory_arena(True)
    assert _build_session().sess_options.enable_cpu_mem_arena is True


def test_loading_applies_the_configured_switch(monkeypatch):
    """_load_layout_model must read the setting, not just the backend."""
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(layout, "set_backend", lambda name: seen.append(("backend", name)))
    monkeypatch.setattr(
        layout, "set_memory_arena", lambda enabled: seen.append(("arena", enabled))
    )
    monkeypatch.setattr(layout.ModelInstance, "value", object())

    cfg = config_mod.AppConfig(layout={"cpu_mem_arena": True})
    api_mod._load_layout_model(cfg, None)

    assert ("backend", "auto") in seen
    assert ("arena", True) in seen


# --- release ---------------------------------------------------------------


def test_release_clears_the_cached_model():
    layout.ModelInstance.value = object()

    api_mod.release_layout_model()

    assert layout.ModelInstance.value is None


def _record_release(monkeypatch) -> list:
    calls: list = []

    def record():
        calls.append(True)

    monkeypatch.setattr(api_mod, "release_layout_model", record)
    monkeypatch.setattr(api_mod, "build_translator", lambda *a, **k: object())
    return calls


def _cfg() -> config_mod.AppConfig:
    return config_mod.AppConfig(
        providers={
            "prov": {
                "kind": "openai_compat",
                "base_url": "https://example.test/v1",
                "models": {"chat": {"model": "m"}},
            }
        }
    )


def test_translate_releases_the_model_after_success(monkeypatch, tmp_path):
    calls = _record_release(monkeypatch)
    monkeypatch.setattr(api_mod.pipeline, "translate", lambda *a, **k: [])

    api_mod.translate(["a.pdf"], config=_cfg(), output=str(tmp_path), mode="ultrafast")

    assert calls == [True]


def test_translate_releases_the_model_after_failure(monkeypatch, tmp_path):
    calls = _record_release(monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("job failed")

    monkeypatch.setattr(api_mod.pipeline, "translate", fail)

    with pytest.raises(RuntimeError, match="job failed"):
        api_mod.translate(
            ["a.pdf"], config=_cfg(), output=str(tmp_path), mode="ultrafast"
        )

    assert calls == [True]