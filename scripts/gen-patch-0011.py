#!/usr/bin/env python3
"""Overlay patch 0011: lock iOS to landscape.
SoH's drawable is landscape, but the app doesn't force the device orientation,
so on a portrait device/simulator the game renders sideways in a portrait
window. Set SDL_HINT_ORIENTATIONS to landscape before SDL_Init so SDL's iOS
view controller reports landscape-only and iOS rotates the UI to match. The
Info.plist already restricts to landscape; this makes SDL agree."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/backends/gfx_sdl2.cpp"
REL = "libultraship/src/fast/backends/gfx_sdl2.cpp"
orig = SRC.read_text()

old = "    SDL_Init(SDL_INIT_VIDEO);\n"
new = ("#ifdef __IOS__\n"
       "    // Force landscape so iOS rotates the device to match SoH's landscape\n"
       "    // drawable (otherwise the game renders sideways in a portrait window).\n"
       "    SDL_SetHint(SDL_HINT_ORIENTATIONS, \"LandscapeLeft LandscapeRight\");\n"
       "#endif\n"
       "    SDL_Init(SDL_INIT_VIDEO);\n")
n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
t = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0011-lus-ios-force-landscape.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
