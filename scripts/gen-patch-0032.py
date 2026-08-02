#!/usr/bin/env python3
"""Overlay patch 0032: export Banjo-Kazooie's live camera basis for stereo 3D.

This is the genuinely port-specific half of Phase 02 — the flagship's 0032
reads OoT's `z_view.c`, and BK has no such file. What it has is better: a
`viewport` module (`src/core1/viewport.c`) that owns the camera outright.

WHY A BASIS AND NOT AN OFFSET. N64 ports hand Fast3D a COMBINED
viewing*projection matrix in `G_MTX_PROJECTION` — the modelview stack is world
space — so the eye offset cannot be injected as a naive translation, and the
camera axes cannot be recovered from the combined matrix either. The flagship
lost four device rounds to world-space offsets: depth varied with camera
heading, vertical parallax broke fusion outright, and stereo cut out entirely
at some headings. The game must export its axes. Overlay 0031 consumes them.

WHAT IS EXPORTED, and where each value comes from:

  gSoh3DCamEye[3]    `sViewportPosition` — the camera's world position.
  gSoh3DCamFwd[3]    `sViewportLookbk_vector` — BK's own forward vector,
                     recomputed by `viewport_update()` as
                     RotYaw(yaw) * RotPitch(pitch) applied to (0,0,-1). Using
                     the game's own answer rather than re-deriving the Euler
                     convention here is deliberate: a sign error in a
                     re-derivation would show up as a subtly wrong eye
                     offset, which is exactly the kind of bug that reads as
                     "the 3D feels off" and takes a device round to name.
  gSoh3DCamRight[3]  normalize(cross(fwd, worldUp)). NOT cross(fwd, cameraUp):
                     under pitch the two agree, and where they differ (roll)
                     the world-up version is the one with no vertical
                     component. Stereo never wants vertical parallax, so the
                     more constrained vector is the correct one, not an
                     approximation of it. Guarded against the degenerate case
                     (looking straight up or down) by keeping the last good
                     value.
  gSoh3DCamP00       cot(fovx/2) = 1 / (tan(fovy/2) * aspect), from
                     `sViewportFOVy` / `sViewportAspect` — the same two values
                     handed to `guPerspective` on the next line.
  gSoh3DCamDist      |player - camera|. BK's camera orbits Banjo, so this is
                     the camera-to-subject distance that adaptive convergence
                     anchors zero parallax on. `player_position` is a plain
                     global array (`playerposition.c`), so reading it is safe
                     in any game mode; 0031 clamps the value and falls back
                     when it is out of range, which covers cutscene and static
                     cameras that legitimately sit far from Banjo.

Plus the three comfort flags 0031 blends on (playbook 2.6):

  gSoh3DInPlay       `getGameMode() == GAME_MODE_3_NORMAL`.
  gSoh3DPaused       `getGameMode() == GAME_MODE_4_PAUSED`. Together these
                     flatten stereo outside gameplay — the title, file select
                     and pause screens draw their own perspective backgrounds,
                     which fight the panel plane and read as doubles.
  gSoh3DAiming       `dynamicCameraInFirstPerson`. BK's first-person view
                     (egg shooting, look-around) puts the camera inside Banjo,
                     where full depth reads as double vision; 0031 blends
                     depth to 30% and convergence bias to 50% while it is set.

PLACEMENT. `viewport_setRenderPerspectiveMatrix()`, immediately before its
`guPerspective` call — the point where this frame's projection is actually
composed, so the exported basis is definitionally the one the eye passes will
modify. `viewport_update()` was the other candidate; it is called from ten
sites and would have made "is the export current for THIS draw" a question
rather than a fact.

Guarded on `__IOS__`; the macOS oracle compiles none of it and the iPhone
build carries it inert (the mode flag never leaves 0 there).
"""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/src/core1/viewport.c"
REL = "src/core1/viewport.c"
orig = SRC.read_text()

