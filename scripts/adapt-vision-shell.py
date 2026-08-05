#!/usr/bin/env python3
"""Adapt the flagship's visionOS shell files for Lighthouse.

Companion to adapt-shell.py, same discipline and same reasoning: the vision
files (SohHostViewController, SohImmersive, SohVisionApp.swift, the bridging
header) are copied VERBATIM from Shipwright-ios and kept as close to it as
possible, because harbour-shell (program D9) will be distilled from the
proven shells and gratuitous divergence is a cost. The `Soh3D_` / `Soh_` /
`SohIos_` symbol namespaces stay AS THEY ARE in every port.

The adaptation surface here turned out to be almost nothing — which is the
finding, not a shortcut. SohImmersive.m is fully self-contained (its only
engine contact is Soh3D_GetEyeMTLTexture / Soh3D_GetEyeFrames, defined in
SohHostViewController.m against globals the shared shell already declares),
and SohVisionApp.swift is pure scene plumbing. Only two strings are actually
port-specific, and both are user- or log-visible.

Every edit asserts its match count. Remember the lesson adapt-shell.py
records: a match-count assert proves HOW MANY, not WHICH — so match on
semantically load-bearing text, never on a comment that happens to contain
the same token.

Run after re-copying the vision files from the flagship. Idempotent in the
same way as adapt-shell.py: re-running on already-adapted files reports zero
matches and exits non-zero, so it can never half-apply.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
IOS = ROOT / "app/ios"

# A real newline, so the multi-line C snippet below is emitted as actual
# lines. Spelled as chr(10) because writing it literally through a shell
# heredoc kept collapsing into an escape (mangled three times today).
NL = chr(10)

# file -> [(old, new, expected_count, why)]
EDITS = {
    "SohHostViewController.m": [
        # argv[0] reaches the game's own arg parsing and every log line the
        # engine prints about its invocation. Overlay 0039 fixed a real
        # argv-handling null deref in this port, so leaving a foreign argv[0]
        # here would make any future argv bug read as a flagship bug.
        ('static char arg0[] = "soh";', 'static char arg0[] = "lighthouse";', 1,
         "argv[0] handed to SDL_main — reaches the game's arg parsing and logs"),
    ],
    "SohImmersive.m": [
        # The fidelity report is printed to the console bridge, i.e. it is
        # read by a human deciding whether THIS port looks right.
        ('@"Ship of Harkinian Vision Pro 3D fidelity report\\n"',
         '@"Lighthouse Vision Pro 3D fidelity report\\n"', 1,
         "bridge-visible fidelity report header"),

        # PANEL ASPECT (user, device, 2026-08-01 — rev2, first attempt was wrong).
        #
        # Original bug: the quad is scaled straight to (halfW, halfH) with the eye
        # texture mapped across all of it, so Width and Height were an anamorphic
        # STRETCH of the picture.
        #
        # My first fix fitted the image inside the box at the source aspect. The
        # user rejected it, correctly: "if you make it TALLER, it also makes it
        # WIDER" — because a fitted rectangle derives one edge from the other, so
        # the two sliders stopped being independent. What was asked for is what
        # the 2D window already does: "the user should be able to make the screen
        # super skinny or super wide just like they can in the 2d panel".
        #
        # In the 2D window, resizing does not letterbox and does not stretch — the
        # ENGINE RE-RENDERS at the new aspect, so a wider window shows more world.
        # The 3D path has the same lever and NO port has ever used it: gSoh3DEyeW/H
        # are declared in the shared shell and left at 0 (= fixed 3840x2160) in
        # every sibling. Drive them from the panel aspect and the panel is free to
        # be any shape, undistorted, with the sliders fully independent.
        #
        # Pixel BUDGET is held constant (3840x2160 worth) rather than one edge, so
        # an extreme aspect costs no extra memory — which matters because the eye
        # framebuffers are ringed 4 deep per eye.
        ('void Soh3D_SetPanel(float dist, float halfW, float halfH) {',
         '// LIGHTHOUSE: the engine renders the eyes AT THE PANEL ASPECT (see'
         + NL +
         '// scripts/adapt-vision-shell.py). gSoh3DEyeW/H live in the shared shell'
         + NL +
         '// and are 0 in every other port, which pins the eyes to 16:9 and makes'
         + NL +
         '// the size sliders stretch the picture.'
         + NL +
         'extern volatile int gSoh3DEyeW, gSoh3DEyeH;'
         + NL +
         ''
         + NL +
         'static void lhIos_updateEyeAspect(void) {'
         + NL +
         '    const double lhBudget = 3840.0 * 2160.0; // hold pixel COUNT constant'
         + NL +
         '    double lhAspect = (double)soh3d_screenHalfW / (double)soh3d_screenHalfH;'
         + NL +
         '    if (!(lhAspect > 0.05 && lhAspect < 20.0)) {'
         + NL +
         '        lhAspect = 16.0 / 9.0;'
         + NL +
         '    }'
         + NL +
         '    int lhW = (int)lround(sqrt(lhBudget * lhAspect));'
         + NL +
         '    int lhH = (int)lround(sqrt(lhBudget / lhAspect));'
         + NL +
         '    lhW = (lhW + 7) & ~7; // multiples of 8 keep the blit paths happy'
         + NL +
         '    lhH = (lhH + 7) & ~7;'
         + NL +
         '    if (lhW < 640) { lhW = 640; }'
         + NL +
         '    if (lhH < 640) { lhH = 640; }'
         + NL +
         '    if (lhW > 7680) { lhW = 7680; }'
         + NL +
         '    if (lhH > 7680) { lhH = 7680; }'
         + NL +
         '    if (lhW != gSoh3DEyeW || lhH != gSoh3DEyeH) {'
         + NL +
         '        gSoh3DEyeW = lhW;'
         + NL +
         '        gSoh3DEyeH = lhH;'
         + NL +
         '        NSLog(@"[Soh3D] eye render %dx%d for panel aspect %.3f", lhW, lhH, lhAspect);'
         + NL +
         '    }'
         + NL +
         '}'
         + NL +
         ''
         + NL +
         'void Soh3D_SetPanel(float dist, float halfW, float halfH) {', 1,
         "engine renders eyes at the panel aspect; sliders stay independent"),
        ('    if (halfH >= 0.4f && halfH <= 3.0f)'
         + NL +
         '        soh3d_screenHalfH = halfH;'
         + NL +
         '}',
         '    if (halfH >= 0.4f && halfH <= 3.0f)'
         + NL +
         '        soh3d_screenHalfH = halfH;'
         + NL +
         '    lhIos_updateEyeAspect();'
         + NL +
         '}', 1,
         "recompute the eye aspect whenever the panel is resized"),
    ],
    "SohVisionApp.swift": [
        # DEPTH RESCALE (user, device, 2026-08-01). The old scale ran 0-200%
        # with 100% = 0.0325 eye-offset fraction; the user found the upper half
        # unusable and settled around 75%. Rescale so the OLD 150% becomes the
        # new maximum:  new% = old% * 200/150,  hence old 75% == new 100%.
        # The per-100% constant becomes 0.0325 * 0.75 = 0.024375, so the new
        # default of 100% is exactly the setting the user chose by hand.
        #
        # The 0-200% / 100%-default CONVENTION is program-wide (playbook 2.2)
        # and is preserved untouched; only what 100% physically MEANS changes,
        # which is per-port tuning. Persisted values are deliberately not
        # migrated: anyone sitting on 100 lands on the gentler default, which is
        # the intended outcome.
        ('Soh3D_SetStereoParams(f("vp3dDepth200", 100.0) / 100.0 * 0.0325, 1.0)',
         'Soh3D_SetStereoParams(f("vp3dDepth200", 100.0) / 100.0 * 0.024375, 1.0)', 1,
         "depth rescale: new 100% == old 75%, new 200% == old 150%"),
    ],
}


def main():
    failures = []
    staged = {}

    for name, edits in EDITS.items():
        path = IOS / name
        if not path.is_file():
            failures.append(f"  {name}: not found — copy it from the flagship first")
            continue
        text = path.read_text()
        for old, new, want, why in edits:
            got = text.count(old)
            if got != want:
                failures.append(
                    f"  {name}: {old!r}: expected {want} matches, found {got}  ({why})")
            else:
                text = text.replace(old, new)
        staged[path] = text

    if failures:
        print("FATAL: vision-shell adaptation match-count assertions failed:",
              file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print("\nThe vision files are not in the expected pristine-flagship state.\n"
              "Re-copy them, or update EDITS if the flagship's files genuinely\n"
              "changed.", file=sys.stderr)
        return 1

    for path, text in staged.items():
        path.write_text(text)
    for name, edits in EDITS.items():
        for old, new, want, _ in edits:
            print(f"  {name}: {old!r} -> {new!r}  ({want} occurrence"
                  f"{'s' if want != 1 else ''})")
    print(f"adapted {len(staged)} visionOS shell file(s) in app/ios/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
