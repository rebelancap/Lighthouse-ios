#!/usr/bin/env python3
"""Overlay patch 0014: on iOS, a config value of "coreaudio" must not select
the (compiled-out) CoreAudio player.
Audio::InitAudioPlayer compiles the COREAUDIO case only on
`__APPLE__ && !__IOS__`; on iOS a saved backend of "coreaudio" would hit the
switch `default:` -> NullAudioPlayer = SILENT. Fresh iOS configs resolve to
SDL fine, but a config synced from a macOS install (Window.AudioBackend =
"coreaudio") would drop audio with no error. Map "coreaudio" -> SDL on iOS
in GetSavedAudioBackend. LUS-side; __IOS__ is defined inside libultraship."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/ship/audio/Audio.cpp"
REL = "libultraship/src/ship/audio/Audio.cpp"
orig = SRC.read_text()

OLD = """    if (backendName == "coreaudio") {
        return AudioBackend::COREAUDIO;
    }
"""
NEW = """    if (backendName == "coreaudio") {
#ifdef __IOS__
        // CoreAudio player is not built on iOS (HALOutput is macOS-only); a
        // config synced from macOS would otherwise select the silent Null
        // player via the switch default. Use SDL (real audio) instead.
        return AudioBackend::SDL;
#else
        return AudioBackend::COREAUDIO;
#endif
    }
"""

n = orig.count(OLD)
assert n == 1, f"[coreaudio-branch] expected 1 match, got {n}"
t = orig.replace(OLD, NEW)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0014-lus-ios-audio-coreaudio-to-sdl.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
