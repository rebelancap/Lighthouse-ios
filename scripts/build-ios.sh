#!/usr/bin/env bash
# One command: pristine vendor -> overlay -> signed iOS DEVICE build.
# Produces build-ios/Release-iphoneos/Lighthouse.app
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build-ios"
PREFIX="$ROOT/work/ios-deps/prefix"
PORT_O2R="$ROOT/oracle/shiphome/lighthouse.o2r"
TEAM="${LIGHTHOUSE_IOS_TEAM:?set LIGHTHOUSE_IOS_TEAM to your Apple Developer Team ID}"
JOBS="${JOBS:-6}"

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$ROOT/vendor/Lighthouse/.git" ] || "$ROOT/scripts/bootstrap.sh"
"$ROOT/scripts/apply-overlay.sh"
[ -f "$PREFIX/lib/libvorbisfile.a" ] || LIGHTHOUSE_IOS_SDK=device "$ROOT/scripts/build-audio-deps-ios.sh"
[ -f "$PORT_O2R" ] || die "missing $PORT_O2R — run scripts/build-oracle.sh (GeneratePortO2R)"

# SYNC WAVE 2 trap — ARCHIVE-POISONED BUILD DIR. `xcodebuild archive` replaces
# the regular-build product with a SYMLINK into ArchiveIntermediates, which is
# then deleted. The next ordinary build dies with a bewildering
#   error: unable to create directory '.../Release-iphoneos/Lighthouse.app'
# because it will not write through a dangling symlink. Clear it if present —
# surgical, so we keep the incremental build instead of wiping the whole dir.
APP_PATH="$BUILD/Release-iphoneos/Lighthouse.app"
if [ -L "$APP_PATH" ]; then
    info "clearing archive-poisoned product symlink (SYNC WAVE 2 trap)"
    rm -f "$APP_PATH"
fi

# See build-sim.sh for why these exist; they apply identically to the device build.
LIBZIP_ANNEX_K=(
    -DHAVE_MEMCPY_S=OFF -DHAVE_STRNCPY_S=OFF
    -DHAVE_STRERROR_S=OFF -DHAVE_STRERRORLEN_S=OFF
)

# Versioning (PUBLISHING-CONVENTIONS §1): VERSION is the PUBLIC version and only
# moves when a release is cut; the build number moves every build so OTA test
# iterations are distinguishable without burning a public version number.
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILDNO="$(date -u +%Y%m%d%H%M)"
# Console ON by default: nearly every build is an OTA test build, which is
# exactly where the bridge earns its keep. scripts/build-release.sh turns it off
# for the comparatively rare public release and asserts it is gone.
CONSOLE="${LIGHTHOUSE_REMOTE_CONSOLE:-ON}"
info "lighthouse $VERSION (build $BUILDNO), remote console: $CONSOLE"

# PLATFORM defaults to OS64 for device (overlay 0038 made it overridable without
# changing that default). -GXcode is required, not preferred (D9).
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
# sed reads all input; `head -3` would SIGPIPE codesign and exit 141 under pipefail.
codesign -dv "$APP" 2>&1 | sed -n '1,3p'
info "built (device): $APP"
