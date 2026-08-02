#!/usr/bin/env python3
"""Oracle comparator for measure_at: Pillow stem darkness vs outline
area on the same sweep points. The metrics measure different things
(pixel stems vs geometric filled area, and Pillow is upm-scaled), so
this compares BEHAVIOR, not values — per sweep:
  - collapse agreement: does the curve fall below half its peak after
    the peak? (must match on both metrics)
  - inert agreement: is the sweep's rise negligible (<5% of the
    collapse sweep's peak)? (must match)
Usage: compare_measure.py FONT AREAS_JSON
"""

import json
import sys

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

GLYPHS = ["a", "e", "o", "g"]
SIZE = 72


def darkness(path, loc, order):
    total = 0
    for g in GLYPHS:
        f = ImageFont.truetype(path, SIZE)
        f.set_variation_by_axes([loc[t] for t in order])
        bbox = f.getbbox(g)
        img = Image.new("L", (max(bbox[2] - bbox[0] + 20, 60), max(bbox[3] - bbox[1] + 20, 90)), 255)
        ImageDraw.Draw(img).text((10 - bbox[0], 10 - bbox[1]), g, font=f, fill=0)
        total += sum(1 for p in img.get_flattened_data() if p < 128)
    return total


def classify(vals):
    peak = max(vals)
    if peak == 0:
        return False, True
    peak_i = vals.index(peak)
    tail = vals[peak_i + 1:]
    collapse = bool(tail) and min(tail) < 0.5 * peak
    inert = (peak - min(vals)) < 0.05 * peak
    return collapse, inert


def main():
    font_path, areas_path = sys.argv[1:3]
    payload = json.load(open(areas_path))
    order = [a.axisTag for a in TTFont(font_path)["fvar"].axes]  # fvar order for Pillow
    pillow_all = [darkness(font_path, loc, order) for loc in payload["locations"]]
    areas = payload["areas"]

    failures = []
    for name, start, end in payload["sweeps"]:
        pillow = pillow_all[start:end]
        area = areas[start:end]
        pc, pi = classify(pillow)
        ac, ai = classify(area)
        status = "ok" if (pc == ac and pi == ai) else "MISMATCH"
        print(f"  {name}: pillow {[round(v) for v in pillow]}")
        print(f"  {name}: area   {[round(v) for v in area]}  [{status}: collapse {pc}/{ac}, inert {pi}/{ai}]")
        if pc != ac:
            failures.append(f"{name}: collapse differs (pillow {pc}, area {ac})")
        if pi != ai:
            failures.append(f"{name}: inert differs (pillow {pi}, area {ai})")

    if failures:
        for f in failures:
            print("  ✗ FAIL", f)
        return 1
    print("MEASURE ORACLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
