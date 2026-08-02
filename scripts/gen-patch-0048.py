#!/usr/bin/env python3
"""Overlay patch 0048: iOS uses libultraship's ImGui file browser, not pfd.

NUMBERING: 0037+ are Lighthouse-only.

FOUND BY TESTING the README's ROM-hack claim (2026-08-01). Feeding the app a
ROM whose SHA-1 is not one of the four retail hashes produced:

    Lighthouse ROM Error
    File /.../bk-romhack-test.z64
    is not a ROM or does not match supported ROMs.

i.e. ROM hacks could not be onboarded on iOS AT ALL, while the README claimed
"ROM-hack support for Banjo's Backpack-style hacks". Two upstream facts
combine to produce that:

1. The boot flow's LOCAL-SCAN branch (`PS_LOCAL` -> `ES_EXTRACT_ARGS`) calls
   `GameExtractor::RunStandalone()`, which looks the whole-file SHA-1 up in
   `mGameList` (the four retail dumps) and returns false for anything else.
   That is a hard hash gate, and it is the branch that fires for a ROM sitting
   in the app directory — which is exactly where the iOS onboarding shell
   copies the user's pick.

   (`docs/asset-pipeline.md` / DECISIONS D5 previously concluded there was NO
   hash gate, reasoning from `ValidateChecksum()` having zero call sites. That
   was right about `ValidateChecksum` and wrong about the outcome: the gate is
   `RunStandalone`'s own `mGameList` lookup. Corrected there.)

2. The PICKER branch (`PS_FIRST` -> `SelectGameFromUI`) has NO hash gate — it
   goes through `LoadRomFromPath()` straight to `GenerateOTR()`, which calls
   `BK64::TrySynthesizeRomConfig()` for an unknown hash. That is upstream's
   real ROM-hack path. But on iOS it can never run, because
   `LIGHTHOUSE_NATIVE_FILE_DIALOG` is 1 here:

       #if defined(__SWITCH__) || defined(__WIIU__) || \\
           (defined(__linux__) && (defined(__aarch64__) || defined(__arm__)))

   Apple is not in that list, so `Lighthouse::PickFile` calls
   `pfd::open_file`. portable-file-dialogs' `__APPLE__` backend drives
   **osascript** — a SUBPROCESS, which iOS does not permit at all (program
   charter 0.5: "no JIT, no subprocesses on iOS"). It returns an empty
   selection immediately, `SelectGameFromUI` reports failure, and the user
   gets "No ROM O2R file detected. Please generate a ROM O2R and relaunch."

So the gate rejected hacks and the ungated path was unreachable. Retail ROMs
worked the whole time, which is why this survived to a release candidate.

FIX: put iOS in the same bucket as the other non-desktop platforms, so
`PickFile` falls back to libultraship's in-game ImGui file browser
(`Ship::FileBrowserWindow`) — the fallback upstream already ships and already
tests on Switch/Wii U/arm Linux. `SelectGameFromUI` then reaches
`LoadRomFromPath` and hacks extract via the synthesized config.

Deliberately a ONE-LINE platform-list change rather than an iOS-specific
picker: the browser is upstream's own answer for platforms without a native
dialog, and the app shell's UIDocumentPicker still handles first-run ROM
IMPORT (copying into Documents). This only fixes which picker the ENGINE's
extraction flow opens.

visionOS is covered by the same macro — the app target defines `__IOS__` for
both (overlay 0004/0005).
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/port/FilePicker.h"
REL = "src/port/FilePicker.h"
orig = SRC.read_text()

old = (
    "// Desktop platforms have a usable native file dialog (portable-file-dialogs). Consoles and Linux\n"
    "// handhelds (arm) generally lack a native dialog / display server, so they fall back to\n"
    "// libultraship's in-game ImGui file browser. Define LIGHTHOUSE_NATIVE_FILE_DIALOG to override.\n"
    "#ifndef LIGHTHOUSE_NATIVE_FILE_DIALOG\n"
    "#if defined(__SWITCH__) || defined(__WIIU__) || (defined(__linux__) && (defined(__aarch64__) || defined(__arm__)))\n"
    "#define LIGHTHOUSE_NATIVE_FILE_DIALOG 0\n"
)
new = (
    "// Desktop platforms have a usable native file dialog (portable-file-dialogs). Consoles and Linux\n"
    "// handhelds (arm) generally lack a native dialog / display server, so they fall back to\n"
    "// libultraship's in-game ImGui file browser. Define LIGHTHOUSE_NATIVE_FILE_DIALOG to override.\n"
    "//\n"
    "// LIGHTHOUSE_IOS (overlay 0048): iOS/visionOS join that list. pfd's __APPLE__ backend drives\n"
    "// osascript -- a subprocess, which iOS forbids -- so it returned an empty selection instantly\n"
    "// and SelectGameFromUI always failed. That mattered beyond the picker: the picker branch is the\n"
    "// only ROM path WITHOUT a SHA-1 gate, so with it dead, ROM hacks could not be onboarded at all.\n"
    "#ifndef LIGHTHOUSE_NATIVE_FILE_DIALOG\n"
    "#if defined(__SWITCH__) || defined(__WIIU__) || defined(__IOS__) || (defined(__linux__) && (defined(__aarch64__) || defined(__arm__)))\n"
    "#define LIGHTHOUSE_NATIVE_FILE_DIALOG 0\n"
)

n = orig.count(old)
assert n == 1, f"expected 1 match for the platform gate, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig)
    fb.write(text)
    fa.flush()
    fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1, f"expected a diff, got rc={r.returncode}"

out = ROOT / "overlay/patches/0048-lighthouse-ios-file-browser.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
