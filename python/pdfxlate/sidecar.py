"""Sidecar process for the Electron desktop app.

Speaks newline-delimited JSON over stdin/stdout.  Chosen over a local HTTP
server because it binds no port (no Windows firewall prompt), needs no auth
token, and dies with its parent process.

Protocol
--------
Request  (stdin):  ``{"id": 1, "method": "translate.start", "params": {...}}``
Response (stdout): ``{"id": 1, "ok": true, "result": {...}}``
                   ``{"id": 1, "ok": false, "error": {"message": "..."}}``
Event    (stdout): ``{"event": "progress", "job_id": "...", "stage": "...",
                     "current": 3, "total": 10, "message": "..."}``

All non-protocol output (logs, library chatter) goes to **stderr** so stdout
carries only JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from pdfxlate import __version__, config as config_mod, paths

logger = logging.getLogger(__name__)

# Job registry so translate.cancel can find the running job's event.
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _emit(payload: dict[str, Any]) -> None:
    """Write one JSON line to stdout and flush immediately."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _respond(req_id: Any, result: Any) -> None:
    _emit({"id": req_id, "ok": True, "result": result})


def _fail(req_id: Any, exc: BaseException) -> None:
    _emit(
        {
            "id": req_id,
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc() if _debug() else None,
            },
        }
    )


_DEBUG = False


def _debug() -> bool:
    return _DEBUG


# --- methods ---------------------------------------------------------------


def method_ping(_params: dict) -> dict:
    return {"version": __version__, "config_dir": str(paths.config_dir())}


def method_config_get(_params: dict) -> dict:
    cfg = config_mod.load()
    return cfg.model_dump()


def method_config_save(params: dict) -> dict:
    """Persist a full config document sent from the UI."""
    cfg = config_mod.AppConfig.model_validate(params.get("config") or {})
    path = config_mod.save(cfg)
    return {"path": str(path)}


def method_profiles_list(_params: dict) -> dict:
    cfg = config_mod.load()
    return {
        "default": cfg.default_profile,
        "profiles": {
            name: {
                "provider": p.provider,
                "model": p.model,
                "base_url": p.base_url,
                "reasoning_effort": p.reasoning_effort,
                # Never send the key back to the renderer; report only whether
                # one is present.
                "has_api_key": bool(p.api_key),
            }
            for name, p in cfg.profiles.items()
        },
    }


def method_assets_status(_params: dict) -> dict:
    from pdfxlate import assets

    return assets.status()


def method_assets_download(params: dict) -> dict:
    """Download the model and/or fonts, reporting progress as events."""
    from pdfxlate import assets

    req_id = params.get("_id")
    targets = params.get("targets") or ["layout_model", "fonts"]
    languages = params.get("languages") or ["zh"]

    def progress_for(name: str):
        def cb(done: int, total: int) -> None:
            _emit(
                {
                    "event": "progress",
                    "job_id": params.get("job_id"),
                    "stage": "assets",
                    "current": done,
                    "total": total,
                    "message": name,
                }
            )

        return cb

    done: list[str] = []
    if "layout_model" in targets:
        assets.ensure_layout_model(on_progress=progress_for("layout_model"))
        done.append("layout_model")
    if "fonts" in targets:
        for lang in languages:
            assets.ensure_font(lang, on_progress=progress_for(f"font:{lang}"))
            done.append(f"font:{lang}")
    return {"downloaded": done}


def method_translate_start(params: dict) -> dict:
    """Run a translation job, streaming progress events."""
    from pdfxlate.api import translate

    job_id = params.get("job_id") or uuid.uuid4().hex
    cancel_event = threading.Event()
    with _JOBS_LOCK:
        _JOBS[job_id] = {"cancel": cancel_event}

    def progress(stage: str, current: int, total: int, message: str) -> None:
        _emit(
            {
                "event": "progress",
                "job_id": job_id,
                "stage": stage,
                "current": current,
                "total": total,
                "message": message,
            }
        )

    try:
        results = translate(
            params["files"],
            profile=params.get("profile"),
            output=params.get("output"),
            pages=params.get("pages"),
            mode=params.get("mode"),
            thread=params.get("thread"),
            progress=progress,
            cancellation_event=_as_asyncio_event(cancel_event),
            vfont=params.get("vfont") or "",
            vchar=params.get("vchar") or "",
        )
    finally:
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)

    outputs = [{"mono": mono, "dual": dual} for mono, dual in results]
    _emit({"event": "done", "job_id": job_id, "result": {"outputs": outputs}})
    return {"job_id": job_id, "outputs": outputs}


def _as_asyncio_event(threading_event: threading.Event) -> asyncio.Event:
    """Bridge a threading.Event to the asyncio.Event the pipeline expects.

    The pipeline only ever calls ``.is_set()``, so a small shim suffices.
    """

    class _EventShim:
        def is_set(self) -> bool:
            return threading_event.is_set()

    return _EventShim()  # type: ignore[return-value]


def method_translate_cancel(params: dict) -> dict:
    job_id = params.get("job_id")
    with _JOBS_LOCK:
        job = _JOBS.get(job_id) if job_id else next(iter(_JOBS.values()), None)
    if not job:
        return {"cancelled": False, "reason": "no such job"}
    job["cancel"].set()
    return {"cancelled": True, "job_id": job_id}


METHODS = {
    "ping": method_ping,
    "config.get": method_config_get,
    "config.save": method_config_save,
    "profiles.list": method_profiles_list,
    "assets.status": method_assets_status,
    "assets.download": method_assets_download,
    "translate.start": method_translate_start,
    "translate.cancel": method_translate_cancel,
}


def handle(request: dict[str, Any]) -> None:
    req_id = request.get("id")
    method = request.get("method")
    params = dict(request.get("params") or {})
    params["_id"] = req_id

    if method not in METHODS:
        _fail(req_id, ValueError(f"unknown method {method!r}"))
        return
    try:
        _respond(req_id, METHODS[method](params))
    except Exception as exc:  # noqa: BLE001 - report any failure to the UI
        logger.exception("method %s failed", method)
        _fail(req_id, exc)


def main(argv: list[str] | None = None) -> int:
    global _DEBUG

    parser = argparse.ArgumentParser(prog="pdfxlate-sidecar", description=__doc__)
    parser.add_argument("--config-dir", help="Config/cache/assets root directory")
    parser.add_argument("--debug", "-d", action="store_true")
    args = parser.parse_args(argv)
    _DEBUG = args.debug

    # Logs must not pollute stdout, which carries the JSON protocol.
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )

    paths.ensure_dirs()
    _emit({"event": "ready", "version": __version__})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _fail(None, ValueError(f"invalid JSON: {exc}"))
            continue
        handle(request)
    return 0


if __name__ == "__main__":
    sys.exit(main())
