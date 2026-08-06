#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/spikes/lighthouse-sim-build"
PREFIX="$ROOT/work/ios-sim-deps/prefix"
PORT_O2R="$ROOT/oracle/shiphome/lighthouse.o2r"
JOBS="${JOBS:-6}"   # spec: --parallel 6 max on this shared box

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$ROOT/vendor/Lighthouse/.git" ] || "$ROOT/scripts/bootstrap.sh"
"$ROOT/scripts/apply-overlay.sh"
[ -f "$PREFIX/lib/libvorbisfile.a" ] || LIGHTHOUSE_IOS_SDK=simulator "$ROOT/scripts/build-audio-deps-ios.sh"
[ -f "$PORT_O2R" ] || die "missing $PORT_O2R — run scripts/build-oracle.sh (GeneratePortO2R)"

LIBZIP_ANNEX_K=(
    -DHAVE_MEMCPY_S=OFF
    -DHAVE_STRNCPY_S=OFF
    -DHAVE_STRERROR_S=OFF
    -DHAVE_STRERRORLEN_S=OFF
)

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILDNO="$(date -u +%Y%m%d%H%M)"
CONSOLE="${LIGHTHOUSE_REMOTE_CONSOLE:-ON}"
info "lighthouse sim $VERSION (build $BUILDNO), remote console: $CONSOLE"

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

cmake --build "$BUILD" --config Release --target Lighthouse --parallel "$JOBS"

APP="$BUILD/Release-iphonesimulator/Lighthouse.app"
[ -d "$APP" ] || die "expected app at $APP"
lipo -info "$APP/Lighthouse"
info "built (simulator): $APP"
