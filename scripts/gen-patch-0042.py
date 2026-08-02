#!/usr/bin/env python3
"""Overlay patch 0042: drive the stereo eye passes from Lighthouse's own loop.

NUMBERING: 0001-0036 mirror the flagship's patch slots; 0037+ are
Lighthouse-only. This is the Lighthouse-only counterpart to the flagship's
0031, and it exists because of a real structural difference between the two
upstreams.

THE DIVERGENCE. SoH renders through
`Fast3dWindow::DrawAndRunGraphicsCommands`, so the flagship's whole two-eye
branch lives inside LUS. Lighthouse EXPANDS that function inline in
`GameEngine::` (src/port/Engine.cpp), with upstream's own comment explaining
why: it needs to read the backbuffer between `Run()` (frame rendered) and
`EndFrame()` (buffer swap), because on N64 the CPU and RDP shared physical
memory and `gFramebuffers` always held valid pixels after rendering.

`Fast3dWindow::DrawAndRunGraphicsCommands` still EXISTS in this LUS fork -- it
is simply never called. Patching it, as the flagship does, compiled cleanly,
applied cleanly, and did nothing at all. The only symptom was the bridge's `3d`
line reading `mode=1 loop=1` (3D active, compositor thread alive, panel showing
its test pattern) next to `eyeL=0 eyeR=0` (no eye pass had ever run). Worth
stating plainly: a patch applying is not evidence that the code it patches
executes.

THE SUB-FRAME MAPPING, which is also ours. Lighthouse renders `frameCount`
interpolated SUB-FRAMES per 30 Hz logic tick; each sub-frame is one presented
frame. The flagship renders both eyes once per host frame, so here both eyes
render per SUB-FRAME. Cost scales the way the 2D path already does, and the
existing budget check (`sPassBudgetNs`) still bounds the pass -- it will simply
fit fewer sub-frames in 3D, which is the correct degradation.

WHAT IS SKIPPED IN THE 3D BRANCH, and why:
* `gui->StartDraw()` / `gui->EndDraw()` -- StartDraw's ImGui NewFrame acquires
  the SCREEN drawable, and the 2D window is parked and hidden, so that acquire
  stalls forever. Overlay 0034's split frame replaces them.
* `interpreter->EndFrame()` -- its `mRapi->EndFrame()` presents to the screen
  swapchain we deliberately never touch. `Soh3DPaceFrame()` (0031) runs the
  window manager's swap pair alone, so the frame-rate limiter still sleeps.
* The `OS_ViBlackActive()` fb-0 clear -- fb 0 is the screen. In 3D the fade is
  applied to the eye framebuffers by the ordinary draw path instead.

The menu frame is built ONCE per sub-frame and rendered into BOTH eyes (ImGui
explicitly supports rendering one draw list more than once). It is built even
when the menu is hidden, because the perf HUD rides in its foreground draw
list. Per program D-041 the menu is display-only in 3D.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/port/Engine.cpp"
REL = "src/port/Engine.cpp"
orig = SRC.read_text()

# --- file-scope externs, next to the render loop -----------------------------
# extern "C" at FILE scope (playbook process rule): a block-scope extern
# namespace-mangles, which is this family's recurring linkage trap.
DECL_OLD = """void GameEngine::RunCommands(Gfx* Commands, const std::vector<std::unordered_map<Mtx*, MtxF>>& mtx_replacements,
"""
DECL_NEW = """#ifdef __IOS__
// LIGHTHOUSE_IOS (overlay 0042): shell-owned stereoscopic mode flag and the
// 3D telemetry the console bridge reports. Defined in SohIosShell.m, which is
// linked on iPhone too, so this resolves on every Apple build; the mode simply
// never leaves 0 outside visionOS.
extern "C" {
extern volatile int gSoh3DMode;
extern volatile int gSoh3DDbgMenuVis, gSoh3DDbgMenuBuilds, gSoh3DDbgMenuVtx;
extern volatile int gSoh3DMenuToggleReq;
}
#endif

void GameEngine::RunCommands(Gfx* Commands, const std::vector<std::unordered_map<Mtx*, MtxF>>& mtx_replacements,
"""
n = orig.count(DECL_OLD)
assert n == 1, f"[decl-anchor] expected 1 match, got {n}"
t = orig.replace(DECL_OLD, DECL_NEW)

# --- the two-eye branch, replacing the 2D body of the sub-frame loop --------
BODY_OLD = """            auto gui = wndBase->GetGui();
            wndBase->GetMouseStateManager()->StartFrame();
            gui->StartDraw();
            interpreter->StartFrame();
            interpreter->Run(Commands, m);
            if (OS_ViBlackActive()) {
                interpreter->mGfxFrameBuffer = 0;
                auto rapi = interpreter->GetCurrentRenderingAPI();
                rapi->StartDrawToFramebuffer(0, 1.0f);
                rapi->ClearFramebuffer(true, false);
            }
            gui->EndDraw();
            sLastSubFrameNs = NsSince(runT0);
            interpreter->EndFrame();
            CALL_EVENT(FrameDrawEnd);
"""
BODY_NEW = """            auto gui = wndBase->GetGui();
            wndBase->GetMouseStateManager()->StartFrame();
#ifdef __IOS__
            // LIGHTHOUSE_IOS (overlay 0042): stereoscopic 3D (visionOS). Render
            // the SAME command list twice, at the same game time, into the two
            // eye framebuffers; alternating eyes would halve each eye's rate and
            // read as judder. Entirely offscreen -- see the patch header for what
            // is skipped and why.
            static bool lhWas3D = false;
            if (gSoh3DMode != 0) {
                lhWas3D = true;
                // Direct toggle request. Reachable only from the dev bridge: the
                // menu is DISPLAY-ONLY in 3D (visionOS never hands an app raw
                // gaze, and the panel is a raw CompositorServices Metal quad, so
                // nothing hit-tests it), and key injection dies on device because
                // the immersive space steals SDL's input focus.
                if (gSoh3DMenuToggleReq != 0 && gui != nullptr && gui->GetMenu() != nullptr) {
                    gSoh3DMenuToggleReq = 0;
                    gui->GetMenu()->ToggleVisibility();
                }
                // Build the ImGui frame ONCE, primed against an eye framebuffer
                // (the screen backend's NewFrame would acquire the hidden
                // window's drawable), then composite the same draw data into both
                // eyes. Built even with the menu hidden: the perf HUD rides in
                // its foreground draw list. Skipped on the very first 3D frame,
                // when no eye framebuffer exists yet.
                void* lhMenuData = nullptr;
                int lhPrimeFb = interpreter->Soh3DGetEyeFb(1);
                if (gui != nullptr && lhPrimeFb >= 0) {
                    interpreter->GetCurrentRenderingAPI()->Soh3DImGuiPrime(lhPrimeFb);
                    gui->Soh3DBuildMenuFrame();
                    lhMenuData = gui->Soh3DGetDrawData();
                    gSoh3DDbgMenuVis = gui->GetMenuOrMenubarVisible() ? 1 : 0;
                    gSoh3DDbgMenuBuilds = gSoh3DDbgMenuBuilds + 1;
                    // ~24 verts = HUD only; a few thousand = menu open.
                    ImDrawData* lhDD = (ImDrawData*)lhMenuData;
                    gSoh3DDbgMenuVtx = (lhDD != nullptr) ? lhDD->TotalVtxCount : -1;
                }
                // LIGHTHOUSE_IOS (overlay 0042 rev2): bound GPU run-ahead
                // before building another pair. Without this the 3D path has
                // NO back-pressure at all -- 2D gets it from the drawable
                // acquire, which this path never performs -- and the shared,
                // unfenced vertex pool gets recycled under the GPU, so one eye
                // draws with the other eye's vertices. The wait sits inside the
                // measured pass, so sPassBudgetNs still degrades sub-frame
                // count honestly when the GPU is the limit.
                interpreter->Soh3DThrottle();
                for (int lhEye = 1; lhEye <= 2; lhEye++) {
                    interpreter->Soh3DSetEye(lhEye);
                    interpreter->StartFrame();
                    interpreter->Run(Commands, m);
                    if (lhMenuData != nullptr) {
                        interpreter->GetCurrentRenderingAPI()->Soh3DImGuiRender(
                            interpreter->Soh3DGetEyeFb(lhEye), lhMenuData);
                    }
                    interpreter->Soh3DEndEyePass(lhEye);
                }
                if (lhMenuData != nullptr) {
                    gui->Soh3DFinishMenuFrame();
                }
                sLastSubFrameNs = NsSince(runT0);
                // NO Soh3DSetEye(0) here: resetting per frame flaps
                // mCurDimensions between the eye and 2D sizes, and the
                // interpreter's dims-changed path then resizes EVERY game
                // framebuffer EVERY frame. Dims stay at eye size for the whole 3D
                // session and are restored once, on the transition back.
                interpreter->Soh3DPaceFrame();
                CALL_EVENT(FrameDrawEnd);
                interpreter->mInterpolationIndex++;
                continue;
            }
            if (lhWas3D) {
                lhWas3D = false;
                interpreter->Soh3DSetEye(0); // restore the saved 2D state ONCE
            }
#endif
            gui->StartDraw();
            interpreter->StartFrame();
            interpreter->Run(Commands, m);
            if (OS_ViBlackActive()) {
                interpreter->mGfxFrameBuffer = 0;
                auto rapi = interpreter->GetCurrentRenderingAPI();
                rapi->StartDrawToFramebuffer(0, 1.0f);
                rapi->ClearFramebuffer(true, false);
            }
            gui->EndDraw();
            sLastSubFrameNs = NsSince(runT0);
            interpreter->EndFrame();
            CALL_EVENT(FrameDrawEnd);
"""
n = t.count(BODY_OLD)
assert n == 1, f"[body-anchor] expected 1 match, got {n}"
t = t.replace(BODY_OLD, BODY_NEW)

# ImDrawData is referenced above; make sure the TU actually sees imgui.
if "#include <imgui.h>" not in t and '#include "imgui.h"' not in t:
    # NOT at the top of the file: overlay 0013 owns a hunk there
    # (`@@ -1,4 @@`), and inserting inside another patch's context window
    # breaks ITS reverse-apply -- which apply-overlay's idempotency check and
    # bootstrap.sh's purity check both depend on. Learned twice this session
    # (0038/0041, then this). Anchor somewhere no other patch is standing.
    INC_OLD = "#include <fast/interpreter.h>\n"
    INC_NEW = ("#include <fast/interpreter.h>\n"
               "#ifdef __IOS__\n"
               "#include <imgui.h> // LIGHTHOUSE_IOS (overlay 0042): ImDrawData telemetry\n"
               "#endif\n")
    n = t.count(INC_OLD)
    assert n == 1, f"[imgui-include] expected 1 match, got {n}"
    t = t.replace(INC_OLD, INC_NEW)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig)
    fb.write(t)
    fa.flush()
    fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0042-lighthouse-stereo-eyes-in-engine-loop.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
