#!/usr/bin/env python3
"""Overlay patch 0028: true GPU frame time in the perf telemetry.
The 0008 probe's eng_ms includes in-present blocking (compositor pacing),
so it cannot distinguish "M5 GPU saturated" from "GPU idle, stalls come
from CPU submission / streaming / pacing" — the exact question the Vision
Pro optimization hinges on (user: 'M5 shouldn't struggle'). Sample each
screen command buffer's GPUEndTime-GPUStartTime via addCompletedHandler
into a global; the shell/probe export it (SohIos_GpuMs)."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/backends/gfx_metal.cpp"
REL = "libultraship/src/fast/backends/gfx_metal.cpp"
orig = SRC.read_text()

OLD_DECL = """#include <spdlog/fmt/fmt.h>
"""
NEW_DECL = """#include <spdlog/fmt/fmt.h>

#ifdef __IOS__
// SOH_IOS (overlay 0028): defined in the app shell; C linkage (the shell is
// ObjC). File scope — block-scope linkage specs don't compile in C++.
extern "C" {
extern volatile float gSohIosGpuMs;
}
#endif
"""

OLD = """    screen_framebuffer.mCommandBuffer->presentDrawable(mCurrentDrawable);
    mCurrentVertexBufferPoolIndex = (mCurrentVertexBufferPoolIndex + 1) % kMaxVertexBufferPoolSize;
    screen_framebuffer.mCommandBuffer->commit();
"""
NEW = """    screen_framebuffer.mCommandBuffer->presentDrawable(mCurrentDrawable);
    mCurrentVertexBufferPoolIndex = (mCurrentVertexBufferPoolIndex + 1) % kMaxVertexBufferPoolSize;
#ifdef __IOS__
    // SOH_IOS (overlay 0028): true GPU duration of the frame's screen pass —
    // the perf probe's eng_ms includes present blocking and cannot separate
    // GPU saturation from pacing waits.
    screen_framebuffer.mCommandBuffer->addCompletedHandler(
        MTL::HandlerFunction([](MTL::CommandBuffer* cb) {
            double ms = (cb->GPUEndTime() - cb->GPUStartTime()) * 1000.0;
            if (ms > 0 && ms < 1000.0) {
                gSohIosGpuMs = (float)ms;
            }
        }));
#endif
    screen_framebuffer.mCommandBuffer->commit();
"""
nd = orig.count(OLD_DECL)
assert nd == 1, f"[gpu-decl] expected 1 match, got {nd}"
t = orig.replace(OLD_DECL, NEW_DECL)
n = t.count(OLD)
assert n == 1, f"[gpu-time] expected 1 match, got {n}"
t = t.replace(OLD, NEW)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0028-soh-ios-gpu-frame-time.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
