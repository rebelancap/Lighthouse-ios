#!/usr/bin/env python3
"""Overlay patch 0035: async Metal shader compilation (2ship cross-port
finding, docs/FINDINGS-FROM-2SHIP-async-shader-stutter.md; their D-008,
device-validated 3/3). Cold `newLibrary` runs 120-160 ms per shader on
A-series — the recurring ~185 ms field stalls and area-transition freezes.
Register the program shell synchronously (draw path binds it immediately),
run newLibrary + newRenderPipelineState on a detached thread (CVar
gSohIos.AsyncShaders, default ON; live kill switch via bridge `cvar`), and
skip draws whose pipeline isn't ready yet (geometry pops in a few frames
later). Per-window lib/pso compile ledgers feed the 0008 perf line
(lib_ms/lib_n/pso_ms/pso_n) — attribution and the fix's own before/after.
__IOS__-only; the macOS oracle keeps the pristine synchronous path."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/fast/backends/gfx_metal.cpp"
REL = "libultraship/src/fast/backends/gfx_metal.cpp"
orig = SRC.read_text()

def replace_once(text, old, new, tag):
    n = text.count(old)
    assert n == 1, f"[{tag}] expected 1 match, got {n}"
    return text.replace(old, new)

t = orig

# --- 1. Compile ledgers + thread include, anchored just above the shader
# factory (disjoint from 0019/0020/0028 hunks by construction).
t = replace_once(t,
    "struct ShaderProgram* GfxRenderingAPIMetal::CreateAndLoadNewShader(uint64_t shader_id0, uint64_t shader_id1) {",
    """#ifdef __IOS__
#include <atomic>
#include <chrono>
#include <thread>
// SOH_IOS (overlay 0035): per-window shader-compile ledgers, reset on read
// by the 0008 perf probe (lib_ms/lib_n/pso_ms/pso_n). newLibrary (MSL
// source -> library) is ~70% of a cold compile, newRenderPipelineState
// ~25% (2ship attribution) -- time BOTH or the stall stays unexplained.
static std::atomic<uint64_t> sSohIosLibWinUs{ 0 };
static std::atomic<uint32_t> sSohIosLibWinCount{ 0 };
extern "C" void SohIos_LibWindowStats(float* ms, unsigned int* count) {
    *ms = (float)(sSohIosLibWinUs.exchange(0) / 1000.0);
    *count = sSohIosLibWinCount.exchange(0);
}
static std::atomic<uint64_t> sSohIosPsoWinUs{ 0 };
static std::atomic<uint32_t> sSohIosPsoWinCount{ 0 };
extern "C" void SohIos_PsoWindowStats(float* ms, unsigned int* count) {
    *ms = (float)(sSohIosPsoWinUs.exchange(0) / 1000.0);
    *count = sSohIosPsoWinCount.exchange(0);
}
#endif

struct ShaderProgram* GfxRenderingAPIMetal::CreateAndLoadNewShader(uint64_t shader_id0, uint64_t shader_id1) {""",
    "ledgers")

# --- 2. The async split. Replace everything between the vertex-descriptor
# build and the function's return with the register-then-compile structure.
OLD_BODY = """    MTL::VertexDescriptor* vertex_descriptor =
        gfx_metal_build_shader(buf, numFloats, cc_features, mCurrentFilterMode == FILTER_THREE_POINT);

    NS::Error* error = nullptr;
    MTL::Library* library =
        mDevice->newLibrary(NS::String::string(buf.data(), NS::UTF8StringEncoding), nullptr, &error);

    if (error != nullptr)
        SPDLOG_ERROR("Failed to compile shader library, error {}",
                     error->localizedDescription()->cString(NS::UTF8StringEncoding));

    MTL::RenderPipelineDescriptor* pipeline_descriptor = MTL::RenderPipelineDescriptor::alloc()->init();
    MTL::Function* vertexFunc = library->newFunction(NS::String::string("vertexShader", NS::UTF8StringEncoding));
    MTL::Function* fragmentFunc = library->newFunction(NS::String::string("fragmentShader", NS::UTF8StringEncoding));

