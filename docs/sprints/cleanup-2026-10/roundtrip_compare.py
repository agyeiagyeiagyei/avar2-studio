#!/usr/bin/env python3
"""Round-trip acceptance for E1d, E2h, E2i and E2j.

Compares a fontmake build of the exported sources with the studio's TTF of the
SAME copy, using HarfBuzz for the metrics (it applies both fonts' avar/avar2):

* at every NON-default CSV row, each glyph's advance and extents agree within
  3 u;
* at SPAC ``lo`` and ``hi`` at those rows, each glyph's advance change and both
  sidebearing changes agree within 1 u.

The default row is excluded by construction: the sources rest at the compiled
origin while the studio relocates the default (decision 0001), and its mapping
is deliberately absent from the designspace's ``<mappings>``.

Usage::

    python roundtrip_compare.py --studio Crispy[...].ttf \\
        --fm Crispy-VF.ttf --csv Crispy-avar.csv [--glyphs aognHeWi] [--json out]

Exit status is 0 only when every check is inside tolerance.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont

from avar2_studio.build import config_generator

ROW_TOLERANCE = 3.0
SPAC_TOLERANCE = 1.0


def hb_font(path: Path) -> hb.Font:
    blob = hb.Blob.from_file_path(str(path))
    return hb.Font(hb.Face(blob))


def _metrics(font: hb.Font, codepoint: int, location: dict):
    """(advance, left sidebearing, ink width) for one glyph at one location."""
    font.set_variations(location)
    gid = font.get_nominal_glyph(codepoint)
    advance = font.get_glyph_h_advance(gid)
    extents = font.get_glyph_extents(gid)
    if extents is None:
        return float(advance), 0.0, 0.0
    return float(advance), float(extents.x_bearing), float(extents.width)


def _axes(path: Path) -> dict:
    """``{tag: (min, default, max)}`` from the built fvar."""
    font = TTFont(str(path))
    try:
        return {a.axisTag: (a.minValue, a.defaultValue, a.maxValue)
                for a in font["fvar"].axes}
    finally:
        font.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--studio", type=Path, required=True)
    ap.add_argument("--fm", type=Path, required=True)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--glyphs", default="aognHeWiw")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    studio = hb_font(args.studio)
    built = hb_font(args.fm)
    studio_axes, built_axes = _axes(args.studio), _axes(args.fm)
    missing = [t for t in studio_axes if t not in built_axes]
    if missing:
        print(f"error: the fontmake build lacks axes {missing}", file=sys.stderr)
        return 2

    spac = "SPAC" if "SPAC" in studio_axes else None
    spac_ends = []
    if spac:
        spac_lo, _default, spac_hi = studio_axes[spac]
        spac_ends = [v for v in (spac_lo, spac_hi) if v]

    # The default row is skipped: the sources rest at the compiled origin.
    defaults = {tag.lower(): trip[1] for tag, trip in studio_axes.items()}
    rows = [r for r in config_generator.read_csv_mappings(args.csv)
            if any(abs(float(v) - defaults.get(str(t).lower(), float("nan"))) > 1e-6
                   for t, v in r.in_axes.items())]
    if not rows:
        print("error: every row sits at the defaults", file=sys.stderr)
        return 2

    glyphs = [ord(ch) for ch in args.glyphs]
    report = {"rows": {}, "spac": {}, "worst_row": 0.0, "worst_spac": 0.0}
    failures = []

    for row in rows:
        user = {str(t).lower(): float(v) for t, v in row.in_axes.items()}
        worst, where = 0.0, ""
        for cp in glyphs:
            a_s, lsb_s, ink_s = _metrics(studio, cp, user)
            a_b, lsb_b, ink_b = _metrics(built, cp, user)
            rsb_s, rsb_b = a_s - lsb_s - ink_s, a_b - lsb_b - ink_b
            for label, x, y in (("advance", a_s, a_b), ("lsb", lsb_s, lsb_b),
                                ("rsb", rsb_s, rsb_b), ("ink", ink_s, ink_b)):
                delta = abs(x - y)
                if delta > worst:
                    worst, where = delta, f"{chr(cp)} {label}"
        report["rows"][row.instance_name] = {"worst": round(worst, 2),
                                             "where": where}
        report["worst_row"] = max(report["worst_row"], round(worst, 2))
        if worst > ROW_TOLERANCE:
            failures.append(f"row {row.instance_name!r}: {where} differs by "
                            f"{worst:.2f} u")

        for end in spac_ends:
            worst_end, where_end = 0.0, ""
            for cp in glyphs:
                a0_s, l0_s, i0_s = _metrics(studio, cp, user)
                a0_b, l0_b, i0_b = _metrics(built, cp, user)
                a1_s, l1_s, i1_s = _metrics(studio, cp, {**user, spac: end})
                a1_b, l1_b, i1_b = _metrics(built, cp, {**user, spac: end})
                r0_s, r0_b = a0_s - l0_s - i0_s, a0_b - l0_b - i0_b
                r1_s, r1_b = a1_s - l1_s - i1_s, a1_b - l1_b - i1_b
                checks = {
                    "advance": ((a1_s - a0_s), (a1_b - a0_b)),
                    "lsb": ((l1_s - l0_s), (l1_b - l0_b)),
                    "rsb": ((r1_s - r0_s), (r1_b - r0_b)),
                }
                for label, (x, y) in checks.items():
                    delta = abs(x - y)
                    if delta > worst_end:
                        worst_end, where_end = delta, f"{chr(cp)} {label} {end:+g}"
            key = f"{row.instance_name} {end:+g}"
            report["spac"][key] = {"worst": round(worst_end, 2),
                                   "where": where_end}
            report["worst_spac"] = max(report["worst_spac"],
                                       round(worst_end, 2))
            if worst_end > SPAC_TOLERANCE:
                failures.append(f"SPAC {end:+g} at {row.instance_name!r}: "
                                f"{where_end} differs by {worst_end:.2f} u")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")

    worst_row = max(report["rows"].items(), key=lambda kv: kv[1]["worst"])
    print(f"rows compared: {len(rows)}  worst row: {worst_row[0]!r} "
          f"{worst_row[1]['worst']} u ({worst_row[1]['where']})")
    if spac_ends:
        worst_spac = max(report["spac"].items(), key=lambda kv: kv[1]["worst"])
        print(f"SPAC ends: {spac_ends}  worst: {worst_spac[0]!r} "
              f"{worst_spac[1]['worst']} u ({worst_spac[1]['where']})")
    for failure in failures[:20]:
        print(f"  FAIL {failure}")
    print(f"{'PASS' if not failures else 'FAIL'}: {len(failures)} check(s) "
          f"outside tolerance")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
