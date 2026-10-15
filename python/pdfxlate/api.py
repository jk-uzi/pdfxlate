"""Public Python API.

    from pdfxlate import translate

    translate(["paper.pdf"], profile="deepseek", output="out/")

Everything is driven by a named profile from ``config.yaml``; the adapter is
built from that profile so callers never touch provider internals.
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
    profile_name: str | None = None,
    app_config: config_mod.AppConfig | None = None,
    prompt: str | None = None,
    ignore_cache: bool = False,
) -> ModelAdapter:
    """Construct the adapter for a named profile.

    ``ignore_cache`` skips the cache for every :meth:`ModelAdapter.translate`
    call made by this adapter.  It is unrelated to ``cache.enabled``, which
    switches the cache off entirely.
    """
    cfg = app_config or config_mod.load()
    profile = cfg.resolve_profile(profile_name)
    return build_adapter(
        profile.provider,
        profile,
        cfg.translation.lang_in,
        cfg.translation.lang_out,
        prompt=prompt or cfg.translation.prompt,
        cache_enabled=cfg.cache.enabled,
        ignore_cache=ignore_cache,
    )


def translate(
    files: list[str],
    profile: str | None = None,
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
) -> list[tuple[str, str]]:
    """Translate PDF files and write ``-mono``/``-dual`` outputs.

    Returns a list of ``(mono_path, dual_path)`` tuples, one per input file.
    ``ignore_cache`` forces every paragraph to be retranslated for this call,
    bypassing cache reads and writes.
    """
    paths.ensure_dirs()
    cfg = config or config_mod.load(app_config_path)
    translator = build_translator(profile, cfg, ignore_cache=ignore_cache)

    resolved_mode = mode or cfg.translation.mode
    ultrafast = resolved_mode == "ultrafast"
    resolved_thread = thread or cfg.translation.thread
    resolved_output = output or str(Path.cwd())

    model = None
    if not ultrafast:
        model = _load_layout_model(cfg, progress)

    return pipeline.translate(
        files,
        translator=translator,
        output=resolved_output,
        pages=pages,
        thread=resolved_thread,
        vfont=vfont or "",
        vchar=vchar or "",
        cancellation_event=cancellation_event,
        model=model,
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
    profile: str | None = None,
    pages: Optional[list[int]] = None,
    mode: str | None = None,
    thread: int | None = None,
    config: config_mod.AppConfig | None = None,
    progress: ProgressFn = None,
    cancellation_event: Any = None,
    vfont: str = "",
    vchar: str = "",
    ignore_cache: bool = False,
) -> tuple[bytes, bytes]:
    """Translate an in-memory PDF; returns ``(mono_bytes, dual_bytes)``."""
    paths.ensure_dirs()
    cfg = config or config_mod.load()
    translator = build_translator(profile, cfg, ignore_cache=ignore_cache)

    resolved_mode = mode or cfg.translation.mode
    ultrafast = resolved_mode == "ultrafast"
    model = None if ultrafast else _load_layout_model(cfg, progress)

    return pipeline.translate_stream(
        stream,
        translator=translator,
        pages=pages,
        thread=thread or cfg.translation.thread,
        vfont=vfont,
        vchar=vchar,
        cancellation_event=cancellation_event,
        model=model,
        ultrafast=ultrafast,
        progress=progress,
        ocr_settings=cfg.ocr,
    )


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
