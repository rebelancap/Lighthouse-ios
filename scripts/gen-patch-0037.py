#!/usr/bin/env python3
"""Overlay patch 0037: keep SDL_main named SDL_main on iOS.

NUMBERING: 0001-0036 mirror the flagship's patch numbers (same intent, same
slot). 0037+ are Lighthouse-only patches with no flagship equivalent, so a
reader can tell at a glance which are inherited and which are ours.

src/port/Game.cpp does:

    /* Rename SDL_main to main for SDL compatibility */
    #ifdef __GNUC__
    #define SDL_main main
    #endif

    int SDL_main(int argc, char* argv[]) {

On desktop clang/gcc that turns the entry point into a plain main(). On iOS
that is exactly wrong: SDL2main's UIKit shim supplies the real main(), which
calls UIApplicationMain and then dispatches to SDL_main on the main thread once
UIKit is up. With the rename in place the game defines its own main(), which
collides with SDL2main's at link — and if it won the link, UIKit would never be
initialised at all.

This is the mirror image of the flagship's approach: SoH's entry is a plain
main() and its patch adds `main=SDL_main`; Lighthouse already renames toward
main, so we suppress the rename instead. Same destination, opposite direction —
worth stating because copying SoH's `set_source_files_properties(... main=SDL_main)`
verbatim here would double-rename and silently produce a binary with no
SDL_main at all.

Guarded on __IOS__, which the app target defines for the game's TUs (patch
0004); inert for every desktop build."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/port/Game.cpp"
REL = "src/port/Game.cpp"
orig = SRC.read_text()

old = ("/* Rename SDL_main to main for SDL compatibility */\n"
       "#ifdef __GNUC__\n"
       "#define SDL_main main\n"
       "#endif\n")
new = ("/* Rename SDL_main to main for SDL compatibility */\n"
       "// LIGHTHOUSE_IOS: never on iOS. SDL2main's UIKit shim owns the real main()\n"
       "// (main -> UIApplicationMain -> SDL_main on the main thread once UIKit is\n"
       "// live), so SDL_main must keep its name here; renaming it collides with the\n"
       "// shim's main() at link, and winning that link would skip UIKit init entirely.\n"
       "#if defined(__GNUC__) && !defined(__IOS__)\n"
       "#define SDL_main main\n"
       "#endif\n")

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
t = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True, text=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0037-lighthouse-sdl-main-on-ios.patch"
out.write_text(__doc__ + "\n\n" + r.stdout)
print(f"wrote {out}")
