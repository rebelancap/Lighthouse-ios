#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/Lighthouse"
BUILD="$VENDOR/build-cmake"
JOBS="${JOBS:-6}"   # spec: --parallel 6 max, shared box

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$VENDOR/.git" ] || die "vendor/Lighthouse missing — run scripts/bootstrap.sh first"

info "Configuring (Release, Ninja)"
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
