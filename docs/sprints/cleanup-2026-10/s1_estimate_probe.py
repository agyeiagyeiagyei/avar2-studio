"""Offline calibration for the S1(a) pre-instancer header budget.

Given a pre-relocation font (fvar + gvar + outlines) and one axis target
value, compare the REAL post-relocation per-glyph tuple counts (fontTools
instancer) with candidate cheap estimates, so the estimator that goes into
`pipeline` is measured rather than guessed.

Usage:
  python3 s1_estimate_probe.py FONT.ttf TAG TARGET [glyph ...]
"""

from __future__ import annotations

import sys

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.varLib.models import normalizeValue


def axis_support(tv, tag):
    """(lower, peak, upper) for ``tag``, or None when the tuple ignores it.

    fontTools >= 4.5x exposes ``TupleVariation.axes`` as tag ->
    (lower, peak, upper) in normalized coordinates.
    """
    return (tv.axes or {}).get(tag)


def main():
    path, tag, target = sys.argv[1], sys.argv[2], float(sys.argv[3])
    glyphs = sys.argv[4:] or ["g", "B", "e", "z"]
    font = TTFont(path)
    # The pipeline relocates BEFORE gen-avar2; the built preview font already
    # carries avar2, and fontTools refuses partial instancing then.
    for table in ("avar", "avar2"):
        if table in font:
            del font[table]
    axes = font["fvar"].axes
    tags = [a.axisTag for a in axes]
    if tag not in tags:
        sys.exit(f"{tag} not in {tags}")
    i = tags.index(tag)
    ax = axes[i]
    dt = normalizeValue(target, (ax.minValue, ax.defaultValue, ax.maxValue))
    print(f"font {path}: {len(tags)} axes, {tag} default {ax.defaultValue} -> {target} "
          f"(normalized {dt:+.3f})")

    gvar = font["gvar"].variations
    fresh = TTFont(path)
    for table in ("avar", "avar2"):
        if table in fresh:
            del fresh[table]
    real = instantiateVariableFont(fresh, {tag: (ax.minValue, target, ax.maxValue)},
                                   inplace=False)
    real_counts = {g: len(v) for g, v in real["gvar"].variations.items()}

    for glyph in glyphs:
        variations = gvar.get(glyph)
        if variations is None:
            print(f"  {glyph}: not in gvar")
            continue
        base = len(variations)
        involved = 0
        spans_target = 0
        peak_between = 0
        for tv in variations:
            got = axis_support(tv, tag)
            if not got:
                continue
            lower, peak, upper = got
            if peak == 0:
                continue
            involved += 1
            if lower < dt < upper:
                spans_target += 1
            if min(0.0, peak) < dt < max(0.0, peak):
                peak_between += 1
        print(f"  {glyph}: base {base} | real {real_counts.get(glyph, '?')} | "
              f"involved {involved} spans_target {spans_target} peak_between {peak_between}")
        for name, extra in (("base+involved", involved),
                            ("base+spans", spans_target),
                            ("base+peak_between", peak_between)):
            print(f"      est {name}: {base + extra} (real {real_counts.get(glyph, '?')})")

    real.close()
    font.close()


if __name__ == "__main__":
    main()
