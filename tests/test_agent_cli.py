"""Tests for the agent CLI adapter: batching, parsing, retries and lifecycle.

The adapter is exercised against a fake executable (the current Python
interpreter plus a tiny script) for the process-boundary behaviour, and against
monkeypatched internals for everything that would otherwise need a real model
call.  No agent is ever invoked and no quota is consumed.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import config as config_mod  # noqa: E402
from pdfxlate.agents import runtime  # noqa: E402
from pdfxlate.agents.batch import (  # noqa: E402
    BatchQueue,
    BatchQueueClosedError,
    MAX_BATCH_ITEMS,
)
from pdfxlate.agents.defs import get_agent_def  # noqa: E402
from pdfxlate.agents.detect import ResolvedAgent  # noqa: E402
from pdfxlate.models import agent_cli  # noqa: E402
from pdfxlate.models.base import (  # noqa: E402
    PERMANENT_TRANSLATION_ERRORS,
    ModelAdapter,
    build_adapter,
    get_adapter,
    registered,
)


@pytest.fixture(autouse=True)
def _clear_caches(tmp_path, monkeypatch):
    from pdfxlate.agents import catalog, detect

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


def _profile(agent: str = "claude", model: str = "sonnet"):
    return config_mod.ResolvedModel(
        kind="agent_cli",
        agent=agent,
        provider=agent,
        alias=model,
        model=model,
    )


@pytest.fixture
def fake_resolution(monkeypatch):
    """Pretend the CLI is installed so the adapter can be constructed."""
    resolved = ResolvedAgent(
        defn=get_agent_def("claude"),
        installed=True,
        command=("fake-agent",),
        path="fake-agent",
        reason=None,
    )
    monkeypatch.setattr(agent_cli, "resolve_agent", lambda *a, **k: resolved)
    monkeypatch.setattr(agent_cli, "configured_paths", lambda: {})
    return resolved


def _adapter(prompt: str | None = None, agent: str = "claude") -> agent_cli.AgentCliAdapter:
    return agent_cli.AgentCliAdapter(
        _profile(agent=agent), "en", "zh", prompt=prompt
    )


# --- registry / cache identity --------------------------------------------


def test_registry_contains_agent_cli():
    assert "agent_cli" in registered()
    assert get_adapter("agent_cli") is agent_cli.AgentCliAdapter


def test_cache_params_include_agent_and_batch_contract(fake_resolution):
    adapter = _adapter()
    try:
        params = adapter._cache_params()
        assert params["agent"] == "claude"
        assert params["agent_batch"] == "v1"
        # The base fields are still there.
        assert params["model"] == "sonnet"
        assert params["lang_out"] == "zh"
    finally:
        adapter.close()


def test_base_cache_params_do_not_gain_agent_fields():
    """HTTP adapters must not be invalidated by the agent feature."""

    class Dummy(ModelAdapter):
        name = "dummy_cache_params"

        def do_translate(self, text: str) -> str:
            return text

    adapter = Dummy(_profile("claude"), "en", "zh")
    params = adapter._cache_params()
    assert "agent" not in params
    assert "agent_batch" not in params


def test_different_agents_have_different_cache_keys(fake_resolution):
    first = _adapter(agent="claude")
    second = _adapter(agent="codex")
    try:
        assert first._cache_params()["agent"] != second._cache_params()["agent"]
    finally:
        first.close()
        second.close()


def test_build_adapter_assigns_cancellation_event(fake_resolution):
    event = threading.Event()
    adapter = build_adapter(
        "agent_cli", _profile(), "en", "zh", cache_enabled=False, cancellation_event=event
    )
    try:
        assert adapter.cancellation_event is event
    finally:
        adapter.close()


def test_missing_agent_rejected_before_any_process():
    profile = config_mod.ResolvedModel(kind="agent_cli", model="sonnet")
    with pytest.raises(ValueError, match="no 'agent'"):
        agent_cli.AgentCliAdapter(profile, "en", "zh")


def test_not_installed_adapter_raises_clearly(monkeypatch):
    resolved = ResolvedAgent(
        defn=get_agent_def("claude"),
        installed=False,
        command=None,
        path=None,
        reason="not_installed",
    )
    monkeypatch.setattr(agent_cli, "resolve_agent", lambda *a, **k: resolved)
    monkeypatch.setattr(agent_cli, "configured_paths", lambda: {})
    with pytest.raises(runtime.AgentNotInstalledError, match="not found"):
        _adapter()


# --- batch queue -----------------------------------------------------------


def test_batch_queue_groups_up_to_eight_items():
    batches: list[list[str]] = []

    def runner(texts):
        batches.append(list(texts))
        return [text.upper() for text in texts]

    queue = BatchQueue(runner, flush_delay=0.3)
    barrier = threading.Barrier(10)
    results: dict[int, str] = {}

    def work(index: int) -> None:
        barrier.wait()
        results[index] = queue.submit(f"text-{index}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    queue.close()

    assert len(results) == 10
    assert sorted(results.values()) == sorted(f"TEXT-{i}" for i in range(10))
    assert sum(len(batch) for batch in batches) == 10
    assert max(len(batch) for batch in batches) <= MAX_BATCH_ITEMS


def test_batch_queue_respects_the_character_limit():
    batches: list[list[str]] = []

    def runner(texts):
        batches.append(list(texts))
        return list(texts)

    queue = BatchQueue(runner, max_chars=10, flush_delay=0.3)
    barrier = threading.Barrier(3)
    results: list[str] = []

    def work(index: int) -> None:
        barrier.wait()
        results.append(queue.submit("12345"))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    queue.close()

    assert sorted(results) == ["12345"] * 3
    assert sum(len(batch) for batch in batches) == 3
    assert all(sum(len(text) for text in batch) <= 10 for batch in batches)


def test_batch_queue_flushes_after_the_delay():
    def runner(texts):
        return list(texts)

    queue = BatchQueue(runner, flush_delay=0.1)
    started = time.monotonic()
    try:
        assert queue.submit("hello") == "hello"
    finally:
        queue.close()
    elapsed = time.monotonic() - started
    assert 0.08 <= elapsed < 2.0


def test_close_wakes_and_rejects_new_work():
    queue = BatchQueue(lambda texts: list(texts), flush_delay=5.0)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            queue.submit("slow")
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            errors.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    time.sleep(0.1)
    queue.close()
    thread.join(timeout=5)

    assert errors and isinstance(errors[0], BatchQueueClosedError)
    with pytest.raises(BatchQueueClosedError, match="closed"):
        queue.submit("later")


def test_queue_restarts_after_idle_shutdown():
    queue = BatchQueue(lambda texts: list(texts), idle_timeout=0.05, flush_delay=0.0)
    try:
        assert queue.submit("first") == "first"
        time.sleep(0.2)  # let the worker hit its idle timeout and exit
        assert queue.submit("again") == "again"
    finally:
        queue.close()


# --- adapter batching / custom prompts ------------------------------------


def test_custom_prompt_forces_batch_size_one(fake_resolution, monkeypatch):
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(
        defn,
        command,
        model,
        texts,
        lang_out,
        custom_prompt=None,
        timeout=None,
        cancel_event=None,
        **kwargs,
    ):
        calls.append((list(texts), custom_prompt))
        return [f"<{text}>" for text in texts]

    monkeypatch.setattr(runtime, "run_batch", fake_run)
    adapter = _adapter(prompt="Translate ${text} into ${lang_out}")
    try:
        assert adapter._queue.max_items == 1
        assert adapter.do_translate("hello") == "<hello>"
    finally:
        adapter.close()
    assert calls == [(["hello"], "Translate ${text} into ${lang_out}")]


def test_default_prompt_uses_the_normal_batch_size(fake_resolution):
    adapter = _adapter()
    try:
        assert adapter._queue.max_items == MAX_BATCH_ITEMS
        assert adapter._queue.max_chars == 12000
    finally:
        adapter.close()


def test_argv_agent_gets_the_smaller_char_budget(fake_resolution):
    adapter = _adapter(agent="zcode")
    try:
        assert adapter._queue.max_chars == 8000
    finally:
        adapter.close()


def test_adapter_close_closes_the_queue(fake_resolution):
    adapter = _adapter()
    adapter.close()
    with pytest.raises(BatchQueueClosedError):
        adapter.do_translate("hello")


# --- output parsing --------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"translations": ["a", "b"]}, ["a", "b"]),
        ({"0": "a", "1": "b"}, ["a", "b"]),
        ({"1": "a", "2": "b"}, ["a", "b"]),
        (["a", "b"], ["a", "b"]),
        ('```json\n{"translations": ["a", "b"]}\n```', ["a", "b"]),
    ],
)
def test_parse_priority(payload, expected):
    assert runtime.parse_translations(payload, ["x", "y"]) == expected


def test_parse_rejects_an_array_that_echoes_the_input():
    assert runtime.parse_translations(["Hello.", "World."], ["Hello.", "World."]) is None


def test_parse_wrong_length_is_rejected():
    assert runtime.parse_translations(["a"], ["x", "y"]) is None
    assert runtime.parse_translations({"translations": ["a"]}, ["x", "y"]) is None


def test_parse_single_plain_text():
    assert runtime.parse_translations("你好", ["Hello."]) == ["你好"]
    assert runtime.parse_translations("你好", ["Hello.", "World."]) is None
    assert runtime.parse_translations("", ["Hello."]) is None


def test_parse_ignores_ansi_sequences():
    assert runtime.parse_translations("\x1b[32m你好\x1b[0m", ["Hello."]) == ["你好"]


def test_parse_index_object_rejects_non_string_values():
    assert runtime.parse_translations({"0": 1, "1": 2}, ["a", "b"]) is None


# --- retries / splitting ---------------------------------------------------


def test_run_batch_retries_strictly_then_splits(monkeypatch):
    calls: list[tuple[list[str], bool]] = []

    def fake_attempt(
        defn,
        command,
        model,
        texts,
        lang_out,
        custom_prompt,
        timeout,
        cancel_event,
        *,
        strict,
        lang_in="en",
    ):
        calls.append((list(texts), strict))
        if len(texts) > 1:
            raise runtime.AgentOutputError("could not parse")
        return [f"T:{texts[0]}"]

    monkeypatch.setattr(runtime, "_run_attempt", fake_attempt)
    result = runtime.run_batch(
        get_agent_def("claude"), ("fake",), None, ["a", "b"], "zh"
    )
    assert result == ["T:a", "T:b"]
    assert calls == [
        (["a", "b"], False),
        (["a", "b"], True),
        (["a"], True),
        (["b"], True),
    ]


def test_run_batch_single_paragraph_fails_after_two_attempts(monkeypatch):
    calls: list[bool] = []

    def fake_attempt(*args, strict, **kwargs):
        calls.append(strict)
        raise runtime.AgentOutputError("could not parse")

    monkeypatch.setattr(runtime, "_run_attempt", fake_attempt)
    with pytest.raises(runtime.AgentOutputError):
        runtime.run_batch(get_agent_def("claude"), ("fake",), None, ["a"], "zh")
    assert calls == [False, True]


def test_custom_prompt_skips_retries(monkeypatch):
    calls: list[str] = []

    def fake_attempt(
        defn,
        command,
        model,
        texts,
        lang_out,
        custom_prompt,
        timeout,
        cancel_event,
        **kwargs,
    ):
        calls.append(custom_prompt)
        raise runtime.AgentOutputError("empty")

    monkeypatch.setattr(runtime, "_run_attempt", fake_attempt)
    with pytest.raises(runtime.AgentOutputError):
        runtime.run_batch(
            get_agent_def("claude"),
            ("fake",),
            None,
            ["a", "b"],
            "zh",
            custom_prompt="${text}",
        )
    # Custom templates run once per paragraph; there is no batch contract to
    # retry against.
    assert calls == ["${text}"]


# --- errors / process boundary --------------------------------------------


def _fake_workdir(tmp_path: Path):
    counter = {"n": 0}

    def make() -> Path:
        counter["n"] += 1
        path = tmp_path / f"work-{counter['n']}"
        path.mkdir()
        return path

    return make


def test_auth_failure_is_permanent_and_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_make_workdir", _fake_workdir(tmp_path))
    monkeypatch.setattr(
        runtime,
        "_run_process",
        lambda *a, **k: (1, "", "Error: not logged in. Run `claude login`."),
    )
    with pytest.raises(runtime.AgentAuthError, match="not logged in"):
        runtime.run_batch(get_agent_def("claude"), ("fake",), None, ["a"], "zh")
    assert runtime.AgentAuthError in PERMANENT_TRANSLATION_ERRORS


def test_process_error_truncates_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_make_workdir", _fake_workdir(tmp_path))
    monkeypatch.setattr(runtime, "_run_process", lambda *a, **k: (2, "", "x" * 1000))
    with pytest.raises(runtime.AgentProcessError) as caught:
        runtime.run_batch(get_agent_def("claude"), ("fake",), None, ["a"], "zh")
    message = str(caught.value)
    assert "x" * 300 in message
    assert "x" * 400 not in message
    assert len(message) < 500


def test_output_error_mentions_truncated_output(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_make_workdir", _fake_workdir(tmp_path))
    monkeypatch.setattr(runtime, "_run_process", lambda *a, **k: (0, "", ""))
    with pytest.raises(runtime.AgentOutputError, match="could not parse"):
        runtime.run_batch(get_agent_def("claude"), ("fake",), None, ["a"], "zh")


def test_timeout_kills_the_process(tmp_path, monkeypatch):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    started = time.monotonic()
    with pytest.raises(runtime.AgentTimeoutError):
        runtime.run_batch(
            get_agent_def("claude"),
            (sys.executable, str(script)),
            None,
            ["a"],
            "zh",
            timeout=0.4,
        )
    assert time.monotonic() - started < 15


def test_cancel_kills_the_process(tmp_path, monkeypatch):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    try:
        with pytest.raises(asyncio.CancelledError):
            runtime.run_batch(
                get_agent_def("claude"),
                (sys.executable, str(script)),
                None,
                ["a"],
                "zh",
                timeout=30,
                cancel_event=cancel,
            )
    finally:
        timer.cancel()


def test_fake_cli_end_to_end(tmp_path, monkeypatch):
    """A real child process: prompt on stdin, JSON on stdout, parsed back."""
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import json, sys\n"
        "raw = sys.stdin.read()\n"
        "texts = json.loads(raw.rsplit('\\n\\n', 1)[-1])\n"
        "print(json.dumps({'translations': [t.upper() for t in texts]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    result = runtime.run_batch(
        get_agent_def("claude"),
        (sys.executable, str(script)),
        None,
        ["hello", "world"],
        "zh",
        timeout=20,
    )
    assert result == ["HELLO", "WORLD"]


def test_custom_prompt_renders_template():
    rendered = runtime.render_custom_prompt(
        "Translate ${text} from ${lang_in} to ${lang_out}", "Hello.", "en", "zh"
    )
    assert rendered == "Translate Hello. from en to zh"
    # A malformed `$` must not raise (safe_substitute, mirroring the base class).
    assert runtime.render_custom_prompt("Cost $5: ${text}", "x", "en", "zh") == "Cost $5: x"


# --- DSH desktop-profile patch --------------------------------------------

#: Synthetic desktop profile: the current default is w1/model-a, a second
#: provider serves the same bare model id, and a secret value is planted in
#: the provider config to prove it never reaches the patch.
DSH_PROFILE = """\
- id: agent-default-model
  config:
    provider: w1
    model: model-a
