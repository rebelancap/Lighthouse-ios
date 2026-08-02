#!/usr/bin/env python3
"""
pack-font-map.py — map BK-Reloaded's UI/Fonts PNGs onto Lighthouse alt-asset
paths and emit them as a .o2r fragment.

Why a separate script (see MEASUREMENTS M-007)
----------------------------------------------
`pack-crc-match.py` matches pack files by recomputing GLideN64's Rice CRC over
STORED vanilla texels. BK's bold font defeats that by construction: each glyph
GLideN64 hashed is a runtime composite of an alphamask chunk and a sphere
gradient (src/core2/font/print.c print_applyTextureToBoldFontLetter), so the
hashed texture never exists as a stored asset in bk.o2r. Upstream anticipated
offline packs: AltBoldFont.cpp serves a pack entry at

    alt/assets/boldfont/<mask chunk basename>_<sphere hex id>

("The offline pack tooling must emit alt/ entries at byte-identical paths or
the lookup misses") before falling back to compositing HD sphere x HD mask.
This script reconstructs the composites offline, byte-exactly, and CRCs THEM.

The dialog font (In-game/) is stored per-glyph as sprite chunks
(assets/sprite/ASSET_6EB_DIALOG_FONT_ALPHAMASK_0_<i>, IA8 8|16 x 13 cells) and
alt entries go at the chunk path (AltDialogFont.cpp / AltSprites.cpp
convention: "alt/" + base path). GLideN64 hashed those as SUB-TILES at the
drawn glyph width with the cell as row stride, so the exact pass here hashes
every (chunk, subwidth) pair.

Evidence tiers — honesty over coverage
--------------------------------------
Every mapping this script emits carries one of these tags, and the report
says which:

  exact-crc        Rice CRC over reconstructed/vanilla v1.1 bytes matches the
                   pack filename. Proof.
  region-twin      pixel-identical art to an exact-crc file (the pack exports
                   one drawing under several regions' hashes). Proof by
                   identity.
  derived          glyph identity from the pack's own folder taxonomy +
                   sphere/glyph classification validated against every
                   exact-crc anchor, with bijection constraints. Not a CRC
                   proof; the validation gate and margins are printed, and any
                   file failing its gate is reported unmapped, never guessed.

Files whose vanilla source bytes are simply not in a US v1.1 bk.o2r (US v1.0
and PAL captures of data that changed between revisions) CANNOT be
CRC-proven offline; that is a property of the pack, not a bug here.

Usage
-----
    pack-font-map.py --archive vendor/Lighthouse/bk.o2r \
                     --pack work/packs/src-hd \
                     --out work/packs/bk-reloaded-v0.2.0-lighthouse-fonts-hd.o2r \
                     [--report work/packs/font-map-report.json]
"""

import argparse
import collections
import hashlib
import json
import re
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Shared machinery, reused not reimplemented: encoder + ALT_PREFIX come from
# build-pack-o2r.py exactly the way pack-crc-match.py imports them.
# ---------------------------------------------------------------------------
_BP_SRC = (Path(__file__).parent / "build-pack-o2r.py").read_text().split("def main()")[0]
_BP = {"__name__": "bp"}
exec(_BP_SRC, _BP)
encode_texture_resource = _BP["encode_texture_resource"]
ALT_PREFIX = _BP["ALT_PREFIX"]

OTR_HEADER_SIZE = 64
RESTYPE_TEXTURE = 0x4F544558
M32 = 0xFFFFFFFF

PACK_RE = re.compile(
    r"^(?P<rom>.+?)#(?P<crc>[0-9A-Fa-f]{8})#(?P<fmt>[0-9A-Fa-f])#(?P<siz>[0-9A-Fa-f])"
    r"(?:#(?P<palcrc>[0-9A-Fa-f]{8}))?_(?P<kind>[A-Za-z]+)\.png$"
)

# Fast::TextureType -> (n64 fmt, siz), as in pack-crc-match.py.
TEXTYPE = {1: (0, 3), 2: (0, 2), 3: (2, 0), 4: (2, 1), 5: (4, 0),
           6: (4, 1), 7: (3, 0), 8: (3, 1), 9: (3, 2)}

SPRITE_DIR = "assets/sprite/"
MASK_LETTERS = SPRITE_DIR + "ASSET_6EC_BOLD_FONT_LETTERS_ALPHAMASK"
MASK_NUMBERS = SPRITE_DIR + "ASSET_6ED_BOLD_FONT_NUMBERS_ALPHAMASK"
DIALOG_FONT = SPRITE_DIR + "ASSET_6EB_DIALOG_FONT_ALPHAMASK"

