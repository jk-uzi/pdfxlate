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
def _clear_caches(tmp_path, monkeypatch):
    # Never read the developer's real ~/.dsh in a unit test.
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    detect._RESOLVE_CACHE.clear()
    detect._DETECT_CACHE.clear()
    catalog._MODELS_CACHE.clear()
    catalog._DSH_DESKTOP_CACHE.clear()
    catalog._DSH_PROVIDERS = {}
    catalog._DSH_DEFAULT = None
    catalog._DSH_PROVIDER_LOADED = False
    yield
    detect._RESOLVE_CACHE.clear()
    detect._DETECT_CACHE.clear()
    catalog._MODELS_CACHE.clear()
    catalog._DSH_DESKTOP_CACHE.clear()
    catalog._DSH_PROVIDERS = {}
    catalog._DSH_DEFAULT = None
    catalog._DSH_PROVIDER_LOADED = False


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