    pipeline_descriptor->setVertexFunction(vertexFunc);
    pipeline_descriptor->setFragmentFunction(fragmentFunc);
    pipeline_descriptor->setVertexDescriptor(vertex_descriptor);

    pipeline_descriptor->colorAttachments()->object(0)->setPixelFormat(mSrgbMode ? MTL::PixelFormatBGRA8Unorm_sRGB
                                                                                 : MTL::PixelFormatBGRA8Unorm);
    pipeline_descriptor->setDepthAttachmentPixelFormat(MTL::PixelFormatDepth32Float);
    if (cc_features.opt_alpha) {
        pipeline_descriptor->colorAttachments()->object(0)->setBlendingEnabled(true);
        pipeline_descriptor->colorAttachments()->object(0)->setSourceRGBBlendFactor(MTL::BlendFactorSourceAlpha);
        pipeline_descriptor->colorAttachments()->object(0)->setDestinationRGBBlendFactor(
            MTL::BlendFactorOneMinusSourceAlpha);
        pipeline_descriptor->colorAttachments()->object(0)->setRgbBlendOperation(MTL::BlendOperationAdd);
        pipeline_descriptor->colorAttachments()->object(0)->setSourceAlphaBlendFactor(MTL::BlendFactorZero);
        pipeline_descriptor->colorAttachments()->object(0)->setDestinationAlphaBlendFactor(MTL::BlendFactorOne);
        pipeline_descriptor->colorAttachments()->object(0)->setAlphaBlendOperation(MTL::BlendOperationAdd);
        pipeline_descriptor->colorAttachments()->object(0)->setWriteMask(MTL::ColorWriteMaskAll);
    } else {
        pipeline_descriptor->colorAttachments()->object(0)->setBlendingEnabled(false);
        pipeline_descriptor->colorAttachments()->object(0)->setWriteMask(MTL::ColorWriteMaskAll);
    }

    struct ShaderProgramMetal* prg = &mShaderProgramPool[std::make_pair(shader_id0, shader_id1)];
    prg->shader_id0 = shader_id0;
    prg->shader_id1 = shader_id1;
    prg->usedTextures[0] = cc_features.usedTextures[0];
    prg->usedTextures[1] = cc_features.usedTextures[1];
    prg->usedTextures[2] = cc_features.used_masks[0];
    prg->usedTextures[3] = cc_features.used_masks[1];
    prg->usedTextures[4] = cc_features.used_blend[0];
    prg->usedTextures[5] = cc_features.used_blend[1];
    prg->numInputs = cc_features.numInputs;
    prg->numFloats = numFloats;

    // Prepoluate pipeline state cache with program and available msaa levels
    for (int i = 0; i < ARRAY_COUNT(mMsaaNumQualityLevels); i++) {
        if (mMsaaNumQualityLevels[i] == 1) {
            int msaa_level = i + 1;
            pipeline_descriptor->setSampleCount(msaa_level);
            MTL::RenderPipelineState* pipeline_state = mDevice->newRenderPipelineState(pipeline_descriptor, &error);

            if (!pipeline_state || error != nullptr) {
                // Pipeline State creation could fail if we haven't properly set up our pipeline descriptor.
                // If the Metal API validation is enabled, we can find out more information about what
                // went wrong.  (Metal API validation is enabled by default when a debug build is run
                // from Xcode)
                SPDLOG_ERROR("Failed to create pipeline state, error {}",
                             error->localizedDescription()->cString(NS::UTF8StringEncoding));
            }

            prg->pipeline_state_variants[msaa_level] = pipeline_state;
        }
    }

    LoadShader((struct ShaderProgram*)prg);

    vertexFunc->release();
    fragmentFunc->release();
    library->release();
    pipeline_descriptor->release();
    autorelease_pool->release();

    return (struct ShaderProgram*)prg;
}"""

NEW_BODY = """    MTL::VertexDescriptor* vertex_descriptor =
        gfx_metal_build_shader(buf, numFloats, cc_features, mCurrentFilterMode == FILTER_THREE_POINT);

