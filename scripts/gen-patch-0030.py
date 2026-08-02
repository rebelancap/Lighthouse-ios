#!/usr/bin/env python3
"""Overlay patch 0030: thread-safe CVar map (LUS ConsoleVariable).
Two device crashes in one Vision Pro session (2026-07-16), both SIGSEGV at
CVarGetInteger+36 on OTRAudio_Thread via AudioSynth_DoOneAudioUpdate —
SoH's synth reads CVAR_ENHANCEMENT("MirroredWorld") every audio update
(audio_synthesis.c:657, plus two more audio-thread reads in
audio_playback.c), while the main thread writes CVars (death sequence,
pause-menu open, any menu interaction). ConsoleVariable::mVariables is a
bare std::unordered_map: a concurrent insert can rehash while the audio
thread is inside find() -> freed-bucket deref -> SEGV. The two crash
moments (death, pause) are exactly the big main-thread CVar-write moments.
Fix: a recursive mutex over every mVariables access. All value reads
funnel through Get(); writes are the five Set*, ClearVariable,
CopyVariable, Save (iteration), Load (clear). recursive_mutex because
Register*/ClearVariable/Load call Get()/Set*() internally. Uncontended
lock cost is tens of ns against hundreds of calls/frame — noise.
Residual (documented, not fixed): GetString returns a char* the map
doesn't own after a concurrent SetString free — no audio-thread call site
reads strings today (all three are integers)."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LUS = ROOT / "vendor/Lighthouse/libultraship"

def gen(path_rel, edits, tag):
    src = LUS / path_rel
    orig = src.read_text()
    t = orig
    for i, (old, new) in enumerate(edits):
        n = t.count(old)
        assert n == 1, f"[{tag}:{i}] expected 1 match, got {n}"
        t = t.replace(old, new)
    rel = f"libultraship/{path_rel}"
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(t); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1
    return r.stdout.decode()

LOCK = "    std::lock_guard<std::recursive_mutex> sohIosLock(mVariablesMutex);\n"

diffs = []

# Header: <mutex> include + the mutex member next to the map it guards.
diffs.append(gen(
    "include/ship/config/ConsoleVariable.h",
    [(
        "#include <memory>\n#include <unordered_map>\n",
        "#include <memory>\n#include <mutex>\n#include <unordered_map>\n",
    ), (
        "    std::unordered_map<std::string, std::shared_ptr<CVar>, TransparentStringHash, TransparentStringEqual> mVariables;\n",
        "    std::unordered_map<std::string, std::shared_ptr<CVar>, TransparentStringHash, TransparentStringEqual> mVariables;\n"
        "    // SOH_IOS (overlay 0030): guards mVariables — SoH's audio thread reads\n"
        "    // CVars every synth update while the main thread writes them (menu,\n"
        "    // death sequence); an unguarded rehash SEGVs the concurrent find().\n"
        "    // Recursive: Register*/ClearVariable/Load re-enter via Get()/Set*().\n"
        "    mutable std::recursive_mutex mVariablesMutex;\n",
    )],
    "header"))

# Impl: lock at the top of every method that touches mVariables directly.
cpp_edits = []

cpp_edits.append((
    "std::shared_ptr<CVar> ConsoleVariable::Get(const char* name) {\n"
    "    auto it = mVariables.find(name);\n",
    "std::shared_ptr<CVar> ConsoleVariable::Get(const char* name) {\n"
    "    // SOH_IOS (overlay 0030): every read funnels through here — see header.\n"
    + LOCK +
    "    auto it = mVariables.find(name);\n",
))

for sig in (
    "void ConsoleVariable::SetInteger(const char* name, int32_t value) {\n",
    "void ConsoleVariable::SetFloat(const char* name, float value) {\n",
    "void ConsoleVariable::SetString(const char* name, const char* value) {\n",
    "void ConsoleVariable::SetColor(const char* name, Color_RGBA8 value) {\n",
    "void ConsoleVariable::SetColor24(const char* name, Color_RGB8 value) {\n",
    "void ConsoleVariable::ClearVariable(const char* name) {\n",
    "void ConsoleVariable::CopyVariable(const char* from, const char* to) {\n",
    "void ConsoleVariable::Save() {\n",
    "void ConsoleVariable::Load() {\n",
):
    cpp_edits.append((sig, sig + LOCK))

diffs.append(gen("src/ship/config/ConsoleVariable.cpp", cpp_edits, "impl"))

out = ROOT / "overlay/patches/0030-lus-cvar-thread-safety.patch"
out.write_text(__doc__ + "\n\n" + "".join(diffs))
print(f"wrote {out}")
