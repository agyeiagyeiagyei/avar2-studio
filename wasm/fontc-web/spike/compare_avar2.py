#!/usr/bin/env python3
"""Structural comparison of two avar2 fonts (gftools oracle vs Rust output).

Byte equality is not expected (VarStore layout/optimization may differ);
semantic content of fvar + avar + HVAR + gvar must match.

Usage: compare_avar2.py ORACLE.ttf CANDIDATE.ttf
Exits non-zero on any mismatch.
"""
import io
import sys

from fontTools.ttLib import TTFont

failures = []


def check(label, cond, detail=""):
    status = "ok  " if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def region_key(region):
    return tuple(
        (round(t.StartCoord, 6), round(t.PeakCoord, 6), round(t.EndCoord, 6))
        for t in region.VarRegionAxis
    )


def avar_axis_deltas(font, axis_count):
    """Per fvar-axis index: {region_key: delta} of the axis's delta set.

    The VarIdxMap is normalized to full axis-count length first: per the
    OT spec, item indices >= mapCount use the last entry (write-fonts
    trims such trailing duplicates; fontTools writes them out).
    0xFFFFFFFF (no-variation) maps to the empty set.
    """
    avar = font["avar"]
    store = avar.table.VarStore
    regions = store.VarRegionList.Region
    mapping = list(avar.table.VarIdxMap.mapping)
    while len(mapping) < axis_count:
        mapping.append(mapping[-1])
    out = []
    for var_idx in mapping[:axis_count]:
        if var_idx == 0xFFFFFFFF:
            out.append({})
            continue
        outer, inner = var_idx >> 16, var_idx & 0xFFFF
        data = store.VarData[outer]
        row = data.Item[inner]
        expanded = {}
        for region_idx, delta in zip(data.VarRegionIndex, row):
            if delta != 0:
                expanded[region_key(regions[region_idx])] = delta
        out.append(expanded)
    return out


