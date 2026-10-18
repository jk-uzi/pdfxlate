"""Public Python API.

    from pdfxlate import translate

    translate(["paper.pdf"], provider="deepseek", model="chat", output="out/")

Everything is driven by the provider/model entries in ``config.yaml``; the
adapter is built from the resolved entry so callers never touch provider
internals.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from pdfxlate import config as config_mod
from pdfxlate import paths
from pdfxlate.models.base import ModelAdapter, build_adapter
from pdfxlate.pdf import pipeline

logger = logging.getLogger(__name__)

# Progress callback: (stage, current, total, message).
ProgressFn = Optional[Callable[[str, int, int, str], None]]


def build_translator(
    provider: str | None = None,
    model: str | None = None,
    app_config: config_mod.AppConfig | None = None,
    prompt: str | None = None,
    ignore_cache: bool = False,
    profile: str | None = None,
    reasoning_effort: str | None = None,
    cancellation_event: Any = None,
) -> ModelAdapter:
    """Construct the adapter for a provider and one of its models.

    ``profile`` is the pre-provider name for what is now a provider (optionally
    naming a model alias); it is still accepted so existing callers keep
    working.

    ``ignore_cache`` skips the cache for every :meth:`ModelAdapter.translate`
    call made by this adapter.  It is unrelated to ``cache.enabled``, which
    switches the cache off entirely.

    ``reasoning_effort`` overrides the value stored on the model entry for this
    adapter only.  It is applied to a copy of the resolved entry, so the caller's
    configuration object is left untouched — a one-off job must not rewrite what
    the settings panel shows.

    ``cancellation_event`` is handed to the adapter after construction.  HTTP
    adapters ignore it; the agent CLI adapter uses it to stop a child process.
    """
    cfg = app_config or config_mod.load()
    if profile and provider is None:
        provider = profile
    resolved = cfg.resolve(provider, model)
    if reasoning_effort is not None:
        # A copy, not a mutation: `resolved` is derived from `cfg`, and editing it
        # in place would be editing (a piece of) the caller's configuration.
        resolved = resolved.model_copy(update={"reasoning_effort": reasoning_effort})
    # The adapter may resolve an agent CLI path while it is constructed.  Pass
    # this config's overrides (not whatever config happens to be on disk) so
    # ``--config`` and in-process callers see the right paths.
    from pdfxlate.agents.detect import use_configured_paths

    agent_paths = cfg.agents.paths if cfg.agents is not None else {}
    with use_configured_paths(agent_paths):
        return build_adapter(
            resolved.kind,
            resolved,
            cfg.translation.lang_in,
            cfg.translation.lang_out,
            prompt=prompt or cfg.translation.prompt,
            cache_enabled=cfg.cache.enabled,
            ignore_cache=ignore_cache,
            cancellation_event=cancellation_event,
        )


def translate(
    files: list[str],
    provider: str | None = None,
    model: str | None = None,
    output: str | None = None,
    pages: Optional[list[int]] = None,
    mode: str | None = None,
    thread: int | None = None,
    config: config_mod.AppConfig | None = None,
    app_config_path: Path | None = None,
    progress: ProgressFn = None,
    cancellation_event: Any = None,
    vfont: str = "",
    vchar: str = "",
    skip_subset_fonts: bool | None = None,
    ignore_cache: bool = False,
    profile: str | None = None,
    lang_in: str | None = None,
    lang_out: str | None = None,
    reasoning_effort: str | None = None,
    output_mode: str = "both",
) -> list[tuple[str, str]]:
    """Translate PDF files and write ``-mono``/``-dual`` outputs.

    Returns a list of ``(mono_path, dual_path)`` tuples, one per input file.
    A side the ``output_mode`` did not ask for is ``""`` and is not written;
    ``output_mode`` is ``"mono"``, ``"dual"`` or ``"both"``.
    ``ignore_cache`` forces every paragraph to be retranslated for this call,
    bypassing cache reads and writes.  ``profile`` is a deprecated alias for
    ``provider``.

    ``lang_in``, ``lang_out`` and ``reasoning_effort`` override the stored
    settings for this call only.  Note that ``None`` means "use the configured
    value" and is different from passing an empty string, which would be an
    override to nothing.

    The overrides are applied to a **deep copy** of the configuration.  The
    caller may hold the object it passed in — the settings panel does — and a
    one-off translation must not silently rewrite the user's saved defaults.
    """
    paths.ensure_dirs()
    cfg = config or config_mod.load(app_config_path)
    overrides: dict[str, str] = {}
    if lang_in is not None:
        overrides["lang_in"] = lang_in
    if lang_out is not None:
        overrides["lang_out"] = lang_out
    if overrides:
        cfg = cfg.model_copy(deep=True)
        cfg.translation = cfg.translation.model_copy(update=overrides)

    translator = build_translator(
        provider,
        model,
        app_config=cfg,
        ignore_cache=ignore_cache,
        profile=profile,
        reasoning_effort=reasoning_effort,
        cancellation_event=cancellation_event,
    )

    resolved_mode = mode or cfg.translation.mode
    ultrafast = resolved_mode == "ultrafast"
    resolved_thread = thread or cfg.translation.thread
    # An agent CLI invocation is a whole LLM round-trip per batch, so the
    # pipeline needs enough workers to keep several batches in flight.  This is
    # a floor: a user-configured larger value still wins.
    if getattr(getattr(translator, "profile", None), "kind", None) == "agent_cli":
        resolved_thread = max(resolved_thread, 8)
    # Empty means "beside the source".  Resolved per file in the pipeline, so a
    # batch over several folders does not lump every output into one place.
    resolved_output = output or ""

    try:
        model_obj = None
        if not ultrafast:
            model_obj = _load_layout_model(cfg, progress)

        return pipeline.translate(
            files,
            translator=translator,
            output=resolved_output,
            pages=pages,
            thread=resolved_thread,
            vfont=vfont or "",
            vchar=vchar or "",
            cancellation_event=cancellation_event,
            model=model_obj,
            skip_subset_fonts=(
                cfg.translation.skip_subset_fonts
                if skip_subset_fonts is None
                else skip_subset_fonts
            ),
            ultrafast=ultrafast,
            progress=progress,
            ocr_settings=cfg.ocr,
            output_mode=output_mode,
        )
    finally:
        # A cancelled or failed job must not leave the agent CLI worker (and its
        # child process) running.  A no-op for HTTP adapters, and tolerant of a
        # caller-supplied duck-typed translator that has no close() at all.
        close = getattr(translator, "close", None)
        if callable(close):
            close()


def translate_stream(
    stream: bytes,
    provider: str | None = None,
    model: str | None = None,
    pages: Optional[list[int]] = None,
    mode: str | None = None,
    thread: int | None = None,
    config: config_mod.AppConfig | None = None,
    progress: ProgressFn = None,
    cancellation_event: Any = None,
    vfont: str = "",
    vchar: str = "",
    ignore_cache: bool = False,
    profile: str | None = None,
) -> tuple[bytes, bytes]:
    """Translate an in-memory PDF; returns ``(mono_bytes, dual_bytes)``."""
    paths.ensure_dirs()
    cfg = config or config_mod.load()
    translator = build_translator(
        provider,
        model,
        app_config=cfg,
        ignore_cache=ignore_cache,
        profile=profile,
        cancellation_event=cancellation_event,
    )

    resolved_mode = mode or cfg.translation.mode
    ultrafast = resolved_mode == "ultrafast"
    resolved_thread = thread or cfg.translation.thread
    if getattr(getattr(translator, "profile", None), "kind", None) == "agent_cli":
        resolved_thread = max(resolved_thread, 8)

    try:
        model_obj = None if ultrafast else _load_layout_model(cfg, progress)

        return pipeline.translate_stream(
            stream,
            translator=translator,
            pages=pages,
            thread=resolved_thread,
            vfont=vfont,
            vchar=vchar,
            cancellation_event=cancellation_event,
            model=model_obj,
            ultrafast=ultrafast,
            progress=progress,
            ocr_settings=cfg.ocr,
        )
    finally:
        close = getattr(translator, "close", None)
        if callable(close):
            close()


def load_layout_model(
    cfg: config_mod.AppConfig | None = None, progress: ProgressFn = None
):
    """Load (and if necessary download) the DocLayout ONNX model.

    Public because a caller that supplies its own translator object (rather
    than a configured provider) still has to hand the pipeline a layout model
    in ``fast`` mode.  The model is cached in a module-level singleton, so
    calling this repeatedly is cheap.
    """
    return _load_layout_model(cfg or config_mod.load(), progress)


def _load_layout_model(cfg: config_mod.AppConfig, progress: ProgressFn):
    """Load (and if necessary download) the DocLayout ONNX model."""
    from pdfxlate.pdf.layout import ModelInstance, OnnxModel, set_backend

    set_backend(cfg.layout.backend)
    if ModelInstance.value is not None:
        return ModelInstance.value

    if cfg.layout.model_path:
        ModelInstance.value = OnnxModel(cfg.layout.model_path)
        return ModelInstance.value

    if progress:
        progress("assets", 0, 1, "preparing layout model")
    ModelInstance.value = OnnxModel.from_pretrained()
    return ModelInstance.value
