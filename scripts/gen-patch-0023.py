#!/usr/bin/env python3
"""Overlay patch 0023: visionOS is Metal-only — no ENABLE_OPENGL.

visionOS ships neither desktop GL (glew) nor the GLES frameworks, so LUS's
OpenGL comparator backend cannot compile there. Upstream already guards the
whole backend (gfx_opengl.cpp's body and the Fast3dWindow instantiation)
behind ENABLE_OPENGL — so this patch only has to stop DEFINING it when
LIGHTHOUSE_VISIONOS is set (overlay 0022 sets that variable). The iPhone
build, where the GLES flavour survives as a debugging comparator, is
unchanged: the genex evaluates to ENABLE_OPENGL exactly as before.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/CMakeLists.txt"
REL = "libultraship/src/CMakeLists.txt"
orig = SRC.read_text()

OLD = """    target_compile_definitions(libultraship PRIVATE
        ENABLE_OPENGL
        $<$<CONFIG:Debug>:_DEBUG>
"""
NEW = """    target_compile_definitions(libultraship PRIVATE
        # LIGHTHOUSE_IOS (overlay 0023): no GL of any kind on visionOS —
        # Metal only. LIGHTHOUSE_VISIONOS comes from overlay 0022.
        $<$<NOT:$<BOOL:${LIGHTHOUSE_VISIONOS}>>:ENABLE_OPENGL>
        $<$<CONFIG:Debug>:_DEBUG>
"""
n = orig.count(OLD)
assert n == 1, f"[enable-opengl] expected 1 match, got {n}"
t = orig.replace(OLD, NEW)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig)
    fb.write(t)
    fa.flush()
    fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0023-lighthouse-visionos-no-opengl.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