#ifdef __IOS__
    // SOH_IOS (overlay 0035): register the program shell + the fields the
    // draw path reads synchronously; the expensive compile fills the
    // pipeline variants below (inline, or on a background thread when
    // gSohIos.AsyncShaders is on). The pool element's address is stable
    // across rehash (unordered_map), so prg crosses the thread safely.
    struct ShaderProgramMetal* prg = &mShaderProgramPool[std::make_pair(shader_id0, shader_id1)];
    prg->shader_id0 = shader_id0;
    prg->shader_id1 = shader_id1;
    prg->usedTextures[0] = cc_features.usedTextures[0];
    prg->usedTextures[1] = cc_features.usedTextures[1];
    prg->usedTextures[2] = cc_features.used_masks[0];
    prg->usedTextures[3] = cc_features.used_masks[1];
    prg->usedTextures[4] = cc_features.used_blend[0];
    prg->usedTextures[5] = cc_features.used_blend[1];
    prg->numInputs = cc_features.numInputs;
    prg->numFloats = numFloats;
    for (size_t sohIosV = 0; sohIosV < sizeof(prg->pipeline_state_variants) / sizeof(prg->pipeline_state_variants[0]);
         sohIosV++) {
        prg->pipeline_state_variants[sohIosV] = nullptr;
    }
    LoadShader((struct ShaderProgram*)prg);
    const bool sohIosAsync =
        Ship::Context::GetRawInstance()->GetConsoleVariables()->GetInteger("gSohIos.AsyncShaders", 1) != 0;
    // gfx_metal_build_shader autoreleases vertex_descriptor; retain it across
    // the (possibly deferred) compile -- the lambda releases it.
    vertex_descriptor->retain();
    const bool sohIosSrgb = mSrgbMode;
    auto sohIosCompile = [this, prg, buf, cc_features, vertex_descriptor, sohIosSrgb]() {
        NS::AutoreleasePool* sohIosPool = NS::AutoreleasePool::alloc()->init();
        NS::Error* error = nullptr;
        auto sohIosLibT0 = std::chrono::steady_clock::now();
        MTL::Library* library =
            mDevice->newLibrary(NS::String::string(buf.data(), NS::UTF8StringEncoding), nullptr, &error);
        sSohIosLibWinUs.fetch_add((uint64_t)std::chrono::duration<double, std::micro>(
                                      std::chrono::steady_clock::now() - sohIosLibT0)
                                      .count());
        sSohIosLibWinCount.fetch_add(1);
        if (error != nullptr)
            SPDLOG_ERROR("Failed to compile shader library, error {}",
                         error->localizedDescription()->cString(NS::UTF8StringEncoding));
        MTL::RenderPipelineDescriptor* pipeline_descriptor = MTL::RenderPipelineDescriptor::alloc()->init();
        MTL::Function* vertexFunc = library->newFunction(NS::String::string("vertexShader", NS::UTF8StringEncoding));
        MTL::Function* fragmentFunc =
            library->newFunction(NS::String::string("fragmentShader", NS::UTF8StringEncoding));
        pipeline_descriptor->setVertexFunction(vertexFunc);
        pipeline_descriptor->setFragmentFunction(fragmentFunc);
        pipeline_descriptor->setVertexDescriptor(vertex_descriptor);
        pipeline_descriptor->colorAttachments()->object(0)->setPixelFormat(
            sohIosSrgb ? MTL::PixelFormatBGRA8Unorm_sRGB : MTL::PixelFormatBGRA8Unorm);
        pipeline_descriptor->setDepthAttachmentPixelFormat(MTL::PixelFormatDepth32Float);
        if (cc_features.opt_alpha) {
            pipeline_descriptor->colorAttachments()->object(0)->setBlendingEnabled(true);
            pipeline_descriptor->colorAttachments()->object(0)->setSourceRGBBlendFactor(MTL::BlendFactorSourceAlpha);
            pipeline_descriptor->colorAttachments()->object(0)->setDestinationRGBBlendFactor(
                MTL::BlendFactorOneMinusSourceAlpha);
            pipeline_descriptor->colorAttachments()->object(0)->setRgbBlendOperation(MTL::BlendOperationAdd);
            pipeline_descriptor->colorAttachments()->object(0)->setSourceAlphaBlendFactor(MTL::BlendFactorZero);
            pipeline_descriptor->colorAttachments()->object(0)->setDestinationAlphaBlendFactor(MTL::BlendFactorOne);
            pipeline_descriptor->colorAttachments()->object(0)->setAlphaBlendOperation(MTL::BlendOperationAdd);
            pipeline_descriptor->colorAttachments()->object(0)->setWriteMask(MTL::ColorWriteMaskAll);
        } else {
            pipeline_descriptor->colorAttachments()->object(0)->setBlendingEnabled(false);
            pipeline_descriptor->colorAttachments()->object(0)->setWriteMask(MTL::ColorWriteMaskAll);
        }
        for (int i = 0; i < ARRAY_COUNT(mMsaaNumQualityLevels); i++) {
            if (mMsaaNumQualityLevels[i] == 1) {
                int msaa_level = i + 1;
                pipeline_descriptor->setSampleCount(msaa_level);
                auto sohIosPsoT0 = std::chrono::steady_clock::now();
                MTL::RenderPipelineState* pipeline_state =
                    mDevice->newRenderPipelineState(pipeline_descriptor, &error);
                sSohIosPsoWinUs.fetch_add((uint64_t)std::chrono::duration<double, std::micro>(
                                              std::chrono::steady_clock::now() - sohIosPsoT0)
                                              .count());
                sSohIosPsoWinCount.fetch_add(1);
                if (!pipeline_state || error != nullptr) {
                    SPDLOG_ERROR("Failed to create pipeline state, error {}",
                                 error->localizedDescription()->cString(NS::UTF8StringEncoding));
                }
                // Publish last: the draw guard reads a non-null pipeline as
                // ready. Release fence orders the writes before the store.
                std::atomic_thread_fence(std::memory_order_release);
                prg->pipeline_state_variants[msaa_level] = pipeline_state;
            }
        }
        vertexFunc->release();
        fragmentFunc->release();
        library->release();
        pipeline_descriptor->release();
        vertex_descriptor->release();
        sohIosPool->release();
    };
    if (sohIosAsync) {
        std::thread(sohIosCompile).detach();
    } else {
        sohIosCompile();
    }
    autorelease_pool->release();
    return (struct ShaderProgram*)prg;
