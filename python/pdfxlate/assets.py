"""Download and cache the layout model, fonts and OCR data.

This replaces the babeldoc asset layer the upstream project depended on.  Two
behaviours matter for a desktop app:

* **Atomic writes** — files land in ``*.part`` and are renamed only after the
  checksum matches, so an interrupted download never poisons the cache.
* **Mirror fallback** — ``huggingface.co`` is unreachable from some networks
  (it is blocked outright on the machine this was developed on).  Downloads
  try the configured endpoint first, then fall back to ``hf-mirror.com``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from pdfxlate import paths

logger = logging.getLogger(__name__)

_CHUNK = 1 << 20  # 1 MiB
# (connect, read) seconds.  The connect timeout is short so an unreachable
# mirror (huggingface.co is blackholed on some networks) fails fast and the
# next candidate is tried; the read timeout is generous because large release
# assets can stall mid-stream on a slow link.
_TIMEOUT = (8, 300)
_ATTEMPTS = 2
_RETRY_BACKOFF = 2.0


@dataclass(frozen=True)
class Asset:
    """A downloadable file with its expected identity."""

    filename: str
    urls: tuple[str, ...]
    sha256: str | None = None
    size: int | None = None
    subdir: str = ""

    @property
    def target(self) -> Path:
        base = paths.assets_dir()
        return (base / self.subdir / self.filename) if self.subdir else (base / self.filename)


# --- HuggingFace endpoints ------------------------------------------------
# Order matters: an explicit HF_ENDPOINT wins, then the official host, then the
# mainland-China mirror which is reachable where huggingface.co is not.
def _hf_endpoints() -> list[str]:
    endpoints = []
    if env := os.environ.get("HF_ENDPOINT"):
        endpoints.append(env.rstrip("/"))
    endpoints.append("https://huggingface.co")
    if "https://hf-mirror.com" not in endpoints:
        endpoints.append("https://hf-mirror.com")
    # A previously successful endpoint is tried first, so a network where the
    # official host is blackholed does not pay its connect timeout every run.
    if preferred := _read_preferred_endpoint():
        if preferred in endpoints:
            endpoints.remove(preferred)
        endpoints.insert(0, preferred)
    return endpoints


def _preferred_endpoint_file() -> Path:
    return paths.assets_dir() / ".hf_endpoint"


def _read_preferred_endpoint() -> str | None:
    try:
        return _preferred_endpoint_file().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _remember_endpoint(endpoint: str) -> None:
    try:
        _preferred_endpoint_file().parent.mkdir(parents=True, exist_ok=True)
        _preferred_endpoint_file().write_text(endpoint, encoding="utf-8")
    except OSError:
        logger.debug("could not persist preferred HF endpoint", exc_info=True)


def _endpoint_of(url: str) -> str:
    for endpoint in _hf_endpoints():
        if url.startswith(endpoint):
            return endpoint
    return ""


_LAYOUT_REPO = "wybxc/DocLayout-YOLO-DocStructBench-onnx"
_LAYOUT_FILE = "doclayout_yolo_docstructbench_imgsz1024.onnx"

_GO_NOTO_FILE = "GoNotoKurrent-Regular.ttf"
_GO_NOTO_URL = (
    "https://github.com/satbyy/go-noto-universal/releases/download/v7.0/"
    f"{_GO_NOTO_FILE}"
)
_SOURCE_HAN_URL = (
    "https://github.com/timelic/source-han-serif/releases/download/main/{name}"
)

# Languages that need a CJK serif face instead of the pan-Noto fallback.
_CJK_FONTS = {
    "zh-cn": "SourceHanSerifCN-Regular.ttf",
    "zh-hans": "SourceHanSerifCN-Regular.ttf",
    "zh": "SourceHanSerifCN-Regular.ttf",
    "zh-tw": "SourceHanSerifTW-Regular.ttf",
    "zh-hant": "SourceHanSerifTW-Regular.ttf",
    "ja": "SourceHanSerifJP-Regular.ttf",
    "ko": "SourceHanSerifKR-Regular.ttf",
}

# Non-CJK scripts GoNotoKurrent is known to cover (upstream's ``noto_list``).
# This is not the full set of languages GoNoto can typeset: Latin and other
# scripts outside this list also use it, so membership only distinguishes a
# *recognised* GoNoto language from an unknown code, not supported/unsupported.
_NOTO_LANGS = {
    "am", "ar", "bn", "bg", "chr", "el", "gu", "iw", "hi", "kn",
    "ml", "mr", "ru", "sr", "ta", "te", "th", "ur", "uk",
}


def layout_model_asset() -> Asset:
    urls = tuple(
        f"{ep}/{_LAYOUT_REPO}/resolve/main/{_LAYOUT_FILE}" for ep in _hf_endpoints()
    )
    return Asset(filename=_LAYOUT_FILE, urls=urls, subdir="models")


def font_filename(lang: str) -> str:
    """Return the font file used to typeset ``lang``.

    The single source of truth for the language→font mapping: CJK codes get a
    region-specific SourceHanSerif face, everything else gets the pan-Noto
    fallback.  Unknown codes fall back to GoNoto rather than raising: the
    target language comes from user input, so a typo or a language we have not
    enumerated should not abort an otherwise valid translation.  A debug log
    records the surprise for whoever debugs a mis-rendered script later.
    """
    lang = lang.lower()
    if name := _CJK_FONTS.get(lang):
        return name
    if lang not in _NOTO_LANGS:
        logger.debug("unknown target language %r; falling back to GoNotoKurrent", lang)
    return _GO_NOTO_FILE


def font_asset(lang: str) -> Asset:
    """Return the font asset appropriate for the target language."""
    name = font_filename(lang)
    if name == _GO_NOTO_FILE:
        return Asset(filename=name, urls=(_GO_NOTO_URL,), subdir="fonts")
    return Asset(filename=name, urls=(_SOURCE_HAN_URL.format(name=name),), subdir="fonts")


def supported_languages() -> set[str]:
    """Every language code with an explicit font mapping.

    CJK codes map to a region face and :data:`_NOTO_LANGS` to GoNoto.  Other
    languages still resolve (see :func:`font_filename`), so this is the set of
    *recognised* codes, not the set that can be translated.
    """
    return set(_CJK_FONTS) | set(_NOTO_LANGS)


def ocr_data_asset(language: str) -> Asset:
    """Tesseract language data for a Tesseract code such as ``eng`` or ``eng+deu``.

    ``language`` may combine codes with ``+``; each becomes its own asset, so
    callers should use :func:`ensure_ocr_data` instead of this directly.
    """
    return Asset(
        filename=f"{language}.traineddata",
        urls=(
            "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/"
            f"{language}.traineddata",
        ),
        subdir="tessdata",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify(path: Path, asset: Asset) -> bool:
    if asset.sha256 and _sha256(path) != asset.sha256:
        return False
    if asset.size is not None and path.stat().st_size != asset.size:
        return False
    return True


def _bundle_root() -> Path | None:
    """Directory holding assets shipped inside the PyInstaller bundle.

    ``sys._MEIPASS`` is PyInstaller's extraction root (``_internal`` for an
    onedir build). It is absent in a source checkout, which is what makes the
    bundled check a no-op there.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    root = Path(base) / "bundle_assets"
    return root if root.is_dir() else None


