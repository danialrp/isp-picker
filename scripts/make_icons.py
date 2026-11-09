#!/usr/bin/env python3
"""Draw the static extension icons (blue circle with two opposite arrows) as PNGs, no dependencies."""
import struct, zlib, sys
from pathlib import Path

def png(path, size, rgb):
    ss = 4
    def inside(x, y):
        s = size; r = s / 2
        if (x - r) ** 2 + (y - r) ** 2 > (r - 0.5) ** 2: return None
        w = max(1.5, s / 11) / 2; h = s * 0.13
        for yy, d in ((s * 0.37, 1), (s * 0.63, -1)):
            x0, x1 = s * 0.27, s * 0.73
            if x0 <= x <= x1 and abs(y - yy) <= w: return "w"
            tip = x1 + s * 0.04 if d > 0 else x0 - s * 0.04
            base = tip - d * h * 1.3
            t = (x - base) / (tip - base)
            if 0 <= t <= 1 and abs(y - yy) <= h * (1 - t): return "w"
        return "c"
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    k = inside(px + (sx + .5) / ss, py + (sy + .5) / ss)
                    if k:
                        c = (255, 255, 255) if k == "w" else rgb
                        acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]; acc[3] += 255
            n = ss * ss; a = acc[3] / 255
            row += bytes([int(acc[0] / a) if a else 0, int(acc[1] / a) if a else 0, int(acc[2] / a) if a else 0, acc[3] // n])
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)
    chunk = lambda t, d: struct.pack("!I", len(d)) + t + d + struct.pack("!I", zlib.crc32(t + d) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b""))

# Static icons use the "off" color so the toolbar icon looks the same before the extension sets it.
COLOR = tuple(int((sys.argv[2] if len(sys.argv) > 2 else "2563eb")[i:i + 2], 16) for i in (0, 2, 4))
out = Path(sys.argv[1] if len(sys.argv) > 1 else "extension/icons")
out.mkdir(parents=True, exist_ok=True)
for s in (16, 32, 48, 128):
    png(out / f"{s}.png", s, COLOR)
