#!/usr/bin/env python3
"""Overlay patch 0045: iOS menu scale (correcting SHELL-DIVERGENCES B8).

NUMBERING: 0037+ are Lighthouse-only.

=== 1. MENU SCALE (correcting SHELL-DIVERGENCES B8) ===

B8 recorded that the flagship's "Menu Scale" slider was deliberately NOT
shipped here, because the flagship applies it through
`OTRGlobals::ScaleImGui()` "which has no analogue here". That was wrong, and
device testing caught it: `GameEngine::ScaleImGui()` (src/port/Engine.cpp:1056) is
structurally identical — same preset-index CVar, same
`previousImGuiScale` ratio tracking, same live `ScaleAllSizes` +
`FontGlobalScale` application. I searched for the flagship's SYMBOL instead of
its MECHANISM and concluded the mechanism was absent.

So this ports flagship overlay 0015 exactly: multiply `gSohIos.MenuScale`
(default 0.85, the family-wide value) into the target scale, and compare the
COMBINED float rather than the preset index — otherwise the early-out on
`imGuiScaleIndex == previousImGuiScaleIndex` swallows every slider move,
which is precisely the "menu scale does nothing" bug the flagship hit on
device.

"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/port/Engine.cpp"
REL = "src/port/Engine.cpp"
orig = SRC.read_text()

# --- 1. menu scale ----------------------------------------------------------
OLD = """void GameEngine::ScaleImGui() {
    int32_t imGuiScaleIndex = CVarGetInteger("gSettings.ImGuiScale", defaultImGuiScale);
    if (imGuiScaleIndex == previousImGuiScaleIndex) {
        return;
    }

    float scale = imguiScaleOptionToValue[imGuiScaleIndex];
    float newScale = scale / previousImGuiScale;
"""
NEW = """void GameEngine::ScaleImGui() {
    int32_t imGuiScaleIndex = CVarGetInteger("gSettings.ImGuiScale", defaultImGuiScale);
#ifdef __IOS__
    // LIGHTHOUSE_IOS (overlay 0045): fold the iOS menu-scale slider into the
    // preset. Compare the COMBINED scale, not the preset index -- the index
    // early-out below is unchanged by a slider move, so keeping it would make
    // the slider silently do nothing (the exact bug the flagship shipped and
    // had to fix on device).
    float scale = imguiScaleOptionToValue[imGuiScaleIndex] *
                  CVarGetFloat("gSohIos.MenuScale", 0.85f);
    if (scale == previousImGuiScale) {
        return;
    }
    float newScale = scale / previousImGuiScale;
#else
    if (imGuiScaleIndex == previousImGuiScaleIndex) {
        return;
    }

    float scale = imguiScaleOptionToValue[imGuiScaleIndex];
    float newScale = scale / previousImGuiScale;
#endif
"""
n = orig.count(OLD)
assert n == 1, f"[scaleimgui] expected 1 match, got {n}"
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
out = ROOT / "overlay/patches/0045-lighthouse-ios-menu-scale.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