def bundled_path(asset: Asset) -> Path | None:
    """Return the copy of ``asset`` shipped inside the app, if there is one."""
    root = _bundle_root()
    if root is None:
        return None
    candidate = root / asset.subdir / asset.filename if asset.subdir else root / asset.filename
    return candidate if candidate.is_file() else None


def _download_one(url: str, dest: Path, on_progress=None) -> None:
    """Stream ``url`` into ``dest`` atomically.

    Writes to ``dest.part`` and renames on success so a killed process leaves
    no half-file behind.  Retries transient network failures with backoff,
    since large release assets are prone to mid-stream stalls.
    """
    part = dest.with_suffix(dest.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with requests.get(
                url, stream=True, timeout=_TIMEOUT, allow_redirects=True
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with part.open("wb") as fh:
                    for chunk in resp.iter_content(_CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, total)
            # A server that closes early can end iter_content() without
            # raising. Without this check the truncated file is renamed into
            # place and looked like a valid cache entry forever.
            if total and done != total:
                raise OSError(f"incomplete download: {done} of {total} bytes")
            part.replace(dest)
            return
        except Exception as exc:  # noqa: BLE001 - retry, then fall through to next mirror
            last_error = exc
            part.unlink(missing_ok=True)
            if attempt < _ATTEMPTS:
                delay = _RETRY_BACKOFF**attempt
                logger.warning(
                    "download attempt %d/%d failed (%s); retrying in %.0fs",
                    attempt,
                    _ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)
    assert last_error is not None
    raise last_error


def ensure(asset: Asset, on_progress=None, force: bool = False) -> Path:
    """Return the local path to ``asset``, downloading it if needed.

    Order: an already-cached file, then the copy shipped inside the app, then
    the network. A bundled file is copied into the cache rather than used in
    place so every runtime write (notably ONNX Runtime's ``.optimized`` model)
    stays in the user's writable data directory.
    """
    dest = asset.target
    if dest.exists() and not force and _verify(dest, asset):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not force:
        bundled = bundled_path(asset)
        if bundled is not None and _verify(bundled, asset):
            part = dest.with_suffix(dest.suffix + ".part")
            try:
                logger.info("installing bundled %s", asset.filename)
                shutil.copy2(bundled, part)
                part.replace(dest)
                return dest
            except OSError:
                part.unlink(missing_ok=True)
                logger.warning(
                    "could not copy bundled %s; falling back to download",
                    asset.filename,
                    exc_info=True,
                )

    errors: list[str] = []
    for url in asset.urls:
        try:
            logger.info("downloading %s", url)
            _download_one(url, dest, on_progress=on_progress)
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            errors.append(f"{url}: {exc}")
            continue
        if not _verify(dest, asset):
            errors.append(f"{url}: checksum mismatch")
            dest.unlink(missing_ok=True)
            continue
        if endpoint := _endpoint_of(url):
            _remember_endpoint(endpoint)
        return dest

    detail = "\n  ".join(errors) if errors else "no download URLs configured"
    raise RuntimeError(f"Could not download {asset.filename}:\n  {detail}")


def ensure_layout_model(on_progress=None, force: bool = False) -> Path:
    return ensure(layout_model_asset(), on_progress=on_progress, force=force)


def ensure_font(lang: str, on_progress=None, force: bool = False) -> Path:
    return ensure(font_asset(lang), on_progress=on_progress, force=force)


def ensure_ocr_data(language: str, on_progress=None) -> Path:
    """Ensure tesseract data for every code in ``language`` (``+``-separated)."""
    cache = paths.assets_dir() / "tessdata"
    for code in dict.fromkeys(language.split("+")):
        ensure(ocr_data_asset(code), on_progress=on_progress)
    return cache


def status(lang: str | None = None) -> dict[str, bool]:
    """Report which assets are already present, for the UI's first-run guide.

    ``font_zh``/``font_ja``/``font_ko`` are kept for the renderer, which still
    reads ``font_zh`` to decide whether to show the first-run banner.  Passing
    ``lang`` additionally reports ``font_ready_for`` for that target language,
    so a caller translating to ``ru``/``ar``/``th`` (GoNoto) is not told its
    font is missing just because no CJK face is cached.  ``lang`` defaults to
    ``None`` and the key is omitted then, which keeps ``sidecar.py``'s
    argument-less ``assets.status`` call byte-for-byte compatible.
    """
    def present(asset: Asset) -> bool:
        # "Ready" means the user will not have to download it: either it is
        # already cached, or the installer shipped it.
        return asset.target.exists() or bundled_path(asset) is not None

    result = {
        "layout_model": present(layout_model_asset()),
        "font_zh": present(font_asset("zh")),
        "font_ja": present(font_asset("ja")),
        "font_ko": present(font_asset("ko")),
    }
    if lang is not None:
        result["font_ready_for"] = present(font_asset(lang))
    return result


def verify_layout_metadata(model_path: Path) -> tuple[int, list[str]]:
    """Read the ``stride``/``names`` metadata the layout model must carry.

    The detector needs both fields; a model without them fails deep inside
    inference, so this surfaces the problem right after download.
    """
    import ast

    import onnx

    model = onnx.load(str(model_path), load_external_data=False)
    meta = {d.key: d.value for d in model.metadata_props}
    missing = [k for k in ("stride", "names") if k not in meta]
    if missing:
        raise RuntimeError(
            f"Layout model is missing required metadata {missing}; "
            f"got keys {sorted(meta)}"
        )
    return ast.literal_eval(meta["stride"]), ast.literal_eval(meta["names"])


def free_disk_hint() -> str:
    """Human-readable free space in the assets directory, for error messages."""
    try:
        usage = shutil.disk_usage(paths.assets_dir())
        return f"{usage.free / 1e9:.1f} GB free"
    except OSError:
        return "unknown free space"


def _platform_tag() -> str:
    return f"{sys.platform}-{os.uname().machine if hasattr(os, 'uname') else 'unknown'}"
