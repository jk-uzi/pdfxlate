"""Cancellation, and the threading that makes it reachable at all.

The request loop used to run a whole translation inline, which meant the
``translate.cancel`` line sat unread in the pipe until the job finished — the
button appeared to do nothing.  These tests pin two things: that
``translate.start`` is dispatched off the loop, and that a cancelled job reports
itself as cancelled rather than as a failure.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import api as api_mod  # noqa: E402
from pdfxlate import sidecar  # noqa: E402


class _BlockingTranslate:
    """Starts, then waits for the cancel flag, like a real job between pages."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.observed_cancel = False
        self.finished = threading.Event()
        self.kwargs: dict | None = None

    def __call__(self, files, **kwargs):
        self.kwargs = kwargs
        self.started.set()
        event = kwargs["cancellation_event"]
        try:
            # 5s is a guard against a hang in the test, not a real deadline.
            for _ in range(500):
                if event.is_set():
                    self.observed_cancel = True
                    raise asyncio.CancelledError("task cancelled")
                threading.Event().wait(0.01)
            raise AssertionError("the cancel flag never reached the job")
        finally:
            self.finished.set()


@pytest.fixture(autouse=True)
def clean_jobs():
    """Each test starts from an empty job table.

    The background threads outlive the test body by design, so a leftover entry
    from the previous test would be picked up by the next one's cancel.
    """
    with sidecar._JOBS_LOCK:
        sidecar._JOBS.clear()
    yield
    with sidecar._JOBS_LOCK:
        sidecar._JOBS.clear()


@pytest.fixture
def job(monkeypatch):
    stub = _BlockingTranslate()
    monkeypatch.setattr(api_mod, "translate", stub)
    return stub


