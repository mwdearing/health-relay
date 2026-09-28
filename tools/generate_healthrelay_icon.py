#!/usr/bin/env python3
"""Generate the HealthRelay mark, app icon set and README lockup with Pillow only.

The mark is HealthRelay's own (not derived from the upstream Health Bridge assets):
a deep-teal rounded tile, a white ECG-style pulse line running left to right, and a
right-pointing chevron at the end of the line: data relayed onward.

Run from the repo root:  python3 tools/generate_healthrelay_icon.py
Requires Pillow only; no browser rendering.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
ICONSET = ROOT / "ios" / "HealthBridgeCompanion" / "App" / "Assets.xcassets" / "AppIcon.appiconset"

BG_TOP = (11, 61, 74)      # deep teal
BG_BOTTOM = (7, 34, 46)    # near-navy
LINE = (255, 255, 255)
ACCENT = (94, 234, 212)    # mint highlight for the chevron

# Pulse polyline in a 1000x1000 design space (x, y); baseline at y=520.
PULSE = [
    (110, 520), (300, 520), (360, 520), (410, 330), (470, 720),
    (530, 430), (570, 520), (700, 520),
]
CHEVRON = [(720, 400), (860, 520), (720, 640)]


def _tile(size: int) -> Image.Image:
    """Rounded tile with a vertical gradient, drawn at 4x and downsampled."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGBA", (s, s))
    px = grad.load()
    for y in range(s):
        t = y / max(1, s - 1)
        r = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        for x in range(s):
            px[x, y] = (r, g, b, 255)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.225), fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def _draw_mark(size: int, *, rounded: bool = True) -> Image.Image:
    s = size * 4
    img = _tile(size) if rounded else Image.new("RGBA", (s, s), BG_BOTTOM + (255,))
    d = ImageDraw.Draw(img)
    k = s / 1000.0
    w = max(2, round(44 * k))
    pts = [(x * k, y * k) for x, y in PULSE]
    d.line(pts, fill=LINE, width=w, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=LINE)
    cpts = [(x * k, y * k) for x, y in CHEVRON]
    d.line(cpts, fill=ACCENT, width=w, joint="curve")
    for x, y in (cpts[0], cpts[-1]):
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=ACCENT)
    return img.resize((size, size), Image.LANCZOS)


def write_iconset() -> int:
    contents = json.loads((ICONSET / "Contents.json").read_text())
    written = set()
    for entry in contents["images"]:
        pt = float(entry["size"].split("x")[0])
        scale = int(entry["scale"].rstrip("x"))
        px = round(pt * scale)
        name = entry["filename"]
        if name in written:
            continue
        # App Store / home-screen icons must be opaque squares; iOS applies the corner mask.
        _draw_mark(px, rounded=False).convert("RGB").save(ICONSET / name, "PNG")
        written.add(name)
    return len(written)


def write_brand() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    for n in (1024, 512, 180, 48, 32, 16):
        _draw_mark(n).save(BRAND / f"healthrelay-mark-{n}.png", "PNG")
    # Lockup: mark + wordmark drawn as simple text (default font keeps this dependency-free).
    mark = _draw_mark(160)
    lock = Image.new("RGBA", (720, 200), (0, 0, 0, 0))
    lock.paste(mark, (20, 20), mark)
    d = ImageDraw.Draw(lock)
    font = sub = None
    from PIL import ImageFont
    for bold, regular in (
        ("/usr/share/fonts/google-noto/NotoSans-Bold.ttf", "/usr/share/fonts/google-noto/NotoSans-Regular.ttf"),
        ("/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf", "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf"),
        ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"),
    ):
        try:
            font = ImageFont.truetype(bold, 72)
            sub = ImageFont.truetype(regular, 26)
            break
        except OSError:
            continue
    d.text((210, 52), "HealthRelay", fill=(17, 24, 39), font=font)
    d.text((214, 136), "Apple Health to your own receiver", fill=(75, 85, 99), font=sub)
    lock.save(BRAND / "healthrelay-lockup.png", "PNG")


if __name__ == "__main__":
    n = write_iconset()
    write_brand()
    print(f"icons written: {n}; brand assets in {BRAND.relative_to(ROOT)}")
