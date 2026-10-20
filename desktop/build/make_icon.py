"""Génère le logo de Carriole (icon.png 256 px, icon.ico, et logo.svg pour l'interface) sans dépendance graphique :
numpy + zlib.

« i/o » (entrées, sorties), celui du nom Carr-i/o-le, en ambre sur le bleu. À relancer si on change le dessin :
    .venv\\Scripts\\python.exe desktop\\build\\make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

SIZE = 256
SS = 4  # suréchantillonnage pour des bords lissés
BLUE, ACCENT = (31, 78, 140), (255, 193, 7)

# le dessin, en unités de l'icône (256) : partagé par l'image et le SVG
BACKGROUND = (8, 8, 248, 248, 48)  # carré arrondi : x0, y0, x1, y1, rayon
I_STEM = ((50, 120), (50, 190), 28)  # le « i » : segment à bouts ronds, épaisseur
I_DOT = ((50, 76), 17)
SLASH = ((138, 62), (100, 200), 20)  # la « / »
WHEEL = ((182, 158), 42, 21)  # le « o » : centre, rayon extérieur, rayon intérieur


def draw() -> np.ndarray:
    n = SIZE * SS
    y, x = (np.mgrid[0:n, 0:n].astype(np.float32) + 0.5) / SS
    img = np.zeros((n, n, 4), np.float32)

    def blend(mask: np.ndarray, color: tuple[int, int, int]) -> None:
        img[mask] = (*color, 255)

    def rounded(x0, y0, x1, y1, r, color):
        cx, cy = np.clip(x, x0 + r, x1 - r), np.clip(y, y0 + r, y1 - r)
        blend((x - cx) ** 2 + (y - cy) ** 2 <= r**2, color)

    def segment(p, q, width, color):
        (px, py), (qx, qy) = p, q
        dx, dy = qx - px, qy - py
        t = np.clip(((x - px) * dx + (y - py) * dy) / (dx * dx + dy * dy), 0, 1)
        blend((x - (px + t * dx)) ** 2 + (y - (py + t * dy)) ** 2 <= (width / 2) ** 2, color)

    def ring(c, outer, inner, color):
        d = (x - c[0]) ** 2 + (y - c[1]) ** 2
        blend((d <= outer**2) & (d >= inner**2), color)

    rounded(*BACKGROUND, BLUE)
    segment(*SLASH, ACCENT)
    segment(*I_STEM, ACCENT)
    blend((x - I_DOT[0][0]) ** 2 + (y - I_DOT[0][1]) ** 2 <= I_DOT[1] ** 2, ACCENT)
    ring(*WHEEL, ACCENT)
    return img.reshape(SIZE, SS, SIZE, SS, 4).mean(axis=(1, 3)).astype(np.uint8)


def svg() -> str:
    def rgb(c: tuple[int, int, int]) -> str:
        return "#{:02x}{:02x}{:02x}".format(*c)

    def rect(x0, y0, x1, y1, r, color):
        return f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{r}" fill="{rgb(color)}"/>'

    def line(segment):
        (sx, sy), (ex, ey), width = segment
        return (
            f'<line x1="{sx}" y1="{sy}" x2="{ex}" y2="{ey}" stroke="{rgb(ACCENT)}" stroke-width="{width}" '
            f'stroke-linecap="round"/>'
        )

    (wx, wy), outer, inner = WHEEL
    parts = [
        rect(*BACKGROUND, BLUE),
        line(SLASH),
        line(I_STEM),
        f'<circle cx="{I_DOT[0][0]}" cy="{I_DOT[0][1]}" r="{I_DOT[1]}" fill="{rgb(ACCENT)}"/>',
        f'<circle cx="{wx}" cy="{wy}" r="{(outer + inner) / 2}" fill="none" stroke="{rgb(ACCENT)}" '
        f'stroke-width="{outer - inner}"/>',
    ]
    body = "\n  ".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" role="img" aria-label="Carriole">\n'
        f"  {body}\n</svg>\n"
    )


def resize(img: np.ndarray, size: int) -> np.ndarray:
    k = SIZE // size
    return img.reshape(size, k, size, k, 4).mean(axis=(1, 3)).astype(np.uint8)


def png(img: np.ndarray) -> bytes:
    h, w = img.shape[:2]
    raw = b"".join(b"\x00" + img[row].tobytes() for row in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def ico(images: dict[int, bytes]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs, offset = b"", b"", 6 + 16 * len(images)
    for size, data in images.items():
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


if __name__ == "__main__":
    out = Path(__file__).parent
    full = draw()
    (out / "icon.png").write_bytes(png(full))
    (out / "icon.ico").write_bytes(ico({s: png(resize(full, s)) for s in (256, 128, 64, 32, 16)}))
    public = out.parents[1] / "frontend" / "public"
    public.mkdir(exist_ok=True)
    (public / "logo.svg").write_text(svg(), encoding="utf-8")
    print("logo écrit dans", out, "et", public)
