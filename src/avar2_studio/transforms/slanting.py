"""Slanting — the slant transform's engine: build-time italics on a
``slnt`` axis, mirroring the Slant Master plugin's settled choices
(shear about y = x-height/2, LSB and advance preserved, no extrema
correction, grid-snapped x).

Enabling the transform adds a ``slnt`` axis (default 0 = upright, min =
minus the steepest master's angle): every master gains a SLANTED twin
drawn at its own angle, so the shared axis end means "each master fully
slanted" — the convention the plugin's one-shared-slnt-value choice set.
Angles follow each master's mean advance between the Narrowest/Widest
dials (the rule that made XCrispy's italics), overridable per master in
degrees; brace layers interpolate their angle on the parametric plane.
Styles take their slant as a percent of the axis (the transform's
font-stage apply stamps instance coordinates).
"""

from __future__ import annotations

import math
from typing import Dict

from .corner_rounding import _copy_layer, _plane_axes, extend_for_axis


def _mean_advance(font, master) -> float:
    total = 0.0
    n = 0
    for glyph in font.glyphs:
        layer = glyph.layers[master.id]
        if layer is None:
            continue
        try:
            total += float(layer.width)
            n += 1
        except (TypeError, ValueError):
            continue
    return total / n if n else 0.0


def master_angles(font, params: dict) -> Dict[str, float]:
    """Per-master slant angle in degrees: linear in mean advance between
    the Narrowest/Widest dials, then per-master overrides by name."""
    narrow = float(params.get("narrow_angle", 11.0))
    wide = float(params.get("wide_angle", 6.0))
    overrides = params.get("master_angles") or {}
    masters = list(font.masters)
    names = {str(m.name) for m in masters}
    unknown = sorted(set(overrides) - names)
    if unknown:
        raise ValueError(
            "slant: master angle(s) name no master: %s (masters: %s)"
            % (", ".join(unknown), ", ".join(sorted(names))))
    means = {str(m.id): _mean_advance(font, m) for m in masters}
    lo = min(means.values())
    hi = max(means.values())
    span = hi - lo
    angles: Dict[str, float] = {}
    for m in masters:
        if span > 0:
            t = (means[str(m.id)] - lo) / span
            a = narrow + (wide - narrow) * t
        else:
            a = narrow
        ov = overrides.get(str(m.name))
        if ov is not None:
            a = float(ov)
        angles[str(m.id)] = max(0.0, min(45.0, a))
    return angles


def _angle_model(font, masters, angles_by_id):
    """angle(plane location) — exact at masters, blended between, so a
    brace layer slants consistently with its neighbours. Same model
    family (and the same normalization) as the radius overrides."""
    from fontTools.varLib.models import VariationModel

    plane = _plane_axes(font)
    order = []
    vals = []
    for m in masters:
        axes = list(getattr(m, "axes", None) or [])
        loc = tuple(float(axes[i]) if i < len(axes) else 0.0 for i, _t in plane)
        if loc in order:
            continue
        order.append(loc)
        vals.append(angles_by_id[str(m.id)])
    tags = [t for _i, t in plane]
    origin = order[0]
    spans = [(min(l[j] for l in order), max(l[j] for l in order)) for j in range(len(tags))]

    def norm(j, v):
        d = v - origin[j]
        span = (spans[j][1] - origin[j]) if d > 0 else (origin[j] - spans[j][0])
        return d / span if span > 0 else 0.0

    locations = [{tags[j]: norm(j, loc[j]) for j in range(len(tags)) if norm(j, loc[j])}
                 for loc in order]
    model = VariationModel(locations, axisOrder=tags)
    deltas = model.getDeltas(vals)

    def at(loc_dict):
        pos = {tags[j]: max(-1.0, min(1.0, norm(j, float(loc_dict.get(tags[j], origin[j])))))
               for j in range(len(tags))}
        pos = {k: v for k, v in pos.items() if v}
        return float(model.interpolateFromDeltas(pos, deltas))
    return at


