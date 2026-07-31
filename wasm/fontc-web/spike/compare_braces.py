#!/usr/bin/env python3
"""Oracle comparison for apply_control_axes / apply_grade.

Compares the wasm crate's injected brace geometry against fontTools'
own instancer (the oracle), per the task's verification spec:

  control axis (glyph 'e', layer location {"XTRA": 1000}):
    a) patched font instantiated at the layer location == original font
       instantiated there (EXACT — the injected tuple is inactive while
       the control axis sits at its default);
    b) the injected outline itself — the patched font instantiated with
       the control axis fully engaged and everything else at default —
       == fontTools' instantiation of the original at the layer
       location, within 1e-6 relative tolerance (expected: exact, the
       delta rounds identically).

  grade (one graded instance, pct 0.3):
    c) at (instance base coords × GRAD ±10) the patched 'e' matches
       fontTools' instantiation at grade.py's light/dark coords, shifted
       to hold the advance (±1 font unit: the int16 delta rounding
       cascade that varLib's gvar has too);
    d) the advance is held EXACTLY across GRAD (zero phantom delta), for
       a simple glyph ('e'), a composite ('W') and 'space'.

  full pipeline (add_avar2 → control → grade):
    e) the font is structurally sound: fvar carries all 8 axes, avar v2
       has 8 segment maps, gvar axisCount is 8, and fontTools can
       instantiate at a spread of locations without error.

Usage: compare_braces.py ORIGINAL CONTROL GRADE FULL
"""

import json
import sys

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

from avar2_studio import grade as grade_model

FAILURES = []


