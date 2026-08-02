#!/usr/bin/env python3
"""Overlay patch 0051: EndPopup() must be called ONLY when BeginPopupModal() succeeded.

NUMBERING: 0037+ are Lighthouse-only.

This is overlay 0047's rule INVERTED, and the inversion is the whole point.

0047 fixed 17 sites where `End()` / `EndChild()` sat inside their `if`, because
`Begin()` and `BeginChild()` must be terminated **unconditionally** — their
return value reports visibility, not whether a window was pushed.

Popups are the opposite. `ImGui::BeginPopupModal()` pushes a window only when it
returns true, and `EndPopup()` asserts that a popup window is current
(`IM_ASSERT(g.CurrentWindow->Flags & ImGuiWindowFlags_Popup)`). So `EndPopup()`
must be called **only** on the true branch. `src/port/UI/LighthouseModals.cpp`
has it the other way round:

    if (modals.size() > 0) {
        ...
        if (ImGui::BeginPopupModal(curModal.title_.c_str(), NULL, ...)) {
            ...buttons...
        }
        ImGui::EndPopup();      // <-- runs even when BeginPopupModal returned false
    }

Every frame in which a modal is queued but `BeginPopupModal` returns false calls
`EndPopup()` with no popup on the stack: an assert in a checked build, and a
corrupted window stack otherwise.

CAN IT ACTUALLY HAPPEN? Yes, and the mechanism is already present in this port.
`Ship::FileBrowserWindow` and this modal window both open at ImGui popup-stack
**level 0**. `ImGui::OpenPopup` at a level that already holds a popup REPLACES
it. So any `RegisterPopup` call made while the file browser is open closes the
browser's popup out from under it; on the following frames the modal queue is
non-empty while `BeginPopupModal` can return false, and this line fires. Today
the boot loop's `IsOpen()` gate happens to stop the extraction state machine
from registering popups while the browser is up — but that is an implicit
invariant, undocumented, and one new `RegisterPopup` call away from breaking.
The port now opens that browser on iOS as a matter of course (overlay 0048), so
the exposure is real rather than theoretical.

It is also worth fixing simply because it is wrong: this is the single modal
renderer every popup in the game flows through, including the extraction
prompts a first-run user cannot avoid.

Fix: move `EndPopup()` inside the `if`. Behaviour on the true branch is
identical; the false branch stops corrupting the stack.

Upstream bug, like 0047 — same file family, same root confusion about which
ImGui Begin/End pairs are conditional. Worth reporting together.
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/port/UI/LighthouseModals.cpp"
REL = "src/port/UI/LighthouseModals.cpp"
NL = chr(10)
orig = SRC.read_text()

old = (
    "                UIWidgets::PopStyleButton();" + NL +
    "            }" + NL +
    "        }" + NL +
    "        ImGui::EndPopup();" + NL +
    "    }" + NL +
    "}" + NL
)
new = (
    "                UIWidgets::PopStyleButton();" + NL +
    "            }" + NL +
    "            // LIGHTHOUSE_IOS (overlay 0051): INSIDE the if. Unlike Begin/BeginChild" + NL +
    "            // (see 0047), EndPopup() may only be called when BeginPopupModal()" + NL +
    "            // returned true -- it asserts that a popup window is current. It used to" + NL +
    "            // sit one brace out, so any frame with a queued modal that failed to" + NL +
    "            // begin unbalanced the window stack." + NL +
    "            ImGui::EndPopup();" + NL +
    "        }" + NL +
    "    }" + NL +
    "}" + NL
)

n = orig.count(old)
assert n == 1, f"expected 1 match for the modal EndPopup block, got {n}"
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

out = ROOT / "overlay/patches/0051-lighthouse-imgui-endpopup-conditional.patch"
out.write_text(__doc__ + NL + r.stdout.decode())
print(f"wrote {out}")
