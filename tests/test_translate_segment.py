"""The per-paragraph retry loop in the converter.

This used to be ``@retry(wait=wait_fixed(1))`` with tenacity's defaults, i.e.
retry every ``Exception`` forever.  A wrong protocol (404) or a bad key (401)
therefore hung the job permanently, and the cancellation flag was only checked
between pages, so it could not be stopped either.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.models.base import (  # noqa: E402
    EmptyTranslationError,
    PlaceholderLossError,
    TruncatedResponseError,
)
from pdfxlate.pdf import converter as converter_mod  # noqa: E402
from pdfxlate.pdf.converter import translate_segment  # noqa: E402


class FlakyTranslator:
    """Fails ``fail_times`` times with ``exc``, then succeeds."""

    def __init__(self, fail_times: int, exc: BaseException | None = None):
        self.fail_times = fail_times
        self.exc = exc or ConnectionError("boom")
        self.calls = 0

    def translate(self, text: str) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return "ok"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """The backoff is a real sleep; tests must not wait for it."""
    monkeypatch.setattr(converter_mod.time, "sleep", lambda _seconds: None)


def test_succeeds_on_the_first_attempt():
    translator = FlakyTranslator(fail_times=0)
    assert translate_segment(translator, "x") == "ok"
    assert translator.calls == 1


def test_recovers_within_the_attempt_budget():
    translator = FlakyTranslator(fail_times=2)
    assert translate_segment(translator, "x", max_attempts=3) == "ok"
    assert translator.calls == 3


def test_gives_up_after_max_attempts():
    translator = FlakyTranslator(fail_times=99)
    with pytest.raises(ConnectionError, match="boom"):
        translate_segment(translator, "x", max_attempts=3)
    # Bounded, not infinite: this is the whole point of the change.
    assert translator.calls == 3


@pytest.mark.parametrize(
    "exc",
    [
        PlaceholderLossError({"0"}, "text {v0}", "text"),
        TruncatedResponseError(4096, "text"),
        EmptyTranslationError("text"),
    ],
)
def test_permanent_errors_are_not_retried(exc):
    """The same input reproduces the same failure, so retrying only delays it."""
    translator = FlakyTranslator(fail_times=99, exc=exc)
    with pytest.raises(type(exc)):
        translate_segment(translator, "x")
    assert translator.calls == 1


def test_cancellation_stops_before_the_next_attempt():
    event = threading.Event()
    event.set()
    translator = FlakyTranslator(fail_times=99)

    from asyncio import CancelledError

    with pytest.raises(CancelledError):
        translate_segment(translator, "x", cancellation_event=event)
    assert translator.calls == 0


def test_cancellation_stops_a_retry_loop_mid_way():
    """A failing paragraph must not outlast a cancel request."""
    event = threading.Event()
    translator = FlakyTranslator(fail_times=99)

    def cancel_after_first(seconds):
        event.set()  # the first backoff is the cancel window

    converter_mod.time.sleep = cancel_after_first

    from asyncio import CancelledError

    with pytest.raises(CancelledError):
        translate_segment(translator, "x", cancellation_event=event, max_attempts=5)
    assert translator.calls == 1
