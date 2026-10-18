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
``providers.test``, ``providers.models``, ``assets.status``,
``assets.download``, ``pdf.info``, ``pdf.spread``, ``translate.start``,
``translate.cancel``, ``mcp.snippet``, ``mcp.installConfig``.
``providers.list`` reports the provider → models tree (each provider's ``kind``,
``base_url``, ``has_api_key`` and per-model sampling settings, including
``enabled``); it deliberately never returns an ``api_key``.  ``providers.test``
sends one tiny translation to a provider using values supplied by the caller
(the unsaved draft in the settings panel) and reports the latency or the error,
so a wrong API format is discovered before a long job starts; it never reads or
writes the cache.  There is no stored default model — which one a job uses is
passed to ``translate.start``.  ``pdf.info`` is a read-only metadata
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
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from pdfxlate import __version__, config as config_mod, paths

logger = logging.getLogger(__name__)

# Job registry so translate.cancel can find the running job's event.
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

#: Serialises writes to stdout.
#:
#: A translation runs on its own thread while the loop keeps reading stdin, so
#: a progress event and the reply to ``translate.cancel`` can be produced at the
#: same instant.  Without this lock two threads can interleave halves of two
#: JSON documents on one line and the renderer loses both.
_EMIT_LOCK = threading.Lock()

#: Methods that must not block the request loop.
#:
#: The loop is what receives ``translate.cancel``; running a whole translation
#: inline meant the cancel request sat in the pipe until the job finished, so
#: cancelling appeared to do nothing at all.
_BACKGROUND_METHODS = frozenset({"translate.start"})


def _emit(payload: dict[str, Any]) -> None:
    """Write one JSON line to stdout and flush immediately."""
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _EMIT_LOCK:
        sys.stdout.write(line)
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
                "enabled": p.enabled,
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


#: How long a connection probe may take before the sidecar reports a timeout.
#:
#: The sidecar reads stdin in a single loop and handles one request at a time,
#: so a probe that blocks also blocks every other call (including the progress
#: of an already running job). The adapters retry transient failures eight
#: times with backoff and allow 120 s per request, which can add up to minutes
#: against an unreachable host; a user-initiated probe must not freeze the app
#: that long. The worker thread is a daemon, so giving up on it here leaks
#: nothing that outlives the process.
_PROBE_TIMEOUT_SECONDS = 25

#: Deliberately short, plain, and free of `{vN}` placeholders.
_PROBE_TEXT = "Hello."


def _draft_api_key(provider: str, api_key: Any) -> str:
    """The key to use for a probe: the draft's, or the one already saved.

    The settings panel never receives a stored key back, so a blank field means
    "unchanged", not "there is no key".
    """
    if api_key:
        return str(api_key)
    cfg = config_mod.load()
    saved = cfg.providers.get(provider)
    if saved is not None and saved.api_key:
        return str(saved.api_key)
    raise ValueError("api_key is required (none is saved for this provider)")


def method_providers_test(params: dict) -> dict:
    """Send one tiny translation to a provider and report what happened.

    Takes the *draft* values from the settings panel rather than reloading the
    config, so an edit can be tested before it is saved.  When the key is
    blank (the UI never shows a stored one) it falls back to the saved key.

    Goes through ``do_translate`` rather than ``translate``: no cache is read
    or written, and the placeholder check has nothing to check.
    """
    from pdfxlate.models.base import build_adapter, registered

    provider = str(params.get("provider") or "").strip()
    kind = str(params.get("kind") or "").strip()
    base_url = params.get("base_url")
    model = str(params.get("model") or "").strip()
    api_key = params.get("api_key")

    if not kind:
        raise ValueError("kind is required")
    if kind not in registered():
        raise ValueError(
            f"unknown kind {kind!r}; the engine has: {', '.join(registered())}"
        )
    if not base_url:
        raise ValueError("base_url is required")
    if not model:
        raise ValueError("model is required (the API id, not the alias)")

    api_key = _draft_api_key(provider, params.get("api_key"))
    profile = config_mod.ResolvedModel(
        kind=kind,
        provider=provider,
        alias=model,
        base_url=str(base_url),
        api_key=str(api_key),
        model=model,
        # Probe with the same knobs a real run would use, except that the
        # response is never cached or typeset.
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        stream=False,
    )
    adapter = build_adapter(
        kind,
        profile,
        "en",
        "zh",
        cache_enabled=False,
    )

    result: dict[str, Any] = {}

    def probe() -> None:
        started = time.monotonic()
        try:
            text = adapter.do_translate(_PROBE_TEXT)
        except BaseException as exc:  # noqa: BLE001 - reported to the UI as-is
            result["error"] = f"{type(exc).__name__}: {exc}"
            return
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["sample"] = text[:120]

    _run_bounded(probe, "provider-probe")
    if "error" in result:
        raise ValueError(result["error"])
    return {"ok": True, **result}


