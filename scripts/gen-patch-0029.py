#!/usr/bin/env python3
"""Overlay patch 0029: mip filtering in Fast3D Metal samplers.
Companion to 0019 rev3 (mip chains for large HD/4K textures): without a
sampler mip filter the chains are never read. Linear-filtered textures
get trilinear + 4x anisotropy; the N64 three-point/nearest path keeps
nearest-mip so the authentic look is preserved. Samplers are created
before the texture uploads, so this applies unconditionally — sampling a
single-level texture with a mip filter is well-defined (level 0)."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/backends/gfx_metal.cpp"
REL = "libultraship/src/fast/backends/gfx_metal.cpp"
orig = SRC.read_text()

OLD = """    sampler_descriptor->setMinFilter(filter);
    sampler_descriptor->setMagFilter(filter);
    sampler_descriptor->setSAddressMode(gfx_cm_to_metal(cms));
"""
NEW = """    sampler_descriptor->setMinFilter(filter);
    sampler_descriptor->setMagFilter(filter);
    // SOH_IOS (overlay 0029): read the 0019-rev3 mip chains — trilinear +
    // modest anisotropy on the linear path; nearest-mip preserves the
    // three-point/nearest N64 look. Single-level textures sample level 0.
    if (filter == MTL::SamplerMinMagFilterLinear) {
        sampler_descriptor->setMipFilter(MTL::SamplerMipFilterLinear);
        sampler_descriptor->setMaxAnisotropy(4);
    } else {
        sampler_descriptor->setMipFilter(MTL::SamplerMipFilterNearest);
    }
    sampler_descriptor->setSAddressMode(gfx_cm_to_metal(cms));
"""
n = orig.count(OLD)
assert n == 1, f"[sampler-mip] expected 1 match, got {n}"
t = orig.replace(OLD, NEW)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0029-soh-ios-sampler-mips.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
