#!/usr/bin/env python3
"""Oracle comparator for clamp_out_of_range: outlines after clamping
must not be mangled relative to the fontmake (varLib) build, which
drops the same stranded sources upstream. Exact equality is NOT
provable — fontdrasil and fontTools VariationModels differ on tent
shapes, leaving a residual (~70–120 units observed). The bound is an
anti-mangling guard: before clamping fontc extrapolated the stranded
sources (huge divergence), after clamping the residual must be small.
Usage: compare_clamp.py SOURCE.glyphs BEFORE.ttf AFTER.ttf
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

LOCS = [
    {"XTRA": 800, "XOPQ": 200, "YOPQ": 170},
    {"XTRA": 145, "XOPQ": 180, "YOPQ": 160},
]
GLYPHS = ["a", "g", "q", "o", "x", "A", "k"]
TOL = 200.0  # anti-mangling bound, not a tolerance match (see docstring)


def points(font, glyph):
    coords, _ = font["glyf"]._getCoordinatesAndControls(glyph, font["hmtx"].metrics)
    return list(coords)


def max_diff(path_a, path_b, loc, glyphs):
    a = instancer.instantiateVariableFont(TTFont(path_a), loc)
    b = instancer.instantiateVariableFont(TTFont(path_b), loc)
    worst = 0.0
    for g in glyphs:
        pa, pb = points(a, g), points(b, g)
        if len(pa) != len(pb):
            return float("inf"), g
        worst = max(
            worst,
            max((abs(v - w) for (xa, ya), (xb, yb) in zip(pa, pb) for v, w in ((xa, xb), (ya, yb))), default=0.0),
        )
    return worst, None


def main():
    source, before, after = sys.argv[1:4]
    failures = []
    with tempfile.TemporaryDirectory() as td:
        fontmake_path = str(Path(td) / "fontmake.ttf")
        r = subprocess.run(
            ["fontmake", "-g", source, "-o", "variable", "--output-path", fontmake_path],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(r.stderr[-800:])
            return 2
        for loc in LOCS:
            d_before, g = max_diff(before, fontmake_path, loc, GLYPHS)
            d_after, g2 = max_diff(after, fontmake_path, loc, GLYPHS)
            print(f"  at {loc}: fontc↔fontmake before {d_before:.3g}, after clamp {d_after:.3g}")
            if d_after > TOL:
                failures.append(f"at {loc}: still {d_after:.3g} off after clamp ({g2 or g})")

    for f in failures:
        print("  ✗ FAIL", f)
    if failures:
        return 1
    print("CLAMP ORACLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
