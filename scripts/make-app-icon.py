#!/usr/bin/env python3
"""
make-app-icon.py — build app/ios/Assets.xcassets/AppIcon.appiconset from
upstream's logo.

Why this exists: without an icon the app shows a blank grey placeholder on the
springboard (visible in artifacts/sim/boot-ladder3.png). Upstream ships
`vendor/Lighthouse/logo.png` — a 256x256 RGBA lighthouse character, 67 %
transparent — which cannot be used directly: iOS icons must be **opaque**,
**square** and **filled edge-to-edge** (the OS applies the rounded mask itself,
so any transparency shows as black corners).

Design decisions, deliberately conservative:
* **Night-sky vertical gradient** behind the character. The tower is red/white
  with grey stone; a deep navy separates all three without competing, and reads
  as "lighthouse" instantly at 60 pt.
* **No light beam, no text.** Both are the usual temptations and both turn to
  mud at the smallest size, which is the size that matters most.
* Character occupies ~74 % of the canvas height and sits slightly low, so the
  bulbous lamp housing has room and the silhouette stays centred after the
  corner mask.
* Upscaled with LANCZOS from 256 → ~760 px. That is a ~3x enlargement and will
  be a little soft under close inspection; it is the best source upstream
  provides, and it is dramatically better than no icon. Swap in a larger source
  here if one ever appears.

Regenerate with: python3 scripts/make-app-icon.py
"""
import json
import pathlib
import sys

from PIL import Image, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/logo.png"
OUT = ROOT / "app/ios/Assets.xcassets/AppIcon.appiconset"

# (filename size, idiom, size string, scale) — the set Xcode/actool expects.
ICONS = [
    ("icon-120.png", 120, "iphone", "60x60", "2x"),
    ("icon-180.png", 180, "iphone", "60x60", "3x"),
    ("icon-152.png", 152, "ipad", "76x76", "2x"),
    ("icon-167.png", 167, "ipad", "83.5x83.5", "2x"),
    ("icon-1024.png", 1024, "ios-marketing", "1024x1024", "1x"),
]

TOP = (14, 24, 54)      # deep navy, top of frame
BOTTOM = (46, 74, 116)  # lifted horizon blue


def build_master(size: int = 1024) -> Image.Image:
    if not SRC.is_file():
        print(f"FATAL: {SRC} not found (is vendor/ checked out?)", file=sys.stderr)
        raise SystemExit(2)

    # Opaque vertical gradient background.
    column = Image.new("RGB", (1, size))
    px = column.load()
    for y in range(size):
        t = y / (size - 1)
        px[0, y] = (
            round(TOP[0] + (BOTTOM[0] - TOP[0]) * t),
            round(TOP[1] + (BOTTOM[1] - TOP[1]) * t),
            round(TOP[2] + (BOTTOM[2] - TOP[2]) * t),
        )
    bg = column.resize((size, size))  # stretch the 1-px column across the width

    logo = Image.open(SRC).convert("RGBA")
    # Trim to the character's actual bounds so framing is driven by the art,
    # not by the transparent padding around it.
    bbox = logo.getbbox()
    if bbox:
        logo = logo.crop(bbox)

    target_h = int(size * 0.74)
    scale = target_h / logo.height
    logo = logo.resize((max(1, int(logo.width * scale)), target_h), Image.LANCZOS)

    x = (size - logo.width) // 2
    y = int(size * 0.15)

    # Soft drop shadow so the pale stone reads against the navy.
    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    shadow.paste(logo, (x, y + int(size * 0.012)), logo)
    shadow = shadow.filter(ImageFilter.GaussianBlur(size * 0.018))
    bg = Image.alpha_composite(bg.convert("RGBA"), shadow)

    bg.paste(logo, (x, y), logo)
    return bg.convert("RGB")  # RGB, not RGBA — iOS rejects alpha in app icons


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    master = build_master(1024)

    for name, px_size, _idiom, _size, _scale in ICONS:
        img = master if px_size == 1024 else master.resize((px_size, px_size), Image.LANCZOS)
        path = OUT / name
        img.save(path, "PNG")
        assert img.mode == "RGB", "app icons must not carry an alpha channel"
        print(f"  {name:16} {px_size}x{px_size}")

    contents = {
        "images": [
            {"idiom": idiom, "size": size, "scale": scale, "filename": name}
            for name, _px, idiom, size, scale in ICONS
        ],
        "info": {"version": 1, "author": "xcode"},
    }
    (OUT / "Contents.json").write_text(json.dumps(contents, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
