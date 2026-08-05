#!/usr/bin/env python3
"""Overlay patch 0044: make the audio device config overridable, to A/B it.

NUMBERING: 0037+ are Lighthouse-only.

WHY. A user reports a constant, subtle crackle on device, present at every
volume (louder simply makes it easier to hear -- explicitly NOT an
amplitude-dependent distortion signature). Overlay 0043's instrument ruled out
the obvious causes in one device session: `aud_min=1656 / aud_max=3128` steady,
so no underrun, no overflow, no frame drops at the DAC. The volume-scaling path
in `osAiSetNextBuffer` never runs (all volume CVars unset -> default 100 ->
masterVol 1.0 -> fast path). So the artifact is in the signal, not the timing.

That leaves the device configuration, which this fork sets in one line:

    InitAudio({ .SampleRate = 22000, .SampleLength = 736, .DesiredBuffered = 2208 })

Two things about it are worth testing rather than defending:

* **736 is not a power of two.** SDL's own header says of `desired->samples`:
  "This number should be a power of two, and may be adjusted by the audio
  driver to a value more suitable for the hardware. Good values seem to range
  between 512 and 4096." 736 = 32 x 23. Whatever the CoreAudio backend does
  with it, it is off-contract, and buffer-boundary handling is exactly where a
  periodic artifact would come from.
* **22000 is not a standard sample rate.** The standard set is
  8000/11025/16000/22050/32000/44100/48000. 22000 looks derived from
  736 x 30 fps = 22080, rounded. iOS CoreAudio runs at 48000, so SDL resamples
  22000 -> 48000 at 2.1818..., a ratio with no fast path, using its built-in
  resampler.

(An earlier draft of this header claimed the rate was output-side only,
because `GameEngine_GetSampleRate` has no caller. That was wrong -- the synth
rate is set independently, and the next section is what I found when I checked
instead of assuming.)

THE PART I GOT WRONG FIRST, and why this patch has three hunks instead of one.
The output rate is set in TWO independent places that must agree, and moving
only the device side silently drifts the queue:

  * the SYNTH rate: `sn_alConfig.outputRate = osAiSetFrequency(22000)`
    (src/core1/audio_manager.c:327) -- quantised by the N64 DAC divisor to
    21998. This is the rate the game actually produces samples at.
  * the FRAME SIZE: `fsize = 44000.0f / FRAMERATE` (audio_manager.c:311),
    rounded up to a multiple of NUM_SAMPLES (184) -> **736**. That 44000 is
    2 x 22000, i.e. one 30 Hz tick's worth at the synth rate. So 736 is not an
    arbitrary buffer length, it is the rate expressed as samples per tick --
    which is also why it is not a power of two.
  * the DEVICE rate: `InitAudio({ .SampleRate = 22000, ... })` (Engine.cpp).

Change the device rate alone and the game still produces 736 samples per tick
while the DAC drains at the new rate -> guaranteed starvation. Change the synth
rate alone and the frame size no longer covers a tick. All three move together
or none do, which is what this patch enforces by deriving all of them from ONE
value.

WHAT THIS PATCH DOES. Adds `LhIos_AudioRate()` (in the port layer, where CVar
access already exists -- no core1 file reads CVars today and this patch does
not make it the first), and drives all three sites from it:

    gSohIos.AudioSampleRate      default 22000  -> synth rate, frame size, device rate
    gSohIos.AudioSampleLength    default 736    -> SDL device buffer ONLY
    gSohIos.AudioDesiredBuffered default 0      -> 0 means "scale with the rate"

Defaults reproduce today's behaviour exactly, so this is inert until a knob is
turned. `SampleLength` is deliberately independent: it is only SDL's device
buffer size and is the one value that can be made a power of two without
touching the synth at all.

InitAudio runs once at startup, so an A/B is: set the CVar over the bridge,
relaunch, listen. That is the same shape as the SSAA A/B that settled the
thermal question in one session, and it is the only way to turn "sounds
crackly" into a comparison instead of a theory.

The values are logged at init so a device log always says which configuration
produced a given recording.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def unified(rel, before, after):
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(before)
        fb.write(after)
        fa.flush()
        fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"[{rel}] no diff"
    return r.stdout.decode()


diffs = []

# --- 1. the single source of truth, plus the device side --------------------
ENG = ROOT / "vendor/Lighthouse/src/port/Engine.cpp"
eng_orig = ENG.read_text()

OLD = """    this->context->InitAudio({ .SampleRate = 22000, .SampleLength = 736, .DesiredBuffered = 2208 });
"""
NEW = """#ifdef __IOS__
    // LIGHTHOUSE_IOS (overlay 0044): ONE knob for all three coupled values.
    // See the patch header -- the synth rate, the frame size and the device
    // rate are three separate constants that must agree, and moving any one of
    // them alone starves or drifts the queue.
    {
        const int32_t lhRate = LhIos_AudioRate();
        // The synth will quantise through the N64 DAC divisor; ask the same
        // function for the same answer so the device plays at EXACTLY the rate
        // the game produces. (22000 -> 21998.)
        const int32_t lhActual = osAiSetFrequency(lhRate);
        const int32_t lhLen = CVarGetInteger("gSohIos.AudioSampleLength", 736);
        int32_t lhBuf = CVarGetInteger("gSohIos.AudioDesiredBuffered", 0);
        if (lhBuf <= 0) {
            // Unset means "hold the same ~100 ms as upstream, whatever the
            // rate". At 21998 this is exactly 2208, upstream's value.
            lhBuf = (int32_t)(((int64_t)lhActual * 2208) / 21998);
        }
        SPDLOG_INFO("[LIGHTHOUSE_IOS] audio: requested={} actual={} length={} desiredBuffered={}",
                    lhRate, lhActual, lhLen, lhBuf);
        this->context->InitAudio(
            { .SampleRate = lhActual, .SampleLength = lhLen, .DesiredBuffered = lhBuf });
    }
