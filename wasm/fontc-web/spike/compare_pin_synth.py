#!/usr/bin/env python3
"""Oracle for pin_corner's synthesis path: pin a corner with scaffold
null → the wasm extrapolates master trends (per-axis v/peak on the
master's side of default, 0 opposite, clamped for intermediate tuples).
This script computes the same extrapolation over the UNPINNED font's
gvar tuples for 'a' and compares against instancing the PINNED font at
the corner (fontTools instancer applies the pin's decomposition).
Usage: compare_pin_synth.py UNPINNED.ttf PINNED.ttf XTRA XOPQ YOPQ
"""

import sys

from fontTools.ttLib import TTFont
from fontTools.varLib.iup import iup_delta
from fontTools.varLib.models import supportScalar
from fontTools.varLib.instancer import instantiateVariableFont

F2DOT14 = 16384.0


def glyph_tuples(font, name, tags):
    gvar = font["gvar"]
    glyf = font["glyf"]
    coords, controls = glyf._getCoordinatesAndControls(name, font["hmtx"].metrics)
    base = list(coords)
    end_pts = controls.endPts
    # fontTools TupleVariation.axes = {tag: (start, peak, end)} — keep
    # per-axis triples (unstated axes default to the origin).
    tuples = [
        ([tv.axes.get(t, (0.0, 0.0, 0.0)) for t in tags], tv)
        for tv in gvar.variations[name]
    ]
    return base, end_pts, tuples


def extrapolated_points(font, name, loc):
    """loc in USER coords; returns 'a' points with master tuples
    extrapolated (v/peak, same-side) and braces clamped."""
    fvar = font["fvar"]
    tags = [a.axisTag for a in fvar.axes]
    axes = {a.axisTag: a for a in fvar.axes}
    norm = {}
    for t in tags:
        a = axes[t]
        v = loc.get(t, a.defaultValue)
        if v == a.defaultValue:
            n = 0.0
        elif v < a.defaultValue:
            n = (v - a.defaultValue) / (a.defaultValue - a.minValue)
        else:
            n = (v - a.defaultValue) / (a.maxValue - a.defaultValue)
        norm[t] = max(-1.0, min(1.0, n))
    base, end_pts, tuples = glyph_tuples(font, name, tags)
    pts = [[x, y] for x, y in base]
    for tri, tv in tuples:
        # Match the wasm rule: per axis, extrapolate v/peak on the
        # master's side of default for regions that reach the axis edge
        # (master tents); strictly interior (brace) regions stay clamped.
        scalar = 1.0
        for i, (st, p, en) in enumerate(tri):
            if p == 0.0:
                continue
            v = norm[tags[i]]
            if v == 0.0 or (v < 0) != (p < 0):
                scalar = 0.0
                break
            if abs(en) >= 0.999 or abs(st) >= 0.999:
                scalar *= v / p
            else:
                if v < st or v > en:
                    scalar = 0.0
                    break
                scalar *= (v - st) / (p - st) if v <= p else (v - en) / (p - en)
        if scalar == 0.0:
            continue
        deltas = list(tv.coordinates)
        filled = iup_delta(deltas, base, end_pts)
        for i in range(len(pts)):
            pts[i][0] += scalar * filled[i][0]
            pts[i][1] += scalar * filled[i][1]
    return pts


def main():
    unpinned_path, pinned_path = sys.argv[1], sys.argv[2]
    loc = {"XTRA": float(sys.argv[3]), "XOPQ": float(sys.argv[4]), "YOPQ": float(sys.argv[5])}

    expected = extrapolated_points(TTFont(unpinned_path), "a", loc)
    inst = instantiateVariableFont(TTFont(pinned_path), loc)
    coords, _ = inst["glyf"]._getCoordinatesAndControls("a", inst["hmtx"].metrics)
    got = list(coords)

    if len(got) != len(expected):
        print(f"  ✗ FAIL point count differs: {len(got)} vs {len(expected)}")
        return 1
    worst = max(
        (abs(v - w) for (xa, ya), (xb, yb) in zip(got, expected) for v, w in ((xa, xb), (ya, yb))),
        default=0.0,
    )
    print(f"  synthesized corner vs pinned-font instance: max diff {worst:.3g} units")
    if worst > 2.0:
        print("  ✗ FAIL beyond tolerance (2.0)")
        return 1
    print("SYNTH ORACLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