# --- the publisher, at file scope -------------------------------------------
FN_OLD = "void viewport_setRenderPerspectiveMatrix(Gfx **gfx, Mtx **mtx, f32 near, f32 far) {\n"
FN_NEW = """#ifdef __IOS__
/* LIGHTHOUSE_IOS (overlay 0032): live camera BASIS for stereoscopic 3D.
 * Defined in the app shell (SohIosShell.m), which is linked on iPhone too, so
 * this resolves on every Apple build; only visionOS ever reads it. See the
 * patch header for why a basis and not a world-space offset. */
extern volatile f32 gSoh3DCamDist, gSoh3DCamP00;
extern volatile f32 gSoh3DCamRight[3], gSoh3DCamFwd[3], gSoh3DCamEye[3];
extern volatile s32 gSoh3DPaused, gSoh3DInPlay, gSoh3DAiming;
extern u8 dynamicCameraInFirstPerson; /* dynamicCamera.c, non-static */

static void lhIos_publishCameraBasis(void) {
    f32 fwd[3];
    f32 rx, ry, rz, rlen;
    f32 playerPos[3];
    f32 halfFovX;
    s32 mode;

    ml_vec3f_copy(fwd, sViewportLookbk_vector);

    /* right = normalize(cross(forward, worldUp)) with worldUp = (0,1,0), which
     * reduces to (-fwd.z, 0, fwd.x). Deliberately world-up rather than the
     * camera's own up: the two agree under yaw and pitch, and where they
     * differ (roll) this one has no vertical component. Stereo must never
     * introduce vertical parallax, so the more constrained vector is the
     * correct answer, not an approximation of it. Sanity check: at identity
     * BK's forward is (0,0,-1), which gives right = (1,0,0) = +X.
     * Degenerate only when looking near-straight up or down, where last
     * frame's value beats a normalized zero vector. */
    rx = -fwd[2];
    ry = 0.0f;
    rz = fwd[0];
    rlen = sqrtf(rx * rx + ry * ry + rz * rz);
    if (rlen > 1e-4f) {
        gSoh3DCamRight[0] = rx / rlen;
        gSoh3DCamRight[1] = ry / rlen;
        gSoh3DCamRight[2] = rz / rlen;
        gSoh3DCamFwd[0] = fwd[0];
        gSoh3DCamFwd[1] = fwd[1];
        gSoh3DCamFwd[2] = fwd[2];
    }

    gSoh3DCamEye[0] = sViewportPosition[0];
    gSoh3DCamEye[1] = sViewportPosition[1];
    gSoh3DCamEye[2] = sViewportPosition[2];

    /* P00 = cot(fovx/2), from the same fovy/aspect guPerspective gets below. */
    halfFovX = tanf(sViewportFOVy * 0.5f * (f32)(BAD_PI / 180.0)) * sViewportAspect;
    gSoh3DCamP00 = (halfFovX > 1e-4f) ? (1.0f / halfFovX) : 1.0f;

    /* Adaptive convergence anchor: BK's camera orbits Banjo, so the
     * camera-to-player distance is the subject distance. 0031 clamps this and
     * falls back when a cutscene or static camera puts it out of range. */
    player_getPosition(playerPos);
    gSoh3DCamDist = ml_vec3f_distance(playerPos, sViewportPosition);

    mode = (s32)getGameMode();
    gSoh3DInPlay = (mode == GAME_MODE_3_NORMAL) ? 1 : 0;
    gSoh3DPaused = (mode == GAME_MODE_4_PAUSED) ? 1 : 0;
    gSoh3DAiming = dynamicCameraInFirstPerson ? 1 : 0;
}
#endif

void viewport_setRenderPerspectiveMatrix(Gfx **gfx, Mtx **mtx, f32 near, f32 far) {
"""
n = orig.count(FN_OLD)
assert n == 1, f"[fn-anchor] expected 1 match, got {n}"
t = orig.replace(FN_OLD, FN_NEW)

# --- the call, immediately before the projection is composed ----------------
CALL_OLD = """    guPerspective(*mtx, &perspNorm, sViewportFOVy, sViewportAspect, near, far, 0.5f);
"""
CALL_NEW = """#ifdef __IOS__
    /* LIGHTHOUSE_IOS (overlay 0032): publish the camera basis for the stereo
     * eye passes, here rather than in viewport_update() so it is current for
     * THIS draw by construction. */
    lhIos_publishCameraBasis();
#endif
    guPerspective(*mtx, &perspNorm, sViewportFOVy, sViewportAspect, near, far, 0.5f);
"""
n = t.count(CALL_OLD)
assert n == 1, f"[call-anchor] expected 1 match, got {n}"
t = t.replace(CALL_OLD, CALL_NEW)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig)
    fb.write(t)
    fa.flush()
    fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0032-lighthouse-camera-basis-export.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
