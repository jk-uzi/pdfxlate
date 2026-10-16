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
) -> ModelAdapter:
    """Construct the adapter for a provider and one of its models.

    ``profile`` is the pre-provider name for what is now a provider (optionally
    naming a model alias); it is still accepted so existing callers keep
    working.

    ``ignore_cache`` skips the cache for every :meth:`ModelAdapter.translate`
    call made by this adapter.  It is unrelated to ``cache.enabled``, which
    switches the cache off entirely.
    """
    cfg = app_config or config_mod.load()
    if profile and provider is None:
        provider = profile
    resolved = cfg.resolve(provider, model)
    return build_adapter(
        resolved.kind,
        resolved,
        cfg.translation.lang_in,
        cfg.translation.lang_out,
        prompt=prompt or cfg.translation.prompt,
        cache_enabled=cfg.cache.enabled,
        ignore_cache=ignore_cache,
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
) -> list[tuple[str, str]]:
    """Translate PDF files and write ``-mono``/``-dual`` outputs.

    Returns a list of ``(mono_path, dual_path)`` tuples, one per input file.
    ``ignore_cache`` forces every paragraph to be retranslated for this call,
    bypassing cache reads and writes.  ``profile`` is a deprecated alias for
    ``provider``.
    """
    paths.ensure_dirs()
    cfg = config or config_mod.load(app_config_path)
    translator = build_translator(
        provider, model, app_config=cfg, ignore_cache=ignore_cache, profile=profile
    )

    resolved_mode = mode or cfg.translation.mode
    ultrafast = resolved_mode == "ultrafast"
    resolved_thread = thread or cfg.translation.thread
    resolved_output = output or str(Path.cwd())

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
    )


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
        provider, model, app_config=cfg, ignore_cache=ignore_cache, profile=profile
    )

    resolved_mode = mode or cfg.translation.mode
    ultrafast = resolved_mode == "ultrafast"
    model_obj = None if ultrafast else _load_layout_model(cfg, progress)

    return pipeline.translate_stream(
        stream,
        translator=translator,
        pages=pages,
        thread=thread or cfg.translation.thread,
        vfont=vfont,
        vchar=vchar,
        cancellation_event=cancellation_event,
        model=model_obj,
        ultrafast=ultrafast,
        progress=progress,
        ocr_settings=cfg.ocr,
    )


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
