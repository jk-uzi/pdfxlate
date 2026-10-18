"""Two-pass translation over the deterministic PDF pipeline.

``pdf/converter.py`` translates one paragraph at a time inside the layout
walk and cannot be made asynchronous, yet an MCP client wants to see the
paragraphs before they are translated (to translate them with its own model,
to inspect them, or to hand them to a human).  The pipeline is *deterministic*:
the same PDF, the same ``pages``/``mode`` and the same layout model produce the
same paragraph sequence, so translation can be split into two passes over the
same pipeline:

1. **Collection** — run the pipeline with a :class:`CollectingTranslator` that
   records every paragraph and returns it unchanged.  Returning the source
   verbatim keeps any ``{vN}`` formula placeholder the base class validates, so
   the collection pass never fails on the placeholder guard.  The output PDF is
   discarded; only the ordered paragraph list is kept.
2. **Typesetting** — run the pipeline again with a :class:`DictTranslator` that
   looks each paragraph up in a dict built from the collected ids.  A miss is a
   hard error, never a silent pass-through: passing text through would emit a
   silently mistranslated PDF, which is the one failure mode worth aborting on.

Because the two passes are separate pipeline runs, a :func:`sequence_fingerprint`
of the collected paragraph order is stored with the collection and recomputed in
pass 2; if they differ the run aborts rather than typesetting a mis-aligned PDF.

Errors subclass :class:`TwoPhaseError`, which derives from ``BaseException``
rather than ``Exception`` on purpose.  The converter wraps its per-paragraph
worker in ``@retry(wait=wait_fixed(1))`` with tenacity's default predicate
(``retry_if_exception_type(Exception)``, ``stop_never``), so a plain
``Exception`` raised from ``do_translate`` is retried *forever*.  A
``BaseException`` that is not an ``Exception`` slips past that predicate and
propagates immediately, which is what a lookup miss or a determinism failure
must do.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from pdfxlate import config as config_mod
from pdfxlate import paths
from pdfxlate.models.base import ModelAdapter, _CACHE_ENABLED, placeholders
from pdfxlate.pdf import pipeline

logger = logging.getLogger(__name__)

# Progress callback, same shape as the rest of the pipeline.
ProgressFn = Optional[Callable[[str, int, int, str], None]]

# Both passes run the pipeline with a single translation worker.  The pipeline
# hands the paragraph list to a thread pool whose *completion* order is fixed
# but whose *call* order is not; recording paragraphs from concurrent threads
# would therefore make the segment ids (and the determinism fingerprint)
# nondeterministic.  Two-phase translation moves the real work out of the
# pipeline anyway — pass 1 only records text and pass 2 only does dict lookups
# — so a single worker costs nothing here while making the recorded sequence
# reproducible across the two passes and across runs.
TWO_PHASE_THREAD = 1

# Default segment id prefix, e.g. ``s0``, ``s1``.
SEGMENT_ID_PREFIX = "s"


# --- errors ----------------------------------------------------------------


class TwoPhaseError(BaseException):
    """Base for every two-phase failure.

    Derives from ``BaseException`` so the converter's tenacity retry (whose
    default predicate only matches ``Exception``) does not retry it forever.
    Callers must catch this explicitly; ``except Exception`` will not see it.
    """


class MissingTranslationError(TwoPhaseError):
    """Pass 2 was asked to typeset a paragraph no translation was supplied for."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        preview = ", ".join(repr(m[:60]) for m in missing[:5])
        more = "" if len(missing) <= 5 else f" (+{len(missing) - 5} more)"
        super().__init__(
            f"no translation for {len(missing)} paragraph(s): {preview}{more}"
        )


