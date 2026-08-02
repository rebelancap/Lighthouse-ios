#!/usr/bin/env python3
"""One-shot generator for overlay patches 0001/0002 (LUS gfx_sdl2 iOS fixes).
Edits are match-count asserted; output is a unified diff against the pristine
file, paths vendor-relative. Vendor tree is untouched."""
import subprocess, sys, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/backends/gfx_sdl2.cpp"
REL = "libultraship/src/fast/backends/gfx_sdl2.cpp"
orig = SRC.read_text()

def replace_once(text, old, new):
    n = text.count(old)
    assert n == 1, f"expected exactly 1 match, got {n}: {old[:70]!r}"
    return text.replace(old, new)

# --- 0001: macUtils (Cocoa) calls must not compile on iOS (link blocker) ---
p1 = orig
p1 = replace_once(
    p1,
    "#if defined(__APPLE__)\n    // Implement fullscreening with native macOS APIs\n    if (on != isNativeMacOSFullscreenActive(mWnd)) {",
    "#if defined(__APPLE__) && !defined(__IOS__)\n    // Implement fullscreening with native macOS APIs\n    if (on != isNativeMacOSFullscreenActive(mWnd)) {",
)
p1 = replace_once(
    p1,
    "    // resync fullscreen state\n#ifdef __APPLE__\n    auto nextFullscreenState = isNativeMacOSFullscreenActive(mWnd);",
    "    // resync fullscreen state\n#if defined(__APPLE__) && !defined(__IOS__)\n    auto nextFullscreenState = isNativeMacOSFullscreenActive(mWnd);",
)

# --- 0002: iOS window needs ALLOW_HIGHDPI for a native-scale CAMetalLayer ---
p2 = p1
p2 = replace_once(
    p2,
    "#ifdef __IOS__\n    Uint32 flags = SDL_WINDOW_BORDERLESS | SDL_WINDOW_SHOWN;\n#else",
    "#ifdef __IOS__\n    Uint32 flags = SDL_WINDOW_BORDERLESS | SDL_WINDOW_SHOWN | SDL_WINDOW_ALLOW_HIGHDPI;\n#else",
)

def make_patch(a_text, b_text, outname, header):
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(a_text); fb.write(b_text)
        fa.flush(); fb.flush()
        r = subprocess.run(
            ["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}", fa.name, fb.name],
            capture_output=True, text=True)
    assert r.returncode == 1, f"diff produced no changes for {outname}"
    out = ROOT / "overlay/patches" / outname
    out.write_text(header + r.stdout)
    print(f"wrote {out}")

make_patch(orig, p1, "0001-lus-gfx-sdl2-gate-macutils-off-ios.patch",
"""Gate the two Cocoa macUtils fullscreen call sites off iOS. macUtils.mm is
(correctly) not compiled for iOS (src/ship/CMakeLists.txt:66-68), so these
__APPLE__ blocks are undefined-symbol link errors on device. iOS is
force-fullscreen anyway (Fast3dWindow.cpp:77); the #else SDL path is fine.

""")
make_patch(p1, p2, "0002-lus-gfx-sdl2-ios-highdpi-window.patch",
"""Add SDL_WINDOW_ALLOW_HIGHDPI to the iOS window flags so SDL sets the
CAMetalLayer contentsScale/drawableSize to native device pixels (without it
the layer is 1x — blurry). Framebuffer sizing already flows from
SDL_GetRendererOutputSize.

""")
print("OK")
