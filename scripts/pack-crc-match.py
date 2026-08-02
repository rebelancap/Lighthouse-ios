#!/usr/bin/env python3
"""
pack-crc-match.py — map GLideN64 hash-named pack PNGs to Lighthouse asset paths
by computing the SAME CRC GLideN64 used to name them.

Why this replaces the content-matching attempt
----------------------------------------------
`build-pack-o2r.py` matched pack art to vanilla art perceptually and reached
only 9.1 % (MEASUREMENTS M-005). BK-Reloaded is a REDRAW, not an upscale
filter, so downsampled similarity is genuinely weak. This approach does not
look at the pack's pixels at all: it recomputes the hash from the VANILLA
texels in bk.o2r and matches the filename. Exact, not perceptual.

The algorithm is transcribed from GLideN64 `src/GLideNHQ/TxUtil.cpp`
(`TxUtil::checksum64`, `RiceCRC32`, `RiceCRC32_CI4/CI8`, `CalculateMaxCI4b/8b`)
— the non-ASM reference path, not from memory:

    bytesPerLine = width << size >> 1
    crc = 0; y = height - 1
    do {
        x = bytesPerLine - 4
        do {
            esi  = u32le(src + x)
            esi ^= x
            crc  = rol32(crc, 4) + esi        # (crc<<4) + ((crc>>28)&15)
            x   -= 4
        } while x >= 0
        esi ^= y
        crc += esi
        src += rowStride
        --y
    } while y >= 0

For CI textures the filename also carries a palette CRC:
    palCrc = RiceCRC32(palette, cimax+1, 1, size=2, rowStride=32 or 512)
where cimax is the largest palette index actually used (CalculateMaxCI4b/8b).

Filenames: <rom>#<texCRC>#<fmt>#<siz>[#<palCRC>]_<kind>.png  (all hex, upper)

Usage:
    pack-crc-match.py --archive vendor/Lighthouse/bk.o2r --pack work/packs/src-hd
                      [--report out.json]
"""

import argparse
import collections
import json
import re
import struct
import sys
import zipfile
from pathlib import Path

PACK_RE = re.compile(
    r"^(?P<rom>.+?)#(?P<crc>[0-9A-Fa-f]{8})#(?P<fmt>[0-9A-Fa-f])#(?P<siz>[0-9A-Fa-f])"
    r"(?:#(?P<palcrc>[0-9A-Fa-f]{8}))?_(?P<kind>[A-Za-z]+)\.png$"
)

OTR_HEADER_SIZE = 64
RESTYPE_TEXTURE = 0x4F544558

# Fast::TextureType -> (n64 fmt, n64 siz).  fmt: 0=RGBA 2=CI 3=IA 4=I
TEXTYPE = {
    1: (0, 3),   # RGBA32bpp
    2: (0, 2),   # RGBA16bpp
    3: (2, 0),   # Palette4bpp  (CI4)
    4: (2, 1),   # Palette8bpp  (CI8)
    5: (4, 0),   # Grayscale4bpp        I4
    6: (4, 1),   # Grayscale8bpp        I8
    7: (3, 0),   # GrayscaleAlpha4bpp   IA4
    8: (3, 1),   # GrayscaleAlpha8bpp   IA8
    9: (3, 2),   # GrayscaleAlpha16bpp  IA16
}

M32 = 0xFFFFFFFF


def rice_crc32(buf: bytes, width: int, height: int, size: int, row_stride: int) -> int:
    """Verbatim transcription of TxUtil::RiceCRC32 (non-ASM path)."""
    bytes_per_line = (width << size) >> 1
    if bytes_per_line < 4 or height < 1:
        return None
    crc = 0
    base = 0
    y = height - 1
    while True:
        esi = 0
        x = bytes_per_line - 4
        while True:
            off = base + x
            if off < 0 or off + 4 > len(buf):
                return None
            # BIG-ENDIAN. GLideN64 reads *(uint32*)src from RDRAM, which the
            # emulator holds byte-swapped to native order; Torch stores the
            # N64's own big-endian texels. Reading little-endian here scored
            # exactly ZERO matches out of 6667; big-endian scored 1539.
            esi = struct.unpack_from(">I", buf, off)[0]
            esi = (esi ^ (x & M32)) & M32
            crc = ((crc << 4) & M32) + ((crc >> 28) & 15)
            crc = (crc + esi) & M32
            x -= 4
            if x < 0:
                break
        esi = (esi ^ (y & M32)) & M32
        crc = (crc + esi) & M32
        base += row_stride
        y -= 1
        if y < 0:
            break
    return crc


def cimax_ci8(buf, width, height, row_stride):
    val = 0
    for y in range(height):
        row = buf[row_stride * y: row_stride * y + width]
        if not row:
            continue
        m = max(row)
        if m > val:
            val = m
        if val == 0xFF:
            return 0xFF
    return val