- id: llm-pi-ai
  config:
    providers:
      w1:
        displayName: Relay One
        api: openai-responses
        baseURL: https://relay-one.invalid/v1
        apiKeyEnv: W1_API_KEY
        apiKey: sk-should-never-leak
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
"""


@pytest.fixture
def dsh_home(tmp_path, monkeypatch):
    """A synthetic ``$DSH_HOME`` with a profile and one dummy credential."""
    home = tmp_path / "dsh-home"
    profile = home / "profiles" / "desktop" / "cordis.patch.yml"
    profile.parent.mkdir(parents=True)
    profile.write_text(DSH_PROFILE, encoding="utf-8")
    (home / ".credentials.yaml").write_text(
        "refs:\n  W1_API_KEY: dummy-w1-key\n", encoding="utf-8"
    )
    monkeypatch.setenv("DSH_HOME", str(home))
    from pdfxlate.agents import catalog

    catalog._DSH_DESKTOP_CACHE.clear()
    catalog._DSH_PROVIDER_LOADED = True  # never run a dump probe in these tests
    return home


def test_build_dsh_patch_includes_provider_and_default_model(dsh_home):
    patch = yaml.safe_load(runtime.build_dsh_patch("w1", "model-a"))
    assert [entry["id"] for entry in patch] == ["llm-pi-ai", "agent-default-model"]
    assert patch[0]["name"] == "@deepseek-ai/dsh-llm-pi-ai"
    assert patch[1]["name"] == "@deepseek-ai/dsh-agent-default-model"
    provider = patch[0]["config"]["providers"]["w1"]
    assert provider["apiKeyEnv"] == "W1_API_KEY"
    assert provider["models"][0]["id"] == "model-a"
    assert patch[1]["config"] == {"provider": "w1", "model": "model-a"}
    dumped = yaml.safe_dump(patch)
    assert "sk-should-never-leak" not in dumped
    assert "dummy-w1-key" not in dumped


def test_build_dsh_patch_falls_back_for_an_unknown_provider(dsh_home):
    patch = yaml.safe_load(runtime.build_dsh_patch("missing", "model-a"))
    assert patch == [
        {
            "id": "agent-default-model",
            "name": "@deepseek-ai/dsh-agent-default-model",
            "config": {"provider": "missing", "model": "model-a"},
        }
    ]
    without_provider = yaml.safe_load(runtime.build_dsh_patch(None, "model-a"))
    assert without_provider[0]["config"] == {"model": "model-a"}


def test_split_agent_model(dsh_home):
    assert runtime.split_agent_model("w1/model-a") == ("w1", "model-a")
    # Only the first slash is the separator; OpenRouter model ids keep theirs.
    assert runtime.split_agent_model("w1/vendor/model-b") == ("w1", "vendor/model-b")
    # A bare id containing a slash is recognised from the desktop profile.
    assert runtime.split_agent_model("vendor/model-b") == ("w1", "vendor/model-b")
    # model-a is served by both providers, so the desktop default provider wins.
    assert runtime.split_agent_model("model-a") == ("w1", "model-a")
    assert runtime.split_agent_model("default") == (None, "default")
    assert runtime.split_agent_model(None) == (None, "")


def _fake_dsh_script(tmp_path: Path) -> Path:
    """A CLI that records argv, the patch, and the injected key, then answers."""
    script = tmp_path / "fake_dsh.py"
    script.write_text(
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "patch = None\n"
        "if '--patch' in argv:\n"
        "    with open(argv[argv.index('--patch') + 1], encoding='utf-8') as fh:\n"
        "        patch = fh.read()\n"
        "raw = sys.stdin.read()\n"
        "texts = json.loads(raw.rsplit('\\n\\n', 1)[-1])\n"
        "with open(os.environ['CAPTURE'], 'w', encoding='utf-8') as fh:\n"
        "    json.dump({'argv': argv, 'patch': patch, "
        "'key': os.environ.get('W1_API_KEY')}, fh)\n"
        "print(json.dumps({'translations': [t.upper() for t in texts]}))\n",
        encoding="utf-8",
    )
    return script


def test_dsh_run_uses_the_patch_and_injects_the_credential(
    dsh_home, tmp_path, monkeypatch
):
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("CAPTURE", str(capture))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("W1_API_KEY", raising=False)
    result = runtime.run_batch(
        get_agent_def("dsh"),
        (sys.executable, str(_fake_dsh_script(tmp_path))),
        "w1/model-a",
        ["hello"],
        "zh",
        timeout=20,
    )
    assert result == ["HELLO"]

    data = json.loads(capture.read_text(encoding="utf-8"))
    assert "--patch" in data["argv"]
    patch_path = data["argv"][data["argv"].index("--patch") + 1]
    # The overlay lives inside the run's disposable cwd and is gone afterwards.
    assert not Path(patch_path).exists()

    patch = yaml.safe_load(data["patch"])
    assert [entry["id"] for entry in patch] == ["llm-pi-ai", "agent-default-model"]
    assert patch[1]["config"] == {"provider": "w1", "model": "model-a"}
    # The key reaches the child's environment, never the patch or a log.
    assert data["key"] == "dummy-w1-key"
    assert "dummy-w1-key" not in data["patch"]
    assert "sk-should-never-leak" not in data["patch"]


def test_dsh_default_model_has_no_patch(dsh_home, tmp_path, monkeypatch):
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("CAPTURE", str(capture))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("W1_API_KEY", raising=False)
    result = runtime.run_batch(
        get_agent_def("dsh"),
        (sys.executable, str(_fake_dsh_script(tmp_path))),
        "default",
        ["hello"],
        "zh",
        timeout=20,
    )
    assert result == ["HELLO"]
    data = json.loads(capture.read_text(encoding="utf-8"))
    assert "--patch" not in data["argv"]
    assert data["patch"] is None
    # No patch means no provider config, so no credential is injected either.
    assert data["key"] is None


def test_composite_dsh_model_id_survives_config_round_trip(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "providers:\n"
        "  dsh:\n"
        "    kind: agent_cli\n"
        "    agent: dsh\n"
        "    models:\n"
        "      w1/model-a:\n"
        "        model: w1/model-a\n",
        encoding="utf-8",
    )
    cfg = config_mod.load(path)
    assert cfg.providers["dsh"].models["w1/model-a"].model == "w1/model-a"
    config_mod.save(cfg, path)
    reloaded = config_mod.load(path)
    assert reloaded.providers["dsh"].models["w1/model-a"].model == "w1/model-a"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["providers"]["dsh"]["models"]["w1/model-a"]["model"] == "w1/model-a"


# --- ZCode current-model guard --------------------------------------------


def _build_zcode(
    tmp_path: Path,
    model: str | None,
    current: tuple[str, str] | None,
    monkeypatch,
) -> tuple[list[str], str | None, dict[str, str]]:
    """Build ZCode's argv with the current model and database mocked out."""
    from pdfxlate.agents import catalog

    monkeypatch.setattr(
        catalog, "zcode_current_model", lambda refresh=False: current
    )
    workdir = tmp_path / "zcode-work"
    workdir.mkdir()
    return runtime._build_command(
        get_agent_def("zcode"),
        ("fake-node", "zcode.cjs"),
        model,
        "hello",
        custom_prompt=False,
        workdir=workdir,
        last_message_path=tmp_path / "last.txt",
    )


