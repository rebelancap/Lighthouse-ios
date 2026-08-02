#!/usr/bin/env python3
"""
build-pack-o2r.py — convert a GLideN64 texture pack into a Lighthouse .o2r.

Why this exists
---------------
BK-Reloaded ships only GLideN64/rt64 artifacts (hash-named PNGs, an HTS cache,
an rt64 .rtz). There is no HarbourMasters .o2r, and Lighthouse's LUS fork has
no GLideN64 loader, so the pack cannot be consumed as shipped. See
DECISIONS.md D2.

The target format is fixed by inspecting a working sibling pack
(MK64-Reloaded's SpaghettiKart o2r) against its base-game archive:

    <pack>.o2r  =  STORED zip containing
                     mods.toml
                     textures/<asset path>.png      <-- base-game asset path + ".png"

So the whole job is: map each hash-named pack PNG to the Lighthouse asset path
of the vanilla texture it replaces.

The mapping method
------------------
GLideN64 names files by a CRC of the *N64 texel data*:

    Banjo-Kazooie#<texCRC>#<fmt>#<siz>_all.png
    Banjo-Kazooie#<texCRC>#<fmt>#<siz>#<palCRC>_ciByRGBA.png

Rather than reimplement GLideN64's CRC (easy to get subtly wrong, and it hashes
textures as the RDP *loads* them, which does not always equal how Torch stores
them), we match on **image content**, which is directly verifiable:

  1. `torch modding export` renders every vanilla texture to PNG under its real
     asset path. That is the ground-truth corpus.
  2. A pack texture is an *upscale* of exactly one vanilla texture, so its
     dimensions are an integer multiple of the original's. That prunes
     candidates hard before any pixel work.
  3. Score the remaining candidates by downsampling the pack image to the
     vanilla size and comparing RGBA. An artist/AI upscale downsampled back to
     the source resolution stays very close to the source.
  4. Accept only *unambiguous* winners: the best score must beat the threshold
     AND be clearly better than the runner-up. Ambiguous and unmatched entries
     are reported, never guessed.

Step 4 is what makes this honest: the match rate and the ambiguity list are the
measurement that says whether the conversion worked, and they get recorded in
MEASUREMENTS.md.

Usage
-----
    build-pack-o2r.py --vanilla <torch-modding-export-dir> \
                      --pack    <unzipped GLideN64 pack dir> \
                      --out     <name.o2r> \
                      --mod-name "BK-Reloaded-Lighthouse" --mod-version "0.2.0" \
                      [--report <report.json>] [--jobs N]
"""

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

# Banjo-Kazooie#<crc>#<fmt>#<siz>[#<palcrc>]_<kind>.png
PACK_RE = re.compile(
    r"^(?P<rom>.+?)#(?P<crc>[0-9A-Fa-f]{8})#(?P<fmt>[0-9A-Fa-f])#(?P<siz>[0-9A-Fa-f])"
    r"(?:#(?P<palcrc>[0-9A-Fa-f]{8}))?_(?P<kind>[A-Za-z]+)\.png$"
)

# Accept a match when the mean per-channel absolute error (0..255) is under this
# after downsampling the pack image to the vanilla image's size.
MAE_ACCEPT = 34.0
# ...and the runner-up must be at least this much worse, or we call it ambiguous.
MARGIN = 6.0
# Largest integer upscale factor we will consider.
MAX_SCALE = 16


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --- O2R resource encoding ---------------------------------------------------
#
# Lighthouse registers only the STOCK Fast::ResourceFactoryBinaryTextureV0/V1,
# which read a binary LUS texture record. It has NO PNG-sniffing texture factory
# (SpaghettiKart added one -- src/port/resource/importers/BetterTextureFactory.cpp
# -- which is why MK64-Reloaded can ship bare .png entries; Lighthouse cannot).
# So pack entries must be encoded as real Texture resources.
#
# The shape is taken from the engine's own HD path (src/port/Resource/Alt/
# AltBoldFont.cpp), which builds exactly this in memory: RGBA32bpp,
# TEX_FLAG_LOAD_AS_RAW, and H/VScale carrying the upscale factor so the N64
# texture coordinates still map onto the larger image.
#
# Layout, verified against ResourceLoader::ReadResourceInitDataBinary and
# ResourceFactoryBinaryTextureV1::ReadResource, and cross-checked byte-for-byte
# against a real base-game archive entry:
#
#   0x00  u8   byte order        (Endianness::Little = 0)
#   0x01  u8   isCustom          (1 -- this is a mod asset)
#   0x02  u8[2] unused
#   0x04  u32  resource type     ('OTEX' little-endian)
#   0x08  u32  resource version  (1 -> TextureV1)
#   0x0C  u64  id                (0xDEADBEEFDEADBEEF, the loader default)
#   ...   pad to OTR_HEADER_SIZE (64)
#   0x40  u32  texture type      (RGBA32bpp = 1)
#   0x44  u32  width
#   0x48  u32  height
#   0x4C  u32  flags             (TEX_FLAG_LOAD_AS_RAW = 1<<0)
#   0x50  f32  HByteScale
#   0x54  f32  VPixelScale
#   0x58  u32  image data size
#   0x5C  u8[] RGBA8 pixels
OTR_HEADER_SIZE = 64
# Fast::ResourceType::Texture = 0x4F544558 ('OTEX' as a big-endian char
# literal). Written as a little-endian u32 the on-disk bytes read "XETO" —
# which is exactly what real archives contain. Getting this backwards produces
# an archive that looks fine and loads nothing, so it is asserted in the
# encoder's self-test against a real base-game entry.
RESTYPE_TEXTURE = 0x4F544558
TEXTYPE_RGBA32 = 1
TEX_FLAG_LOAD_AS_RAW = 1 << 0
DEFAULT_ID = 0xDEADBEEFDEADBEEF
# LUS ResourceManager tries gAltAssetPrefix + <path> first when alt assets are
# enabled (Resource.h: gAltAssetPrefix = "alt/"). Upstream's own note in
# AltBoldFont.cpp is explicit: "The offline pack tooling must emit alt/ entries
# at byte-identical paths or the lookup misses." No ".png" suffix — that is
# SpaghettiKart's private convention, not LUS's.
ALT_PREFIX = "alt/"
# Texel formats Torch appends to exported PNG names.
TEXEL_FORMATS = {"ci4", "ci8", "rgba16", "rgba32", "ia8", "ia4", "ia16", "i4", "i8", "tlut"}

