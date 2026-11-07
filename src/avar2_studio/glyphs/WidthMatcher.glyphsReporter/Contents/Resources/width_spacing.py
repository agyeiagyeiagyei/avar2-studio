# Copyright 2026 Agyei Archer. Licensed under the Apache License, Version 2.0.
"""Width Matcher's measuring and spacing.

Deliberately free of Glyphs.app / vanilla / AppKit imports so it can be
unit-tested outside Glyphs — ``plugin.py`` hands it live GSLayer objects,
duck-typed on ``paths`` / ``nodes`` / ``anchors`` / ``components`` /
``position`` / ``transform``.

The ink is measured from the outline itself, components resolved by
hand, never through ``layer.bounds`` or ``layer.LSB``: those need the
layer to be held by a glyph, are kept in whole units, and re-align
composites as a side effect of being read. Measuring by hand is also
what lets every glyph be planned BEFORE any glyph is moved — a composite
measured after its base has moved, or before its base has landed, is
measured wrong.
"""

from __future__ import annotations

import math

# Spacing contracts for the saved master. Mode 0 pastes the reference's
# sidebearings; the rest pin the ADVANCE to the reference's (plus an
# offset) and let the sidebearings land wherever the generated ink
# requires — same width, different spacing.
SPACING_REF_SB = 0
SPACING_ADV_PROPORTIONAL = 1
SPACING_ADV_CENTRED = 2
SPACING_ADV_KEEP_LSB = 3
SPACING_MODES = [
    "Reference sidebearings",
    "Reference advance - proportional",
    "Reference advance - centred",
    "Reference advance - keep LSB",
]

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

#: How deep components may nest before the walk gives up (a glyph that
#: contains itself would otherwise never end).
MAX_DEPTH = 8


def target_spacing(mode, ref_lsb, ref_rsb, ref_adv, ink_w, offset=0.0):
    """(lsb, rsb, advance) the saved layer should end up with.

    ``SPACING_REF_SB`` pastes the reference's sidebearings, so the advance
    only matches when the ink does. Every other mode pins the advance to
    the reference's (plus ``offset``) and derives the sidebearings from
    whatever ink the generated layer actually has: the slack left over is
    distributed in the reference's own LSB:RSB proportion, evenly, or
    entirely onto the right (keep LSB).
    """
    if mode == SPACING_REF_SB:
        return (ref_lsb, ref_rsb, ref_lsb + ink_w + ref_rsb)
    target_adv = ref_adv + offset
    slack = target_adv - ink_w
    if mode == SPACING_ADV_PROPORTIONAL:
        total = ref_lsb + ref_rsb
        # A zero total (full-bleed glyph) has no ratio to preserve — fall
        # back to an even split rather than dividing by zero.
        lsb = slack * (ref_lsb / total) if abs(total) > 1e-6 else slack / 2.0
    elif mode == SPACING_ADV_CENTRED:
        lsb = slack / 2.0
    else:
        lsb = ref_lsb
    return (lsb, slack - lsb, target_adv)


def empty_advance(mode, ref_adv, offset=0.0):
    """Advance for a glyph with no ink (space etc.)."""
    return ref_adv if mode == SPACING_REF_SB else ref_adv + offset


def snap(value, grid):
    """The multiple of ``grid`` nearest to ``value``; a half goes up,
    which is how Glyphs rounds coordinates. A grid of 0 asks for no
    rounding."""
    if not grid:
        return value
    return math.floor(value / grid + 0.5) * grid


# --- the outline --------------------------------------------------------------


def _pos(obj):
    p = obj.position
    return (float(p.x), float(p.y))


def _set_pos(obj, pt):
    pt = (float(pt[0]), float(pt[1]))
    obj.position = pt
    if (float(obj.position.x), float(obj.position.y)) != pt:
        # Glyphs takes a move too small to notice for no move at all, and
        # leaves 798.99999 where 799 was asked for. Go by way of elsewhere.
        obj.position = (pt[0] + 1.0, pt[1] + 1.0)
        obj.position = pt


