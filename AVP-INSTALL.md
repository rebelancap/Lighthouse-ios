# Installing Lighthouse on Apple Vision Pro

This is an iOS and visionOS build of [Lighthouse](https://github.com/HarbourMasters/Lighthouse), Harbour Masters' native Banjo-Kazooie port, on [libultraship](https://github.com/HarbourMasters/libultraship), rendering natively on Metal. On Apple Vision Pro it runs in a freely resizable 2D window at true 4K, and a stereoscopic 3D mode puts the game on a world-locked panel floating in your room.

## What you need

- Apple Vision Pro on visionOS 2 or later
- Your own Banjo-Kazooie ROM (`.z64`): US v1.0, US v1.1, Japan or PAL
- For the prebuilt app: SideStore on the headset, installed with the [visionOS fork of iloader](https://github.com/rebelancap/iloader/releases#release-visionos) on an Apple Silicon Mac
- To build from source: macOS with Xcode and `cmake` (`brew install cmake`)
- Optional: a game controller (Backbone, DualSense, Xbox and others). The window also shows the on-screen touch layout.

## Your game files

Neither this repository nor the app contains any game content. You supply your own Banjo-Kazooie ROM.

1. Install the app and open it.
2. The app walks you through adding your ROM on first launch. Extraction runs inside the app on the headset; no PC tools or companion app are needed.

ROM hacks are optional: *Settings → Romhack Menu → Generate Romhack from ROM*, then pick the hack's `.z64`. Hacks are built against US v1.0, so your base game has to come from a US v1.0 ROM for them to play correctly. See the [FAQ](README.md#faq) in the README.

Texture packs and other `.o2r` mods are optional too. Copy the `.o2r` into *On My Apple Vision Pro → Lighthouse → mods* in the Files app and relaunch; mods apply when **Enable Mods** is on, which it is by default. See [Texture packs](README.md#texture-packs) in the README.

## Install the prebuilt app

1. Install SideStore on the headset with the [visionOS fork of iloader](https://github.com/rebelancap/iloader/releases#release-visionos). It runs on an Apple Silicon Mac and pairs with the headset over Wi-Fi: no cable, no Dev Strap, no Xcode.
2. In SideStore, go to *Sources → +* and paste this source, then install Lighthouse:

   ```
   https://raw.githubusercontent.com/rebelancap/harbourmasters-ports/main/apps-visionos.json
   ```

   The app updates from this source when new versions ship.

To install by hand instead, download `lighthouse-*-visionOS.ipa` from the [latest release](https://github.com/rebelancap/Lighthouse-ios/releases/latest) and install it through SideStore or AltStore.

## Build from source

From a checkout of this repo:

```sh
scripts/bootstrap.sh          # clone + pin upstream Lighthouse (submodules recursive)
scripts/build-oracle.sh       # native macOS build: generates lighthouse.o2r
LIGHTHOUSE_IOS_TEAM=<your team ID> scripts/build-visionos.sh   # signed Apple Vision Pro build
```

`scripts/build-visionos.sh` takes your Apple Developer team ID from `LIGHTHOUSE_IOS_TEAM` (set it, or the script uses the maintainer's team) and writes the app to `build-visionos/Release-xros/Lighthouse.app`. No ROM is needed for the device build.

Upstream Lighthouse is vendored unmodified and pinned by commit. Every local change is a patch in `overlay/patches/`, applied by `scripts/apply-overlay.sh`; a patch that fails to apply fails the build. Simulator and iPhone builds are in [Building from source](README.md#building-from-source).

## Notes

- The 3D mode has live settings for stereo depth, screen size, distance and height, and surroundings dimming, plus a recenter button. The panel resizes freely and the engine re-renders to match.
- Apps sideloaded with a free Apple account expire after 7 days (paid developer accounts last a year). SideStore refreshes them in the background; if the app stops launching, open SideStore and let it re-sign.
- If the app crashes, it writes `crash.txt` and a `logs/` folder to *On My Apple Vision Pro → Lighthouse* in Files. Attach them to a [GitHub issue](https://github.com/rebelancap/Lighthouse-ios/issues).
- BANJO-KAZOOIE is © Nintendo / Rare. This project is not affiliated with or endorsed by Nintendo or Rare, and ships no Nintendo or Rare content.
