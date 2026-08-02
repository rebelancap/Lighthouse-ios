#!/usr/bin/env python3
"""Overlay patch 0034: the port menu inside stereoscopic 3D (Gui frame split).

The 3D path skips the gui's StartDraw/EndDraw entirely, because the Metal
backend's ImGui NewFrame acquires the SCREEN drawable and that stalls forever
while the 2D window is parked and hidden. So the menu (and, more importantly,
the perf HUD) would simply not exist in 3D.

Split the frame:
* `Soh3DBuildMenuFrame()` runs the ImGui frame WITHOUT the backend NewFrame —
  the caller primes ImGui_ImplMetal against an EYE framebuffer instead — and
  skips DrawGame / CalculateGameViewport, since the menu composites OVER the
  already-rendered eye images rather than hosting them.
* the caller renders the resulting draw data into BOTH eyes (ImGui explicitly
  supports rendering one draw list more than once).
* `Soh3DFinishMenuFrame()` closes the frame and saves CVars.

VERIFIED FOR THIS FORK, not assumed (the playbook asks for exactly this):
`Fast3dGui::CalculateGameViewport` derives the engine's render canvas from the
`ImGui::Begin("Main Game", ...)` window's content size here too
(Fast3dGui.cpp:306/314/370, Gui.cpp:159/222). So the flagship's rule holds
without change: the 3D path must NEVER `Begin("Main Game")`. Touching it with
SetNextWindowPos/Size undocks it and leaves it floating at the 3D canvas size,
and back in 2D the engine then renders 1920x1080 into a smaller drawable — a
top-left crop that `imgui.ini` happily persists across launches. Hence the
self-heal re-dock below, gated to 2D frames.

Also carries the 3D perf HUD. The 2D fps readout is a UIKit label on the
parked window, which is architecturally invisible on the panel; the shell
composes the same string (`SohIos_PerfHud3DText`) and it is drawn into the
ImGui FOREGROUND draw list, which is part of the main viewport's draw data and
therefore reaches both eyes. ASCII only — the menu atlas has no bullet glyph
and renders one as '?'.

Per program D-041 the menu is DISPLAY-ONLY in 3D: visionOS never hands an app
raw gaze, and the panel is a raw CompositorServices Metal quad, so nothing hit-
tests it. The frame is still built every 3D frame because the HUD rides in it.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LUS = ROOT / "vendor/Lighthouse/libultraship"


def gen(path_rel, edits, tag):
    src = LUS / path_rel
    orig = src.read_text()
    t = orig
    for i, (old, new) in enumerate(edits):
        n = t.count(old)
        assert n == 1, f"[{tag}:{i}] expected 1 match, got {n}"
        t = t.replace(old, new)
    rel = f"libultraship/{path_rel}"
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig)
        fb.write(t)
        fa.flush()
        fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"[{tag}] no diff produced"
    return r.stdout.decode()


diffs = []

diffs.append(gen(
    "include/ship/window/gui/Gui.h",
    [(
        "    bool GetMenuOrMenubarVisible();\n",
        "    bool GetMenuOrMenubarVisible();\n"
        "    // LIGHTHOUSE_IOS (overlay 0034): stereo 3D menu frame -- build once (no\n"
        "    // backend NewFrame: the caller primed ImGui_ImplMetal with an eye\n"
        "    // framebuffer), render the draw data per eye, then finish.\n"
        "    void Soh3DBuildMenuFrame();\n"
        "    void* Soh3DGetDrawData();\n"
        "    void Soh3DFinishMenuFrame();\n",
    )],
    "gui-h"))

diffs.append(gen(
    "src/ship/window/gui/Gui.cpp",
    [(
        "void Gui::DrawMenu() {\n",
        "// LIGHTHOUSE_IOS (overlay 0034): 3D mode flag (shell-owned) -- gates the\n"
        "// Main Game re-dock self-heal below to 2D frames.\n"
        "extern \"C\" volatile int gSoh3DMode;\n"
        "\n"
        "void Gui::DrawMenu() {\n",
    ), (
        "    ImGui::DockSpace(dockId, ImVec2(0.0f, 0.0f), ImGuiDockNodeFlags_None | ImGuiDockNodeFlags_NoDockingInCentralNode);\n",
        "    ImGui::DockSpace(dockId, ImVec2(0.0f, 0.0f), ImGuiDockNodeFlags_None | ImGuiDockNodeFlags_NoDockingInCentralNode);\n"
        "    // LIGHTHOUSE_IOS (overlay 0034): self-heal. If Main Game ever ends up\n"
        "    // FLOATING, the engine renders the floating size into the smaller\n"
        "    // drawable and the result is a top-left crop -- and imgui.ini persists\n"
        "    // that layout across launches, so it does not clear itself. Re-dock it.\n"
        "    // 2D frames only: the 3D path must never touch Main Game at all.\n"
        "    if (gSoh3DMode == 0) {\n"
        "        ImGuiWindow* sohMg = ImGui::FindWindowByName(\"Main Game\");\n"
        "        if (sohMg != NULL && sohMg->DockId == 0) {\n"
        "            ImGui::DockBuilderDockWindow(\"Main Game\", dockId);\n"
        "        }\n"
        "    }\n",
    ), (
        "void Gui::StartFrame() {\n",
        "// LIGHTHOUSE_IOS (overlay 0034): the 2D perf HUD is a UIKit label on the\n"
        "// parked window -- invisible on the panel. The shell composes the same text\n"
        "// here and the 3D frame draws it into the eye images. Returns -1 when the\n"
        "// HUD is off, else the thermal state, which picks the colour.\n"
        "extern \"C\" int SohIos_PerfHud3DText(char* buf, int cap);\n"
        "\n"
        "// LIGHTHOUSE_IOS (overlay 0034): see Gui.h. DrawGame and\n"
        "// CalculateGameViewport are intentionally absent -- the menu composites\n"
        "// over the already-rendered eyes rather than hosting the game image.\n"
        "void Gui::Soh3DBuildMenuFrame() {\n"
        "    HandleMouseCapture();\n"
        "    ImGuiWMNewFrame();\n"
        "    // The SDL backend just set DisplaySize from the PARKED 480x320 card.\n"
        "    // Laying the menu out against that canvas renders it ~8x too large on\n"
        "    // the panel. Use a 2D-window-like logical canvas at the eye aspect\n"
        "    // instead; the render step's FramebufferScale recompute maps it onto\n"
        "    // the eye texture.\n"
        "    ImGui::GetIO().DisplaySize = ImVec2(1920.0f, 1080.0f);\n"
        "    ImGui::NewFrame();\n"
        "    // The main dockspace window normally hosts the game image (DrawGame,\n"
        "    // skipped here); left opaque it curtains the rendered eyes. Every\n"
        "    // backdrop goes transparent so only menu chrome composites.\n"
        "    ImGui::PushStyleColor(ImGuiCol_WindowBg, ImVec4(0, 0, 0, 0));\n"
        "    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0, 0, 0, 0));\n"
        "    ImGui::PushStyleColor(ImGuiCol_DockingEmptyBg, ImVec4(0, 0, 0, 0));\n"
        "    DrawMenu();\n"
        "    ImGui::PopStyleColor(3);\n"
        "    // Notification overlay: Draw() begins its own full-viewport GameOverlay\n"
        "    // window, so call it bare. NEVER wrap it in a Begin(\"Main Game\") here --\n"
        "    // see the patch header for what that costs.\n"
        "    GetGameOverlay()->Draw();\n"
        "    // Perf HUD in 3D: same text as the 2D label, top-right, every frame the\n"
        "    // HUD CVar is on (independent of menu visibility). The foreground draw\n"
        "    // list is part of the draw data, so it reaches both eyes.\n"
        "    {\n"
        "        char sohHudBuf[64];\n"
        "        int sohHudTh = SohIos_PerfHud3DText(sohHudBuf, (int)sizeof(sohHudBuf));\n"
        "        if (sohHudTh >= 0 && sohHudBuf[0] != 0) {\n"
        "            ImDrawList* sohFg = ImGui::GetForegroundDrawList();\n"
        "            ImFont* sohFont = ImGui::GetFont();\n"
        "            const float sohHudSize = 30.0f;\n"
        "            ImVec2 sohTs = sohFont->CalcTextSizeA(sohHudSize, FLT_MAX, 0.0f, sohHudBuf);\n"
        "            ImVec2 sohPos = ImVec2(ImGui::GetIO().DisplaySize.x - sohTs.x - 34.0f, 18.0f);\n"
        "            ImU32 sohCol = (sohHudTh >= 2)  ? IM_COL32(255, 102, 77, 242)\n"
        "                           : (sohHudTh == 1) ? IM_COL32(255, 204, 102, 230)\n"
        "                                             : IM_COL32(255, 255, 255, 217);\n"
        "            sohFg->AddText(sohFont, sohHudSize, ImVec2(sohPos.x + 2, sohPos.y + 2),\n"
        "                           IM_COL32(0, 0, 0, 200), sohHudBuf);\n"
        "            sohFg->AddText(sohFont, sohHudSize, sohPos, sohCol, sohHudBuf);\n"
        "        }\n"
        "    }\n"
        "    ImGui::Render();\n"
        "}\n"
        "\n"
        "void* Gui::Soh3DGetDrawData() {\n"
        "    return (void*)ImGui::GetDrawData();\n"
        "}\n"
        "\n"
        "void Gui::Soh3DFinishMenuFrame() {\n"
        "    ImGui::EndFrame();\n"
        "    CheckSaveCvars();\n"
        "}\n"
        "\n"
        "void Gui::StartFrame() {\n",
    )],
    "gui-cpp"))

out = ROOT / "overlay/patches/0034-lighthouse-gui-3d-menu-split.patch"
out.write_text(__doc__ + "\n" + "".join(diffs))
print(f"wrote {out}")
