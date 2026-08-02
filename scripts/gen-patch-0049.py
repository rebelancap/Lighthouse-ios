#!/usr/bin/env python3
r"""Overlay patch 0049: iOS ROM-hack onboarding — the explicit "unverified ROM" path.

NUMBERING: 0037+ are Lighthouse-only.

THE PROBLEM (DECISIONS D5-correction / D23). On iOS a ROM hack cannot be
onboarded at all. Handing the app a ROM whose SHA-1 is not one of the four
retail dumps produces a dead end:

    Lighthouse ROM Error
    File /.../bk-romhack-test.z64
    is not a ROM or does not match supported ROMs.

The gate is `GameExtractor::RunStandalone()`. Reading it is worth the ten
lines, because the rejection is not even deliberate:

    if (mGameList.find(hash) != mGameList.end()) {   // four retail SHA-1s
        romPath = rom;
        romData = std::move(data);
    }
    if (romData.empty()) {
        std::ifstream inFile(romPath, std::ios::binary);   // romPath is ""
        if (!inFile.is_open()) {
            return false;                                  // <-- always
        }
    }

An unknown hash leaves `romPath` empty, so it re-opens the empty string, fails,
and returns false. The ROM was already fully read into `data` at that point —
the bytes are simply dropped.

WHY THAT BRANCH IS THE ONLY ONE iOS SEES. `RunStandalone` is called from the
LOCAL-SCAN branch (`PS_LOCAL` -> `ES_EXTRACT_ARGS`), which scans the app's own
directory — and the app directory is exactly where this port's onboarding shell
copies the ROM the user picked in Files. Upstream's ungated route is the file
PICKER (`PS_FIRST` -> `SelectGameFromUI` -> `LoadRomFromPath` -> `GenerateOTR`
-> `BK64::TrySynthesizeRomConfig`), which on iOS was calling a subprocess-based
dialog and is being fixed separately (overlay 0048). Even with 0048 the picker
is a worse answer here: it asks the user to go find a file the app has already
copied into its own Documents folder.

THE FIX. On iOS only, when the hash is unrecognised, replace the dead-end error
with the design the program charter asked for — *retail = verified, hack =
explicit user-acknowledged unverified path*:

    Unverified ROM
    This file is not one of the four known retail Banjo-Kazooie dumps.
    That is expected for a ROM HACK, and Lighthouse can extract it using a
    synthesized config.
    It also looks like this if the file is damaged or is not Banjo-Kazooie.
    Custom code in a hack cannot be extracted, so some behaviour may be
    missing or broken.
    Extract it anyway?
    [Extract anyway]  [Cancel]

"Cancel" keeps today's behaviour exactly (the original error popup, which
exits). "Extract anyway" routes to `LoadRomFromPath` + `GenerateOTR` — the same
two calls the picker branch makes, with no hash involved — so the hack extracts
through `TrySynthesizeRomConfig` just as it does on desktop.

WHY `#ifdef __IOS__`, and why that is honest rather than timid:
  * Desktop reaches the ungated picker, so it does not need this.
  * `Engine.cpp` is compiled into the macOS ORACLE too, and the oracle is
    permanent ground truth (program charter 0.7). A patch that changed the
    oracle's onboarding would change the reference I compare against.
  * `__IOS__` is defined for the game's own TUs by overlay 0004/0005, and
    Engine.cpp is one of them.

The re-extract confirmation is deliberately NOT reused here. That popup exists
to stop you clobbering an archive you already built; this one has to explain a
different thing (why the hash is unknown, and what is lost), so a shared string
would serve neither.

LANDMINE HANDLED BY UPSTREAM, verified rather than assumed: `GenerateOTR`
blocks its worker thread in a 33 ms sleep loop on
`GameExtractor::sCustomCodePromptResult` until the UI answers a "Custom Code
Romhack Detected" prompt. That prompt IS rendered by this same boot loop
(Engine.cpp:552, `sCustomCodePromptRequested`), which is the loop we are still
inside — so a hack shipping custom code prompts instead of hanging. Wiring the
extraction anywhere else would have to re-implement that.

SCOPE NOTE: this makes the ROM *reach the extractor*. Whether any particular
hack then extracts cleanly is upstream's business — a hack with un-ported MIPS
code will still be missing that code, which is what the prompt says.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/port/Engine.cpp"
REL = "src/port/Engine.cpp"
orig = SRC.read_text()

NL = chr(10)
Q = chr(34)

old = (
    "                } else {" + NL +
    "                    bool open = true;" + NL +
    "                    std::string msg = " + Q + "File\\n" + Q + " + std::string(file) + "
    + Q + "\\nis not a ROM or does not match supported ROMs." + Q + ";" + NL +
    "                    LighthouseGui::RegisterPopup(" + Q + "Lighthouse ROM Error" + Q + ", msg.c_str());" + NL +
    "                }" + NL
)

new = (
    "                } else {" + NL +
    "                    bool open = true;" + NL +
    "                    std::string msg = " + Q + "File\\n" + Q + " + std::string(file) + "
    + Q + "\\nis not a ROM or does not match supported ROMs." + Q + ";" + NL +
    "#ifdef __IOS__" + NL +
    "                    // LIGHTHOUSE_IOS (overlay 0049): the unrecognised hash is the ROM-HACK" + NL +
    "                    // case, and on iOS this is the only branch a user can reach -- the" + NL +
    "                    // onboarding shell copies their pick into the app directory, which is" + NL +
    "                    // what PS_LOCAL scans. RunStandalone() rejects any hash outside the four" + NL +
    "                    // retail dumps (it drops the bytes it already read), so without this a" + NL +
    "                    // hack dead-ends here. Offer the charter's explicit user-acknowledged" + NL +
    "                    // path instead: LoadRomFromPath + GenerateOTR are the same two calls the" + NL +
    "                    // desktop picker branch makes, and GenerateOTR synthesizes a config for" + NL +
    "                    // an unknown hash (BK64::TrySynthesizeRomConfig)." + NL +
    "                    //" + NL +
    "                    // Custom code is safe to reach from here: this same loop renders the" + NL +
    "                    // \"Custom Code Romhack Detected\" prompt that GenerateOTR blocks on." + NL +
    "                    LighthouseGui::RegisterPopup(" + NL +
    "                        " + Q + "Unverified ROM" + Q + "," + NL +
    "                        " + Q + "This file is not one of the four known retail Banjo-Kazooie dumps.\\n" + Q + NL +
    "                        " + Q + "That is expected for a ROM HACK, and Lighthouse can extract it\\n" + Q + NL +
    "                        " + Q + "using a synthesized config.\\n" + Q + NL +
    "                        " + Q + "\\n" + Q + NL +
    "                        " + Q + "It looks the same if the file is damaged or is not Banjo-Kazooie.\\n" + Q + NL +
    "                        " + Q + "Custom code in a hack cannot be extracted, so some behaviour may\\n" + Q + NL +
    "                        " + Q + "be missing or broken.\\n" + Q + NL +
    "                        " + Q + "\\n" + Q + NL +
    "                        " + Q + "Extract it anyway?" + Q + "," + NL +
    "                        " + Q + "Extract anyway" + Q + ", " + Q + "Cancel" + Q + "," + NL +
    "                        [&]() {" + NL +
    "                            if (extract.LoadRomFromPath(file)) {" + NL +
    "                                extracting = true;" + NL +
    "                                (void)threadPool->submit_task([&]() -> void {" + NL +
    "                                    extract.GenerateOTR(extractCount, totalExtract, " + Q + "bk" + Q + ");" + NL +
    "                                    extracting = false;" + NL +
    "                                });" + NL +
    "                            } else {" + NL +
    "                                LighthouseGui::RegisterPopup(" + Q + "Lighthouse ROM Error" + Q + ", msg.c_str());" + NL +
    "                            }" + NL +
    "                        }," + NL +
    "                        [&]() { LighthouseGui::RegisterPopup(" + Q + "Lighthouse ROM Error" + Q + ", msg.c_str()); });" + NL +
    "#else" + NL +
    "                    LighthouseGui::RegisterPopup(" + Q + "Lighthouse ROM Error" + Q + ", msg.c_str());" + NL +
    "#endif" + NL +
    "                }" + NL
)

n = orig.count(old)
assert n == 1, f"expected 1 match for the ROM-error branch, got {n}"
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

out = ROOT / "overlay/patches/0049-lighthouse-ios-unverified-rom-path.patch"
out.write_text(__doc__ + NL + r.stdout.decode())
print(f"wrote {out}")
