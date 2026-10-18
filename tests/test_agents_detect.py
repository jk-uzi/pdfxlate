"""Tests for agent CLI discovery and the pure parsers around it.

Nothing here runs a real agent.  ``shutil.which``, the Windows registry and the
subprocess probe are monkeypatched, so the suite is fast and works on every
platform.  The security invariant is checked explicitly: a login probe may
return a status, never the CLI's output (``codex login status`` prints a masked
key).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.agents import catalog, detect  # noqa: E402
from pdfxlate.agents.defs import AGENT_DEFS, get_agent_def  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_caches():
    detect._RESOLVE_CACHE.clear()
    detect._DETECT_CACHE.clear()
    catalog._MODELS_CACHE.clear()
    catalog._DSH_PROVIDERS = {}
    catalog._DSH_PROVIDER_LOADED = False
    yield
    detect._RESOLVE_CACHE.clear()
    detect._DETECT_CACHE.clear()
    catalog._MODELS_CACHE.clear()
    catalog._DSH_PROVIDERS = {}
    catalog._DSH_PROVIDER_LOADED = False


def _file(tmp_path: Path, name: str = "agent.cmd") -> Path:
    path = tmp_path / name
    path.write_text("", encoding="utf-8")
    return path


# --- executable resolution -------------------------------------------------


def test_path_override_wins_over_path(tmp_path, monkeypatch):
    override = _file(tmp_path, "my-claude.cmd")
    monkeypatch.setattr(
        detect.shutil, "which", lambda name: r"C:\from-path\claude.cmd"
    )
    resolved = detect.resolve_agent(
        get_agent_def("claude"), {"claude": str(override)}, refresh=True
    )
    assert resolved.installed is True
    assert resolved.command == (str(override),)


def test_environment_override_is_used(tmp_path, monkeypatch):
    override = _file(tmp_path, "my-codex.cmd")
    monkeypatch.setenv("PDFXLATE_CODEX_BIN", str(override))
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    resolved = detect.resolve_agent(get_agent_def("codex"), {}, refresh=True)
    assert resolved.installed is True
    assert resolved.path == str(override)


def test_path_lookup_is_used(monkeypatch):
    monkeypatch.setattr(
        detect.shutil, "which", lambda name: f"/usr/local/bin/{name}"
    )
    resolved = detect.resolve_agent(get_agent_def("claude"), {}, refresh=True)
    assert resolved.installed is True
    assert resolved.command == ("/usr/local/bin/claude",)


def test_missing_binary_reports_not_installed(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setattr(detect, "_windows_uninstall_entries", lambda: [])
    monkeypatch.setattr(detect, "_standard_bases", lambda: [])
    resolved = detect.resolve_agent(get_agent_def("claude"), {}, refresh=True)
    assert resolved.installed is False
    assert resolved.reason == "not_installed"


def test_invalid_configured_path_is_named(tmp_path):
    missing = tmp_path / "does-not-exist.cmd"
    resolved = detect.resolve_agent(
        get_agent_def("claude"), {"claude": str(missing)}, refresh=True
    )
    assert resolved.installed is False
    assert resolved.reason == "invalid_path"
    assert str(missing) in (resolved.path or "")


def test_install_directory_override_is_forgiving(tmp_path):
    """Pasting the install directory instead of the binary must work."""
    directory = tmp_path / "install"
    directory.mkdir()
    (directory / "codex.cmd").write_text("", encoding="utf-8")
    resolved = detect.resolve_agent(
        get_agent_def("codex"), {"codex": str(directory)}, refresh=True
    )
    assert resolved.installed is True
    assert resolved.command == (str(directory / "codex.cmd"),)


def test_configured_paths_can_be_scoped(monkeypatch):
    """An adapter built from a non-default config sees that config's paths."""
    from pdfxlate import config as config_mod

    def no_config(*args, **kwargs):
        raise RuntimeError("no config on disk")

    monkeypatch.setattr(config_mod, "load", no_config)
    assert detect.configured_paths() == {}
    with detect.use_configured_paths({"claude": "/opt/claude"}):
        assert detect.configured_paths() == {"claude": "/opt/claude"}
    assert detect.configured_paths() == {}


