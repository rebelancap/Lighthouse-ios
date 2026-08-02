#!/usr/bin/env bash
# One command: pristine vendor -> overlay -> signed visionOS DEVICE build.
# Produces build-visionos/Release-xros/Lighthouse.app
# The iOS build (build-ios/) and the vision simulator build (build-vision-sim/)
# are untouched — three separate build dirs, one source tree.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build-visionos"
PREFIX="$ROOT/work/vision-deps/prefix"
PORT_O2R="$ROOT/oracle/shiphome/lighthouse.o2r"
TEAM="${LIGHTHOUSE_IOS_TEAM:?set LIGHTHOUSE_IOS_TEAM to your Apple Developer Team ID}"
JOBS="${JOBS:-6}"

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$ROOT/vendor/Lighthouse/.git" ] || "$ROOT/scripts/bootstrap.sh"
"$ROOT/scripts/apply-overlay.sh"
[ -f "$PREFIX/lib/libvorbisfile.a" ] || LIGHTHOUSE_IOS_SDK=visionos "$ROOT/scripts/build-audio-deps-ios.sh"
[ -f "$PORT_O2R" ] || die "missing $PORT_O2R — run scripts/build-oracle.sh (GeneratePortO2R)"

# SYNC WAVE 2 trap — see build-ios.sh: `xcodebuild archive` leaves a dangling
# symlink where the regular-build product goes, and the next build dies with an
# "unable to create directory" that has nothing to do with permissions.
APP_PATH="$BUILD/Release-xros/Lighthouse.app"
if [ -L "$APP_PATH" ]; then
    info "clearing archive-poisoned product symlink"
    rm -f "$APP_PATH"
fi

LIBZIP_ANNEX_K=(
    -DHAVE_MEMCPY_S=OFF -DHAVE_STRNCPY_S=OFF
    -DHAVE_STRERROR_S=OFF -DHAVE_STRERRORLEN_S=OFF
)

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILDNO="$(date -u +%Y%m%d%H%M)"
CONSOLE="${LIGHTHOUSE_REMOTE_CONSOLE:-ON}"
info "lighthouse visionOS $VERSION (build $BUILDNO), remote console: $CONSOLE"

# STRIP_INSTALLED_PRODUCT=NO is not cosmetic: the crash handler resolves its own
# backtrace at RUNTIME, so a stripped binary ships address-only crash.txt files.
# publish-ota.sh asserts the symbol count for exactly this reason.
cmake --no-warn-unused-cli -S "$ROOT/vendor/Lighthouse" -B "$BUILD" -GXcode \
    -DCMAKE_SYSTEM_NAME=visionOS -DPLATFORM=VISIONOS \
    -DCMAKE_OSX_SYSROOT=xros \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=2.0 -DCMAKE_BUILD_TYPE:STRING=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_XCODE_ATTRIBUTE_XROS_DEPLOYMENT_TARGET=2.0 \
    -DCMAKE_XCODE_ATTRIBUTE_STRIP_INSTALLED_PRODUCT=NO \
    -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local" \
    -DENABLE_SCRIPTING=OFF \
    -DSDL_OPENGLES=OFF -DSDL_OPENGL=OFF \
    "${LIBZIP_ANNEX_K[@]}" \
    "-DLIGHTHOUSE_IOS_VERSION=$VERSION" "-DLIGHTHOUSE_IOS_BUILD=$BUILDNO" \
    "-DLIGHTHOUSE_REMOTE_CONSOLE=$CONSOLE" \
    "-DLIGHTHOUSE_IOS_DEPS_PREFIX=$PREFIX" \
    "-DLIGHTHOUSE_O2R_PATH=$PORT_O2R" \
    "-DLIGHTHOUSE_IOS_SHELL_DIR=$ROOT/app/ios" \
    -DLIGHTHOUSE_IOS_BUNDLE_IDENTIFIER=com.rebelancap.lighthouse \
    "-DLIGHTHOUSE_IOS_DEVELOPMENT_TEAM=$TEAM"

cmake --build "$BUILD" --config Release --target Lighthouse --parallel "$JOBS" -- -allowProvisioningUpdates

APP="$BUILD/Release-xros/Lighthouse.app"
[ -d "$APP" ] || die "expected app at $APP"
codesign -dv "$APP" 2>&1 | sed -n '1,3p'
# Substitute, don't pipe — see build-vision-sim.sh: `nm | grep -q` SIGPIPEs nm
# and pipefail reports a failure on a perfectly good binary.
SWIFT_ENTRY=$(nm "$APP/Lighthouse" 2>/dev/null | grep -c "SohVisionApp" || true)
[ "$SWIFT_ENTRY" -gt 0 ] || die "no Swift app entry in the binary — force_load of lighthousevisionswift did not take"
info "    Swift @main entry present ($SWIFT_ENTRY symbols)"
info "built (visionOS device): $APP"