def test_zcode_rejects_a_non_current_model(tmp_path, monkeypatch):
    with pytest.raises(runtime.AgentError, match="current model") as caught:
        _build_zcode(tmp_path, "p1/other-model", ("p1", "m1"), monkeypatch)
    message = str(caught.value)
    assert "p1/m1" in message
    assert "switch models inside ZCode" in message


def test_zcode_accepts_its_current_model(tmp_path, monkeypatch):
    argv, stdin_prompt, _env = _build_zcode(tmp_path, "p1/m1", ("p1", "m1"), monkeypatch)
    # The CLI has no --model flag; the prompt is still the argv payload.
    assert "--model" not in argv
    assert argv[:2] == ["fake-node", "zcode.cjs"]
    assert argv[argv.index("-p") + 1] == "hello"
    assert stdin_prompt is None


def test_zcode_accepts_a_bare_current_model_id(tmp_path, monkeypatch):
    argv, _stdin, _env = _build_zcode(tmp_path, "m1", ("p1", "m1"), monkeypatch)
    assert "--model" not in argv


def test_zcode_default_model_is_not_a_selection(tmp_path, monkeypatch):
    # "default" means "leave the CLI alone"; there is nothing to verify.
    argv, _stdin, _env = _build_zcode(tmp_path, "default", ("p1", "m1"), monkeypatch)
    assert "--model" not in argv