# --- version / login parsing ----------------------------------------------


def test_parse_version():
    assert detect.parse_version("2.1.153 (Claude Code)") == "2.1.153"
    assert detect.parse_version("codex-cli 0.42.0") == "0.42.0"
    assert detect.parse_version("v1.2") == "1.2"
    assert detect.parse_version("no digits here") is None
    assert detect.parse_version("") is None


def test_claude_auth_json_maps_to_status():
    assert detect.parse_claude_auth('{"loggedIn": true, "authMethod": "oauth"}') == "ok"
    assert detect.parse_claude_auth('{"loggedIn": false}') == "missing"
    assert detect.parse_claude_auth("not json") == "unknown"
    assert detect.parse_claude_auth("[]") == "unknown"


def test_claude_auth_never_returns_payload():
    """A status is all that leaves the probe, even if the payload has secrets."""
    payload = '{"loggedIn": true, "authMethod": "oauth_token", "token": "sk-secret"}'
    status = detect.parse_claude_auth(payload)
    assert status == "ok"
    assert "sk-secret" not in status
    assert "token" not in status


def test_codex_auth_reads_stderr_and_does_not_leak():
    """`codex login status` prints a masked key on stderr; only status leaves."""
    status = detect.parse_codex_auth(
        "Logged in using an API key - sk-123***456\n", 0
    )
    assert status == "ok"
    assert "sk-" not in status
    assert detect.parse_codex_auth("Not logged in", 1) == "missing"
    assert detect.parse_codex_auth("something else", 0) == "unknown"
    assert detect.parse_codex_auth("", 1) == "missing"


def test_codebuddy_auth_is_best_effort():
    assert detect.parse_codebuddy_auth("Logged in as a@b.c", 0) == "ok"
    assert detect.parse_codebuddy_auth("Not logged in", 1) == "missing"
    # An unsupported `auth status` must not be reported as "logged out".
    assert detect.parse_codebuddy_auth("unknown command: auth", 1) == "unknown"


def test_check_auth_probe_output_never_leaks(monkeypatch):
    def fake_probe(command, args, timeout):
        return 0, "", "Logged in using an API key - sk-super-secret\n"

    monkeypatch.setattr(detect, "_run_probe", fake_probe)
    status = detect.check_auth(get_agent_def("codex"), ("codex",))
    assert status == "ok"
    assert "sk-super-secret" not in status


def test_dsh_has_no_login_probe():
    assert detect.check_auth(get_agent_def("dsh"), ("dsh",)) == "unknown"


# --- registry / standard directories --------------------------------------


def test_derive_install_dir_from_icon():
    assert detect.derive_install_dir(r"D:\ZCode\uninstallerIcon.ico") == Path(
        r"D:\ZCode"
    )
    assert detect.derive_install_dir(r"D:\WorkBuddy\WorkBuddy.exe,0") == Path(
        r"D:\WorkBuddy"
    )
    assert detect.derive_install_dir(r"D:\Tools") == Path(r"D:\Tools")
    assert detect.derive_install_dir("") is None
    assert detect.derive_install_dir(None) is None


def test_zcode_is_found_through_the_registry(tmp_path, monkeypatch):
    install = tmp_path / "ZCode"
    script = install / "resources" / "glm" / "zcode.cjs"
    script.parent.mkdir(parents=True)
    script.write_text("// zcode", encoding="utf-8")

    monkeypatch.setattr(
        detect,
        "_windows_uninstall_entries",
        lambda: [
            {
                "name": "ZCode 3.15.3",
                "install_location": "",
                "display_icon": str(install / "uninstallerIcon.ico"),
            }
        ],
    )
    monkeypatch.setattr(detect, "_standard_bases", lambda: [])
    monkeypatch.setattr(
        detect.shutil, "which", lambda name: r"C:\nodejs\node.exe" if name == "node" else None
    )

    resolved = detect.resolve_agent(get_agent_def("zcode"), {}, refresh=True)
    assert resolved.installed is True
    assert resolved.reason is None
    assert resolved.command == (r"C:\nodejs\node.exe", str(script))


