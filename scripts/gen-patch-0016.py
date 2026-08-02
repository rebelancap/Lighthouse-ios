#!/usr/bin/env python3
"""Overlay patch 0016: never render while backgrounded (iOS).
Device symptom (2026-07-10): game becomes unplayable/slow after minimize +
reopen. Metal's nextDrawable must not run while the app is backgrounded —
it stalls/times out (and background GPU work risks watchdog kills), and the
drawable pool can come back degraded. Gate the acquisition on the shell's
SohIos_IsBackgrounded() flag (set in sceneDidEnterBackground, cleared on
foreground): the game thread parks here while backgrounded — safe, the
process is about to be suspended anyway — and resumes exactly here with a
fresh drawable. Also retry a null drawable briefly and skip the frame
instead of dereferencing null. __IOS__-only; single hunk at
SetupScreenFramebuffer, disjoint from 0010's NewFrame hunk (D6)."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/backends/gfx_metal.cpp"
REL = "libultraship/src/fast/backends/gfx_metal.cpp"
orig = SRC.read_text()

OLD = """void GfxRenderingAPIMetal::SetupScreenFramebuffer(uint32_t width, uint32_t height) {
    mCurrentDrawable = nullptr;
    mCurrentDrawable = mLayer->nextDrawable();
"""
NEW = """#ifdef __IOS__
// SOH_IOS (overlay 0016): shell-provided background flag (linkage specs
// must live at file scope, not inside the function).
extern "C" int SohIos_IsBackgrounded(void);
extern "C" void SohIos_ParkTick(void);
#endif

void GfxRenderingAPIMetal::SetupScreenFramebuffer(uint32_t width, uint32_t height) {
    mCurrentDrawable = nullptr;
#ifdef __IOS__
    // Park the game thread while backgrounded — nextDrawable in the
    // background stalls/times out (post-resume slowdown, watchdog risk).
    // The process suspends moments later and resumes here.
    {
        // ParkTick services the main run loop (the game loop IS the main
        // thread on iOS) so the lifecycle events that clear the flag can
        // deliver — a sleeping park here deadlocks into a black screen.
        while (SohIos_IsBackgrounded()) {
            SohIos_ParkTick();
        }
        const struct timespec sohIosRetryNap = { 0, 10 * 1000 * 1000 };
        for (int i = 0; i < 100 && mCurrentDrawable == nullptr; i++) {
            mCurrentDrawable = mLayer->nextDrawable();
            if (mCurrentDrawable == nullptr) {
                nanosleep(&sohIosRetryNap, nullptr); // pool drained (just resumed?)
            }
        }
        if (mCurrentDrawable == nullptr) {
            return; // skip the frame rather than crash on ->texture()
        }
    }
#else
    mCurrentDrawable = mLayer->nextDrawable();
#endif
"""

n = orig.count(OLD)
assert n == 1, f"[setup-screen-fb] expected 1 match, got {n}"
t = orig.replace(OLD, NEW)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0016-lus-ios-no-render-while-backgrounded.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
