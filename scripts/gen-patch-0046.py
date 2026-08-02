#!/usr/bin/env python3
"""Overlay patch 0046: hold A to fast-forward dialog.

NUMBERING: 0037+ are Lighthouse-only.

REQUESTED AFTER DEVICE TESTING: a way to skip in-game dialog, prompted by
reports online that an L + R + B combo does it (it does not).

FINDINGS FIRST. There is no such combo, in this port or in the game. Upstream
Lighthouse ships six cutscene skips (`Cutscenes.SkipBootLogos`,
`StartSkipIntro`, `SkipMiscCutscenes`, `SkipJiggyDance`, `SkipNoteDoorDance`,
`SkipCluckerCutscene`) and **nothing at all for dialog text** — no text-speed,
no skip, no auto-advance. Dialog advances one page per A press, edge-triggered
(`src/core2/gc/dialog.c`, the `func_8024E5E8(0, 3|4)` guard).

DESIGN: auto-repeat the game's own advance, do NOT jump states.

The tempting implementation is to force `dialog_setState()` past the
conversation. That is how you desync scripted sequences: BK gates real game
progress on dialog completion (Bottles' tutorials unlock moves, Jiggy and note
door dances, boss intros), and a state jump skips whatever bookkeeping the
intermediate states do. Instead, this treats a HELD A exactly as if the player
were mashing it: the state machine runs every transition it normally would,
just without waiting for a fresh edge each page. Nothing downstream can tell
the difference between this and a fast player.

`controller_face_buttons` is already populated in `dialog_update` (filled by
`controller_copyFaceButtons` / `pfsManager_getFirstControllerFaceButtonState`),
and BK stores a HOLD COUNTER there — the existing code compares `== 1u` for
"pressed this frame", so `>= 1` is "held". No new input plumbing.

Why A and not a chord: A is already the advance button, so hold-to-fast-forward
is discoverable without being told, and it cannot fire by accident — a tap is
one page, exactly as before. On a touch device a three-button chord would be
close to unusable anyway.

Gated on `gSohIos.DialogHoldSkip` (default ON) with a Settings -> iOS toggle in
overlay 0017, so anyone who wants strict vanilla pacing can turn it off.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/core2/gc/dialog.c"
REL = "src/core2/gc/dialog.c"
orig = SRC.read_text()

OLD = """        if (NOT((g_Dialog.u8_s.unk128_31 & 0x80) ? func_8024E5E8(0, 4) : func_8024E5E8(0, 3))) {
            break;
        }
"""
NEW = """#ifdef __IOS__
        /* LIGHTHOUSE_IOS (overlay 0046): hold A to fast-forward dialog.
         * Deliberately auto-repeats the ADVANCE rather than jumping states --
         * BK gates real progress on dialog completing (move unlocks, jiggy and
         * note-door dances, boss intros), so a state jump would skip the
         * bookkeeping those states do. Holding A is indistinguishable to the
         * game from a player mashing it. The face-button array here is a hold
         * counter (the code above compares == 1u for "pressed this frame"), so
         * >= 1 means held. A tap still advances exactly one page. */
        {
            extern s32 CVarGetInteger(const char* name, s32 defaultValue);
            const s32 lhHoldSkip = CVarGetInteger("gSohIos.DialogHoldSkip", 1) &&
                                   controller_face_buttons[FACE_BUTTON(BUTTON_A)] >= 1;
            if (!lhHoldSkip)
#endif
        if (NOT((g_Dialog.u8_s.unk128_31 & 0x80) ? func_8024E5E8(0, 4) : func_8024E5E8(0, 3))) {
            break;
        }
#ifdef __IOS__
        }
#endif
"""
n = orig.count(OLD)
assert n == 1, f"[advance-guard] expected 1 match, got {n}"
t = orig.replace(OLD, NEW)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig)
    fb.write(t)
    fa.flush()
    fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0046-lighthouse-dialog-hold-to-skip.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
