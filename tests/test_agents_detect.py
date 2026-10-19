"""Tests for agent CLI discovery and the pure parsers around it.

Nothing here runs a real agent.  ``shutil.which``, the Windows registry and the
subprocess probe are monkeypatched, so the suite is fast and works on every
platform.  The security invariant is checked explicitly: a login probe may
return a status, never the CLI's output (``codex login status`` prints a masked
key).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.agents import catalog, detect  # noqa: E402
from pdfxlate.agents.defs import AGENT_DEFS, get_agent_def  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_caches(tmp_path, monkeypatch):
    # Never read the developer's real ~/.dsh or ~/.zcode in a unit test.
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    monkeypatch.setenv("ZCODE_HOME", str(tmp_path / "zcode-home"))
    detect._RESOLVE_CACHE.clear()
    detect._DETECT_CACHE.clear()
    catalog._MODELS_CACHE.clear()
    catalog._DSH_DESKTOP_CACHE.clear()
    catalog._DSH_PROVIDERS = {}
    catalog._DSH_DEFAULT = None
    catalog._DSH_PROVIDER_LOADED = False
    catalog._ZCODE_CURRENT_CACHE = None
    yield
    detect._RESOLVE_CACHE.clear()
    detect._DETECT_CACHE.clear()
    catalog._MODELS_CACHE.clear()
    catalog._DSH_DESKTOP_CACHE.clear()
    catalog._DSH_PROVIDERS = {}
    catalog._DSH_DEFAULT = None
    catalog._DSH_PROVIDER_LOADED = False
    catalog._ZCODE_CURRENT_CACHE = None


#: Synthetic desktop profile: two enabled providers, one disabled, three
#: composite models (one model id contains a slash), and a current default.
DESKTOP_PROFILE = """\
- id: agent-default-model
  name: "@deepseek-ai/dsh-agent-default-model"
  config:
    provider: w1
    model: model-a
- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      w1:
        displayName: Relay One
        api: openai-responses
        baseURL: https://relay-one.invalid/v1
        apiKeyEnv: W1_API_KEY
        apiKey: sk-should-never-leak
        headers:
          Authorization: Bearer sk-should-never-leak
          X-Trace: keep-me
        models:
          - id: model-a
            name: Model A
          - id: vendor/model-b
            name: Model B
      w:
        displayName: Relay Two
        api: openai-chat
        baseURL: https://relay-two.invalid/v1
        apiKeyEnv: W_API_KEY
        models:
          - id: model-a
            name: Model A
      off:
        enabled: false
        apiKeyEnv: OFF_API_KEY
        models:
          - id: hidden
            name: Hidden
"""


def _write_desktop_profile(tmp_path: Path, text: str = DESKTOP_PROFILE) -> Path:
    home = tmp_path / "dsh-home"
    profile = home / "profiles" / "desktop" / "cordis.patch.yml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(text, encoding="utf-8")
    return home


def _resolved_dsh(path: str = "fake-dsh") -> detect.ResolvedAgent:
    return detect.ResolvedAgent(
        defn=get_agent_def("dsh"),
        installed=True,
        command=(path,),
        path=path,
        reason=None,
    )


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


# --- DSH desktop profile ---------------------------------------------------


def test_dsh_desktop_profile_path_honours_dsh_home(tmp_path, monkeypatch):
    home = _write_desktop_profile(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(home))
    expected = home / "profiles" / "desktop" / "cordis.patch.yml"
    assert catalog.dsh_desktop_profile_path() == expected
    assert catalog.load_dsh_desktop_profile(refresh=True)["default"] == (
        "w1",
        "model-a",
    )


def test_dsh_desktop_profile_path_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "nowhere"))
    assert catalog.dsh_desktop_profile_path() is None
    assert catalog.load_dsh_desktop_profile() == {
        "models": [],
        "providers": {},
        "default": None,
    }
    assert catalog.parse_dsh_desktop_profile(tmp_path / "missing.yml") == {
        "models": [],
        "providers": {},
        "default": None,
    }


def test_parse_dsh_desktop_profile_lists_composite_models():
    parsed = catalog.parse_dsh_desktop_profile(DESKTOP_PROFILE)
    assert parsed["default"] == ("w1", "model-a")

    ids = [model["id"] for model in parsed["models"]]
    assert ids == ["w1/model-a", "w1/vendor/model-b", "w/model-a"]
    # Only the first slash separates provider from model.
    assert parsed["models"][1]["provider"] == "w1"
    assert parsed["models"][1]["label"] == "Model B (Relay One)"

    current = {model["id"]: model["current"] for model in parsed["models"]}
    assert current == {
        "w1/model-a": True,
        "w1/vendor/model-b": False,
        "w/model-a": False,
    }

    # Only the enabled providers are kept, and only the env-var *name* survives.
    assert set(parsed["providers"]) == {"w1", "w"}
    assert parsed["providers"]["w1"]["apiKeyEnv"] == "W1_API_KEY"
    assert "apiKey" not in parsed["providers"]["w1"]
    assert parsed["providers"]["w1"]["headers"] == {"X-Trace": "keep-me"}
    assert "sk-should-never-leak" not in repr(parsed)


def test_parse_dsh_desktop_profile_tolerates_js_tags_and_bad_yaml():
    text = """\
