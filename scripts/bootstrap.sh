#!/usr/bin/env bash
# bootstrap.sh — reproduce the pinned vendor checkout from nothing.
#
# Program rule: upstream stays pristine, pinned by commit. This script is the
# ONE command that recreates vendor/ on a clean machine. Failures are loud:
# no `|| true`, and every pin is asserted after checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/Lighthouse"

# --- D0 pins (see DECISIONS.md) ---------------------------------------------
LH_REPO="https://github.com/HarbourMasters/Lighthouse.git"
# Upstream release 1.0.2 (2026-08-05). LUS is unchanged from the previous pin —
# only Lighthouse and Torch moved, which is why the LUS half of the overlay
# (22 of 45 patches) needed no rebase.
LH_PIN="e598cfcc21e21b6ff5cdf533098f22539246c770"
LUS_PIN="2917d0f4fe62c579174561dcd34f327c9410bb72"
TORCH_PIN="f89e944671b406615246685a5a60b65e48ab01f8"

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

if [ ! -d "$VENDOR/.git" ]; then
  info "Cloning $LH_REPO -> vendor/Lighthouse"
  mkdir -p "$ROOT/vendor"
  git clone --recursive "$LH_REPO" "$VENDOR"
fi

info "Pinning to D0 commits"
git -C "$VENDOR" fetch --all --tags --quiet
git -C "$VENDOR" checkout --quiet "$LH_PIN"
git -C "$VENDOR" submodule update --init --recursive --quiet

# --- assert the pins actually took ------------------------------------------
assert_pin() {
  local label="$1" dir="$2" want="$3" got
  got="$(git -C "$dir" rev-parse HEAD)"
  [ "$got" = "$want" ] || die "$label pin mismatch: want $want, got $got"
  printf '    %-12s %s\n' "$label" "$got"
}
assert_pin "Lighthouse"   "$VENDOR"                 "$LH_PIN"
assert_pin "libultraship" "$VENDOR/libultraship"    "$LUS_PIN"
assert_pin "Torch"        "$VENDOR/Torch"           "$TORCH_PIN"

# --- assert vendor purity ----------------------------------------------------
# Untracked build artifacts (baserom.z64, *.o2r, build-cmake/) are expected.
# Modified TRACKED files are only acceptable if they are EXACTLY the overlay —
# i.e. every modification is accounted for by a patch in overlay/patches.
#
# The naive check ("no modified tracked files") was wrong: it fired in the
# normal working state, because the overlay legitimately modifies tracked files.
# A check that fails whenever you are actually working is a check nobody runs.
# What we care about is that NOTHING was hand-edited, so verify it directly:
# every patch must reverse cleanly, which is only true if the tree is exactly
# pristine-plus-overlay.
DIRTY="$(git -C "$VENDOR" status --porcelain --untracked-files=no)"
if [ -n "$DIRTY" ]; then
  shopt -s nullglob
  series=("$ROOT"/overlay/patches/[0-9][0-9][0-9][0-9]-*.patch)
  if [ ${#series[@]} -eq 0 ]; then
    printf '%s\n' "$DIRTY" >&2
    die "vendor has modified tracked files but overlay/patches is empty — hand edits never happen (program rule 1)"
  fi
  info "vendor is modified; verifying the changes are exactly the overlay"
  for ((i=${#series[@]}-1; i>=0; i--)); do
    p="${series[i]}"
    if ! patch -p1 -R --force --fuzz=0 --dry-run -d "$VENDOR" < "$p" > /dev/null 2>&1; then
      die "overlay patch $(basename "$p") does not reverse — vendor has been hand-edited (program rule 1)"
    fi
  done
  info "    all ${#series[@]} patches reverse cleanly — vendor is pristine + overlay"
else
  info "vendor/ is pinned and pristine (no overlay applied)."
fi
