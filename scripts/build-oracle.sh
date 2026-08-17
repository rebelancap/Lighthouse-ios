#!/usr/bin/env bash
# build-oracle.sh — build the permanent macOS ground-truth reference.
#
# The oracle is the same port built natively on this Mac with the same game
# data. It is the reference for every visual/feel comparison, and (uniquely for
# this port) the co-op peer for phone<->Mac Anchor testing. It must stay green
# on every upstream bump.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/Lighthouse"
BUILD="$VENDOR/build-cmake"
JOBS="${JOBS:-6}"   # charter: --parallel 6 max, shared box

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$VENDOR/.git" ] || die "vendor/Lighthouse missing — run scripts/bootstrap.sh first"

info "Configuring (Release, Ninja)"
# CMAKE_POLICY_VERSION_MINIMUM: CMake 4 rejects the old deps otherwise (known trap).
cmake -S "$VENDOR" -B "$BUILD" -GNinja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5

info "Building port assets archive (lighthouse.o2r)"
cmake --build "$BUILD" --target GeneratePortO2R

info "Building Lighthouse (-j$JOBS)"
cmake --build "$BUILD" --parallel "$JOBS"

BIN="$BUILD/Lighthouse"
[ -x "$BIN" ] || die "expected binary not produced: $BIN"
info "Oracle binary: $BIN ($(stat -f%z "$BIN") bytes)"