def component_name(comp):
    return str(getattr(comp, "componentName", None) or comp.name)


def _transform_of(comp):
    return tuple(float(v) for v in comp.transform)


def _apply(t, pt):
    return (t[0] * pt[0] + t[2] * pt[1] + t[4], t[1] * pt[0] + t[3] * pt[1] + t[5])


def _compose(outer, inner):
    """The transform that applies ``inner`` first, then ``outer``."""
    return (
        outer[0] * inner[0] + outer[2] * inner[1],
        outer[1] * inner[0] + outer[3] * inner[1],
        outer[0] * inner[2] + outer[2] * inner[3],
        outer[1] * inner[2] + outer[3] * inner[3],
        outer[0] * inner[4] + outer[2] * inner[5] + outer[4],
        outer[1] * inner[4] + outer[3] * inner[5] + outer[5],
    )


def _path_segments(path):
    """A path's segments as point tuples: 4 points for a cubic, 2 for a
    line. Any other run (a quadratic) counts as the line between its
    on-curve ends."""
    nodes = list(path.nodes)
    on = [i for i, nd in enumerate(nodes) if str(nd.type) != "offcurve"]
    if not on:
        return []
    if len(on) == 1:
        p = _pos(nodes[on[0]])
        return [(p, p)]
    pairs = list(zip(on, on[1:]))
    if bool(path.closed):
        pairs.append((on[-1], on[0]))
    out = []
    for i, j in pairs:
        between = nodes[i + 1:j] if j > i else nodes[i + 1:] + nodes[:j]
        if len(between) == 2:
            out.append((_pos(nodes[i]), _pos(between[0]), _pos(between[1]), _pos(nodes[j])))
        else:
            out.append((_pos(nodes[i]), _pos(nodes[j])))
    return out


def outline(layer, base_layer, transform=IDENTITY, depth=0):
    """Every segment of a layer's drawn outline, components included.
    ``base_layer(name)`` answers the layer a component draws (None when
    there is none)."""
    segs = [tuple(_apply(transform, p) for p in seg)
            for path in layer.paths for seg in _path_segments(path)]
    if depth >= MAX_DEPTH:
        return segs
    for comp in layer.components:
        base = base_layer(component_name(comp))
        if base is not None:
            segs += outline(base, base_layer, _compose(transform, _transform_of(comp)), depth + 1)
    return segs


def _cubic_x_extremes(seg):
    """x at the interior turning points of a cubic (where dx/dt = 0)."""
    x0, x1, x2, x3 = (p[0] for p in seg)
    a = 3.0 * (x3 - 3.0 * x2 + 3.0 * x1 - x0)
    b = 6.0 * (x0 - 2.0 * x1 + x2)
    c = 3.0 * (x1 - x0)
    if abs(a) < 1e-9:
        roots = [] if abs(b) < 1e-9 else [-c / b]
    else:
        disc = b * b - 4.0 * a * c
        if disc < 0:
            roots = []
        else:
            root = math.sqrt(disc)
            roots = [(-b + root) / (2.0 * a), (-b - root) / (2.0 * a)]
    xs = []
    for t in roots:
        if 1e-6 < t < 1.0 - 1e-6:
            mt = 1.0 - t
            xs.append(mt * mt * mt * x0 + 3.0 * mt * mt * t * x1 + 3.0 * mt * t * t * x2 + t * t * t * x3)
    return xs


def ink_span(layer, base_layer, angle_deg=0.0, pivot_y=0.0):
    """(left, right) of a layer's ink, components included, or None for
    a layer that draws nothing.

    With an angle the extent is taken along the slant: of the outline
    un-slanted by ``angle_deg`` around ``pivot_y``. That is how Glyphs
    measures the sidebearings of a master with an italic angle, the pivot
    being half its x-height.
    """
    tan = math.tan(math.radians(angle_deg))
    xs = []
    for seg in outline(layer, base_layer):
        pts = [(x - tan * (y - pivot_y), y) for x, y in seg]
        xs += [pts[0][0], pts[-1][0]]
        if len(pts) == 4:
            xs += _cubic_x_extremes(pts)
    if not xs:
        return None
    return (min(xs), max(xs))


