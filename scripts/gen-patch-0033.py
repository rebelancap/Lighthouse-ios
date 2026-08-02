#!/usr/bin/env python3
"""Overlay patch 0033: skybox stereo sentinels (Banjo-Kazooie's sky.c).

BK's sky is drawn CENTRED ON THE CAMERA — `sky_draw` literally does
`viewport_getPosition_vec3f(position)` and then `modelRender_draw(..., position,
...)` for each of the three sky layers. So the per-eye camera shift CANCELS for
it: the sky lands at zero disparity, i.e. exactly on the panel plane, while the
terrain recedes behind it. Occlusion says "farthest", stereo says "nearest", and
the brain refuses. The flagship hit this on device and the sm64coopdx port hit
it before that; both describe it the same way, as disorienting clouds.

The fix is infinity disparity for skybox draws — skew only, no eye translation —
which requires the interpreter to KNOW which triangles are sky. Bracket the sky
model emission with G_NOOP sentinel tags; overlay 0031's noop handler flips a
flag between them, and clears it defensively at every Run start.

Opcode check, done rather than assumed: this tree builds with `F3DEX_GBI=1`
(root CMakeLists), NOT F3DEX_GBI_2, so gbi.h's `#else` branch applies and
`G_NOOP` is 0xc0. LUS maps `F3DEX_G_NOOP = OPCODE(0xc0)` to
`gfx_noop_handler_f3dex2` (interpreter.cpp), which is the handler 0031 patches.
Tags 0x5A / 0x5B sit in w0 bits 16-23, where the handler's `C0(16, 8)` reads
them; 7 and 8 are already taken by LUS's own disp-stack tags.

Note the sentinels bracket only the MODEL draws, not the fallback
`drawRectangle2D` backdrop — that one is a 2D orthographic pass, which 0031's
perspective test already leaves alone (and therefore already sits correctly on
the panel plane).

Two extra display-list commands, `__IOS__`-gated. Desktop and macOS-oracle
skybox output is byte-identical to upstream.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/core2/gc/sky.c"
REL = "src/core2/gc/sky.c"
orig = SRC.read_text()

OLD_HEAD = """        viewport_getPosition_vec3f(position);
        for (i = 0; i < 3; i++) {
"""
NEW_HEAD = """        viewport_getPosition_vec3f(position);
#ifdef __IOS__
        /* LIGHTHOUSE_IOS (overlay 0033): stereo skybox BEGIN sentinel. */
        {
            Gfx *lhTag = (*gfx)++;
            lhTag->words.w0 = (uintptr_t)((G_NOOP << 24) | (0x5A << 16));
            lhTag->words.w1 = 0;
        }
#endif
        for (i = 0; i < 3; i++) {
"""

OLD_TAIL = """                modelRender_draw(gfx, mtx, position, rotation, gcSky.sky_info->sky_list[i].scale, NULL, sky_model_bin);
            }
        }
    } else {
"""
NEW_TAIL = """                modelRender_draw(gfx, mtx, position, rotation, gcSky.sky_info->sky_list[i].scale, NULL, sky_model_bin);
            }
        }
#ifdef __IOS__
        /* LIGHTHOUSE_IOS (overlay 0033): stereo skybox END sentinel. */
        {
            Gfx *lhTag = (*gfx)++;
            lhTag->words.w0 = (uintptr_t)((G_NOOP << 24) | (0x5B << 16));
            lhTag->words.w1 = 0;
        }
#endif
    } else {
"""

t = orig
for tag, old, new in (("head", OLD_HEAD, NEW_HEAD), ("tail", OLD_TAIL, NEW_TAIL)):
    n = t.count(old)
    assert n == 1, f"[{tag}] expected 1 match, got {n}"
    t = t.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig)
    fb.write(t)
    fa.flush()
    fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0033-lighthouse-skybox-stereo-markers.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
