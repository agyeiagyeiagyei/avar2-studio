#!/usr/bin/env python3
"""Oracle comparator for the fixer transforms: run gftools'
fix_unhinted_font and fix_fvar_instances on the BEFORE font and diff
against our wasm output (AFTER). Compares gasp/prep bytes exactly and
fvar instances as (name, coordinates) pairs.
Usage: compare_fix.py BEFORE.ttf AFTER.ttf
"""

import sys

from fontTools.ttLib import TTFont
from gftools.fix import fix_fvar_instances, fix_unhinted_font


def table_bytes(font, tag):
    if tag not in font:
        return None
    return font.getTableData(font.reader, tag) if hasattr(font, "getTableData") else None


def instances(font):
    f = font["fvar"]
    return [
        (font["name"].getDebugName(i.subfamilyNameID), dict(i.coordinates))
        for i in f.instances
    ]


def main():
    before_path, after_path = sys.argv[1:3]
    oracle_font = TTFont(before_path)
    fix_unhinted_font(oracle_font)
    fix_fvar_instances(oracle_font)
    ours = TTFont(after_path)

    failures = []

    # gasp/prep bytes
    for tag in ("gasp", "prep"):
        if tag not in oracle_font:
            continue  # oracle skipped (e.g. fpgm present) — nothing to check
        if tag not in ours:
            failures.append(f"{tag} missing after fix_unhinted")
            continue
        o_bytes = oracle_font.getTableData(tag)
        a_bytes = ours.getTableData(tag)
        if o_bytes != a_bytes:
            failures.append(f"{tag} bytes differ: oracle {list(o_bytes)} vs ours {list(a_bytes)}")

    # fvar instances
    o_inst = instances(oracle_font)
    a_inst = instances(ours)
    if o_inst != a_inst:
        failures.append(f"fvar instances differ:\n  oracle {o_inst}\n  ours   {a_inst}")

    for f in failures:
        print("  ✗ FAIL", f)
    if failures:
        return 1
    print("FIX ORACLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
