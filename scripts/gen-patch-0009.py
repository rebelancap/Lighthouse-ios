#!/usr/bin/env python3
"""Overlay patch 0009: make LUS's iOS toolchain PLATFORM overridable.
ios-toolchain-populate.cmake hardcodes PLATFORM=OS64COMBINED, which forces the
device sysroot and overrides a caller's -DCMAKE_OSX_SYSROOT=iphonesimulator —
so simulator builds silently compile for device and fail at link with a
device/simulator object mismatch. Respect an already-set PLATFORM (default
unchanged: OS64COMBINED for device). Simulator builds pass
-DPLATFORM=SIMULATORARM64. Inert for the device path; upstreamable."""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "vendor/Lighthouse/libultraship/cmake/ios-toolchain-populate.cmake"
REL = "libultraship/cmake/ios-toolchain-populate.cmake"
orig = SRC.read_text()

old = 'set(PLATFORM "OS64COMBINED")'
new = ('# Allow the caller to select the Apple platform (e.g. SIMULATORARM64 for a\n'
       '# simulator build); default to device+sim combined as before.\n'
       'if(NOT DEFINED PLATFORM)\n'
       '    set(PLATFORM "OS64COMBINED")\n'
       'endif()')
n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
t = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(t); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1
out = ROOT / "overlay/patches/0009-lus-ios-toolchain-platform-overridable.patch"
out.write_text(__doc__ + "\n\n" + r.stdout.decode())
print(f"wrote {out}")