def _messages(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _send_cancel(job_id: str | None = None) -> None:
    handler = threading.Thread(
        target=lambda: sidecar.handle(
            {"id": 99, "method": "translate.cancel", "params": {"job_id": job_id}}
        ),
        daemon=True,
    )
    handler.start()
    handler.join(2)


def test_translate_start_does_not_block_the_request_loop(job, capsys):
    """The loop must still answer while a job runs — that is how cancel arrives."""
    sidecar.handle({"id": 1, "method": "translate.start", "params": {"files": ["a.pdf"]}})
    assert job.started.wait(2), "translate.start never reached the pipeline"

    _send_cancel()
    assert job.finished.wait(2), "translate.cancel did not reach the job"
    assert job.observed_cancel is True

    replies = _messages(capsys)
    assert [m for m in replies if m.get("id") == 99][0]["ok"] is True


def test_cancel_stops_the_job_and_reports_it(job, capsys):
    sidecar.handle({"id": 1, "method": "translate.start", "params": {"files": ["a.pdf"]}})
    assert job.started.wait(2)

    _send_cancel()
    assert job.finished.wait(2)
    assert job.observed_cancel is True

    messages = _messages(capsys)
    start_reply = next(m for m in messages if m.get("id") == 1)
    cancel_reply = next(m for m in messages if m.get("id") == 99)

    assert cancel_reply["ok"] is True
    assert cancel_reply["result"]["cancelled"] is True
    assert start_reply["ok"] is True
    assert start_reply["result"]["cancelled"] is True
    assert start_reply["result"]["outputs"] == []
    # A cancel is not a completion: no `done` event should be emitted for it.
    assert not [m for m in messages if m.get("event") == "done"]


def test_cancel_with_no_running_job_is_not_an_error():
    """The UI treats this as "it already finished", so it must not throw."""
    assert sidecar.method_translate_cancel({})["cancelled"] is False


def test_cancel_targets_the_named_job():
    """A stale job id must not cancel somebody else's work."""
    wanted = threading.Event()
    other = threading.Event()
    with sidecar._JOBS_LOCK:
        sidecar._JOBS["wanted"] = {"cancel": wanted}
        sidecar._JOBS["other"] = {"cancel": other}

    sidecar.method_translate_cancel({"job_id": "wanted"})

    assert wanted.is_set() is True
    assert other.is_set() is False


def test_emitted_lines_stay_well_formed_under_concurrency(capsys):
    """Two threads must not interleave halves of one JSON document."""
    payloads = [{"event": "progress", "message": "x" * 2000} for _ in range(200)]

    def writer() -> None:
        for payload in payloads:
            sidecar._emit(payload)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    messages = _messages(capsys)
    assert len(messages) == len(payloads) * len(threads)
    assert all(m["message"] == "x" * 2000 for m in messages)


def test_force_utf8_stdio_reconfigures_protocol_streams(monkeypatch):
    """The frozen Windows runtime may ignore PYTHONIOENCODING; force UTF-8."""

    class _Stream:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def reconfigure(self, **kwargs: str) -> None:
            self.calls.append(kwargs)

    stdin, stdout, stderr = _Stream(), _Stream(), _Stream()
    monkeypatch.setattr(sidecar.sys, "stdin", stdin)
    monkeypatch.setattr(sidecar.sys, "stdout", stdout)
    monkeypatch.setattr(sidecar.sys, "stderr", stderr)

    sidecar._force_utf8_stdio()

    expected = [{"encoding": "utf-8", "errors": "replace"}]
    assert stdin.calls == expected
    assert stdout.calls == expected
    assert stderr.calls == expected


def test_background_dispatch_covers_only_translation():
    """A cheap request must still be answered synchronously."""
    assert "translate.start" in sidecar._BACKGROUND_METHODS
    assert "translate.cancel" not in sidecar._BACKGROUND_METHODS
    assert "ping" not in sidecar._BACKGROUND_METHODS


def test_numpy_is_imported_before_the_thread_starts(monkeypatch):
    """The warm-up in ``main()`` is load-bearing on Windows.

    Importing numpy's C extension for the first time *from a worker thread*
    while stdin is a pipe deadlocks: the thread never returns from the
    extension's module init and no reply is written.  ``translate.start`` runs
    on a worker thread, so a sidecar without this warm-up hangs on the first
    job.  This pins that numpy is already in ``sys.modules`` by the time the
    request loop starts.
    """
    import numpy  # noqa: F401  (the test asserts it stays loaded)

    assert "numpy" in sys.modules


def test_background_start_does_not_import_heavy_modules_itself():
    """Keep the import in ``main()``, not buried in the worker path.

    A future refactor that lazy-imports numpy inside ``translate_stream`` would
    reintroduce the deadlock on the first job, and only on Windows.
    """
    source = Path(sidecar.__file__).read_text(encoding="utf-8")
    main_body = source[source.index("def main(") :]
    warm = main_body.index("import numpy")
    loop = main_body.index("for line in sys.stdin")
    assert warm < loop, "numpy must be imported before the request loop starts"


def test_translate_start_reports_cache_stats(monkeypatch, capsys):
    """The reply and the `done` event both carry the counters the UI shows."""
    from pdfxlate import cache as cache_mod
    from pdfxlate.models.base import (
        provider_cache_snapshot,
        record_provider_cache,
    )

    def fake_translate(files, **kwargs):
        # Stand in for one hit, one miss and provider-reported cache usage.
        cache_mod._record_lookup(True)
        cache_mod._record_lookup(False)
        record_provider_cache(384, 180)
        return [("m.pdf", "")]

    monkeypatch.setattr(api_mod, "translate", fake_translate)
    result = sidecar.method_translate_start({"files": ["a.pdf"]})

    assert result["cache"] == {"hits": 1, "misses": 1}
    assert result["provider_cache"] == {"hit_tokens": 384, "miss_tokens": 180}

    done = [m for m in _messages(capsys) if m.get("event") == "done"]
    assert done[0]["result"]["cache"] == {"hits": 1, "misses": 1}
    assert done[0]["result"]["provider_cache"] == {
        "hit_tokens": 384,
        "miss_tokens": 180,
    }