def _shear_layer(layer, tan_a, y0):
    """Shear a layer about y = y0, keep the LSB (node-bounds) and the
    advance, snap x to the grid. Components take the translation
    compensation (dx += dy*tan); a scaled/rotated component keeps its
    2x2 as drawn — the Glyphs component model has no shear term."""
    xs = []
    for path in layer.paths:
        for node in path.nodes:
            xs.append(float(node.position.x))
    min_before = min(xs) if xs else None
    for path in layer.paths:
        for node in path.nodes:
            node.position.x = float(node.position.x) + (float(node.position.y) - y0) * tan_a
    shift = 0.0
    if min_before is not None:
        min_after = min(float(n.position.x) for p in layer.paths for n in p.nodes)
        shift = min_before - min_after
    for path in layer.paths:
        for node in path.nodes:
            node.position.x = math.floor(float(node.position.x) + shift + 0.5)
    # Positions are written back as glyphsLib Points — a plain tuple
    # SERIALIZES as a quoted string ('pos = "(88, 0.0)";') that fontc
    # rejects when the shadow is saved and compiled.
    from glyphsLib.types import Point

    def _xy(obj):
        pos = obj.position
        try:
            return float(pos.x), float(pos.y)
        except AttributeError:  # a plain tuple
            return float(pos[0]), float(pos[1])

    for comp in getattr(layer, "components", None) or []:
        x, y = _xy(comp)
        comp.position = Point(math.floor(x + y * tan_a + shift + 0.5), y)
    for anchor in getattr(layer, "anchors", None) or []:
        x, y = _xy(anchor)
        anchor.position = Point(math.floor(x + (y - y0) * tan_a + shift + 0.5), y)


def slant_font(font, params: dict, log=None) -> dict:
    """Add the slnt axis and the slanted twins in place."""
    import uuid

    log = log or (lambda _m: None)
    stats = {"axis": False, "max_angle": 0.0, "twins": 0, "masters": 0}
    masters = list(font.masters)
    if not masters:
        return stats
    angles = master_angles(font, params)
    max_angle = max(angles.values())
    stats["max_angle"] = max_angle
    stats["masters"] = len(masters)
    if max_angle <= 0:
        log("slant: every angle is 0 — nothing to slant")
        return stats
    master_by_id = {str(m.id): m for m in masters}
    xheights = {str(m.id): float(getattr(m, "xHeight", 0.0) or 0.0) for m in masters}
    angle_at = _angle_model(font, masters, angles)
    plane = _plane_axes(font)

    twin_of = extend_for_axis(font, "slnt", "Slant", -max_angle, "Slanted")
    stats["axis"] = True

    for glyph in font.glyphs:
        twins = []
        for L in list(glyph.layers):
            lid = str(L.layerId)
            coords = None
            attrs = getattr(L, "attributes", None)
            if attrs is not None:
                try:
                    coords = attrs.get("coordinates")
                except AttributeError:
                    coords = None
            is_master = lid in master_by_id
            if not is_master and not isinstance(coords, (list, tuple)):
                continue  # backups stay single
            twin = _copy_layer(L)
            if is_master:
                tm = twin_of[lid]
                twin.layerId = tm.id
                twin.associatedMasterId = tm.id
                angle = angles[lid]
                y0 = xheights[lid] / 2.0
            else:
                twin.layerId = str(uuid.uuid4()).upper()
                assoc = str(getattr(L, "associatedMasterId", ""))
                if assoc in twin_of:
                    twin.associatedMasterId = twin_of[assoc].id
                c = list(coords)
                c[-1] = -max_angle
                twin.attributes["coordinates"] = c
                loc = {tag: float(coords[i]) for i, tag in plane if i < len(coords)}
                angle = max(0.0, min(45.0, angle_at(loc)))
                y0 = xheights.get(assoc, xheights[str(masters[0].id)]) / 2.0
            _shear_layer(twin, math.tan(math.radians(angle)), y0)
            twins.append(twin)
        for twin in twins:
            glyph.layers.append(twin)
        stats["twins"] += len(twins)
    return stats
