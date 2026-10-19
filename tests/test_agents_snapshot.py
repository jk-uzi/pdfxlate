"""Tests for the persisted agents snapshot and the cache-first list."""

from __future__ import annotations

import json

import pytest

import pdfxlate.agents as agents_pkg
from pdfxlate import paths, sidecar
from pdfxlate.agents import detect, snapshot
from pdfxlate.agents.defs import get_agent_def


def _entry(agent_id: str = "dsh") -> dict:
    return {
        "id": agent_id,
        "name": "DeepSeek Harness",
        "installed": True,
        "path": "C:/DeepSeek/dsh.cmd",
        "version": None,
        "auth": "unknown",
        "reason": None,
        "install_url": "https://example.invalid/install",
        "docs_url": "https://example.invalid/docs",
        "supported": True,
        "models": [{"id": "default", "label": "default"}],
        "models_source": "profile",
        "models_error": None,
    }


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(snapshot.paths, "config_dir", lambda: tmp_path)
    return tmp_path


def test_snapshot_round_trip_and_path_signature(config_dir):
    agents = [_entry()]
    snapshot.save(agents, {"dsh": "C:/DeepSeek/dsh.cmd"})
    assert snapshot.load({"dsh": "C:/DeepSeek/dsh.cmd"}) == agents
    # A path override changed since the snapshot: force a live detection.
    assert snapshot.load({}) is None
    assert snapshot.load({"dsh": "C:/other/dsh.cmd"}) is None


def test_snapshot_ignores_corrupt_and_old_files(config_dir):
    snapshot.snapshot_path().write_text("{not json", encoding="utf-8")
    assert snapshot.load({}) is None
    snapshot.snapshot_path().write_text(
        json.dumps({"version": 0, "paths": {}, "agents": [_entry()]}),
        encoding="utf-8",
    )
    assert snapshot.load({}) is None


def test_snapshot_contains_no_credentials(config_dir):
    snapshot.save([_entry()], {})
    text = snapshot.snapshot_path().read_text(encoding="utf-8")
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "apiKey" not in text
    assert "sk-" not in text


def test_update_agent_replaces_models_and_desktop(config_dir):
    snapshot.save([_entry()], {})
    updated = {
        "models": [{"id": "glm-5.3", "label": "GLM-5.3"}],
        "source": "desktop",
        "error": None,
        "desktop": {"available": True, "auth": "ok", "app_path": "C:/W.exe"},
    }
    assert snapshot.update_agent("dsh", updated, {}) is True
    entry = snapshot.load({})[0]
    assert entry["models"] == updated["models"]
    assert entry["models_source"] == "desktop"
    assert entry["desktop"]["auth"] == "ok"
    assert entry["installed"] is True


def test_list_agents_returns_snapshot_without_detecting(config_dir, monkeypatch):
    monkeypatch.setattr(detect, "configured_paths", lambda: {})
    snapshot.save([_entry()], {})

    def unexpected(*_args, **_kwargs):
        raise AssertionError("live detection must not run when a snapshot exists")

    monkeypatch.setattr(agents_pkg.detect, "detect_agent", unexpected)
    monkeypatch.setattr(agents_pkg.catalog, "list_models", unexpected)
    assert agents_pkg.list_agents(refresh=False) == [_entry()]


def test_list_agents_refresh_runs_live_and_saves(config_dir, monkeypatch):
    monkeypatch.setattr(detect, "configured_paths", lambda: {})
    calls: list[str] = []

    def fake_detect(defn, paths, refresh):
        calls.append(defn.id)
        return detect.blank_detection(defn)

    def fake_models(agent, refresh=False, paths=None):
        calls.append(f"models:{agent}")
        return {"models": [], "source": "fallback"}

    monkeypatch.setattr(agents_pkg.detect, "detect_agent", fake_detect)
    monkeypatch.setattr(agents_pkg.catalog, "list_models", fake_models)
    entries = agents_pkg.list_agents(refresh=True)
    assert [entry["id"] for entry in entries] == [defn.id for defn in agents_pkg.AGENT_DEFS]
    assert len(calls) == len(agents_pkg.AGENT_DEFS) * 2
    assert snapshot.load({}) is not None


def test_list_agents_without_snapshot_runs_live_once(config_dir, monkeypatch):
    monkeypatch.setattr(detect, "configured_paths", lambda: {})
    calls: list[str] = []

    def fake_detect(defn, paths, refresh):
        calls.append(defn.id)
        return detect.blank_detection(defn)

    monkeypatch.setattr(agents_pkg.detect, "detect_agent", fake_detect)
    monkeypatch.setattr(
        agents_pkg.catalog,
        "list_models",
        lambda agent, refresh=False, paths=None: {"models": [], "source": "fallback"},
    )
    agents_pkg.list_agents(refresh=False)
    assert len(calls) == len(agents_pkg.AGENT_DEFS)
    assert snapshot.load({}) is not None
    # The second call is served from the snapshot.
    agents_pkg.list_agents(refresh=False)
    assert len(calls) == len(agents_pkg.AGENT_DEFS)


def test_agents_models_updates_the_snapshot(config_dir, monkeypatch):
    monkeypatch.setattr(detect, "configured_paths", lambda: {})
    snapshot.save([_entry("workbuddy")], {})
    monkeypatch.setattr(
        agents_pkg.catalog,
        "list_models",
        lambda agent, refresh=False, paths=None: {
            "models": [{"id": "hy3", "label": "Hy3"}],
            "source": "desktop",
            "desktop": {"available": True, "auth": "ok"},
        },
    )
    result = sidecar.method_agents_models({"agent": "workbuddy", "refresh": True})
    assert result["models"][0]["id"] == "hy3"
    entry = snapshot.load({})[0]
    assert entry["models"][0]["id"] == "hy3"
    assert entry["models_source"] == "desktop"
    assert entry["desktop"]["auth"] == "ok"


def test_detect_agent_does_not_probe_login(monkeypatch):
    defn = get_agent_def("claude")
    resolved = detect.ResolvedAgent(
        defn=defn,
        installed=True,
        command=("C:/claude.exe",),
        path="C:/claude.exe",
        reason=None,
    )
    monkeypatch.setattr(detect, "resolve_agent", lambda *_args, **_kwargs: resolved)
    monkeypatch.setattr(detect, "_probe_version", lambda _command: "2.1.0")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("detect_agent must not probe login state")

    monkeypatch.setattr(detect, "check_auth", unexpected)
    detection = detect.detect_agent(defn, {}, refresh=True)
    assert detection.installed is True
    assert detection.version == "2.1.0"
    assert detection.auth == "unknown"