def _run_bounded(work, name: str) -> None:
    """Run ``work`` on a daemon thread and give up after ``_PROBE_TIMEOUT_SECONDS``.

    The sidecar handles requests on one thread, so a call that blocks on a dead
    endpoint also blocks every other call. The worker cannot be killed — an
    in-flight HTTP request is not interruptible — so this only bounds how long
    the caller waits; the thread is a daemon and dies with the process.
    """
    thread = threading.Thread(target=work, name=name, daemon=True)
    thread.start()
    thread.join(_PROBE_TIMEOUT_SECONDS)
    if thread.is_alive():
        raise TimeoutError(
            f"no answer within {_PROBE_TIMEOUT_SECONDS}s; check the base URL, "
            "the network, and that the endpoint speaks this API format"
        )


def method_providers_models(params: dict) -> dict:
    """List the model ids a provider's endpoint reports (``GET .../models``).

    Uses the settings panel's draft endpoint and format so the list can be
    fetched before anything is saved; the key falls back to the stored one, as
    for ``providers.test``.
    """
    from pdfxlate.models import catalog

    provider = str(params.get("provider") or "").strip()
    kind = str(params.get("kind") or "").strip() or "openai_compat"
    base_url = str(params.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("base_url is required")

    api_key = _draft_api_key(provider, params.get("api_key"))
    result: dict[str, Any] = {}

    def fetch() -> None:
        try:
            result["models"] = catalog.list_models(kind, base_url, api_key)
        except BaseException as exc:  # noqa: BLE001 - reported to the UI as-is
            result["error"] = str(exc)

    _run_bounded(fetch, "provider-catalog")
    if "error" in result:
        raise ValueError(result["error"])
    return {"models": result.get("models", [])}



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
    from pdfxlate import cache as cache_mod
    from pdfxlate.api import translate
    from pdfxlate.models.base import provider_cache_snapshot

    job_id = params.get("job_id") or uuid.uuid4().hex
    cancel_event = threading.Event()
    with _JOBS_LOCK:
        _JOBS[job_id] = {"cancel": cancel_event}

    # The counters are process-wide.  The desktop app allows one job at a time,
    # so clearing them here attributes every lookup to this job; a concurrent
    # caller would mix the numbers rather than crash, which is acceptable for
    # a diagnostic that is only shown to a human.
    cache_mod.snapshot_stats(reset=True)
    provider_cache_snapshot(reset=True)

    def stats() -> dict[str, dict[str, int]]:
        return {
            "cache": cache_mod.snapshot_stats(),
            "provider_cache": provider_cache_snapshot(),
        }

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
            # Which document to build. The app sends "mono" or "dual"; an older
            # caller that omits it keeps the historical pair of files.
            output_mode=params.get("output_mode") or "both",
        )
    except asyncio.CancelledError:
        # A cancel is a normal end, not a failure: the renderer asked for it and
        # must not show an error for obeying.  No `done` event either — the
        # caller that is awaiting this reply is the one that cancelled, and it
        # learns the outcome from the result.
        logger.info("translation job %s cancelled", job_id)
        return {"job_id": job_id, "cancelled": True, "outputs": [], **stats()}
    finally:
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)

    outputs = [{"mono": mono, "dual": dual} for mono, dual in results]
    measured = stats()
    _emit(
        {
            "event": "done",
            "job_id": job_id,
            "result": {"outputs": outputs, **measured},
        }
    )
    return {"job_id": job_id, "outputs": outputs, **measured}


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
    # A one-shot probe: sends a tiny translation with the values currently in
    # the settings panel, so a wrong API format or key is caught before a long
    # job starts.  Never touches the cache.
    "providers.test": method_providers_test,
    # What the endpoint itself says it offers, so the settings panel can show a
    # checklist instead of asking for model ids from memory.
    "providers.models": method_providers_models,
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

    if method in _BACKGROUND_METHODS:
        # Off the request loop so the next stdin line — in practice a
        # `translate.cancel` — can be read while the job runs.  Daemon, because
        # the process must still be able to exit with a job in flight.
        threading.Thread(
            target=_run_method,
            args=(method, req_id, params),
            name=f"sidecar-{method}",
            daemon=True,
        ).start()
        return

    _run_method(method, req_id, params)


def _run_method(method: str, req_id: Any, params: dict[str, Any]) -> None:
    """Call one method and report its outcome, whatever thread we are on."""
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

    # Load numpy on the **main** thread before the request loop starts.
    #
    # Not an optimisation.  On Windows, importing numpy's C extension for the
    # first time from a worker thread while stdin is a pipe deadlocks: the
    # thread sits inside the extension's module init forever and no reply is
    # ever written.  `translate.start` runs on a worker thread — that is what
    # makes cancellation reachable — so without this warm-up every job started
    # by the desktop app hung before its first progress event.  Measured: the
    # same import takes 0.2s here and never completes from the thread.
    import numpy  # noqa: F401  (imported for the side effect)

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
