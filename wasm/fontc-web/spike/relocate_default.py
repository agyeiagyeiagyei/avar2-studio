#!/usr/bin/env python3
"""Move a variable font's default, losslessly, as the studio's build does.

Usage: relocate_default.py IN.ttf OUT.ttf TAG=VALUE [TAG=VALUE ...]
  VALUE is in user units. Each named axis keeps its range.
"""

import sys

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer


def main():
    src, dst, *moves = sys.argv[1:]
    font = TTFont(src)
    axes = {axis.axisTag: axis for axis in font["fvar"].axes}
    limits = {}
    for move in moves:
        tag, value = move.split("=")
        limits[tag] = (axes[tag].minValue, float(value), axes[tag].maxValue)
    instancer.instantiateVariableFont(font, limits).save(dst)


if __name__ == "__main__":
    main()
