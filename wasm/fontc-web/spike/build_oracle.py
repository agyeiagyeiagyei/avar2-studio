#!/usr/bin/env python3
"""Build the gftools oracle for the add_avar2 wasm test.

Mirrors the studio pipeline: parse the avar mappings CSV into
[{"in": {...}, "out": {...}}] and call gftools' gen_avar2_mapping.
Columns that are already fvar axes are 'out' (parametric) axes; the rest
are 'in' (user) axes. Empty cells are dropped.
"""
import csv
import sys

from fontTools.ttLib import TTFont
from gftools.scripts.gen_avar2 import gen_avar2_mapping


def main():
    font_path, csv_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    font = TTFont(font_path)
    fvar_tags = {a.axisTag for a in font["fvar"].axes}
    fh = open(csv_path, "r", encoding="utf-8-sig", newline="")
    rows = [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]
    fh.close()
    header = [c.strip() for c in rows[0]]
    # csv_io.normalize_in_axis_name: registered traditional axes map
    # onto lowercase registered tags; custom columns stay verbatim.
    REGISTERED = {"WGHT": "wght", "WDTH": "wdth", "OPSZ": "opsz",
                  "CONTRAST": "cntr", "CNTR": "cntr"}
    mapping = []
    for row in rows[1:]:
        in_loc, out_loc = {}, {}
        for i, tag in enumerate(header[1:], start=1):
            cell = row[i].strip() if i < len(row) else ""
            if not cell:
                continue
            if tag in fvar_tags and tag.upper() not in REGISTERED:
                out_loc[tag] = float(cell)
            else:
                in_loc[REGISTERED.get(tag.upper(), tag)] = float(cell)
        mapping.append({"in": in_loc, "out": out_loc})

    # fontTools' VariationModel drops zero-valued entries from every
    # input location and then raises "Locations must be unique" on
    # collisions. CrispyMini-avar.csv collides: an explicit `OPSZ=12`
    # cell and an empty cell both normalize to 0 (the new axes have
    # default == min, so value == column-min ⟺ normalized 0). Mirror
    # the Rust implementation: dedup on the zero-stripped input
    # location with dict-overwrite semantics (last row wins, keyed in
    # first-occurrence order).
    col_min = {}
    for m in mapping:
        for tag, v in m["in"].items():
            col_min[tag] = v if tag not in col_min else min(col_min[tag], v)
    deduped = {}
    for m in mapping:
        key = tuple(sorted((k, v) for k, v in m["in"].items() if v != col_min[k]))
        deduped[key] = m
    mapping = list(deduped.values())

    gen_avar2_mapping(font, mapping)
    font.save(out_path)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