#else
    NS::Error* error = nullptr;
    MTL::Library* library =
        mDevice->newLibrary(NS::String::string(buf.data(), NS::UTF8StringEncoding), nullptr, &error);

    if (error != nullptr)
        SPDLOG_ERROR("Failed to compile shader library, error {}",
                     error->localizedDescription()->cString(NS::UTF8StringEncoding));

    MTL::RenderPipelineDescriptor* pipeline_descriptor = MTL::RenderPipelineDescriptor::alloc()->init();
    MTL::Function* vertexFunc = library->newFunction(NS::String::string("vertexShader", NS::UTF8StringEncoding));
    MTL::Function* fragmentFunc = library->newFunction(NS::String::string("fragmentShader", NS::UTF8StringEncoding));

    pipeline_descriptor->setVertexFunction(vertexFunc);
    pipeline_descriptor->setFragmentFunction(fragmentFunc);
    pipeline_descriptor->setVertexDescriptor(vertex_descriptor);

    pipeline_descriptor->colorAttachments()->object(0)->setPixelFormat(mSrgbMode ? MTL::PixelFormatBGRA8Unorm_sRGB
                                                                                 : MTL::PixelFormatBGRA8Unorm);
    pipeline_descriptor->setDepthAttachmentPixelFormat(MTL::PixelFormatDepth32Float);
    if (cc_features.opt_alpha) {
        pipeline_descriptor->colorAttachments()->object(0)->setBlendingEnabled(true);
        pipeline_descriptor->colorAttachments()->object(0)->setSourceRGBBlendFactor(MTL::BlendFactorSourceAlpha);
        pipeline_descriptor->colorAttachments()->object(0)->setDestinationRGBBlendFactor(
            MTL::BlendFactorOneMinusSourceAlpha);
        pipeline_descriptor->colorAttachments()->object(0)->setRgbBlendOperation(MTL::BlendOperationAdd);
        pipeline_descriptor->colorAttachments()->object(0)->setSourceAlphaBlendFactor(MTL::BlendFactorZero);
        pipeline_descriptor->colorAttachments()->object(0)->setDestinationAlphaBlendFactor(MTL::BlendFactorOne);
        pipeline_descriptor->colorAttachments()->object(0)->setAlphaBlendOperation(MTL::BlendOperationAdd);
        pipeline_descriptor->colorAttachments()->object(0)->setWriteMask(MTL::ColorWriteMaskAll);
    } else {
        pipeline_descriptor->colorAttachments()->object(0)->setBlendingEnabled(false);
        pipeline_descriptor->colorAttachments()->object(0)->setWriteMask(MTL::ColorWriteMaskAll);
    }

    struct ShaderProgramMetal* prg = &mShaderProgramPool[std::make_pair(shader_id0, shader_id1)];
    prg->shader_id0 = shader_id0;
    prg->shader_id1 = shader_id1;
    prg->usedTextures[0] = cc_features.usedTextures[0];
    prg->usedTextures[1] = cc_features.usedTextures[1];
    prg->usedTextures[2] = cc_features.used_masks[0];
    prg->usedTextures[3] = cc_features.used_masks[1];
    prg->usedTextures[4] = cc_features.used_blend[0];
    prg->usedTextures[5] = cc_features.used_blend[1];
    prg->numInputs = cc_features.numInputs;
    prg->numFloats = numFloats;

    // Prepoluate pipeline state cache with program and available msaa levels
    for (int i = 0; i < ARRAY_COUNT(mMsaaNumQualityLevels); i++) {
        if (mMsaaNumQualityLevels[i] == 1) {
            int msaa_level = i + 1;
            pipeline_descriptor->setSampleCount(msaa_level);
            MTL::RenderPipelineState* pipeline_state = mDevice->newRenderPipelineState(pipeline_descriptor, &error);

            if (!pipeline_state || error != nullptr) {
                // Pipeline State creation could fail if we haven't properly set up our pipeline descriptor.
                // If the Metal API validation is enabled, we can find out more information about what
                // went wrong.  (Metal API validation is enabled by default when a debug build is run
                // from Xcode)
                SPDLOG_ERROR("Failed to create pipeline state, error {}",
                             error->localizedDescription()->cString(NS::UTF8StringEncoding));
            }

            prg->pipeline_state_variants[msaa_level] = pipeline_state;
        }
    }

    LoadShader((struct ShaderProgram*)prg);

    vertexFunc->release();
    fragmentFunc->release();
    library->release();
    pipeline_descriptor->release();
    autorelease_pool->release();

    return (struct ShaderProgram*)prg;
