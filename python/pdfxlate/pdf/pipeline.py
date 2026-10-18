"""PDF translation pipeline: parse, detect layout, translate, typeset.

Ported from the upstream project's ``high_level.py``.  Changes from upstream:

* Translation is driven by an injected :class:`~pdfxlate.models.base.ModelAdapter`
  instead of a ``service``/``envs`` string pair.
* doc/docx conversion (LibreOffice) and PDF/A output (pikepdf) are dropped —
  this application only handles PDFs.
* Fonts come from :mod:`pdfxlate.assets` rather than babeldoc.
* The mask-building loop is unchanged: it is what preserves formulas, tables
  and figures, and it is the reason the output keeps the original layout.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import tempfile
from asyncio import CancelledError
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path
from time import perf_counter
from typing import Any, BinaryIO, Callable, Optional

import numpy as np
import requests
import tqdm
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfexceptions import PDFValueError
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pymupdf import Document, Font

from pdfxlate import assets
from pdfxlate.models.base import ModelAdapter
from pdfxlate.pdf.converter import TranslateConverter
from pdfxlate.pdf.interp import PDFPageInterpreterEx

logger = logging.getLogger(__name__)

NOTO_NAME = "noto"

# Progress callback: (stage, current, total, message).
ProgressFn = Optional[Callable[[str, int, int, str], None]]

#: Folder created beside each source document when no output directory is given.
#:
#: A fixed ASCII name rather than a translated one: it lives on disk next to the
#: user's papers, not in the UI, and the same path must mean the same thing on
#: every platform and in every interface language.
OUTPUT_SUBDIR = "translated"

#: Which documents a run produces.
#:
#: ``mono`` is translated pages only, ``dual`` interleaves each source page with
#: its translation, and ``both`` produces the two files (what the CLI and the
#: MCP tools have always done).  Building the one that was not asked for is not
#: free — it is a second full serialisation of the document — so the mode
#: decides what gets built, not only what gets written.
OUTPUT_MONO = "mono"
OUTPUT_DUAL = "dual"
OUTPUT_BOTH = "both"
OUTPUT_MODES = (OUTPUT_MONO, OUTPUT_DUAL, OUTPUT_BOTH)


def _selected_pages(pages: Optional[list[int]], page_count: int) -> Optional[list[int]]:
    """Normalise a page selection, or ``None`` when everything is included.

    Sorted and de-duplicated because the caller may type ``3,1-2`` and the
    order is not a request about the output's order; out-of-range pages are
    dropped because a range is often written loosely (``1-99`` on a 10-page
    paper).  ``None``/empty means "every page", which is the common case and
    keeps ``select()`` out of the path entirely.
    """
    if not pages:
        return None
    chosen = sorted({p for p in pages if 0 <= p < page_count})
    if not chosen or len(chosen) == page_count:
        return None
    return chosen


def _unique_paths(target: Path, stem: str, suffixes: list[str]) -> dict[str, Path]:
    """Free paths for ``stem + suffix``, all sharing one `` (n)`` marker.

    A translation is a result, not a scratch file: re-running a job must not
    destroy the previous output.  The same rule the main process uses when a
    dropped file would collide, so the two places behave alike.

    The marker goes before the extension (``paper-mono (2).pdf``) and is shared
    rather than chosen per file, so a run that produced ``paper-mono (2).pdf``
    also names its dual ``paper-dual (2).pdf`` — pairing them up by name is how
    a person reads the folder.
    """

    def free(marker: str) -> dict[str, Path] | None:
        candidates = {}
        for suffix in suffixes:
            # Split each side's own tail off so the number lands before ".pdf":
            # "-mono.pdf" -> "-mono" + marker + ".pdf".
            tail = Path(f"x{suffix}")
            name = f"{stem}{suffix[: -len(tail.suffix)]}{marker}{tail.suffix}"
            candidates[suffix] = target / name
        if all(not p.exists() for p in candidates.values()):
            return candidates
        return None

    if found := free(""):
        return found
    for n in range(2, 1000):
        if found := free(f" ({n})"):
            return found
    raise FileExistsError(f"too many files named like {stem}*{suffixes[0]}")


def check_files(files: list[str]) -> list[str]:
    files = [f for f in files if not f.startswith(("http://", "https://"))]
    return [f for f in files if not os.path.exists(f)]


def _save_document(doc: Document, target, **options) -> None:
    """Save ``doc`` to a stream or path, tolerating mildly broken PDFs.

    Some real-world papers (an arXiv preprint was the reproducer) contain a
    dangling indirect reference.  MuPDF repairs the document well enough to
    read it, but writing it back fails with "cannot parse object" unless
    garbage collection is aggressive enough to drop the dead reference.

    The normal options are tried first so well-formed files keep identical
    output; the fallback only engages for documents that need it.
    """
    try:
        doc.save(target, **options)
        return
    except Exception as exc:  # noqa: BLE001 - retry with stronger cleanup
        logger.warning("save failed (%s); retrying with aggressive cleanup", exc)
        if hasattr(target, "seek"):
            target.seek(0)
            target.truncate()
    # garbage=4 removes unreachable objects and renumbers, which drops the
    # dangling reference that breaks the plain write.
    doc.save(target, garbage=4, clean=True, deflate=True, **options)


def translate_patch(
    inf: BinaryIO,
    translator: ModelAdapter,
    pages: Optional[list[int]] = None,
    vfont: str = "",
    vchar: str = "",
    thread: int = 0,
    doc_zh: Document = None,
    noto_name: str = "",
    noto: Font = None,
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model=None,
    ocr_pages: Optional[set[int]] = None,
    ultrafast: bool = False,
    perf=None,
    text_items=None,
    progress: ProgressFn = None,
    **kwarg: Any,
) -> None:
    if ultrafast and not thread:
        thread = 16
    rsrcmgr = PDFResourceManager()
    layout = {}
    device = TranslateConverter(
        rsrcmgr,
        translator,
        vfont,
        vchar,
        thread,
        layout,
        noto_name,
        noto,
        cancellation_event=cancellation_event,
    )

    assert device is not None
    device.perf = perf
    device.translator.no_cache = ultrafast and getattr(translator, "ignore_cache", False)
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch)
    total_pages = len(pages) if pages else doc_zh.page_count

    parser = PDFParser(inf)
    doc = PDFDocument(parser)
    if ultrafast:
        from pdfxlate.pdf.ultrafast import extract_layout, layout_mask

        if text_items is None:
            inf.seek(0)
            started = perf_counter()
            text_items = extract_layout(inf.read(), pages)
            if perf:
                perf.add("layout_extract", perf_counter() - started)
            inf.seek(0)
    pending_pages = []
    with (
        tqdm.tqdm(total=total_pages, disable=True) as progress_bar,
        ThreadPoolExecutor(max_workers=thread) if ultrafast else nullcontext() as pool,
    ):
        if ultrafast:
            device.translation_pool = pool
            device.translation_futures = deque()
        for pageno, page in enumerate(PDFPage.create_pages(doc)):
            if cancellation_event and cancellation_event.is_set():
                raise CancelledError("task cancelled")
            if pages and (pageno not in pages):
                continue
            progress_bar.update()
            if callback:
                callback(progress_bar)
            page.pageno = pageno
            page_started = perf_counter() if perf else 0
            if ultrafast:
                if not text_items.get(pageno):
                    logger.info(
                        "[ultrafast perf] page_scan %d: skipped (no native text)",
                        pageno + 1,
                    )
                    continue
                page.rotate = 0
                started = perf_counter()
                layout[page.pageno] = layout_mask(page, text_items.get(pageno, []))
                if perf:
                    perf.add("layout_mask", perf_counter() - started)
            else:
                pix = doc_zh[page.pageno].get_pixmap()
                image = np.frombuffer(pix.samples, np.uint8).reshape(
                    pix.height, pix.width, 3
                )[:, :, ::-1]
                page_layout = model.predict(image, imgsz=int(pix.height / 32) * 32)[0]
                if ocr_pages and pageno in ocr_pages:
                    from pdfxlate.pdf.ocr import translate_ocr_page

                    translate_ocr_page(
                        doc_zh[pageno],
                        page_layout,
                        device.translator,
                        noto,
                        thread,
                        cancellation_event,
                    )
                    continue
                box = np.ones((pix.height, pix.width))
                h, w = box.shape
                vcls = [
                    "abandon",
                    "figure",
                    "table",
                    "isolate_formula",
                    "formula_caption",
                ]
                # Two passes: translatable regions get their index + 2, then
                # protected regions are zeroed so they are preserved verbatim.
                for i, d in enumerate(page_layout.boxes):
                    if page_layout.names[int(d.cls)] not in vcls:
                        x0, y0, x1, y1 = d.xyxy.squeeze()
                        x0, y0, x1, y1 = (
                            np.clip(int(x0 - 1), 0, w - 1),
                            np.clip(int(h - y1 - 1), 0, h - 1),
                            np.clip(int(x1 + 1), 0, w - 1),
                            np.clip(int(h - y0 + 1), 0, h - 1),
                        )
                        box[y0:y1, x0:x1] = i + 2
                for i, d in enumerate(page_layout.boxes):
                    if page_layout.names[int(d.cls)] in vcls:
                        x0, y0, x1, y1 = d.xyxy.squeeze()
                        x0, y0, x1, y1 = (
                            np.clip(int(x0 - 1), 0, w - 1),
                            np.clip(int(h - y1 - 1), 0, h - 1),
                            np.clip(int(x1 + 1), 0, w - 1),
                            np.clip(int(h - y0 + 1), 0, h - 1),
                        )
                        box[y0:y1, x0:x1] = 0
                layout[page.pageno] = box
            if progress:
                progress("layout", pageno + 1, total_pages, f"page {pageno + 1}")
            # Allocate a fresh xref to hold the translated content stream.
            page.page_xref = doc_zh.get_new_xref()
            doc_zh.update_object(page.page_xref, "<<>>")
            doc_zh.update_stream(page.page_xref, b"")
            doc_zh[page.pageno].set_contents(page.page_xref)
            interpreter.process_page(page)
            if ultrafast:
                pending_pages.append(page)
            if perf:
                logger.info(
                    "[ultrafast perf] page_scan %d: %.3fs",
                    pageno + 1,
                    perf_counter() - page_started,
                )
        if ultrafast:
            logger.info(
                "[ultrafast perf] document pool: %d tasks, %d workers",
                len(device.translation_futures),
                thread,
            )
            started = perf_counter()
            progress_bar.reset(total=len(device.translation_futures))
            for future in as_completed(device.translation_futures):
                future.result()
                if cancellation_event and cancellation_event.is_set():
                    raise CancelledError("task cancelled")
                progress_bar.update()
                if callback:
                    callback(progress_bar)
            if perf:
                perf.add("translation", perf_counter() - started)
            device.translation_pool = None
            # Replay the parse so per-page/form font state is retained for
            # typesetting; the first pass only collected translations.
            progress_bar.reset(total=len(pending_pages))
            for page in pending_pages:
                if cancellation_event and cancellation_event.is_set():
                    raise CancelledError("task cancelled")
                started = perf_counter()
                interpreter.process_page(page)
                progress_bar.update()
                if callback:
                    callback(progress_bar)
                if perf:
                    logger.info(
                        "[ultrafast perf] page_render %d: %.3fs",
                        page.pageno + 1,
                        perf_counter() - started,
                    )
            assert not device.translation_futures

    device.close()
    return obj_patch


def _ocr_tessdata(language: str, prefix: str | None = None) -> str:
    """Return a tessdata directory, downloading the requested languages."""
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
        raise ValueError("Invalid OCR language; use Tesseract codes such as eng+deu")
    if prefix:
        return prefix
    return str(assets.ensure_ocr_data(language))


def _ocr_pages(doc, pages, lang_in, cancellation_event, ocr_settings=None):
    """Add a text layer to image-only pages of the working copy."""
    # OCR settings come from config (see config.OcrSettings); the language
    # falls back to a mapping from the source language.
    configured = getattr(ocr_settings, "language", None)
    language = configured or {
        "en": "eng",
        "zh": "chi_sim",
        "zh-cn": "chi_sim",
        "zh-tw": "chi_tra",
        "ja": "jpn",
        "ko": "kor",
        "de": "deu",
        "fr": "fra",
        "es": "spa",
        "it": "ita",
        "pt": "por",
        "ru": "rus",
    }.get(lang_in.lower(), lang_in or "eng")
    tessdata_prefix = getattr(ocr_settings, "tessdata_prefix", None)
    ocr_pages = set()
    tessdata = None
    for pageno in range(len(doc)):
        if cancellation_event and cancellation_event.is_set():
            raise CancelledError("task cancelled")
        if pages and pageno not in pages:
            continue
        page = doc[pageno]
        if page.get_text().strip() or not page.get_image_info():
            continue
        logger.info("OCR page %d (%s)", pageno + 1, language)
        if tessdata is None:
            tessdata = _ocr_tessdata(language, tessdata_prefix)
        try:
            data = page.get_pixmap(dpi=300, alpha=False, annots=False).pdfocr_tobytes(
                language=language,
                tessdata=tessdata,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"OCR failed on page {pageno + 1} using '{language}' data in {tessdata}. "
                "Check the language data and the ocr.tessdata_prefix setting "
                "in config.yaml."
            ) from exc
        with Document(stream=data) as recognized:
            if not recognized[0].get_text().strip():
                logger.warning("OCR found no text on page %d", pageno + 1)
                continue
            contents = doc.get_new_xref()
            doc.update_object(contents, "<<>>")
            doc.update_stream(contents, b"")
            page.set_contents(contents)
            page.show_pdf_page(
                page.rect * page.derotation_matrix,
                recognized,
                0,
                rotate=page.rotation,
            )
        ocr_pages.add(pageno)
    return ocr_pages


def translate_stream(
    stream: bytes,
    translator: ModelAdapter,
    pages: Optional[list[int]] = None,
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model=None,
    skip_subset_fonts: bool = False,
    ultrafast: bool = False,
    perf=None,
    progress: ProgressFn = None,
    ocr_settings=None,
    output_mode: str = OUTPUT_BOTH,
    **kwarg: Any,
):
    """Translate a PDF given as bytes.

    Returns ``(mono_bytes, dual_bytes)``; the side that ``output_mode`` did not
    ask for is ``b""``.

    ``output_mode`` decides which documents are built at all — ``mono``
    (translated pages only), ``dual`` (each source page followed by its
    translation) or ``both``.

    ``pages`` has two jobs, and they are different: it selects which pages are
    *translated*, and it also **cuts the output down to those pages**.  A page
    range is a request for that part of the paper, so a range of 2-3 is a
    two-page result — not the whole document with the other pages left
    untranslated.
    """
    if output_mode not in OUTPUT_MODES:
        raise ValueError(
            f"output_mode must be one of {', '.join(OUTPUT_MODES)}; got {output_mode!r}"
        )
    if ultrafast and not thread:
        thread = 16
    own_perf = perf is None
    text_items = None
    if ultrafast:
        from pdfxlate.pdf.ultrafast import PerfTimer, extract_layout

        if own_perf:
            perf = PerfTimer()
        logger.info(
            "[ultrafast perf] skipped: layout models, rasterization, OCR, font subsetting"
        )
        text_items = extract_layout(stream, pages)
        perf.step("layout_extract")
    font_list = [("tiro", None)]

    if progress:
        progress("assets", 0, 1, "preparing fonts")
    font_path = download_font(translator.lang_out)
    noto_name = NOTO_NAME
    noto = Font(noto_name, font_path)
    font_list.append((noto_name, font_path))
    if perf:
        perf.step("font_setup")

    doc_en = Document(stream=stream)
    if ultrafast:
        doc_zh = Document(stream=stream)
    else:
        stream = io.BytesIO()
        _save_document(doc_en, stream)
        doc_zh = Document(stream=stream)
    ocr_pages = (
        set()
        if ultrafast
        else _ocr_pages(
            doc_zh, pages, translator.lang_in, cancellation_event, ocr_settings
        )
    )
    page_count = doc_zh.page_count
    if perf:
        perf.step("document_open")
    font_id = {}
    for pageno, page in enumerate(doc_zh):
        if ultrafast and (
            (pages and pageno not in pages) or not text_items.get(pageno)
        ):
            continue
        for font in font_list:
            font_id[font[0]] = page.insert_font(font[0], font[1])
    # Inject the font resources into every xref, including those reached
    # through indirect Resources or XObject dictionaries.
    xreflen = doc_zh.xref_length()
    for xref in range(1, xreflen):
        for label in ["Resources/", ""]:
            try:
                font_res = doc_zh.xref_get_key(xref, f"{label}Font")
                target_key_prefix = f"{label}Font/"
                if font_res[0] == "xref":
                    resource_xref_id = re.search(r"(\d+) 0 R", font_res[1]).group(1)
                    xref = int(resource_xref_id)
                    font_res = ("dict", doc_zh.xref_object(xref))
                    target_key_prefix = ""

                if font_res[0] == "dict":
                    for font in font_list:
                        target_key = f"{target_key_prefix}{font[0]}"
                        font_exist = doc_zh.xref_get_key(xref, target_key)
                        if font_exist[0] == "null":
                            doc_zh.xref_set_key(
                                xref,
                                target_key,
                                f"{font_id[font[0]]} 0 R",
                            )
            except Exception:
                pass

    if perf:
        perf.step("font_resources")
    fp = io.BytesIO()

    _save_document(doc_zh, fp)
    if perf:
        perf.step("working_copy_save")
    obj_patch: dict = translate_patch(
        fp,
        translator,
        pages=pages,
        vfont=vfont,
        vchar=vchar,
        thread=thread,
        doc_zh=doc_zh,
        noto_name=noto_name,
        noto=noto,
        callback=callback,
        cancellation_event=cancellation_event,
        model=model,
        ocr_pages=ocr_pages,
        ultrafast=ultrafast,
        perf=perf,
        text_items=text_items,
        progress=progress,
    )
    if perf:
        perf.step("page_processing")

    for obj_id, ops_new in obj_patch.items():
        doc_zh.update_stream(obj_id, ops_new.encode())
    if perf:
        perf.step("apply_patches")

    # Cut the output down *before* assembling the interleaved copy and before
    # font subsetting, so no work is spent on pages that are about to be
    # dropped.  Both documents take the same selection, in the same order, so
    # the source/translation pairing stays aligned.
    keep = _selected_pages(pages, page_count)
    if keep is not None:
        doc_zh.select(keep)
        doc_en.select(keep)
        if perf:
            perf.step("page_select")
    kept = len(keep) if keep is not None else page_count

    want_dual = output_mode in (OUTPUT_DUAL, OUTPUT_BOTH)
    want_mono = output_mode in (OUTPUT_MONO, OUTPUT_BOTH)

    # `doc_en` holds the source pages and `doc_zh` the translated ones; the
    # interleaved document pairs them page by page.  Skipped entirely when it
    # was not asked for — that assembly is a full second copy of the document.
    if want_dual:
        doc_en.insert_file(doc_zh)
        for id in range(kept):
            doc_en.move_page(kept + id, id * 2 + 1)
    if perf:
        perf.step("bilingual_assembly")
    if not (skip_subset_fonts or ultrafast):
        if want_mono:
            doc_zh.subset_fonts(fallback=True)
        if want_dual:
            doc_en.subset_fonts(fallback=True)
    if perf:
        perf.step("font_subset")

    def serialize(doc: Document) -> bytes:
        try:
            return doc.write(deflate=True, garbage=3, use_objstms=1)
        except Exception as exc:  # noqa: BLE001 - see _save_document
            logger.warning("serialize failed (%s); retrying with aggressive cleanup", exc)
            return doc.write(deflate=True, garbage=4, clean=True, use_objstms=1)

    mono_bytes = serialize(doc_zh) if want_mono else b""
    dual_bytes = serialize(doc_en) if want_dual else b""
    if perf:
        perf.step("pdf_serialize")
        if own_perf:
            perf.report()
    return mono_bytes, dual_bytes


def translate(
    files: list[str],
    translator: ModelAdapter,
    output: str = "",
    pages: Optional[list[int]] = None,
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model=None,
    skip_subset_fonts: bool = False,
    ultrafast: bool = False,
    progress: ProgressFn = None,
    ocr_settings=None,
    output_mode: str = OUTPUT_BOTH,
    **kwarg: Any,
):
    """Translate PDF files; returns ``[(mono_path, dual_path), ...]``.

    An output the ``output_mode`` did not ask for is returned as an empty
    string, and its file is never written.  Existing files are never replaced:
    a second run of the same paper lands as ``name (2)``.
    """
    if output_mode not in OUTPUT_MODES:
        raise ValueError(
            f"output_mode must be one of {', '.join(OUTPUT_MODES)}; got {output_mode!r}"
        )
    if not files:
        raise PDFValueError("No files to process.")

    missing_files = check_files(files)
    if missing_files:
        raise PDFValueError(f"Some files do not exist: {missing_files}")

    result_files = []
    for file in files:
        perf = None
        if ultrafast:
            from pdfxlate.pdf.ultrafast import PerfTimer

            perf = PerfTimer()
        if type(file) is str and file.startswith(("http://", "https://")):
            logger.info("downloading %s", file)
            r = requests.get(file, allow_redirects=True, timeout=(10, 120))
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
                tmp_file.write(r.content)
                file = tmp_file.name

        filename = os.path.splitext(os.path.basename(file))[0]
        with open(file, "rb") as doc_raw:
            s_raw = doc_raw.read()
        if perf:
            perf.step("input_read")

        s_mono, s_dual = translate_stream(
            s_raw,
            translator=translator,
            pages=pages,
            thread=thread,
            vfont=vfont,
            vchar=vchar,
            callback=callback,
            cancellation_event=cancellation_event,
            model=model,
            skip_subset_fonts=skip_subset_fonts,
            ultrafast=ultrafast,
            perf=perf,
            progress=progress,
            ocr_settings=ocr_settings,
            output_mode=output_mode,
        )
        # An empty `output` means "beside the source": each file gets its own
        # `translated/` subfolder, so a job over files from several folders
        # keeps every output with the paper it came from.  Callers that want one
        # shared directory (the CLI's `-o`, the MCP tools) still pass one.
        target = Path(output) if output else Path(file).resolve().parent / OUTPUT_SUBDIR
        target.mkdir(parents=True, exist_ok=True)

        # Only the documents that were actually built, and never on top of an
        # earlier result — a second run of the same paper adds a numbered copy.
        sides = []
        if s_mono:
            sides.append(("-mono.pdf", s_mono))
        if s_dual:
            sides.append(("-dual.pdf", s_dual))
        paths = _unique_paths(target, filename, [s for s, _ in sides]) if sides else {}
        for suffix, data in sides:
            Path(paths[suffix]).write_bytes(data)
        if perf:
            perf.step("output_write")
            perf.report()
        result_files.append(
            (
                str(paths.get("-mono.pdf", "")),
                str(paths.get("-dual.pdf", "")),
            )
        )

    return result_files


# Font selection mirrors upstream: CJK targets get a serif CJK face, other
# scripts fall back to the pan-Noto font that covers them.
_CJK_TARGETS = {
    "zh-cn": "SourceHanSerifCN-Regular.ttf",
    "zh-hans": "SourceHanSerifCN-Regular.ttf",
    "zh": "SourceHanSerifCN-Regular.ttf",
    "zh-tw": "SourceHanSerifTW-Regular.ttf",
    "zh-hant": "SourceHanSerifTW-Regular.ttf",
    "ja": "SourceHanSerifJP-Regular.ttf",
    "ko": "SourceHanSerifKR-Regular.ttf",
}


def download_font(lang: str) -> str:
    """Return the path to the font used for typesetting the target language."""
    path = assets.ensure_font(lang)
    logger.info("using font: %s", path)
    return str(path)
