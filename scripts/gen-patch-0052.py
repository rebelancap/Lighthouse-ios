#!/usr/bin/env python3
"""Overlay patch 0052: the file browser self-heals if its popup is evicted.

NUMBERING: 0037+ are Lighthouse-only.

THE HAZARD. ImGui allows ONE popup per stack level, and `OpenPopup` at a level
that is already occupied REPLACES the incumbent. `Ship::FileBrowserWindow` and
this port's `LighthouseModalWindow` both open at level 0. So any unrelated
`RegisterPopup(...)` issued while the browser is up silently evicts the
browser's popup — and the browser never learns.

The consequence is not a crash, it is worse: `mActive` stays true, `Finish()`
never runs, `sActiveOrQueued` stays true, and `IsOpen()` keeps answering yes
forever. The first-run extraction loop gates on exactly that
(`Engine.cpp`: `if (LighthouseGui::PopupsQueued() > 0 || extracting ||
Ship::FileBrowserWindow::IsOpen()) goto render;`), so it would spin with
nothing on screen and no way forward — a soft-lock on the one flow every new
user must complete, with the app apparently alive.

IS IT REACHABLE TODAY? No — and that is why this is a five-line guard rather
than a redesign. The extraction state machine cannot register a popup while the
browser is open (the `goto render` above keeps it parked), and the custom-code
prompt only fires while `extracting`, by which point the browser has closed.
The invariant holds by accident of ordering: nothing declares it, nothing tests
it, and it is one new `RegisterPopup` call — or one upstream reshuffle of that
loop — away from breaking. Overlay 0048 made iOS open this browser as a matter
of course, so the exposure went from theoretical to routine.

WHY NOT FIX THE REAL THING. The principled fix is popup-stack ownership: give
the browser its own level, or have it re-open itself when evicted. Both are
design changes to a shared submodule to defend against a collision that cannot
currently occur, and both would diverge this port's libultraship from every
sibling's for no observable gain. That trade is not worth it.

WHAT THIS DOES INSTEAD: treat eviction as a cancel. If the browser believes it
is showing a popup and ImGui says that popup is not open, hand the caller
`std::nullopt` — exactly what pressing Cancel does — so the flow continues and
the user can try again. A recoverable "nothing selected" instead of a hang.

Correctness notes:
  * Placed AFTER the `mOpenPopup` block, so the frame that opens the popup is
    never mistaken for an eviction (`OpenPopup` pushes onto `OpenPopupStack`
    immediately, so `IsPopupOpen` is already true on that same frame).
  * `Finish()` calls `ImGui::CloseCurrentPopup()`, which is a no-op when no
    popup is current (it returns early on an empty `BeginPopupStack`), so
    calling it from outside a popup is safe.
  * Only reachable with `mActive` true — the `!mActive` branch above returns
    early on an empty queue and otherwise calls `BeginRequest()`, which sets
    `mOpenPopup = true`.

Behaviour is unchanged whenever the popup is where it is supposed to be, which
is every case that occurs today — including on the macOS oracle.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/src/ship/window/gui/FileBrowserWindow.cpp"
REL = "libultraship/src/ship/window/gui/FileBrowserWindow.cpp"
NL = chr(10)
orig = SRC.read_text()

old = (
    "        ImGui::OpenPopup(popupId.c_str());" + NL +
    "        mOpenPopup = false;" + NL +
    "    }" + NL +
    "    const ImVec2 viewport = ImGui::GetMainViewport()->WorkSize;" + NL
)
new = (
    "        ImGui::OpenPopup(popupId.c_str());" + NL +
    "        mOpenPopup = false;" + NL +
    "    }" + NL +
    NL +
    "    // LIGHTHOUSE_IOS (overlay 0052): self-heal if our popup was evicted." + NL +
    "    // ImGui allows one popup per stack level and OpenPopup REPLACES the" + NL +
    "    // incumbent, so an unrelated modal opened while this browser is up takes" + NL +
    "    // its slot without telling it. mActive would then stay true forever, and" + NL +
    "    // with it sActiveOrQueued and IsOpen() -- which the first-run extraction" + NL +
    "    // loop gates on, so it would spin with nothing on screen. Treat eviction" + NL +
    "    // as a cancel: the caller gets nullopt and can recover, instead of hanging." + NL +
    "    if (!mOpenPopup && !ImGui::IsPopupOpen(popupId.c_str())) {" + NL +
    "        Finish(std::nullopt);" + NL +
    "        return;" + NL +
    "    }" + NL +
    NL +
    "    const ImVec2 viewport = ImGui::GetMainViewport()->WorkSize;" + NL
)

n = orig.count(old)
assert n == 1, f"expected 1 match for the OpenPopup block, got {n}"
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

out = ROOT / "overlay/patches/0052-lus-filebrowser-selfheal-evicted-popup.patch"
out.write_text(__doc__ + NL + r.stdout.decode())
print(f"wrote {out}")
