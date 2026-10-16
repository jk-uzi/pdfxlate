"""Rasterise the app mark into the files electron-builder needs.

PyMuPDF is used because it renders SVG natively and is already a runtime
dependency of the app (PyMuPDF is the PDF engine), so this adds nothing to
install. Electron's offscreen capture was tried first and abandoned: a hidden
window never composites, so `capturePage` hangs and the offscreen `paint` event
delivers a blank first frame.

`src/renderer/icon.svg` is the single source. The title bar references it
directly, and the packaged icons are rasterised from it, so the two cannot
drift apart.

Writes:
    assets/icon.png           512x512, for macOS/Linux and the top-level config
    assets/icon.ico           16..256, for Windows
    .zcode/icon-preview.png   the small sizes magnified, for eyeballing

Usage:
    python scripts/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "src" / "renderer" / "icon.svg"
OUT_DIR = ROOT / "assets"

MASTER = 512
# The sizes Windows asks for. 256 is the largest an .ico entry can address.
ICO_SIZES = (16, 20, 22, 24, 32, 48, 64, 128, 256)
# (size, magnification) for the preview sheet. 22 is in the list because that
# is the size the title bar actually draws, so it is the one that decides
# whether the mark reads.
PREVIEW_SIZES = ((16, 8), (22, 7), (32, 5), (48, 4), (64, 3))


def render(size: int) -> tuple[bytes, bytes, int]:
    """Render the mark at exactly ``size`` square.

    Returns ``(png_bytes, rgba_samples, size)``. The PNG is taken straight from
    the rendered pixmap rather than rebuilt from the samples: the constructor
    that would rebuild one takes positional arguments only, and going through
    the pixmap keeps the alpha channel intact.
    """
    with fitz.open(SVG) as doc:
        page = doc[0]
        scale = size / page.rect.width
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
        assert pixmap.width == pixmap.height, (pixmap.width, pixmap.height)
        assert pixmap.n == 4, f"expected RGBA, got {pixmap.n} channels"
        return pixmap.tobytes("png"), bytes(pixmap.samples), pixmap.width


def build_ico(entries: list[tuple[int, bytes]]) -> bytes:
    """Assemble an ICO container from PNG-encoded entries.

    Each entry's payload is a whole PNG, which the format has allowed since
    Vista. A 256px entry is written as 0 in the width/height bytes, because
    that field is one byte wide and 256 does not fit.
    """
    header = struct.pack("<HHH", 0, 1, len(entries))
    directory = b""
    offset = len(header) + 16 * len(entries)
    for size, png in entries:
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII",
            dimension,
            dimension,
            0,  # palette size
            0,  # reserved
            1,  # colour planes
            32,  # bits per pixel
            len(png),
            offset,
        )
        offset += len(png)
    return header + directory + b"".join(png for _, png in entries)


def build_preview(rendered: dict[int, tuple[bytes, bytes, int]]) -> tuple[bytes, int, int]:
    """Magnify each small size, side by side, so the raster can be judged.

    Nearest-neighbour on purpose: the question is what the small sizes actually
    resolve to, and smooth interpolation would invent detail that is not there.
    Composited over white because the mark has transparent corners.
    """
    gap = 16
    margin = 16
    cells = [(size, zoom) for size, zoom in PREVIEW_SIZES]
    height = max(size * zoom for size, zoom in cells)
    width = margin * 2 + sum(size * zoom for size, zoom in cells) + gap * (len(cells) - 1)

    canvas = bytearray(b"\xff" * (width * height * 4))

    x = margin
    for size, zoom in cells:
        _, rgba, _ = rendered[size]
        for row in range(size):
            for col in range(size):
                src = (row * size + col) * 4
                r, g, b, a = rgba[src : src + 4]
                if a == 0:
                    continue
                for dy in range(zoom):
                    py = row * zoom + dy
                    base = (py * width + x + col * zoom) * 4
                    for dx in range(zoom):
                        dst = base + dx * 4
                        if a == 255:
                            canvas[dst : dst + 4] = bytes((r, g, b, 255))
                        else:
                            # Straight-alpha blend over the white canvas.
                            inv = 255 - a
                            canvas[dst] = (r * a + 255 * inv) // 255
                            canvas[dst + 1] = (g * a + 255 * inv) // 255
                            canvas[dst + 2] = (b * a + 255 * inv) // 255
                            canvas[dst + 3] = 255
        x += size * zoom + gap

    return bytes(canvas), width, height


def main() -> int:
    if not SVG.exists():
        print(f"error: missing source {SVG}", file=sys.stderr)
        return 1

    rendered: dict[int, tuple[bytes, bytes, int]] = {}
    for size in (MASTER, *ICO_SIZES):
        if size in rendered:
            continue
        rendered[size] = render(size)
        print(f"rendered {size}px")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    master_png, _, master_size = rendered[MASTER]
    (OUT_DIR / "icon.png").write_bytes(master_png)
    print(f"wrote {OUT_DIR.relative_to(ROOT) / 'icon.png'} ({master_size}x{master_size})")

    (OUT_DIR / "icon.ico").write_bytes(
        build_ico([(size, rendered[size][0]) for size in ICO_SIZES])
    )
    print(f"wrote {OUT_DIR.relative_to(ROOT) / 'icon.ico'} ({', '.join(map(str, ICO_SIZES))})")

    preview_rgba, width, height = build_preview(rendered)
    preview = ROOT / ".zcode" / "icon-preview.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    _write_rect(preview, preview_rgba, width, height)
    print(f"wrote {preview.relative_to(ROOT)} ({width}x{height})")
    return 0


def _write_rect(path: Path, rgba: bytes, width: int, height: int) -> None:
    """Write a non-square RGBA buffer as a PNG (PyMuPDF Pixmap is square-only)."""
    import zlib

    raw = b"".join(
        b"\x00" + rgba[row * width * 4 : (row + 1) * width * 4] for row in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    raise SystemExit(main())
