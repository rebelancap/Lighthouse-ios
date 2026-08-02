#!/usr/bin/env bash
# build-release.sh — the ONE build where the remote console is compiled out.
#
# PUBLISHING-CONVENTIONS §2: IPAs published to GitHub/SideStore have the console
# compiled out; every other build keeps it on, because OTA test builds vastly
# outnumber public releases. The bridge is an unauthenticated TCP command server
# (input injection, CVar writes, crash.txt/log reads) and `lighthouse://console`
# means a tapped link can switch it on — so a public build must not contain it
# at all.
#
# This script forces it off, then ASSERTS with `strings` that it is genuinely
# gone rather than trusting the flag. It also asserts the version matches the
# VERSION file and that symbols survived (SYNC WAVE 2 items 2 and 3: crash
# handling is in-process backtrace_symbols_fd, so a stripped binary makes every
# shipped crash.txt address-only and unreadable).
#
# Usage:  scripts/build-release.sh [--sim]
#   (--sim builds a simulator slice, for verifying the console-off path compiles
#    without needing signing. Device builds are the real deliverable.)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM=0
[ "${1:-}" = "--sim" ] && SIM=1

die() { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[ -n "$VERSION" ] || die "VERSION file is empty"

if [ "$SIM" = "1" ]; then
    info "RELEASE-MODE build (simulator slice) — remote console OFF, v$VERSION"
    LIGHTHOUSE_REMOTE_CONSOLE=OFF "$ROOT/scripts/build-sim.sh"
    APP="$ROOT/spikes/lighthouse-sim-build/Release-iphonesimulator/Lighthouse.app"
else
    # --- device release: the actual deliverable ------------------------------
    # Credentials come from the environment with NO hardcoded defaults, so this
    # script is safe to publish. Set them once in your shell:
    #   LIGHTHOUSE_IOS_TEAM, LIGHTHOUSE_ASC_KEY_ID, LIGHTHOUSE_ASC_ISSUER_ID
    KEY_ID="${LIGHTHOUSE_ASC_KEY_ID:?set LIGHTHOUSE_ASC_KEY_ID (App Store Connect API key id)}"
    ISSUER_ID="${LIGHTHOUSE_ASC_ISSUER_ID:?set LIGHTHOUSE_ASC_ISSUER_ID}"
    KEY_PATH="${LIGHTHOUSE_ASC_KEY_PATH:-$HOME/.appstoreconnect/private_keys/AuthKey_${KEY_ID}.p8}"
    TEAM="${LIGHTHOUSE_IOS_TEAM:?set LIGHTHOUSE_IOS_TEAM to your Apple Developer Team ID}"
    [ -f "$KEY_PATH" ] || die "App Store Connect key missing at $KEY_PATH"

    case "${1:---all}" in
        --ios)      PLATFORMS="ios" ;;
        --visionos) PLATFORMS="visionos" ;;
        --all|"")   PLATFORMS="ios visionos" ;;
        *)          die "usage: build-release.sh [--sim|--ios|--visionos|--all]" ;;
    esac

    mkdir -p "$ROOT/release"

    for PLAT in $PLATFORMS; do
        if [ "$PLAT" = "ios" ]; then
            BUILD="$ROOT/build-ios";      DEST='generic/platform=iOS';       ASSET="iOS"
        else
            BUILD="$ROOT/build-visionos"; DEST='generic/platform=visionOS';  ASSET="visionOS"
        fi

        info "RELEASE-MODE build ($PLAT) — remote console OFF, v$VERSION"
        LIGHTHOUSE_REMOTE_CONSOLE=OFF "$ROOT/scripts/build-$PLAT.sh"

        info "  archive"
        xcodebuild -project "$BUILD/Lighthouse.xcodeproj" -scheme Lighthouse \
            -configuration Release -destination "$DEST" \
            archive -archivePath "$BUILD/LighthouseRelease.xcarchive" \
            -allowProvisioningUpdates \
            -authenticationKeyPath "$KEY_PATH" \
            -authenticationKeyID "$KEY_ID" \
            -authenticationKeyIssuerID "$ISSUER_ID" | tail -3

        info "  export IPA"
        cat > "$BUILD/export-release.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>method</key><string>debugging</string>
	<key>teamID</key><string>$TEAM</string>
	<key>signingStyle</key><string>automatic</string>
	<key>stripSwiftSymbols</key><true/>
	<key>compileBitcode</key><false/>