def main():
    oracle_path, rust_path = sys.argv[1], sys.argv[2]
    oracle = TTFont(oracle_path)
    rust = TTFont(rust_path)

    # fvar axes: identical tag/min/default/max/nameID, in order
    o_axes = [
        (a.axisTag, a.minValue, a.defaultValue, a.maxValue, a.axisNameID)
        for a in oracle["fvar"].axes
    ]
    r_axes = [
        (a.axisTag, a.minValue, a.defaultValue, a.maxValue, a.axisNameID)
        for a in rust["fvar"].axes
    ]
    check("fvar axes (tag/min/default/max/nameID, ordered)", o_axes == r_axes,
          f"\n  oracle={o_axes}\n  rust  ={r_axes}")

    # fvar instances
    o_inst = [dict(i.coordinates) for i in oracle["fvar"].instances]
    r_inst = [dict(i.coordinates) for i in rust["fvar"].instances]
    check("fvar instance coordinates", o_inst == r_inst,
          f"\n  oracle={o_inst}\n  rust  ={r_inst}")

    # name records: the two tables must carry the same strings. Compare
    # full (platform, encoding, lang, id, decoded) sets.
    def name_records(font):
        out = set()
        for n in font["name"].names:
            try:
                s = n.toUnicode()
            except Exception:  # noqa: BLE001
                s = repr(n.string)
            out.add((n.platformID, n.platEncID, n.langID, n.nameID, s))
        return out

    o_names, r_names = name_records(oracle), name_records(rust)
    check("name records identical (all platforms)",
          o_names == r_names,
          f"\n  only-oracle={sorted(o_names - r_names)}\n  only-rust  ={sorted(r_names - o_names)}")

    # avar exists, version 2
    check("avar present in both", "avar" in oracle and "avar" in rust)
    check("avar version 2.0 in both",
          oracle["avar"].majorVersion == 2 and rust["avar"].majorVersion == 2)

    axis_count = len(o_axes)

    def normalized_map(font):
        m = list(font["avar"].table.VarIdxMap.mapping)
        while len(m) < axis_count:
            m.append(m[-1])
        return m[:axis_count]

    check("avar VarIdxMap covers every fvar axis",
          len(normalized_map(oracle)) == axis_count
          and len(normalized_map(rust)) == axis_count)

    # avar segment maps: identity in both (oracle writes {-1,0,1}
    # segments; the Rust output writes empty maps — both are identity)
    def segments_are_identity(font):
        segs = font["avar"].segments
        for axis in font["fvar"].axes:
            for k, v in segs.get(axis.axisTag, {}).items():
                if k != v:
                    return False
        return True

    check("avar segment maps are identity in both",
          segments_are_identity(oracle) and segments_are_identity(rust))

    # per-axis delta sets, expanded over region tents
    o_deltas = avar_axis_deltas(oracle, axis_count)
    r_deltas = avar_axis_deltas(rust, axis_count)
    for i, tag in enumerate(a[0] for a in o_axes):
        check(f"avar delta set for axis {tag}", o_deltas[i] == r_deltas[i],
              f"\n  oracle={o_deltas[i]}\n  rust  ={r_deltas[i]}")

    # avar VarRegionList contents as sets
    o_regions = {region_key(r) for r in oracle["avar"].table.VarStore.VarRegionList.Region}
    r_regions = {region_key(r) for r in rust["avar"].table.VarStore.VarRegionList.Region}
    check("avar VarRegionList region sets equal", o_regions == r_regions,
          f"\n  only-oracle={o_regions - r_regions}\n  only-rust  ={r_regions - o_regions}")
    check("avar VarRegionList axis counts",
          oracle["avar"].table.VarStore.VarRegionList.RegionAxisCount == axis_count
          and rust["avar"].table.VarStore.VarRegionList.RegionAxisCount == axis_count)

    # HVAR: region axis count + padded tents
    if "HVAR" in oracle or "HVAR" in rust:
        check("HVAR present in both", "HVAR" in oracle and "HVAR" in rust)
        o_h = oracle["HVAR"].table.VarStore.VarRegionList
        r_h = rust["HVAR"].table.VarStore.VarRegionList
        check("HVAR RegionAxisCount == fvar axis count",
              o_h.RegionAxisCount == axis_count and r_h.RegionAxisCount == axis_count)
        check("HVAR region tents identical (ordered)",
              [region_key(r) for r in o_h.Region] == [region_key(r) for r in r_h.Region],
              f"\n  oracle={[region_key(r) for r in o_h.Region][:3]}..."
              f"\n  rust  ={[region_key(r) for r in r_h.Region][:3]}...")

    # GDEF/MVAR VarStores if present
    for tag in ("GDEF", "MVAR"):
        o_store = getattr(oracle[tag].table, "VarStore", None) if tag in oracle else None
        r_store = getattr(rust[tag].table, "VarStore", None) if tag in rust else None
        if o_store is None and r_store is None:
            continue
        check(f"{tag} VarStore present in both", o_store is not None and r_store is not None)
        check(f"{tag} RegionAxisCount + tents",
              o_store.VarRegionList.RegionAxisCount == axis_count
              and r_store.VarRegionList.RegionAxisCount == axis_count
              and [region_key(r) for r in o_store.VarRegionList.Region]
              == [region_key(r) for r in r_store.VarRegionList.Region])

    # gvar axisCount
    check("gvar axisCount == fvar axis count",
          oracle["gvar"].axisCount == axis_count and rust["gvar"].axisCount == axis_count)

    # gvar full semantic comparison: shared tuples (as sets — the oracle
    # re-collects them) and every glyph's tuple variations (axes + deltas)
    def shared_tuples(font):
        from fontTools.ttLib.tables.TupleVariation import decompileSharedTuples
        raw = font.getTableData("gvar")
        import struct
        count, off = struct.unpack(">H", raw[6:8])[0], struct.unpack(">I", raw[8:12])[0]
        tags = [a.axisTag for a in font["fvar"].axes]
        out = set()
        for t in decompileSharedTuples(tags, count, raw, off):
            out.add(tuple(sorted((k, round(v, 6)) for k, v in t.items())))
        return out

    o_shared, r_shared = shared_tuples(oracle), shared_tuples(rust)
    check("gvar shared tuples equal (as sets)", o_shared == r_shared,
          f"\n  only-oracle={sorted(o_shared - r_shared)[:4]}"
          f"\n  only-rust  ={sorted(r_shared - o_shared)[:4]}")

    def glyph_variations(font):
        out = {}
        for glyph, variations in font["gvar"].variations.items():
            tvs = []
            for v in variations:
                axes = tuple(sorted((k, tuple(round(x, 6) for x in vv))
                                    for k, vv in v.axes.items()))
                coords = tuple(v.coordinates)
                tvs.append((axes, coords))
            out[glyph] = sorted(tvs)
        return out

    o_gvar, r_gvar = glyph_variations(oracle), glyph_variations(rust)
    check("gvar tuple variations identical per glyph (axes + deltas)",
          o_gvar == r_gvar,
          "\n  glyphs differing: "
          + str([g for g in o_gvar if o_gvar.get(g) != r_gvar.get(g)][:6]))

    # both fonts re-save + reload cleanly (validity smoke test)
    for font, label in ((oracle, "oracle"), (rust, "rust")):
        try:
            buf = io.BytesIO()
            font.save(buf)
            TTFont(io.BytesIO(buf.getvalue()))
            check(f"{label} re-saves and reloads", True)
        except Exception as e:  # noqa: BLE001
            check(f"{label} re-saves and reloads", False, repr(e))

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