def test_zcode_unknown_current_model_does_not_block(tmp_path, monkeypatch):
    # If the database cannot be read the CLI may still know its own selection.
    argv, _stdin, _env = _build_zcode(tmp_path, "p1/other-model", None, monkeypatch)
    assert "--model" not in argv


def test_zcode_guard_runs_before_the_process(tmp_path, monkeypatch):
    """A mismatch is rejected before any CLI process is spawned."""
    from pdfxlate.agents import catalog

    monkeypatch.setattr(
        catalog, "zcode_current_model", lambda refresh=False: ("p1", "m1")
    )
    monkeypatch.setattr(runtime, "_make_workdir", _fake_workdir(tmp_path))

    def must_not_spawn(*args, **kwargs):
        raise AssertionError("the ZCode guard must run before spawning")

    monkeypatch.setattr(runtime, "_run_process", must_not_spawn)
    with pytest.raises(runtime.AgentError, match="current model"):
        runtime.run_batch(
            get_agent_def("zcode"), ("fake",), "p1/other-model", ["a"], "zh"
        )


# --- API wiring ------------------------------------------------------------


class _FakeTranslator:
    def __init__(self, kind: str = "agent_cli"):
        from types import SimpleNamespace

        self.profile = SimpleNamespace(kind=kind)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _ultrafast_config(thread: int = 2):
    return config_mod.AppConfig(
        translation={"lang_in": "en", "lang_out": "zh", "thread": thread, "mode": "ultrafast"}
    )