- id: timer
  disabled: !!js 'process.platform === "win32"'
- id: agent-default-model
  config:
    provider: w1
    model: model-a
- id: llm-pi-ai
  config:
    providers:
      w1:
        models:
          - id: model-a
            name: Model A
"""
    parsed = catalog.parse_dsh_desktop_profile(text)
    assert [model["id"] for model in parsed["models"]] == ["w1/model-a"]
    assert parsed["models"][0]["label"] == "Model A (w1)"
    assert catalog.parse_dsh_desktop_profile("[[[ not: yaml") == {
        "models": [],
        "providers": {},
        "default": None,
    }


def test_list_models_dsh_uses_the_desktop_profile(tmp_path, monkeypatch):
    home = _write_desktop_profile(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setattr(catalog.detect, "resolve_agent", lambda *a, **k: _resolved_dsh())

    result = catalog.list_models("dsh")
    assert result["source"] == "profile"
    assert [model["id"] for model in result["models"]] == [
        "default",
        "w1/model-a",
        "w1/vendor/model-b",
        "w/model-a",
    ]
    assert result["models"][0] == {"id": "default", "label": "Current model"}
    assert result["models"][1]["current"] is True

    # The cached answer survives a profile edit until an explicit refresh.
    profile = home / "profiles" / "desktop" / "cordis.patch.yml"
    profile.write_text(
        DESKTOP_PROFILE.replace("name: Model A", "name: Model A2", 1),
        encoding="utf-8",
    )
    assert catalog.list_models("dsh") == result
    refreshed = catalog.list_models("dsh", refresh=True)
    assert refreshed["models"][1]["label"] == "Model A2 (Relay One)"


def test_list_models_dsh_falls_back_to_dump_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "empty-dsh"))
    monkeypatch.setattr(catalog.detect, "resolve_agent", lambda *a, **k: _resolved_dsh())
    monkeypatch.setattr(
        catalog.detect,
        "_run_probe",
        lambda command, args, timeout: (
            0,
            "- id: agent-default-model\n"
            "  config:\n"
            "    provider: deepseek-official\n"
            "    model: deepseek-v4-flash\n"
            "- id: llm-pi-ai\n"
            "  config:\n"
            "    providers:\n"
            "      deepseek-official:\n"
            "        models:\n"
            "          - id: deepseek-pro\n",
            "",
        ),
    )
    result = catalog.list_models("dsh", refresh=True)
    assert result["source"] == "cli"
    assert [model["id"] for model in result["models"]] == [
        "default",
        "deepseek-v4-flash",
        "deepseek-pro",
    ]


def test_dsh_provider_for_model_uses_the_desktop_profile(tmp_path, monkeypatch):
    home = _write_desktop_profile(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(home))
    catalog._DSH_PROVIDERS = {}
    # No lazy dump probe: everything below is answered by the desktop profile.
    catalog._DSH_PROVIDER_LOADED = True

    assert catalog.provider_for_model("dsh", "w1/model-a") == "w1"
    assert catalog.provider_for_model("dsh", "w1/vendor/model-b") == "w1"
    # A bare model id containing a slash is matched exactly before splitting.
    assert catalog.provider_for_model("dsh", "vendor/model-b") == "w1"
    # model-a is served by two providers, so a bare id is ambiguous.
    assert catalog.provider_for_model("dsh", "model-a") is None
    assert catalog.provider_for_model("dsh", "unknown-model") is None
    assert catalog.provider_for_model("dsh", "default") is None
    assert catalog.provider_for_model("claude", "sonnet") is None


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


# --- ZCode provider config -------------------------------------------------

#: Synthetic ``provider_config.json``: two enabled providers, one enabled but
#: provider-less rule, one disabled provider, a disabled model, a duplicate
#: across the two rule lists, and a credential planted to prove it never leaks.
ZCODE_PROVIDER_CONFIG: dict = {
    "config": {
        "providerOrder": ["p1", "p2", "p3"],
        "providerConfigRules": {
            "providerRules": [
                {
                    "providerId": "p1",
                    "providerName": "Relay One",
                    "enabled": True,
                    "config": {"apiKey": "sk-should-never-leak"},
                },
                {
                    "providerId": "p2",
                    "providerName": "Relay Two",
                    "enabled": None,
                    "config": {"baseUrl": "https://relay-two.invalid"},
                },
                {
                    "providerId": "p3",
                    "providerName": "Off",
                    "enabled": False,
                    "config": {},
                },
                {
                    "providerId": "p4",
                    "providerName": "No Order",
                    "enabled": True,
                    "config": {},
                },
            ]
        },
        "modelConfigRules": {
            "providerModelRules": [
                {"providerId": "p1", "modelId": "m1", "config": {"enabled": True}},
                {"providerId": "p1", "modelId": "m2", "config": {"enabled": False}},
                {"providerId": "p3", "modelId": "hidden", "config": {}},
                {"providerId": "p2", "modelId": "m3", "config": {"enabled": None}},
            ],
            "manualProviderModelRules": [
                {"providerId": "p1", "modelId": "m1", "config": {}},
                {"providerId": "p4", "modelId": "m4", "config": {}},
            ],
        },
    }
}


def _write_zcode_provider_config(
    tmp_path: Path, payload: dict | None = None
) -> Path:
    home = tmp_path / "zcode-home"
    path = home / "v2" / "provider_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload or ZCODE_PROVIDER_CONFIG), encoding="utf-8")
    return home


def _write_zcode_db(home: Path, sessions: list[tuple[str, int, list[dict]]]) -> Path:
    path = home / "cli" / "db" / "db.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, time_created INTEGER)"
        )
        connection.execute("CREATE TABLE message (session_id TEXT, data TEXT)")
        for session_id, created, messages in sessions:
            connection.execute(
                "INSERT INTO session (id, time_created) VALUES (?, ?)",
                (session_id, created),
            )
            for message in messages:
                connection.execute(
                    "INSERT INTO message (session_id, data) VALUES (?, ?)",
                    (session_id, json.dumps(message)),
                )
        connection.commit()
    finally:
        connection.close()
    return path


def _resolved_zcode(path: str = "fake-zcode.cjs") -> detect.ResolvedAgent:
    return detect.ResolvedAgent(
        defn=get_agent_def("zcode"),
        installed=True,
        command=("fake-node", path),
        path=path,
        reason=None,
    )


def test_zcode_provider_config_path_honours_zcode_home(tmp_path, monkeypatch):
    home = _write_zcode_provider_config(tmp_path)
    monkeypatch.setenv("ZCODE_HOME", str(home))
    expected = home / "v2" / "provider_config.json"
    assert catalog.zcode_home() == home
    assert catalog.zcode_provider_config_path() == expected

    monkeypatch.setenv("ZCODE_HOME", str(tmp_path / "nowhere"))
    assert catalog.zcode_provider_config_path() is None


def test_parse_zcode_provider_config_filters_and_orders(tmp_path):
    parsed = catalog.parse_zcode_provider_config(
        tmp_path / "unused.json", current=("p2", "m3")
    )
    # A path that does not exist is empty, not an exception.
    assert parsed == {"models": [], "current": None, "providers": {}}

    parsed = catalog.parse_zcode_provider_config(
        json.dumps(ZCODE_PROVIDER_CONFIG), current=("p2", "m3")
    )
    assert parsed["current"] == ("p2", "m3")
    # Provider order first, then providers missing from it; disabled providers
    # and disabled models are gone.
    assert [model["id"] for model in parsed["models"]] == [
        "p1/m1",
        "p2/m3",
        "p4/m4",
    ]
    assert parsed["models"][0] == {
        "id": "p1/m1",
        "label": "m1 (Relay One)",
        "provider": "p1",
        "current": False,
        # Non-current models are translatable through the app-server now.
        "selectable": True,
    }
    assert parsed["models"][1]["label"] == "m3 (Relay Two)"
    assert parsed["models"][1]["current"] is True
    assert parsed["models"][1]["selectable"] is True
    assert parsed["models"][2]["label"] == "m4 (No Order)"

    # Only id -> name; the provider config (and its credential) is dropped.
    assert parsed["providers"] == {
        "p1": "Relay One",
        "p2": "Relay Two",
        "p4": "No Order",
    }
    assert "sk-should-never-leak" not in repr(parsed)
    assert "baseUrl" not in repr(parsed)


def test_parse_zcode_provider_config_tolerates_bad_json():
    assert catalog.parse_zcode_provider_config("not json at all") == {
        "models": [],
        "current": None,
        "providers": {},
    }
    assert catalog.parse_zcode_provider_config("[]") == {
        "models": [],
        "current": None,
        "providers": {},
    }


def test_parse_zcode_provider_config_reads_a_path(tmp_path):
    path = tmp_path / "provider_config.json"
    path.write_text(json.dumps(ZCODE_PROVIDER_CONFIG), encoding="utf-8")
    parsed = catalog.parse_zcode_provider_config(path, current=("p1", "m1"))
    assert [model["id"] for model in parsed["models"]] == [
        "p1/m1",
        "p2/m3",
        "p4/m4",
    ]
    assert parsed["models"][0]["selectable"] is True


def test_zcode_current_model_reads_latest_non_subagent_session():
    home = Path(catalog.zcode_home())
    db = _write_zcode_db(
        home,
        [
            (
                "sess_user",
                100,
                [
                    {"role": "user", "providerId": "p1", "modelId": "m1"},
                    {
                        "role": "assistant",
                        "providerId": "new-provider-2",
                        "modelId": "deepseek-v4.1-flash",
                    },
                ],
            ),
            # A newer subagent session must be ignored.
            (
                "sess_subagent_1",
                200,
                [
                    {
                        "role": "assistant",
                        "providerId": "new-provider-4",
                        "modelId": "other",
                    }
                ],
            ),
        ],
    )
    assert db.is_file()
    assert catalog.zcode_current_model(refresh=True) == (
        "new-provider-2",
        "deepseek-v4.1-flash",
    )


def test_zcode_current_model_prefers_model_selection():
    home = Path(catalog.zcode_home())
    _write_zcode_db(
        home,
        [
            (
                "sess_user",
                100,
                [
                    {
                        "role": "assistant",
                        "providerId": "top-level",
                        "modelId": "top-model",
                        "modelSelection": {
                            "providerId": "selected",
                            "modelId": "selected-model",
                        },
                    }
                ],
            )
        ],
    )
    assert catalog.zcode_current_model(refresh=True) == (
        "selected",
        "selected-model",
    )


def test_zcode_current_model_prefers_the_newest_message():
    """The production schema is ordered by time_created, not insertion order."""
    db = Path(catalog.zcode_home()) / "cli" / "db" / "db.sqlite"
    db.parent.mkdir(parents=True)
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, time_created INTEGER)"
        )
        connection.execute(
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, "
            "time_created INTEGER, data TEXT)"
        )
        connection.execute(
            "INSERT INTO session (id, time_created) VALUES ('sess_user', 100)"
        )
        # Insert the newer message first so a rowid-based read would get it
        # wrong; only time_created points at it.
        for message_id, created, provider in (
            ("m_new", 200, "p2"),
            ("m_old", 100, "p1"),
        ):
            connection.execute(
                "INSERT INTO message (id, session_id, time_created, data) "
                "VALUES (?, 'sess_user', ?, ?)",
                (
                    message_id,
                    created,
                    json.dumps(
                        {
                            "role": "assistant",
                            "providerId": provider,
                            "modelId": f"{provider}-model",
                        }
                    ),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    assert catalog.zcode_current_model(refresh=True) == ("p2", "p2-model")


def test_zcode_current_model_missing_or_corrupt_returns_none():
    # No database at all.
    assert catalog.zcode_current_model(refresh=True) is None

    # A file that is not a database: locked/corrupt is "unknown", not fatal.
    home = Path(catalog.zcode_home())
    db = home / "cli" / "db" / "db.sqlite"
    db.parent.mkdir(parents=True)
    db.write_text("this is not sqlite", encoding="utf-8")
    assert catalog.zcode_current_model(refresh=True) is None


def test_zcode_current_model_caches_until_refresh():
    home = Path(catalog.zcode_home())
    _write_zcode_db(
        home,
        [
            (
                "sess_user",
                100,
                [
                    {
                        "role": "assistant",
                        "providerId": "p1",
                        "modelId": "m1",
                    }
                ],
            )
        ],
    )
    assert catalog.zcode_current_model() == ("p1", "m1")
    # A second read inside the TTL comes from the cache.
    catalog._ZCODE_CURRENT_CACHE = (
        catalog._ZCODE_CURRENT_CACHE[0],
        ("cached", "value"),
    )
    assert catalog.zcode_current_model() == ("cached", "value")
    assert catalog.zcode_current_model(refresh=True) == ("p1", "m1")


def test_list_models_zcode_uses_the_provider_config(tmp_path, monkeypatch):
    home = _write_zcode_provider_config(tmp_path)
    monkeypatch.setenv("ZCODE_HOME", str(home))
    monkeypatch.setattr(
        catalog.detect, "resolve_agent", lambda *a, **k: _resolved_zcode()
    )
    monkeypatch.setattr(
        catalog, "zcode_current_model", lambda refresh=False: ("p2", "m3")
    )

    result = catalog.list_models("zcode")
    assert result["source"] == "profile"
    assert [model["id"] for model in result["models"]] == [
        "p1/m1",
        "p2/m3",
        "p4/m4",
    ]
    assert result["models"][1]["current"] is True
    assert result["models"][1]["selectable"] is True
    # Non-current ZCode models are now selectable (translated via the
    # official app-server one-off generation, not the CLI).
    assert result["models"][0]["selectable"] is True

    # The cached answer survives a profile edit until an explicit refresh.
    path = home / "v2" / "provider_config.json"
    changed = json.loads(json.dumps(ZCODE_PROVIDER_CONFIG))
    changed["config"]["providerConfigRules"]["providerRules"][0][
        "providerName"
    ] = "Renamed"
    path.write_text(json.dumps(changed), encoding="utf-8")
    assert catalog.list_models("zcode") == result
    refreshed = catalog.list_models("zcode", refresh=True)
    assert refreshed["models"][0]["label"] == "m1 (Renamed)"


def test_list_models_zcode_without_a_profile_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ZCODE_HOME", str(tmp_path / "no-zcode"))
    monkeypatch.setattr(
        catalog.detect, "resolve_agent", lambda *a, **k: _resolved_zcode()
    )
    result = catalog.list_models("zcode")
    assert result["source"] == "fallback"
    assert result["models"] == [{"id": "default", "label": "Current model"}]
    assert "error" not in result


def test_zcode_provider_for_model():
    assert catalog.provider_for_model("zcode", "p1/m1") == "p1"
    # Only the first slash separates provider from model.
    assert catalog.provider_for_model("zcode", "p1/vendor/m") == "p1"
    assert catalog.provider_for_model("zcode", "m1") is None
    assert catalog.provider_for_model("zcode", "default") is None
    assert catalog.provider_for_model("zcode", "") is None
    assert catalog.provider_for_model("claude", "sonnet") is None

