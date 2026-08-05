#!/usr/bin/env python3
"""Adapt the flagship's shared shell for Lighthouse.

The shell (app/ios/SohIosShell.m) is copied from Shipwright-ios verbatim and
kept as close to it as possible — harbour-shell (program D9) will be distilled
from the proven shells, so gratuitous divergence is a cost, not a feature. The
`SohIos_` / `gSohIos.` symbol and CVar namespaces therefore stay AS THEY ARE in
every port; only genuinely port-specific values change.

Every edit asserts its match count: a silent no-op edit shipped three
regressions in one day on the predecessor port.

Run after re-copying the shell from the flagship. Idempotent: re-running on an
already-adapted shell reports the expected zero matches and exits non-zero, so
it can never half-apply.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHELL = ROOT / "app/ios/SohIosShell.m"

# (old, new, expected_count, why)
EDITS = [
    ("soh://", "lighthouse://", 9,
     "URL scheme — must match CFBundleURLSchemes in the generated Info.plist"),
    ("8765", "8770", 3,
     "console bridge port — charter machine etiquette: soh=8765, 2ship=8766, "
     "starship=8767, spaghettikart=8768, ghostship=8769, lighthouse=8770"),
    ("then relaunch Ship of Harkinian.\\n", "then relaunch Lighthouse.\\n", 1,
     "user-facing onboarding text"),
    # Rename the env-var TOKENS wholesale (3 and 6 sites: comments AND the
    # getenv() calls), so a sibling session's exported var can't drive this
    # port's bridge or self-test.
    #
    # Learned here: an earlier version replaced the string "SOH_CONSOLE=1",
    # which asserted "exactly 1 match" and passed — but the only match was a
    # COMMENT. The real gate, getenv("SOH_CONSOLE"), was untouched, so the
    # bridge stayed off and nothing on :8770 answered. A match-count assert
    # proves HOW MANY, not WHICH: match the semantically load-bearing text.
    ("SOH_CONSOLE", "LIGHTHOUSE_CONSOLE", 3,
     "console-bridge env gate (2 comments + the getenv call)"),
    ("SOH_SELFTEST", "LIGHTHOUSE_SELFTEST", 6,
     "self-test input-injection env gate (comments + the getenv call)"),

    # --- device feedback 2026-07-31 (user, on iPhone Air) --------------------
    # rev3 (user report x3, device, 2026-08-01). MEASURED, not assumed: during
    # the Rareware intro `hideprobe` reports MENU:hit0 while every other button
    # is hit1, which means every term of this predicate was false — including
    # SohIos_IsTitleOrDemo(). So BK runs its intro in GAME_MODE_3_NORMAL and is
    # not "title or demo" there. The sticky HasEnteredGameplay latch (rev2) had
    # therefore ALREADY fired before the user ever saw a frame.
    #
    # The conclusion is not "pick a better mode": no game-mode predicate can
    # separate the intro from gameplay in this game, and I got it wrong twice
    # trying. The chip is now ALWAYS visible, which is what was asked for and
    # removes the whole class of bug. Users who want it gone in gameplay have
    # the per-button hide/show in the touch customizer — policy by explicit
    # choice beats policy by a guess the game does not support.
    # rev4 (user decision, 2026-08-01): back to the flagship rule, plus
    # menu-open. rev3's always-visible was MY call to make and it should not
    # have been -- the user's judgement: an always-on chip is not worth paying
    # for the first ten seconds of a launch. The `|| IsMenuOpen()` term is kept
    # because it is not a heuristic: it just lets the chip that opened a menu
    # close it again.
    #
    # This deliberately leaves the intro with NO chip. That is the accepted
    # cost, not an oversight -- see D15 for why no game-mode predicate can
    # detect BK's intro (measured: it runs in GAME_MODE_3_NORMAL and does not
    # report as title/demo).
    ('- (BOOL)menuButtonVisible {\n    return SohIos_IsGamePaused() || SohIos_IsTitleOrDemo();\n}', '- (BOOL)menuButtonVisible {\n    // LIGHTHOUSE: + IsMenuOpen so the chip that opened a menu can close it.\n    // No intro/gameplay inference here on purpose -- see DECISIONS D15.\n    return SohIos_IsGamePaused() || SohIos_IsTitleOrDemo() || SohIos_IsMenuOpen();\n}', 1,
     'menu chip: flagship rule + menu-open (user decision, D15 rev4)'),
    # The intro touch-layer hide is GONE, not disabled. SohIos_IsIntroPlaying()
    # can never return 1: it early-returns 0 once HasEnteredGameplay() is set,
    # and that latch fires during the intro itself (proven above). Every site
    # that consumed it was therefore dead code that read as live — the same
    # trap as the Fast3dWindow patch in D14. Hiding the touch layer over the
    # intro needs a signal this game does not currently expose; when one turns
    # up, reinstate it against THAT rather than against a mode number.
    ('float SohIos_SsaaFactor(void) {\n    float f = CVarGetFloat("gSohIos.Supersample", 0.0f);', '// Device-tier SSAA default (Ghostship pattern, PORTING-DELTAS): unset means\n// "pick for me", so a capable phone supersamples without the user hunting for\n// a slider. Keyed on the Metal GPU FAMILY rather than a model table so new\n// hardware inherits sensibly. The slider always wins once it has a value.\n// Measured on this port, iPhone Air, native scene: gpu_ms 3.66 at 1.0x and\n// 7.87 at 2.0x against an 8.33 ms budget at 120 fps — 2.0 fits, with less\n// headroom than Ghostship had (SM64 was 1.26 ms native), so heavy scenes are\n// the thing to watch.\nstatic float SohIos_SsaaDeviceDefault(void) {\n    static float cached = 0.0f;\n    if (cached > 0.0f) {\n        return cached;\n    }\n    cached = 1.0f;\n    id<MTLDevice> dev = MTLCreateSystemDefaultDevice();\n    if (dev != nil) {\n        if ([dev supportsFamily:MTLGPUFamilyApple8]) {\n            cached = 2.0f;\n        } else if ([dev supportsFamily:MTLGPUFamilyApple7]) {\n            cached = 1.5f;\n        }\n    }\n    NSLog(@"[SohIosShell] SSAA device default %.2fx", cached);\n    return cached;\n}\n\nfloat SohIos_SsaaFactor(void) {\n#if TARGET_OS_VISION\n    // LIGHTHOUSE: Vision Pro is flat 1.0x, no slider (overlay 0017 shows a\n    // truth line instead). The panel window already renders at a 3840 long\n    // edge, ~2x the angular resolution the headset can resolve at that window\n    // size, so an SSAA factor on top is pure GPU cost. The device-tier default\n    // below MUST NOT run here: Vision Pro\'s M-series reports\n    // MTLGPUFamilyApple8, so it would silently pick 2.0x and render 7680x4320.\n    return 1.0f;\n#else\n    float f = CVarGetFloat("gSohIos.Supersample", 0.0f);\n    if (f < 1.0f) {\n        f = SohIos_SsaaDeviceDefault();\n    }', 1,
     "device-tier SSAA default so capable phones don't need the slider; Vision Pro "
     "is pinned to 1.0x because its GPU family would otherwise trip the phone default"),
    ('    CVarSetInteger("gSohIos.DefaultsVersion", 4);\n    CVarSave();\n    NSLog(@"[SohIosShell] defaults seeded v4 (ZFightingMode=2 no-vanishing-paths)");', '    // v5 (LIGHTHOUSE, user request 2026-07-31): START already skips cutscenes,\n    // but upstream gates every skip behind an enhancement that ships OFF — so\n    // pressing START did nothing and the intro felt unskippable. Seed them on.\n    // SkipBootLogos also covers the Lighthouse intro video.\n    if (version < 5) {\n        CVarSetInteger("gEnhancements.Cutscenes.StartSkipIntro", 1);\n        CVarSetInteger("gEnhancements.Cutscenes.SkipMiscCutscenes", 1);\n        CVarSetInteger("gEnhancements.Cutscenes.SkipBootLogos", 1);\n        CVarSetInteger("gEnhancements.Cutscenes.SkipJiggyDance", 1);\n    }\n    CVarSetInteger("gSohIos.DefaultsVersion", 5);\n    CVarSave();\n    NSLog(@"[SohIosShell] defaults seeded v5 (cutscene skips on by default)");', 1,
     'seed START-to-skip enhancements ON (upstream ships them off)'),
    # Closes the #if TARGET_OS_VISION opened in the SSAA edit above. Anchored on
    # the clamp tail rather than on a bare "return f;\n}" — there are several of
    # those in this file and only this one ends SohIos_SsaaFactor.
    # Closes the #if TARGET_OS_VISION opened in the SSAA edit above. Anchored on
    # the clamp tail rather than on a bare "return f;\n}" — there are several of
    # those in this file and only this one ends SohIos_SsaaFactor.
    ('    if (f > 2.0f) {\n        f = 2.0f;\n    }\n    return f;\n}',
     '    if (f > 2.0f) {\n        f = 2.0f;\n    }\n    return f;\n#endif\n}', 1,
     "close the visionOS branch in SohIos_SsaaFactor"),
    ('#import <GameController/GameController.h>', '#import <GameController/GameController.h>\n#import <Metal/Metal.h>  // LIGHTHOUSE: GPU-family query for the SSAA default', 1,
     'Metal import for the GPU-family query'),
    # --- local-repro plumbing (2026-08-01) ----------------------------------
    # Both of these exist because the Vision Pro flicker has now cost four
    # failed fixes, every one of them unfalsifiable without the user's eyes.
    # The visionOS SIMULATOR reports views=1 (monoscopic) and the user has
    # since established the flicker is visible with ONE EYE CLOSED -- i.e. it
    # is monocular, i.e. the simulator should be able to show it. Getting a
    # local repro is worth more than another guess.
    #
    # 1. Bridge port fallback. On this Mac `sharingd` squats *:8770, so the
    #    bridge bind fails on the simulator (errno 60) and there is no way to
    #    drive 3D mode from a script. Fall back through 8771..8779 and log
    #    which port was taken. On device the port is free and behaviour is
    #    unchanged.
    ("""        addr.sin_port = htons(8770);
        BOOL bound = NO;
        for (int i = 0; i < 10 && !bound; i++) { // bind retry (predecessor pattern)
            bound = bind(srv, (struct sockaddr*)&addr, sizeof(addr)) == 0;
            if (!bound) {
                usleep(500 * 1000);
            }
        }""",
     """        BOOL bound = NO;
        int boundPort = 0;
        // LIGHTHOUSE: try 8770 first, then 8771..8779. A system daemon
        // (sharingd) holds *:8770 on this dev Mac, which killed the bridge on
        // the SIMULATOR only -- the device is unaffected. Falling back beats
        // having no way to drive the app from a script.
        for (int p = 8770; p < 8780 && !bound; p++) {
            addr.sin_port = htons(p);
            for (int i = 0; i < 4 && !bound; i++) { // bind retry (predecessor pattern)
                bound = bind(srv, (struct sockaddr*)&addr, sizeof(addr)) == 0;
                if (!bound) {
                    usleep(250 * 1000);
                }
            }
            if (bound) {
                boundPort = p;
            }
        }""", 1,
     "bridge port fallback 8770..8779 (sharingd squats 8770 on this Mac)"),
    ('        NSLog(@"[SohIosShell] console bridge listening on :8770");',
     '        NSLog(@"[SohIosShell] console bridge listening on :%d", boundPort);', 1,
     "log the port actually bound"),

    # 1b. `scroll X DY` — the missing half of `click`.
    #
    # FOUND BY NEEDING IT (feature verification, 2026-08-01). The bridge could
    # click anything it could SEE, but a phone viewport is 402pt tall and the
    # LUS menu routinely runs longer than that: Network -> Anchor puts its
    # Enable button below the fold, so the single action that starts a co-op
    # session was unreachable from a script. Every port has pages like this.
    #
    # The shell already owns the mechanism -- the menu touch-router calls
    # SohIos_QueueMenuScroll for finger drags, and overlay 0018 drains the
    # queue inside each scrollable BeginChild. This exposes that same call to
    # the bridge, so a script scrolls exactly the way a finger does. Wheel
    # events are deliberately NOT used: overlay 0013 records that they fire
    # tooltips and make sliders jump.
    #
    # X is the finger x in view POINTS and picks which column scrolls (the LUS
    # menu is multi-column); DY is a pixel delta with the same sign convention
    # as a drag, so a NEGATIVE dy pushes the content up, revealing what is
    # below -- the direction you almost always want.
    ('    if ([cmd isEqualToString:@"click"] && tok.count >= 3) {\n'
     '        SohIos_InjectClick(tok[1].intValue, tok[2].intValue);\n'
     '        return @"ok";\n'
     '    }',
     '    if ([cmd isEqualToString:@"click"] && tok.count >= 3) {\n'
     '        SohIos_InjectClick(tok[1].intValue, tok[2].intValue);\n'
     '        return @"ok";\n'
     '    }\n'
     '    if ([cmd isEqualToString:@"scroll"] && tok.count >= 3) {\n'
     '        // LIGHTHOUSE: reach menu content below the fold (see adapt-shell.py).\n'
     '        SohIos_QueueMenuScroll(tok[1].floatValue, tok[2].floatValue);\n'
     '        return @"ok";\n'
     '    }', 1,
     "bridge `scroll X DY` — menu content below the fold was script-unreachable"),

    # 2. lighthouse://3d toggles stereo mode, so a script can enter 3D without
    #    the bridge and without a gaze-tap on the ornament.
    ('    if ([url hasPrefix:@"lighthouse://menu"]) {',
     '    if ([url hasPrefix:@"lighthouse://3d"]) {\n'
     '        // LIGHTHOUSE: script-drivable 3D toggle. The ornament button needs a\n'
     '        // gaze-tap, and the bridge may not have a port; this needs neither,\n'
     '        // so an automated stereo repro is possible on the simulator.\n'
     '#if TARGET_OS_VISION\n'
     '        extern int Soh_Get3DMode(void);\n'
     '        extern void Soh_Enter3D(bool on);\n'
     '        Soh_Enter3D(Soh_Get3DMode() == 0);\n'
     '#else\n'
     '        NSLog(@"[SohIosShell] lighthouse://3d ignored: not visionOS");\n'
     '#endif\n'
     '    } else if ([url hasPrefix:@"lighthouse://menu"]) {', 1,
     "lighthouse://3d deep link so stereo can be driven from a script"),

    # --- user-facing ROM-onboarding copy -------------------------------------
    # Caught on the FIRST sim boot: the alert read "Ocarina of Time ROM needed"
    # in a Banjo-Kazooie port. These are the only game-named strings in the
    # shell, and they are the first thing a new user ever sees.
    ('@"Ocarina of Time ROM needed"', '@"Banjo-Kazooie ROM needed"', 1,
     "onboarding alert title"),
    ('@"Pick your legally-owned OoT ROM (.z64 / .n64 / .v64). It will "',
     '@"Pick your legally-owned Banjo-Kazooie ROM (.z64). It will "', 1,
     "onboarding alert body — Lighthouse's picker filters .z64/.n64/.v64 but "
     "upstream only accepts .z64, so don't promise the others"),
    ('@"Drop your Ocarina of Time ROM (.z64) or an extracted oot.o2r here,\\n"',
     '@"Drop your Banjo-Kazooie ROM (.z64) or an extracted bk.o2r here,\\n"', 1,
     "Files-app drop-target readme; the archive is bk.o2r here, not oot.o2r"),
]


def main():
    if not SHELL.is_file():
        print(f"FATAL: {SHELL} not found — copy it from the flagship first", file=sys.stderr)
        return 2

    text = SHELL.read_text()
    failures = []
    for old, new, want, why in EDITS:
        got = text.count(old)
        if got != want:
            failures.append(f"  {old!r}: expected {want} matches, found {got}  ({why})")
        else:
            text = text.replace(old, new)

    if failures:
        print("FATAL: shell adaptation match-count assertions failed:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print("\nThe shell is not in the expected pristine-flagship state. Re-copy it,\n"
              "or update EDITS if the flagship's shell genuinely changed.", file=sys.stderr)
        return 1

    SHELL.write_text(text)
    for old, new, want, _ in EDITS:
        print(f"  {old!r} -> {new!r}  ({want} occurrence{'s' if want != 1 else ''})")
    print(f"adapted {SHELL.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
