#!/usr/bin/env python3
"""Oracle comparison for the width-aware SPAC at the MASTERS (E2i-c).

The amount is computed per master corner, so a font can agree with the
reference at the default of every other axis and still be wrong at every
master. This comparator gives the REAL transform (the studio's
WidthAwareSpacTransform) the same input font and the same master locations
the Rust injector got — `params.master_locations` of TRANSFORMS, which is
`BuildContext.master_locations` on the Python side — and compares:

  1. structure: the SPAC fvar axis, HVAR, untouched glyf bytes, and avar's
     raw axis count equal to fvar's;
  2. the SPAC gvar tuples, glyph by glyph: the same number, the same
     regions, the same deltas on the same points (every other point
     untouched), and the font's own tuples left as they were;
  3. each SPAC tuple's BYTES against fontTools' `TupleVariation.compile`
     of the reference's tuple (embedded peak, private point numbers);
  4. advances at EVERY master corner x SPAC {min, 0, max}, read both ways
     a renderer can: from the gvar phantom points and from HVAR;
  5. that the fixture can tell the rules apart: some glyph's SPAC change
     differs between two corners (a constant amount per glyph cannot pass).

With --trace it instead compares what the Rust injector MEASURED
(fontc_web::spac_widthaware_trace) with the reference's own values, as
exact floats: the normalized corners, the default inks and their mean, each
corner's inks, mean and advances, and the model's supports.

Usage: compare_spac_masters.py INPUT RUST_OUT TRANSFORMS.json
       compare_spac_masters.py INPUT --trace TRACE.json TRANSFORMS.json
"""

import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.models import VariationModel

from avar2_studio.transforms import builtin_spac_widthaware as wa
from avar2_studio.transforms.base import BuildContext

FAILURES = []


