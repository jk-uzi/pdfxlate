"""Sidecar tests for the external-agent methods.

The sidecar is the renderer's only contract, so these tests pin the exact JSON
shapes (``agents.list``, ``agents.models``, ``agents.test``) and the two rules
that protect the existing HTTP providers: ``providers.list`` now carries
``agent``, and ``providers.test`` refuses an agent provider with a pointer to
the Agents panel.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdfxlate.agents as agents_pkg  # noqa: E402
from pdfxlate import config as config_mod  # noqa: E402
from pdfxlate import sidecar  # noqa: E402
from pdfxlate.agents import catalog, runtime  # noqa: E402

CONFIG = """\
providers:
  claude:
    kind: agent_cli
    agent: claude
    models:
      sonnet:
        model: sonnet
  http:
    kind: openai_compat
    base_url: https://example.invalid/v1
    api_key: saved-key
    models:
      m: {}
"""


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    cfg = config_mod.load(path)
    monkeypatch.setattr(config_mod, "load", lambda path=None: cfg)
    return cfg


# --- method registration ---------------------------------------------------


def test_agent_methods_are_registered():
    assert "agents.list" in sidecar.METHODS
    assert "agents.models" in sidecar.METHODS
    assert "agents.test" in sidecar.METHODS
    # A real model call must not block the stdin request loop.
    assert "agents.test" in sidecar._BACKGROUND_METHODS


# --- agents.list -----------------------------------------------------------


def _entry(agent_id: str = "claude") -> dict:
    return {
        "id": agent_id,
        "name": "Claude Code",
        "installed": True,
        "path": "/usr/bin/claude",
        "version": "2.1.0",
        "auth": "ok",
        "reason": None,
        "models": [{"id": "sonnet", "label": "Sonnet"}],
        "models_source": "fallback",
        "models_error": None,
        "install_url": "https://example.invalid/install",
        "docs_url": "https://example.invalid/docs",
        "supported": True,
    }


def test_agents_list_shape(monkeypatch):
    monkeypatch.setattr(agents_pkg, "list_agents", lambda refresh=False: [_entry()])
    result = sidecar.method_agents_list({})
    assert set(result) == {"agents"}
    entry = result["agents"][0]
    assert set(entry) == {
        "id",
        "name",
        "installed",
        "path",
        "version",
        "auth",
        "reason",
        "models",
        "models_source",
        "models_error",
        "install_url",
        "docs_url",
        "supported",
    }
    assert entry["models"] == [{"id": "sonnet", "label": "Sonnet"}]


def test_agents_list_forwards_refresh(monkeypatch):
    seen: list[bool] = []

    def fake_list(refresh: bool = False):
        seen.append(refresh)
        return []

    monkeypatch.setattr(agents_pkg, "list_agents", fake_list)
    assert sidecar.method_agents_list({"refresh": True}) == {"agents": []}
    assert sidecar.method_agents_list({}) == {"agents": []}
    assert seen == [True, False]


# --- agents.models ---------------------------------------------------------


def test_agents_models_returns_cli_list(monkeypatch):
    calls: list[tuple[str, bool]] = []

    def fake_list(agent, refresh=False, paths=None):
        calls.append((agent, refresh))
        return {"models": [{"id": "sonnet", "label": "Sonnet"}], "source": "cli"}

    monkeypatch.setattr(catalog, "list_models", fake_list)
    result = sidecar.method_agents_models({"agent": "claude", "refresh": True})
    assert result == {
        "models": [{"id": "sonnet", "label": "Sonnet"}],
        "source": "cli",
    }
    assert calls == [("claude", True)]


def test_agents_models_passes_the_desktop_profile_shape(monkeypatch):
    def fake_list(agent, refresh=False, paths=None):
        return {
            "models": [
                {"id": "default", "label": "Current model"},
                {
                    "id": "w1/model-a",
                    "label": "Model A (Relay One)",
                    "provider": "w1",
                    "current": True,
                },
            ],
            "source": "profile",
        }

    monkeypatch.setattr(catalog, "list_models", fake_list)
    result = sidecar.method_agents_models({"agent": "dsh"})
    assert result["source"] == "profile"
    assert result["models"][1] == {
        "id": "w1/model-a",
        "label": "Model A (Relay One)",
        "provider": "w1",
        "current": True,
    }


def test_agents_models_passes_the_zcode_shape(monkeypatch):
    """ZCode rows carry current/selectable; the sidecar must not reshape them."""

    def fake_list(agent, refresh=False, paths=None):
        return {
            "models": [
                {
                    "id": "p1/m1",
                    "label": "m1 (Relay One)",
                    "provider": "p1",
                    "current": False,
                    "selectable": False,
                },
                {
                    "id": "p2/m2",
                    "label": "m2 (Relay Two)",
                    "provider": "p2",
                    "current": True,
                    "selectable": True,
                },
            ],
            "source": "profile",
        }

    monkeypatch.setattr(catalog, "list_models", fake_list)
    result = sidecar.method_agents_models({"agent": "zcode"})
    assert result["source"] == "profile"
    assert result["models"][0]["selectable"] is False
    assert result["models"][1]["current"] is True
    assert result["models"][1]["selectable"] is True


def test_agents_models_unknown_agent_is_rejected():
    with pytest.raises(ValueError, match="unknown agent"):
        sidecar.method_agents_models({"agent": "nope"})


def test_agents_models_missing_agent_is_rejected():
    with pytest.raises(ValueError, match="agent is required"):
        sidecar.method_agents_models({})


def test_agents_models_failure_falls_back(monkeypatch):
    def boom(agent, refresh=False, paths=None):
        raise RuntimeError("enumeration exploded")

    monkeypatch.setattr(catalog, "list_models", boom)
    result = sidecar.method_agents_models({"agent": "codex"})
    assert result["source"] == "fallback"
    assert result["models"]
    assert "enumeration exploded" in result["error"]


def test_agents_models_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(
        sidecar,
        "_run_bounded",
        lambda work, name, timeout=None, hint=None: (_ for _ in ()).throw(
            TimeoutError("no answer within 15s")
        ),
    )
    result = sidecar.method_agents_models({"agent": "codex"})
    assert result["source"] == "fallback"
    assert "no answer" in result["error"]


# --- agents.test -----------------------------------------------------------


class _FakeAdapter:
    def __init__(self, sample: str = "你好", error: BaseException | None = None):
        self.sample = sample
        self.error = error
        self.closed = False
        self.texts: list[str] = []

    def do_translate(self, text: str) -> str:
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return self.sample

    def close(self) -> None:
        self.closed = True


def _patch_build(monkeypatch, adapter, seen: dict):
    import pdfxlate.models.base as base

    def fake_build(kind, profile, lang_in, lang_out, **kwargs):
        seen.update(
            kind=kind,
            profile=profile,
            lang_in=lang_in,
            lang_out=lang_out,
            kwargs=kwargs,
        )
        return adapter

    monkeypatch.setattr(base, "build_adapter", fake_build)


def test_agents_test_success(monkeypatch, loaded):
    adapter = _FakeAdapter(sample="你好")
    seen: dict = {}
    _patch_build(monkeypatch, adapter, seen)

    result = sidecar.method_agents_test({"agent": "claude", "model": "sonnet"})

    assert result["ok"] is True
    assert result["sample"] == "你好"
    assert isinstance(result["latency_ms"], int)
    assert adapter.closed is True
    assert adapter.texts == ["Hello."]
    # The probe is not cached, so no cache database is opened.
    assert seen["kind"] == "agent_cli"
    assert seen["profile"].agent == "claude"
    assert seen["profile"].model == "sonnet"
    assert seen["kwargs"]["cache_enabled"] is False
    # A timed-out probe must be able to stop the CLI's process tree.
    assert hasattr(seen["kwargs"]["cancellation_event"], "is_set")


def test_agents_test_reports_errors_and_closes(monkeypatch, loaded):
    adapter = _FakeAdapter(error=runtime.AgentAuthError("claude is not logged in"))
    seen: dict = {}
    _patch_build(monkeypatch, adapter, seen)

    with pytest.raises(ValueError, match="not logged in"):
        sidecar.method_agents_test({"agent": "claude"})
    assert adapter.closed is True


def test_agents_test_timeout_still_closes(monkeypatch, loaded):
    adapter = _FakeAdapter()
    seen: dict = {}
    _patch_build(monkeypatch, adapter, seen)
    monkeypatch.setattr(
        sidecar,
        "_run_bounded",
        lambda work, name, timeout=None, hint=None: (_ for _ in ()).throw(
            TimeoutError("no answer within 90s")
        ),
    )
    with pytest.raises(TimeoutError):
        sidecar.method_agents_test({"agent": "claude"})
    assert adapter.closed is True


def test_agents_test_unknown_agent_is_rejected(loaded):
    with pytest.raises(ValueError, match="unknown agent"):
        sidecar.method_agents_test({"agent": "nope"})


# --- providers.list / providers.test --------------------------------------


def test_providers_list_includes_agent(loaded):
    providers = sidecar.method_providers_list({})["providers"]
    assert providers["claude"]["agent"] == "claude"
    assert providers["claude"]["kind"] == "agent_cli"
    assert providers["http"]["agent"] is None


def test_providers_test_rejects_agent_cli(loaded):
    with pytest.raises(ValueError, match="Settings › Agents"):
        sidecar.method_providers_test(
            {
                "provider": "claude",
                "kind": "agent_cli",
                "model": "sonnet",
            }
        )


# --- _run_bounded ----------------------------------------------------------


def test_run_bounded_timeout_includes_hint():
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="check the agent"):
        sidecar._run_bounded(
            lambda: time.sleep(0.4),
            "test-slow",
            timeout=0.05,
            hint="check the agent CLI",
        )
    assert time.monotonic() - started < 2
    time.sleep(0.5)  # let the daemon thread finish before the test process moves on


def test_run_bounded_default_timeout_message(monkeypatch):
    monkeypatch.setattr(sidecar, "_PROBE_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(TimeoutError, match="no answer within"):
        sidecar._run_bounded(lambda: time.sleep(0.3), "test-default")
    time.sleep(0.4)
