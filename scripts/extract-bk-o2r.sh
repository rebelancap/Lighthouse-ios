#!/usr/bin/env bash
# extract-bk-o2r.sh — replicate the in-app extractor's argv exactly.
#
# Why "exactly": the on-device onboarding runs the SAME Torch/Companion code
# in-process. Keeping the scripted path argv-identical is what makes the
# desktop timing (MEASUREMENTS M-001) a legitimate baseline for the device
# budget, and what makes an output mismatch mean something.
#
# Upstream's ExtractAssets target is:
#     WORKING_DIRECTORY <source dir>
#     COMMAND <torch> o2r baserom.z64
# so the ROM must be at vendor/Lighthouse/baserom.z64 and bk.o2r is written
# beside it. Both are untracked artifacts (.gitignore) -- NOT source edits.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/Lighthouse"
TORCH="$VENDOR/build-cmake/TorchExternal/src/TorchExternal-build/torch"
ROM_SRC="${ROM_SRC:-$ROOT/work/gamedata/baserom.us.v11.z64}"

# Upstream's four supported retail hashes (src/port/Extractor/GameExtractor.cpp).
declare -a SUPPORTED=(
  1fe1632098865f639e22c11b9a81ee8f29c75d7a  # US v1.0
  ded6ee166e740ad1bc810fd678a84b48e245ab80  # US v1.1
  90726d7e7cd5bf6cdfd38f45c9acbf4d45bd9fd8  # Japan
  bb359a75941df74bf7290212c89fbc6e2c5601fe  # PAL
)

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -f "$ROM_SRC" ] || die "ROM not found: $ROM_SRC"
[ -x "$TORCH" ] || die "torch not built: $TORCH (run scripts/build-oracle.sh)"

SHA="$(shasum -a 1 "$ROM_SRC" | cut -d' ' -f1)"
MATCH=no
for h in "${SUPPORTED[@]}"; do [ "$SHA" = "$h" ] && MATCH=yes; done
if [ "$MATCH" = yes ]; then
  info "ROM SHA-1 $SHA — recognised retail dump"
else
  # NOT fatal, deliberately: upstream has no boot-time hash gate and supports
  # ROM hacks via synthesized configs (DECISIONS D5). Mirror that here.
  info "ROM SHA-1 $SHA — not a known retail hash; treating as a ROM hack"
fi

info "Staging ROM as vendor/Lighthouse/baserom.z64"
cp "$ROM_SRC" "$VENDOR/baserom.z64"
chmod 644 "$VENDOR/baserom.z64"

info "Extracting (this is minutes, not seconds — see MEASUREMENTS M-001)"
START=$(date +%s)
( cd "$VENDOR" && "$TORCH" o2r baserom.z64 )
ELAPSED=$(( $(date +%s) - START ))

[ -f "$VENDOR/bk.o2r" ] || die "torch reported success but bk.o2r was not produced"
SIZE=$(stat -f%z "$VENDOR/bk.o2r")
info "bk.o2r: $SIZE bytes in ${ELAPSED}s"

# NON-OPTIONAL. `torch o2r` does not write the `portVersion` record -- only the
# in-app extractor does (GameExtractor::WritePortVersion). Without it, boot
# computes {0,0,0}, decides the archive is outdated, and DELETES it
# (Engine.cpp: shouldRegen -> std::filesystem::remove). Cost of learning this:
# one 18-minute extraction. See DECISIONS D7.
info "Stamping portVersion (else the game deletes this archive on first boot)"
PROJ_VERSION="$(sed -n 's/^project(Lighthouse VERSION \([0-9.]*\).*/\1/p' "$VENDOR/CMakeLists.txt" | head -1)"
[ -n "$PROJ_VERSION" ] || die "could not read project version from vendor CMakeLists.txt"
python3 "$ROOT/scripts/stamp-port-version.py" "$VENDOR/bk.o2r" --version "$PROJ_VERSION"

# Prove it took, rather than trusting the stamper's own word.
python3 - "$VENDOR/bk.o2r" <<'PY' || die "portVersion verification failed"
import sys, zipfile, struct
with zipfile.ZipFile(sys.argv[1]) as z:
    rec = z.read("portVersion")
assert len(rec) == 6, f"portVersion is {len(rec)} bytes, expected 6"
print("    portVersion verified: %d.%d.%d" % struct.unpack(">HHH", rec))
PY

mkdir -p "$ROOT/oracle/shiphome"
cp "$VENDOR/bk.o2r" "$ROOT/oracle/shiphome/bk.o2r"
info "Copied to oracle/shiphome/bk.o2r"
