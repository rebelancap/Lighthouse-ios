#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK="${LIGHTHOUSE_IOS_SDK:-device}"
SYSNAME=iOS
DEPTGT=15.0
if [[ "$SDK" == "simulator" ]]; then
    WORK="$ROOT/work/ios-sim-deps"
    SYSROOT_FLAG=(-DCMAKE_OSX_SYSROOT=iphonesimulator)
elif [[ "$SDK" == "visionsim" ]]; then
    WORK="$ROOT/work/vision-sim-deps"
    SYSROOT_FLAG=(-DCMAKE_OSX_SYSROOT=xrsimulator)
    SYSNAME=visionOS
    DEPTGT=2.0
elif [[ "$SDK" == "visionos" ]]; then
    WORK="$ROOT/work/vision-deps"
    SYSROOT_FLAG=(-DCMAKE_OSX_SYSROOT=xros)
    SYSNAME=visionOS
    DEPTGT=2.0
else
    WORK="$ROOT/work/ios-deps"
    SYSROOT_FLAG=(-DCMAKE_OSX_SYSROOT=iphoneos)
fi
PREFIX="$WORK/prefix"
SRC="$WORK/src"
IOS_FLAGS=(-DCMAKE_SYSTEM_NAME=$SYSNAME -DCMAKE_OSX_DEPLOYMENT_TARGET=$DEPTGT
           -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_BUILD_TYPE=Release
           "${SYSROOT_FLAG[@]}"
           -DBUILD_SHARED_LIBS=OFF "-DCMAKE_INSTALL_PREFIX=$PREFIX"
           "-DCMAKE_PREFIX_PATH=$PREFIX"
           "-DCMAKE_FIND_ROOT_PATH=$PREFIX"
           -DCMAKE_POLICY_VERSION_MINIMUM=3.5)
mkdir -p "$SRC" "$PREFIX"

fetch() { # name url tag
    if [[ ! -d "$SRC/$1" ]]; then
        git clone -q --depth 1 --branch "$3" "$2" "$SRC/$1"
    fi
}

BDIR="build-$SDK"
build() { # name extra-args...
    local name="$1"; shift
    echo "=== $name ($SDK) ==="
    cmake -S "$SRC/$name" -B "$SRC/$name/$BDIR" -GNinja "${IOS_FLAGS[@]}" "$@"
    cmake --build "$SRC/$name/$BDIR" --parallel
    cmake --install "$SRC/$name/$BDIR"
}

fetch ogg      https://github.com/xiph/ogg.git      v1.3.6
fetch vorbis   https://github.com/xiph/vorbis.git   v1.3.7
fetch libpng   https://github.com/pnggroup/libpng.git v1.6.50

build libpng   -DPNG_SHARED=OFF -DPNG_STATIC=ON -DPNG_TESTS=OFF -DPNG_TOOLS=OFF -DPNG_FRAMEWORK=OFF
build ogg      -DBUILD_TESTING=OFF -DINSTALL_DOCS=OFF
build vorbis   "-DOGG_INCLUDE_DIR=$PREFIX/include" "-DOGG_LIBRARY=$PREFIX/lib/libogg.a"

echo "=== verify ($SDK) ==="
case "$SDK" in
    simulator) WANT="IOSSIMULATOR|7" ;;
    visionos)  WANT="XROS|11" ;;
    visionsim) WANT="XROS_SIMULATOR|XROSSIMULATOR|12" ;;
    *)         WANT="IOS|2" ;;
esac
for lib in libogg libvorbis libvorbisfile libvorbisenc libpng16; do
    f="$PREFIX/lib/$lib.a"
    [[ -f "$f" ]] || { echo "FATAL: missing $f" >&2; exit 1; }
    lipo -info "$f" | grep -q arm64 || { echo "FATAL: $f not arm64" >&2; exit 1; }
    plat=$(otool -l "$f" 2>/dev/null | awk '/LC_BUILD_VERSION/{f=1} f&&/platform/{print $2; exit}')
    [[ "$plat" =~ ^($WANT)$ ]] || { echo "FATAL: $f platform=$plat, expected $WANT" >&2; exit 1; }
done
echo "audio deps OK ($SDK): $PREFIX"