class DeterminismError(TwoPhaseError):
    """The paragraph sequence changed between the collection and typeset passes."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            "the PDF produced a different paragraph sequence in pass 2 than in "
            "pass 1 (fingerprint "
            f"{expected[:12]} != {actual[:12]}); the input file, pages, mode or "
            "layout model changed between the two passes, so the translations "
            "no longer line up. Re-run extract_segments and translate again."
        )


class TranslationPlaceholderError(TwoPhaseError):
    """A supplied translation dropped a ``{vN}`` formula placeholder."""

    def __init__(self, missing: set[str], source: str) -> None:
        self.missing = missing
        self.source = source
        super().__init__(
            f"translation dropped formula placeholder(s) {sorted(missing)} for "
            f"paragraph {source[:60]!r}; a missing placeholder corrupts the "
            "typeset formula."
        )


class SegmentMappingError(TwoPhaseError):
    """The supplied segment ids do not match the collected segments."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# --- fingerprint -----------------------------------------------------------


def sequence_fingerprint(texts: list[str]) -> str:
    """Hash an ordered paragraph sequence, unambiguously.

    Each paragraph is length-prefixed before hashing so that ``["ab", "c"]``
    and ``["a", "bc"]`` cannot collide, and the count is included so an empty
    and a missing sequence differ.
    """
    digest = hashlib.sha256()
    digest.update(len(texts).to_bytes(8, "big"))
    for text in texts:
        encoded = text.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


# --- translators -----------------------------------------------------------


def _make_adapter(cls: type[ModelAdapter], *args: Any, **kwargs: Any) -> ModelAdapter:
    """Instantiate a two-phase adapter without touching the translation cache.

    These adapters are constructed directly rather than through
    ``build_adapter``; ``ModelAdapter.__init__`` opens (and would create) the
    cache database unless the ``_CACHE_ENABLED`` contextvar is off for the
    constructor call, which is exactly what ``build_adapter`` does.
    """
    token = _CACHE_ENABLED.set(False)
    try:
        return cls(*args, **kwargs)
    finally:
        _CACHE_ENABLED.reset(token)


class CollectingTranslator(ModelAdapter):
    """Records every paragraph and returns it unchanged.

    Returning the source keeps ``{vN}`` placeholders intact, so the base
    class's placeholder guard always passes; the cache is disabled so a
    previous real translation can never leak into the collection.
    """

    name = "twophase_collect"

    def __init__(self, lang_in: str, lang_out: str, prompt: str | None = None) -> None:
        super().__init__(None, lang_in, lang_out, prompt=prompt)
        # ``translate_patch`` resets ``no_cache`` on the translator for the
        # non-ultrafast path, so ``ignore_cache`` (which it never touches) is
        # the reliable switch that keeps the cache out of collection.
        self.ignore_cache = True
        self.no_cache = True
        self._texts: list[str] = []
        self._lock = threading.Lock()

    def do_translate(self, text: str) -> str:
        with self._lock:
            self._texts.append(text)
        return text

    @property
    def texts(self) -> list[str]:
        with self._lock:
            return list(self._texts)


class DictTranslator(ModelAdapter):
    """Looks paragraphs up in a precomputed ``text -> translation`` dict.

    A miss raises :class:`MissingTranslationError` (a ``BaseException``) so the
    converter cannot retry it and no text is ever passed through untranslated.
    Every paragraph it is asked for is recorded so the caller can verify the
    sequence matches the collection pass.
    """

    name = "twophase_dict"

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        translations: dict[str, str],
        prompt: str | None = None,
    ) -> None:
        super().__init__(None, lang_in, lang_out, prompt=prompt)
        self.ignore_cache = True
        self.no_cache = True
        self.translations = translations
        self._seen: list[str] = []
        self._missing: list[str] = []
        self._lock = threading.Lock()

    def do_translate(self, text: str) -> str:
        with self._lock:
            self._seen.append(text)
        try:
            return self.translations[text]
        except KeyError:
            with self._lock:
                if text not in self._missing:
                    self._missing.append(text)
            raise MissingTranslationError([text]) from None

    def _check_placeholders(self, source: str, response: str) -> None:
        # Override so a placeholder loss surfaces as a TwoPhaseError (a
        # BaseException) instead of PlaceholderLossError, which the converter's
        # retry would retry forever.
        expected = placeholders(source)
        if not expected:
            return
        missing = expected - placeholders(response)
        if missing:
            raise TranslationPlaceholderError(missing, source)

    @property
    def seen(self) -> list[str]:
        with self._lock:
            return list(self._seen)

    def verify(self, expected_fingerprint: str) -> None:
        """Raise :class:`DeterminismError` if the pass-2 sequence differs."""
        actual = sequence_fingerprint(self.seen)
        if actual != expected_fingerprint:
            raise DeterminismError(expected_fingerprint, actual)


