#!/usr/bin/env python3
"""Parity fixture + comparator for the SPAC JSON contract (T3.5).

The mappings CSV is parsed by the ONE reader (avar2_studio.csv_io); the
per-instance SPAC values ride the transforms JSON into the Rust injector
(`params.instance_spac`), which never opens the CSV. This script:

  1. mints the fixture: one fvar instance per CSV row name (at the axis
     defaults) on a copy of the spike font — including names with a
     comma, which the old Rust split-on-',' reader silently lost;
  2. prints {"overrides": {name: value}} from csv_io's parse (stdout);
  3. given the injector's output font, asserts every fvar instance's
     SPAC coordinate equals csv_io's parse (blank cells → the axis
     default 0.0), and that the parse is non-empty (no vacuous pass).

Usage: spac_csv_parity.py IN.ttf OUT.ttf CSV [INJECTOR_OUT.ttf]
"""

import json
import shutil
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._f_v_a_r import NamedInstance

from avar2_studio import csv_io


def spac_coords(path):
    font = TTFont(path)
    names = font["name"]
    coords = {
        names.getDebugName(i.subfamilyNameID): i.coordinates.get("SPAC")
        for i in font["fvar"].instances
    }
    font.close()
    return coords


def main():
    src, dst, csv_path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    rows, _fieldnames = csv_io.read_csv_mappings(csv_path)
    overrides = csv_io.read_csv_spac_overrides(csv_path)

    shutil.copy2(src, dst)
    font = TTFont(dst)
    fvar = font["fvar"]
    name_table = font["name"]
    defaults = {a.axisTag: a.defaultValue for a in fvar.axes}
    for inst_name in dict.fromkeys(r.get("Instance Name", "") for r in rows):
        if not inst_name:
            continue
        inst = NamedInstance()
        inst.subfamilyNameID = name_table.addMultilingualName({"en": inst_name})
        inst.postscriptNameID = 0xFFFF
        inst.coordinates = dict(defaults)
        fvar.instances.append(inst)
    font.save(dst)
    font.close()

    print(json.dumps({"overrides": overrides}))

    if len(sys.argv) > 4:
        pinned = spac_coords(sys.argv[4])
        expected = {name: overrides.get(name, 0.0) for name in pinned}
        if pinned != expected:
            diffs = {n: (pinned[n], expected[n]) for n in pinned if pinned[n] != expected[n]}
            print(f"PARITY FAIL: injector pinning != csv_io parse: {diffs}", file=sys.stderr)
            return 1
        if not overrides:
            print("PARITY FAIL: the CSV parsed to zero overrides — vacuous", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
