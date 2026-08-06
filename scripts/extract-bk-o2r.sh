#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/Lighthouse"
TORCH="$VENDOR/build-cmake/TorchExternal/src/TorchExternal-build/torch"
ROM_SRC="${ROM_SRC:-$ROOT/work/gamedata/baserom.us.v11.z64}"

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

info "Stamping portVersion (else the game deletes this archive on first boot)"
PROJ_VERSION="$(sed -n 's/^project(Lighthouse VERSION \([0-9.]*\).*/\1/p' "$VENDOR/CMakeLists.txt" | head -1)"
[ -n "$PROJ_VERSION" ] || die "could not read project version from vendor CMakeLists.txt"
python3 "$ROOT/scripts/stamp-port-version.py" "$VENDOR/bk.o2r" --version "$PROJ_VERSION"

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
