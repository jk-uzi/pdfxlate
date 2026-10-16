"""MCP server exposing pdfxlate to an agent over stdio.

The server is a thin, *read-only* front door for a coding agent (Claude Code,
ZCode, or any MCP client): it lists the configured providers and translates a
PDF, either with a provider configured in the app or with the connected
agent's own model via MCP sampling.  Nothing here mutates the config — there is
deliberately no tool that adds a provider, edits a model or writes an API key,
so pointing an agent at this server can never change the user's setup.

Why the transport is stdio
--------------------------
Same reasoning as :mod:`pdfxlate.sidecar`: no port is bound (so no Windows
firewall prompt), there is no auth token to leak, and the process dies with its
parent.  stdout carries ONLY the JSON-RPC protocol; every log line goes to
stderr.  The MCP SDK owns the stdio transport and, on Windows, even diverts the
real fd 1 to keep stray prints off the wire — but our own discipline is to log
to stderr and never print.

Two translation routes
----------------------
``translate_pdf(..., translator="provider")`` uses a provider/model from
``config.yaml`` and runs the normal one-pass pipeline (with its translation
cache).  ``translator="client"`` instead asks the *connected agent's* model to
translate, via MCP sampling; that needs the client to advertise the ``sampling``
capability, which is checked before any work is done.

The sampling route is why :mod:`pdfxlate.pdf.two_phase` exists: the layout
walk cannot be made async, so the paragraphs are collected in one pass and
typeset in a second pass after the async sampling calls have produced the
translations.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import anyio

from pdfxlate import __version__, config as config_mod, paths
from pdfxlate.models.base import strip_reasoning
from pdfxlate.pdf import two_phase

logger = logging.getLogger(__name__)

# Imported at module scope so the module fails loudly (and early) when the
# optional ``mcp`` extra is not installed, rather than halfway through a call.
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import SamplingMessage, TextContent

# How many paragraphs to hand the sampling model in one request.  Batching
# keeps the round-trip count down on a long document; the batch is small enough
# that a single response is unlikely to be truncated.
SAMPLING_BATCH_SIZE = 20

# Sampling responses are budgeted per request.  ``max_tokens`` is required by
# ``create_message``; the value scales with the batch so a 20-paragraph batch is
# not capped at the size of a one-paragraph one.
_SAMPLING_MIN_TOKENS = 512
_SAMPLING_MAX_TOKENS = 8192

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


# --- helpers ---------------------------------------------------------------


def _load_config() -> config_mod.AppConfig:
    return config_mod.load()


def _require_file(file: str) -> str:
    path = Path(file).expanduser()
    if not path.exists():
        raise ToolError(f"input file does not exist: {file}")
    if not path.is_file():
        raise ToolError(f"input path is not a file: {file}")
    return str(path)


def _progress_callback(ctx: Context, label: str):
    """Return a sync progress callback that forwards to ``ctx.report_progress``.

    The pipeline calls the callback from the worker thread that runs the
    blocking PDF work, so the async report has to hop back to the event loop
    with ``anyio.from_thread.run``.  A progress failure (e.g. a client that
    ignored the progress token) must never abort the translation, hence the
    swallowed exception.
    """

    async def _report(stage: str, current: int, total: int, message: str) -> None:
        await ctx.report_progress(
            float(current),
            float(total) if total else None,
            f"{label} [{stage}] {message}",
        )

    def cb(stage: str, current: int, total: int, message: str) -> None:
        try:
            anyio.from_thread.run(_report, stage, current, total, message)
        except Exception:  # noqa: BLE001 - progress is best-effort
            logger.debug("progress report failed", exc_info=True)

    return cb


def _resolve(cfg: config_mod.AppConfig, provider: str | None, model: str | None):
    """Resolve a provider/model, surfacing the config's own error text.

    ``AppConfig.resolve`` already raises a precise message naming the known
    providers/models, so it is re-wrapped as a ``ToolError`` verbatim rather
    than replaced with something vaguer.
    """
    try:
        return cfg.resolve(provider, model)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


# --- sampling --------------------------------------------------------------


def _batch_prompt(texts: list[str], lang_out: str) -> str:
    return (
        f"Translate every string in the JSON array below into {lang_out}. "
        "Keep any formula placeholder such as {v0} or {v12} exactly as written, "
        "in place. Do not merge, split, add or drop items. "
        "Reply with ONLY a JSON array of the translated strings, in the same "
        "order and with the same number of items, with no commentary.\n\n"
        + json.dumps(list(texts), ensure_ascii=False)
    )


def _single_prompt(text: str, lang_out: str) -> str:
    return (
        f"Translate the following text into {lang_out}. Keep any formula "
        "placeholder such as {v0} exactly as written, in place. Reply with ONLY "
        "the translated text, with no explanation.\n\n" + text
    )


def _parse_array(raw: str, expected: int) -> list[str]:
    """Parse a JSON array of strings, tolerating a fenced code block."""
    text = raw.strip()
    if match := _FENCE_RE.search(text):
        text = match.group(1).strip()
    data = json.loads(text)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError("not a JSON array of strings")
    if len(data) != expected:
        raise ValueError(f"expected {expected} items, got {len(data)}")
    return data


async def _sample(ctx: Context, prompt: str, max_tokens: int) -> str:
    result = await ctx.session.create_message(
        messages=[
            SamplingMessage(
                role="user", content=TextContent(type="text", text=prompt)
            )
        ],
        max_tokens=max_tokens,
    )
    content = result.content
    if getattr(content, "type", None) != "text":
        raise ToolError(
            "the client's sampling model returned non-text content; "
            "cannot use it to translate"
        )
    return strip_reasoning(content.text)


def _require_sampling(ctx: Context) -> None:
    caps = ctx.client_capabilities
    if not (caps and caps.sampling):
        raise ToolError(
            "this client does not support MCP sampling, so translator='client' "
            "is unavailable; use translator='provider' (a model configured in "
            "config.yaml) instead"
        )


async def _translate_via_sampling(
    ctx: Context,
    texts: list[str],
    lang_out: str,
) -> list[str]:
    """Translate ``texts`` in batches with the client's own model.

    A batch whose response cannot be parsed (or has the wrong length) is
    retried one paragraph at a time, so one malformed batch does not sink the
    whole document.
    """
    if not texts:
        return []
    results: list[str] = [""] * len(texts)
    total = len(texts)
    done = 0
    for start in range(0, total, SAMPLING_BATCH_SIZE):
        batch = texts[start : start + SAMPLING_BATCH_SIZE]
        budget = max(
            _SAMPLING_MIN_TOKENS,
            min(_SAMPLING_MAX_TOKENS, 128 * len(batch) + 256),
        )
        try:
            raw = await _sample(ctx, _batch_prompt(batch, lang_out), budget)
            translated = _parse_array(raw, len(batch))
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - fall back to per-paragraph
            logger.warning(
                "batch sampling at %d failed (%s); retrying one by one", start, exc
            )
            translated = []
            for text in batch:
                raw = await _sample(ctx, _single_prompt(text, lang_out), budget)
                translated.append(raw)
        for offset, value in enumerate(translated):
            results[start + offset] = value
        done += len(batch)
        await ctx.report_progress(done, total, f"translated {done}/{total} paragraphs")
    return results


# --- tools -----------------------------------------------------------------


def build_server() -> MCPServer:
    """Build the MCP server with its four tools registered."""
    mcp = MCPServer(
        "pdfxlate",
        version=__version__,
        instructions=(
            "Layout-preserving PDF translation. Use list_providers to see the "
            "configured models, extract_segments + typeset_translations to "
            "translate with your own model, or translate_pdf to do it in one "
            "call (translator='provider' uses the app's configured model, "
            "translator='client' uses yours via sampling). Configuration is "
            "read-only."
        ),
    )

    @mcp.tool()
    def list_providers() -> dict[str, Any]:
        """List the configured providers and their models (never the API key).

        Read-only. Returns each provider's kind, base_url, whether an API key
        is configured (has_api_key, never the key itself) and its models with
        their alias, model id, reasoning_effort and temperature, plus the
        configured default provider/model.
        """
        cfg = _load_config()
        providers = {
            name: {
                "kind": provider.kind,
                "base_url": provider.base_url,
                "has_api_key": bool(provider.api_key),
                "models": {
                    alias: {
                        "model": entry.model,
                        "reasoning_effort": entry.reasoning_effort,
                        "temperature": entry.temperature,
                    }
                    for alias, entry in provider.models.items()
                },
            }
            for name, provider in cfg.providers.items()
        }
        return {"providers": providers}

    @mcp.tool()
    async def extract_segments(
        file: str,
        pages: Optional[list[int]] = None,
        mode: Optional[str] = None,
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Collect the translatable paragraphs of a PDF without translating them.

        Runs the layout pipeline once and records every paragraph in order,
        returning a job_id and a list of {id, text} segments. Pass the same
        job_id to typeset_translations with a translation for every id. ``pages``
        is a list of 0-based page indices; ``mode`` is 'fast' (ONNX layout) or
        'ultrafast'. The layout model is downloaded on first use.
        """
        path = _require_file(file)
        cfg = _load_config()
        progress = _progress_callback(ctx, "extract")
        try:
            collection = await anyio.to_thread.run_sync(
                functools.partial(
                    two_phase.collect, path, cfg, pages, mode, progress
                )
            )
        except two_phase.TwoPhaseError as exc:
            raise ToolError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        two_phase.store_job(collection)
        return {"job_id": collection.job_id, "segments": collection.segments}

    @mcp.tool()
    async def typeset_translations(
        job_id: str,
        translations: dict[str, str],
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Typeset a previously extracted job with the supplied translations.

        ``translations`` maps every segment id from extract_segments to its
        translated text. Writes '<name>-mono.pdf' and '<name>-dual.pdf' next to
        the input file and returns their paths. Fails if any segment id is
        missing, unknown, or if the paragraph sequence no longer matches the
        extraction.
        """
        try:
            job = two_phase.get_job(job_id)
        except KeyError as exc:
            raise ToolError(str(exc)) from exc
        cfg = _load_config()
        progress = _progress_callback(ctx, "typeset")
        try:
            mono, dual = await anyio.to_thread.run_sync(
                functools.partial(
                    two_phase.typeset, job.collection, translations, cfg, progress
                )
            )
        except two_phase.TwoPhaseError as exc:
            raise ToolError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise ToolError(str(exc)) from exc
        mono_path, dual_path = two_phase.write_outputs(job.collection.file, mono, dual)
        return {"mono": mono_path, "dual": dual_path}

    @mcp.tool()
    async def translate_pdf(
        file: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        pages: Optional[list[int]] = None,
        mode: Optional[str] = None,
        translator: str = "provider",
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Translate a PDF and write '-mono.pdf' / '-dual.pdf' next to it.

        One call does everything: extract, translate, typeset. ``translator``
        selects the model — 'provider' (the default) uses provider/model from
        config.yaml (falling back to the configured default); 'client' uses
        your own model via MCP sampling and requires the client to support
        sampling. Returns the mono and dual output paths.
        """
        path = _require_file(file)
        if translator not in ("provider", "client"):
            raise ToolError(
                f"unknown translator {translator!r}; expected 'provider' or 'client'"
            )
        cfg = _load_config()

        if translator == "provider":
            resolved = _resolve(cfg, provider, model)
            return await _translate_with_provider(
                path, cfg, resolved, pages, mode, ctx
            )
        _require_sampling(ctx)
        return await _translate_with_client(path, cfg, pages, mode, ctx)

    return mcp


async def _translate_with_provider(
    file: str,
    cfg: config_mod.AppConfig,
    resolved,
    pages: Optional[list[int]],
    mode: Optional[str],
    ctx: Context,
) -> dict[str, Any]:
    """One-pass translation with a configured provider (uses the cache)."""
    from pdfxlate import api

    progress = _progress_callback(ctx, "translate")
    output = str(Path(file).parent)
    try:
        results = await anyio.to_thread.run_sync(
            functools.partial(
                api.translate,
                [file],
                provider=resolved.provider,
                model=resolved.alias,
                output=output,
                pages=pages,
                mode=mode,
                config=cfg,
                progress=progress,
            )
        )
    except two_phase.TwoPhaseError as exc:
        raise ToolError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    mono, dual = results[0]
    return {"mono": mono, "dual": dual, "provider": resolved.provider, "model": resolved.alias}


async def _translate_with_client(
    file: str,
    cfg: config_mod.AppConfig,
    pages: Optional[list[int]],
    mode: Optional[str],
    ctx: Context,
) -> dict[str, Any]:
    """Two-pass translation with the connected agent's own model (sampling)."""
    progress = _progress_callback(ctx, "extract")
    try:
        collection = await anyio.to_thread.run_sync(
            functools.partial(two_phase.collect, file, cfg, pages, mode, progress)
        )
    except two_phase.TwoPhaseError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    await ctx.info(
        f"collected {len(collection.texts)} paragraph(s); translating with your model"
    )
    translated = await _translate_via_sampling(
        ctx, collection.texts, cfg.translation.lang_out
    )
    translations = {
        f"{two_phase.SEGMENT_ID_PREFIX}{i}": text
        for i, text in enumerate(translated)
    }

    progress2 = _progress_callback(ctx, "typeset")
    try:
        mono, dual = await anyio.to_thread.run_sync(
            functools.partial(
                two_phase.typeset, collection, translations, cfg, progress2
            )
        )
    except two_phase.TwoPhaseError as exc:
        raise ToolError(str(exc)) from exc
    mono_path, dual_path = two_phase.write_outputs(file, mono, dual)
    return {
        "mono": mono_path,
        "dual": dual_path,
        "segments": len(collection.texts),
        "translator": "client",
    }


# --- entry point -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the MCP server over stdio.

    Mirrors :func:`pdfxlate.sidecar.main`: ``--config-dir`` lets the desktop app
    point the server at its userData directory, and ``--debug`` turns on verbose
    logging.  Logs go to stderr so stdout carries only the JSON-RPC protocol.
    """
    parser = argparse.ArgumentParser(
        prog="pdfxlate-mcp",
        description="Run the pdfxlate MCP server over stdio.",
    )
    parser.add_argument(
        "--config-dir",
        help="Config/cache/assets root directory (used by the desktop app)",
    )
    parser.add_argument("--debug", "-d", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # ``paths`` reads --config-dir out of sys.argv itself, but an in-process
    # caller may have passed argv without touching sys.argv; setting the env var
    # makes the override authoritative either way.
    if args.config_dir:
        os.environ["PDFXLATE_CONFIG_DIR"] = args.config_dir

    paths.ensure_dirs()
    logger.info("pdfxlate MCP server %s starting (config dir %s)", __version__, paths.config_dir())
    build_server().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
