#!/usr/bin/env python3
"""Overlay patch 0036: iOS supersampling (SSAA) — Ghostship cross-port
adoption (their overlay 0032, device-confirmed "looks phenomenal").
FINDING (confirmed on SoH by live telemetry): on iOS the game renders
SUB-NATIVE — CalculateGameViewport sets mCurDimensions from ImGui's
POINT-space content region (x internal-res seed), and ImGui::Image
upscales to the native-pixel fb0. iPhone shipped at 1824x840 vs the
2736x1260 drawable (67%); the Vision Pro 2D window even lower vs its 4K
drawable. Scale mCurDimensions to (native drawable px * ssaa) in
StartFrame: ssaa=1.0 alone fixes the sub-native upscale; >1.0 renders
above native and downsamples (true SSAA). Factor is native-px-relative
so it lands on native*ssaa whether mCurDimensions arrives as points or
pixels. Only mCurDimensions scales — mNativeDimensions and
mGameWindowViewport stay untouched (their equality-guarded uses
deactivate to the safe full-fb path). Clamped to the backend max
texture size. The visionOS 3D eye path is unaffected (the eye override
later in StartFrame wins). Shell provides SohIos_SsaaFactor
(gSohIos.Supersample, 1.0-2.0, SoH default 1.0 — heavy port) and
SohIos_DrawablePixelWidth. __IOS__-only; macOS oracle pristine."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/interpreter.cpp"
REL = "libultraship/src/fast/interpreter.cpp"
orig = SRC.read_text()

def replace_once(text, old, new, tag):
    n = text.count(old)
    assert n == 1, f"[{tag}] expected 1 match, got {n}"
    return text.replace(old, new)

t = orig
t = replace_once(t,
    """void Interpreter::StartFrame() {
    mWapi->GetDimensions(&mGfxCurrentWindowDimensions.width, &mGfxCurrentWindowDimensions.height, &mCurWindowPosX,
                         &mCurWindowPosY);
    if (mCurDimensions.height == 0) {
        // Avoid division by zero
        mCurDimensions.height = 1;
    }
    mCurDimensions.aspect_ratio = (float)mCurDimensions.width / (float)mCurDimensions.height;""",
    """#ifdef __IOS__
// SOH_IOS (overlay 0036): SSAA hooks provided by the app shell (SohIosShell.m).
extern "C" float SohIos_SsaaFactor(void);
extern "C" uint32_t SohIos_DrawablePixelWidth(void);
#endif

void Interpreter::StartFrame() {
    mWapi->GetDimensions(&mGfxCurrentWindowDimensions.width, &mGfxCurrentWindowDimensions.height, &mCurWindowPosX,
                         &mCurWindowPosY);
    if (mCurDimensions.height == 0) {
        // Avoid division by zero
        mCurDimensions.height = 1;
    }
#ifdef __IOS__
    // SOH_IOS (overlay 0036) SSAA: CalculateGameViewport just set
    // mCurDimensions from ImGui's POINT-space content region; the game
    // renders offscreen to mGameFb and ImGui::Image scales it to the
    // native-pixel fb0. Scale mCurDimensions to (native drawable px * ssaa)
    // so that scale is a downsample (supersample), never an upscale.
    // Uniform factor => aspect preserved. mNativeDimensions and
    // mGameWindowViewport are NOT scaled — their equality-guarded uses
    // deactivate to the safe full-fb path. The 3D eye override later in
    // this function wins in stereo mode.
    {
        float sohSsaa = SohIos_SsaaFactor();
        uint32_t sohNativePxW = SohIos_DrawablePixelWidth();
        if (sohSsaa > 0.0f && sohNativePxW > 0 && mCurDimensions.width > 0) {
            float sohF = ((float)sohNativePxW / (float)mCurDimensions.width) * sohSsaa;
            uint32_t sohW = (uint32_t)(mCurDimensions.width * sohF + 0.5f);
            uint32_t sohH = (uint32_t)(mCurDimensions.height * sohF + 0.5f);
            // mGameFb is a texture — clamp uniformly to the backend max.
            int sohMax = mRapi->GetMaxTextureSize();
            if (sohMax > 0 && ((int)sohW > sohMax || (int)sohH > sohMax)) {
                float sohClamp = (float)sohMax / (float)(sohW > sohH ? sohW : sohH);
                sohW = (uint32_t)(sohW * sohClamp);
                sohH = (uint32_t)(sohH * sohClamp);
            }
            mCurDimensions.width = sohW;
            mCurDimensions.height = sohH;
        }
    }
#endif
    mCurDimensions.aspect_ratio = (float)mCurDimensions.width / (float)mCurDimensions.height;""",
    "ssaa")

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0036-lus-ios-supersampling.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
