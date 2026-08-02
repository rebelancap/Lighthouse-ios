#!/usr/bin/env python3
"""Overlay patch 0012: wire the iOS app shell (app/ios/SohIosShell.m) into the
soh target and have LUS call it right after SDL creates its window.
The shell lives in the Shipwright-ios repo (not vendor); its dir is passed as
-DSOH_IOS_SHELL_DIR. It grafts SDL's UIWindow onto the active UIWindowScene,
forces landscape (iOS 26 sceneless-window fix), and will host touch controls.
Generated against the post-overlay vendor tree (0004 etc already applied)."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/Lighthouse"

def replace_once(text, old, new, tag):
    n = text.count(old)
    assert n == 1, f"[{tag}] expected 1 match, got {n}"
    return text.replace(old, new)

def unified(a, b, rel):
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(a); fb.write(b); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"no diff for {rel}"
    return r.stdout.decode()

# NOTE: the shell's CMake wiring lives in patch 0004 (same concern: the iOS
# app target). This patch only hooks the window-creation call in LUS —
# keeping each patch's hunks out of every other patch's context so the
# independent forward/reverse idempotence probes stay valid.

# --- gfx_sdl2.cpp: call the shell hook right after SDL_CreateWindow
sdl = VENDOR / "libultraship/src/fast/backends/gfx_sdl2.cpp"
s0 = sdl.read_text()
# File-scope extern "C" decl (block scope is illegal) just inside namespace Fast.
s1 = replace_once(s0,
    "namespace Fast {\n",
    "namespace Fast {\n"
    "#ifdef __IOS__\n"
    "// iOS app shell hook (overlay 0012), implemented in app/ios/SohIosShell.m.\n"
    "extern \"C\" void SohIos_OnWindowCreated(struct SDL_Window*);\n"
    "#endif\n",
    "shell-decl")
s1 = replace_once(s1,
    "    mWnd = SDL_CreateWindow(title, posX, posY, mWindowWidth, mWindowHeight, flags);\n",
    "    mWnd = SDL_CreateWindow(title, posX, posY, mWindowWidth, mWindowHeight, flags);\n"
    "#ifdef __IOS__\n"
    "    // Graft onto the active UIWindowScene, force landscape, host touch controls.\n"
    "    SohIos_OnWindowCreated(mWnd);\n"
    "#endif\n",
    "shell-hook")

out = ROOT / "overlay/patches/0012-soh-ios-app-shell-hook.patch"
out.write_text(__doc__ + "\n\n"
    + unified(s0, s1, "libultraship/src/fast/backends/gfx_sdl2.cpp"))
print(f"wrote {out}")