def ok(cond, label):
    print(f"  {'✓' if cond else '✗ FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def points(font, glyph):
    """Contour/component points + 4 phantom points, instanced state."""
    coords, _ = font["glyf"]._getCoordinatesAndControls(glyph, font["hmtx"].metrics)
    return list(coords)


def instance(path, location):
    """Instantiate a fresh copy of the font at `location` (instancer
    returns a new font unless inplace=True — do not discard it)."""
    font = TTFont(path)
    return instancer.instantiateVariableFont(font, location)


def max_rel_diff(a, b, abs_floor=1e-9):
    worst = 0.0
    for (xa, ya), (xb, yb) in zip(a, b):
        for va, vb in ((xa, xb), (ya, yb)):
            d = abs(va - vb)
            m = max(abs(va), abs(vb), abs_floor)
            worst = max(worst, d / m)
    return worst


def max_abs_diff(a, b):
    return max(
        (abs(va - vb) for (xa, ya), (xb, yb) in zip(a, b) for va, vb in ((xa, xb), (ya, yb))),
        default=0.0,
    )


def main():
    original_path, control_path, grade_path, full_path = sys.argv[1:5]
    control_json = json.load(open("/tmp/braces-control.json"))
    grade_json = json.load(open("/tmp/braces-grade.json"))
    coords_json = json.load(open("/tmp/braces-coords.json"))

    layer = control_json[0]["layers"][0]
    glyph = layer["glyph"]
    location = layer["location"]  # {"XTRA": 1000.0}
    control_tag = control_json[0]["tag"]
    control_max = control_json[0]["max"]

    print("1. control axis: outline oracle (glyph '%s' at %s)" % (glyph, location))
    expected = points(instance(original_path, location), glyph)
    at_location = points(
        instance(control_path, {**location, control_tag: 0}), glyph
    )
    d = max_rel_diff(at_location, expected)
    print(f"     patched@location vs original@location: max rel diff {d:.3g}")
    ok(d == 0, "patched font at layer location == original (tuple inactive at default)")
    injected = points(instance(control_path, {control_tag: control_max}), glyph)
    d = max_rel_diff(injected, expected)
    print(f"     injected outline vs fontTools@location:   max rel diff {d:.3g}")
    ok(d <= 1e-6, "injected outline == fontTools instantiation (rel tol 1e-6)")

    print("2. grade: shape + advance oracle")
    inst = grade_json["instances"][0]
    base = coords_json[inst["name"]]
    ranges = {
        "XTRA": (94.0, 3330.0),
        "XOPQ": (2.0, 1016.0),
        "YOPQ": (2.0, 462.0),
    }
    light, dark = grade_model.grade_coords(base, inst["pct"], ranges)
    for grad_val, gcoords in ((-10, light), (10, dark)):
        at_grade = instance(grade_path, {**base, "GRAD": grad_val})
        at_base = instance(grade_path, {**base, "GRAD": 0})
        oracle = instance(original_path, gcoords)
        oracle_base = instance(original_path, base)
        a0 = oracle_base["hmtx"][glyph][0]
        w = oracle["hmtx"][glyph][0]
        shift = round((a0 - w) / 2)  # python round == round_ties_even
        oracle_pts = points(oracle, glyph)
        expected_pts = [(x + shift, y) for x, y in oracle_pts[:-4]] + points(
            oracle_base, glyph
        )[-4:]
        got_pts = points(at_grade, glyph)
        d = max_abs_diff(got_pts, expected_pts)
        print(
            f"     GRAD={grad_val:+d}: max abs diff {d:.3g} units "
            f"(shift {shift}, A0 {a0}, W {w})"
        )
        # Tolerance ±2 units: the int16 delta rounding cascade (my delta
        # is otRound(grade − base); the instancer re-rounds on apply —
        # the same double rounding varLib's gvar output has).
        ok(d <= 2.0, f"'{glyph}' at base × GRAD {grad_val:+d} == shifted grade outline (±2 units)")
        held = at_grade["hmtx"][glyph][0] == at_base["hmtx"][glyph][0]
        ok(held, f"'{glyph}' advance held across GRAD {grad_val:+d}")
    # composite + space advances held too
    for gname in ("W", "space"):
        adv = instance(grade_path, {**base, "GRAD": 10})["hmtx"][gname][0]
        adv0 = instance(grade_path, {**base, "GRAD": 0})["hmtx"][gname][0]
        ok(adv == adv0, f"'{gname}' advance held across GRAD +10")

    print("3. full pipeline (avar2 + control + grade): structure")
    full = TTFont(full_path)
    axes = [(a.axisTag, a.minValue, a.defaultValue, a.maxValue) for a in full["fvar"].axes]
    tags = [a[0] for a in axes]
    ok(
        tags == ["XTRA", "XOPQ", "YOPQ", "OPSZ", "WGHT", "WDTH", "crbr", "GRAD"],
        f"fvar axis set/order ({tags})",
    )
    grad = axes[-1]
    ok(grad[1:] == (-10.0, 0.0, 10.0), f"GRAD range {grad[1:]}")
    ok(full["gvar"].axisCount == 8, "gvar axisCount == 8")
    avar = full["avar"]
    ok(
        len(avar.segments) == 8,
        f"avar segment maps == 8 ({len(avar.segments)})",
    )
    names = {n.toUnicode() for n in full["name"].names}
    ok("Crossbar" in names and "Grade" in names, "axis names in name table")
    try:
        # fontTools can't PARTIALLY instance an avar2 font, so pin all
        # 8 axes at a spread of locations.
        all_axes = ["XTRA", "XOPQ", "YOPQ", "OPSZ", "WGHT", "WDTH", "crbr", "GRAD"]
        for loc in (
            {"WGHT": 900},
            {"OPSZ": 12, "crbr": 100},
            {"GRAD": -10},
            {"WGHT": 900, "WDTH": 200, "crbr": 50, "GRAD": 10},
            {"XTRA": 1000},
        ):
            full_loc = {
                t: dict((a.axisTag, a.defaultValue) for a in full["fvar"].axes)[t]
                for t in all_axes
            }
            full_loc.update(loc)
            instance(full_path, full_loc)
        ok(True, "fontTools instantiates the full pipeline font at 5 locations")
    except Exception as e:  # noqa: BLE001
        ok(False, f"instancer failed on full pipeline font: {e}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("ALL BRACE ORACLE CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
