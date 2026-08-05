#!/usr/bin/env python3
"""Overlay patch 0031: stereoscopic eye rendering for visionOS 3D (Phase 02).

The shell's CompositorServices loop (SohImmersive.m) composites two per-eye
Metal textures onto a world-locked panel. This patch is the LUS half of making
Fast3D produce them: an eye state that (a) swaps mGameFb to a per-eye
framebuffer, so every existing offscreen consumer targets it transparently,
and (b) applies an off-axis stereo projection to PERSPECTIVE matrices only.
Orthographic passes (HUD, 2D) are left untouched and therefore sit exactly on
the panel plane, which is comfort for free.

THE HOST-FRAME BRANCH IS NOT HERE — see overlay 0042. The flagship puts it in
`Fast3dWindow::DrawAndRunGraphicsCommands`, and that function exists in this
LUS fork too, but LIGHTHOUSE NEVER CALLS IT: upstream expanded it inline in
`GameEngine` (src/port/Engine.cpp) so it can read the backbuffer between
`Run()` and `EndFrame()` — on real N64 hardware the CPU and RDP shared
physical memory, so `gFramebuffers` always held valid pixels after rendering,
and the port reproduces that. Patching Fast3dWindow here would have compiled,
applied, and done absolutely nothing; it cost one sim round to notice, with
`mode=1 loop=1` (3D live, compositor alive) sitting next to `eyeL=0 eyeR=0`
(no eye ever rendered) as the only symptom. So this patch stops at the LUS
API and 0042 drives it from the game's own render loop.

The screen framebuffer (fb 0) is NEVER touched in 3D: no drawable acquire (a
hidden window's acquire stalls forever) and no present. A new Metal
EndFrameOffscreen commits the drawn framebuffers, rides overlay 0019's
pending-mip pass, and replicates EndFrame's per-frame state cleanup.

Both eyes render EVERY host frame. Alternating them halves each eye's rate
and reads as judder.

PORTED FROM THE FLAGSHIP WITH ZERO REBASE. All fourteen anchors matched this
LUS fork (2917d0f) exactly on the first probe — the Phase 0 divergence audit
predicted the visionOS patches would apply clean and they do. Local variable
names keep their `soh` prefix deliberately: they were chosen to avoid
collisions in these functions, renaming buys nothing, and keeping them makes
a future three-way diff against the flagship readable.

WHAT IS BANJO-SPECIFIC, and therefore genuinely ours:
* The camera basis comes from overlay 0032, which exports BK's own
  `sViewportPosition` / `sViewportLookbk_vector` / FOV from `viewport_update`.
  See that patch for why the export must be a BASIS and not a world offset.
* The convergence clamp range and fallback are BK's game units.

ALSO FOLDED IN HERE (was briefly its own patch 0041, Phase 01): publishing
`mGfxCurrentWindowDimensions` and `mCurDimensions` to the shell for the
`drawable` bridge command. It lives in the flagship's 0031 too; it was split
out only because Phase 01 needed the sub-native check (playbook 1.4a) before
Phase 02 existed. Now that this patch is here it takes the lines back, because
two patches writing inside one hunk's context window is precisely the trap
that broke 0038's reverse-apply earlier in this session.

Everything new is `__IOS__`-guarded. The shell globals live in SohIosShell.m,
which is compiled for iPhone too, so iPhone links resolve and the mode simply
never leaves 0; the macOS oracle compiles none of it.
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

# --- base rendering API: default no-ops so only Metal implements them --------
diffs.append(gen(
    "include/fast/backends/gfx_rendering_api.h",
    [(
        "    virtual void EndFrame() = 0;\n",
        "    virtual void EndFrame() = 0;\n"
        "    // LIGHTHOUSE_IOS (overlay 0031): end-of-pass flush for offscreen stereo\n"
        "    // eye rendering -- commit work WITHOUT touching the screen swapchain.\n"
        "    virtual void Soh3DEndFrameOffscreen(int eyeIdx) {\n"
        "    }\n"
        "    // Bound the number of eye-pass PAIRS the CPU may run ahead of the\n"
        "    // GPU. The 2D path gets this for free from the drawable acquire;\n"
        "    // the 3D path acquires nothing and would otherwise run unbounded.\n"
        "    virtual void Soh3DThrottle() {\n"
        "    }\n"
        "    // Stereo-3D ImGui: prime the backend against a framebuffer (never the\n"
        "    // screen drawable) and render one ImDrawData (void*) into a\n"
        "    // framebuffer's still-open encoder.\n"
        "    virtual void Soh3DImGuiPrime(int fb) {\n"
        "    }\n"
        "    virtual void Soh3DImGuiRender(int fb, void* drawData) {\n"
        "    }\n"
        "    // Publish this framebuffer's texture to the stereo compositor ON GPU\n"
        "    // COMPLETION of the frame that renders it -- never at commit time.\n"
        "    virtual void Soh3DPublishOnComplete(int fb, int eyeIdx) {\n"
        "    }\n",
    )],
    "api-h"))

# --- Metal header: override decls -------------------------------------------
diffs.append(gen(
    "include/fast/backends/gfx_metal.h",
    [(
        "static constexpr size_t kMaxVertexBufferPoolSize = 3;\n",
        "// LIGHTHOUSE_IOS (overlay 0031 rev2): DEEPER ON iOS. Fast3D transforms\n"
        "// vertices on the CPU and sub-allocates every draw from this shared pool;\n"
        "// the slot index advances once per EndFrame. Nothing fences reuse -- in 2D\n"
        "// nothing has to, because the drawable acquire bounds CPU run-ahead below\n"
        "// the pool depth by construction. The stereo path acquires no drawable AND\n"
        "// advances the pool once per EYE (twice per sub-frame), so at depth 3 a slot\n"
        "// is recycled every 1.5 sub-frames (~12 ms at 120 fps). Once GPU lag exceeds\n"
        "// that under load, the CPU overwrites vertices the GPU has not drawn yet and\n"
        "// one eye rasterizes the other eye's positions -- the reported left/right\n"
        "// flicker. Soh3DThrottle bounds the lag; this gives it headroom to work in:\n"
        "// 2 pairs in flight x (2 eyes + 1 building) = 6 live slots <= 8. ~25 MB.\n"
        "#ifdef __IOS__\n"
        "static constexpr size_t kMaxVertexBufferPoolSize = 8;\n"
        "#else\n"
        "static constexpr size_t kMaxVertexBufferPoolSize = 3;\n"
        "#endif\n",
    ), (
        "    void EndFrame() override;\n",
        "    void EndFrame() override;\n"
        "    void Soh3DEndFrameOffscreen(int eyeIdx) override;\n"
        "    void Soh3DThrottle() override;\n"
        "    void Soh3DImGuiPrime(int fb) override;\n"
        "    void Soh3DImGuiRender(int fb, void* drawData) override;\n"
        "    void Soh3DPublishOnComplete(int fb, int eyeIdx) override;\n",
    )],
    "metal-h"))

# --- Metal impl --------------------------------------------------------------
diffs.append(gen(
    "src/fast/backends/gfx_metal.cpp",
    [(
        "int GfxRenderingAPIMetal::CreateFramebuffer() {\n",
        "// LIGHTHOUSE_IOS (overlay 0031): end-of-eye-pass flush for stereoscopic 3D.\n"
        "// The screen framebuffer was never set up this frame (no drawable -- a\n"
        "// hidden window's acquire stalls forever), so EndFrame's present path is\n"
        "// unusable: commit every drawn framebuffer's command buffer, ride overlay\n"
        "// 0019's pending-mip pass on the last one, and replicate EndFrame's\n"
        "// per-frame state cleanup.\n"
        "extern \"C\" {\n"
        "extern void* volatile gSoh3DEyeTexture[2];\n"
        "}\n"
        "#include <chrono>\n"
        "#include <condition_variable>\n"
        "\n"
        "// GPU BACK-PRESSURE FOR THE 3D PATH (overlay 0031 rev2).\n"
        "// 2D gets this free: acquiring the next drawable blocks once the swapchain\n"
        "// is full. The stereo path never acquires a drawable, never waits on\n"
        "// completion, and is paced only by a wall-clock limiter -- so CPU run-ahead\n"
        "// is unbounded, and every shared, unfenced resource (the vertex pool above\n"
        "// all, plus the frame/coord uniform buffers) is recycled under the GPU as\n"
        "// soon as the GPU falls behind. Bounding in-flight PAIRS fixes the whole\n"
        "// class at once rather than deepening each buffer separately.\n"
        "static constexpr int kSoh3DMaxInFlightPairs = 2;\n"
        "static std::atomic<int> sSoh3DInFlight{ 0 };\n"
        "static std::mutex sSoh3DThrottleMutex;\n"
        "static std::condition_variable sSoh3DThrottleCv;\n"
        "static std::atomic<unsigned long long> sSoh3DThrottleWaitNs{ 0 };\n"
        "\n"
        "// Telemetry for the LH_PERF line (overlay 0008): how deep the queue is and\n"
        "// how long the engine actually spent waiting. inflight pinned at the cap\n"
        "// with a rising thr_ms means the GPU is the limit, which is the honest\n"
        "// reading this path never had.\n"
        "extern \"C\" void SohIos_Stereo3DStats(int* outInflight, float* outWaitMs) {\n"
        "    if (outInflight != nullptr) {\n"
        "        *outInflight = sSoh3DInFlight.load(std::memory_order_relaxed);\n"
        "    }\n"
        "    if (outWaitMs != nullptr) {\n"
        "        *outWaitMs = (float)(sSoh3DThrottleWaitNs.exchange(0, std::memory_order_relaxed) / 1.0e6);\n"
        "    }\n"
        "}\n"
        "\n"
        "void GfxRenderingAPIMetal::Soh3DThrottle() {\n"
        "    const auto sohT0 = std::chrono::steady_clock::now();\n"
        "    {\n"
        "        std::unique_lock<std::mutex> sohLk(sSoh3DThrottleMutex);\n"
        "        // Timeout, never an unbounded wait: if the GPU wedges, this must\n"
        "        // degrade to a slideshow rather than deadlock the game thread.\n"
        "        sSoh3DThrottleCv.wait_for(sohLk, std::chrono::milliseconds(100), [] {\n"
        "            return sSoh3DInFlight.load(std::memory_order_acquire) < kSoh3DMaxInFlightPairs;\n"
        "        });\n"
        "    }\n"
        "    sSoh3DThrottleWaitNs.fetch_add(\n"
        "        (unsigned long long)std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
        "            std::chrono::steady_clock::now() - sohT0)\n"
        "            .count(),\n"
        "        std::memory_order_relaxed);\n"
        "}\n"
        "\n"
        "static int sSoh3DPublishFb = -1;\n"
        "static int sSoh3DPublishEye = -1;\n"
        "static std::mutex sSoh3DPublishMutex;\n"
        "static void* sSoh3DRetired[16];\n"
        "static int sSoh3DRetiredIdx = 0;\n"
        "\n"
        "void GfxRenderingAPIMetal::Soh3DPublishOnComplete(int fb, int eyeIdx) {\n"
        "    sSoh3DPublishFb = fb;\n"
        "    sSoh3DPublishEye = eyeIdx;\n"
        "}\n"
        "\n"
        "// EYE-GEOMETRY HASH (overlay 0031 rev4) -- the falsifiable instrument this\n"
        "// bug has lacked through four failed fixes. The user reports the flicker is\n"
        "// visible with ONE EYE CLOSED, so a single view's CONTENT alternates; and\n"
        "// \"objects at two locations\" is GEOMETRY, which lives in CPU memory here\n"
        "// (Fast3D transforms vertices on the CPU into mVertexBufferPool). So hash\n"
        "// the bytes actually submitted for each eye pass and keep the last four per\n"
        "// eye. Reading them tells the three reported states apart directly:\n"
        "//   L[i] != R[i], both varying   -> correct stereo\n"
        "//   L[i] == R[i]                 -> both eyes drew the SAME geometry (\"2D\")\n"
        "//   L alternating A,B,A,B        -> one eye alternating between two\n"
        "//                                   transforms (the flicker)\n"
        "//   L[i] == R[i-1]               -> cross-eye contamination\n"
        "// Strided sample, not every byte: this runs per eye pass at up to 120 fps\n"
        "// and only has to detect CHANGE, not authenticate content.\n"
        "static unsigned int sSoh3DEyeHash[2][4];\n"
        "static int sSoh3DEyeHashIdx[2];\n"
        "\n"
        "extern \"C\" void SohIos_Stereo3DHashes(unsigned int* outL, unsigned int* outR) {\n"
        "    for (int i = 0; i < 4; i++) {\n"
        "        if (outL != nullptr) {\n"
        "            outL[i] = sSoh3DEyeHash[0][i];\n"
        "        }\n"
        "        if (outR != nullptr) {\n"
        "            outR[i] = sSoh3DEyeHash[1][i];\n"
        "        }\n"
        "    }\n"
        "}\n"
        "\n"
        "void GfxRenderingAPIMetal::Soh3DEndFrameOffscreen(int eyeIdx) {\n"
        "    // Hash BEFORE the pool index advances at the end of this function.\n"
        "    if (eyeIdx >= 1 && eyeIdx <= 2) {\n"
        "        MTL::Buffer* sohVb = mVertexBufferPool[mCurrentVertexBufferPoolIndex];\n"
        "        const unsigned char* sohBytes =\n"
        "            (sohVb != nullptr) ? (const unsigned char*)sohVb->contents() : nullptr;\n"
        "        unsigned int sohH = 2166136261u; // FNV-1a\n"
        "        if (sohBytes != nullptr) {\n"
        "            for (size_t sohI = 0; sohI < mCurrentVertexBufferOffset; sohI += 64) {\n"
        "                sohH ^= sohBytes[sohI];\n"
        "                sohH *= 16777619u;\n"
        "            }\n"
        "        }\n"
        "        // Fold the length in: two passes can sample identically yet submit\n"
        "        // different amounts of geometry.\n"
        "        sohH ^= (unsigned int)mCurrentVertexBufferOffset;\n"
        "        const int sohE = eyeIdx - 1;\n"
        "        sSoh3DEyeHash[sohE][sSoh3DEyeHashIdx[sohE] & 3] = sohH;\n"
        "        sSoh3DEyeHashIdx[sohE]++;\n"
        "    }\n"
        "    // The pass\'s LAST committed command buffer carries the in-flight\n"
        "    // release, so find it before committing anything (Metal requires\n"
        "    // completion handlers to be attached pre-commit).\n"
        "    int sohLastFbId = -1;\n"
        "    for (int sohScanId : mDrawnFramebuffers) {\n"
        "        if (mFramebuffers[sohScanId].mCommandBuffer != nullptr) {\n"
        "            sohLastFbId = sohScanId;\n"
        "        }\n"
        "    }\n"
        "    for (int sohFbId : mDrawnFramebuffers) {\n"
        "        auto& sohFb = mFramebuffers[sohFbId];\n"
        "        if (sohFb.mCommandBuffer == nullptr) {\n"
        "            continue;\n"
        "        }\n"
        "        if (!sohFb.mHasEndedEncoding && sohFb.mCommandEncoder != nullptr) {\n"
        "            sohFb.mCommandEncoder->endEncoding();\n"
        "        }\n"
        "        if (!sSohIosPendingMips.empty()) {\n"
        "            MTL::BlitCommandEncoder* sohBlit = sohFb.mCommandBuffer->blitCommandEncoder();\n"
        "            for (MTL::Texture* sohTex : sSohIosPendingMips) {\n"
        "                sohBlit->generateMipmaps(sohTex);\n"
        "            }\n"
        "            sohBlit->endEncoding();\n"
        "            for (MTL::Texture* sohTex : sSohIosPendingMips) {\n"
        "                sohTex->release();\n"
        "            }\n"
        "            sSohIosPendingMips.clear();\n"
        "        }\n"
        "        // Attach the completion-publish for the requested eye fb. The\n"
        "        // compositor must never see a texture whose frame is still\n"
        "        // mid-GPU: its unfenced cross-queue blit then captures every pass\n"
        "        // EXCEPT the final one, which is the one holding the ImGui overlay.\n"
        "        // Device-only symptom (the simulator serializes Metal queues), and\n"
        "        // it presents as \"the game renders but the menu never appears\".\n"
        "        if (sohFbId == sSoh3DPublishFb) {\n"
        "            MTL::Texture* sohPubTex = mTextures[sohFb.mTextureId].texture;\n"
        "            int sohPubEye = sSoh3DPublishEye;\n"
        "            sSoh3DPublishFb = -1;\n"
        "            if (sohPubTex != nullptr && sohPubEye >= 0 && sohPubEye <= 1) {\n"
        "                sohPubTex->retain();\n"
        "                sohFb.mCommandBuffer->addCompletedHandler(\n"
        "                    MTL::HandlerFunction([sohPubTex, sohPubEye](MTL::CommandBuffer* sohCb) {\n"
        "                        std::lock_guard<std::mutex> sohLock(sSoh3DPublishMutex);\n"
        "                        void* sohOld = (void*)gSoh3DEyeTexture[sohPubEye];\n"
        "                        gSoh3DEyeTexture[sohPubEye] = (void*)sohPubTex;\n"
        "                        if (sohOld != nullptr) {\n"
        "                            void* sohSlot = sSoh3DRetired[sSoh3DRetiredIdx];\n"
        "                            if (sohSlot != nullptr) {\n"
        "                                ((MTL::Texture*)sohSlot)->release();\n"
        "                            }\n"
        "                            sSoh3DRetired[sSoh3DRetiredIdx] = sohOld;\n"
        "                            sSoh3DRetiredIdx = (sSoh3DRetiredIdx + 1) % 16;\n"
        "                        }\n"
        "                    }));\n"
        "            }\n"
        "        }\n"
        "        // Count the PAIR as in flight once its final buffer is committed,\n"
        "        // and release it when the GPU actually finishes. eyeIdx 2 is the\n"
        "        // second eye, i.e. the end of one stereo pair.\n"
        "        if (eyeIdx == 2 && sohFbId == sohLastFbId) {\n"
        "            sSoh3DInFlight.fetch_add(1, std::memory_order_release);\n"
        "            sohFb.mCommandBuffer->addCompletedHandler(\n"
        "                MTL::HandlerFunction([](MTL::CommandBuffer* sohCb) {\n"
        "                    sSoh3DInFlight.fetch_sub(1, std::memory_order_release);\n"
        "                    sSoh3DThrottleCv.notify_all();\n"
        "                }));\n"
        "        }\n"
        "        sohFb.mCommandBuffer->commit();\n"
        "    }\n"
        "    mDrawnFramebuffers.clear();\n"
        "    mCurrentVertexBufferPoolIndex = (mCurrentVertexBufferPoolIndex + 1) % kMaxVertexBufferPoolSize;\n"
        "    for (int fb_id = 0; fb_id < (int)mFramebuffers.size(); fb_id++) {\n"
        "        FramebufferMetal& fb = mFramebuffers[fb_id];\n"
        "        fb.mLastShaderProgram = nullptr;\n"
        "        fb.mCommandBuffer = nullptr;\n"
        "        fb.mCommandEncoder = nullptr;\n"
        "        fb.mHasEndedEncoding = false;\n"
        "        fb.mHasBoundVertexShader = false;\n"
        "        fb.mHasBoundFragShader = false;\n"
        "        for (int i = 0; i < SHADER_MAX_TEXTURES; i++) {\n"
        "            fb.mLastBoundTextures[i] = nullptr;\n"
        "            fb.mLastBoundSamplers[i] = nullptr;\n"
        "        }\n"
        "        memset(fb.mViewport, 0, sizeof(MTL::Viewport));\n"
        "        memset(fb.mScissorRect, 0, sizeof(MTL::ScissorRect));\n"
        "        fb.mLastDepthTest = -1;\n"
        "        fb.mLastDepthMask = -1;\n"
        "        fb.mLastZmodeDecal = -1;\n"
        "    }\n"
        "    mFrameAutoreleasePool->release();\n"
        "}\n"
        "\n"
        "// Stereo-3D menu: prime ImGui_ImplMetal with an EYE framebuffer's pass\n"
        "// descriptor, then render the built draw data into each eye's still-open\n"
        "// encoder. FramebufferScale is recomputed against the EYE texture because\n"
        "// the SDL2 ImGui backend reports POINTS on iOS.\n"
        "void GfxRenderingAPIMetal::Soh3DImGuiPrime(int fb) {\n"
        "    if (fb < 0 || fb >= (int)mFramebuffers.size()) {\n"
        "        return;\n"
        "    }\n"
        "    MTL::RenderPassDescriptor* sohPass = mFramebuffers[fb].mRenderPassDescriptor;\n"
        "    if (sohPass != nullptr) {\n"
        "        ImGui_ImplMetal_NewFrame(sohPass);\n"
        "    }\n"
        "}\n"
        "\n"
        "extern \"C\" volatile int gSoh3DDbgMenuDraws;\n"
        "\n"
        "void GfxRenderingAPIMetal::Soh3DImGuiRender(int fb, void* drawData) {\n"
        "    if (fb < 0 || fb >= (int)mFramebuffers.size() || drawData == nullptr) {\n"
        "        return;\n"
        "    }\n"
        "    auto& sohFb = mFramebuffers[fb];\n"
        "    if (sohFb.mCommandBuffer == nullptr || sohFb.mCommandEncoder == nullptr || sohFb.mHasEndedEncoding) {\n"
        "        return;\n"
        "    }\n"
        "    ImDrawData* sohData = (ImDrawData*)drawData;\n"
        "    MTL::Texture* sohTex = mTextures[sohFb.mTextureId].texture;\n"
        "    if (sohTex != nullptr && sohData->DisplaySize.x > 0.0f && sohData->DisplaySize.y > 0.0f) {\n"
        "        sohData->FramebufferScale = ImVec2((float)sohTex->width() / sohData->DisplaySize.x,\n"
        "                                           (float)sohTex->height() / sohData->DisplaySize.y);\n"
        "    }\n"
        "    ImGui_ImplMetal_RenderDrawData(sohData, sohFb.mCommandBuffer, sohFb.mCommandEncoder);\n"
        "    gSoh3DDbgMenuDraws = gSoh3DDbgMenuDraws + 1; // telemetry: guards passed\n"
        "}\n"
        "\n"
        "int GfxRenderingAPIMetal::CreateFramebuffer() {\n",
    )],
    "metal-cpp"))

# --- interpreter header ------------------------------------------------------
diffs.append(gen(
    "include/fast/interpreter.h",
    [(
        "    void EndFrame();\n",
        "    void EndFrame();\n"
        "    // LIGHTHOUSE_IOS (overlay 0031): stereoscopic eye passes (visionOS 3D).\n"
        "    void Soh3DSetEye(int eye);\n"
        "    void Soh3DEndEyePass(int eye);\n"
        "    int Soh3DGetEyeFb(int eye);\n"
        "    // Frame-rate pacing WITHOUT the present path: EndFrame() would call\n"
        "    // the rendering API's EndFrame, which needs the screen swapchain we\n"
        "    // deliberately never touch in 3D. The pacing sleep still has to run.\n"
        "    void Soh3DPaceFrame();\n"
        "    // Blocks until fewer than N eye-pass pairs are in flight on the GPU.\n"
        "    void Soh3DThrottle();\n",
    ), (
        "    int mGameFbMsaaResolved{}; // game_framebuffer_msaa_resolved;\n",
        "    int mGameFbMsaaResolved{}; // game_framebuffer_msaa_resolved;\n"
        "    // LIGHTHOUSE_IOS (overlay 0031): per-eye framebuffers + saved 2D state.\n"
        "    // RING-BUFFERED per eye -- the compositor blit-copies the published\n"
        "    // texture from its own queue with NO cross-queue fence, so the engine\n"
        "    // must not re-render into a texture the compositor may still be\n"
        "    // reading.\n"
        "    //\n"
        "    // DEPTH 4, not the flagship's 2, and this is a genuine port\n"
        "    // divergence rather than padding. The flagship renders both eyes once\n"
        "    // per HOST frame, so a 2-deep ring is reused every other frame.\n"
        "    // Lighthouse renders them once per interpolated SUB-FRAME (overlay\n"
        "    // 0042; 4 sub-frames per 30 Hz tick at 120 fps), so a 2-deep ring is\n"
        "    // reused every ~16 ms while the compositor -- running at 90 Hz and\n"
        "    // blitting asynchronously -- may still be copying it. Device symptom:\n"
        "    // objects flickering rapidly between two positions.\n"
        "    // At depth 4 a buffer is not reused for ~33 ms, comfortably past a\n"
        "    // compositor frame. Cost is GPU memory: 4 x 2 x eye-size.\n"
        "    static constexpr int kSoh3DEyeRing = 4;\n"
        "    int mSoh3DEyeFb[2][kSoh3DEyeRing] = { { -1, -1, -1, -1 }, { -1, -1, -1, -1 } };\n"
        "    int mSoh3DPing = 0;\n"
        "    int mSoh3DSavedGameFb = -1;\n"
        "    uint32_t mSoh3DSavedW = 0, mSoh3DSavedH = 0;\n"
        "    float mSoh3DSavedAspect = 0.0f;\n",
    )],
    "interp-h"))

# --- interpreter impl --------------------------------------------------------
interp_edits = []

# Stereo state + shell externs, placed before the matrix code that reads them.
interp_edits.append((
    "void Interpreter::MatrixMul(float res[4][4], const float a[4][4], const float b[4][4]) {\n",
    "#ifdef __IOS__\n"
    "#include <CoreFoundation/CFBase.h>\n"
    "// LIGHTHOUSE_IOS (overlay 0031): stereoscopic eye state. The shell owns the\n"
    "// mode flag and the publish targets (SohIosShell.m -- also linked on iPhone,\n"
    "// where the mode simply never leaves 0). sSoh3DEye is set around each pass.\n"
    "// extern \"C\" at FILE scope: a block-scope extern namespace-mangles, which is\n"
    "// this family's recurring linkage trap.\n"
    "extern \"C\" {\n"
    "extern volatile int gSoh3DMode;\n"
    "extern void* volatile gSoh3DEyeTexture[2];\n"
    "extern volatile int gSoh3DEyeFrames[2];\n"
    "extern volatile int gSoh3DEyeW, gSoh3DEyeH;\n"
    "extern volatile float gSoh3DCamDist, gSoh3DCamP00;\n"
    "extern volatile float gSoh3DCamRight[3], gSoh3DCamFwd[3];\n"
    "// Still exported by 0032 and reported on the bridge, but deliberately NOT\n"
    "// used by the fold below -- see the convergence-skew comment.\n"
    "extern volatile float gSoh3DCamEye[3];\n"
    "extern volatile float gSoh3DDbgConv, gSoh3DDbgSep;\n"
    "extern volatile int gSoh3DPaused;\n"
    "extern volatile int gSoh3DAiming;\n"
    "extern volatile int gSoh3DInPlay;\n"
    "extern volatile int gSoh3DDbg2DW, gSoh3DDbg2DH;\n"
    "extern volatile int gSoh3DDbgCurW, gSoh3DDbgCurH;\n"
    "}\n"
    "static int sSoh3DEye = 0;\n"
    "static float sSoh3DSep = 3.0f;\n"
    "static float sSoh3DConv = 300.0f;\n"
    "static float sSoh3DConvSmooth = 300.0f;\n"
    "static float sSoh3DPauseMix = 1.0f; // 1 = full stereo, 0 = flat (paused)\n"
    "static float sSoh3DAimMix = 0.0f;   // 1 = first-person (BK's egg/eye view)\n"
    "static int sSoh3DSkybox = 0;        // inside a skybox draw (0033 sentinels)\n"
    "static float sSoh3DP[4][4];\n"
    "// Off-axis stereo for a COMBINED viewing*projection matrix. N64 ports feed\n"
    "// Fast3D the combined matrix in G_MTX_PROJECTION (the modelview stack is\n"
    "// world space), so a naive eye translation cannot be injected and the camera\n"
    "// axes cannot be recovered from the matrix either -- the GAME exports its\n"
    "// basis instead (overlay 0032, from BK's viewport_update).\n"
    "//   Camera shift:      world translation by -e*right, folded into the matrix.\n"
    "//   Convergence skew:  clip_x += s*view_z, with view_z = dot(v,fwd) -\n"
    "//                      dot(eye,fwd) and s = e*P00/C, so parallax is exactly\n"
    "//                      zero at the focus distance C.\n"
    "//   Perspective detect: the combined matrix's w-column top-3 is (+/-)forward\n"
    "//                      and therefore unit length; an orthographic matrix has\n"
    "//                      (0,0,0) there. HUD and 2D passes thus stay perfectly\n"
    "//                      on the panel plane with no special-casing.\n"
    "// Per-reason counters (rev5). M-032 proved only ONE sub-frame per tick gets\n"
    "// stereo -- the other passes submit byte-identical geometry to both eyes --\n"
    "// but not WHY. There is no unpatched MP composition site, so the fold IS\n"
    "// reached; it must be bailing. These say which branch, in one run.\n"
    "static unsigned int sSoh3DSpCall, sSoh3DSpOrtho, sSoh3DSpZero, sSoh3DSpAppl;\n"
    "static float sSoh3DSpLastSep, sSoh3DSpLastMix;\n"
    "\n"
    "extern \"C\" void SohIos_Stereo3DFoldStats(unsigned int* c, unsigned int* o, unsigned int* z,\n"
    "                                          unsigned int* a, float* sep, float* mix) {\n"
    "    if (c) { *c = sSoh3DSpCall; }\n"
    "    if (o) { *o = sSoh3DSpOrtho; }\n"
    "    if (z) { *z = sSoh3DSpZero; }\n"
    "    if (a) { *a = sSoh3DSpAppl; }\n"
    "    if (sep) { *sep = sSoh3DSpLastSep; }\n"
    "    if (mix) { *mix = sSoh3DSpLastMix; }\n"
    "    sSoh3DSpCall = sSoh3DSpOrtho = sSoh3DSpZero = sSoh3DSpAppl = 0;\n"
    "}\n"
    "\n"
    "static void Soh3DStereoP(float out[4][4], const float in[4][4]) {\n"
    "    sSoh3DSpCall++;\n"
    "    sSoh3DSpLastSep = sSoh3DSep;\n"
    "    sSoh3DSpLastMix = sSoh3DPauseMix;\n"
    "    memcpy(out, in, sizeof(sSoh3DP));\n"
    "    float sohWx = in[0][3], sohWy = in[1][3], sohWz = in[2][3];\n"
    "    // PERSPECTIVE DETECT -- threshold is 0.01, NOT the flagship's 0.25.\n"
    "    //\n"
    "    // guPerspective's last argument scales the whole matrix, and the w-column\n"
    "    // scales with it. OoT passes view->scale (1.0), so |w| = 1 and |w|^2 = 1.0,\n"
    "    // comfortably clear of 0.25. **Banjo-Kazooie hardcodes 0.5f**\n"
    "    // (core1/viewport.c: guPerspective(..., 0.5f)), so |w| = 0.5 and\n"
    "    // |w|^2 = 0.25 -- EXACTLY the flagship's threshold. Float noise and the\n"
    "    // three rotation MULs that follow push it either side of the line\n"
    "    // essentially at random, so the fold applied on some frames and not\n"
    "    // others: measured 155160 ortho-rejections out of 164160 calls in\n"
    "    // gameplay, with sep and pauseMix both healthy (M-032/M-033).\n"
    "    //\n"
    "    // On screen that is a stereo-offset frame and a flat frame alternating at\n"
    "    // tick rate -- objects snapping between two horizontal positions, visible\n"
    "    // with ONE EYE CLOSED, which is what finally identified it.\n"
    "    //\n"
    "    // A true orthographic matrix has an EXACTLY zero w-column, so the test\n"
    "    // only has to separate 0 from 0.5. 0.01 (|w| > 0.1) sits 25x below BK's\n"
    "    // value and far above zero, and would still be correct for OoT's 1.0.\n"
    "    if (sohWx * sohWx + sohWy * sohWy + sohWz * sohWz < 0.01f) {\n"
    "        sSoh3DSpOrtho++;\n"
    "        return; // orthographic (HUD/2D): stays on the panel plane\n"
    "    }\n"
    "    const float e = (sSoh3DEye == 2 ? 1.0f : -1.0f) * 0.5f * sSoh3DSep * sSoh3DPauseMix;\n"
    "    if (e == 0.0f) {\n"
    "        sSoh3DSpZero++;\n"
    "        return;\n"
    "    }\n"
    "    sSoh3DSpAppl++;\n"
    "    // Skybox draws (0033 sentinels): a camera-centred skybox CANCELS the eye\n"
    "    // translation, so it would render at zero disparity -- i.e. sky ON the\n"
    "    // panel, stereoscopically IN FRONT of the terrain behind it. Skew-only\n"
    "    // puts it at infinity where it belongs.\n"
    "    if (!sSoh3DSkybox) {\n"
    "        const float dx = -e * gSoh3DCamRight[0];\n"
    "        const float dy = -e * gSoh3DCamRight[1];\n"
    "        const float dz = -e * gSoh3DCamRight[2];\n"
    "        for (int j = 0; j < 4; j++) {\n"
    "            out[3][j] += dx * out[0][j] + dy * out[1][j] + dz * out[2][j];\n"
    "        }\n"
    "    }\n"
    "    const float s = e * gSoh3DCamP00 / sSoh3DConv;\n"
    "    // CONVERGENCE SKEW -- and the ONE place this port must NOT copy the\n"
    "    // flagship. The skew wants clip_x += s * view_depth. The flagship writes\n"
    "    // that as s * (dot(v,fwd) - dot(eye,fwd)), which is correct for OoT\n"
    "    // because OoT feeds Fast3D a COMBINED viewing*projection matrix, so the\n"
    "    // vertices reaching P are WORLD-ABSOLUTE and the camera position has to\n"
    "    // be subtracted back out.\n"
    "    //\n"
    "    // Banjo-Kazooie is the opposite convention: it renders CAMERA-RELATIVE.\n"
    "    // viewport_setRenderPerspectiveMatrix (core1/viewport.c) loads perspective\n"
    "    // and MULs roll/pitch/yaw into G_MTX_PROJECTION -- rotation only, no camera\n"
    "    // translation -- and then LOADS an identity modelview. The camera offset is\n"
    "    // baked per object instead: modelRender.c calls func_80252AF0(cameraPos,\n"
    "    // objectPos, ...) which translates by (objectPos - cameraPos)\n"
    "    // (core1/mlmtx.c:526-538). So the coordinates entering P are ALREADY\n"
    "    // camera-relative and dot(v,fwd) alone IS the view depth.\n"
    "    //\n"
    "    // Subtracting dot(eye,fwd) here therefore adds a constant clip-x offset of\n"
    "    // -s*dot(eye,fwd), with OPPOSITE SIGN PER EYE, proportional to the camera\'s\n"
    "    // ABSOLUTE world position -- and BK levels span +/-16000 units. At shipped\n"
    "    // defaults that is 3% of screen width at 500 units from the origin and 33%\n"
    "    // at 5000. Nothing near that can fuse; the two images rival and the viewer\n"
    "    // sees objects snapping between two positions. Device symptom, three\n"
    "    // builds running: \"flickers between left right left right\".\n"
    "    //\n"
    "    // The camera-SHIFT half above needs no such correction: a translation\n"
    "    // along right is translation-invariant, so it is already right for\n"
    "    // camera-relative input.\n"
    "    out[0][0] += s * gSoh3DCamFwd[0];\n"
    "    out[1][0] += s * gSoh3DCamFwd[1];\n"
    "    out[2][0] += s * gSoh3DCamFwd[2];\n"
    "}\n"
    "#endif\n"
    "\n"
    "void Interpreter::MatrixMul(float res[4][4], const float a[4][4], const float b[4][4]) {\n",
))

# G_NOOP sentinels from 0033: skybox begin/end.
interp_edits.append((
    "    uint32_t p = C0(16, 8);\n"
    "    uint32_t l = C0(0, 16);\n"
    "    if (p == 7) {\n",
    "    uint32_t p = C0(16, 8);\n"
    "    uint32_t l = C0(0, 16);\n"
    "#ifdef __IOS__\n"
    "    // LIGHTHOUSE_IOS (overlay 0031): overlay 0033's skybox stereo sentinels.\n"
    "    if (p == 0x5A) {\n"
    "        sSoh3DSkybox = 1;\n"
    "    } else if (p == 0x5B) {\n"
    "        sSoh3DSkybox = 0;\n"
    "    }\n"
    "#endif\n"
    "    if (p == 7) {\n",
))

# MP composition site 1: GfxSpMatrix tail.
interp_edits.append((
    "        mRsp->lights_changed = 1;\n"
    "    }\n"
    "    MatrixMul(mRsp->MP_matrix, mRsp->modelview_matrix_stack[mRsp->modelview_matrix_stack_size - 1], mRsp->P_matrix);\n"
    "}\n",
    "        mRsp->lights_changed = 1;\n"
    "    }\n"
    "#ifdef __IOS__\n"
    "    if (sSoh3DEye != 0) {\n"
    "        Soh3DStereoP(sSoh3DP, mRsp->P_matrix);\n"
    "        MatrixMul(mRsp->MP_matrix, mRsp->modelview_matrix_stack[mRsp->modelview_matrix_stack_size - 1], sSoh3DP);\n"
    "    } else\n"
    "#endif\n"
    "    MatrixMul(mRsp->MP_matrix, mRsp->modelview_matrix_stack[mRsp->modelview_matrix_stack_size - 1], mRsp->P_matrix);\n"
    "}\n",
))

# MP composition site 2: GfxSpPopMatrix.
interp_edits.append((
    "                MatrixMul(mRsp->MP_matrix, mRsp->modelview_matrix_stack[mRsp->modelview_matrix_stack_size - 1],\n"
    "                          mRsp->P_matrix);\n",
    "#ifdef __IOS__\n"
    "                if (sSoh3DEye != 0) {\n"
    "                    Soh3DStereoP(sSoh3DP, mRsp->P_matrix);\n"
    "                    MatrixMul(mRsp->MP_matrix, mRsp->modelview_matrix_stack[mRsp->modelview_matrix_stack_size - 1],\n"
    "                              sSoh3DP);\n"
    "                } else\n"
    "#endif\n"
    "                MatrixMul(mRsp->MP_matrix, mRsp->modelview_matrix_stack[mRsp->modelview_matrix_stack_size - 1],\n"
    "                          mRsp->P_matrix);\n",
))

# StartFrame: eye passes render offscreen at the eye size, dims overridden.
interp_edits.append((
    "    } else {\n"
    "        mRendersToFb = false;\n"
    "    }\n"
    "\n"
    "    mFbActive = false;\n",
    "    } else {\n"
    "        mRendersToFb = false;\n"
    "    }\n"
    "\n"
    "#ifdef __IOS__\n"
    "    // LIGHTHOUSE_IOS (overlay 0031): an eye pass renders offscreen at the\n"
    "    // panel's render size regardless of the (hidden) window. These dims drive\n"
    "    // the viewport and the game aspect; Soh3DSetEye(0) restores the saved 2D\n"
    "    // values. Note this deliberately bypasses overlay 0036's SSAA scaling:\n"
    "    // the eye size IS the render size here, there is no window to scale from.\n"
    "    if (sSoh3DEye != 0) {\n"
    "        uint32_t sohEw = gSoh3DEyeW > 0 ? (uint32_t)gSoh3DEyeW : 3840u;\n"
    "        uint32_t sohEh = gSoh3DEyeH > 0 ? (uint32_t)gSoh3DEyeH : 2160u;\n"
    "        mRendersToFb = true;\n"
    "        mCurDimensions.width = sohEw;\n"
    "        mCurDimensions.height = sohEh;\n"
    "        mCurDimensions.aspect_ratio = (float)sohEw / (float)sohEh;\n"
    "        mRapi->UpdateFramebufferParameters(mGameFb, sohEw, sohEh, 1, true, true, true, true);\n"
    "    }\n"
    "#endif\n"
    "    mFbActive = false;\n",
))

# Run head: never touch fb 0 (screen) during an eye pass; publish live dims.
interp_edits.append((
    "    mCurMtxReplacements = &mtx_replacements;\n"
    "\n"
    "    mRapi->UpdateFramebufferParameters(0, mGfxCurrentWindowDimensions.width, mGfxCurrentWindowDimensions.height, 1,\n"
    "                                       false, true, true, !mRendersToFb);\n",
    "    mCurMtxReplacements = &mtx_replacements;\n"
    "\n"
    "#ifdef __IOS__\n"
    "    // LIGHTHOUSE_IOS (overlay 0031): defensive per-Run clear of the skybox\n"
    "    // flag, plus the engine's live dimensions for the `drawable` bridge\n"
    "    // command. mCurDimensions is the render canvas in PIXELS;\n"
    "    // mGfxCurrentWindowDimensions is the window in POINTS. A healthy 2D build\n"
    "    // has cur == drawable x ssaa; cur < drawable means the sub-native trap\n"
    "    // (playbook 1.4a) is back, and without these two lines there is no way to\n"
    "    // tell -- which cost the flagship two device rounds.\n"
    "    sSoh3DSkybox = 0;\n"
    "    gSoh3DDbg2DW = (int)mGfxCurrentWindowDimensions.width;\n"
    "    gSoh3DDbg2DH = (int)mGfxCurrentWindowDimensions.height;\n"
    "    gSoh3DDbgCurW = (int)mCurDimensions.width;\n"
    "    gSoh3DDbgCurH = (int)mCurDimensions.height;\n"
    "    // fb 0 is the screen -- never touched in 3D (the hidden window's\n"
    "    // layer/drawable must stay unpoked).\n"
    "    if (sSoh3DEye == 0)\n"
    "#endif\n"
    "    mRapi->UpdateFramebufferParameters(0, mGfxCurrentWindowDimensions.width, mGfxCurrentWindowDimensions.height, 1,\n"
    "                                       false, true, true, !mRendersToFb);\n",
))

# Run tail: skip the fb0 bind/clear + resolve; the eye texture publishes later.
interp_edits.append((
    "    Flush();\n"
    "    mGfxFrameBuffer = 0;\n"
    "    currentDir = std::stack<std::string>();\n"
    "\n"
    "    if (mRendersToFb) {\n"
    "        mRapi->StartDrawToFramebuffer(0, 1);\n"
    "        mRapi->ClearFramebuffer(true, true);\n",
    "    Flush();\n"
    "    mGfxFrameBuffer = 0;\n"
    "    currentDir = std::stack<std::string>();\n"
    "\n"
    "#ifdef __IOS__\n"
    "    if (sSoh3DEye != 0 && mRendersToFb) {\n"
    "        // Eye pass: no fb 0 (its texture is a stale drawable in 3D). MSAA is\n"
    "        // forced off for eye framebuffers; Soh3DEndEyePass publishes the\n"
    "        // texture after the offscreen flush.\n"
    "        mGfxFrameBuffer = (uintptr_t)mRapi->GetFramebufferTextureId(mGameFb);\n"
    "    } else\n"
    "#endif\n"
    "    if (mRendersToFb) {\n"
    "        mRapi->StartDrawToFramebuffer(0, 1);\n"
    "        mRapi->ClearFramebuffer(true, true);\n",
))

# Eye-pass API, after EndFrame.
interp_edits.append((
    "void Interpreter::EndFrame() {\n"
    "    mRapi->EndFrame();\n"
    "    mWapi->SwapBuffersBegin();\n"
    "    mRapi->FinishRender();\n"
    "    mWapi->SwapBuffersEnd();\n"
    "}\n",
    "void Interpreter::EndFrame() {\n"
    "    mRapi->EndFrame();\n"
    "    mWapi->SwapBuffersBegin();\n"
    "    mRapi->FinishRender();\n"
    "    mWapi->SwapBuffersEnd();\n"
    "}\n"
    "\n"
    "#ifdef __IOS__\n"
    "// LIGHTHOUSE_IOS (overlay 0031): eye-pass control. SetEye(1|2) swaps mGameFb\n"
    "// to that eye's framebuffer (created lazily) -- every existing mGameFb\n"
    "// consumer then targets it transparently; SetEye(0) restores 2D state.\n"
    "void Interpreter::Soh3DSetEye(int eye) {\n"
    "    if (eye == 0) {\n"
    "        if (mSoh3DSavedGameFb >= 0) {\n"
    "            mGameFb = mSoh3DSavedGameFb;\n"
    "        }\n"
    "        if (mSoh3DSavedW > 0) {\n"
    "            mCurDimensions.width = mSoh3DSavedW;\n"
    "            mCurDimensions.height = mSoh3DSavedH;\n"
    "            mCurDimensions.aspect_ratio = mSoh3DSavedAspect;\n"
    "        }\n"
    "        sSoh3DEye = 0;\n"
    "        return;\n"
    "    }\n"
    "    if (mSoh3DEyeFb[0][0] < 0) {\n"
    "        for (int sohE = 0; sohE < 2; sohE++) {\n"
    "            for (int sohB = 0; sohB < kSoh3DEyeRing; sohB++) {\n"
    "                mSoh3DEyeFb[sohE][sohB] = mRapi->CreateFramebuffer();\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "    if (eye == 1) {\n"
    "        // Advance the ring once per rendered frame (a SUB-frame here).\n"
    "        mSoh3DPing = (mSoh3DPing + 1) % kSoh3DEyeRing;\n"
    "    }\n"
    "    if (sSoh3DEye == 0) {\n"
    "        mSoh3DSavedGameFb = mGameFb;\n"
    "        mSoh3DSavedW = mCurDimensions.width;\n"
    "        mSoh3DSavedH = mCurDimensions.height;\n"
    "        mSoh3DSavedAspect = mCurDimensions.aspect_ratio;\n"
    "    }\n"
    "    // Adaptive convergence: zero parallax is anchored on the camera's live\n"
    "    // focus distance (overlay 0032's export, smoothed) so the subject sits ON\n"
    "    // the panel in every scene, and the eye offset is PROPORTIONAL to it, so\n"
    "    // background disparity (e/C) is scene-constant instead of swinging with\n"
    "    // camera distance. Depth is e/C as a fraction; ConvBias biases the anchor\n"
    "    // for taste. BK's camera-to-Banjo distance lives around 400-700 game\n"
    "    // units; the clamp is deliberately wide because cutscene and static\n"
    "    // cameras leave that range legitimately, and the fallback is a mid value\n"
    "    // rather than a guess at zero.\n"
    "    if (eye == 1) { // smooth once per host frame\n"
    "        float sohCamD = gSoh3DCamDist;\n"
    "        if (!(sohCamD >= 30.0f && sohCamD <= 3000.0f)) {\n"
    "            sohCamD = 500.0f;\n"
    "        }\n"
    "        sSoh3DConvSmooth += (sohCamD - sSoh3DConvSmooth) * 0.08f;\n"
    "        // Flat outside gameplay: the file-select and title screens draw their\n"
    "        // own perspective backgrounds, which fight the panel plane and read\n"
    "        // as doubles.\n"
    "        float sohStereoOn = (gSoh3DPaused || !gSoh3DInPlay) ? 0.0f : 1.0f;\n"
    "        sSoh3DPauseMix += (sohStereoOn - sSoh3DPauseMix) * 0.15f;\n"
    "        if (sSoh3DPauseMix < 0.02f) {\n"
    "            sSoh3DPauseMix = 0.0f;\n"
    "        }\n"
    "        sSoh3DAimMix += ((gSoh3DAiming ? 1.0f : 0.0f) - sSoh3DAimMix) * 0.15f;\n"
    "    }\n"
    "    auto sohCvars = Ship::Context::GetRawInstance()->GetConsoleVariables();\n"
    "    // Depth default 0.024375, NOT the flagship 0.0325. The user rescaled\n"
    "    // the slider so the OLD 150% became the new 200% maximum, which makes\n"
    "    // the new 100% equal the old 75% -- the value they had settled on by\n"
    "    // hand. 0.0325 * 0.75 = 0.024375. SohVisionApp.swift applies the same\n"
    "    // factor, so an unset CVar and a slider at 100% agree exactly.\n"
    "    float sohDepth = sohCvars->GetFloat(\"gSohIos.StereoDepth\", 0.024375f);\n"
    "    float sohBias = sohCvars->GetFloat(\"gSohIos.StereoConvBias\", 1.0f);\n"
    "    // First-person profile: BK's egg-shooting / look-around view puts the\n"
    "    // camera inside Banjo, where full depth reads as double vision. Depth 30%\n"
    "    // and focus 50%, smoothly blended (the flagship's device-tuned numbers for\n"
    "    // the equivalent slingshot/bow camera).\n"
    "    float sohAimDepthMul = 1.0f - 0.7f * sSoh3DAimMix;\n"
    "    float sohAimBiasMul = 1.0f - 0.5f * sSoh3DAimMix;\n"
    "    sSoh3DConv = sSoh3DConvSmooth * sohBias * sohAimBiasMul;\n"
    "    sSoh3DSep = sohDepth * sohAimDepthMul * sSoh3DConv;\n"
    "    gSoh3DDbgConv = sSoh3DConv;\n"
    "    gSoh3DDbgSep = sSoh3DSep;\n"
    "    mGameFb = mSoh3DEyeFb[eye - 1][mSoh3DPing];\n"
    "    sSoh3DEye = eye;\n"
    "}\n"
    "\n"
    "int Interpreter::Soh3DGetEyeFb(int eye) {\n"
    "    return (eye >= 1 && eye <= 2) ? mSoh3DEyeFb[eye - 1][mSoh3DPing] : -1;\n"
    "}\n"
    "\n"
    "void Interpreter::Soh3DEndEyePass(int eye) {\n"
    "    // Publish ON GPU COMPLETION, never at commit -- the compositor\n"
    "    // blit-copies the published texture from its own queue with no\n"
    "    // cross-queue fence, and a mid-frame capture contains every pass EXCEPT\n"
    "    // the final one (which holds the ImGui overlay): game visible, menu\n"
    "    // never. Device-only; the simulator serializes queues and hides it.\n"
    "    if (eye >= 1 && eye <= 2 && mSoh3DEyeFb[eye - 1][mSoh3DPing] >= 0) {\n"
    "        mRapi->Soh3DPublishOnComplete(mSoh3DEyeFb[eye - 1][mSoh3DPing], eye - 1);\n"
    "    }\n"
    "    mRapi->Soh3DEndFrameOffscreen(eye);\n"
    "    mRapi->FinishRender();\n"
    "    if (eye >= 1 && eye <= 2) {\n"
    "        gSoh3DEyeFrames[eye - 1] = gSoh3DEyeFrames[eye - 1] + 1;\n"
    "    }\n"
    "}\n"
    "\n"
    "void Interpreter::Soh3DThrottle() {\n"
    "    mRapi->Soh3DThrottle();\n"
    "}\n"
    "\n"
    "void Interpreter::Soh3DPaceFrame() {\n"
    "    // The window manager's swap pair is where the frame-rate limiter\n"
    "    // sleeps. In 3D there is nothing to present -- the compositor paces\n"
    "    // itself off the drawable -- but the ENGINE still needs its cadence, so\n"
    "    // run the pair without mRapi->EndFrame()'s screen work.\n"
    "    mWapi->SwapBuffersBegin();\n"
    "    mWapi->SwapBuffersEnd();\n"
    "}\n"
    "#endif\n",
))

diffs.append(gen("src/fast/interpreter.cpp", interp_edits, "interp-cpp"))

out = ROOT / "overlay/patches/0031-lighthouse-visionos-stereo-eyes.patch"
out.write_text(__doc__ + "\n" + "".join(diffs))
print(f"wrote {out}")
