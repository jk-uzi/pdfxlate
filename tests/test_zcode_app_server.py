"""Tests for the ZCode app-server connectivity client (fake stdio server)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pdfxlate.agents import zcode_app_server
from pdfxlate.agents.zcode_app_server import ServerPaths, ZCodeAppServerError

FAKE = Path(__file__).with_name("fake_zcode_app_server.py")


@pytest.fixture
def server_paths(tmp_path) -> ServerPaths:
    return ServerPaths(
        command=(sys.executable, str(FAKE)),
        builtin_config=tmp_path / "zcode-builtin.json",
        personal_config=tmp_path / "provider_config.json",
        data_base=tmp_path,
    )


def test_connectivity_returns_ok_and_sends_the_selection(
    server_paths, tmp_path, monkeypatch
):
    selection_file = tmp_path / "selection.json"
    monkeypatch.setenv("FAKE_ZCODE_SELECTION_FILE", str(selection_file))
    result = zcode_app_server.test_model(
        "new-provider-2", "hy3", server_paths=server_paths, timeout=10
    )
    assert result["ok"] is True
    assert result["via"] == "app_server"
    assert isinstance(result["latency_ms"], int)
    payload = json.loads(selection_file.read_text(encoding="utf-8"))
    assert payload["selection"] == {
        "providerId": "new-provider-2",
        "modelId": "hy3",
    }
    assert payload["workspace"]["workspaceKey"] == "pdfxlate-agent-test"
    assert payload["workspace"]["workspacePath"].endswith("workspace")


def test_app_server_error_is_readable(server_paths, monkeypatch):
    monkeypatch.setenv("FAKE_ZCODE_ERROR", "1")
    with pytest.raises(ZCodeAppServerError, match="boom"):
        zcode_app_server.test_model(
            "new-provider-2", "hy3", server_paths=server_paths, timeout=10
        )


def test_app_server_timeout_kills_the_process(server_paths, monkeypatch):
    monkeypatch.setenv("FAKE_ZCODE_DELAY", "5")
    with pytest.raises(ZCodeAppServerError, match="did not answer in time"):
        zcode_app_server.test_model(
            "new-provider-2", "hy3", server_paths=server_paths, timeout=0.5
        )


def test_resolve_paths_from_the_resource_layout(tmp_path, monkeypatch):
    root = tmp_path / "resources"
    cli = root / "glm" / "zcode.cjs"
    cli.parent.mkdir(parents=True)
    cli.write_text("// cli", encoding="utf-8")
    builtin = root / "config" / "provider" / "zcode-builtin.json"
    builtin.parent.mkdir(parents=True)
    builtin.write_text("{}", encoding="utf-8")
    home = tmp_path / "home"
    personal = home / ".zcode" / "v2" / "provider_config.json"
    personal.parent.mkdir(parents=True)
    personal.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(zcode_app_server.shutil, "which", lambda name: "C:/node.exe")

    resolved = zcode_app_server.resolve_paths(cli_path=str(cli), home=home)
    assert resolved.builtin_config == builtin
    assert resolved.personal_config == personal
    assert resolved.data_base == home
    assert resolved.command == ("C:/node.exe", str(cli))


def test_resolve_paths_reports_a_missing_builtin(tmp_path, monkeypatch):
    cli = tmp_path / "resources" / "glm" / "zcode.cjs"
    cli.parent.mkdir(parents=True)
    cli.write_text("// cli", encoding="utf-8")
    home = tmp_path / "home"
    personal = home / ".zcode" / "v2" / "provider_config.json"
    personal.parent.mkdir(parents=True)
    personal.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(zcode_app_server.shutil, "which", lambda name: "C:/node.exe")
    with pytest.raises(ZCodeAppServerError, match="built-in provider config"):
        zcode_app_server.resolve_paths(cli_path=str(cli), home=home)
