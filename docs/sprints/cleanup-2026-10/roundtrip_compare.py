#!/usr/bin/env python3
"""Round-trip acceptance for E1d, E2h, E2i and E2j.

Compares a fontmake build of the exported sources with the studio's TTF of the
SAME copy, using HarfBuzz for the metrics (it applies both fonts' avar/avar2):

* at every NON-default CSV row, each glyph's advance and extents agree within
  3 u;
* at SPAC ``lo`` and ``hi`` at those rows the deltas are judged by their MEAN,
  not by a per-check tolerance: ``|signed mean| <= 0.25 u`` per row and end and
  ``|mean| < 0.75 u`` per glyph across every row and end. Per-tuple rounding
  leaves individual checks 2–3 u apart in both directions, so a 1 u check
  cannot tell rounding from an offset (verifier, 2026-11-06); the worst single
  check is reported, not judged.
* with ``--designspace``, every glyph is also compared at every SPARSE layer
  location of the exported designspace (1 u), so the locations the build
  synthesises are covered and not just the rows the designer wrote down.

The default row is excluded by construction: the sources rest at the compiled
origin while the studio relocates the default (decision 0001), and its mapping
is deliberately absent from the designspace's ``<mappings>``.

Every glyph is compared by glyph id, grouped by class — ``.notdef``, unencoded
(no cmap entry), composite (a ``glyf`` composite), mixed (components *and*
contours in the source, flattened by the compiler; needs ``--sources-zip``) and
simple — and the numbers are reported per class and per SPAC end.

Usage::

    python roundtrip_compare.py --studio Crispy[...].ttf \\
        --fm Crispy-VF.ttf --csv Crispy-avar.csv [--sources-zip out.zip] \\
        [--mixed-from Crispy.glyphs] [--designspace Crispy.designspace] \\
        [--glyphs aognHeWi] [--json out] [--failures out.jsonl]

Without ``--glyphs`` every glyph in the font is compared. Exit status is 0 only
when every check is inside tolerance.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import zipfile
from pathlib import Path

import uharfbuzz as hb
from fontTools.designspaceLib import DesignSpaceDocument
from fontTools.ttLib import TTFont

from avar2_studio.build import config_generator

ROW_TOLERANCE = 3.0
SPAC_TOLERANCE = 1.0
SPARSE_TOLERANCE = 1.0
# The SPAC ends are judged by their MEAN error, not by a per-check tolerance:
# per-tuple rounding leaves individual checks 2–3 u apart in both directions,
# which a 1 u check cannot tell from a systematic offset (verifier, 2026-11-06).
SPAC_MEAN_TOLERANCE = 0.25    # |signed mean| per row × end, in font units
SPAC_GLYPH_TOLERANCE = 0.75   # |mean| per glyph across every row and end
CLASSES = ("simple", "composite", "mixed", "unencoded", ".notdef")


def hb_font(path: Path) -> hb.Font:
    blob = hb.Blob.from_file_path(str(path))
    return hb.Font(hb.Face(blob))


def _metrics(font: hb.Font, gid: int):
    """(advance, left sidebearing, ink width) for one glyph, location already set."""
    advance = font.get_glyph_h_advance(gid)
    extents = font.get_glyph_extents(gid)
    if extents is None:
        return float(advance), 0.0, 0.0
    return float(advance), float(extents.x_bearing), float(extents.width)


def _measure(font: hb.Font, gids, location: dict):
    """Measure every gid at one location — one ``set_variations`` per location."""
    font.set_variations(location)
    return [_metrics(font, gid) for gid in gids]


def _glyph_classes(path: Path, mixed=None):
    """``{glyph name: class}`` from the built font, plus the mixed set."""
    mixed = mixed or set()
    ttf = TTFont(str(path), lazy=True)
    try:
        encoded = {name for name in ttf.getGlyphOrder()
                   if _in_cmap(ttf, name)}
        glyf = ttf["glyf"] if "glyf" in ttf else None
        out = {}
        for name in ttf.getGlyphOrder():
            if name == ".notdef":
                out[name] = ".notdef"
            elif glyf is not None and ttf["glyf"][name].isComposite():
                out[name] = "composite"
            elif name in mixed:
                out[name] = "mixed"
            elif name not in encoded:
                out[name] = "unencoded"
            else:
                out[name] = "simple"
        return out
    finally:
        ttf.close()


def _codepoint(path: Path, name: str) -> int:
    """First unicode codepoint a glyph is mapped to, or -1."""
    ttf = TTFont(str(path), lazy=True)
    try:
        for table in ttf["cmap"].tables:
            if not table.isUnicode():
                continue
            for cp, gname in table.cmap.items():
                if gname == name:
                    return cp
        return -1
    finally:
        ttf.close()


def _mixed_from_glyphs(path: Path):
    """Glyph names with components AND contours in the design source."""
    import glyphsLib

    font = glyphsLib.GSFont(str(path))
    mixed = set()
    for glyph in font.glyphs:
        for layer in glyph.layers:
            has_component = any(_is_component(s) for s in layer.shapes)
            has_path = any(not _is_component(s) for s in layer.shapes)
            if has_component and has_path:
                mixed.add(glyph.name)
                break
    return mixed


def _is_component(shape) -> bool:
    return type(shape).__name__ == "GSComponent"


def _in_cmap(ttf, name) -> bool:
    for table in ttf["cmap"].tables:
        if table.isUnicode() and name in table.cmap.values():
            return True
    return False


def _mixed_from_zip(path: Path):
    """Glyph names whose source has components AND contours (the flattening case)."""
    mixed = set()
    with zipfile.ZipFile(path) as zf:
        masters = {n for n in zf.namelist() if n.endswith(".ufo/glyphs/")}
        if not masters:
            return mixed
        master = sorted(masters)[0]
        names = [n for n in zf.namelist()
                 if n.startswith(master) and n.endswith(".glif")]
        for entry in names:
            text = zf.read(entry).decode("utf-8", "replace")
            if "<component" not in text:
                continue
            match = re.search(r'<glyph[^>]*\bname="([^"]+)"', text)
            if match and "<contour" in text:
                mixed.add(match.group(1))
        return mixed


def _sparse_locations(path: Path) -> dict:
    """``{(tag, value), …: layerName}`` for every sparse layer source.

    The CSV rows only reach the locations the designer wrote down; the sparse
    layers reach the ones the build SYNTHESISES (a base's Lowercase-width layer
    forces its mixed glyphs' layers too). A glyph that misses one of those is a
    real disagreement, so the sweep runs at every layer location with the
    traditional axes at their defaults — the resting row of a coincide copy maps
    there, so both fonts are asked the same question.
    """
    doc = DesignSpaceDocument.fromfile(str(path))
    tags = {axis.name: axis.tag for axis in doc.axes}
    out = {}
    for source in doc.sources:
        if source.layerName:
            key = tuple(sorted((tags[name], value)
                               for name, value in source.location.items()))
            out.setdefault(key, source.layerName)
    return out


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
    ap.add_argument("--glyphs", default=None,
                    help="compare only these characters (default: every glyph)")
    ap.add_argument("--sources-zip", type=Path,
                    help="the exported sources zip, to classify mixed glyphs")
    ap.add_argument("--mixed-from", type=Path,
                    help="the ORIGINAL .glyphs, whose mixed glyphs the export "
                         "flattens (they compare as simple)")
    ap.add_argument("--designspace", type=Path,
                    help="the exported designspace, whose sparse layers the "
                         "sweep visits a second time with the traditional axes "
                         "at their defaults")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--failures", type=Path,
                    help="write every over-tolerance record here (JSON lines)")
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

    mixed = _mixed_from_zip(args.sources_zip) if args.sources_zip else set()
    if args.mixed_from:
        mixed |= _mixed_from_glyphs(args.mixed_from)
    classes = _glyph_classes(args.studio, mixed)
    # Glyph ids are per font: the two compilers emit the same production names
    # but may order them differently, so map by NAME and use each font's own id.
    studio_ttf = TTFont(str(args.studio), lazy=True)
    fm_ttf = TTFont(str(args.fm), lazy=True)
    try:
        fm_order = set(fm_ttf.getGlyphOrder())
        order = [g for g in studio_ttf.getGlyphOrder()
                 if g in classes and g in fm_order]
        missing_glyphs = [g for g in studio_ttf.getGlyphOrder()
                          if g in classes and g not in fm_order]
        gids = [studio_ttf.getGlyphID(g) for g in order]
        fm_gids = [fm_ttf.getGlyphID(g) for g in order]
    finally:
        studio_ttf.close()
        fm_ttf.close()
    if args.glyphs:
        wanted = {chr(c) for c in map(ord, args.glyphs)}
        keep = [(n, a) for n, a in enumerate(order)
                if chr(_codepoint(args.studio, n)) in wanted]
        order = [n for n, _ in keep]
        gids = [gids[i] for _, i in keep]
        fm_gids = [fm_gids[i] for _, i in keep]

    over_tolerance = []
    report = {"rows": {}, "spac": {}, "sparse": {}, "worst_row": 0.0,
              "worst_spac": 0.0, "worst_sparse": 0.0,
              "glyphs": len(order),
              "classes": {c: sum(1 for n in order if classes[n] == c)
                          for c in CLASSES},
              "by_class": {}}
    failures = []
    glyph_signed: dict = {}
    sparse_locations = (_sparse_locations(args.designspace)
                        if args.designspace else {})

    for row in rows:
        user = {str(t).lower(): float(v) for t, v in row.in_axes.items()}
        worst, where = 0.0, ""
        per_class = {}
        for name, ms, mb in zip(order,
                                _measure(studio, gids, user),
                                _measure(built, fm_gids, user)):
            a_s, lsb_s, ink_s = ms
            a_b, lsb_b, ink_b = mb
            rsb_s, rsb_b = a_s - lsb_s - ink_s, a_b - lsb_b - ink_b
            cls = classes[name]
            entry = per_class.setdefault(cls, {"worst": 0.0, "where": "",
                                               "over": 0})
            for label, x, y in (("advance", a_s, a_b), ("lsb", lsb_s, lsb_b),
                                ("rsb", rsb_s, rsb_b), ("ink", ink_s, ink_b)):
                delta = abs(x - y)
                if delta > entry["worst"]:
                    entry["worst"], entry["where"] = delta, f"{name} {label}"
                if delta > ROW_TOLERANCE:
                    entry["over"] += 1
                    over_tolerance.append({
                        "kind": "row", "row": row.instance_name,
                        "end": None, "glyph": name, "class": cls,
                        "metric": label, "delta": round(delta, 2)})
                if delta > worst:
                    worst, where = delta, f"{name} {label} ({cls})"
        report["rows"][row.instance_name] = {"worst": round(worst, 2),
                                             "where": where,
                                             "by_class": {
                                                 k: {"worst": round(v["worst"], 2),
                                                     "where": v["where"],
                                                     "over": v["over"]}
                                                 for k, v in per_class.items()}}
        report["worst_row"] = max(report["worst_row"], round(worst, 2))
        for cls, entry in per_class.items():
            agg = report["by_class"].setdefault(
                cls, {"worst": 0.0, "where": "", "over": 0})
            if entry["worst"] > agg["worst"]:
                agg["worst"], agg["where"] = entry["worst"], entry["where"]
            agg["over"] += entry["over"]
        if worst > ROW_TOLERANCE:
            failures.append(f"row {row.instance_name!r}: {where} differs by "
                            f"{worst:.2f} u")

        base_s = _measure(studio, gids, user)
        base_b = _measure(built, fm_gids, user)
        for end in spac_ends:
            worst_end, where_end = 0.0, ""
            per_class = {}
            signed: list = []
            for name, before_s, before_b, after_s, after_b in zip(
                    order, base_s, base_b,
                    _measure(studio, gids, {**user, spac: end}),
                    _measure(built, fm_gids, {**user, spac: end})):
                a0_s, l0_s, i0_s = before_s
                a0_b, l0_b, i0_b = before_b
                a1_s, l1_s, i1_s = after_s
                a1_b, l1_b, i1_b = after_b
                r0_s, r0_b = a0_s - l0_s - i0_s, a0_b - l0_b - i0_b
                r1_s, r1_b = a1_s - l1_s - i1_s, a1_b - l1_b - i1_b
                checks = {
                    "advance": ((a1_s - a0_s), (a1_b - a0_b)),
                    "lsb": ((l1_s - l0_s), (l1_b - l0_b)),
                    "rsb": ((r1_s - r0_s), (r1_b - r0_b)),
                }
                cls = classes[name]
                entry = per_class.setdefault(cls, {"worst": 0.0, "where": "",
                                                   "over": 0})
                for label, (x, y) in checks.items():
                    delta = abs(x - y)
                    signed.append(x - y)
                    glyph_signed.setdefault(name, []).append(x - y)
                    if delta > entry["worst"]:
                        entry["worst"] = delta
                        entry["where"] = f"{name} {label} {end:+g}"
                    if delta > SPAC_TOLERANCE:
                        entry["over"] += 1
                        over_tolerance.append({
                            "kind": "spac", "row": row.instance_name,
                            "end": end, "glyph": name, "class": cls,
                            "metric": label, "delta": round(delta, 2),
                            "signed": round(x - y, 2)})
                    if delta > worst_end:
                        worst_end = delta
                        where_end = f"{name} {label} {end:+g} ({cls})"
            mean = statistics.fmean(signed) if signed else 0.0
            sd = statistics.pstdev(signed) if len(signed) > 1 else 0.0
            positives = sum(1 for d in signed if d > 0)
            negatives = sum(1 for d in signed if d < 0)
            key = f"{row.instance_name} {end:+g}"
            report["spac"][key] = {"worst": round(worst_end, 2),
                                   "where": where_end,
                                   "mean": round(mean, 3),
                                   "sd": round(sd, 3),
                                   "signed": len(signed),
                                   "signs": [positives, negatives],
                                   "by_class": {
                                       k: {"worst": round(v["worst"], 2),
                                           "where": v["where"],
                                           "over": v["over"]}
                                       for k, v in per_class.items()}}
            report["worst_spac"] = max(report["worst_spac"],
                                       round(worst_end, 2))
            for cls, entry in per_class.items():
                agg = report["by_class"].setdefault(
                    cls, {"worst": 0.0, "where": "", "over": 0})
                if entry["worst"] > agg["worst"]:
                    agg["worst"], agg["where"] = entry["worst"], entry["where"]
                agg["over"] += entry["over"]
            # The verdict is the MEAN, not the worst check: rounding leaves
            # individual checks 2–3 u apart in both directions, and what would
            # be a real defect is a systematic shift (verifier, 2026-11-06).
            if abs(mean) > SPAC_MEAN_TOLERANCE:
                failures.append(
                    f"SPAC {end:+g} at {row.instance_name!r}: mean "
                    f"{mean:+.2f} u over {len(signed)} checks (sd {sd:.2f}, "
                    f"{positives}+ / {negatives}-)")

    for key, layer in sorted(sparse_locations.items()):
        location = {tag: defaults[tag] for tag in ("wght", "wdth", "opsz")
                    if tag in defaults}
        location.update(dict(key))
        worst_loc, where_loc = 0.0, ""
        per_class = {}
        over_here = 0
        for name, ms, mb in zip(order,
                                _measure(studio, gids, location),
                                _measure(built, fm_gids, location)):
            cls = classes[name]
            entry = per_class.setdefault(cls, {"worst": 0.0, "where": "",
                                               "over": 0})
            for label, x, y in (("advance", ms[0], mb[0]),
                                ("lsb", ms[1], mb[1]),
                                ("ink", ms[2], mb[2])):
                delta = abs(x - y)
                if delta > entry["worst"]:
                    entry["worst"], entry["where"] = delta, f"{name} {label}"
                if delta > SPARSE_TOLERANCE:
                    entry["over"] += 1
                    over_here += 1
                    over_tolerance.append({
                        "kind": "sparse", "row": None, "end": None,
                        "layer": layer, "glyph": name, "class": cls,
                        "metric": label, "delta": round(delta, 2)})
                if delta > worst_loc:
                    worst_loc = delta
                    where_loc = f"{name} {label} ({cls})"
        report["sparse"][layer] = {"location": dict(key),
                                   "worst": round(worst_loc, 2),
                                   "where": where_loc,
                                   "by_class": {
                                       k: {"worst": round(v["worst"], 2),
                                           "where": v["where"],
                                           "over": v["over"]}
                                       for k, v in per_class.items()}}
        report["worst_sparse"] = max(report["worst_sparse"],
                                     round(worst_loc, 2))
        for cls, entry in per_class.items():
            agg = report["by_class"].setdefault(
                cls, {"worst": 0.0, "where": "", "over": 0})
            if entry["worst"] > agg["worst"]:
                agg["worst"], agg["where"] = entry["worst"], entry["where"]
            agg["over"] += entry["over"]
        if over_here:
            failures.append(f"sparse layer {layer!r}: {where_loc} differs by "
                            f"{worst_loc:.2f} u ({over_here} check(s) over "
                            f"{SPARSE_TOLERANCE:g} u)")

    # Per-glyph means across every row and end: one glyph drifting
    # systematically is a real defect even when each row's mean cancels.
    glyph_means = {name: statistics.fmean(values)
                   for name, values in glyph_signed.items() if values}
    report["spac_glyph_means"] = {name: round(value, 3)
                                  for name, value in sorted(glyph_means.items())}
    report["worst_spac_glyph_mean"] = (
        round(max((abs(v) for v in glyph_means.values()), default=0.0), 3))
    for name, value in sorted(glyph_means.items(), key=lambda kv: -abs(kv[1])):
        if abs(value) > SPAC_GLYPH_TOLERANCE:
            failures.append(f"glyph {name!r}: mean SPAC error {value:+.2f} u "
                            f"over every row and end")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    if args.failures:
        args.failures.write_text("".join(json.dumps(r) + "\n"
                                        for r in over_tolerance))

    worst_row = max(report["rows"].items(), key=lambda kv: kv[1]["worst"])
    print(f"glyphs compared: {len(order)}  classes: {report['classes']}")
    if missing_glyphs:
        print(f"  not in the fontmake build: {len(missing_glyphs)} "
              f"({', '.join(missing_glyphs[:6])}…)")
    print(f"rows compared: {len(rows)}  worst row: {worst_row[0]!r} "
          f"{worst_row[1]['worst']} u ({worst_row[1]['where']})")
    for cls in CLASSES:
        agg = report["by_class"].get(cls)
        if agg:
            print(f"  {cls:<10} worst {agg['worst']:6.2f} u "
                  f"({agg['where']})  over tolerance: {agg['over']}")
    if spac_ends:
        worst_spac = max(report["spac"].items(), key=lambda kv: kv[1]["worst"])
        print(f"SPAC ends: {spac_ends}  worst single check: {worst_spac[0]!r} "
              f"{worst_spac[1]['worst']} u ({worst_spac[1]['where']})")
        for key, entry in report["spac"].items():
            print(f"  {key}: mean {entry['mean']:+.2f} u sd {entry['sd']:.2f} "
                  f"over {entry['signed']} checks "
                  f"({entry['signs'][0]}+ / {entry['signs'][1]}-)")
        worst_glyph = max(glyph_means.items(), key=lambda kv: abs(kv[1]))
        print(f"SPAC per glyph: worst mean {worst_glyph[1]:+.2f} u "
              f"({worst_glyph[0]}), tolerance |mean| < "
              f"{SPAC_GLYPH_TOLERANCE:g} u")
    if report["sparse"]:
        worst_sparse = max(report["sparse"].items(),
                           key=lambda kv: kv[1]["worst"])
        print(f"sparse locations: {len(report['sparse'])}  worst: "
              f"{worst_sparse[0]!r} {worst_sparse[1]['worst']} u "
              f"({worst_sparse[1]['where']})")
    for failure in failures[:20]:
        print(f"  FAIL {failure}")
    print(f"{'PASS' if not failures else 'FAIL'}: {len(failures)} check(s) "
          f"outside tolerance")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
