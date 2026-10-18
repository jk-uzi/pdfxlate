"""External agent CLIs as translation backends.

The package is split by concern:

* :mod:`pdfxlate.agents.defs` — static descriptions of the five supported CLIs;
* :mod:`pdfxlate.agents.detect` — where the executable is, its version and login
  state;
* :mod:`pdfxlate.agents.catalog` — the model ids each CLI offers;
* :mod:`pdfxlate.agents.runtime` — spawn one CLI, parse its answer, kill its
  process tree on timeout/cancel;
* :mod:`pdfxlate.agents.batch` — group paragraphs into as few invocations as
  possible.

The public surface below is what the sidecar and the model adapter import.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Mapping

from pdfxlate.agents import catalog, detect
from pdfxlate.agents.batch import BatchQueue, BatchQueueClosedError
from pdfxlate.agents.defs import AGENT_DEFS, AGENT_DEFS_BY_ID, AgentDef, get_agent_def
from pdfxlate.agents.runtime import (
    AgentAuthError,
    AgentError,
    AgentNotInstalledError,
    AgentOutputError,
    AgentProcessError,
    AgentTimeoutError,
    run_batch,
)

#: Total wall-clock budget for ``agents.list`` (the feature brief asks for ≤ 8s).
LIST_BUDGET = detect.DETECT_BUDGET


def list_agents(
    refresh: bool = False,
    paths: Mapping[str, str] | None = None,
    budget: float = LIST_BUDGET,
) -> list[dict[str, Any]]:
    """Return the full ``agents.list`` payload (detection + model lists).

    Detection and model enumeration run concurrently, and the whole thing is
    bounded by ``budget`` so the settings panel never waits on a wedged CLI.
    Anything unfinished falls back to a cached value or to the built-in model
    list with a readable error.
    """
    configured = detect.configured_paths() if paths is None else paths
    deadline = time.monotonic() + budget
    pool = ThreadPoolExecutor(
        max_workers=len(AGENT_DEFS) * 2, thread_name_prefix="agent-list"
    )
    detection_futures = {
        defn.id: pool.submit(detect.detect_agent, defn, configured, refresh)
        for defn in AGENT_DEFS
    }
    model_futures = {
        defn.id: pool.submit(catalog.list_models, defn.id, refresh, configured)
        for defn in AGENT_DEFS
    }

    wait(
        [*detection_futures.values(), *model_futures.values()],
        timeout=max(0.0, deadline - time.monotonic()),
    )

    agents: list[dict[str, Any]] = []
    for defn in AGENT_DEFS:
        detection_future = detection_futures[defn.id]
        if detection_future.done() and not detection_future.cancelled():
            try:
                detection = detection_future.result()
            except Exception:  # noqa: BLE001 - one agent must not sink the list
                detection = detect.blank_detection(defn)
        else:
            detection_future.cancel()
            detection = detect.stale_detection(
                defn, configured
            ) or detect.provisional_detection(defn, configured)

        model_future = model_futures[defn.id]
        if model_future.done() and not model_future.cancelled():
            try:
                models = model_future.result()
            except Exception as exc:  # noqa: BLE001
                models = catalog.fallback_models(defn.id, str(exc))
        else:
            model_future.cancel()
            models = catalog.fallback_models(
                defn.id, "the model list did not finish in time"
            )

        entry = detection.to_dict()
        entry["models"] = models.get("models", [])
        entry["models_source"] = models.get("source", "fallback")
        entry["models_error"] = models.get("error")
        # WorkBuddy's desktop status is optional and only present when the
        # catalog looked at that backend; the renderer treats absence as "not
        # applicable" so the CLI-only agents keep their old shape.
        desktop = models.get("desktop")
        if isinstance(desktop, dict):
            entry["desktop"] = desktop
        agents.append(entry)

    pool.shutdown(wait=False, cancel_futures=True)
    return agents


__all__ = [
    "AGENT_DEFS",
    "AGENT_DEFS_BY_ID",
    "AgentAuthError",
    "AgentDef",
    "AgentError",
    "AgentNotInstalledError",
    "AgentOutputError",
    "AgentProcessError",
    "AgentTimeoutError",
    "BatchQueue",
    "BatchQueueClosedError",
    "get_agent_def",
    "list_agents",
    "run_batch",
]
