#!/usr/bin/env python3
"""Overlay patch 0003: Audio.cpp references CoreAudioAudioPlayer under
__APPLE__, but src/ship/CMakeLists.txt:16-18 only compiles it on Darwin —
undefined symbol at app link on iOS, and COREAUDIO would be the default
backend with no implementation. Gate all four __APPLE__ blocks off iOS;
default/available backend on iOS becomes SDL (works: SDL-iOS configures the
AVAudioSession internally)."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/ship/audio/Audio.cpp"
REL = "libultraship/src/ship/audio/Audio.cpp"
orig = SRC.read_text()

def replace_once(text, old, new):
    n = text.count(old)
    assert n == 1, f"expected exactly 1 match, got {n}: {old[:70]!r}"
    return text.replace(old, new)

t = orig
t = replace_once(t,
    '#ifdef __APPLE__\n#include "ship/audio/CoreAudioAudioPlayer.h"\n#endif',
    '#if defined(__APPLE__) && !defined(__IOS__)\n#include "ship/audio/CoreAudioAudioPlayer.h"\n#endif')
t = replace_once(t,
    "#ifdef __APPLE__\n        case AudioBackend::COREAUDIO:\n            mAudioPlayer = std::make_shared<CoreAudioAudioPlayer>(this->mAudioSettings);\n            break;\n#endif",
    "#if defined(__APPLE__) && !defined(__IOS__)\n        case AudioBackend::COREAUDIO:\n            mAudioPlayer = std::make_shared<CoreAudioAudioPlayer>(this->mAudioSettings);\n            break;\n#endif")
t = replace_once(t,
    "#ifdef __APPLE__\n    mAvailableAudioBackends->push_back(AudioBackend::COREAUDIO);\n#endif",
    "#if defined(__APPLE__) && !defined(__IOS__)\n    mAvailableAudioBackends->push_back(AudioBackend::COREAUDIO);\n#endif")
t = replace_once(t,
    "#ifdef __APPLE__\n    return AudioBackend::COREAUDIO;\n#endif",
    "#if defined(__APPLE__) && !defined(__IOS__)\n    return AudioBackend::COREAUDIO;\n#endif")

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True, text=True)
assert r.returncode == 1, "no diff produced"
out = ROOT / "overlay/patches/0003-lus-audio-no-coreaudio-on-ios.patch"
out.write_text(__doc__ + "\n\n" + r.stdout)
print(f"wrote {out}")
