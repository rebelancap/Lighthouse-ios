#!/usr/bin/env python3
"""Overlay patch 0027: audio bridge null-chain guards.
Recurring device SEGV (Vision Pro, twice confirmed by crash.txt):
AudioPlayerBuffered+32 on OTRAudio_Thread. The bridge null-checks the
PLAYER but not the chain — Context::GetRawInstance()->GetAudio() is null
during teardown/init windows, and ->GetAudioPlayer() on it faults. Guard
the chain in every bridge function the audio thread can hit."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/libultraship/bridge/audiobridge.cpp"
REL = "libultraship/src/libultraship/bridge/audiobridge.cpp"
orig = SRC.read_text()

CHAIN = "    auto audio = Ship::Context::GetRawInstance()->GetAudio()->GetAudioPlayer();\n"
GUARD = """    // SOH_IOS (overlay 0027): the audio thread races Context teardown/init —
    // GetAudio() is briefly null and the chained call faulted (device SEGV).
    auto sohIosCtx = Ship::Context::GetRawInstance();
    if (sohIosCtx == nullptr || sohIosCtx->GetAudio() == nullptr) {
        return {DEFAULT};
    }
    auto audio = sohIosCtx->GetAudio()->GetAudioPlayer();
"""
n = orig.count(CHAIN)
assert n >= 3, f"[chain-sites] expected >=3, got {n}"

# per-function correct default return values
t = orig
for fn, default in (("int32_t AudioPlayerBuffered() {", "0"),
                    ("int32_t AudioPlayerGetDesiredBuffered() {", "0"),
                    ("AudioChannelsSetting GetAudioChannels() {", "audioStereo"),
                    ("int32_t GetNumAudioChannels() {", "2"),
                    ("void AudioPlayerPlayFrame(const uint8_t* buf, size_t len) {", "")):
    marker = fn + "\n" + CHAIN
    if marker in t:
        ret = "return;" if default == "" else f"return {default};"
        t = t.replace(marker, fn + "\n" + GUARD.replace("return {DEFAULT};", ret), 1)

assert CHAIN not in t, "unguarded chain sites remain"

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0027-soh-ios-audio-bridge-guards.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