# --- pipeline plumbing -----------------------------------------------------


@dataclass
class Collection:
    """The result of a collection pass: the ordered paragraphs plus inputs."""

    file: str
    pages: Optional[list[int]]
    mode: str
    texts: list[str]
    fingerprint: str
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def segments(self) -> list[dict[str, str]]:
        return [
            {"id": f"{SEGMENT_ID_PREFIX}{i}", "text": text}
            for i, text in enumerate(self.texts)
        ]

    def text_for(self, segment_id: str) -> str:
        index = _segment_index(segment_id)
        if index is None or index >= len(self.texts):
            raise SegmentMappingError(f"unknown segment id {segment_id!r}")
        return self.texts[index]


def _segment_index(segment_id: str) -> int | None:
    if not segment_id.startswith(SEGMENT_ID_PREFIX):
        return None
    suffix = segment_id[len(SEGMENT_ID_PREFIX) :]
    return int(suffix) if suffix.isdigit() else None


def _resolve_mode(cfg: config_mod.AppConfig, mode: str | None) -> str:
    resolved = mode or cfg.translation.mode
    if resolved not in ("fast", "ultrafast"):
        raise ValueError(
            f"unknown mode {resolved!r}; expected 'fast' or 'ultrafast'"
        )
    return resolved


def _stream_kwargs(
    cfg: config_mod.AppConfig,
    translator: ModelAdapter,
    pages: Optional[list[int]],
    mode: str,
    progress: ProgressFn,
) -> dict[str, Any]:
    """Build the keyword arguments for ``pipeline.translate_stream``.

    Mirrors ``api.translate_stream``: the layout model is only needed in
    ``fast`` mode, and it is the cached singleton from ``api.load_layout_model``.
    """
    from pdfxlate import api

    ultrafast = mode == "ultrafast"
    model_obj = None if ultrafast else api.load_layout_model(cfg, progress)
    return {
        "translator": translator,
        "pages": pages,
        "thread": TWO_PHASE_THREAD,
        "model": model_obj,
        "ultrafast": ultrafast,
        "skip_subset_fonts": cfg.translation.skip_subset_fonts,
        "progress": progress,
        "ocr_settings": cfg.ocr,
    }


def _check_input(file: str) -> Path:
    path = Path(file).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"input file does not exist: {file}")
    if not path.is_file():
        raise ValueError(f"input path is not a file: {file}")
    return path


def collect(
    file: str,
    cfg: config_mod.AppConfig,
    pages: Optional[list[int]] = None,
    mode: str | None = None,
    progress: ProgressFn = None,
) -> Collection:
    """Run pass 1 and return the ordered paragraph collection.

    The produced PDF is discarded; only the paragraph list is kept.
    """
    path = _check_input(file)
    resolved_mode = _resolve_mode(cfg, mode)
    translator = _make_adapter(
        CollectingTranslator, cfg.translation.lang_in, cfg.translation.lang_out
    )
    data = path.read_bytes()
    pipeline.translate_stream(
        data, **_stream_kwargs(cfg, translator, pages, resolved_mode, progress)
    )
    texts = translator.texts
    logger.info("collected %d paragraph(s) from %s", len(texts), path.name)
    return Collection(
        file=str(path),
        pages=pages,
        mode=resolved_mode,
        texts=texts,
        fingerprint=sequence_fingerprint(texts),
    )


