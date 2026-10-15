"""Translation cache backed by stdlib sqlite3.

Replaces the upstream peewee-based cache.  Two deliberate changes:

* **No import-time side effects.**  The upstream module opened the database at
  import, which made it awkward to relocate and surprising in tests.  Here the
  connection is created explicitly and can be pointed anywhere.
* **Thread-local connections.**  sqlite3 connections are not shareable across
  threads, and the typesetter translates paragraphs from a thread pool.

The cache key is ``(engine, params_json, original_text)``.  Every knob that
changes model output (temperature, reasoning effort, extra body, prompt) is
folded into ``params`` so switching a setting never serves a stale translation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from pdfxlate import paths

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS translation_cache (
    engine       TEXT NOT NULL,
    params       TEXT NOT NULL,
    original     TEXT NOT NULL,
    translation  TEXT NOT NULL,
    PRIMARY KEY (engine, params, original)
);
"""


def _sort_recursively(value: Any) -> Any:
    """Order dict keys so an equal mapping always serialises identically."""
    if isinstance(value, dict):
        return {k: _sort_recursively(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list):
        return [_sort_recursively(v) for v in value]
    return value


class TranslationCache:
    """Per-engine translation cache.

    Engine names are kept short because they form part of the primary key and
    are compared verbatim across runs.
    """

    def __init__(
        self,
        engine: str,
        params: dict[str, Any] | None = None,
        path: Path | None = None,
        enabled: bool = True,
    ) -> None:
        if len(engine) >= 32:
            raise ValueError("engine name must be shorter than 32 characters")
        self.engine = engine
        self.enabled = enabled
        self.path = path or paths.cache_file()
        self._local = threading.local()
        self._params: dict[str, Any] = {}
        self._params_json = ""
        self.replace_params(params or {})
        if self.enabled:
            self._init_db()

    # --- params ---------------------------------------------------------
    def replace_params(self, params: dict[str, Any]) -> None:
        self._params = _sort_recursively(params)
        self._params_json = json.dumps(self._params, sort_keys=True, default=str)

    def add_params(self, key: str, value: Any) -> None:
        self._params[key] = _sort_recursively(value)
        self.replace_params(self._params)

    # --- connection -----------------------------------------------------
    def _init_db(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.executescript(_SCHEMA)
        conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
        return conn

    # --- public API -----------------------------------------------------
    def get(self, original: str) -> str | None:
        if not self.enabled:
            return None
        try:
            row = self._connect().execute(
                "SELECT translation FROM translation_cache "
                "WHERE engine=? AND params=? AND original=?",
                (self.engine, self._params_json, original),
            ).fetchone()
        except sqlite3.Error:
            logger.debug("cache read failed", exc_info=True)
            return None
        return row[0] if row else None

    def set(self, original: str, translation: str) -> None:
        if not self.enabled:
            return
        try:
            conn = self._connect()
            conn.execute(
                "INSERT INTO translation_cache (engine, params, original, translation) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (engine, params, original) DO UPDATE SET translation=excluded.translation",
                (self.engine, self._params_json, original, translation),
            )
            conn.commit()
        except sqlite3.Error:
            logger.debug("cache write failed", exc_info=True)

    def clear(self) -> int:
        """Delete every entry for this engine; returns the row count removed."""
        if not self.enabled:
            return 0
        conn = self._connect()
        cur = conn.execute("DELETE FROM translation_cache WHERE engine=?", (self.engine,))
        conn.commit()
        return cur.rowcount

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