def test_api_bumps_agent_threads_to_at_least_eight(monkeypatch):
    from pdfxlate import api

    translator = _FakeTranslator()
    monkeypatch.setattr(api, "build_translator", lambda *a, **k: translator)
    seen: dict = {}
    monkeypatch.setattr(
        api.pipeline,
        "translate",
        lambda files, **kwargs: seen.update(kwargs) or [],
    )
    api.translate(["a.pdf"], config=_ultrafast_config(thread=2))
    assert seen["thread"] == 8
    assert translator.closed is True


def test_api_keeps_a_larger_user_thread_count(monkeypatch):
    from pdfxlate import api

    translator = _FakeTranslator()
    monkeypatch.setattr(api, "build_translator", lambda *a, **k: translator)
    seen: dict = {}
    monkeypatch.setattr(
        api.pipeline,
        "translate",
        lambda files, **kwargs: seen.update(kwargs) or [],
    )
    api.translate(["a.pdf"], config=_ultrafast_config(thread=16))
    assert seen["thread"] == 16


def test_api_does_not_bump_http_provider_threads(monkeypatch):
    from pdfxlate import api

    translator = _FakeTranslator(kind="openai_compat")
    monkeypatch.setattr(api, "build_translator", lambda *a, **k: translator)
    seen: dict = {}
    monkeypatch.setattr(
        api.pipeline,
        "translate",
        lambda files, **kwargs: seen.update(kwargs) or [],
    )
    api.translate(["a.pdf"], config=_ultrafast_config(thread=2))
    assert seen["thread"] == 2


