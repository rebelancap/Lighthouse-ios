#!/usr/bin/env python3
"""
stamp-port-version.py — add the `portVersion` record to a CLI-extracted o2r.

THE TRAP THIS FIXES (measured 2026-07-31, cost one 18-minute extraction):

`torch o2r baserom.z64` — which is exactly what upstream's own `ExtractAssets`
CMake target runs, and what scripts/extract-bk-o2r.sh replicates — produces an
archive with NO `portVersion` record. Only the in-app extractor writes one
(`GameExtractor::WritePortVersion()` →
`Companion::RegisterCompanionFile("portVersion", …)`).

On boot, Engine.cpp does:

    romArchiveVersion = DetectOTRVersion("bk.o2r")   // no record  -> {0,0,0}
    shouldRegen = !VerifyArchiveVersion(v) && v.major != INT16_MAX
                //  {0,0,0} != build version         -> true
                //  0 != INT16_MAX                   -> true
    if (shouldRegen) for (archive : sRomArchives) std::filesystem::remove(archive);

…so the game **silently deletes bk.o2r on first launch** and asks you to
re-extract. `VerifyArchiveVersion` compares major and minor only.

Record format (verified against Ghostship's sm64.o2r, which has one):
6 bytes, three BIG-ENDIAN uint16s — major, minor, patch.

Usage:
    stamp-port-version.py <archive.o2r> [--version 1.0.0]

Default version comes from the pinned upstream's `project(Lighthouse VERSION …)`.
"""

import argparse
import shutil
import struct
import sys
import zipfile
from pathlib import Path

DEFAULT_VERSION = "1.0.0"
RECORD_NAME = "portVersion"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive", type=Path)
    ap.add_argument("--version", default=DEFAULT_VERSION,
                    help="major.minor.patch (must match the built binary's "
                         "gBuildVersionMajor/Minor, or the game deletes the archive)")
    args = ap.parse_args()

    if not args.archive.is_file():
        print(f"FATAL: no such archive: {args.archive}", file=sys.stderr)
        return 2

    try:
        major, minor, patch = (int(x) for x in args.version.split("."))
    except ValueError:
        print(f"FATAL: --version must be major.minor.patch, got {args.version!r}", file=sys.stderr)
        return 2

    record = struct.pack(">HHH", major, minor, patch)

    with zipfile.ZipFile(args.archive) as z:
        existing = set(z.namelist())
    if RECORD_NAME in existing:
        with zipfile.ZipFile(args.archive) as z:
            cur = z.read(RECORD_NAME)
        if cur == record:
            print(f"{args.archive.name}: already stamped {args.version} — nothing to do")
            return 0
        # Rewriting an existing entry means rebuilding the archive; refuse rather
        # than silently produce a zip with two same-named members.
        print(f"FATAL: {args.archive.name} already has a different {RECORD_NAME} "
              f"({cur.hex()}); refusing to append a duplicate", file=sys.stderr)
        return 3

    backup = args.archive.with_suffix(args.archive.suffix + ".prestamp")
    if not backup.exists():
        shutil.copy2(args.archive, backup)

    # o2r archives are STORED (uncompressed) and read in place — match that.
    with zipfile.ZipFile(args.archive, "a", compression=zipfile.ZIP_STORED) as z:
        z.writestr(RECORD_NAME, record)

    with zipfile.ZipFile(args.archive) as z:
        got = z.read(RECORD_NAME)
    if got != record:
        print("FATAL: readback mismatch after stamping", file=sys.stderr)
        return 4

    print(f"{args.archive.name}: stamped {RECORD_NAME} = {major}.{minor}.{patch} "
          f"({record.hex()}); backup at {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