</dict>
</plist>
PLIST
        rm -rf "$BUILD/export-release"
        xcodebuild -exportArchive -archivePath "$BUILD/LighthouseRelease.xcarchive" \
            -exportPath "$BUILD/export-release" -exportOptionsPlist "$BUILD/export-release.plist" \
            -allowProvisioningUpdates \
            -authenticationKeyPath "$KEY_PATH" \
            -authenticationKeyID "$KEY_ID" \
            -authenticationKeyIssuerID "$ISSUER_ID" | tail -3

        IPA=$(ls "$BUILD/export-release/"*.ipa 2>/dev/null | head -1)
        [ -f "$IPA" ] || die "no IPA produced for $PLAT"

        # --- the three assertions this script exists for ---------------------
        WORKDIR=$(mktemp -d)
        unzip -q "$IPA" -d "$WORKDIR"
        APPBIN="$WORKDIR/Payload/Lighthouse.app/Lighthouse"
        [ -f "$APPBIN" ] || die "no app binary inside the IPA"

        # 1. the remote console is genuinely gone — assert, do not trust the flag
        HITS=$(strings -a "$APPBIN" 2>/dev/null | grep -c "console bridge listening" || true)
        [ "$HITS" = "0" ] || die "REMOTE CONSOLE IS PRESENT in the $PLAT release IPA \
($HITS marker strings). This build must not be published."
        info "    remote console: absent"

        # 2. symbols survived (crash.txt uses backtrace_symbols_fd at runtime)
        SYMS=$(nm "$APPBIN" 2>/dev/null | wc -l | tr -d ' ')
        [ "$SYMS" -gt 10000 ] || die "only $SYMS symbols — binary is STRIPPED; \
every shipped crash.txt would be address-only"
        info "    symbols: $SYMS"

        # 3. the shipped version matches VERSION (SideStore compares this string)
        PLISTV=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" \
            "$WORKDIR/Payload/Lighthouse.app/Info.plist" 2>/dev/null || echo "")
        rm -rf "$WORKDIR"
        [ "$PLISTV" = "$VERSION" ] || die "IPA reports version '$PLISTV' but VERSION says '$VERSION'"
        info "    version: $PLISTV"

        OUT="$ROOT/release/lighthouse-$VERSION-$ASSET.ipa"
        cp "$IPA" "$OUT"
        info "  -> $(basename "$OUT")  ($(du -h "$OUT" | cut -f1))"
    done

    info "release assets in $ROOT/release/"
    ls -la "$ROOT/release/"
fi

if [ "$SIM" = "1" ]; then
BIN="$APP/Lighthouse"
[ -x "$BIN" ] || die "expected binary at $BIN"

# --- assert 1: the console really is gone -----------------------------------
# Match on a string only the bridge emits. Trusting the CMake flag is not
# enough: the whole point is that a mis-wired #if silently ships the server.
info "Asserting the console bridge is absent"
if strings "$BIN" | grep -q "console bridge listening"; then
    die "REMOTE CONSOLE IS PRESENT in a release build — do not publish this binary"
fi
info "    console bridge: absent"

# --- assert 2: version matches VERSION --------------------------------------
PLIST_VER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Info.plist")"
[ "$PLIST_VER" = "$VERSION" ] || die "CFBundleShortVersionString=$PLIST_VER but VERSION says $VERSION"
info "    version: $PLIST_VER (matches VERSION)"

# --- assert 3: symbols survived (wave 2 items 2 & 3) ------------------------
SYMS="$(nm "$BIN" 2>/dev/null | wc -l | tr -d ' ')"
[ "$SYMS" -gt 10000 ] || die "only $SYMS symbols — binary looks STRIPPED; crash.txt would be address-only"
info "    symbols: $SYMS (not stripped)"

info "release-mode build OK: $APP"
fi
