"""Measure the memory cost of one translate_stream job (USS, not RSS).

Why USS: on Windows `psutil` reports "working set" as RSS, which includes pages
shared with other processes.  Summing RSS across Electron's process tree
double-counts them (measured: 290 MB RSS vs 69 MB USS for the same blank
window).  USS is the memory that actually disappears when the process exits.

Usage:
    <venv>/python requirements/mem-probe/probe.py <file.pdf> [fast|ultrafast]

The numbers this prints are what the diagnosis in
`requirements/memory-diagnosis-2026-11-08.md` was based on.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time

import psutil


PROCESS = psutil.Process()
STOP = threading.Event()


def uss_mb() -> float:
    try:
        return PROCESS.memory_full_info().uss / 1048576
    except Exception:  # uss is not available everywhere
        return PROCESS.memory_info().rss / 1048576


def rss_mb() -> float:
    return PROCESS.memory_info().rss / 1048576


PEAK = {"uss": 0.0, "rss": 0.0}


def _watch() -> None:
    while not STOP.is_set():
        PEAK["uss"] = max(PEAK["uss"], uss_mb())
        PEAK["rss"] = max(PEAK["rss"], rss_mb())
        time.sleep(0.03)


class FakeTranslator:
    """Deterministic stand-in: same length bookkeeping, no network."""

    def __init__(self) -> None:
        from pdfxlate.models.base import ModelAdapter

        class _T(ModelAdapter):
            name = "memprobe"

            def do_translate(self, text: str) -> str:
                return re.sub(r"[A-Za-z]+", "译文", text)

        self._inner = _T(
            profile=type("P", (), {"model": "fake", "base_url": None})(),
            lang_in="en",
            lang_out="zh",
        )
        # Skip sqlite entirely: the probe measures the pipeline, not the cache.
        self._inner.cache = type(
            "C", (), {"get": lambda *a: None, "set": lambda *a: None}
        )()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    source = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "fast"
    # "app" runs the path the Electron sidecar actually calls (``api.translate``
    # with files on disk) instead of ``translate_stream`` with bytes in memory.
    # The difference matters: the app path never holds the outputs, and it is
    # where the layout model is released after the job.
    app_path = len(sys.argv) > 3 and sys.argv[3] == "app"

    threading.Thread(target=_watch, daemon=True).start()
    print(f"[probe] start            USS {uss_mb():7.1f}  RSS {rss_mb():7.1f}")

    from pdfxlate.pdf import pipeline

    print(f"[probe] imports          USS {uss_mb():7.1f}  RSS {rss_mb():7.1f}")

    translator = FakeTranslator()
    model = None
    # On the app path `api.translate` loads and releases the model itself; a
    # reference held here would keep it alive past the release and hide what
    # the job leaves behind.
    if mode == "fast" and not app_path:
        from pdfxlate.pdf.layout import ModelInstance, OnnxModel

        model = ModelInstance.value or OnnxModel.from_pretrained()
        ModelInstance.value = model
        print(f"[probe] model loaded     USS {uss_mb():7.1f}  RSS {rss_mb():7.1f}")

    if app_path:
        import tempfile

        from pdfxlate import api

        # api.translate builds its own translator from config; swap in the
        # deterministic fake so this still measures compute and memory only.
        api.build_translator = lambda *a, **k: FakeTranslator()
        out_dir = tempfile.mkdtemp(prefix="probe-out-")
        print(f"[probe] output dir       {out_dir}")
        results = api.translate(
            [source],
            output=out_dir,
            mode=mode,
            output_mode="both",
        )
        print(f"[probe] after job        USS {uss_mb():7.1f}  RSS {rss_mb():7.1f}")
        for mono, dual in results:
            print(
                f"[probe] outputs          mono={os.path.getsize(mono) / 1048576:.2f} MB "
                f"dual={os.path.getsize(dual) / 1048576:.2f} MB"
            )
    else:
        with open(source, "rb") as fh:
            raw = fh.read()
        print(f"[probe] input read       USS {uss_mb():7.1f}  ({len(raw) / 1048576:.2f} MB)")

        mono, dual = pipeline.translate_stream(
            raw,
            translator,
            model=model,
            thread=4,
            ultrafast=(mode != "fast"),
            output_mode="both",
        )
        print(f"[probe] after job        USS {uss_mb():7.1f}  RSS {rss_mb():7.1f}")
        print(
            f"[probe] outputs          mono={len(mono) / 1048576:.2f} MB "
            f"dual={len(dual) / 1048576:.2f} MB"
        )

    STOP.set()
    print(f"[probe] PEAK             USS {PEAK['uss']:7.1f}  RSS {PEAK['rss']:7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
