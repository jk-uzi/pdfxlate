"""A batching queue in front of one agent CLI.

The PDF pipeline calls the adapter from several threads at once.  Spawning one
CLI per paragraph would be both slow (each CLI pays its own startup) and
expensive (every call re-reads the prompt), so calls are grouped:

* up to **8** paragraphs per batch,
* at most **12000** characters of source text (**8000** for argv-based agents),
* and never longer than **0.2 s** after the first paragraph arrived.

A custom prompt template disables batching entirely (one paragraph per call),
because the user's template defines its own output contract and cannot be
wrapped in a translations-array request.

The queue owns exactly one daemon thread.  ``submit()`` blocks the caller until
its own paragraph has a result, which keeps the adapter's interface synchronous
and lets the converter's thread pool stay as it is.  An idle queue exits after a
minute and is restarted transparently by the next submission.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Sequence

#: Trigger sizes from the feature brief.
MAX_BATCH_ITEMS = 8
MAX_BATCH_CHARS = 12000
#: argv agents (ZCode) must stay below the platform command-line limit.
MAX_BATCH_CHARS_ARGV = 8000
FLUSH_DELAY = 0.2
IDLE_TIMEOUT = 60.0


class BatchQueueClosedError(RuntimeError):
    """Raised when a translation is submitted to (or pending on) a closed queue."""


@dataclass
class _Item:
    text: str
    done: bool = False
    result: str | None = None
    error: BaseException | None = None


class BatchQueue:
    """Thread-safe batching queue with a single worker thread."""

    def __init__(
        self,
        runner: Callable[[Sequence[str]], Sequence[str]],
        *,
        max_items: int = MAX_BATCH_ITEMS,
        max_chars: int = MAX_BATCH_CHARS,
        flush_delay: float = FLUSH_DELAY,
        idle_timeout: float = IDLE_TIMEOUT,
        name: str = "agent-batch",
    ) -> None:
        self._runner = runner
        self.max_items = max(1, int(max_items))
        self.max_chars = max(1, int(max_chars))
        self.flush_delay = max(0.0, float(flush_delay))
        self.idle_timeout = max(0.0, float(idle_timeout))
        self._name = name
        self._condition = threading.Condition()
        self._items: deque[_Item] = deque()
        self._closed = False
        self._thread: threading.Thread | None = None

    # --- public API -----------------------------------------------------
    def submit(self, text: str) -> str:
        """Enqueue one paragraph and block until its translation is ready."""
        item = _Item(text=str(text))
        with self._condition:
            if self._closed:
                raise BatchQueueClosedError(
                    "the agent CLI queue is closed; build a new adapter to translate again"
                )
            self._ensure_thread_locked()
            self._items.append(item)
            self._condition.notify_all()
            while not item.done:
                if self._closed:
                    raise BatchQueueClosedError(
                        "the agent CLI queue was closed while a translation was pending"
                    )
                self._condition.wait()
        if item.error is not None:
            raise item.error
        return item.result if item.result is not None else ""

    def close(self) -> None:
        """Stop the worker and wake every waiter with a clear error."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._fail_locked(self._items)
            self._items.clear()
            self._condition.notify_all()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    # --- worker ---------------------------------------------------------
    def _ensure_thread_locked(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._loop, name=self._name, daemon=True
            )
            self._thread.start()

    def _fail_locked(self, items: Sequence[_Item]) -> None:
        for item in items:
            if not item.done:
                item.error = BatchQueueClosedError(
                    "the agent CLI queue was closed before this translation finished"
                )
                item.done = True

    def _collect_locked(self) -> list[_Item]:
        """Wait for the batch window and return everything that arrived."""
        assert self._items
        deadline = time.monotonic() + self.flush_delay
        batch = [self._items.popleft()]
        chars = len(batch[0].text)
        while len(batch) < self.max_items:
            if self._items:
                candidate = self._items[0]
                if chars + len(candidate.text) > self.max_chars:
                    break
                batch.append(self._items.popleft())
                chars += len(candidate.text)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._condition.wait(remaining)
            if self._closed:
                break
        return batch

    def _loop(self) -> None:
        while True:
            with self._condition:
                while not self._items and not self._closed:
                    if not self._condition.wait(self.idle_timeout):
                        if not self._items:
                            # Idle shutdown.  A later submit() starts a fresh
                            # thread; this is not a permanent close.
                            self._thread = None
                            return
                if self._closed:
                    self._fail_locked(self._items)
                    self._items.clear()
                    self._thread = None
                    return
                batch = self._collect_locked()
                if self._closed:
                    self._fail_locked(batch)
                    self._fail_locked(self._items)
                    self._items.clear()
                    self._thread = None
                    return

            texts = [item.text for item in batch]
            try:
                results = list(self._runner(texts))
                if len(results) != len(batch):
                    raise ValueError(
                        f"the agent CLI returned {len(results)} translation(s) "
                        f"for {len(batch)} paragraph(s)"
                    )
            except BaseException as exc:  # noqa: BLE001 - delivered to each waiter
                with self._condition:
                    for item in batch:
                        if not item.done:
                            item.error = exc
                            item.done = True
                    self._condition.notify_all()
            else:
                with self._condition:
                    for item, value in zip(batch, results):
                        if not item.done:
                            item.result = value
                            item.done = True
                    self._condition.notify_all()


__all__ = [
    "BatchQueue",
    "BatchQueueClosedError",
    "FLUSH_DELAY",
    "IDLE_TIMEOUT",
    "MAX_BATCH_CHARS",
    "MAX_BATCH_CHARS_ARGV",
    "MAX_BATCH_ITEMS",
]