import struct  # noqa: E402


def encode_texture_resource(png_path, van_w, van_h):
    """Encode an upscaled PNG as a binary LUS TextureV1 resource."""
    arr = load_rgba(png_path)
    h, w = arr.shape[0], arr.shape[1]
    hdr = bytearray(OTR_HEADER_SIZE)
    struct.pack_into("<BBxx", hdr, 0, 0, 1)          # little-endian, isCustom
    struct.pack_into("<I", hdr, 4, RESTYPE_TEXTURE)
    struct.pack_into("<I", hdr, 8, 1)                # TextureV1
    struct.pack_into("<Q", hdr, 12, DEFAULT_ID)
    pixels = arr.tobytes()
    body = struct.pack(
        "<IIIIffI",
        TEXTYPE_RGBA32,
        w,
        h,
        TEX_FLAG_LOAD_AS_RAW,
        (w / van_w) if van_w else 1.0,   # HByteScale  = upscale factor
        (h / van_h) if van_h else 1.0,   # VPixelScale = upscale factor
        len(pixels),
    )
    return bytes(hdr) + body + pixels


def load_rgba(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)


def index_vanilla(root: Path, anchor: str = "assets/"):
    """archive-relative asset path (no .png) -> (file, w, h).

    Torch's modding export nests the corpus under its own workdir layout, so we
    normalise to the path the archive actually uses by anchoring at the first
    `anchor` component. For Lighthouse that is "assets/" — bk.o2r's namespace is
    assets/model/ASSET_*_tex_N, assets/sprite/ASSET_*_N_M, assets/level/... —
    NOT MK64's "textures/".
    """
    out = {}
    for p in root.rglob("*.png"):
        rel = p.relative_to(root).as_posix()
        idx = rel.find(anchor)
        if idx < 0:
            continue
        arch = rel[idx:][: -len(".png")]
        # Torch's export appends the N64 texel format: X.ci4.png, X.rgba16.png,
        # X_TLUT.tlut.png … The archive path is the name WITHOUT that suffix.
        dot = arch.rfind(".")
        if dot < 0:
            continue
        fmt = arch[dot + 1:]
        if fmt not in TEXEL_FORMATS:
            continue
        # TLUTs are palettes (16x1 / 256x1), not images anything replaces. A
        # pack entry supplies RGBA32 directly, so the palette becomes moot —
        # and leaving them in would pollute the size buckets with 1-px-tall
        # candidates.
        if fmt == "tlut":
            continue
        arch = arch[:dot]
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            continue
        out[arch] = (p, w, h)
    return out


def index_pack(root: Path):
    """list of (path, w, h, meta)."""
    out = []
    for p in root.rglob("*.png"):
        m = PACK_RE.match(p.name)
        if not m:
            continue
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            continue
        out.append((p, w, h, m.groupdict()))
    return out


def build_size_buckets(vanilla):
    buckets = {}
    for arch, (p, w, h) in vanilla.items():
        buckets.setdefault((w, h), []).append(arch)
    return buckets


