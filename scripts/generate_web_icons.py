"""Generate the web client's app icons.

The icon is generated rather than committed as an opaque binary, so anyone
reading this repository can see exactly what is in it and regenerate it.

Pure standard library: the mark is rasterised with 4x supersampling and written
as PNG with zlib. Run from the repository root:

    python scripts/generate_web_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "apps" / "web" / "icons"
SIZES = (180, 192, 512)
SUPERSAMPLE = 4

#: Deep blue to a lighter blue, matching the sign-in mark in the client.
GRADIENT_TOP = (11, 59, 111)
GRADIENT_BOTTOM = (27, 127, 208)
INK = (255, 255, 255)

#: The mark is designed on a 64 unit square and inset so that it survives the
#: circular safe zone a maskable icon may be cropped to.
DESIGN = 64.0
CONTENT_INSET = 0.14

STROKE = 5.0
ROOF = ((12.0, 27.0), (32.0, 11.0), (52.0, 27.0))
WAVES = (
    {"baseline": 40.0, "amplitude": 4.2, "opacity": 1.0},
    {"baseline": 52.0, "amplitude": 4.2, "opacity": 0.45},
)
WAVE_START = 7.0
WAVE_END = 57.0
WAVE_LENGTH = 25.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_squared))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _wave_y(x: float, baseline: float, amplitude: float) -> float:
    return baseline + amplitude * math.sin(2.0 * math.pi * (x - WAVE_START) / WAVE_LENGTH)


def _wave_distance(px: float, py: float, baseline: float, amplitude: float) -> float:
    if px < WAVE_START or px > WAVE_END:
        # Round the cap ends rather than cutting them off square.
        end = WAVE_START if px < WAVE_START else WAVE_END
        return math.hypot(px - end, py - _wave_y(end, baseline, amplitude))
    slope = (
        amplitude
        * (2.0 * math.pi / WAVE_LENGTH)
        * math.cos(2.0 * math.pi * (px - WAVE_START) / WAVE_LENGTH)
    )
    return abs(py - _wave_y(px, baseline, amplitude)) / math.hypot(1.0, slope)


def _render(size: int) -> bytes:
    hi = size * SUPERSAMPLE
    pixels = bytearray(hi * hi * 3)

    # Background gradient, one flat colour per row.
    for y in range(hi):
        t = y / (hi - 1)
        row = (
            bytes(
                (
                    round(_lerp(GRADIENT_TOP[0], GRADIENT_BOTTOM[0], t)),
                    round(_lerp(GRADIENT_TOP[1], GRADIENT_BOTTOM[1], t)),
                    round(_lerp(GRADIENT_TOP[2], GRADIENT_BOTTOM[2], t)),
                )
            )
            * hi
        )
        pixels[y * hi * 3 : (y + 1) * hi * 3] = row

    # Map design units onto the inset content box.
    content = hi * (1.0 - 2.0 * CONTENT_INSET)
    origin = hi * CONTENT_INSET
    scale = content / DESIGN
    half_stroke = STROKE / 2.0

    def paint(x0: float, y0: float, x1: float, y1: float, distance, opacity: float) -> None:
        # Widen the search box by the stroke radius, otherwise the round caps
        # get clipped into square ends.
        px_from = max(0, int(origin + (x0 - half_stroke) * scale) - 2)
        px_to = min(hi, int(origin + (x1 + half_stroke) * scale) + 3)
        py_from = max(0, int(origin + (y0 - half_stroke) * scale) - 2)
        py_to = min(hi, int(origin + (y1 + half_stroke) * scale) + 3)
        for py in range(py_from, py_to):
            design_y = (py + 0.5 - origin) / scale
            base = py * hi * 3
            for px in range(px_from, px_to):
                design_x = (px + 0.5 - origin) / scale
                if distance(design_x, design_y) > half_stroke:
                    continue
                index = base + px * 3
                if opacity >= 1.0:
                    pixels[index] = INK[0]
                    pixels[index + 1] = INK[1]
                    pixels[index + 2] = INK[2]
                else:
                    for channel in range(3):
                        current = pixels[index + channel]
                        pixels[index + channel] = round(_lerp(current, INK[channel], opacity))

    def roof_distance(x: float, y: float) -> float:
        return min(
            _distance_to_segment(x, y, *ROOF[0], *ROOF[1]),
            _distance_to_segment(x, y, *ROOF[1], *ROOF[2]),
        )

    paint(ROOF[0][0], ROOF[1][1], ROOF[2][0], ROOF[0][1], roof_distance, 1.0)

    for wave in WAVES:
        baseline = wave["baseline"]
        amplitude = wave["amplitude"]
        paint(
            WAVE_START,
            baseline - amplitude,
            WAVE_END,
            baseline + amplitude,
            lambda x, y, b=baseline, a=amplitude: _wave_distance(x, y, b, a),
            wave["opacity"],
        )

    return _downsample(pixels, hi, size)


def _downsample(pixels: bytearray, hi: int, size: int) -> bytes:
    out = bytearray(size * size * 3)
    samples = SUPERSAMPLE * SUPERSAMPLE
    for y in range(size):
        for x in range(size):
            totals = [0, 0, 0]
            for sy in range(SUPERSAMPLE):
                base = ((y * SUPERSAMPLE + sy) * hi + x * SUPERSAMPLE) * 3
                for sx in range(SUPERSAMPLE):
                    offset = base + sx * 3
                    totals[0] += pixels[offset]
                    totals[1] += pixels[offset + 1]
                    totals[2] += pixels[offset + 2]
            index = (y * size + x) * 3
            out[index] = totals[0] // samples
            out[index + 1] = totals[1] // samples
            out[index + 2] = totals[2] // samples
    return bytes(out)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _write_png(path: Path, size: int, rgb: bytes) -> None:
    stride = size * 3
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        target = OUTPUT_DIR / f"icon-{size}.png"
        _write_png(target, size, _render(size))
        print(f"{target.relative_to(OUTPUT_DIR.parents[2])} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
