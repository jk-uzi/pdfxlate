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

Methods: ``ping``, ``config.get``, ``config.save``, ``providers.list``,
``assets.status``, ``assets.download``, ``pdf.info``, ``pdf.spread``,
``translate.start``, ``translate.cancel``, ``mcp.snippet``,
``mcp.installConfig``.
``providers.list`` reports the provider → models tree (each provider's ``kind``,
``base_url``, ``has_api_key`` and per-model sampling settings, including
``enabled``); it deliberately never returns an ``api_key``.  There is no stored
default model — which one a job uses is passed to ``translate.start``.  ``pdf.info`` is a read-only metadata
probe (page count) used by the preview toolbar; it never renders or modifies the
document.  ``pdf.spread`` writes a *derived* copy with two pages side by side
and returns its path, so the viewer can show facing pages in one scrollbar;
the source is never modified.  ``mcp.snippet`` / ``mcp.installConfig`` help
point an AI agent at this
install: the first returns config text to paste, the second adds our entry to one
agent's own config file (backing it up first, and touching nothing else in it).

Run with ``--mcp`` to serve the same install as an MCP server instead of as the
JSON sidecar.  That flag is how a *packaged* install is reachable by an agent:
an onedir bundle has no `python -m` to offer, so the binary itself has to be the
entry point.

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


def method_providers_list(_params: dict) -> dict:
    cfg = config_mod.load()
    return {
        "providers": {
            name: {
                "kind": p.kind,
                "base_url": p.base_url,
                # Never send the key back to the renderer; report only whether
                # one is present.
                "has_api_key": bool(p.api_key),
                "models": {
                    alias: {
                        "model": entry.model,
                        "reasoning_effort": entry.reasoning_effort,
                        "temperature": entry.temperature,
                        "max_tokens": entry.max_tokens,
                        "top_p": entry.top_p,
                        "stream": entry.stream,
                        "context_length": entry.context_length,
                        "enabled": entry.enabled,
                    }
                    for alias, entry in p.models.items()
                },
            }
            for name, p in cfg.providers.items()
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


def method_pdf_info(params: dict) -> dict:
    """Report read-only metadata (page count) for one PDF.

    Used by the preview toolbar's "page N / M" label, so it must stay fast and
    side-effect free: the document is opened, its page count read, and closed
    without rendering or modifying anything.
    """
    from pymupdf import Document, FileDataError

    if "path" not in params:
        raise ValueError("missing required parameter: path")
    raw = params["path"]
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        raise ValueError(f"path must be a non-empty string, got {raw!r}")

    path = Path(raw).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"input file does not exist: {raw}")
    if not path.is_file():
        raise ValueError(f"input path is not a file: {raw}")

    try:
        with Document(path) as doc:
            if not doc.is_pdf:
                raise ValueError(f"input file is not a PDF: {raw}")
            pages = doc.page_count
    except FileDataError as exc:
        # PyMuPDF raises this for truncated/corrupt/non-PDF data; reword it so
        # the UI shows something actionable instead of a library traceback.
        raise ValueError(f"could not read PDF file: {raw} ({exc})") from exc

    return {"pages": pages, "path": str(path.resolve())}


def method_pdf_spread(params: dict) -> dict:
    """Write a facing-pages copy of one PDF and return where it landed.

    The renderer cannot lay out PDF pages itself (the viewer is an
    out-of-process plugin with its own scrollbar, so two frames cannot be kept
    in step).  Combining the pages here gives the viewer a single document with
    one scrollbar, which is what a facing-page view needs.

    The result is cached on disk and keyed by the source's path, size and mtime,
    so repeated requests for the same file cost nothing.  The source is only
    read.
    """
    from pdfxlate.pdf.spread import build_spread

    if "path" not in params:
        raise ValueError("missing required parameter: path")
    raw = params["path"]
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        raise ValueError(f"path must be a non-empty string, got {raw!r}")

    spread_path, source_pages, spread_pages = build_spread(raw)
    return {
        "path": str(spread_path),
        "pages": source_pages,
        "spreads": spread_pages,
    }


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
            provider=params.get("provider"),
            model=params.get("model"),
            output=params.get("output"),
            pages=params.get("pages"),
            mode=params.get("mode"),
            thread=params.get("thread"),
            progress=progress,
            cancellation_event=_as_asyncio_event(cancel_event),
            vfont=params.get("vfont") or "",
            vchar=params.get("vchar") or "",
            ignore_cache=bool(params.get("ignore_cache")),
            # None means "not specified", so api.translate falls back to the
            # config value; a bool would force it either way.
            skip_subset_fonts=params.get("skip_subset_fonts"),
            # Same three-state rule for the per-job overrides. Only the settings
            # panel writes the stored values, so an absent parameter here must
            # leave them alone rather than reset anything.
            lang_in=params.get("lang_in"),
            lang_out=params.get("lang_out"),
            reasoning_effort=params.get("reasoning_effort"),
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


def method_mcp_snippet(params: dict) -> dict:
    """The MCP config to paste into any agent.

    Built here rather than in the renderer so the escaping of the command and
    its arguments comes from ``json.dumps`` rather than string concatenation —
    Windows paths are full of backslashes that a template would mangle.
    """
    from pdfxlate import mcp_config

    return {"snippet": mcp_config.snippet(params["command"], params.get("args") or [])}


def method_mcp_install_config(params: dict) -> dict:
    """Add our entry to one agent's config file.

    The write itself lives in :mod:`pdfxlate.mcp_config` so it can be tested
    against real files, which is the part of this that must not go wrong: these
    are other programs' settings, and some hold API keys.
    """
    from pdfxlate import mcp_config

    try:
        return mcp_config.install(
            params["path"],
            params["target"],
            params["command"],
            params.get("args") or [],
        )
    except mcp_config.McpConfigError as exc:
        # A refusal is an expected outcome, not a crash; the message is written
        # to be shown to the user as-is.
        raise ValueError(str(exc)) from exc


METHODS = {
    "ping": method_ping,
    "config.get": method_config_get,
    "config.save": method_config_save,
    # Renamed from profiles.list when the schema gained providers.  The old
    # name is *not* kept as an alias: its result shape changed too, so an old
    # caller would break either way and a silent alias would only hide that.
    "providers.list": method_providers_list,
    "assets.status": method_assets_status,
    "assets.download": method_assets_download,
    "pdf.info": method_pdf_info,
    "pdf.spread": method_pdf_spread,
    "translate.start": method_translate_start,
    "translate.cancel": method_translate_cancel,
    # Helping the user point an agent at this install.
    "mcp.snippet": method_mcp_snippet,
    "mcp.installConfig": method_mcp_install_config,
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

    argv = list(sys.argv[1:] if argv is None else argv)

    # ``--mcp`` turns this same executable into the MCP server instead of the
    # JSON-protocol sidecar. It exists because a PyInstaller onedir bundle
    # cannot be asked to run a different module: the executable *is* the
    # interpreter with an entry point baked in, so there is no `python -m`
    # available inside a packaged install. An agent pointed at the installed
    # app therefore launches this binary with this flag.
    #
    # Handled before argparse so the remaining arguments pass through
    # untouched, and imported lazily so that launching the sidecar does not
    # require the optional ``mcp`` dependency to be installed.
    if "--mcp" in argv:
        argv.remove("--mcp")
        from pdfxlate.mcp_server import main as mcp_main

        return mcp_main(argv)

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
