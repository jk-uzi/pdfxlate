"""Persisted ``agents.list`` snapshot.

The settings panel should show installed agents and their model lists
immediately, not re-probe every CLI on every app launch.  This module stores the
last successful result in the config directory and lets ``list_agents()`` reuse
it until the user explicitly asks for a fresh detection.

Security: a snapshot contains only installation/model metadata.  It never
contains an API key, an access token, a credential file, or the decrypted
WorkBuddy payload.  ``AgentDetection.to_dict()`` already omits the command
tuple, and the model catalog only returns ids/labels/credits.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Mapping

from pdfxlate import paths

logger = logging.getLogger(__name__)

#: Bump when the payload shape changes; older files are ignored.
SNAPSHOT_VERSION = 1
SNAPSHOT_FILENAME = "agents-snapshot.json"


def snapshot_path() -> Path:
    """Where the snapshot lives (inside the app's config directory)."""
    return paths.config_dir() / SNAPSHOT_FILENAME


def _signature(configured: Mapping[str, str]) -> dict[str, str]:
    """The ``agents.paths`` overrides the snapshot was taken with."""
    return {str(key): str(value) for key, value in sorted(configured.items())}


def load(configured: Mapping[str, str]) -> list[dict[str, Any]] | None:
    """Return the cached agent list, or ``None`` when there is no usable one."""
    path = snapshot_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("version") != SNAPSHOT_VERSION:
        return None
    if document.get("paths") != _signature(configured):
        return None
    agents = document.get("agents")
    if not isinstance(agents, list):
        return None
    if not all(isinstance(entry, dict) and "id" in entry for entry in agents):
        return None
    return agents


def save(agents: list[dict[str, Any]], configured: Mapping[str, str]) -> None:
    """Atomically replace the snapshot; a failure must never break detection."""
    path = snapshot_path()
    document = {
        "version": SNAPSHOT_VERSION,
        "saved_at": int(time.time()),
        "paths": _signature(configured),
        "agents": agents,
    }
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:  # noqa: BLE001 - cache is best-effort
        logger.debug("could not write the agents snapshot: %s", exc)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def update_agent(
    agent_id: str,
    models: Mapping[str, Any],
    configured: Mapping[str, str],
) -> bool:
    """Replace one agent's model block in the snapshot (manual refresh)."""
    agents = load(configured)
    if agents is None:
        return False
    for entry in agents:
        if entry.get("id") != agent_id:
            continue
        entry["models"] = list(models.get("models") or [])
        entry["models_source"] = models.get("source", "fallback")
        entry["models_error"] = models.get("error")
        desktop = models.get("desktop")
        if isinstance(desktop, dict):
            entry["desktop"] = desktop
        else:
            entry.pop("desktop", None)
        save(agents, configured)
        return True
    return False