def test_api_closes_the_translator_on_failure(monkeypatch):
    from pdfxlate import api

    translator = _FakeTranslator()
    monkeypatch.setattr(api, "build_translator", lambda *a, **k: translator)

    def boom(files, **kwargs):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(api.pipeline, "translate", boom)
    with pytest.raises(RuntimeError, match="pipeline exploded"):
        api.translate(["a.pdf"], config=_ultrafast_config())
    assert translator.closed is True


def test_cli_list_models_names_the_agent(tmp_path, capsys):
    from pdfxlate import cli

    path = tmp_path / "config.yaml"
    path.write_text(
        "providers:\n"
        "  claude:\n"
        "    kind: agent_cli\n"
        "    agent: claude\n"
        "    models:\n"
        "      sonnet:\n"
        "        model: sonnet\n",
        encoding="utf-8",
    )
    assert cli.main(["--list-models", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "claude @ agent:claude:" in out
    assert "(no base_url)" not in out

def _zcode_resolution(monkeypatch):
    resolved = ResolvedAgent(
        defn=get_agent_def("zcode"),
        installed=True,
        command=("fake-zcode",),
        path="fake-zcode",
        reason=None,
    )
    monkeypatch.setattr(agent_cli, "resolve_agent", lambda *a, **k: resolved)
    monkeypatch.setattr(agent_cli, "configured_paths", lambda: {})
    return resolved


def test_zcode_non_current_model_uses_the_app_server(monkeypatch):
    _zcode_resolution(monkeypatch)
    monkeypatch.setattr(
        agent_cli.catalog,
        "zcode_current_model",
        lambda: ("new-provider-2", "deepseek-v4.1-flash"),
    )
    calls: list = []

    class FakeSession:
        def __init__(self, provider, model, **kwargs):
            calls.append(("init", provider, model))

        def generate(self, messages, **kwargs):
            calls.append(("generate", messages))
            return '{"translations": ["你好"]}'

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(agent_cli.zcode_app_server, "TranslationSession", FakeSession)
    adapter = agent_cli.AgentCliAdapter(
        _profile("zcode", "new-provider-2/hy3"), "en", "zh", ignore_cache=True
    )
    try:
        assert adapter._backend == "zcode_app_server"
        assert adapter.translate("hello") == "你好"
        assert calls[0] == ("init", "new-provider-2", "hy3")
        assert calls[1][0] == "generate"
        assert calls[1][1][0]["role"] == "system"
        assert "Translate every string" in calls[1][1][1]["content"]
    finally:
        adapter.close()
    assert calls[-1] == ("close",)


def test_zcode_current_model_keeps_the_cli_backend(monkeypatch):
    _zcode_resolution(monkeypatch)
    monkeypatch.setattr(
        agent_cli.catalog,
        "zcode_current_model",
        lambda: ("new-provider-2", "deepseek-v4.1-flash"),
    )
    adapter = agent_cli.AgentCliAdapter(
        _profile("zcode", "new-provider-2/deepseek-v4.1-flash"),
        "en",
        "zh",
        ignore_cache=True,
    )
    try:
        assert adapter._backend == "cli"
        assert adapter._zcode_session is None
    finally:
        adapter.close()
