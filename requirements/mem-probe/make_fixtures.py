"""Generate the two synthetic fixtures the memory probe was measured on.

Neither is a real paper: they are shaped to isolate one variable at a time.
  paper  — 15 pages, ~1.8 MB, mostly text with a few mid-size figures
  scan   — 30 pages, ~46 MB, one full-page greyscale image per page
Run with the project venv (needs PyMuPDF).
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pymupdf


BODY = (
    "The dominant sequence transduction models are based on complex recurrent or "
    "convolutional neural networks that include an encoder and a decoder. The best "
    "performing models also connect the encoder and decoder through an attention "
    "mechanism. We propose a new simple network architecture. "
) * 3


def make_paper(path: str) -> None:
    rng = np.random.default_rng(3)
    doc = pymupdf.open()
    for i in range(15):
        page = doc.new_page(width=595, height=842)
        y = 55.0
        for j in range(46):
            start = (j * 37) % 300
            page.insert_text((50, y), BODY[start : start + 95], fontsize=9)
            y += 16.5
        if i % 5 == 2:
            arr = (rng.random((420, 560, 3)) * 90 + 130).astype("uint8")
            pix = pymupdf.Pixmap(pymupdf.csRGB, 560, 420, arr.tobytes(), False)
            page.insert_image(pymupdf.Rect(60, 300, 535, 660), pixmap=pix)
    doc.save(path, deflate=True, garbage=3)
    doc.close()


def make_scan(path: str) -> None:
    rng = np.random.default_rng(7)
    doc = pymupdf.open()
    for i in range(30):
        page = doc.new_page(width=595, height=842)
        arr = (rng.random((1754, 1240)) * 60 + 195).astype("uint8")
        pix = pymupdf.Pixmap(pymupdf.csGRAY, 1240, 1754, arr.tobytes(), False)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
        page.insert_text((60, 800), f"Page {i + 1} scanned text layer", fontsize=10)
    doc.save(path, deflate=True, garbage=3)
    doc.close()


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    paper = os.path.join(out, "memtest-paper.pdf")
    scan = os.path.join(out, "memtest-scan.pdf")
    make_paper(paper)
    make_scan(scan)
    for p in (paper, scan):
        print(f"{p}: {os.path.getsize(p) / 1048576:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