# print.c boldFontLetters, verbatim. Numbers font (6ED) serves indices 0-9;
# letters font (6EC) serves index-10 onward (print_getBoldFontLetterSprite).
BOLD_FONT_LETTERS = (
    list("0123456789") + [":"] + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    + ["@", "%", "?", "(", ")", "<", ">", '"', ".", ";", "-", "!", "/", "'"]
)

# Pack folder taxonomy -> bold-font glyph. Latin/<X> and Numerals/<D> map by
# their own names; punctuation folder names map here. '@' is BK's copyright
# glyph and '%' its trademark glyph — both assignments are cross-checked by
# the mask classifier below, never trusted blind.
PUNCT_FOLDER_GLYPH = {
    "Apostrophe": "'", "Bracket (Closing)": ")", "Bracket (Opening)": "(",
    "Colon": ":", "Copyright": "@", "Dot": ".", "Exclamation Mark": "!",
    "Greater Than": ">", "Minus": "-", "Question Mark": "?",
    "Quotation Marks": '"', "Semicolon": ";", "Slash": "/",
    "Smaller Than": "<", "Trademark": "%",
}

# Dialog font charset: print.c:590 gates letters to
#   '\x21' <= c < '\x21' + glyphCount
# so chunk i renders character 0x21+i. Chunks past 'Z' (>= 58) hold the PAL
# accent set (AltDialogFont.cpp: "the extra 13 are the PAL accents").
DIALOG_FIRST_CHAR = 0x21
DIALOG_CATEGORY_CHUNKS = {
    # folder category -> predicate over chunk index
    "Numerals": lambda i: 0x30 <= DIALOG_FIRST_CHAR + i <= 0x39,
    "Latin": lambda i: 0x41 <= DIALOG_FIRST_CHAR + i <= 0x5A,
    "Punctuation": lambda i: DIALOG_FIRST_CHAR + i < 0x41
    and not 0x30 <= DIALOG_FIRST_CHAR + i <= 0x39,
    "Special": lambda i: DIALOG_FIRST_CHAR + i > 0x5A,
}
# In-game/Europe nests accents under Special/{French,German}; In-game/USA has
# German directly. Both are the post-'Z' accent chunk range.
DIALOG_CATEGORY_ALIAS = {"French": "Special", "German": "Special"}

