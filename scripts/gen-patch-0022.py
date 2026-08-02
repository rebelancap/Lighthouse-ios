#!/usr/bin/env python3
"""Overlay patch 0022: visionOS platform dispatch.

The leetal toolchain sets CMAKE_SYSTEM_NAME=visionOS for
PLATFORM=SIMULATOR_VISIONOS / VISIONOS, but the whole tree (upstream
Lighthouse + LUS + this port's overlay CMake) dispatches on
STREQUAL "iOS" at a dozen-plus sites. Rather than patch every site, flip
CMAKE_SYSTEM_NAME back to "iOS" right after project(): by then the
compilers, the sysroot (xros / xrsimulator) and Xcode's SDKROOT are
locked, so the flip only affects dispatch conditionals. The real
visionOS-ness keeps coming from the SDK (TARGET_OS_VISION == 1 in
TargetConditionals.h). LIGHTHOUSE_VISIONOS stays a normal variable for
genuinely vision-specific deltas (device family, plist, Swift entry).

The flip has to happen THREE times because each project()/toolchain
re-entry resets it:
  1. after the top-level project()
  2. after libultraship's own project()
  3. after LUS includes ios-toolchain-populate.cmake (which re-runs
     leetal's platform init in that scope)

Deployment target: CMake then emits IPHONEOS_DEPLOYMENT_TARGET, which
Xcode ignores for xros — scripts/build-vision-sim.sh and
scripts/build-visionos.sh compensate with
CMAKE_XCODE_ATTRIBUTE_XROS_DEPLOYMENT_TARGET.

No-op for iOS/macOS builds: the condition is never true there.

Divergence from the flagship's 0022 (recorded rather than silently
dropped): the flagship also language-scopes a top-level
-fno-fast-math/-ffp-contract genex so the Xcode generator does not feed
those C driver flags to swiftc. Lighthouse's root CMakeLists has NO such
global add_compile_options — the only ones are inside the
ENABLE_ASAN block, which is OFF — so that hunk has no anchor here and is
correctly absent. If a future upstream bump introduces one, swiftc will
hard-error and this comment is the pointer to the fix.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLIP_BODY = """if(CMAKE_SYSTEM_NAME STREQUAL "visionOS")
    set(CMAKE_SYSTEM_NAME "iOS")
    set(IOS 1)
    set(LIGHTHOUSE_VISIONOS 1)
endif()
"""

ROOT_FLIP = """
# LIGHTHOUSE_IOS (overlay 0022): visionOS builds (PLATFORM=VISIONOS /
# SIMULATOR_VISIONOS) dispatch as iOS throughout the tree. The compilers and
# the SDK are already fixed by the toolchain at this point; only the
# STREQUAL "iOS" conditionals care. TARGET_OS_VISION (from the xros SDK)
# remains the source-level discriminator.
""" + FLIP_BODY

LUS_FLIP = """
# LIGHTHOUSE_IOS (overlay 0022): this project() re-ran the toolchain,
# resetting CMAKE_SYSTEM_NAME to visionOS — re-flip so this subtree keeps
# dispatching as iOS (see the top-level CMakeLists hunk).
""" + FLIP_BODY


def unified(rel, before, after):
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(before)
        fb.write(after)
        fa.flush()
        fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"[{rel}] diff returned {r.returncode} (no change?)"
    return r.stdout.decode()


# --- 1. top-level CMakeLists ------------------------------------------------
SRC = ROOT / "vendor/Lighthouse/CMakeLists.txt"
orig = SRC.read_text()
OLD = "project(Lighthouse VERSION 1.0.0 LANGUAGES C CXX ASM)\n"
n = orig.count(OLD)
assert n == 1, f"[project-anchor] expected 1 match, got {n}"
root_diff = unified("CMakeLists.txt", orig, orig.replace(OLD, OLD + ROOT_FLIP))

# --- 2 + 3. libultraship CMakeLists (two hunks, one file) -------------------
LUS_SRC = ROOT / "vendor/Lighthouse/libultraship/CMakeLists.txt"
lus_orig = LUS_SRC.read_text()

LUS_OLD = "project(libultraship LANGUAGES C CXX)\n"
n = lus_orig.count(LUS_OLD)
assert n == 1, f"[lus-project-anchor] expected 1 match, got {n}"
lus_t = lus_orig.replace(LUS_OLD, LUS_OLD + LUS_FLIP)

LUS_OLD2 = """if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    include(cmake/ios-toolchain-populate.cmake)
endif()
"""
LUS_NEW2 = """if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    include(cmake/ios-toolchain-populate.cmake)
    # LIGHTHOUSE_IOS (overlay 0022): the toolchain include above re-ran
    # leetal's platform init IN THIS SCOPE, resetting CMAKE_SYSTEM_NAME to
    # visionOS again — re-flip a third time so src/ (compile definitions,
    # __IOS__) dispatches as iOS.
    if(CMAKE_SYSTEM_NAME STREQUAL "visionOS")
        set(CMAKE_SYSTEM_NAME "iOS")
        set(IOS 1)
        set(LIGHTHOUSE_VISIONOS 1)
    endif()
endif()
"""
n = lus_t.count(LUS_OLD2)
assert n == 1, f"[lus-toolchain-anchor] expected 1 match, got {n}"
lus_t = lus_t.replace(LUS_OLD2, LUS_NEW2)
lus_diff = unified("libultraship/CMakeLists.txt", lus_orig, lus_t)

out = ROOT / "overlay/patches/0022-lighthouse-visionos-dispatch.patch"
out.write_text(__doc__ + "\n" + root_diff + lus_diff)
print(f"wrote {out}")