def test_zcode_without_node_reports_node_missing(tmp_path, monkeypatch):
    script = tmp_path / "zcode.cjs"
    script.write_text("// zcode", encoding="utf-8")
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    resolved = detect.resolve_agent(
        get_agent_def("zcode"), {"zcode": str(script)}, refresh=True
    )
    assert resolved.installed is False
    assert resolved.reason == "node_missing"


def test_workbuddy_desktop_only_is_app_only(tmp_path, monkeypatch):
    install = tmp_path / "WorkBuddy"
    install.mkdir()
    (install / "WorkBuddy.exe").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        detect,
        "_windows_uninstall_entries",
        lambda: [
            {
                "name": "WorkBuddy 5.8.2",
                "install_location": "",
                "display_icon": str(install / "WorkBuddy.exe") + ",0",
            }
        ],
    )
    monkeypatch.setattr(detect, "_standard_bases", lambda: [])
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)

    resolved = detect.resolve_agent(get_agent_def("workbuddy"), {}, refresh=True)
    assert resolved.installed is False
    assert resolved.reason == "app_only"

    detection = detect.detect_agent(get_agent_def("workbuddy"), {}, refresh=True)
    assert detection.supported is False
    assert detection.installed is False


def test_dsh_is_found_through_install_location(tmp_path, monkeypatch):
    install = tmp_path / "DeepSeek Harness"
    binary = install / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"
    binary.parent.mkdir(parents=True)
    binary.write_text("@echo off", encoding="utf-8")

    monkeypatch.setattr(
        detect,
        "_windows_uninstall_entries",
        lambda: [
            {
                "name": "DeepSeek Harness 0.2.1",
                "install_location": str(install),
            }
        ],
    )
    monkeypatch.setattr(detect, "_standard_bases", lambda: [])
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)

    resolved = detect.resolve_agent(get_agent_def("dsh"), {}, refresh=True)
    assert resolved.installed is True
    assert resolved.path == str(binary)


def test_dsh_provider_for_model_uses_the_resolved_command(monkeypatch):
    """The lazy dump must use the adapter's executable, not the disk config."""
    seen: list[tuple[list[str], list[str]]] = []

    def fake_probe(command, args, timeout):
        seen.append((list(command), list(args)))
        return (
            0,
            "- id: llm-pi-ai\n"
            "  config:\n"
            "    providers:\n"
            "      deepseek-official:\n"
            "        models:\n"
            "          - id: deepseek-pro\n",
            "",
        )

    monkeypatch.setattr(detect, "_run_probe", fake_probe)
    catalog._DSH_PROVIDERS = {}
    catalog._DSH_PROVIDER_LOADED = False
    provider = catalog.provider_for_model(
        "dsh", "deepseek-pro", command=("C:/DeepSeek/dsh.cmd",)
    )
    assert provider == "deepseek-official"
    assert seen == [
        (
            ["C:/DeepSeek/dsh.cmd"],
            ["--profile", "headless", "--dump-config"],
        )
    ]


# --- detection result shape ------------------------------------------------