def cimax_ci4(buf, width, height, row_stride):
    val = 0
    w = width >> 1
    for y in range(height):
        row = buf[row_stride * y: row_stride * y + w]
        for b in row:
            v1, v2 = b >> 4, b & 0xF
            if v1 > val:
                val = v1
            if v2 > val:
                val = v2
            if val == 0xF:
                return 0xF
    return val


def read_texture(raw: bytes):
    """Parse a LUS binary Texture resource -> (type, w, h, texel bytes)."""
    if len(raw) < OTR_HEADER_SIZE + 16:
        return None
    if struct.unpack_from("<I", raw, 4)[0] != RESTYPE_TEXTURE:
        return None
    ver = struct.unpack_from("<I", raw, 8)[0]
    o = OTR_HEADER_SIZE
    if ver == 0:
        ttype, w, h, sz = struct.unpack_from("<IIII", raw, o)
        data = raw[o + 16:]
    elif ver == 1:
        ttype, w, h, flags, hs, vs, sz = struct.unpack_from("<IIIIffI", raw, o)
        data = raw[o + 28:]
    else:
        return None
    return ttype, w, h, data[:sz] if sz and sz <= len(data) else data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--out", type=Path, help="write the .o2r here")
    ap.add_argument("--mod-name", default="BK-Reloaded-Lighthouse")
    ap.add_argument("--mod-version", default="0.2.0")
    args = ap.parse_args()

    z = zipfile.ZipFile(args.archive)
    names = z.namelist()

    # ---- index the pack by (crc, fmt, siz) --------------------------------
    pack = {}
    for p in args.pack.rglob("*.png"):
        m = PACK_RE.match(p.name)
        if m:
            g = m.groupdict()
            pack[(int(g["crc"], 16), int(g["fmt"], 16), int(g["siz"], 16))] = (p, g)
    print(f"pack entries: {len(pack)}", file=sys.stderr)

    # ---- walk every vanilla texture, compute its Rice CRC -----------------
    tex, tluts = {}, {}
    for n in names:
        if n.endswith("_TLUT"):
            r = read_texture(z.read(n))
            if r:
                tluts[n] = r
            continue
        try:
            raw = z.read(n)
        except Exception:
            continue
        r = read_texture(raw)
        if r:
            tex[n] = r
    print(f"vanilla textures: {len(tex)}  tluts: {len(tluts)}", file=sys.stderr)

    matched, stats = {}, collections.Counter()
    for name, (ttype, w, h, data) in tex.items():
        if ttype not in TEXTYPE:
            stats["unknown-type"] += 1
            continue
        fmt, siz = TEXTYPE[ttype]
        row_stride = (w << siz) >> 1          # full-texture load: stride == line
        crc = rice_crc32(data, w, h, siz, row_stride)
        if crc is None:
            stats["crc-unavailable"] += 1
            continue
        key = (crc, fmt, siz)
        if key in pack:
            matched.setdefault(name, pack[key])
            stats["hit"] += 1
        else:
            stats["miss"] += 1

    print(f"\nRice-CRC hits: {stats['hit']}  misses: {stats['miss']}  "
          f"other: {stats['unknown-type'] + stats['crc-unavailable']}", file=sys.stderr)
    print(f"pack coverage: {len(matched)}/{len(pack)} "
          f"({100.0 * len(matched) / max(len(pack), 1):.1f}%)", file=sys.stderr)

    # ---- emit the o2r ------------------------------------------------------
    if args.out:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("bp", Path(__file__).parent / "build-pack-o2r.py")
        bp = _ilu.module_from_spec(spec)
        _src = (Path(__file__).parent / "build-pack-o2r.py").read_text().split("def main()")[0]
        _ns = {"__name__": "bp"}
        exec(_src, _ns)
        encode = _ns["encode_texture_resource"]
        ALT = _ns["ALT_PREFIX"]
        mods_toml = ('[mod]\n'
                     f'name = "{args.mod_name}"\n'
                     f'version = "{args.mod_version}"\n')
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_STORED) as zo:
            zo.writestr("mods.toml", mods_toml)
            for asset, (png, g) in sorted(matched.items()):
                _, w, h, _d = tex[asset]
                zo.writestr(ALT + asset, encode(png, w, h))
        sz = args.out.stat().st_size
        print(f"wrote {args.out}: {len(matched)} textures, {sz} bytes "
              f"({sz/1048576:.1f} MB)", file=sys.stderr)

    if args.report:
        args.report.write_text(json.dumps({
            "pack_entries": len(pack),
            "vanilla_textures": len(tex),
            "hits": stats["hit"],
            "misses": stats["miss"],
            "matches": {k: str(v[0]) for k, v in matched.items()},
        }, indent=1))
        print(f"report -> {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
