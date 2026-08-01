#!/usr/bin/env python3
"""Structural comparison of a regen_stat candidate against the
axisregistry.build_stat oracle, for the single-font/no-siblings case.

The oracle is computed in-process: INPUT is loaded with fontTools and
`axisregistry.build_stat(ttFont, [])` run on it. CANDIDATE must match
the oracle's STAT structure exactly (byte equality is not expected):

  - STAT version and ElidedFallbackNameID
  - axis records (tag, nameID, ordering), in order
  - axis value records (axisIndex, format, flags, nameID,
    value/linked/nominal/range), in order
  - the full name table (STAT regen deletes and re-adds records)
  - fvar (untouched by the regen)

Usage: compare_stat.py INPUT.ttf CANDIDATE.ttf
Exits non-zero on any mismatch.
"""
import io
import sys

from fontTools.ttLib import TTFont
from axisregistry import build_stat

failures = []


def check(label, cond, detail=""):
    status = "ok  " if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def stat_struct(font):
    if "STAT" not in font:
        return None
    t = font["STAT"].table
    axes = [
        (a.AxisTag, a.AxisNameID, a.AxisOrdering)
        for a in (t.DesignAxisRecord.Axis if t.DesignAxisCount else [])
    ]
    values = []
    if t.AxisValueCount:
        for av in t.AxisValueArray.AxisValue:
            rec = (av.AxisIndex, av.Format, av.Flags, av.ValueNameID)
            if av.Format == 1:
                rec += (av.Value,)
            elif av.Format == 2:
                rec += (av.NominalValue, av.RangeMinValue, av.RangeMaxValue)
            elif av.Format == 3:
                rec += (av.Value, av.LinkedValue)
            elif av.Format == 4:
                rec += (tuple((r.AxisIndex, r.Value) for r in av.AxisValueRecord),)
            values.append(rec)
    return (t.Version, t.ElidedFallbackNameID, axes, values)


def name_records(font):
    out = set()
    for n in font["name"].names:
        try:
            s = n.toUnicode()
        except Exception:  # noqa: BLE001
            s = repr(n.string)
        out.add((n.platformID, n.platEncID, n.langID, n.nameID, s))
    return out


def fvar_struct(font):
    fvar = font["fvar"]
    axes = [
        (a.axisTag, a.minValue, a.defaultValue, a.maxValue, a.axisNameID)
        for a in fvar.axes
    ]
    inst = [dict(i.coordinates) for i in fvar.instances]
    return axes, inst


def main():
    input_path, candidate_path = sys.argv[1], sys.argv[2]
    oracle = TTFont(input_path)
    _, oracle_inst = fvar_struct(oracle)
    build_stat(oracle, [])
    candidate = TTFont(candidate_path)

    o_stat, c_stat = stat_struct(oracle), stat_struct(candidate)
    check("STAT present in candidate", c_stat is not None)
    if c_stat is not None:
        check("STAT version + ElidedFallbackNameID",
              o_stat[:2] == c_stat[:2],
              f"\n  oracle={o_stat[:2]}\n  rust  ={c_stat[:2]}")
        check("axis records (tag/nameID/ordering, ordered)",
              o_stat[2] == c_stat[2],
              f"\n  oracle={o_stat[2]}\n  rust  ={c_stat[2]}")
        check("axis value records (fmt/flags/nameID/values, ordered)",
              o_stat[3] == c_stat[3],
              f"\n  oracle={o_stat[3]}\n  rust  ={c_stat[3]}")

    o_names, c_names = name_records(oracle), name_records(candidate)
    check("name records identical (all platforms)",
          o_names == c_names,
          f"\n  only-oracle={sorted(o_names - c_names)}"
          f"\n  only-rust  ={sorted(c_names - o_names)}")

    o_axes, _ = fvar_struct(oracle)
    c_axes, c_inst = fvar_struct(candidate)
    check("fvar axes unchanged (tag/min/default/max/nameID)",
          o_axes == c_axes,
          f"\n  oracle={o_axes}\n  rust  ={c_axes}")
    check("fvar instance coordinates unchanged",
          oracle_inst == c_inst,
          f"\n  oracle={oracle_inst}\n  rust  ={c_inst}")

    # both fonts re-save + reload cleanly (validity smoke test)
    for font, label in ((oracle, "oracle"), (candidate, "rust")):
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
    print("all checks passed (0 structural diffs)")


if __name__ == "__main__":
    main()
