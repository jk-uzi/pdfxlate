"""Two-page spread: lay a document out as side-by-side page pairs.

Why this exists rather than two viewers side by side: Chromium's built-in PDF
viewer is an out-of-process plugin with its own scrollbar, and a page cannot
read its scroll position.  Two frames therefore scroll independently and can
never be kept in step, which defeats the point of a facing-page view.

So the pages are combined into a *derived document* and shown in the ordinary
single viewer: one document, one scrollbar.  Each source page is placed with
``show_pdf_page``, which copies it as a Form XObject — vector, selectable text,
not a raster image.

The output is cached on disk because generating it means rewriting every page
of the source; the key is the source's path, size and mtime, so an edited file
is never served a stale spread.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from pdfxlate import paths

logger = logging.getLogger(__name__)

#: How long an unused spread file is kept.  These are pure derivatives, so
#: discarding them only costs the regeneration time.
_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


class SpreadError(ValueError):
    """Raised when a source file cannot be turned into a spread."""


def spread_dir() -> Path:
    """Where derived spread documents live."""
    return paths.cache_dir() / "spread"


def _cache_key(source: Path) -> str:
    """Identity of one version of a source file.

    Path alone is not enough: a file that is edited or replaced in place would
    otherwise keep serving the old spread.  Size and mtime change on any real
    edit, and together with the path they are cheap to read.
    """
    stat = source.stat()
    raw = f"{source.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _prune(directory: Path, keep: Path) -> None:
    """Drop old spread files, never the one just produced."""
    cutoff = time.time() - _MAX_AGE_SECONDS
    for entry in directory.glob("*.pdf"):
        if entry == keep:
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            # Another process may be reading or removing it; either way this is
            # housekeeping and must not fail the request.
            pass


def build_spread(source: str | Path) -> tuple[Path, int, int]:
    """Write (or reuse) a facing-pages copy of ``source``.

    Returns ``(spread_path, source_pages, spread_pages)``.  Page *k* of the
    result holds source pages ``2k-1`` and ``2k``.

    An odd source ends with a spread whose right half is blank, and that sheet
    keeps the full width.  Making it narrow instead would be worse than the
    wasted space: the viewer scales each page to the same width, so a half-width
    page would be blown up to double size and the last page would jump.
    """
    from pymupdf import Document, FileDataError, Rect

    raw = Path(source).expanduser()
    if not raw.exists():
        raise FileNotFoundError(f"input file does not exist: {raw}")
    if not raw.is_file():
        raise SpreadError(f"input path is not a file: {raw}")

    directory = spread_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{_cache_key(raw)}.pdf"

    try:
        with Document(raw) as doc:
            if not doc.is_pdf:
                raise SpreadError(f"input file is not a PDF: {raw}")
            page_count = doc.page_count
            if page_count == 0:
                raise SpreadError(f"input PDF has no pages: {raw}")
            spreads = (page_count + 1) // 2

            if target.exists():
                # Already built for this exact version of the source.  Prune
                # here too: a file that is always a cache hit would otherwise
                # never trigger the housekeeping below.
                _prune(directory, target)
                return target, page_count, spreads

            out = Document()
            partial = target.with_name(target.name + ".part")
            try:
                for index in range(0, page_count, 2):
                    left = doc[index]
                    right = doc[index + 1] if index + 1 < page_count else None
                    left_box = left.rect
                    right_box = right.rect if right is not None else None

                    # A spread is as wide as the two pages it holds.  Heights
                    # can differ between pages, so the taller one wins: using
                    # the left page's height would clip a taller right page.
                    right_width = right_box.width if right_box else left_box.width
                    width = left_box.width + right_width
                    height = max(
                        left_box.height, right_box.height if right_box else 0.0
                    )
                    page = out.new_page(width=width, height=height)

                    # Placed at its own size and origin, so nothing is scaled
                    # and the page looks exactly as it does on its own.
                    page.show_pdf_page(
                        Rect(0.0, 0.0, left_box.width, left_box.height), doc, index
                    )
                    if right is not None:
                        page.show_pdf_page(
                            Rect(
                                left_box.width,
                                0.0,
                                left_box.width + right_box.width,
                                right_box.height,
                            ),
                            doc,
                            index + 1,
                        )

                # Written under a temporary name and then renamed: a
                # half-written PDF left at the cache path would fail to open
                # while looking like a valid cache hit.
                out.save(partial, garbage=3, deflate=True)
            finally:
                out.close()
                # Also removes the partial file when saving threw.
                if not target.exists() and partial.exists() and not partial.stat().st_size:
                    partial.unlink(missing_ok=True)

            partial.replace(target)
    except FileDataError as exc:
        raise SpreadError(f"could not read PDF file: {raw} ({exc})") from exc

    _prune(directory, target)
    return target, page_count, (page_count + 1) // 2
