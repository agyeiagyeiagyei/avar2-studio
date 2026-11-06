#!/usr/bin/env python3
"""Build the multi-master SPAC fixture: a small variable font whose masters
differ in WIDTH, plus the master locations a designspace would hand the
width-aware SPAC transform.

CrispyMini's eight masters sit at the corners of a cube with the default at
one of them. This font covers what that cannot:

  * a NEGATIVE side (the default is not at the axis minimum);
  * an INTERIOR master, behind a non-linear avar map — its support carries
    an intermediate region, and its user value normalizes through avar;
  * a master LOCATION with no master behind it (user XTRA 300): the outlines
    are interpolated there, so the coordinates are fractional;
  * an axis no master moves (`wght`), absent from every location;
  * glyphs that take each branch of the rule: a narrow advance under a wide
    outline (the 1 u floor), a zero advance, a contour with no on-curve
    point (curve extremes beyond every point), a glyph with ink at some
    masters only, composites (plain, scaled, nested, USE_MY_METRICS).

Usage: make_spac_masters_fixture.py OUT.ttf LOCATIONS.json [--no-hvar]
  LOCATIONS.json gets the master locations in USER space, the default as
  `{}` first (the shape of pipeline._master_locations).
  --no-hvar drops HVAR, so advances come from the gvar phantom points.
"""

import json
import sys

from fontTools import varLib
from fontTools.designspaceLib import (
    AxisDescriptor,
    DesignSpaceDocument,
    SourceDescriptor,
)
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables._g_l_y_f import USE_MY_METRICS

GLYPHS = [".notdef", "space", "H", "o", "j", "mark", "thin", "grow",
          "Hacute", "Hmetrics", "Hsmall", "nested"]

# (XTRA, XOPQ) in DESIGN units; the first is the default master.
MASTERS = [(200, 10), (100, 10), (275, 10), (500, 10),
           (200, 110), (100, 110), (275, 110), (500, 110)]
# XTRA user → design: user 350 is design 275 (normalized 0.5 → 0.25).
XTRA_MAP = [(100, 100), (200, 200), (350, 275), (500, 500)]
# What the designspace hands the transform, in USER units: the masters,
# plus one location (XTRA 300) that no master sits at.
LOCATIONS = [{},
             {"XTRA": 200, "XOPQ": 10}, {"XTRA": 100, "XOPQ": 10},
             {"XTRA": 350, "XOPQ": 10}, {"XTRA": 500, "XOPQ": 10},
             {"XTRA": 200, "XOPQ": 110}, {"XTRA": 100, "XOPQ": 110},
             {"XTRA": 350, "XOPQ": 110}, {"XTRA": 500, "XOPQ": 110},
             {"XTRA": 300, "XOPQ": 10}]


def box(pen, x0, y0, x1, y1):
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()


def sliver(pen, x0, x1):
    """Four points that stay distinct when x0 == x1 (a box would lose its
    closing point there, and the masters would stop being compatible)."""
    pen.moveTo((x0, 0))
    pen.lineTo((x0, 150))
    pen.lineTo((x1, 300))
    pen.lineTo((x1, 150))
    pen.closePath()


