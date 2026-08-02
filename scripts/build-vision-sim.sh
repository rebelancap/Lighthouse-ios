#!/usr/bin/env bash
# Build Lighthouse for the visionOS SIMULATOR (arm64).
# Phase 01 of the Vision Pro bring-up (VISION-PRO-LUS-PLAYBOOK.md): same tree,
# PLATFORM=SIMULATOR_VISIONOS, the vision-sim dep slices, and the Swift @main
# entry from overlay 0004. The iOS build (build-ios/, build-sim/) is untouched.
# Produces build-vision-sim/Release-xrsimulator/Lighthouse.app
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build-vision-sim"
PREFIX="$ROOT/work/vision-sim-deps/prefix"
PORT_O2R="$ROOT/oracle/shiphome/lighthouse.o2r"
JOBS="${JOBS:-6}"

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$ROOT/vendor/Lighthouse/.git" ] || "$ROOT/scripts/bootstrap.sh"
"$ROOT/scripts/apply-overlay.sh"
[ -f "$PREFIX/lib/libvorbisfile.a" ] || LIGHTHOUSE_IOS_SDK=visionsim "$ROOT/scripts/build-audio-deps-ios.sh"
[ -f "$PORT_O2R" ] || die "missing $PORT_O2R — run scripts/build-oracle.sh (GeneratePortO2R)"

# Same archive-poisoned-product guard as build-ios.sh (SYNC WAVE 2 trap).
APP_PATH="$BUILD/Release-xrsimulator/Lighthouse.app"
if [ -L "$APP_PATH" ]; then
    info "clearing archive-poisoned product symlink"
    rm -f "$APP_PATH"
fi

# libzip's Annex K probes link-but-don't-declare when cross-compiling; see
# build-sim.sh for the full story.
LIBZIP_ANNEX_K=(
    -DHAVE_MEMCPY_S=OFF -DHAVE_STRNCPY_S=OFF
    -DHAVE_STRERROR_S=OFF -DHAVE_STRERRORLEN_S=OFF
)

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILDNO="$(date -u +%Y%m%d%H%M)"
CONSOLE="${LIGHTHOUSE_REMOTE_CONSOLE:-ON}"
info "lighthouse vision-sim $VERSION (build $BUILDNO), remote console: $CONSOLE"

# CMAKE_SYSTEM_NAME=visionOS drives the leetal toolchain; overlay 0022 flips it
# back to "iOS" after each project() so the tree's STREQUAL "iOS" dispatch keeps
# working, and leaves LIGHTHOUSE_VISIONOS behind for the genuine deltas.
# XROS_DEPLOYMENT_TARGET is set explicitly because CMake emits
# IPHONEOS_DEPLOYMENT_TARGET, which Xcode ignores for the xrsimulator SDK.
# SDL_OPENGLES/SDL_OPENGL OFF: GLES does not exist on visionOS (overlay 0023
# also drops ENABLE_OPENGL, so nothing references it).
cmake --no-warn-unused-cli -S "$ROOT/vendor/Lighthouse" -B "$BUILD" -GXcode \
    -DCMAKE_SYSTEM_NAME=visionOS -DPLATFORM=SIMULATOR_VISIONOS \
    -DCMAKE_OSX_SYSROOT=xrsimulator \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=2.0 -DCMAKE_BUILD_TYPE:STRING=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_XCODE_ATTRIBUTE_XROS_DEPLOYMENT_TARGET=2.0 \
    -DCMAKE_XCODE_ATTRIBUTE_STRIP_INSTALLED_PRODUCT=NO \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED=NO \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY="" \
    -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local" \
    -DENABLE_SCRIPTING=OFF \
    -DSDL_OPENGLES=OFF -DSDL_OPENGL=OFF \
    "${LIBZIP_ANNEX_K[@]}" \
    "-DLIGHTHOUSE_IOS_VERSION=$VERSION" "-DLIGHTHOUSE_IOS_BUILD=$BUILDNO" \
    "-DLIGHTHOUSE_REMOTE_CONSOLE=$CONSOLE" \
    "-DLIGHTHOUSE_IOS_DEPS_PREFIX=$PREFIX" \
    "-DLIGHTHOUSE_O2R_PATH=$PORT_O2R" \
    "-DLIGHTHOUSE_IOS_SHELL_DIR=$ROOT/app/ios" \
    -DLIGHTHOUSE_IOS_BUNDLE_IDENTIFIER=com.rebelancap.lighthouse

cmake --build "$BUILD" --config Release --target Lighthouse --parallel "$JOBS"

APP="$BUILD/Release-xrsimulator/Lighthouse.app"
[ -d "$APP" ] || die "expected app at $APP"
lipo -info "$APP/Lighthouse"
# The whole point of the visionOS branch is the Swift @main entry: it lives in
# a static archive nothing else in the link references, so if -Wl,-force_load
# were ever dropped the link would still SUCCEED and the app would simply have
# no entry point. Assert it rather than discovering that at launch.
# Substitute, don't pipe: `nm | grep -q` exits grep early, SIGPIPEs nm, and
# pipefail turns that into a spurious failure (this script did exactly that
# on its first green build — same trap build-ios.sh records for codesign|head).
SWIFT_ENTRY=$(nm "$APP/Lighthouse" 2>/dev/null | grep -c "SohVisionApp" || true)
[ "$SWIFT_ENTRY" -gt 0 ] || die "no Swift app entry in the binary — force_load of lighthousevisionswift did not take"
info "    Swift @main entry present ($SWIFT_ENTRY symbols)"
info "built (visionOS simulator): $APP"