def typeset(
    collection: Collection,
    translations: dict[str, str],
    cfg: config_mod.AppConfig,
    progress: ProgressFn = None,
) -> tuple[bytes, bytes]:
    """Run pass 2 and return ``(mono_bytes, dual_bytes)``.

    ``translations`` maps segment id (``s0``, ``s1``, ...) to translated text.
    Every collected segment must be present; a missing one raises
    :class:`SegmentMappingError` before any PDF is produced.
    """
    text_map = _build_text_map(collection, translations)

    translator = _make_adapter(
        DictTranslator,
        cfg.translation.lang_in,
        cfg.translation.lang_out,
        text_map,
    )
    data = Path(collection.file).read_bytes()
    mono, dual = pipeline.translate_stream(
        data,
        **_stream_kwargs(cfg, translator, collection.pages, collection.mode, progress),
    )
    # Verify *before* returning: a mismatch means the paragraphs we translated
    # do not line up with what this pass typeset.
    translator.verify(collection.fingerprint)
    return mono, dual


def _build_text_map(
    collection: Collection, translations: dict[str, str]
) -> dict[str, str]:
    expected_ids = [f"{SEGMENT_ID_PREFIX}{i}" for i in range(len(collection.texts))]
    missing = [sid for sid in expected_ids if sid not in translations]
    if missing:
        raise SegmentMappingError(
            f"missing translations for {len(missing)} segment(s): "
            + ", ".join(missing[:10])
            + ("" if len(missing) <= 10 else f" (+{len(missing) - 10} more)")
        )
    unknown = [sid for sid in translations if sid not in expected_ids]
    if unknown:
        raise SegmentMappingError(
            "unknown segment id(s): " + ", ".join(sorted(unknown)[:10])
        )

    text_map: dict[str, str] = {}
    conflicts: list[str] = []
    for index, text in enumerate(collection.texts):
        value = translations[f"{SEGMENT_ID_PREFIX}{index}"]
        if text in text_map and text_map[text] != value:
            conflicts.append(text)
        text_map[text] = value
    if conflicts:
        raise SegmentMappingError(
            f"{len(conflicts)} duplicate paragraph(s) were given conflicting "
            "translations; identical source text must map to one translation: "
            + ", ".join(repr(c[:40]) for c in conflicts[:3])
        )
    return text_map


def output_paths(file: str) -> tuple[Path, Path]:
    """``-mono.pdf`` / ``-dual.pdf`` next to the input, as the desktop app does."""
    path = Path(file).expanduser()
    return (
        path.with_name(f"{path.stem}-mono.pdf"),
        path.with_name(f"{path.stem}-dual.pdf"),
    )


def write_outputs(file: str, mono: bytes, dual: bytes) -> tuple[str, str]:
    """Write both documents beside the input; never replace an earlier result."""
    mono_path, dual_path = output_paths(file)
    target = mono_path.parent
    paths = pipeline._unique_paths(
        target, mono_path.stem.removesuffix("-mono"), ["-mono.pdf", "-dual.pdf"]
    )
    mono_path = paths["-mono.pdf"]
    dual_path = paths["-dual.pdf"]
    mono_path.write_bytes(mono)
    dual_path.write_bytes(dual)
    return str(mono_path), str(dual_path)


# --- process-level job table ----------------------------------------------


@dataclass
class Job:
    """A collection kept between ``extract_segments`` and ``typeset_translations``."""

    collection: Collection
    provider: Optional[str] = None
    model: Optional[str] = None


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def store_job(collection: Collection, provider: str | None = None, model: str | None = None) -> Job:
    job = Job(collection=collection, provider=provider, model=model)
    with _JOBS_LOCK:
        _JOBS[collection.job_id] = job
    return job


def get_job(job_id: str) -> Job:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        known = ", ".join(sorted(_JOBS)[:5]) or "(none)"
        raise KeyError(
            f"unknown job_id {job_id!r}; run extract_segments first "
            f"(known jobs: {known})"
        )
    return job


def drop_job(job_id: str) -> None:
    with _JOBS_LOCK:
        _JOBS.pop(job_id, None)


def ensure_dirs() -> None:
    paths.ensure_dirs()
