#!/usr/bin/env bash
# Run the macOS oracle — the same port built natively on this Mac, with the
# same game data. Charter: the permanent ground-truth reference for every
# visual, feel and (now) AUDIO comparison.
#
# Why it settles audio questions: the macOS build compiles the SAME synth code
# as iOS, including the same arm64 NEON paths in src/port/Audio/mixer.c (Fable's
# audit compiled both and proved them bit-exact), and every iOS overlay patch is
# __IOS__-guarded, so this binary runs UPSTREAM audio behaviour — including the
# same 22000 Hz / 736-sample device config. If a defect is audible here too, it
# is upstream Lighthouse and not the iOS port.
#
# The game finds lighthouse.o2r / bk.o2r / gamecontrollerdb.txt by RELATIVE
# path, so the working directory has to be the build dir. That is the only
# non-obvious part, and the whole reason this script exists.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/vendor/Lighthouse/build-cmake"
BIN="$BUILD/Lighthouse"

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -x "$BIN" ] || die "no oracle binary at $BIN — run scripts/build-oracle.sh"
[ -f "$BUILD/lighthouse.o2r" ] || die "missing $BUILD/lighthouse.o2r (the port assets + LUS shaders; the renderer will not come up without it)"
[ -f "$BUILD/bk.o2r" ] || die "missing $BUILD/bk.o2r — copy it from $ROOT/oracle/shiphome/bk.o2r"

info "oracle: $BIN"
info "built:  $(date -r "$BIN" '+%Y-%m-%d %H:%M')"
# Report the audio config the way the iOS build does, so a listening comparison
# is against known numbers rather than an assumption.
info "audio:  upstream defaults (22000 Hz, 736-sample frames) — __IOS__ overrides do not apply here"

cd "$BUILD"
exec ./Lighthouse "$@"
