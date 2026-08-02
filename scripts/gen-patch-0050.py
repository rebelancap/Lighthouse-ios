#!/usr/bin/env python3
"""Overlay patch 0050: an unextractable ROM must fail LOUDLY, not silently succeed.

NUMBERING: 0037+ are Lighthouse-only.

FOUND BY AUDIT (2026-08-01), after the ROM-hack work in 0048/0049 left one
observation unexplained: declining the first-run local scan landed instantly on
"No ROM O2R file detected. Please generate a ROM O2R and relaunch." with no file
browser ever appearing. That was first attributed to the browser failing to open. It was
not. Two separate defects produce it, and neither is the browser.

DEFECT 1 — the baserom fast-path accepts a ROM it cannot extract.

    std::string baserom = Ship::Context::GetPathRelativeToAppDirectory("baserom.us.z64");
    if (std::filesystem::exists(baserom) && extract.LoadRomFromPath(baserom)) {

`LoadRomFromPath` is a pure byte read with NO validation of any kind, so this
shortcut fires for *any* file called `baserom.us.z64` and then skips
`SelectGameFromUI` entirely — which is why no browser appeared. And on iOS
`GetPathRelativeToAppDirectory` resolves to `$HOME/Documents`
(`libultraship/src/ship/Context.cpp`, `#ifdef __IOS__`), i.e. the very folder
the onboarding shell copies the user's pick into. A ROM hack parked there is
grabbed by a shortcut whose stated purpose is "skip the picker for a known-good
dump".

Fix: gate the shortcut on `RunStandalone`, which is load-plus-verify in one call
and leaves `mGamePath`/`mGameData` set exactly as `LoadRomFromPath` does. A
retail `baserom.us.z64` keeps its shortcut; anything else now falls through to
the picker, which is the branch that can actually handle it.

DEFECT 2 — extraction reports success having produced nothing.

When no config matches the ROM's hash and romhack synthesis declines,
`Companion::Init` logs `No config found for {}` and **returns normally**
(`Torch/src/Companion.cpp`). `GenerateOTR`'s try/catch never fires, so
`sLastError` stays empty and it returns `true` — with no archive written. The
boot loop then finds no o2r, sees an empty `sLastError`, and prints its generic
"No ROM O2R file detected" instead of saying what went wrong. That is how a
misconfigured ROM masquerades as a missing one, and it cost real debugging time:
it made overlay 0049 look like it had failed when 0049 had worked correctly and
Torch had quietly done nothing.

Fix: after `Init` returns, assert the archive is actually THERE. If it is not,
set `sLastError` and return false, so the existing failure path reports "ROM
extraction failed: …" with a reason. Checked via `sLastOutputPath` +
`std::filesystem::exists`, which is mode-independent (that path is a directory
in some export modes and a file in others; either way it must exist) and catches
every silent-no-output failure, not just this one.

WHY NOT PATCH TORCH INSTEAD. The tidy fix for defect 2 is a `throw` at Torch's
`return;`. Rejected: Torch is a second pinned submodule this overlay does not
touch at all today (36 hunks in libultraship, 22 in src, 0 in Torch), and adding
it widens the upstream-bump surface for a failure-reporting improvement. The
port-side check is equivalent from the user's seat, sits in already-patched
territory, and is strictly broader — it catches ANY path that returns without
producing an archive.

Both edits are platform-NEUTRAL, deliberately. Neither changes behaviour on a
successful retail extraction, so the macOS oracle (permanent ground truth,
charter 0.7) is untouched in every case it actually exercises; they only alter
what happens on a failure that today is silent or misdirected. Desktop gets the
same improvement, which is correct — the defects are not iOS-specific, iOS is
just where the ROM lands in the shortcut's path.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/Lighthouse"
NL = chr(10)
Q = chr(34)

edits = {}

# --- Defect 1: the baserom fast-path -----------------------------------------
edits["src/port/Engine.cpp"] = [(
    "                            // Skip the picker entirely if a baserom.us.z64 is sitting in the app" + NL +
    "                            // directory: load it and go straight to extraction." + NL +
    "                            std::string baserom = Ship::Context::GetPathRelativeToAppDirectory(" + Q + "baserom.us.z64" + Q + ");" + NL +
    "                            if (std::filesystem::exists(baserom) && extract.LoadRomFromPath(baserom)) {" + NL,

    "                            // Skip the picker entirely if a baserom.us.z64 is sitting in the app" + NL +
    "                            // directory: load it and go straight to extraction." + NL +
    "                            std::string baserom = Ship::Context::GetPathRelativeToAppDirectory(" + Q + "baserom.us.z64" + Q + ");" + NL +
    "                            // LIGHTHOUSE_IOS (overlay 0050): RunStandalone, not LoadRomFromPath." + NL +
    "                            // LoadRomFromPath is a bare byte read, so this shortcut fired for ANY" + NL +
    "                            // file with that name and then skipped the picker -- and on iOS this" + NL +
    "                            // path is $HOME/Documents, exactly where onboarding puts the user's" + NL +
    "                            // ROM. A hack got swallowed by a shortcut meant for a known-good dump." + NL +
    "                            // RunStandalone is load-plus-verify and leaves the same state behind," + NL +
    "                            // so retail keeps the shortcut and anything else reaches the picker." + NL +
    "                            if (std::filesystem::exists(baserom) && extract.RunStandalone(baserom)) {" + NL,

    "baserom-fastpath-verify"),

    # --- Defect 3: a failed extraction must SAY SO, not loop -----------------
    #
    # Found by testing defect 2's fix and watching it not appear. `sLastError`
    # is only ever rendered by ES_VERIFY, but the local-scan route finishes in
    # ES_EXTRACT_ARGS, whose "All files have been processed. Run Lighthouse?"
    # → Yes sends control back to PS_FILE_CHECK ("No O2R files found. Generate
    # one now?"). So a ROM that cannot be extracted puts the user in a silent
    # Yes/No LOOP with no explanation -- and after 0049 gave them an "Extract
    # anyway" button, that loop is what they get for pressing it. Strictly
    # worse than the blunt error it replaced, and a direct violation of the
    # program's "failures are loud" rule.
    #
    # Report the reason at the point the work finished, then clear it so a
    # later successful attempt in the same session is not haunted by it.
    (
    "            case ES_EXTRACT_ARGS: {" + NL +
    "#if !defined(__SWITCH__) && !defined(__WIIU__)" + NL +
    "                if (args.size() == 0) {" + NL,

    "            case ES_EXTRACT_ARGS: {" + NL +
    "#if !defined(__SWITCH__) && !defined(__WIIU__)" + NL +
    "                // LIGHTHOUSE_IOS (overlay 0050): report a FAILED extraction here." + NL +
    "                // sLastError is only rendered by ES_VERIFY, but this route never" + NL +
    "                // reaches it -- \"All files have been processed\" → Yes goes back to" + NL +
    "                // PS_FILE_CHECK, so an unextractable ROM looped forever without ever" + NL +
    "                // saying why. Cleared after showing, so a later good attempt in the" + NL +
    "                // same session is not haunted by it." + NL +
    "                if (args.size() == 0 && !GameExtractor::sLastError.empty() && !AnyRomArchiveExists()) {" + NL +
    "                    const std::string lhErr = GameExtractor::sLastError;" + NL +
    "                    GameExtractor::sLastError.clear();" + NL +
    "                    LighthouseGui::RegisterPopup(" + Q + "ROM Extraction Failed" + Q + "," + NL +
    "                                                 (" + Q + "Extraction did not produce any game data.\\n\\n" + Q + " + lhErr).c_str()," + NL +
    "                                                 " + Q + "OK" + Q + ", " + Q + Q + ", [&]() { promptStep = PS_FILE_CHECK; });" + NL +
    "                    extractStep = ES_EXTRACT;" + NL +
    "                    break;" + NL +
    "                }" + NL +
    "                if (args.size() == 0) {" + NL,

    "extract-args-report-failure")]

# --- Defect 2: extraction that produces nothing must say so ------------------
edits["src/port/Extractor/GameExtractor.cpp"] = [(
    "    // Record the produced archive path before tearing Companion down, so the" + NL +
    "    // inline Mod Menu flow can enable exactly this file by name." + NL +
    "    sLastOutputPath = Companion::Instance->GetOutputPath();" + NL,

    "    // Record the produced archive path before tearing Companion down, so the" + NL +
    "    // inline Mod Menu flow can enable exactly this file by name." + NL +
    "    sLastOutputPath = Companion::Instance->GetOutputPath();" + NL +
    NL +
    "    // LIGHTHOUSE_IOS (overlay 0050): Init() can return NORMALLY having done" + NL +
    "    // nothing -- when no config matches the ROM hash and romhack synthesis" + NL +
    "    // declines, Torch logs and returns, so the catch above never fires," + NL +
    "    // sLastError stays empty, and we would report success with no archive on" + NL +
    "    // disk. The boot loop then blames a MISSING o2r rather than an" + NL +
    "    // unextractable ROM, which is a genuinely misleading error. Verify the" + NL +
    "    // thing we claim to have produced actually exists." + NL +
    "    if (sLastOutputPath.empty() || !std::filesystem::exists(sLastOutputPath)) {" + NL +
    "        SPDLOG_ERROR(" + Q + "Extraction produced no archive for this ROM" + Q + ");" + NL +
    "        // Pre-wrapped: this string is shown verbatim by the ES_EXTRACT_ARGS" + NL +
    "        // failure popup, which does no wrapping of its own, and an error that" + NL +
    "        // runs off the edge of a phone screen is only half-loud." + NL +
    "        sLastError =" + NL +
    "            " + Q + "No extraction config matched this ROM, and it could\\n" + Q + NL +
    "            " + Q + "not be identified as a Banjo-Kazooie romhack, so\\n" + Q + NL +
    "            " + Q + "nothing was extracted.\\n" + Q + NL +
    "            " + Q + "\\n" + Q + NL +
    "            " + Q + "Check that the file really is a Banjo-Kazooie ROM.\\n" + Q + NL +
    "            " + Q + "Romhacks must be EXTENDED ROMs, larger than 16 MB." + Q + ";" + NL +
    "        sStatusText.clear();" + NL +
    "        sPhase = 0;" + NL +
    "        delete Companion::Instance;" + NL +
    "        Companion::Instance = nullptr;" + NL +
    "        return false;" + NL +
    "    }" + NL,

    "extraction-produced-nothing")]

chunks = []
for rel, pairs in edits.items():
    src = VENDOR / rel
    orig = src.read_text()
    text = orig
    for old, new, tag in pairs:
        n = text.count(old)
        assert n == 1, f"[{tag}] expected 1 match in {rel}, got {n}"
        text = text.replace(old, new)
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig)
        fb.write(text)
        fa.flush()
        fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: expected a diff, got rc={r.returncode}"
    chunks.append(r.stdout.decode())
    print(f"  {rel}: {len(pairs)} edit(s)")

out = ROOT / "overlay/patches/0050-lighthouse-loud-extraction-failure.patch"
out.write_text(__doc__ + NL + "".join(chunks))
print(f"wrote {out}")
