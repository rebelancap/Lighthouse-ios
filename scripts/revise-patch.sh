#!/usr/bin/env bash
# Reverse ONE overlay patch so its generator can be re-run against the state it
# was written against, then regenerate and re-apply the whole series.
#
#   scripts/revise-patch.sh 0031 [0042 ...]
#
# WHY THIS EXISTS. Doing this by hand — `patch -R ... 2>/dev/null` in a loop —
# silently swallowed a failed reverse three times in one session. Each time the
# generator then ran against a PARTIALLY PATCHED tree and wrote a patch whose
# context encoded another patch's edits. That is invisible until
# overlay-drill.sh's step 2 catches it, or until a clean checkout fails to
# build. The reverse must be LOUD (program ground rule 3: failures are loud).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/Lighthouse"
PATCHES="$ROOT/overlay/patches"

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ $# -ge 1 ] || die "usage: revise-patch.sh <NNNN> [NNNN ...]"

# Reverse newest-first so overlapping context unwinds in the right order.
for n in $(printf '%s\n' "$@" | sort -r); do
    shopt -s nullglob
    files=("$PATCHES"/"$n"-*.patch)
    [ ${#files[@]} -eq 1 ] || die "expected exactly one patch matching $n-*, found ${#files[@]}"
    p="${files[0]}"
    info "reversing $(basename "$p")"
    # No 2>/dev/null, no || true. If this cannot reverse cleanly, stop —
    # regenerating from here would produce a patch against a hybrid tree.
    patch -p1 -R --force --fuzz=0 -d "$VENDOR" < "$p" \
        || die "could not reverse $(basename "$p") cleanly. The tree is now in a
mixed state; restore with:  git -C vendor/Lighthouse checkout -- .  &&
scripts/apply-overlay.sh"
done
find "$VENDOR" \( -name '*.orig' -o -name '*.rej' \) -delete

info "edit the generator(s) now, then press Enter to regenerate + re-apply"
info "  (or Ctrl-C — the tree is missing $* until you re-apply)"
read -r _

for n in "$@"; do
    info "regenerating gen-patch-$n.py"
    python3 "$ROOT/scripts/gen-patch-$n.py" || die "generator $n failed"
done

"$ROOT/scripts/apply-overlay.sh"
info "now run scripts/overlay-drill.sh before building — it is the only thing"
info "that catches a patch whose context drifted from its generator"
