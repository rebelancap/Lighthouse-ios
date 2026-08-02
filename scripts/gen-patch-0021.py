#!/usr/bin/env python3
"""Overlay patch 0021: SDL2 visionOS compatibility (VISION-PRO-LUS-PLAYBOOK §1).

SDL 2.32.10 has zero visionOS awareness and its UIKit backend leans on
UIScreen/status-bar APIs that the xros SDK marks unavailable (~90 compile
errors). This patch:

1. Adds cmake/dependencies/patches/sdl2-visionos-compat.patch to LUS — a
   TARGET_OS_VISION-guarded port of SDL's UIKit backend (virtual
   1920x1080@90 display replacing UIScreen enumeration; scene-sized
   UIWindow; traitCollection.displayScale for HIGHDPI; status-bar /
   launch-image / orientation machinery compiled out; 90 Hz CADisplayLink;
   SDL_PLATFORM_VISIONOS define for the backported sysurl guard). It is
   INERT on iOS/tvOS builds (TARGET_OS_VISION == 0), which is why applying
   it unconditionally does not change the iPhone build's behaviour.
2. Hunks LUS cmake/dependencies/ios.cmake to run it as the SDL2
   FetchContent PATCH_COMMAND via the existing git-patch.cmake mechanism
   (the same pattern upstream already uses for the imgui/stormlib dep
   patches).

3. Adds a SECOND, Lighthouse-only SDL patch —
   cmake/dependencies/patches/sdl2-ios-native-audio-rate.patch — which stops
   SDL's iOS backend renegotiating the AVAudioSession sample rate. See that
   asset's own header for the full derivation; in short, SDL asks the session
   for 21998 Hz (the N64 DAC quantisation of 22000), iOS can only grant rates
   from a fixed hardware list, SDL overwrites spec.freq with what it got, and
   — because LUS opens with allowed_changes=0 — silently builds an internal
   SDL_AudioStream to convert. That stream's resampler resets its fractional
   phase every chunk, producing a measured -30.5 dBc noise floor against
   -71.4 dBc unchunked, ~30 times a second. Skipping the renegotiation leaves
   Apple's converter to do the work, which is structurally what the macOS
   build does — and the macOS oracle is clean.

The visionOS asset is taken VERBATIM from the flagship (Shipwright-ios) —
verified first that both ports' LUS forks pin the identical SDL tag
(release-2.32.10) and carry a byte-identical SDL2 FetchContent block, so
there is nothing port-specific in it. Copying rather than re-deriving is
the point: the playbook's whole claim is that a sixth port pays only for
its own divergences. The audio asset is genuinely ours and is kept in a
SEPARATE file so the flagship one stays byte-identical and diffable.

WHY BOTH LIVE IN THIS PATCH rather than a new 0045: the SDL2 FetchContent
block below is a single hunk, and a second patch inserting a PATCH_COMMAND
into it would break THIS patch's reverse-apply — which apply-overlay's
idempotency and bootstrap.sh's purity check both depend on. Paid twice
already (0038/0041, then 0042/0013); not paying it a third time.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LUS = ROOT / "vendor/Lighthouse/libultraship"
SDL_PATCH_SRC = ROOT / "overlay/assets/sdl2-visionos-compat.patch"

diffs = []

# --- Hunk 1: the SDL2 patch file itself, added under LUS's dep-patches dir ---
sdl_patch_text = SDL_PATCH_SRC.read_text()
assert sdl_patch_text.count("TARGET_OS_VISION") > 10, \
    "the SDL compat asset should be vision-guarded throughout — wrong file?"


def add_asset(text, rel_new, tag):
    """Emit a 'new file' diff hunk carrying an SDL patch into LUS's deps dir."""
    with tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fb.write(text)
        fb.flush()
        empty = fb.name + ".empty"
        open(empty, "w").close()
        r = subprocess.run(["diff", "-uN", "--label", "/dev/null", "--label", f"b/{rel_new}",
                            empty, fb.name], capture_output=True)
    assert r.returncode == 1, f"[{tag}] diff returned {r.returncode}"
    return r.stdout.decode()


