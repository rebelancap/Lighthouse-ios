#!/usr/bin/env bash
# Install + launch Lighthouse on THIS session's simulator and capture a
# screenshot. Ladder step 3 is "app boots on sim + CONTENT screenshot" — never
# trust logs alone: the flagship once measured "120 fps" over a black screen.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/spikes/lighthouse-sim-build/Release-iphonesimulator/Lighthouse.app"
# D8 — this session's OWN simulator. Never boot a sibling session's.
UDID="${LIGHTHOUSE_SIM_UDID:?set LIGHTHOUSE_SIM_UDID to your iOS Simulator UDID (xcrun simctl list devices)}"
BUNDLE_ID="com.rebelancap.lighthouse"
SHOT="${1:-$ROOT/artifacts/sim/boot-$(date -u +%Y%m%d-%H%M%S).png}"

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -d "$APP" ] || die "no app at $APP — run scripts/build-sim.sh"
xcrun simctl list devices | grep -q "$UDID" || die "simulator $UDID not found (see DECISIONS D8)"

mkdir -p "$(dirname "$SHOT")"

info "Booting $UDID (idempotent)"
xcrun simctl bootstatus "$UDID" -b >/dev/null 2>&1 || xcrun simctl boot "$UDID" || true
xcrun simctl bootstatus "$UDID" >/dev/null 2>&1 || true

info "Installing $APP"
xcrun simctl install "$UDID" "$APP"

# Baseline: what the screen looks like with the app NOT running. Compared
# against the post-launch shot below — because "the frame is not black" is NOT
# evidence the app is up: the iOS springboard is colourful and passes that test
# trivially. This script asserted a pass over a home-screen screenshot once.
info "Capturing springboard baseline"
xcrun simctl terminate "$UDID" "$BUNDLE_ID" >/dev/null 2>&1 || true
BASE="$ROOT/work/sim-springboard.png"
xcrun simctl io "$UDID" screenshot "$BASE" >/dev/null 2>&1 || die "baseline screenshot failed"

# Env vars for the app must carry the SIMCTL_CHILD_ prefix. Trailing tokens on
# `simctl launch` become ARGV, not environment — passing LIGHTHOUSE_CONSOLE=1
# bare made argc=2 and tripped an upstream argv[argc] null-deref (patch 0039).
info "Launching $BUNDLE_ID"
SIMCTL_CHILD_LIGHTHOUSE_CONSOLE=1 \
    xcrun simctl launch --console-pty "$UDID" "$BUNDLE_ID" \
    >"$ROOT/work/sim-run.log" 2>&1 &
LAUNCH_PID=$!

# First launch compiles every Metal shader (30-45 s on the flagship); the app
# also has to find its bundled resources before anything renders. Give it room,
# then capture. simctl io screenshot grabs the Metal + UIKit composite, which is
# the whole point — the Simulator bezel window lies about rotation.
SECS="${LIGHTHOUSE_SIM_SETTLE:-60}"
info "Settling ${SECS}s before screenshot (first launch builds the shader cache)"
END=$((SECONDS + SECS))
while [ $SECONDS -lt $END ]; do sleep 5; done

xcrun simctl io "$UDID" screenshot "$SHOT" || die "screenshot failed"
info "screenshot: $SHOT"

# Three things must ALL hold for this to count as a boot:
#   1. the process is still alive (a crashed app leaves the springboard up),
#   2. the frame is not black (the classic "120 fps over a black screen"),
#   3. the frame DIFFERS from the springboard baseline (the app is actually
#      presenting, not merely running headless behind the home screen).
# launchctl lists a live app as "UIKitApplication:<bundle-id>[...]". Run the
# pipeline in a subshell without pipefail: simctl spawn is chatty on stderr and
# a non-zero exit there would otherwise be misread as "app dead" (it was).
ALIVE=0
if (set +o pipefail; xcrun simctl spawn "$UDID" launchctl list 2>/dev/null \
        | grep -q "UIKitApplication:$BUNDLE_ID"); then
    ALIVE=1
fi

python3 - "$SHOT" "$BASE" "$ALIVE" <<'PY'
import sys
import numpy as np
from PIL import Image

shot, base, alive = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
a = np.asarray(Image.open(shot).convert("RGB"), dtype=np.uint8)
b = np.asarray(Image.open(base).convert("RGB"), dtype=np.uint8)

nonblack = float((a.max(axis=2) > 12).mean())
uniq = len(np.unique(a.reshape(-1, 3), axis=0))
differs = float((np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > 16).mean()) \
    if a.shape == b.shape else 1.0

print(f"    process alive:   {alive}")
print(f"    non-black:       {nonblack*100:.1f}%   distinct colours: {uniq}")
print(f"    differs from springboard: {differs*100:.1f}% of pixels")

fail = []
if not alive:
    fail.append("process is NOT running (check for a crash .ips)")
if nonblack < 0.02 or uniq < 8:
    fail.append("frame is black/near-empty")
if differs < 0.10:
    fail.append("frame is ~identical to the springboard — the app is not presenting")
if fail:
    print("    VERDICT: NOT A PASSING BOOT — " + "; ".join(fail), file=sys.stderr)
    raise SystemExit(1)
print("    VERDICT: app is running and presenting its own frame")
PY

kill "$LAUNCH_PID" 2>/dev/null || true
info "done — log at work/sim-run.log"
