#!/usr/bin/env python3
"""Overlay patch 0010: the iOS BLACK-SCREEN / corner-render fix.

On __APPLE__ the game reaches the screen (fb 0) by being drawn as an
ImGui::Image quad. LUS inits the ImGui SDL2 backend via
ImGui_ImplSDL2_InitForMetal (Fast3dGui.cpp:103), i.e. with NO SDL_Renderer, so
its NewFrame derives DisplayFramebufferScale from SDL_GL_GetDrawableSize
(imgui_impl_sdl2.cpp:937) — which returns POINTS on a Metal window. Measured on
device/sim: DisplaySize=912x420, FramebufferScale=1x1, while the drawable is
2736x1260 native pixels. So DisplaySize*FramebufferScale=912x420 and
RenderDrawData's guard (gfx_metal.cpp:123) sees screen_texture(2736) != 912 and
early-RETURNS -> the whole ImGui pass (incl. the game image) is skipped every
frame -> solid black. (Forcing the guard through instead draws the game into
the top-left corner, since the ImGui viewport is DisplaySize*scale=912x420.)

Fix: in RenderDrawData, recompute FramebufferScale from the actual drawable
pixel size (screen_texture, == SDL_GetRendererOutputSize here) over the
point-sized DisplaySize. DisplaySize stays in points so the ImGui menu keeps
its normal on-screen size; the game image then fills the full drawable at
native 2736x1260 and the guard passes. Verified on the iPhone Air simulator:
the OoT title screen renders full-screen. iOS-only; desktop untouched."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/backends/gfx_metal.cpp"
REL = "libultraship/src/fast/backends/gfx_metal.cpp"
orig = SRC.read_text()

old = ("    MTL::Texture* screen_texture = mTextures[framebuffer.mTextureId].texture;\n"
       "    int fb_width = (int)(drawData->DisplaySize.x * drawData->FramebufferScale.x);\n"
       "    int fb_height = (int)(drawData->DisplaySize.y * drawData->FramebufferScale.y);\n")
new = ("    MTL::Texture* screen_texture = mTextures[framebuffer.mTextureId].texture;\n"
       "#ifdef __IOS__\n"
       "    // The ImGui SDL2 backend was init'd for Metal (no SDL_Renderer) so it derives\n"
       "    // FramebufferScale from SDL_GL_GetDrawableSize, which returns POINTS on iOS\n"
       "    // (scale ends up 1x). Recompute it from the real drawable pixels so\n"
       "    // DisplaySize(points)*scale == drawable, or the game image is skipped (black)\n"
       "    // / drawn into the top-left corner. See overlay 0010.\n"
       "    if (drawData->DisplaySize.x > 0.0f && drawData->DisplaySize.y > 0.0f) {\n"
       "        drawData->FramebufferScale =\n"
       "            ImVec2((float)screen_texture->width() / drawData->DisplaySize.x,\n"
       "                   (float)screen_texture->height() / drawData->DisplaySize.y);\n"
       "    }\n"
       "#endif\n"
       "    int fb_width = (int)(drawData->DisplaySize.x * drawData->FramebufferScale.x);\n"
       "    int fb_height = (int)(drawData->DisplaySize.y * drawData->FramebufferScale.y);\n")
n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
t = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0010-lus-metal-ios-imgui-framebuffer-scale.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
