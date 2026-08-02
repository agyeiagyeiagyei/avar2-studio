#!/usr/bin/env python3
"""Corner coverage audit — spike.

Diagnoses "missing corner" design-space coverage from a compiled
variable font — the failure class that never errors at build time, only
in axis usage (extrapolation collapse, inert regions, out-of-range
sources). Two layers:

  A. Structural: the gvar tuple regions are the per-glyph source
     positions. Report axis-extreme corners no source reaches, and
     sources outside the axis box (|normalized coord| > 1).
  B. Behavioral: rasterize probe glyphs across per-axis sweeps and
     measure stem darkness — flag weight reversal (rises, then
     collapses) and inert sweeps. Monotonicity, not absolute weight:
     display fonts legitimately have hairline corners.

Usage: corner_audit.py FONT.ttf [FONT2.ttf ...]
"""

import itertools
import sys

from fontTools.ttLib import TTFont
from fontTools.varLib.models import supportScalar
from PIL import Image, ImageDraw, ImageFont

PROBE_GLYPHS = ["a", "e", "o", "g", "A", "H", "n", "x"]
SWEEP_STEPS = 7
COLLAPSE_RATIO = 0.5   # darkness after the peak falls below this share of it
COLLAPSE_MIN_PEAK = 300  # a collapse only counts if real weight appeared first
INERT_MAX_PX = 30      # sweep never renders more than this many dark pixels
EPS = 1e-3


def axis_box(font):
    return {a.axisTag: (a.minValue, a.defaultValue, a.maxValue) for a in font["fvar"].axes}


def source_peaks(font, tags):
    """Normalized source positions (gvar tuple peaks) → glyphs using them."""
    peaks = {}
    for glyph, tuples in font["gvar"].variations.items():
        for t in tuples:
            peak = tuple(t.axes.get(tag, (0.0, 0.0, 0.0))[1] for tag in tags)
            peaks.setdefault(peak, set()).add(glyph)
    # The default master is a source too — it just produces no gvar
    # tuple (zero delta), so it never appears in the peaks.
    peaks.setdefault(tuple(0.0 for _ in tags), set()).add("(default)")
    return peaks


def layer_a(font, box):
    tags = list(box)
    peaks = source_peaks(font, tags)
    findings = []

    # Sources outside the axis box (out-of-range braces/masters)
    for p in sorted(p for p in peaks if any(abs(c) > 1.0 + EPS for c in p)):
        over = ", ".join(f"{tags[i]} {p[i]:+.2f}" for i in range(len(tags)) if abs(p[i]) > 1.0 + EPS)
        findings.append(f"source outside the axis box ({over}) used by {len(peaks[p])} glyph(s)")

    # Corner coverage: for each axis-extreme corner, does any source's
    # tent reach it with a nonzero scalar? The corner that IS the
    # default location is covered by definition (zero deltas there).
    lo, _, hi = next(iter(box.values()))
    norm_default = tuple(
        -1.0 if box[t][1] == box[t][0] else (1.0 if box[t][1] == box[t][2] else 0.0)
        for t in tags
    )
    all_tuples = [t for tuples in font["gvar"].variations.values() for t in tuples]
    for corner in itertools.product((-1.0, 1.0), repeat=len(tags)):
        if corner == norm_default:
            continue
        loc = dict(zip(tags, corner))
        scalar = max((supportScalar(loc, t.axes) for t in all_tuples), default=0.0)
        if scalar > EPS:
            continue
        reach = []
        for i, tag in enumerate(tags):
            best = max((p[i] * corner[i] for p in peaks), default=0.0)
            reach.append(min(1.0, max(0.0, best)))
        corner_txt = ", ".join(f"{tags[i]} {'▲' if corner[i] > 0 else '▼'}" for i in range(len(tags)))
        reach_txt = ", ".join(f"{tags[i]} {r:.0%}" for i, r in enumerate(reach))
        findings.append(f"corner ({corner_txt}): no source reaches it (edge coverage {reach_txt})")
    return findings


def layer_b(path, box, glyphs):
    tags = list(box)
    default = {t: box[t][1] for t in tags}
    findings = []

    def darkness(axes_user, size=72):
        total = 0
        for g in glyphs:
            f = ImageFont.truetype(path, size)
            f.set_variation_by_axes([axes_user[t] for t in tags])
            bbox = f.getbbox(g)
            img = Image.new("L", (max(bbox[2] - bbox[0] + 20, 40), max(bbox[3] - bbox[1] + 20, 80)), 255)
            ImageDraw.Draw(img).text((10 - bbox[0], 10 - bbox[1]), g, font=f, fill=0)
            total += sum(1 for p in img.get_flattened_data() if p < 128)
        return total

    def fmt_loc(a):
        return "(" + ", ".join(f"{t} {a[t]:g}" for t in tags) + ")"

    def sweep(steps, label, report_inert=False):
        vals = [darkness(a) for a in steps]
        peak = max(vals)
        peak_i = vals.index(peak)
        tail = vals[peak_i + 1:]
        if peak >= COLLAPSE_MIN_PEAK and tail and min(tail) < COLLAPSE_RATIO * peak:
            cut = steps[peak_i + 1 + tail.index(min(tail))]
            findings.append(("fail", f"{label}: collapses — peak {peak}px then {min(tail)}px at {fmt_loc(cut)}"))
        elif report_inert and peak <= INERT_MAX_PX:
            findings.append(("info", f"{label}: inert (never more than {peak}px of stem)"))

    for axis in tags:
        lo, _, hi = box[axis]
        for other, pos in [(None, None)] + [(t, box[t][0]) for t in tags if t != axis] + \
                          [(t, box[t][2]) for t in tags if t != axis]:
            steps = []
            for i in range(SWEEP_STEPS):
                a = {**default, axis: lo + (hi - lo) * i / (SWEEP_STEPS - 1)}
                if other:
                    a[other] = pos
                steps.append(a)
            label = f"{axis} sweep" + (" (others at default)" if other is None else f" with {other} at {pos:g}")
            sweep(steps, label, report_inert=(other is None))
    return findings


def main():
    for path in sys.argv[1:]:
        font = TTFont(path)
        box = axis_box(font)
        cmap = font.getBestCmap() or {}
        glyphs = [g for g in PROBE_GLYPHS if g in cmap.values()] or list(font.getGlyphOrder())[1:6]
        print(f"\n=== {path}")
        print(f"    axes: {box}")
        for msg in layer_a(font, box):
            print(f"  A ✗ {msg}")
        for kind, msg in layer_b(path, box, glyphs):
            print(f"  B {'✗' if kind == 'fail' else 'i'} {msg}")


if __name__ == "__main__":
    main()
