#!/usr/bin/env python3
"""Overlay patch 0018: mouse-free menu scrolling, and the iOS quit fix.

## Why scrolling is not optional here

The menu's sidebar renders nine entries but only seven fit on an iPhone Air.
"iOS" and "Mod Menu" are BELOW THE FOLD — so without this patch a touch user
cannot reach the iOS settings section 0017 just added, or the mod menu, at all.
Verified on the simulator: `artifacts/sim/settings-menu.png` shows the sidebar
ending at "Romhack Menu".

## How

The shell's menu touch-router turns a vertical swipe into a queued
`(x, dy)` via `SohIos_QueueMenuScroll` (overlay 0013 owns the queue). This
patch drains it inside each scrollable `BeginChild` with `ImGui::SetScrollY`,
targeted by the finger's x so the correct column scrolls when several are on
screen.

**Wheel injection is a known dead end** and is deliberately not used: it fires
hover tooltips and makes sliders jump, because ImGui routes wheel events to
whatever is hovered.

Only children that can actually scroll consume the queue (`GetScrollMaxY() > 0`),
so a swipe over a short column falls through to the next candidate instead of
being silently eaten.

## The iOS quit fix

`Menu.cpp` quits with `Window->Close()`. On desktop the main loop then unwinds
and the process exits. **On iOS there is no such loop — `Close()` alone leaves
the app frozen on a dead frame.** So on iOS we flush CVars and `exit(0)`
immediately after, which is also the only path that persists settings on a
deliberate quit (swipe-kill is SIGKILL and never runs anything).

**And `_exit(0)`, not `exit(0)`.** `exit()` runs static destructors on the main
thread while BK's audio thread is still running — and that thread calls
`CVarGetInteger` for the master volume on every audio frame. Quitting therefore
SIGSEGV'd during teardown *every time*, after the app had already visibly
exited:

```
EXC_BAD_ACCESS (SIGSEGV) at 0x38
  CVarGetInteger <- osAiSetNextBuffer
  <- audioManager_handleFrameMsg <- audioManagerThread_entry
```

Harmless to the user in the sense that the quit still completes, but it writes a
crash report on every deliberate quit — which on device means a `crash.txt` that
makes a clean exit look like a fault, exactly the signal we need to stay
trustworthy. Since settings are flushed on the line above and the OS reclaims
everything, there is nothing worth unwinding: `_exit` skips the destructors and
the entire class of teardown race with them (a `Context::~Context` re-entry was
observed on the same path). The flagship uses `exit(0)` and is likely to have
the same race — worth reporting back.

## Verification status (2026-07-31)

The game-side half is PROVEN LIVE with the built-in `LH_IOS_SCROLL_DEBUG` probe
(compile with `-DLH_IOS_SCROLL_DEBUG=1` in this file's HELPER to re-enable):

```
LH_SCROLL dy=0.0 fx=0.0 win=(24.0,86.0)  size=(237.0,310.0) maxY=98.0
LH_SCROLL dy=0.0 fx=0.0 win=(281.0,92.0) size=(303.0,310.0) maxY=246.0
LH_SCROLL dy=0.0 fx=0.0 win=(592.0,92.0) size=(303.0,310.0) maxY=162.0
```

That establishes three things that were previously assumptions:
1. the helper is reached at **all three** scrollable children every frame;
2. each reports a **non-zero `GetScrollMaxY()`**, so they genuinely can scroll
   and the `> 0` guard will not reject them;
3. ImGui's coordinates here are **POINTS** (a 912-pt-wide window: children at
   x = 24, 281, 592), the same space the shell queues its finger x in — so the
   finger-x targeting compares like with like. A pixel/point mismatch was the
   most likely silent failure and it is ruled out.

What is NOT yet verified: `dy` and `fx` stay **0.0**, i.e. the shell's router
never called `SohIos_QueueMenuScroll` for an **idb-synthesised** swipe, even
though idb's horizontal drags DO reach the same router (a slider moved). Since
taps and drags both work, this looks like a limitation of synthetic touch
delivery rather than a defect in either half — and the charter's own trap list
warns that real-finger flows need real-touch tests. Resolve it with a finger on
the device at the device gate.

Match-count asserted against the pristine vendor state."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/port/UI/Menu.cpp"
REL = "src/port/UI/Menu.cpp"
orig = SRC.read_text()

HELPER = '''
#ifdef __IOS__
#ifndef LH_IOS_SCROLL_DEBUG
#define LH_IOS_SCROLL_DEBUG 0
#endif
// LIGHTHOUSE_IOS (overlay 0018): drain the shell's queued swipe into whichever
// scrollable child sits under the finger. Declared by 0013, which owns the queue.
extern "C" float gSohIosScrollX;
extern "C" float gSohIosScrollDy;

static void LhIosApplyQueuedScroll() {
#if LH_IOS_SCROLL_DEBUG
    {
        static int lhDbg = 0;
        if (lhDbg < 12) {
            lhDbg++;
            const ImVec2 o = ImGui::GetWindowPos();
            const ImVec2 s = ImGui::GetWindowSize();
            SPDLOG_INFO("LH_SCROLL dy={:.1f} fx={:.1f} win=({:.1f},{:.1f}) size=({:.1f},{:.1f}) maxY={:.1f}",
                        gSohIosScrollDy, gSohIosScrollX, o.x, o.y, s.x, s.y, ImGui::GetScrollMaxY());
        }
    }
#endif
    if (gSohIosScrollDy == 0.0f) {
        return;
    }
    // Only a child that can actually scroll may consume the gesture; otherwise a
    // swipe over a short column would be eaten instead of reaching the long one.
    if (ImGui::GetScrollMaxY() <= 0.0f) {
        return;
    }
    const ImVec2 origin = ImGui::GetWindowPos();
    const ImVec2 size = ImGui::GetWindowSize();
    if (gSohIosScrollX < origin.x || gSohIosScrollX > origin.x + size.x) {
        return;
    }
    ImGui::SetScrollY(ImGui::GetScrollY() - gSohIosScrollDy);
    gSohIosScrollDy = 0.0f;
}
#endif // __IOS__

'''

# --- 1. helper at FILE scope, ahead of every use ---------------------------
# NOT inside `namespace LighthouseGui {`: that namespace opens at line 19 and
# CLOSES at line 43, while the call sites are ~840 lines later in a different
# scope. Putting it there compiled the helper but left every call site with
# "use of undeclared identifier ... did you mean LighthouseGui::...".
anchor_ns = "std::vector<ImVec2> windowTypeSizes = { {} };\n"
n = orig.count(anchor_ns)
assert n == 1, f"[helper-anchor] expected 1 match, got {n}"
t = orig.replace(anchor_ns, anchor_ns + HELPER, 1)

# --- 2. the sidebar child (this is the one hiding the iOS section) ----------
old_sidebar = '''    ImGui::BeginChild((menuEntries.at(headerIndex).label + " Section").c_str(), { sidebarWidth, columnHeight * 3 },
                      ImGuiChildFlags_AutoResizeY | ImGuiChildFlags_AlwaysAutoResize, ImGuiWindowFlags_NoTitleBar);
'''
new_sidebar = old_sidebar + '''#ifdef __IOS__
    LhIosApplyQueuedScroll();
#endif
'''
n = t.count(old_sidebar)
assert n == 1, f"[sidebar] expected 1 match, got {n}"
t = t.replace(old_sidebar, new_sidebar)

# --- 3. the per-column widget children -------------------------------------
old_col = '''                ImGui::BeginChild(sectionId.c_str(), { columnWidth, windowHeight * 4 }, ImGuiChildFlags_AutoResizeY,
                                  ImGuiWindowFlags_NoTitleBar);
'''
new_col = old_col + '''#ifdef __IOS__
                LhIosApplyQueuedScroll();
#endif
'''
n = t.count(old_col)
assert n == 1, f"[column] expected 1 match, got {n}"
t = t.replace(old_col, new_col)

# --- 4. the single-column section child ------------------------------------
old_sec = '''        ImGui::BeginChild(sectionMenuId.c_str(), { sectionWidth, windowHeight * 4 }, ImGuiChildFlags_AutoResizeY,
'''
n = t.count(old_sec)
assert n == 1, f"[section] expected 1 match, got {n}"
# insert after the full statement (it spans two lines) — anchor on its closing line
old_sec_full = old_sec + '''                          ImGuiWindowFlags_NoTitleBar);
'''
n = t.count(old_sec_full)
assert n == 1, f"[section-full] expected 1 match, got {n}"
t = t.replace(old_sec_full, old_sec_full + '''#ifdef __IOS__
        LhIosApplyQueuedScroll();
#endif
''')

# --- 5. iOS quit: Close() alone freezes ------------------------------------
old_quit = '''                Ship::Context::GetRawInstance()->GetWindow()->Close();
'''
new_quit = '''                Ship::Context::GetRawInstance()->GetWindow()->Close();
#ifdef __IOS__
                // LIGHTHOUSE_IOS (0018): there is no desktop main loop to unwind
                // on iOS, so Close() alone leaves the app frozen on a dead frame.
                // Flush settings first — a deliberate quit is the ONLY exit that
                // can persist them (swipe-kill is SIGKILL and runs nothing).
                CVarSave();
                // _exit, NOT exit: exit() runs static destructors on the main
                // thread while BK's audio thread is still live, and it calls
                // CVarGetInteger for the master volume on every frame
                // (OS_AI.cpp osAiSetNextBuffer). Device-observed SIGSEGV at
                // teardown, every single quit:
                //     CVarGetInteger <- osAiSetNextBuffer
                //     <- audioManager_handleFrameMsg <- audioManagerThread_entry
                // The settings are already flushed above, so there is nothing
                // left worth unwinding — and the OS reclaims everything anyway.
                // _exit skips the destructors and with them the whole class of
                // teardown race (a Context::~Context re-entry was also seen).
                _exit(0);
#endif
'''
n = t.count(old_quit)
assert n == 1, f"[quit] expected 1 match, got {n}"
t = t.replace(old_quit, new_quit)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True, text=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0018-lighthouse-ios-menu-touch-scroll.patch"
out.write_text(__doc__ + "\n\n" + r.stdout)
print(f"wrote {out}")
