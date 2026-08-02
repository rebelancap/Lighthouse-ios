#!/usr/bin/env bash
# Build Lighthouse for the iOS SIMULATOR (arm64).
# Produces spikes/lighthouse-sim-build/Release-iphonesimulator/Lighthouse.app
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/spikes/lighthouse-sim-build"
PREFIX="$ROOT/work/ios-sim-deps/prefix"
PORT_O2R="$ROOT/oracle/shiphome/lighthouse.o2r"
JOBS="${JOBS:-6}"   # charter: --parallel 6 max on this shared box

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$ROOT/vendor/Lighthouse/.git" ] || "$ROOT/scripts/bootstrap.sh"
"$ROOT/scripts/apply-overlay.sh"
[ -f "$PREFIX/lib/libvorbisfile.a" ] || LIGHTHOUSE_IOS_SDK=simulator "$ROOT/scripts/build-audio-deps-ios.sh"
[ -f "$PORT_O2R" ] || die "missing $PORT_O2R — run scripts/build-oracle.sh (GeneratePortO2R)"

# libzip probes the ISO C Annex K "secure" functions with check_function_exists,
# which only tests that the symbol LINKS — not that it is declared. Cross-
# compiling to Apple SDKs that resolves for memcpy_s / strncpy_s / strerror_s /
# strerrorlen_s, none of which Darwin's headers declare, so libzip compiles calls
# to undeclared functions and clang rejects them ("call to undeclared function
# 'memcpy_s'"). The check_symbol_exists probes right beside them (localtime_s,
# snprintf_s) get it right, which is the tell.
#
# Pre-seed the cache: CMake's check macros skip when the result variable is
# already defined, so libzip takes its portable fallbacks.
LIBZIP_ANNEX_K=(
    -DHAVE_MEMCPY_S=OFF
    -DHAVE_STRNCPY_S=OFF
    -DHAVE_STRERROR_S=OFF
    -DHAVE_STRERRORLEN_S=OFF
)

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILDNO="$(date -u +%Y%m%d%H%M)"
# The simulator IS the dev loop, so the console is ON unless told otherwise
# (SYNC WAVE 2 item 1: leaving it off the sim script compiles it out of the
# path used most).
CONSOLE="${LIGHTHOUSE_REMOTE_CONSOLE:-ON}"
info "lighthouse sim $VERSION (build $BUILDNO), remote console: $CONSOLE"

# PLATFORM=SIMULATORARM64 makes LUS's ios-toolchain-populate (overlay 0009)
# select the simulator sysroot instead of forcing device.
# -GXcode is REQUIRED, not a preference: Ninja compiles SDL2's .m sources as
# Objective-C++ and hard-errors (D9).
# STRIP_INSTALLED_PRODUCT=NO (SYNC WAVE 2 item 2): crash handling is in-process
# backtrace_symbols_fd, which reads the symbol table at runtime — a stripped
# binary makes every shipped crash.txt address-only and unreadable.
# CMAKE_IGNORE_PREFIX_PATH keeps homebrew's macOS libs out of the cross build.
cmake --no-warn-unused-cli -S "$ROOT/vendor/Lighthouse" -B "$BUILD" -GXcode \
    -DCMAKE_SYSTEM_NAME=iOS -DPLATFORM=SIMULATORARM64 \
    -DCMAKE_OSX_SYSROOT=iphonesimulator \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=15.0 -DCMAKE_BUILD_TYPE:STRING=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_XCODE_ATTRIBUTE_STRIP_INSTALLED_PRODUCT=NO \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED=NO \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY="" \
    -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local" \
    -DENABLE_SCRIPTING=OFF \
    "${LIBZIP_ANNEX_K[@]}" \
    "-DLIGHTHOUSE_IOS_VERSION=$VERSION" "-DLIGHTHOUSE_IOS_BUILD=$BUILDNO" \
    "-DLIGHTHOUSE_REMOTE_CONSOLE=$CONSOLE" \
    "-DLIGHTHOUSE_IOS_DEPS_PREFIX=$PREFIX" \
    "-DLIGHTHOUSE_O2R_PATH=$PORT_O2R" \
    "-DLIGHTHOUSE_IOS_SHELL_DIR=$ROOT/app/ios"

# Build the app target explicitly: TorchExternal is a HOST tool (the CLI used by
# ExtractAssets/GeneratePortO2R) and has no business in a cross build.
cmake --build "$BUILD" --config Release --target Lighthouse --parallel "$JOBS"

APP="$BUILD/Release-iphonesimulator/Lighthouse.app"
[ -d "$APP" ] || die "expected app at $APP"
lipo -info "$APP/Lighthouse"
info "built (simulator): $APP"