#endif
}"""

t = replace_once(t, OLD_BODY, NEW_BODY, "async-split")

# --- 3. Draw guard: async compile not finished -> skip the draw, retry next.
t = replace_once(t,
    """    if (current_framebuffer.mLastShaderProgram != mShaderProgram) {
        current_framebuffer.mLastShaderProgram = mShaderProgram;

        MTL::RenderPipelineState* pipeline_state =
            mShaderProgram->pipeline_state_variants[current_framebuffer.mMsaaLevel];
        current_framebuffer.mCommandEncoder->setRenderPipelineState(pipeline_state);
    }""",
    """    if (current_framebuffer.mLastShaderProgram != mShaderProgram) {
        MTL::RenderPipelineState* pipeline_state =
            mShaderProgram->pipeline_state_variants[current_framebuffer.mMsaaLevel];
#ifdef __IOS__
        // SOH_IOS (overlay 0035): async shader compile not finished -- skip
        // this draw; the geometry pops in a few frames later when the
        // pipeline lands. Do NOT advance mLastShaderProgram (retry next
        // draw) or the vbo offset (the slot is reused). Never fires in the
        // synchronous path (variants are non-null there). The per-draw setup
        // above is idempotent, so the early return corrupts no state.
        if (pipeline_state == nullptr) {
            autorelease_pool->release();
            return;
        }
#endif
        current_framebuffer.mLastShaderProgram = mShaderProgram;
        current_framebuffer.mCommandEncoder->setRenderPipelineState(pipeline_state);
    }""",
    "draw-guard")

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0035-lus-metal-async-shaders.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
