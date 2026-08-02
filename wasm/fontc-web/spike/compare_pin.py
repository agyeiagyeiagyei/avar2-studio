#!/usr/bin/env python3
"""Oracle comparator for pin_corner. Asserts, via Pillow darkness:
  1. BEFORE the pin, the ghost corner is dead (<10% of scaffold's darkness)
  2. AFTER the pin, the corner stands up (>=80% of scaffold's darkness)
  3. the default is unchanged (before == after)
  4. the mastered corner (1665,700,300) is unchanged (no bleed)
Usage: compare_pin.py BEFORE.ttf AFTER.ttf
"""

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


def main():
    before_path, after_path = sys.argv[1:3]
    order = [a.axisTag for a in TTFont(before_path)["fvar"].axes]
    corner = {"XTRA": 47, "XOPQ": 700, "YOPQ": 300}
    scaffold = {"XTRA": 47, "XOPQ": 350, "YOPQ": 255}
    default = {"XTRA": 47, "XOPQ": 1, "YOPQ": 1}
    mastered = {"XTRA": 1665, "XOPQ": 700, "YOPQ": 300}

    scaf_b = darkness(before_path, scaffold, order)
    corner_b = darkness(before_path, corner, order)
    corner_a = darkness(after_path, corner, order)
    default_b = darkness(before_path, default, order)
    default_a = darkness(after_path, default, order)
    master_b = darkness(before_path, mastered, order)
    master_a = darkness(after_path, mastered, order)

    print(f"  scaffold darkness (before): {scaf_b}")
    print(f"  corner darkness: before {corner_b} → after {corner_a}")
    print(f"  default darkness: before {default_b} → after {default_a}")
    print(f"  mastered corner:  before {master_b} → after {master_a}")

    failures = []
    if corner_b >= 0.1 * scaf_b:
        failures.append(f"ghost corner not dead before pin ({corner_b} vs scaffold {scaf_b})")
    if corner_a < 0.8 * scaf_b:
        failures.append(f"corner not held after pin ({corner_a} < 80% of scaffold {scaf_b})")
    if default_a != default_b:
        failures.append(f"default changed ({default_b} → {default_a})")
    if master_a != master_b:
        failures.append(f"mastered corner bled ({master_b} → {master_a})")

    for f in failures:
        print("  ✗ FAIL", f)
    if failures:
        return 1
    print("PIN ORACLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
