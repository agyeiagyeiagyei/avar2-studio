#!/usr/bin/env python3
"""Oracle for pin_corner's synthesis path: pin a corner with scaffold
null → the wasm synthesizes the corner content by free extrapolation
(per-axis trend peel-off, linear continuation — see extrapolate.rs).
This script computes the same extrapolation over the UNPINNED font's
gvar tuples for 'a' and compares against instancing the PINNED font at
the corner (fontTools instancer applies the pin's decomposition).
Usage: compare_pin_synth.py UNPINNED.ttf PINNED.ttf XTRA XOPQ YOPQ
"""

import sys

from fontTools.ttLib import TTFont
from fontTools.varLib.iup import iup_delta
from fontTools.varLib.instancer import instantiateVariableFont


def glyph_sources(font, name, tags):
    """Per-source (loc, deltas): master-level tuples only — braces
    (regions bounded inside the box on a moved axis) are excluded."""
    glyf = font["glyf"]
    coords, controls = glyf._getCoordinatesAndControls(name, font["hmtx"].metrics)
    base = list(coords)
    end_pts = controls.endPts
    sources = []
    for tv in font["gvar"].variations[name]:
        tri = [tv.axes.get(t, (0.0, 0.0, 0.0)) for t in tags]
        is_brace = any(
            p != 0.0 and abs(en) < 0.999
            for (st, p, en) in tri
        )
        if is_brace:
            continue
        loc = [p for (_st, p, _en) in tri]
        deltas = list(tv.coordinates)
        filled = iup_delta(deltas, base, end_pts)
        sources.append((loc, [(float(dx), float(dy)) for dx, dy in filled]))
    return base, sources


def synthesize(font, name, loc, tags):
    axes = {a.axisTag: a for a in font["fvar"].axes}
    norm = []
    for t in tags:
        a = axes[t]
        v = loc.get(t, a.defaultValue)
        if v == a.defaultValue:
            n = 0.0
        elif v < a.defaultValue:
            n = (v - a.defaultValue) / (a.defaultValue - a.minValue)
        else:
            n = (v - a.defaultValue) / (a.maxValue - a.defaultValue)
        norm.append(max(-1.0, min(1.0, n)))

    base, sources = glyph_sources(font, name, tags)
    n_pts = len(base)
    zero = [(0.0, 0.0)] * n_pts

    def moved(s):
        return [i for i, v in enumerate(s[0]) if v != 0.0]

    # basis: on C's side and never beyond C per axis; axes C leaves at
    # default are unrestricted within the box (joint masters off the
    # default plane still inform trends via the residual pass)
    basis = [
        s for s in sources
        if all(
            v == 0.0
            or (norm[i] != 0.0 and (v < 0) == (norm[i] < 0) and abs(v) <= abs(norm[i]) + 1e-9)
            or (norm[i] == 0.0 and abs(v) <= 1.0 + 1e-9)
            for i, v in enumerate(s[0])
        )
    ]

    trends = {a: [] for a in range(len(tags))}  # a -> [(v, deltas)]
    known = set()

    for s in basis:
        m = moved(s)
        if len(m) == 1:
            a = m[0]
            trends[a].append((s[0][a], s[1]))
            known.add(a)

    joints = [s for s in basis if len(moved(s)) >= 2]
    while True:
        progress = False
        deferred = []
        for s in joints:
            m = moved(s)
            unknown = [a for a in m if a not in known]
            residual = [list(d) for d in s[1]]
            for a in m:
                if a in known:
                    eff = eval_trend(trends[a], s[0][a], n_pts)
                    for k in range(n_pts):
                        residual[k][0] -= eff[k][0]
                        residual[k][1] -= eff[k][1]
            residual = [(dx, dy) for dx, dy in residual]
            if not unknown:
                continue
            if len(unknown) == 1:
                a = unknown[0]
                trends[a].append((s[0][a], residual))
                known.add(a)
                progress = True
            else:
                total = sum(abs(s[0][a]) for a in unknown)
                if total > 1e-9:
                    for a in unknown:
                        share = abs(s[0][a]) / total
                        trends[a].append((s[0][a], [(dx * share, dy * share) for dx, dy in residual]))
                        known.add(a)
                        progress = True
                else:
                    deferred.append(s)
        joints = deferred
        if not progress or not joints:
            break

    def add(a, b):
        return [(ax + bx, ay + by) for (ax, ay), (bx, by) in zip(a, b)]

    out = [[x, y] for x, y in base]
    for a in range(len(tags)):
        v = norm[a]
        if v == 0.0:
            continue
        eff = eval_trend(trends[a], v, n_pts)
        for k in range(n_pts):
            out[k][0] += eff[k][0]
            out[k][1] += eff[k][1]
    return out


def eval_trend(samples, v, n_pts):
    zero = [(0.0, 0.0)] * n_pts
    if v == 0.0 or not samples:
        return zero
    s = sorted(samples, key=lambda sd: abs(sd[0]))

    def lerp(a, b, t):
        return [(ax + (bx - ax) * t, ay + (by - ay) * t) for (ax, ay), (bx, by) in zip(a, b)]

    prev_v, prev_d = 0.0, zero
    for vi, di in s:
        if abs(v) <= abs(vi):
            t = (v - prev_v) / (vi - prev_v)
            return lerp(prev_d, di, t)
        prev_v, prev_d = vi, di
    if len(s) == 1:
        v1, d1 = s[0]
        return [(dx * v / v1, dy * v / v1) for dx, dy in d1]
    (vp, dp), (vn, dn) = s[-2], s[-1]
    t = (v - vp) / (vn - vp)
    return lerp(dp, dn, t)


def main():
    unpinned_path, pinned_path = sys.argv[1], sys.argv[2]
    loc = {"XTRA": float(sys.argv[3]), "XOPQ": float(sys.argv[4]), "YOPQ": float(sys.argv[5])}
    tags = ["XTRA", "XOPQ", "YOPQ"]

    expected = synthesize(TTFont(unpinned_path), "a", loc, tags)
    inst = instantiateVariableFont(TTFont(pinned_path), loc)
    coords, _ = inst["glyf"]._getCoordinatesAndControls("a", inst["hmtx"].metrics)
    got = list(coords)

    if len(got) != len(expected):
        print(f"  ✗ FAIL point count differs: {len(got)} vs {len(expected)}")
        return 1
    # Compare outline points only — the 4 trailing phantom points travel
    # differently across the two paths (HVAR carries advances in the
    # pinned font) and are covered by the e2e's width checks.
    got = got[:-4]
    expected = expected[:-4]
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