def test_detection_to_dict_shape(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(detect, "_run_probe", lambda command, args, timeout: (0, "2.1.0", ""))
    detection = detect.detect_agent(get_agent_def("claude"), {}, refresh=True)
    payload = detection.to_dict()
    assert set(payload) == {
        "id",
        "name",
        "installed",
        "path",
        "version",
        "auth",
        "reason",
        "install_url",
        "docs_url",
        "supported",
    }
    assert payload["id"] == "claude"
    assert payload["installed"] is True
    assert payload["version"] == "2.1.0"


def test_detect_all_covers_every_agent(monkeypatch):
    monkeypatch.setattr(
        detect,
        "detect_agent",
        lambda defn, paths, refresh: detect.blank_detection(defn),
    )
    results = detect.detect_all({}, refresh=True)
    assert [entry.id for entry in results] == [defn.id for defn in AGENT_DEFS]
    assert all(entry.reason == "not_installed" for entry in results)


def test_provisional_detection_keeps_installed_status(monkeypatch):
    """A probe that timed out must not report an installed CLI as missing."""
    monkeypatch.setattr(detect, "_windows_uninstall_entries", lambda: [])
    monkeypatch.setattr(detect, "_standard_bases", lambda: [])
    monkeypatch.setattr(detect.shutil, "which", lambda name: f"/bin/{name}")
    provisional = detect.provisional_detection(get_agent_def("claude"), {})
    assert provisional.installed is True
    assert provisional.path == "/bin/claude"
    assert provisional.version is None
    assert provisional.auth == "unknown"
    assert provisional.reason is None


# --- model-list parsers ----------------------------------------------------


def test_codex_models_filters_hidden_and_keeps_fences_in_descriptions():
    payload = {
        "models": [
            {
                "slug": "gpt-5",
                "display_name": "GPT-5",
                "visibility": "list",
                # Real entries contain markdown fences; parsing must not be
                # confused by them.
                "description": "Use ```json\n{}\n``` in your answer.",
            },
            {"slug": "internal", "visibility": "hide"},
            {"slug": "gpt-5", "display_name": "duplicate"},
            {"display_name": "no slug"},
        ]
    }
    models = catalog.parse_codex_models(payload)
    assert models == [{"id": "gpt-5", "label": "GPT-5"}]


def test_codex_models_accepts_json_text():
    text = '{"models": [{"slug": "o3", "display_name": "o3", "visibility": "list"}]}'
    assert catalog.parse_codex_models(text) == [{"id": "o3", "label": "o3"}]
    assert catalog.parse_codex_models("not json") == []


def test_codebuddy_help_parses_currently_supported():
    text = "Usage: codebuddy -p\n\nCurrently supported: (default, sonnet, opus)\n"
    assert catalog.parse_codebuddy_help(text) == [
        {"id": "default", "label": "default"},
        {"id": "sonnet", "label": "sonnet"},
        {"id": "opus", "label": "opus"},
    ]
    assert catalog.parse_codebuddy_help("nothing here") == []


def test_dsh_config_tolerates_js_tags():
    text = """\
- id: plugin-manager
  name: '@deepseek-ai/dsh-plugin-manager'
  disabled: !!js '!ctx.get(''profileContext'')'
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: deepseek-official
    model: deepseek-v4-flash
- id: llm-pi-ai
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      deepseek-official:
        models:
          - id: deepseek-pro
            name: DeepSeek Pro
      local:
        models:
          - id: qwen3
- id: timer
  disabled: !!js >-
    process.platform === 'win32'
"""
    parsed = catalog.parse_dsh_config(text)
    ids = [model["id"] for model in parsed["models"]]
    assert ids == ["deepseek-v4-flash", "deepseek-pro", "qwen3"]
    assert parsed["default"] == ("deepseek-official", "deepseek-v4-flash")
    assert parsed["providers"]["deepseek-pro"] == "deepseek-official"
    assert parsed["providers"]["qwen3"] == "local"


def test_dsh_config_invalid_yaml_returns_empty():
    parsed = catalog.parse_dsh_config("[[[ not: yaml")
    assert parsed == {"models": [], "providers": {}, "default": None}


def test_dsh_provider_for_model():
    assert catalog.provider_for_model("dsh", "default") is None
    assert catalog.provider_for_model("claude", "sonnet") is None
    # An explicit provider/model spelling is accepted.
    assert catalog.provider_for_model("dsh", "local/qwen3") == "local"


def test_dsh_provider_for_model_refreshes_once(monkeypatch):
    """A configured model may reach the adapter before the panel listed models."""
    catalog._DSH_PROVIDERS = {"deepseek-pro": "deepseek-official"}
    catalog._DSH_PROVIDER_LOADED = False
    assert catalog.provider_for_model("dsh", "deepseek-pro") == "deepseek-official"

    calls: list[tuple[str, bool]] = []

    def fake_list(agent, refresh=False, paths=None):
        calls.append((agent, refresh))
        return {"models": [], "source": "fallback"}

    catalog._DSH_PROVIDERS = {}
    catalog._DSH_PROVIDER_LOADED = False
    monkeypatch.setattr(catalog, "list_models", fake_list)
    assert catalog.provider_for_model("dsh", "unknown-model") is None
    assert calls == [("dsh", True)]
    # A second lookup must not re-run the dump for the same unknown id.
    assert catalog.provider_for_model("dsh", "unknown-model") is None
    assert len(calls) == 1