def master(xtra, xopq):
    """One master: widths follow XTRA, stems follow XOPQ."""
    # Counter width: not linear through the interior master, so its tuple
    # carries real deltas. Stem: not additive in the two axes, so the corner
    # masters do too.
    w = xtra + (25 if xtra == 275 else 0)
    s = 20 + xopq + xtra * xopq // 2000
    glyphs, metrics = {}, {}

    def simple(name, advance, draw):
        pen = TTGlyphPen(None)
        draw(pen)
        glyphs[name] = pen.glyph()
        metrics[name] = advance

    simple(".notdef", 600, lambda p: box(p, 50, 0, 550, 700))
    simple("space", 250 + w // 2, lambda p: None)

    def draw_h(p):
        box(p, 40, 0, 40 + s, 700)
        box(p, 40 + s + w, 0, 40 + 2 * s + w, 700)
    simple("H", 80 + 2 * s + w, draw_h)

    def draw_o(p):
        # No on-curve point, and a triangle of off-curves: the outline's
        # extremes lie on the curves, inside the control points' box.
        cx, rx = 30 + s + w // 2, s + w // 2
        p.qCurveTo((cx - rx, 0), (cx + rx, 0), (cx, 500), None)
        p.closePath()
    simple("o", 60 + 2 * s + w, draw_o)

    # A wide outline over a narrow advance: tightening is capped (the floor).
    simple("j", 40 + xopq // 10, lambda p: box(p, -w, -200, s, 500))
    # A zero advance with ink (a combining mark).
    simple("mark", 0, lambda p: box(p, -w // 3 - s, 550, -s, 650))
    # Ink at the default; none at the narrow masters (the outline collapses).
    thin_w = max(0, w - 100)
    simple("thin", 100 + thin_w, lambda p: sliver(p, 20, 20 + thin_w))
    # No ink at the default (XTRA 200); ink wherever the masters are wider.
    grow_w = max(0, w - 200)
    simple("grow", 120 + grow_w, lambda p: sliver(p, 30, 30 + grow_w))

    def composite(name, advance, parts, my_metrics=None):
        pen = TTGlyphPen(glyphs)
        for base, transform in parts:
            pen.addComponent(base, transform)
        glyph = pen.glyph()
        if my_metrics is not None:
            glyph.components[my_metrics].flags |= USE_MY_METRICS
        glyphs[name] = glyph
        metrics[name] = advance

    h_adv = metrics["H"]
    composite("Hacute", h_adv + 20, [("H", (1, 0, 0, 1, 10, 0)),
                                     ("mark", (1, 0, 0, 1, h_adv // 2 + w // 4, 100))])
    composite("Hmetrics", h_adv, [("H", (1, 0, 0, 1, 0, 0)),
                                  ("mark", (1, 0, 0, 1, h_adv // 2, 100))], my_metrics=0)
    composite("Hsmall", h_adv // 2 + 30, [("H", (0.5, 0, 0, 0.75, 15, 0))])
    composite("nested", h_adv + 60, [("Hacute", (1, 0, 0, 1, 30, 0)),
                                     ("o", (0.5, 0.25, 0, 0.5, w // 5, 300))])

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(GLYPHS)
    fb.setupCharacterMap({ord("H"): "H", ord("o"): "o", ord("j"): "j", 32: "space"})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(
        {name: (metrics[name], fb.font["glyf"][name].xMin if glyphs[name].numberOfContours else 0)
         for name in GLYPHS})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SpacMasters", "styleName": f"{xtra}-{xopq}"})
    fb.setupOS2()
    fb.setupPost()
    return fb.font


def build():
    doc = DesignSpaceDocument()
    for tag, lo, default, hi, mapping in (("XTRA", 100, 200, 500, XTRA_MAP),
                                          ("XOPQ", 10, 10, 110, None),
                                          ("wght", 100, 400, 900, None)):
        axis = AxisDescriptor()
        axis.tag = axis.name = tag
        axis.minimum, axis.default, axis.maximum = lo, default, hi
        if mapping:
            axis.map = mapping
        doc.addAxis(axis)
    for xtra, xopq in MASTERS:
        source = SourceDescriptor()
        source.font = master(xtra, xopq)
        source.location = {"XTRA": xtra, "XOPQ": xopq, "wght": 400}
        doc.addSource(source)
    font, _model, _excess = varLib.build(doc)
    return font


def main():
    out, locations = sys.argv[1:3]
    font = build()
    if "--no-hvar" in sys.argv[3:]:
        del font["HVAR"]
    font.save(out)
    with open(locations, "w") as fh:
        json.dump(LOCATIONS, fh)


if __name__ == "__main__":
    main()
