#!/usr/bin/env python3
"""
make-vision-icon.py — build the visionOS LAYERED app icon
(app/ios/Assets.xcassets/AppIcon.solidimagestack).

visionOS icons are not flat images. The system renders each layer at a
different depth and parallaxes them as your gaze moves, so the subject
physically lifts off its background. An `AppIcon.appiconset` alone gets a flat,
lifeless tile — which is what this port shipped until now, while every sibling
(Shipwright, 2ship, Starship, SpaghettiKart) already had a solidimagestack.

Layer split, and why this port's art makes it easy:
* **Back** — the night-sky gradient alone, opaque. Same gradient the iOS icon
  uses, so the two icons are recognisably the same artwork.
* **Front** — the lighthouse character alone on full transparency. Upstream's
  `logo.png` is already a transparent-background RGBA cutout, so there is no
  matting to fake; the subject separates cleanly by construction.

Deliberately TWO layers, not three. Apple allows up to three, but a middle
layer only earns its place when there is genuine mid-ground art. Inventing one
(a duplicated glow, a drop shadow) reads as a smeared halo at the shallow
parallax angles the system actually uses.

Geometry notes:
* 1024x1024 at `"scale": "2x"`, matching the siblings' stacks exactly.
* visionOS applies a CIRCULAR mask and the layers move relative to each other,
  so the subject is scaled to ~62% of the canvas (vs ~74% for the iOS square
  icon) and centred: at full parallax a larger subject clips against the
  circle.
* Back is written as RGB (no alpha) because the bottom layer must be opaque;
  Front keeps its alpha.

Regenerate with: python3 scripts/make-vision-icon.py
"""
import json
import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/logo.png"
OUT = ROOT / "app/ios/Assets.xcassets/AppIcon.solidimagestack"

CANVAS = 1024
SUBJECT_FRACTION = 0.62  # of canvas height; smaller than iOS — circular mask + parallax

# Same night sky as the iOS icon (scripts/make-app-icon.py) so the two read as
# one piece of artwork rather than two.
SKY_TOP = (10, 16, 42)
SKY_BOTTOM = (34, 52, 96)


def info():
    return {"author": "xcode", "version": 1}


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def build_back():
    img = Image.new("RGB", (CANVAS, CANVAS))
    px = img.load()
    for y in range(CANVAS):
        t = y / (CANVAS - 1)
        row = (
            round(SKY_TOP[0] + (SKY_BOTTOM[0] - SKY_TOP[0]) * t),
            round(SKY_TOP[1] + (SKY_BOTTOM[1] - SKY_TOP[1]) * t),
            round(SKY_TOP[2] + (SKY_BOTTOM[2] - SKY_TOP[2]) * t),
        )
        for x in range(CANVAS):
            px[x, y] = row
    return img


def build_front(src):
    # Trim to the subject's own bounding box first: logo.png is mostly empty,
    # and scaling the padded image would leave the lighthouse tiny inside the
    # circular mask.
    bbox = src.getbbox()
    if bbox is None:
        raise SystemExit("FATAL: source logo is fully transparent")
    subject = src.crop(bbox)

    target_h = round(CANVAS * SUBJECT_FRACTION)
    scale = target_h / subject.height
    target_w = max(1, round(subject.width * scale))
    subject = subject.resize((target_w, target_h), Image.LANCZOS)

    front = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    # Centred horizontally; nudged slightly low so the lamp housing has room,
    # matching the iOS icon's composition.
    x = (CANVAS - target_w) // 2
    y = (CANVAS - target_h) // 2 + round(CANVAS * 0.02)
    front.paste(subject, (x, y), subject)
    return front


def main():
    if not SRC.is_file():
        print(f"FATAL: {SRC} not found — run scripts/bootstrap.sh", file=sys.stderr)
        return 2
    src = Image.open(SRC).convert("RGBA")

    write_json(OUT / "Contents.json", {
        "info": info(),
        "layers": [
            {"filename": "Front.solidimagestacklayer"},
            {"filename": "Back.solidimagestacklayer"},
        ],
    })

    for name, img, fname in (
        ("Front", build_front(src), "front.png"),
        ("Back", build_back(), "back.png"),
    ):
        layer = OUT / f"{name}.solidimagestacklayer"
        write_json(layer / "Contents.json", {"info": info()})
        content = layer / "Content.imageset"
        write_json(content / "Contents.json", {
            "images": [{"filename": fname, "idiom": "vision", "scale": "2x"}],
            "info": info(),
        })
        img.save(content / fname)
        print(f"  {name:5s} {img.size} {img.mode} -> {content / fname}")

    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