def ok(cond, label):
    print(f"  {'✓' if cond else '✗ FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def spac_entry(transforms_path):
    entries = json.loads(Path(transforms_path).read_text())
    (entry,) = [e for e in entries if e["type"] == "spac_widthaware" and e["enabled"]]
    params = dict(entry["params"])
    return params, params.pop("master_locations", None)


def run_oracle(input_path, params, master_locations, work_dir):
    """Copy the input into work_dir and apply the transform for real."""
    src = Path(work_dir) / "input.ttf"
    shutil.copy2(input_path, src)
    source = Path(work_dir) / "Fixture.glyphs"  # only its sidecars are read
    ctx = BuildContext(
        build_dir=Path(work_dir), source_path=source, glyphs_path=source,
        family="Fixture", log=lambda m: print(f"     [oracle] {m}"),
        master_locations=master_locations,
    )
    return wa.WidthAwareSpacTransform().apply(src, params, ctx)


def corners_of(font, master_locations):
    """The corners, exactly as apply() picks them."""
    if master_locations:
        return wa._normalize_corners(font, master_locations)
    return wa._parametric_corners(font)


# -- 1. structure ---------------------------------------------------------

def raw_table(path, tag):
    """The table's exact on-disk bytes."""
    font = TTFont(path)
    return font.reader[tag] if tag in font.reader else None


def check_structure(input_path, rust_path, params):
    font = TTFont(rust_path)
    axes = {a.axisTag: (a.minValue, a.defaultValue, a.maxValue) for a in font["fvar"].axes}
    want = (float(params["min"]), 0.0, float(params["max"]))
    ok(axes.get("SPAC") == want, f"SPAC fvar range {axes.get('SPAC')}")
    ok("HVAR" in font, "HVAR present (variation-driven advances)")
    ok(raw_table(input_path, "glyf") == raw_table(rust_path, "glyf"), "glyf bytes untouched")
    avar = raw_table(rust_path, "avar")
    if avar is not None:
        (count,) = struct.unpack(">H", avar[6:8])
        ok(count == len(font["fvar"].axes),
           f"avar's raw axis count {count} = fvar's {len(font['fvar'].axes)}")


# -- 2./3. the tuples ------------------------------------------------------

def spac_tuples(font, glyph):
    return [t for t in font["gvar"].variations[glyph] if "SPAC" in t.axes]


def describe(tuple_variation):
    return (sorted(tuple_variation.axes.items()), list(tuple_variation.coordinates))


def packed_points_len(data, pos):
    start = pos
    count = data[pos]
    pos += 1
    if count & 0x80:
        count = ((count & 0x7F) << 8) | data[pos]
        pos += 1
    seen = 0
    while seen < count:
        run = data[pos]
        pos += 1
        length = (run & 0x7F) + 1
        pos += length * (2 if run & 0x80 else 1)
        seen += length
    return pos - start


def raw_glyph_tuples(gvar, gid):
    """[(header bytes, data bytes)] of one glyph's tuples, from the raw table."""
    axis_count, = struct.unpack(">H", gvar[4:6])
    glyph_count, flags, data_start = struct.unpack(">HHL", gvar[12:20])
    if flags & 1:
        start, end = struct.unpack(">LL", gvar[20 + gid * 4: 28 + gid * 4])
    else:
        start, end = (2 * v for v in struct.unpack(">HH", gvar[20 + gid * 2: 24 + gid * 2]))
    glyph = gvar[data_start + start: data_start + end]
    if not glyph:
        return [], 0
    count_flags, data_pos = struct.unpack(">HH", glyph[:4])
    if count_flags & 0x8000:  # shared point numbers lead the data
        data_pos += packed_points_len(glyph, data_pos)
    pos, out = 4, []
    for _ in range(count_flags & 0x0FFF):
        size, tuple_flags = struct.unpack(">HH", glyph[pos: pos + 4])
        length = 4
        if tuple_flags & 0x8000:
            length += 2 * axis_count
        if tuple_flags & 0x4000:
            length += 4 * axis_count
        out.append((glyph[pos: pos + length], glyph[data_pos: data_pos + size]))
        pos += length
        data_pos += size
    return out, len(glyph)


def check_tuples(input_path, rust_path, oracle_path):
    original, rust, oracle = TTFont(input_path), TTFont(rust_path), TTFont(oracle_path)
    order = oracle.getGlyphOrder()
    axis_tags = [a.axisTag for a in oracle["fvar"].axes]
    rust_gvar, oracle_gvar = raw_table(rust_path, "gvar"), raw_table(oracle_path, "gvar")
    count_diffs, tuple_diffs, byte_diffs, disturbed = [], [], [], []
    total = rust_bytes = oracle_bytes = 0
    for gid, glyph in enumerate(order):
        mine, theirs = spac_tuples(rust, glyph), spac_tuples(oracle, glyph)
        total += len(theirs)
        if len(mine) != len(theirs):
            count_diffs.append((glyph, len(mine), len(theirs)))
            continue
        for index, (a, b) in enumerate(zip(mine, theirs)):
            if describe(a) != describe(b):
                tuple_diffs.append((glyph, index, describe(a), describe(b)))
        kept = [t for t in rust["gvar"].variations[glyph] if "SPAC" not in t.axes]
        if [describe(t) for t in kept] != [describe(t) for t in original["gvar"].variations[glyph]]:
            disturbed.append(glyph)
        raw, size = raw_glyph_tuples(rust_gvar, gid)
        rust_bytes += size
        oracle_bytes += raw_glyph_tuples(oracle_gvar, gid)[1]
        for index, (reference, written) in enumerate(zip(theirs, raw[len(raw) - len(theirs):])):
            if tuple(bytes(b) for b in reference.compile(axis_tags)) != written:
                byte_diffs.append((glyph, index))
    ok(not count_diffs, f"SPAC tuple count per glyph matches the reference "
                        f"({len(count_diffs)} differ: {count_diffs[:5]})")
    ok(not tuple_diffs, f"SPAC tuples match the reference: regions, deltas, touched points "
                        f"({len(tuple_diffs)} differ: {tuple_diffs[:2]})")
    ok(not disturbed, f"the font's own tuples are unchanged ({len(disturbed)} differ: {disturbed[:5]})")
    ok(not byte_diffs, f"each SPAC tuple's bytes = fontTools' compile of the reference's "
                       f"({len(byte_diffs)} differ: {byte_diffs[:5]})")
    ok(total > 0, f"the reference wrote SPAC tuples ({total} over {len(order)} glyphs)")
    print(f"     gvar glyph data: Rust {rust_bytes} B (tuples spliced in), "
          f"reference {oracle_bytes} B (fontTools recompiles the table)")


# -- 4./5. advances at the masters ------------------------------------------

def widths(font, location):
    glyph_set = font.getGlyphSet(location=location, normalized=True)
    return [glyph_set[name].width for name in font.getGlyphOrder()]


def check_advances(input_path, rust_path, oracle_path, corners):
    def both(path):
        """(read through HVAR, read from the gvar phantom points)"""
        phantom = TTFont(path)
        if "HVAR" in phantom:
            del phantom["HVAR"]
        return TTFont(path), phantom

    original, rust, oracle = both(input_path), both(rust_path), both(oracle_path)
    order = rust[0].getGlyphOrder()
    worst_hvar, changes = 0.0, {}
    for corner in corners:
        for spac in (-1.0, 0.0, 1.0):
            location = {**corner, "SPAC": spac} if spac else dict(corner)
            label = f"corner {sorted(corner.items())} SPAC {spac:+.0f}"
            mine, theirs = widths(rust[1], location), widths(oracle[1], location)
            diffs = {g: (a, b) for g, a, b in zip(order, mine, theirs) if a != b}
            ok(not diffs, f"{label}: advances (gvar) match the reference "
                          f"({len(diffs)} differ: {dict(list(diffs.items())[:4])})")
            hvar = [abs(a - b) for a, b in zip(widths(rust[0], location), widths(oracle[0], location))]
            worst_hvar = max(worst_hvar, *hvar)
            if spac:
                changes[(tuple(sorted(corner.items())), spac)] = [
                    a - b for a, b in zip(mine, widths(rust[1], dict(corner)))]
            else:
                ok(mine == widths(original[1], dict(corner)),
                   f"{label}: advances are the input font's")
    ok(worst_hvar < 1e-6, f"advances read through HVAR match the reference at every corner "
                          f"(worst {worst_hvar:g})")
    varies = sorted({order[i] for (_, spac), row in changes.items() for i, v in enumerate(row)
                     if any(other[i] != v for (_, s), other in changes.items() if s == spac)})
    ok(len(corners) > 2 and bool(varies),
       f"the amounts differ between corners ({len(corners)} corners; "
       f"{len(varies)} glyphs vary, e.g. {varies[:5]})")


# -- --trace ---------------------------------------------------------------

def reference_trace(input_path, master_locations):
    """The reference's own helpers, called the way apply() calls them."""
    font = TTFont(input_path)
    order = font.getGlyphOrder()
    corners = corners_of(font, master_locations)
    glyph_set = font.getGlyphSet()
    inks = {name: ink for name in order
            if name != ".notdef" and (ink := wa._ink_width(glyph_set, name)) > 0}
    corner_inks, corner_means, corner_advances = [], [], []
    for corner in corners:
        at = wa._ink_widths_at(font, corner)
        adv_set = (font.getGlyphSet(location=corner, normalized=True)
                   if corner else font.getGlyphSet())
        corner_advances.append([float(adv_set[name].width) for name in order])
        shared = [name for name in inks if name in at]
        corner_inks.append([at.get(name, 0.0) for name in order])
        corner_means.append(sum(at[name] for name in shared) / len(shared))
    model = VariationModel(corners, axisOrder=sorted({t for c in corners for t in c}))
    return {
        "corners": [[[tag, value] for tag, value in corner.items()] for corner in corners],
        "inks": [[order.index(name), ink] for name, ink in inks.items()],
        "mean_ink": sum(inks.values()) / len(inks),
        "corner_inks": corner_inks,
        "corner_means": corner_means,
        "corner_advances": corner_advances,
        "supports": [[[tag, *region] for tag, region in support.items()]
                     for support in model.supports],
    }


def check_trace(input_path, trace_path, master_locations):
    mine = json.loads(Path(trace_path).read_text())
    theirs = reference_trace(input_path, master_locations)
    order = TTFont(input_path).getGlyphOrder()
    for key in ("corners", "inks", "mean_ink", "corner_means", "supports"):
        ok(mine[key] == theirs[key], f"{key} equal the reference's, exactly"
           + ("" if mine[key] == theirs[key] else f" (Rust {mine[key]} vs {theirs[key]})"))
    for key in ("corner_inks", "corner_advances"):
        diffs = [(index, order[gid], a, b)
                 for index, (row, ref) in enumerate(zip(mine[key], theirs[key]))
                 for gid, (a, b) in enumerate(zip(row, ref)) if a != b]
        ok(len(mine[key]) == len(theirs[key]) and not diffs,
           f"{key} equal the reference's, exactly, for {len(order)} glyphs at "
           f"{len(theirs[key])} corners ({len(diffs)} differ: {diffs[:4]})")
    fractional = sum(1 for row in theirs["corner_inks"] for v in row if v != int(v))
    print(f"     {len(theirs['corners'])} corners; {fractional} fractional ink widths compared")


def main():
    args = sys.argv[1:]
    if "--trace" in args:
        input_path, _flag, trace_path, transforms = args
        _params, master_locations = spac_entry(transforms)
        print("width-aware SPAC: what the injector measured vs the reference")
        check_trace(input_path, trace_path, master_locations)
    else:
        input_path, rust_path, transforms = args
        params, master_locations = spac_entry(transforms)
        tmp = tempfile.mkdtemp(prefix="spac-masters-oracle-")
        print("width-aware SPAC vs WidthAwareSpacTransform, at the masters")
        oracle_path = run_oracle(input_path, params, master_locations, tmp)
        check_structure(input_path, rust_path, params)
        check_tuples(input_path, rust_path, oracle_path)
        check_advances(input_path, rust_path, oracle_path,
                       corners_of(TTFont(input_path), master_locations))
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("ALL SPAC MASTER CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