# Classification gates, calibrated on the exact-crc anchor sets (see
# docs/FONT-MAPPING.md): anchors validated 22/22 folders (bold) and 17/17
# files (dialog, post-bijection); the dialog gate excludes anything whose
# post-assignment margin is below this.
DIALOG_MARGIN_GATE = 3.0


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg):
    log(f"FATAL: {msg}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Rice CRC (GLideN64 TxUtil.cpp non-ASM path; big-endian reads per M-006) with
# sub-tile support: width may be narrower than the row stride, which is how
# GLideN64 saw variable-width dialog glyphs drawn out of fixed-width cells.
# ---------------------------------------------------------------------------
def rice_crc32(buf, width_bytes, height, row_stride):
    if width_bytes < 4 or height < 1:
        return None  # GLideN64's do-while would read out of bounds; skip.
    crc = 0
    base = 0
    y = height - 1
    while True:
        esi = 0
        x = width_bytes - 4
        while True:
            off = base + x
            if off + 4 > len(buf):
                return None
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


def read_texture(raw):
    """LUS binary Texture resource -> (textype, w, h, texel bytes) or None."""
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


# ---------------------------------------------------------------------------
# The bold-font composite, byte-exact to print.c print_applyTextureToBoldFontLetter
# / print_setBoldFontTexturePixel (which is v1.0 decomp code, i.e. exactly what
# ran on every retail ROM):
#   sphere sampled RGBA5551 big-endian, clamped, centred on the mask;
#   s = intensity(B byte of mask px) / 0x1F  (integer divide -> 0..8);
#   out = (r5*s, g5*s, b5*s, mask alpha), RGBA32 in N64 byte order.
# ---------------------------------------------------------------------------
def composite(mask, sphere):
    _, mw, mh, mdata = mask
    _, tw, th, tdata = sphere
    x_min = (tw - mw) >> 1
    y_min = (th - mh) >> 1
    out = bytearray(mw * mh * 4)
    oi = 0
    for y in range(y_min, mh + y_min):
        for x in range(x_min, mw + x_min):
            cx = min(max(0, x), tw - 1)
            cy = min(max(0, y), th - 1)
            o2 = (cx + cy * tw) * 2
            pixel = (tdata[o2] << 8) | tdata[o2 + 1]
            r5 = (pixel >> 11) & 0x1F
            g5 = (pixel >> 6) & 0x1F
            b5 = (pixel >> 1) & 0x1F
            i8 = mdata[oi * 4 + 2]
            a8 = mdata[oi * 4 + 3]
            s = i8 // 0x1F
            out[oi * 4 + 0] = (r5 * s) & 0xFF
            out[oi * 4 + 1] = (g5 * s) & 0xFF
            out[oi * 4 + 2] = (b5 * s) & 0xFF
            out[oi * 4 + 3] = a8
            oi += 1
    return bytes(out)


def variant_path(mask_entry, sphere_entry):
    """AltBoldFont.cpp variantPath(), byte-identical:
    assets/boldfont/<mask chunk basename>_<sphere hex id>."""
    mask_base = mask_entry.rsplit("/", 1)[-1]
    sphere_base = sphere_entry.rsplit("/", 1)[-1]
    m = re.match(r"ASSET_([0-9A-Fa-f]+)_", sphere_base)
    if not m:
        die(f"sphere entry {sphere_entry} has no ASSET_<hex>_ prefix")
    return f"assets/boldfont/{mask_base}_{m.group(1)}"


def load_rgba(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)


def pixel_key(arr):
    return hashlib.sha1(arr.tobytes()).hexdigest()


def box_downscale(arr, k):
    h, w = arr.shape[0] // k, arr.shape[1] // k
    return arr.astype(np.float64).reshape(h, k, w, k, 4).mean(axis=(1, 3))


def masked_mae(ds, ref):
    """Alpha-weighted mean abs error between two float RGBA arrays."""
    wgt = np.maximum(ds[..., 3], ref[..., 3]) / 255.0
    tot = wgt.sum()
    if tot == 0:
        return None
    diff = np.abs(ds[..., :3] - ref[..., :3]).mean(axis=-1)
    return float((diff * wgt).sum() / tot)


def ia8_to_rgba(data, w, h):
    a = np.frombuffer(data[: w * h], dtype=np.uint8).reshape(h, w)
    i4 = (a >> 4).astype(np.float64) * 17.0
    a4 = (a & 0xF).astype(np.float64) * 17.0
    return np.stack([i4, i4, i4, a4], axis=-1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--pack", required=True, type=Path,
                    help="pack source root (the dir containing */UI/Fonts)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--mod-name", default="BK-Reloaded-Lighthouse-Fonts")
    ap.add_argument("--mod-version", default="0.2.0")
    args = ap.parse_args()

    z = zipfile.ZipFile(args.archive)
    names = z.namelist()

    # ---- pack census ------------------------------------------------------
    font_files = []
    for p in args.pack.rglob("*.png"):
        rel = p.as_posix()
        if "/UI/Fonts/" not in rel:
            continue
        m = PACK_RE.match(p.name)
        if not m:
            die(f"unparseable pack filename {p}")
        g = m.groupdict()
        font_files.append((p, (int(g["crc"], 16), int(g["fmt"], 16), int(g["siz"], 16))))
    if not font_files:
        die(f"no UI/Fonts PNGs under {args.pack}")
    fonts_root = None
    for p, _ in font_files:
        parts = p.as_posix().split("/UI/Fonts/")
        r = Path(parts[0] + "/UI/Fonts")
        if fonts_root is None:
            fonts_root = r
        elif fonts_root != r:
            die("multiple UI/Fonts roots under --pack")
    pack_by_key = {}
    for p, key in font_files:
        if key in pack_by_key:
            die(f"duplicate pack CRC key {key}: {p} vs {pack_by_key[key]}")
        pack_by_key[key] = p
    log(f"UI/Fonts files: {len(font_files)}")

    def rel(p):
        return p.relative_to(fonts_root).as_posix()

    # ---- archive: masks, spheres, dialog chunks, all textures --------------
    all_tex = {}
    for n in names:
        if n.endswith("_TLUT"):
            continue
        r = read_texture(z.read(n))
        if r:
            all_tex[n] = r

    def chunk_entries(prefix):
        pat = re.compile(re.escape(prefix) + r"_0_(\d+)$")
        found = {}
        for n in all_tex:
            m = pat.match(n)
            if m:
                found[int(m.group(1))] = n
        if sorted(found) != list(range(len(found))):
            die(f"non-contiguous chunk indices for {prefix}: {sorted(found)}")
        return [found[i] for i in range(len(found))]

    letters = chunk_entries(MASK_LETTERS)
    numbers = chunk_entries(MASK_NUMBERS)
    dialog = chunk_entries(DIALOG_FONT)
    if len(letters) != len(BOLD_FONT_LETTERS) - 10:
        die(f"letters mask has {len(letters)} chunks, expected {len(BOLD_FONT_LETTERS) - 10}")
    if len(numbers) != 10:
        die(f"numbers mask has {len(numbers)} chunks, expected 10")
    log(f"bold masks: {len(letters)} letters + {len(numbers)} numbers; "
        f"dialog chunks: {len(dialog)}")
    for n in letters + numbers:
        if all_tex[n][0] != 1:
            die(f"bold mask chunk {n} is not RGBA32")
    for n in dialog:
        t, w, h, _ = all_tex[n]
        if t != 8:
            die(f"dialog chunk {n} is not IA8")

    def glyph_mask_entry(ch):
        idx = BOLD_FONT_LETTERS.index(ch)
        return numbers[idx] if idx < 10 else letters[idx - 10]

    # sphere candidates: every frame-0 chunk-0 RGBA16 texture (the game always
    # composites with sprite_getFramePtr(textureSprite, 0) + 1).
    sphere_candidates = {n: v for n, v in all_tex.items()
                         if n.endswith("_0_0") and v[0] == 2}
    log(f"sphere candidates (RGBA16 _0_0): {len(sphere_candidates)}")

    mapping = {}      # pack file -> dict(path=..., van=(w,h), tier=..., detail=...)
    unmapped = {}     # pack file -> reason
    covered = {}      # pack file -> stored-asset entry already handled by pack-crc-match

    # =======================================================================
    # PASS 1 (exact): stored standalone textures. These are the files the main
    # archive already carries (M-007's 36): model textures (PRESS START / NO
    # CONTROLLER / NOTE DOOR overlays) and full-cell dialog chunks.
    # =======================================================================
    for n, (ttype, w, h, data) in all_tex.items():
        if ttype not in TEXTYPE:
            continue
        fmt, siz = TEXTYPE[ttype]
        crc = rice_crc32(data, (w << siz) >> 1, h, (w << siz) >> 1)
        if crc is None:
            continue
        key = (crc, fmt, siz)
        if key in pack_by_key:
            covered[pack_by_key[key]] = n
    log(f"pass 1 — covered by the main pack-crc-match archive: {len(covered)}")

    # =======================================================================
    # PASS 2 (exact): bold-font composite CRCs.
    # =======================================================================
    comps = {}        # (mask_entry, sphere_entry) -> composite bytes
    comp_anchor = {}  # pack file -> (mask_entry, sphere_entry)
    for sn, sv in sphere_candidates.items():
        for mn in letters + numbers:
            mv = all_tex[mn]
            c = composite(mv, sv)
            comps[(mn, sn)] = c
            crc = rice_crc32(c, mv[1] * 4, mv[2], mv[1] * 4)
            if crc is None:
                continue
            key = (crc, 0, 3)
            if key in pack_by_key:
                p = pack_by_key[key]
                if p in comp_anchor and comp_anchor[p] != (mn, sn):
                    die(f"composite CRC collision for {p}")
                comp_anchor[p] = (mn, sn)
    anchor_spheres = sorted({sn for _, sn in comp_anchor.values()})
    log(f"pass 2 — composite CRC anchors: {len(comp_anchor)} "
        f"across {len(anchor_spheres)} spheres")
    # (arrays are loaded a few lines below; anchor mappings pick their art up
    # in the same loop that fills `arrays`)

    # =======================================================================
    # PASS 3 (exact): dialog-font sub-tile CRCs (glyph width, cell stride).
    # =======================================================================
    dialog_anchor = {}  # pack file -> chunk entry
    for n in dialog:
        _, w, h, data = all_tex[n]
        for wg in range(4, w + 1):
            crc = rice_crc32(data, wg, h, w)
            if crc is None:
                continue
            key = (crc, 3, 1)
            if key in pack_by_key:
                p = pack_by_key[key]
                if p in dialog_anchor and dialog_anchor[p] != n:
                    die(f"dialog sub-tile CRC collision for {p}")
                dialog_anchor[p] = n
    log(f"pass 3 — dialog sub-tile CRC anchors: {len(dialog_anchor)}")

    # =======================================================================
    # Art identity: the pack exports one drawing under several regions'
    # hashes. Group files by pixel content; anchors propagate to twins.
    # =======================================================================
    arrays = {}
    art_groups = collections.defaultdict(list)
    for p, _ in font_files:
        arr = load_rgba(p)
        arrays[p] = arr
        art_groups[pixel_key(arr)].append(p)
    for p, (mn, sn) in comp_anchor.items():
        mv = all_tex[mn]
        mapping[p] = dict(path=variant_path(mn, sn), van=(mv[1], mv[2]),
                          tier="exact-crc", detail="composite", arr=arrays[p])

    # =======================================================================
    # Region-twin propagation, round 1: the pack exports one drawing under
    # several regions' hashes, so a pixel-identical twin of a CRC-anchored
    # file inherits its mapping as proof-by-identity, BEFORE any classifier
    # runs. (Round 2 after the derived passes picks up stragglers.)
    # =======================================================================
    def propagate_twins():
        n = 0
        for key, group in art_groups.items():
            tgt = [p for p in group if p in mapping]
            if not tgt:
                continue
            proto = mapping[tgt[0]]
            for p in tgt[1:]:
                if mapping[p]["path"] != proto["path"]:
                    die(f"pixel-identical files map to different paths: {group}")
            for p in group:
                if p not in mapping and p not in covered:
                    mapping[p] = dict(path=proto["path"], van=proto["van"],
                                      tier="region-twin",
                                      detail=f"art of {rel(tgt[0])}",
                                      arr=proto["arr"])
                    n += 1
        return n
    twins = propagate_twins()
    log(f"region-twin propagation (round 1): {twins}")

    # =======================================================================
    # PASS 4: Textured/ — folder-derived glyph + per-folder sphere assignment.
    # =======================================================================
    tex_root = fonts_root / "Textured"
    tex_folders = collections.defaultdict(list)
    for p, _ in font_files:
        if tex_root in p.parents:
            tex_folders[p.parent].append(p)

    def folder_glyph(folder):
        cat, leaf = folder.parts[-2], folder.parts[-1]
        if cat == "Latin":
            return leaf
        if cat == "Numerals":
            return leaf
        if cat == "Punctuation":
            if leaf not in PUNCT_FOLDER_GLYPH:
                die(f"unknown punctuation folder {folder}")
            return PUNCT_FOLDER_GLYPH[leaf]
        die(f"unrecognised Textured folder layout {folder}")

    def mask_last_col_blank(mask_entry):
        t, mw, mh, data = all_tex[mask_entry]
        a = np.frombuffer(data[: mw * mh * 4], dtype=np.uint8).reshape(mh, mw, 4)
        return int(a[:, -1, 3].sum()) == 0 and int(a[:, -1, 2].sum()) == 0

    def prepare_art(arr, mask_entry):
        """Fit pack art onto the v1.1 mask cell.

        BK-Reloaded's bold-font art is an 8x upscale of the US v1.0 / PAL
        cells. v1.1 padded every odd-width glyph to even width with one blank
        RIGHT column (verified: alpha and intensity of the last column are 0
        for all 28 affected chunks), so one-column-short art is right-padded
        with transparent pixels — byte-faithful, not a resample. Anything
        else that does not fit exactly is rejected (None)."""
        t, mw, mh, _ = all_tex[mask_entry]
        if arr.shape[0] % mh:
            return None
        k = arr.shape[0] // mh
        if k < 1:
            return None
        if arr.shape[1] == mw * k:
            return arr
        if arr.shape[1] == (mw - 1) * k and mask_last_col_blank(mask_entry):
            pad = np.zeros((arr.shape[0], k, 4), dtype=np.uint8)
            return np.concatenate([arr, pad], axis=1)
        return None

    def classify_scores(arr, mask_entry, sphere_entries):
        mv = all_tex[mask_entry]
        mw, mh = mv[1], mv[2]
        fit = prepare_art(arr, mask_entry)
        if fit is None:
            return None
        k = fit.shape[0] // mh
        ds = box_downscale(fit, k)
        out = {}
        for sn in sphere_entries:
            ref = np.frombuffer(comps[(mask_entry, sn)], dtype=np.uint8) \
                .reshape(mh, mw, 4).astype(np.float64)
            s = masked_mae(ds, ref)
            if s is not None:
                out[sn] = s
        return out

    def best_permutation(files, mask_entry, sphere_entries):
        import itertools
        S = []
        for f in files:
            sc = classify_scores(arrays[f], mask_entry, sphere_entries)
            if sc is None or len(sc) != len(sphere_entries):
                return None, None, None
            S.append([sc[sn] for sn in sphere_entries])
        best = second = None
        bperm = None
        for perm in itertools.permutations(range(len(sphere_entries))):
            cost = sum(S[i][perm[i]] for i in range(len(files)))
            if best is None or cost < best:
                second, best, bperm = best, cost, perm
            elif second is None or cost < second:
                second = cost
        return bperm, best, (second - best if second is not None else None)

    # Validation gate: every anchored folder's assignment must reproduce its
    # CRC-proven sphere labels exactly, and the anchored files' folder glyph
    # must equal their CRC-proven mask.
    n_anch_folders = 0
    for folder, files in sorted(tex_folders.items()):
        if len(files) != len(anchor_spheres):
            die(f"{folder} has {len(files)} files, expected {len(anchor_spheres)}")
        anchored = {p: comp_anchor[p] for p in files if p in comp_anchor}
        if not anchored:
            continue
        n_anch_folders += 1
        gm = glyph_mask_entry(folder_glyph(folder))
        for p, (mn, sn) in anchored.items():
            if mn != gm:
                die(f"folder taxonomy contradicts CRC anchor: {p} folder says "
                    f"{gm}, CRC says {mn}")
        perm, cost, gap = best_permutation(sorted(files), gm, anchor_spheres)
        if perm is None:
            die(f"anchored folder {folder} failed to score")
        for i, f in enumerate(sorted(files)):
            if f in anchored and anchor_spheres[perm[i]] != anchored[f][1]:
                die(f"sphere assignment validation FAILED in {folder} for {f}")
    log(f"pass 4 — validation: {n_anch_folders} anchored folders reproduce "
        f"their CRC labels under assignment; folder taxonomy agrees with all "
        f"anchors")

    tex_derived = 0
    folder_gaps = {}
    for folder, files in sorted(tex_folders.items()):
        if any(p in mapping for p in files):
            # anchored or twin-of-anchored folders: assert ALL files carry a
            # mapping, else something is inconsistent — be loud, not partial.
            if not all(p in mapping for p in files):
                die(f"folder {folder} is partially anchored — investigate")
            continue
        gm = glyph_mask_entry(folder_glyph(folder))
        perm, cost, gap = best_permutation(sorted(files), gm, anchor_spheres)
        if perm is None:
            for p in files:
                unmapped[p] = "Textured: dims not an integer multiple of the v1.1 mask"
            continue
        folder_gaps[rel(folder / "_")[:-2]] = round(gap, 2)
        mv = all_tex[gm]
        for i, f in enumerate(sorted(files)):
            sn = anchor_spheres[perm[i]]
            # cross-check: joint mask classification must agree with taxonomy
            best_mask = None
            best_s = None
            for cand in (letters + numbers):
                cv = all_tex[cand]
                if (cv[1], cv[2]) != (mv[1], mv[2]):
                    # different dims cannot even be scored against this art;
                    # only same-cell masks compete
                    pass
                sc = classify_scores(arrays[f], cand, [sn])
                if sc and (best_s is None or sc[sn] < best_s):
                    best_s, best_mask = sc[sn], cand
            if best_mask != gm:
                unmapped[f] = (f"Textured: mask cross-check disagrees with folder "
                               f"({best_mask} vs {gm})")
                continue
            mapping[f] = dict(path=variant_path(gm, sn), van=(mv[1], mv[2]),
                              tier="derived", detail=f"folder+assignment gap={gap:.2f}",
                              arr=prepare_art(arrays[f], gm))
            tex_derived += 1
    log(f"pass 4 — Textured derived mappings: {tex_derived} "
        f"(per-folder assignment gaps min "
        f"{min(folder_gaps.values()) if folder_gaps else 0:.2f})")

    twins2 = propagate_twins()
    log(f"region-twin propagation (round 2): {twins2}")

    # =======================================================================
    # PASS 5: In-game/ — dialog glyph classification with bijective category
    # constraints, anchors as validation.
    # =======================================================================
    ingame_root = fonts_root / "In-game"
    ingame = [p for p, _ in font_files if ingame_root in p.parents]

    def dialog_category(p):
        parts = p.relative_to(ingame_root).parts
        # <Region>/<Category>[/<Sub>]/file
        cat = parts[1]
        cat = DIALOG_CATEGORY_ALIAS.get(cat, cat)
        if cat not in DIALOG_CATEGORY_CHUNKS:
            die(f"unknown In-game category for {p}")
        return cat

    def dialog_cohort(p):
        h = arrays[p].shape[0]
        # 13-row art is PAL/v1.1-shaped; 12-row art is US v1.0-shaped.
        for rows in (13, 12):
            if h % rows == 0:
                k = h // rows
                if arrays[p].shape[1] % k == 0:
                    return rows, k, arrays[p].shape[1] // k
        return None

    def dialog_score(p, chunk_entry, rows, k, wg):
        t, w, h, data = all_tex[chunk_entry]
        if w < wg:
            return None
        ds = box_downscale(arrays[p], k)
        ref = ia8_to_rgba(data, w, h)[:rows, :wg]
        wgt = np.maximum(ds[..., 3], ref[..., 3]) / 255.0
        tot = wgt.sum()
        if tot == 0:
            return None
        diff = (np.abs(ds[..., :3] - ref[..., :3]).mean(axis=-1)
                + np.abs(ds[..., 3] - ref[..., 3]))
        return float((diff * wgt).sum() / tot)

    def assign(files):
        """Greedy auction with per-file candidate lists restricted to the
        file's folder category. Returns file -> (chunk, score, margin)."""
        cand = {}
        for p in files:
            co = dialog_cohort(p)
            if co is None:
                cand[p] = {}
                continue
            rows, k, wg = co
            pred = DIALOG_CATEGORY_CHUNKS[dialog_category(p)]
            sc = {}
            for i, n in enumerate(dialog):
                if not pred(i):
                    continue
                s = dialog_score(p, n, rows, k, wg)
                if s is not None:
                    sc[n] = s
            cand[p] = sc
        taken = {}
        pos = {p: sorted(cand[p].items(), key=lambda kv: kv[1]) for p in files}
        pending = [p for p in files if pos[p]]
        while pending:
            nxt = []
            for p in pending:
                while pos[p] and pos[p][0][0] in taken and \
                        taken[pos[p][0][0]][1] <= pos[p][0][1]:
                    pos[p] = pos[p][1:]
                if not pos[p]:
                    continue
                n, s = pos[p][0]
                if n in taken:
                    loser = taken[n][0]
                    taken[n] = (p, s)
                    nxt.append(loser)
                    pos[loser] = [(cn, cs) for cn, cs in pos[loser] if cn != n]
                else:
                    taken[n] = (p, s)
            pending = nxt
        out = {}
        for n, (p, s) in taken.items():
            alts = [cs for cn, cs in cand[p].items()
                    if cn != n and (cn not in taken or taken[cn][1] > cs)]
            margin = (min(alts) - s) if alts else float("inf")
            out[p] = (n, s, margin)
        return out

    eur = [p for p in ingame if (dialog_cohort(p) or (13,))[0] == 13]
    usa = [p for p in ingame if p not in eur]
    eur_assign = assign(eur)

    # validation: the assignment must reproduce every CRC anchor
    for p, n_true in dialog_anchor.items():
        got = eur_assign.get(p, (None,))[0]
        if got != n_true:
            die(f"dialog assignment validation FAILED: {rel(p)} -> "
                f"{got}, CRC says {n_true}")
    log(f"pass 5 — dialog assignment reproduces all {len(dialog_anchor)} "
        f"CRC anchors")

    def dialog_pad(p, chunk_entry, rows):
        """Right-pad dialog art to the full k-scaled cell width. The region
        past the drawn glyph width is never sampled (the game draws each glyph
        at its own width), and AltDialogFont derives the integer scale from
        alt->Width / nativeW, which requires full-cell width."""
        t, w, h, _ = all_tex[chunk_entry]
        arr = arrays[p]
        if arr.shape[0] % rows:
            return None
        k = arr.shape[0] // rows
        if arr.shape[1] > w * k or arr.shape[1] % k:
            return None
        if arr.shape[1] == w * k:
            return arr
        pad = np.zeros((arr.shape[0], w * k - arr.shape[1], 4), dtype=np.uint8)
        return np.concatenate([arr, pad], axis=1)

    dial_exact = dial_derived = 0
    low_margin = []
    for p in eur:
        if p in covered:
            continue  # full-cell CRC matches already shipped in the main o2r
        if p not in eur_assign:
            unmapped[p] = "In-game: no scoreable dialog chunk candidate"
            continue
        n, s, margin = eur_assign[p]
        t, w, h, _ = all_tex[n]
        fit = dialog_pad(p, n, 13)
        if fit is None:
            unmapped[p] = "In-game: art does not fit the assigned cell"
            continue
        if p in dialog_anchor:
            mapping[p] = dict(path=n, van=(w, h), tier="exact-crc",
                              detail="dialog sub-tile", arr=fit)
            dial_exact += 1
        elif margin >= DIALOG_MARGIN_GATE:
            mapping[p] = dict(path=n, van=(w, h), tier="derived",
                              detail=f"dialog assignment margin={margin:.1f}",
                              arr=fit)
            dial_derived += 1
        else:
            unmapped[p] = (f"In-game: assignment margin {margin:.1f} below "
                           f"gate {DIALOG_MARGIN_GATE}")
            low_margin.append(rel(p))
    log(f"pass 5 — dialog: {dial_exact} exact + {dial_derived} derived, "
        f"{len(low_margin)} below margin gate")

    # USA (v1.0-shaped, 12-row) files: classified only to prove they duplicate
    # Europe art for the same chunks; never emitted over a Europe mapping.
    usa_assign = assign([p for p in usa if p not in covered])
    usa_dup = usa_fill = 0
    # chunks already carrying Europe art — via THIS fragment or via the main
    # pack-crc-match archive (pass 1's full-cell dialog matches). A USA fill
    # at any of these would ship conflicting art for the same path across the
    # two mounted archives.
    eur_chunks = {v["path"] for v in mapping.values()
                  if v["path"].startswith(DIALOG_FONT)}
    eur_chunks |= {n for n in covered.values() if n.startswith(DIALOG_FONT)}
    for p in usa:
        if p in covered or p in mapping:
            continue
        if p not in usa_assign:
            unmapped[p] = "In-game USA: no scoreable candidate"
            continue
        n, s, margin = usa_assign[p]
        if margin < DIALOG_MARGIN_GATE:
            unmapped[p] = (f"In-game USA: margin {margin:.1f} below gate")
            continue
        if n in eur_chunks:
            unmapped[p] = (f"In-game USA: v1.0-shaped duplicate of {n.rsplit('/', 1)[-1]}"
                           f" (Europe art emitted instead)")
            usa_dup += 1
        else:
            t, w, h, _ = all_tex[n]
            fit = dialog_pad(p, n, 12)
            if fit is None:
                unmapped[p] = "In-game USA: art does not fit the assigned cell"
                continue
            # 12-row v1.0-shaped art at the v1.1 chunk path: HByteScale is the
            # true k, VPixelScale is k against the 12 v1.0 rows; AltDialogFont
            # re-derives and top-aligns at runtime (that is what it is FOR).
            mapping[p] = dict(path=n, van=(w, 12), tier="derived",
                              detail=f"dialog USA fill margin={margin:.1f}",
                              arr=fit)
            usa_fill += 1
    log(f"pass 5 — USA files: {usa_dup} superseded duplicates, {usa_fill} fills")

    # =======================================================================
    # Emit the o2r fragment.
    # =======================================================================
    # one entry per alt path; assert identical art where several files share it
    by_path = {}
    for p, info in mapping.items():
        if info["arr"] is None:
            die(f"mapping for {rel(p)} has no prepared art")
        e = by_path.setdefault(info["path"],
                               dict(files=[], van=info["van"], arr=info["arr"]))
        e["files"].append(p)
        if e["van"] != info["van"]:
            die(f"van dims disagree for {info['path']}")
        if e["arr"].shape != info["arr"].shape or \
                not np.array_equal(e["arr"], info["arr"]):
            die(f"different art mapped to one path {info['path']}")

    tmpdir = Path(tempfile.mkdtemp(prefix="pack-font-map-"))
    mods_toml = ('[mod]\n'
                 f'name = "{args.mod_name}"\n'
                 f'version = "{args.mod_version}"\n')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_STORED) as zo:
        zo.writestr("mods.toml", mods_toml)
        for path in sorted(by_path):
            e = by_path[path]
            van_w, van_h = e["van"]
            arr = e["arr"]
            k = arr.shape[0] // van_h
            if arr.shape[0] != van_h * k or arr.shape[1] != van_w * k:
                die(f"prepared art {arr.shape} is not an integer multiple of "
                    f"van {e['van']} for {path}")
            # encode via the shared encoder (which reads a PNG file); the
            # prepared art is written out losslessly first.
            src = tmpdir / (path.replace("/", "_") + ".png")
            Image.fromarray(arr).save(src)
            blob = encode_texture_resource(src, van_w, van_h)
            # self-check: the entry must round-trip through the same reader
            # the engine models (guards the XETO byte-order trap).
            rt = read_texture(blob)
            if not rt or rt[0] != 1 or rt[1] != arr.shape[1] or rt[2] != arr.shape[0]:
                die(f"encoded entry failed round-trip for {path}")
            zo.writestr(ALT_PREFIX + path, blob)
            n_written += 1
    sz = args.out.stat().st_size
    log(f"wrote {args.out}: {n_written} textures, {sz} bytes ({sz / 1048576:.1f} MB)")

    # ---- final accounting --------------------------------------------------
    tiers = collections.Counter(v["tier"] for v in mapping.values())
    total = len(font_files)
    log("\n==== accounting over all UI/Fonts files ====")
    log(f"  exact-crc        : {tiers['exact-crc']}")
    log(f"  region-twin      : {tiers['region-twin']}")
    log(f"  derived          : {tiers['derived']}")
    log(f"  covered-by-main  : {len(covered)}")
    log(f"  unmapped         : {len(unmapped)}")
    log(f"  total            : {tiers['exact-crc'] + tiers['region-twin'] + tiers['derived'] + len(covered) + len(unmapped)} / {total}")
    if tiers["exact-crc"] + tiers["region-twin"] + tiers["derived"] \
            + len(covered) + len(unmapped) != total:
        die("accounting does not sum to the pack census — refuse to report")

    reasons = collections.Counter(v.split(":")[0] + ":" + v.split(":")[1][:40]
                                  if ":" in v else v for v in unmapped.values())
    for r, c in sorted(reasons.items()):
        log(f"  unmapped[{c}]: {r}")

    if args.report:
        args.report.write_text(json.dumps({
            "pack_files": total,
            "tiers": dict(tiers),
            "covered_by_main_archive": {rel(p): n for p, n in sorted(covered.items())},
            "mapping": {rel(p): {"path": v["path"], "tier": v["tier"],
                                 "detail": v["detail"]}
                        for p, v in sorted(mapping.items())},
            "unmapped": {rel(p): r for p, r in sorted(unmapped.items())},
            "textured_folder_assignment_gaps": folder_gaps,
            "entries_written": n_written,
        }, indent=1))
        log(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
