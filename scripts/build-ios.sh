#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build-ios"
PREFIX="$ROOT/work/ios-deps/prefix"
PORT_O2R="$ROOT/oracle/shiphome/lighthouse.o2r"
TEAM="${LIGHTHOUSE_IOS_TEAM:?set your Apple Developer team id (see README)}"
JOBS="${JOBS:-6}"

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$ROOT/vendor/Lighthouse/.git" ] || "$ROOT/scripts/bootstrap.sh"
"$ROOT/scripts/apply-overlay.sh"
[ -f "$PREFIX/lib/libvorbisfile.a" ] || LIGHTHOUSE_IOS_SDK=device "$ROOT/scripts/build-audio-deps-ios.sh"
[ -f "$PORT_O2R" ] || die "missing $PORT_O2R — run scripts/build-oracle.sh (GeneratePortO2R)"

APP_PATH="$BUILD/Release-iphoneos/Lighthouse.app"
if [ -L "$APP_PATH" ]; then
    info "clearing archive-poisoned product symlink (SYNC WAVE 2 trap)"
    rm -f "$APP_PATH"
fi

LIBZIP_ANNEX_K=(
    -DHAVE_MEMCPY_S=OFF -DHAVE_STRNCPY_S=OFF
    -DHAVE_STRERROR_S=OFF -DHAVE_STRERRORLEN_S=OFF
)

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILDNO="$(date -u +%Y%m%d%H%M)"
CONSOLE="${LIGHTHOUSE_REMOTE_CONSOLE:-ON}"
info "lighthouse $VERSION (build $BUILDNO), remote console: $CONSOLE"

cmake --no-warn-unused-cli -S "$ROOT/vendor/Lighthouse" -B "$BUILD" -GXcode \
    -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_SYSROOT=iphoneos \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=15.0 -DCMAKE_BUILD_TYPE:STRING=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_XCODE_ATTRIBUTE_STRIP_INSTALLED_PRODUCT=NO \
    -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local" \
    -DENABLE_SCRIPTING=OFF \
    "${LIBZIP_ANNEX_K[@]}" \
    "-DLIGHTHOUSE_IOS_VERSION=$VERSION" "-DLIGHTHOUSE_IOS_BUILD=$BUILDNO" \
    "-DLIGHTHOUSE_REMOTE_CONSOLE=$CONSOLE" \
    "-DLIGHTHOUSE_IOS_DEPS_PREFIX=$PREFIX" \
    "-DLIGHTHOUSE_O2R_PATH=$PORT_O2R" \
    "-DLIGHTHOUSE_IOS_SHELL_DIR=${LIGHTHOUSE_SHELL_DIR_OVERRIDE:-$ROOT/app/ios}" \
    -DLIGHTHOUSE_IOS_BUNDLE_IDENTIFIER=com.rebelancap.lighthouse \
    "-DLIGHTHOUSE_IOS_DEVELOPMENT_TEAM=$TEAM"

cmake --build "$BUILD" --config Release --target Lighthouse --parallel "$JOBS" -- -allowProvisioningUpdates

APP="$BUILD/Release-iphoneos/Lighthouse.app"
[ -d "$APP" ] || die "expected app at $APP"
codesign -dv "$APP" 2>&1 | sed -n '1,3p'
info "built (device): $APP"
