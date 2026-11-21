# SPDX-License-Identifier: GPL-3.0-or-later

"""Render Wible's UI icons to wible/icons/*.png.

Standard library only. Each icon is a few anti-aliased strokes drawn from
distance functions, in the same pastel palette as Blaya's badges.
Run: python tools/make_icons.py
"""

import math
import pathlib
import struct
import zlib

SIZE = 32
SAMPLES = 4  # supersampling per axis
OUT = pathlib.Path(__file__).resolve().parents[1] / "wible" / "icons"

SKY = (0x7F, 0xD3, 0xFF)
PINK = (0xFF, 0x8F, 0xA3)
LILAC = (0xC4, 0xA7, 0xFF)
AMBER = (0xFF, 0xC3, 0x6B)
MINT = (0x6F, 0xD6, 0xA0)


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


def polyline(points, width):
    segs = list(zip(points, points[1:]))

    def dist(x, y):
        return min(_seg_dist(x, y, *a, *b) for a, b in segs) - width / 2

    return dist


def circle(cx, cy, r, width=None):
    def dist(x, y):
        d = math.hypot(x - cx, y - cy) - r
        return abs(d) - width / 2 if width else d

    return dist


def union(*shapes):
    return lambda x, y: min(s(x, y) for s in shapes)


def catenary(x0, x1, y_top, sag, steps=24):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (x1 - x0) * t
        u = (t - 0.5) * 2.0
        y = y_top + sag * (1.0 - (math.cosh(u * 1.3) - 1.0) / (math.cosh(1.3) - 1.0))
        pts.append((x, y))
    return pts


# Each glyph is scaled and centered to fill the icon, leaving this margin (px).
MARGIN = 1.0


def _bounds(shape, step=0.25):
    xs, ys = [], []
    v = -4.0
    grid = []
    while v <= SIZE + 4.0:
        grid.append(v)
        v += step
    for y in grid:
        for x in grid:
            if shape(x, y) <= 0.0:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def render(shape, color):
    x0, y0, x1, y1 = _bounds(shape)
    scale = (SIZE - 2 * MARGIN) / max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    c = SIZE / 2.0
    rows = []
    n = SAMPLES
    for py in range(SIZE):
        row = bytearray([0])  # PNG filter byte
        for px in range(SIZE):
            cover = 0
            for sy in range(n):
                for sx in range(n):
                    x = cx + (px + (sx + 0.5) / n - c) / scale
                    y = cy + (py + (sy + 0.5) / n - c) / scale
                    if shape(x, y) <= 0.0:
                        cover += 1
            alpha = round(255 * cover / (n * n))
            row += bytes((*color, alpha))
        rows.append(bytes(row))
    return b"".join(rows)


def write_png(path, raw):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def icons():
    hang = catenary(5, 27, 9, 14)
    yield "wible", SKY, union(polyline(hang, 3.0), circle(5, 9, 3.2), circle(27, 9, 3.2))
    yield "draw", SKY, union(
        polyline([(7, 25), (22, 10)], 5.0),
        polyline([(22, 10), (25, 7)], 3.0),
        polyline([(4, 28), (7, 25)], 2.0),
    )
    yield "shape", PINK, union(
        polyline(catenary(4, 28, 6, 12), 3.0),
        polyline([(16, 21), (16, 29)], 2.6),
        polyline([(12.5, 25.5), (16, 29), (19.5, 25.5)], 2.6),
    )
    yield "physics", LILAC, polyline(
        [(3 + i * 26 / 32, 16 + 9 * math.sin(i / 32 * 2 * math.pi * 1.5) * (1 - i / 40)) for i in range(33)], 3.0
    )
    yield "geometry", AMBER, union(circle(16, 16, 10.5, width=3.2), circle(16, 16, 3.5))
    grid = []
    for k in (6, 16, 26):
        grid.append(polyline([(k, 5), (k, 27)], 2.4))
        grid.append(polyline([(5, k), (27, k)], 2.4))
    yield "mesh", MINT, union(*grid)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, color, shape in icons():
        write_png(OUT / f"{name}.png", render(shape, color))
        print("wrote", OUT / f"{name}.png")


if __name__ == "__main__":
    main()
