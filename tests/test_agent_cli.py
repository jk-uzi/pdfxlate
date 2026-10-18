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
def _clear_caches():
    from pdfxlate.agents import catalog, detect

    detect._RESOLVE_CACHE.clear()
    detect._DETECT_CACHE.clear()
    catalog._MODELS_CACHE.clear()
    yield


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