#else
    this->context->InitAudio({ .SampleRate = 22000, .SampleLength = 736, .DesiredBuffered = 2208 });
#endif
"""
n = eng_orig.count(OLD)
assert n == 1, f"[initaudio] expected 1 match, got {n}"
eng_t = eng_orig.replace(OLD, NEW)

# The helper itself, at file scope. Defined HERE rather than in OS_AI.cpp
# because overlay 0043 already owns hunks in that file, and two patches writing
# inside one hunk's context window breaks the other's reverse-apply (learned
# twice: 0038/0041, then 0042/0013).
# Anchored ABOVE the first USE, not next to the other extern "C" helpers 1300
# lines below -- C++ needs the declaration first, and `extern "C"` cannot be
# declared at block scope, so there is nowhere inside the calling function to
# put it.
#
# The anchor was `void GameEngine::FinishInit() {` until the 1.0.2 bump, when
# upstream 9f3f30b1 ("Init audio before menu") moved the InitAudio call OUT of
# FinishInit and into the constructor ~175 lines earlier. The helper kept
# regenerating cleanly (its anchor still matched, and so did InitAudio's) but
# now sat BELOW its only caller: "use of undeclared identifier
# 'LhIos_AudioRate'". Anchor on the constructor instead, which is what actually
# contains the call -- if a future bump moves it again, this same error is the
# signal, and the fix is to re-anchor on the new enclosing function.
HELPER_OLD = """GameEngine::GameEngine() {
"""
HELPER_NEW = """#ifdef __IOS__
// LIGHTHOUSE_IOS (overlay 0044): the one audio-rate knob, cached so the synth
// (audio_manager.c) and the device (InitAudio) can never disagree even if the
// CVar is edited at runtime. core1 does not read CVars anywhere today and this
// patch does not make it start; audio_manager.c just calls this.
extern "C" int32_t LhIos_AudioRate(void) {
    static int32_t cached = 0;
    if (cached == 0) {
        cached = CVarGetInteger("gSohIos.AudioSampleRate", 22000);
        if (cached < 8000 || cached > 48000) {
            cached = 22000; // a typo in a CVar should not brick audio
        }
    }
    return cached;
}
#endif

GameEngine::GameEngine() {
"""
n = eng_t.count(HELPER_OLD)
assert n == 1, f"[helper] expected 1 match, got {n}"
eng_t = eng_t.replace(HELPER_OLD, HELPER_NEW)
diffs.append(unified("src/port/Engine.cpp", eng_orig, eng_t))

# --- 2. the synth side: frame size + output rate -----------------------------
AM = ROOT / "vendor/Lighthouse/src/core1/audio_manager.c"
am_orig = AM.read_text()

FSIZE_OLD = """    fsize = 44000.0f / FRAMERATE;
"""
FSIZE_NEW = """#ifdef __IOS__
    // LIGHTHOUSE_IOS (overlay 0044): 44000 is 2 x 22000 -- one 30 Hz tick's
    // worth of samples at the synth rate. It is the RATE expressed as samples
    // per tick, not an arbitrary buffer size, which is why the resulting 736
    // is not a power of two. It has to track the rate knob or the frame stops
    // covering a tick.
    extern s32 LhIos_AudioRate(void);
    fsize = 2.0f * (f32)LhIos_AudioRate() / FRAMERATE;
#else
    fsize = 44000.0f / FRAMERATE;
#endif
"""
n = am_orig.count(FSIZE_OLD)
assert n == 1, f"[fsize] expected 1 match, got {n}"
am_t = am_orig.replace(FSIZE_OLD, FSIZE_NEW)

RATE_OLD = """    sn_alConfig.outputRate = osAiSetFrequency(22000);
"""
RATE_NEW = """#ifdef __IOS__
    // LIGHTHOUSE_IOS (overlay 0044): same knob as the frame size above and as
    // the device rate in Engine.cpp.
    sn_alConfig.outputRate = osAiSetFrequency(LhIos_AudioRate());
#else
    sn_alConfig.outputRate = osAiSetFrequency(22000);
#endif
"""
n = am_t.count(RATE_OLD)
assert n == 1, f"[outputrate] expected 1 match, got {n}"
am_t = am_t.replace(RATE_OLD, RATE_NEW)
diffs.append(unified("src/core1/audio_manager.c", am_orig, am_t))

out = ROOT / "overlay/patches/0044-lighthouse-audio-config-overridable.patch"
out.write_text(__doc__ + "\n" + "".join(diffs))
print(f"wrote {out}")
