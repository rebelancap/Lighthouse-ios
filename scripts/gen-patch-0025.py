#!/usr/bin/env python3
"""Overlay patch 0025: size screen-pass attachments from the ACQUIRED drawable.

Flagship device finding (visionOS, window-resize crash):
SetupScreenFramebuffer receives the CALLER's cached width/height but binds
the freshly acquired drawable as the colour attachment. During a live
window resize the drawable's real size differs from those cached values, so
the depth texture — sized from the stale numbers — mismatches the colour
attachment and Metal aborts on render-pass validation. Device testing saw the
app restarting to the intro repeatedly while dragging the window corner.

The acquired drawable is the ground truth; derive width/height from its
texture. Benign on iPhone, where the drawable size never changes mid-run,
so this ships unconditionally on __IOS__ rather than behind a vision-only
guard (fewer build-configuration-dependent code paths to reason about).
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/backends/gfx_metal.cpp"
REL = "libultraship/src/fast/backends/gfx_metal.cpp"
orig = SRC.read_text()

OLD = """    tex.texture = mCurrentDrawable->texture();

    MTL::RenderPassDescriptor* render_pass_descriptor = MTL::RenderPassDescriptor::renderPassDescriptor();
"""
NEW = """    tex.texture = mCurrentDrawable->texture();

#ifdef __IOS__
    // LIGHTHOUSE_IOS (overlay 0025): during a live window resize (visionOS)
    // the acquired drawable's size can differ from the caller's cached w/h;
    // sizing the depth attachment from the stale values makes the render
    // pass attachments mismatch -> Metal validation abort. The drawable is
    // the ground truth.
    width = (uint32_t)tex.texture->width();
    height = (uint32_t)tex.texture->height();
#endif

    MTL::RenderPassDescriptor* render_pass_descriptor = MTL::RenderPassDescriptor::renderPassDescriptor();
"""
n = orig.count(OLD)
assert n == 1, f"[drawable-truth] expected 1 match, got {n}"
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
out = ROOT / "overlay/patches/0025-lighthouse-visionos-resize-drawable-truth.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