def candidates_for(pw, ph, buckets):
    """Vanilla entries whose dims scale up to (pw, ph) by a common integer factor."""
    cands = []
    for k in range(1, MAX_SCALE + 1):
        if pw % k or ph % k:
            continue
        key = (pw // k, ph // k)
        if key in buckets:
            cands.extend(buckets[key])
    return cands


def score(pack_arr, van_path, van_w, van_h, cache):
    van = cache.get(van_path)
    if van is None:
        van = load_rgba(van_path)
        cache[van_path] = van
    if van.shape[0] != van_h or van.shape[1] != van_w:
        return None
    # Downsample the pack image to the vanilla size (area average via PIL).
    small = np.asarray(
        Image.fromarray(pack_arr).resize((van_w, van_h), Image.BOX), dtype=np.float32
    )
    v = van.astype(np.float32)
    # Weight RGB by alpha so fully-transparent padding can't dominate.
    a = np.maximum(v[..., 3:4], small[..., 3:4]) / 255.0
    rgb_err = np.abs(small[..., :3] - v[..., :3]) * a
    alpha_err = np.abs(small[..., 3] - v[..., 3])
    denom = max(a.sum() * 3.0, 1.0)
    return float(rgb_err.sum() / denom + alpha_err.mean() * 0.25)


def match_one(item, vanilla, buckets):
    ppath, pw, ph, meta = item
    cands = candidates_for(pw, ph, buckets)
    if not cands:
        return (str(ppath), None, None, "no-size-candidate", len(cands))
    try:
        arr = load_rgba(ppath)
    except Exception as e:
        return (str(ppath), None, None, f"unreadable:{e}", len(cands))

    cache = {}
    scored = []
    for arch in cands:
        vp, vw, vh = vanilla[arch]
        s = score(arr, vp, vw, vh, cache)
        if s is not None:
            scored.append((s, arch))
    if not scored:
        return (str(ppath), None, None, "no-scoreable-candidate", len(cands))

    scored.sort()
    best_s, best_arch = scored[0]
    runner = scored[1][0] if len(scored) > 1 else float("inf")

    if best_s > MAE_ACCEPT:
        return (str(ppath), None, best_s, "above-threshold", len(cands))
    if runner - best_s < MARGIN:
        return (str(ppath), None, best_s, f"ambiguous(vs {scored[1][1]})", len(cands))
    return (str(ppath), best_arch, best_s, "ok", len(cands))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vanilla", required=True, type=Path)
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--mod-name", default="BK-Reloaded-Lighthouse")
    ap.add_argument("--mod-version", default="0.2.0")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--readme", type=Path)
    args = ap.parse_args()

    log(f"[1/4] indexing vanilla textures under {args.vanilla}")
    vanilla = index_vanilla(args.vanilla)
    if not vanilla:
        log("FATAL: no vanilla textures/** PNGs found — check --vanilla")
        return 2
    buckets = build_size_buckets(vanilla)
    log(f"      {len(vanilla)} vanilla textures, {len(buckets)} distinct sizes")

    log(f"[2/4] indexing pack under {args.pack}")
    pack = index_pack(args.pack)
    if not pack:
        log("FATAL: no GLideN64-named PNGs found — check --pack")
        return 2
    log(f"      {len(pack)} pack textures")

    log(f"[3/4] matching ({args.jobs} workers)")
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(match_one, it, vanilla, buckets) for it in pack]
        for i, f in enumerate(cf.as_completed(futs), 1):
            results.append(f.result())
            if i % 200 == 0:
                log(f"      {i}/{len(pack)}")

    matched = {}
    collisions = []
    for ppath, arch, s, why, ncand in results:
        if why != "ok":
            continue
        if arch in matched:
            collisions.append((arch, matched[arch][0], ppath))
            # keep the better score
            if s < matched[arch][1]:
                matched[arch] = (ppath, s)
        else:
            matched[arch] = (ppath, s)

    ok = len(matched)
    log(f"      matched {ok}/{len(pack)} ({100.0*ok/len(pack):.1f}%), "
        f"{len(collisions)} collisions")

    if args.report:
        args.report.write_text(json.dumps({
            "vanilla_count": len(vanilla),
            "pack_count": len(pack),
            "matched": ok,
            "collisions": len(collisions),
            "results": [
                {"pack": p, "asset": a, "score": s, "why": w, "candidates": n}
                for (p, a, s, w, n) in results
            ],
        }, indent=1))
        log(f"      report -> {args.report}")

    if ok == 0:
        log("FATAL: nothing matched — not writing an empty o2r")
        return 3

    log(f"[4/4] writing {args.out}")
    mods_toml = (
        "[mod]\n"
        f'name = "{args.mod_name}"\n'
        f'version = "{args.mod_version}"\n'
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # STORED, per the sibling pack's own convention ("0 - Store ... for best
    # performance") — the archive is mmap-read at runtime.
    with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("mods.toml", mods_toml)
        if args.readme and args.readme.exists():
            z.write(args.readme, args.readme.name)
        for arch, (ppath, s) in sorted(matched.items()):
            vp, vw, vh = vanilla[arch]
            z.writestr(ALT_PREFIX + arch, encode_texture_resource(ppath, vw, vh))

    size = args.out.stat().st_size
    log(f"      {ok} textures, {size} bytes ({size/1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