diffs.append(add_asset(sdl_patch_text,
                       "libultraship/cmake/dependencies/patches/sdl2-visionos-compat.patch",
                       "sdl-vision-asset"))

AUDIO_PATCH_SRC = ROOT / "overlay/assets/sdl2-ios-native-audio-rate.patch"
audio_patch_text = AUDIO_PATCH_SRC.read_text()
assert "setPreferredSampleRate" in audio_patch_text, \
    "the audio asset should be the session-rate fix — wrong file?"
diffs.append(add_asset(audio_patch_text,
                       "libultraship/cmake/dependencies/patches/sdl2-ios-native-audio-rate.patch",
                       "sdl-audio-asset"))

# --- Hunk 2: wire the PATCH_COMMAND into the SDL2 FetchContent in ios.cmake --
src = LUS / "cmake/dependencies/ios.cmake"
orig = src.read_text()
OLD = """#=================== SDL2 ===================
find_package(SDL2 QUIET)
if (NOT ${SDL2_FOUND})
    FetchContent_Declare(
        SDL2
        GIT_REPOSITORY https://github.com/libsdl-org/SDL.git
        GIT_TAG release-2.32.10
        OVERRIDE_FIND_PACKAGE
    )
    FetchContent_MakeAvailable(SDL2)
endif()
"""
NEW = """#=================== SDL2 ===================
find_package(SDL2 QUIET)
if (NOT ${SDL2_FOUND})
    # LIGHTHOUSE_IOS (overlay 0021): visionOS compatibility for SDL's UIKit
    # backend (TARGET_OS_VISION-guarded; inert on iOS). Applied through the
    # same git-patch mechanism upstream uses for the imgui/stormlib deps.
    set(sdl2_visionos_patch_file ${CMAKE_CURRENT_SOURCE_DIR}/cmake/dependencies/patches/sdl2-visionos-compat.patch)
    set(sdl2_apply_patch_command ${CMAKE_COMMAND} -Dpatch_file=${sdl2_visionos_patch_file} -Dwith_reset=TRUE -P ${CMAKE_CURRENT_SOURCE_DIR}/cmake/dependencies/git-patch.cmake)
    # LIGHTHOUSE_IOS (overlay 0021): second patch, ours -- stop SDL's iOS
    # backend renegotiating the AVAudioSession rate. with_reset=FALSE is
    # load-bearing: the first command may `git reset --hard` and re-apply, and
    # a second reset here would undo it. Order matters, so this stays second.
    set(sdl2_ios_audio_patch_file ${CMAKE_CURRENT_SOURCE_DIR}/cmake/dependencies/patches/sdl2-ios-native-audio-rate.patch)
    set(sdl2_apply_audio_patch_command ${CMAKE_COMMAND} -Dpatch_file=${sdl2_ios_audio_patch_file} -Dwith_reset=FALSE -P ${CMAKE_CURRENT_SOURCE_DIR}/cmake/dependencies/git-patch.cmake)
    FetchContent_Declare(
        SDL2
        GIT_REPOSITORY https://github.com/libsdl-org/SDL.git
        GIT_TAG release-2.32.10
        PATCH_COMMAND ${sdl2_apply_patch_command}
        COMMAND ${sdl2_apply_audio_patch_command}
        OVERRIDE_FIND_PACKAGE
    )
    FetchContent_MakeAvailable(SDL2)
endif()
"""
n = orig.count(OLD)
assert n == 1, f"[sdl2-fetch] expected 1 match, got {n}"
t = orig.replace(OLD, NEW)

# The mechanism this rides on must actually exist in THIS fork.
assert (LUS / "cmake/dependencies/git-patch.cmake").is_file(), \
    "git-patch.cmake missing from this LUS fork — the PATCH_COMMAND would be a no-op"

rel = "libultraship/cmake/dependencies/ios.cmake"
with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig)
    fb.write(t)
    fa.flush()
    fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1, f"[ios.cmake] diff returned {r.returncode}"
diffs.append(r.stdout.decode())

out = ROOT / "overlay/patches/0021-lighthouse-visionos-sdl2-compat.patch"
out.write_text(__doc__ + "\n" + "".join(diffs))
print(f"wrote {out}")