def shapes(layer):
    """What a layer holds, for telling a layer that landed from the
    empty one a new master starts every glyph with."""
    return (tuple(len(path.nodes) for path in layer.paths),
            tuple(component_name(c) for c in layer.components))


# --- planning and moving --------------------------------------------------------


def plan(mode, offset, ref_span, ref_adv, span, width, grid=0.0):
    """Where a glyph's new layer goes:
    ``{"shift", "width", "lsb", "rsb", "fits"}``.

    ``span`` and ``width`` are the new layer's ink and advance as they
    stand, ``ref_span`` and ``ref_adv`` the reference layer's. The shift
    is a whole number of grid steps, so a layer that is on the grid stays
    on it, and the advance is SET rather than left to follow from two
    rounded sidebearings — in the advance modes it is the reference's,
    exactly. ``lsb`` and ``rsb`` are what the layer then measures (None
    for a layer without ink).

    The contract cannot always be met: sidebearings as negative as an
    ultra-wide master's, around ink a fraction as wide, add up to an
    advance below zero, which Glyphs would store as 0. Such a glyph
    ``fits`` not, and keeps the spacing it was interpolated with.
    """
    if span is None:
        return {"shift": 0.0, "width": max(0.0, snap(empty_advance(mode, ref_adv, offset), grid)),
                "lsb": None, "rsb": None, "fits": True}
    ref_lsb, ref_rsb = (0.0, 0.0) if ref_span is None else (ref_span[0], ref_adv - ref_span[1])
    lsb, _rsb, adv = target_spacing(mode, ref_lsb, ref_rsb, ref_adv, span[1] - span[0], offset)
    fits = snap(adv, grid) >= 0.0
    shift = snap(lsb - span[0], grid) if fits else 0.0
    width = snap(adv if fits else width, grid)
    return {"shift": shift, "width": width, "fits": fits,
            "lsb": span[0] + shift, "rsb": width - (span[1] + shift)}


def round_layer(layer, grid):
    """Put every node, anchor and component of ``layer`` on a multiple of
    ``grid``. An interpolated layer sits between grid points, and Glyphs
    rounds one only when something moves it afterwards."""
    if not grid:
        return
    points = [nd for path in layer.paths for nd in path.nodes]
    points += list(getattr(layer, "anchors", None) or [])
    for pt in points:
        x, y = _pos(pt)
        _set_pos(pt, (snap(x, grid), snap(y, grid)))
    for comp in layer.components:
        t = _transform_of(comp)
        comp.transform = t[:4] + (snap(t[4], grid), snap(t[5], grid))


def shift_layer(layer, shift, base_shift, grid=0.0):
    """Move a layer's whole outline sideways by ``shift``.

    Its own paths and anchors move by ``shift``. A component draws its
    base glyph, and the base's layer moves by a shift of its own
    (``base_shift(name)``), so the component is offset by what is left:
    drawn through the transform M at t, a base moved by (s, 0) appears
    moved by M·(s, 0), and the component has to make up the difference,
    t' = t + (shift, 0) − M·(s, 0). Without that a composite is moved
    twice, or by its base's amount instead of its own.
    """
    before = [_transform_of(c) for c in layer.components]
    if shift:
        layer.applyTransform((1.0, 0.0, 0.0, 1.0, float(shift), 0.0))
    for comp, t in zip(layer.components, before):
        s = base_shift(component_name(comp))
        tx = t[4] + shift - t[0] * s
        ty = t[5] - t[1] * s
        comp.transform = t[:4] + (snap(tx, grid), snap(ty, grid))
    # The shift is a whole number of grid steps, but the transform is
    # carried out in floating point: 789 comes back as 788.99999.
    points = [nd for path in layer.paths for nd in path.nodes]
    points += list(getattr(layer, "anchors", None) or [])
    for pt in points if grid else ():
        x, y = _pos(pt)
        _set_pos(pt, (snap(x, grid), snap(y, grid)))
