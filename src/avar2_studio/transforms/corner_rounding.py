"""Corner rounding — the geometry behind the ``round_corners`` transform.

Inserts a circular round at every line-line corner of a ``.glyphs``
font, IN EVERY LAYER THAT INTERPOLATES — the masters and every brace
layer (drawn corrections, computed control-axis seeds, grade braces) —
so the layers stay point-compatible: one rounded corner is one on-curve
node replaced by four (tangent, two handles, tangent) everywhere.

The RADIUS is per layer, a blend of the stroke it represents and the
width it sits at — without the width share, a wide instance at hairline
XOPQ reads fully sharp exactly where its letterforms are largest:

  outer corners (ink-convex)   ``outer_pct``% of XOPQ + ``outer_xtra_pct``% of XTRA
  inner corners (ink-concave)  ``inner_pct``% of XOPQ + ``inner_xtra_pct``% of XTRA

with floors for the light end, clamped to the room the corner's shorter
segment leaves, collapsing to the corner point where a hairline leaves
none. Corners are classified per layer, because counters can evert
between masters — and classified by the RENDERED fill (nonzero winding)
probed around each corner, not by contour identity or direction, because
counters formed by overlap (a self-crossing contour, a shell pair, a
folded counter path) render differently than they are drawn. A
correction layer draws the outline "as if at" another parametric point,
so its radius follows its TARGET's XOPQ and XTRA where the control
sidecar declares them.

Proven on XCrispy (18 masters) before it became a transform: identical
node structure everywhere, advances untouched, fontc accepts the
collapsed rounds, and the Corner Radii tool re-detects the radii it
asked for.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

#: A corner may take this share of its shorter segment, so two rounds
#: sharing a segment can never cross.
ROOM = 0.48

#: Turns flatter than this count as straight in that layer: the round
#: collapses there and grows where the corner is real.
MIN_TURN_DEG = 1.0

#: The tangent offset t = r·tan(turn/2) explodes on near-straight bends
#: (3.7·r at 150°), and the arc then swings across the outline wherever
#: the segments are long enough that the room clamp does not bite.
#: Capping t at this multiple of r lets shallow bends take a shallower
#: round instead: past ~127° of turn the effective radius eases off.
T_CAP = 2.0

#: Corner classification probes the rendered fill on a circle around the
#: corner whose radius scales to the feature: a quarter of the gap to the
#: nearest non-incident segment, never wider than this — Crispy's hairline
#: masters are shells two units apart, and a fixed probe would jump them.
PROBE_MAX = 4.0

#: Below this probe radius the nearest feature is sub-grid: nothing to
#: classify safely, so the corner collapses.
PROBE_MIN = 0.25


def _pts(path) -> List[Tuple[float, float]]:
    return [(float(n.position.x), float(n.position.y)) for n in path.nodes]

def _seg_dist(pt, a, b) -> float:
    """Distance from ``pt`` to the segment a-b (control-polygon distance —
    the same approximation the winding probes make)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.hypot(pt[0] - a[0], pt[1] - a[1])
    t = max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(pt[0] - (a[0] + t * dx), pt[1] - (a[1] + t * dy))

def _fit(r, phi, l1, l2, stats):
    """(t, k, effective radius) for a round of radius ``r`` on a corner
    of turn ``phi`` between segments ``l1``/``l2``: the tangent cap and
    the room clamp applied, collapsed to zeros when no room is left."""
    tan_half = math.tan(math.radians(phi) / 2.0)
    t = min(r * tan_half, T_CAP * r)
    t_max = math.floor(ROOM * min(l1, l2))
    if t > t_max:
        t = float(t_max)
        stats["clamped"] += 1
    if t < 1.0:
        stats["collapsed"] += 1
        return 0.0, 0.0, 0.0
    r_eff = t / tan_half
    k = (4.0 / 3.0) * math.tan(math.radians(phi) / 4.0) * r_eff
    return t, k, r_eff


def _winding(pt, poly) -> int:
    """Signed crossing number of ``poly`` around ``pt`` (nonzero rule)."""
    x, y = pt
    w = 0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if y1 <= y < y2 and (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1) > 0:
            w += 1
        elif y2 <= y < y1 and (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1) < 0:
            w -= 1
    return w


def _filled(pt, polys) -> bool:
    """What the renderer draws at ``pt``: nonzero winding over ALL the
    layer's contours. Overlap constructions — a self-crossing contour
    folding a counter, a shell pair, a folded counter path — all answer
    correctly, where contour identity or direction cannot."""
    return sum(_winding(pt, q) for q in polys) != 0


def _white_span(b, u1, u2, eps, polys):
    """Classify a corner by the rendered fill around it. Within ``eps`` of
    the corner only its two incident edges exist, so the neighborhood is
    exactly two sectors and two probes decide it. Returns "hidden" (all
    ink — construction, not a visible corner), "ambiguous" (all white or
    no room to probe — nothing to round safely), or "outer"/"inner" by
    the white sector's span (>180° = ink-convex, <180° = ink-concave)."""
    th1 = math.atan2(-u1[1], -u1[0])
    th2 = math.atan2(u2[1], u2[0])
    span_a = (th2 - th1) % (2.0 * math.pi)
    mid_a = th1 + span_a / 2.0
    mid_b = th2 + (2.0 * math.pi - span_a) / 2.0
    ink_a = _filled((b[0] + eps * math.cos(mid_a), b[1] + eps * math.sin(mid_a)), polys)
    ink_b = _filled((b[0] + eps * math.cos(mid_b), b[1] + eps * math.sin(mid_b)), polys)
    if ink_a and ink_b:
        return "hidden"
    if not ink_a and not ink_b:
        return "ambiguous"
    white = span_a if not ink_a else 2.0 * math.pi - span_a
    return "outer" if white > math.pi else "inner"


def _snap(v: float) -> float:
    return float(math.floor(v + 0.5))

def _structure(layer):
    return [(bool(p.closed), tuple(str(n.type) for n in p.nodes)) for p in layer.paths]


def _axis_index(font, tag: str) -> Optional[int]:
    for i, ax in enumerate(font.axes):
        if str(getattr(ax, "axisTag", "")).upper() == tag:
            return i
    return None


def xopq_index(font) -> Optional[int]:
    return _axis_index(font, "XOPQ")


def control_targets(control_sidecar: Optional[dict]) -> Dict[str, List[Tuple[dict, dict]]]:
    """glyph name -> [(location, target values)] from a ``-control.json``
    payload (``control_axes.load``). ``target values`` holds whichever of
    XOPQ / XTRA the record declares; a layer without either rounds by its
    own location."""
    out: Dict[str, List[Tuple[dict, dict]]] = {}
    for ax in (control_sidecar or {}).get("axes", []):
        for rec in ax.get("layers", []):
            target = rec.get("target") or {}
            declared = {k: float(target[k]) for k in ("XOPQ", "XTRA") if k in target}
            if declared:
                out.setdefault(str(rec.get("glyph")), []).append(
                    (dict(rec.get("location") or {}), declared))
    return out


def _layer_stroke(font, glyph_name, layer, master_by_id, xopq_idx, xtra_idx,
                  targets) -> Tuple[Optional[float], float]:
    """(XOPQ, XTRA) the layer's drawing represents. XTRA is 0 when the
    font has no such axis, so the width share contributes nothing."""
    def pick(coords):
        xopq = float(coords[xopq_idx]) if xopq_idx < len(coords) else None
        xtra = float(coords[xtra_idx]) if xtra_idx is not None and xtra_idx < len(coords) else 0.0
        return xopq, xtra

    coords = None
    attrs = getattr(layer, "attributes", None)
    if attrs is not None:
        try:
            coords = attrs.get("coordinates")
        except AttributeError:
            coords = None
    if isinstance(coords, (list, tuple)):
        xopq, xtra = pick(coords)
        location = {str(getattr(ax, "axisTag", "")): float(v)
                    for ax, v in zip(font.axes, coords)}
        for rec_loc, target in targets.get(glyph_name, []):
            if all(abs(location.get(k, 1e9) - float(v)) <= 0.51 for k, v in rec_loc.items()):
                return (target.get("XOPQ", xopq), target.get("XTRA", xtra))
        return xopq, xtra
    master = master_by_id.get(str(layer.layerId))
    if master is None:
        return None, 0.0
    return pick(list(getattr(master, "axes", None) or []))


#: The axes radii live on. A slanted master sits at the same parametric
#: point as its upright, so it shares the upright's rounding — slant can
#: never fork radii.
PLANE_TAGS = ("XOPQ", "XTRA", "YOPQ")


def _plane_axes(font):
    """[(axis index, tag)] of the parametric axes, in font order."""
    return [(i, str(getattr(ax, "axisTag", ""))) for i, ax in enumerate(font.axes)
            if str(getattr(ax, "axisTag", "")) in PLANE_TAGS]


def _plane_loc(font, glyph_name, layer, master_by_id, plane, targets):
    """The parametric location a layer's radii are evaluated at — its own
    coordinates, with a correction layer's target XOPQ/XTRA merged in,
    exactly as :func:`_layer_stroke` resolves the stroke."""
    coords = None
    attrs = getattr(layer, "attributes", None)
    if attrs is not None:
        try:
            coords = attrs.get("coordinates")
        except AttributeError:
            coords = None
    if isinstance(coords, (list, tuple)):
        loc = {tag: float(coords[i]) for i, tag in plane if i < len(coords)}
        location = {str(getattr(ax, "axisTag", "")): float(v)
                    for ax, v in zip(font.axes, coords)}
        for rec_loc, target in targets.get(glyph_name, []):
            if all(abs(location.get(k, 1e9) - float(v)) <= 0.51 for k, v in rec_loc.items()):
                loc.update({k: float(v) for k, v in target.items()})
                break
        return loc
    master = master_by_id.get(str(layer.layerId))
    if master is None:
        return None
    axes = list(getattr(master, "axes", None) or [])
    return {tag: float(axes[i]) for i, tag in plane if i < len(axes)}


def _override_deltas(font, masters, overrides, xopq_idx, xtra_idx, outer_rule, inner_rule):
    """A callable (parametric location -> (outer delta, inner delta)) for
    the ``master_overrides`` table. Deltas are pinned: exactly the
    override at an overridden master's parametric point, exactly zero at
    every point that only holds formula masters — so an override can
    never move a master it does not name, and an empty table is a no-op.
    Unknown names and twins that disagree fail loudly."""
    from fontTools.varLib.models import VariationModel

    plane = _plane_axes(font)
    names = set(str(m.name) for m in masters)
    unknown = sorted(set(overrides) - names)
    if unknown:
        raise ValueError(
            "round_corners: master override(s) name no master: %s (masters: %s)"
            % (", ".join(unknown), ", ".join(sorted(names))))
    (o_pct, o_xtra, o_min), (i_pct, i_xtra, i_min) = outer_rule, inner_rule

    pinned = {}   # parametric point -> {"outer": delta, "inner": delta, "by": name}
    order = []
    for m in masters:
        axes = list(getattr(m, "axes", None) or [])
        loc = tuple(float(axes[i]) if i < len(axes) else 0.0 for i, _tag in plane)
        if loc not in pinned:
            pinned[loc] = {}
            order.append(loc)
        ov = overrides.get(str(m.name))
        if not ov:
            continue
        xopq = float(axes[xopq_idx]) if xopq_idx < len(axes) else 0.0
        xtra = float(axes[xtra_idx]) if (xtra_idx is not None and xtra_idx < len(axes)) else 0.0
        want = {}
        if "outer" in ov:
            want["outer"] = float(ov["outer"]) - max(o_pct * xopq + o_xtra * xtra, o_min)
        if "inner" in ov:
            want["inner"] = float(ov["inner"]) - max(i_pct * xopq + i_xtra * xtra, i_min)
        slot = pinned[loc]
        for key, val in want.items():
            if key in slot and abs(slot[key] - val) > 1e-6:
                raise ValueError(
                    "round_corners: '%s' and '%s' sit at the same parametric point "
                    "but override different values — a slanted master shares its "
                    "upright's rounding" % (slot.get("by"), m.name))
            slot[key] = val
        slot["by"] = str(m.name)

    tags = [tag for _i, tag in plane]
    origin = order[0]
    spans = []
    for j in range(len(tags)):
        vals = [loc[j] for loc in order]
        spans.append((min(vals), max(vals)))

    def norm(j, v):
        d = v - origin[j]
        if d > 0:
            span = spans[j][1] - origin[j]
        else:
            span = origin[j] - spans[j][0]
        return d / span if span > 0 else 0.0

    locations = [{tags[j]: norm(j, loc[j]) for j in range(len(tags)) if norm(j, loc[j])}
                 for loc in order]
    model = VariationModel(locations, axisOrder=tags)
    outer_deltas = [pinned[loc].get("outer", 0.0) for loc in order]
    inner_deltas = [pinned[loc].get("inner", 0.0) for loc in order]

    def at(loc_dict):
        nloc = {}
        for j, tag in enumerate(tags):
            v = max(-1.0, min(1.0, norm(j, float(loc_dict.get(tag, origin[j])))))
            if v:
                nloc[tag] = v
        return (model.interpolateFromMasters(nloc, outer_deltas),
                model.interpolateFromMasters(nloc, inner_deltas))
    return at


def _copy_layer(layer):
    """A deep copy of a layer, detached from its glyph: with the parent
    shared, assigning the copy's layerId registers it with the glyph at
    once, and the twin would leak into the layer collection before it is
    meant to exist. Appending it later re-parents it."""
    import copy
    parent = getattr(layer, "parent", None)
    memo = {id(parent): None} if parent is not None else {}
    return copy.deepcopy(layer, memo)


def extend_for_axis(font, tag, name, end_value, twin_suffix):
    """Grow the font by one studio axis (default 0): every master,
    instance and coordinate layer gains a trailing 0, and every master
    gets a twin at ``end_value`` to hold that axis's end geometry
    (rounding's ROND, slanting's slnt). Returns
    {original master id: twin master}."""
    import copy
    import uuid

    from glyphsLib.classes import GSAxis

    ax = GSAxis()
    ax.name, ax.axisTag = name, tag
    font.axes.append(ax)
    # Right-pad every coordinate vector to the PRE-ADD axis count before
    # appending the new slot. Studio axes are append-only and all
    # default to 0, so a short vector is always missing a suffix of
    # zero-default axes — but the twin builders then write the new
    # axis's value into the LAST slot, and on an under-padded layer that
    # landed one axis early (observed: a slnt -11 twin written into the
    # GRAD slot, which clamped onto the real grade brace's location —
    # corrupting the compiled font's grade/slant interaction and killing
    # export staging with "Locations must be unique").
    n_before = len(font.axes) - 1
    originals = list(font.masters)
    for m in originals:
        ax_list = list(m.axes)
        ax_list += [0.0] * (n_before - len(ax_list))
        m.axes = ax_list + [0.0]
    for inst in list(getattr(font, "instances", None) or []):
        ax_list = list(inst.axes)
        ax_list += [0.0] * (n_before - len(ax_list))
        inst.axes = ax_list + [0.0]
    for glyph in font.glyphs:
        for L in glyph.layers:
            attrs = getattr(L, "attributes", None)
            if attrs is None:
                continue
            try:
                coords = attrs.get("coordinates")
            except AttributeError:
                continue
            if isinstance(coords, (list, tuple)):
                c = list(coords)
                c += [0.0] * (n_before - len(c))
                L.attributes["coordinates"] = c + [0.0]
    twin_of = {}
    for m in originals:
        parent = getattr(m, "font", None)
        memo = {id(parent): parent} if parent is not None else {}
        tm = copy.deepcopy(m, memo)
        tm.id = str(uuid.uuid4()).upper()
        tm.name = "%s %s" % (m.name, twin_suffix)
        tm.axes = list(m.axes)[:-1] + [float(end_value)]
        twin_of[str(m.id)] = tm
        font.masters.append(tm)
    return twin_of


def _extend_for_axis(font, axis_max):
    """Rounding's ROND axis (default 0 = sharp). Returns
    {original master id: twin master}."""
    return extend_for_axis(font, "ROND", "Rounding", axis_max, "Rounded")


def round_font(font, params: dict, targets=None, log=None, axis_max=None) -> dict:
    """Round every corner of ``font`` in place. Returns stats:
    ``corners``, ``layers``, ``collapsed``, ``clamped``, ``skipped_layers``
    (brace layers whose structure does not match the masters', left
    alone and named).

    With ``axis_max``, the rounding becomes a ROND axis instead: the
    original layers keep their sharp geometry (the corner quads collapse
    in place, so both ends interpolate), twin masters and twin braces at
    ``axis_max`` take the rounded geometry, and everything else — braces,
    instances — gains the axis at its default, 0 = sharp."""
    log = log or (lambda _m: None)
    targets = targets or {}
    out_pct = float(params.get("outer_pct", 15.0)) / 100.0
    in_pct = float(params.get("inner_pct", 5.0)) / 100.0
    out_xtra = float(params.get("outer_xtra_pct", 3.0)) / 100.0
    in_xtra = float(params.get("inner_xtra_pct", 1.0)) / 100.0
    out_min = float(params.get("outer_min", 2.0))
    in_min = float(params.get("inner_min", 1.0))
    stats = {"corners": 0, "layers": 0, "collapsed": 0, "clamped": 0, "hidden": 0,
             "ambiguous": 0, "concentric": 0, "overrides": 0, "twins": 0,
             "skipped_layers": []}

    # Guardrail, not a silent no-op: without a stroke axis the radius
    # formula has nothing to follow, and "enabled but sharp" is exactly
    # the state that must not pass as ok. The raise surfaces as the
    # transforms PUT rejection (requires_axis_tags) or, on paths that
    # bypass it, as health.source_transform_error. XTRA is optional —
    # its share of the blend is simply zero without the axis.
    xopq_idx = xopq_index(font)
    if xopq_idx is None:
        tags = ", ".join(str(getattr(ax, "axisTag", "?")) for ax in font.axes) or "none"
        raise ValueError(
            "round corners needs the parametric stroke axis XOPQ (the radius "
            f"formula blends it with XTRA) — this source declares: {tags}")
    xtra_idx = _axis_index(font, "XTRA")
    masters = list(font.masters)
    master_by_id = {str(m.id): m for m in masters}
    plane = _plane_axes(font)
    over = params.get("master_overrides") or {}
    deltas_at = _override_deltas(font, masters, over, xopq_idx, xtra_idx,
                                 (out_pct, out_xtra, out_min),
                                 (in_pct, in_xtra, in_min)) if over else None
    stats["overrides"] = len(over)
    twin_master = {}
    if axis_max is not None:
        axis_max = float(axis_max)
        if axis_max <= 0:
            raise ValueError("round_corners: the ROND axis maximum must be positive")
        twin_master = _extend_for_axis(font, axis_max)
    # glyphsLib is imported lazily, as everywhere else in this package.
    import uuid

    from glyphsLib.classes import GSNode

    for glyph in font.glyphs:
        master_layers = [glyph.layers[m.id] for m in masters if glyph.layers[m.id] is not None]
        if not master_layers:
            continue
        # In axis mode every interpolating layer gets a twin — master
        # layers always (a master needs a layer in every glyph), braces
        # when present. The twin is a sharp copy for now; the apply step
        # rounds it. Backups (no coordinates, not a master) stay single.
        rond_twins = {}
        if axis_max is not None:
            for L in list(glyph.layers):
                lid = str(L.layerId)
                attrs = getattr(L, "attributes", None)
                coords = None
                if attrs is not None:
                    try:
                        coords = attrs.get("coordinates")
                    except AttributeError:
                        coords = None
                if lid in master_by_id:
                    twin = _copy_layer(L)
                    tm = twin_master[lid]
                    twin.layerId = tm.id
                    twin.associatedMasterId = tm.id
                elif isinstance(coords, (list, tuple)):
                    twin = _copy_layer(L)
                    twin.layerId = str(uuid.uuid4()).upper()
                    assoc = str(getattr(L, "associatedMasterId", ""))
                    if assoc in twin_master:
                        twin.associatedMasterId = twin_master[assoc].id
                    twin.attributes["coordinates"] = list(coords)[:-1] + [axis_max]
                else:
                    continue
                rond_twins[id(L)] = twin

        # Called only within the loop iteration that defines it (never
        # deferred), so the loop-variable capture is same-iteration and safe.
        def _keep_twins():
            for twin in rond_twins.values():  # noqa: B023
                glyph.layers.append(twin)  # noqa: B023
            stats["twins"] += len(rond_twins)  # noqa: B023

        ref_structure = _structure(master_layers[0])
        if any(_structure(L) != ref_structure for L in master_layers[1:]):
            # Masters that do not interpolate cannot be rounded in step.
            stats["skipped_layers"].append("%s (masters differ)" % glyph.name)
            _keep_twins()
            continue
        # Every layer that interpolates: the masters and the coordinate
        # braces. Backups (no coordinates, not a master layer) stay.
        layers = list(master_layers)
        for layer in glyph.layers:
            attrs = getattr(layer, "attributes", None)
            has_coords = attrs is not None and isinstance(
                (attrs.get("coordinates") if hasattr(attrs, "get") else None), (list, tuple))
            if has_coords and layer not in layers:
                if _structure(layer) != ref_structure:
                    stats["skipped_layers"].append("%s/%s" % (glyph.name, layer.name))
                    continue
                layers.append(layer)

        # The corners, decided on the MASTER layers so the same corners
        # gain nodes in every layer: a corner is a LINE node whose
        # outgoing segment is a line too (the next node is a line), with
        # no zero-length segment beside it in any master. A node on a
        # curve — an existing round included — is not a corner, which is
        # also what makes a second pass find nothing left to do.
        path_corners = {}   # path index -> [corner node indices]
        for pi in range(len(ref_structure)):
            types = ref_structure[pi][1]
            n = len(types)
            skip = set(i for i in range(n)
                       if not (types[i] == "line" and types[(i + 1) % n] == "line"))
            for L in master_layers:
                P = _pts(L.paths[pi])
                for i in range(n):
                    if math.hypot(P[(i + 1) % n][0] - P[i][0], P[(i + 1) % n][1] - P[i][1]) < 1e-9:
                        skip.add(i)
                        skip.add((i + 1) % n)
            corners = [i for i in range(n) if i not in skip]
            if corners:
                path_corners[pi] = corners
                stats["corners"] += len(corners)

        # Plan per layer across ALL paths before touching any of them, so
        # an ink-concave corner can find its wall partner on another
        # contour (an O's counter pairs with the outer contour).
        plans = {}   # (id(layer), path index) -> {node index: (t, k)}
        for L in layers:
            w, xt = _layer_stroke(font, glyph.name, L, master_by_id,
                                  xopq_idx, xtra_idx, targets)
            if w is None:
                w = float(masters[0].axes[xopq_idx])
            d_out = d_in = 0.0
            if deltas_at is not None:
                loc = _plane_loc(font, glyph.name, L, master_by_id, plane, targets)
                if loc is not None:
                    d_out, d_in = deltas_at(loc)
            polys = [_pts(p) for p in L.paths]
            outers = []   # (x, y, effective radius, turn) of live outer rounds
            inners = []   # concave corners, radius deferred until pairing
            for pi, corners in path_corners.items():
                P = polys[pi]
                n = len(P)
                for i in corners:
                    a, b, c = P[(i - 1) % n], P[i], P[(i + 1) % n]
                    v1 = (b[0] - a[0], b[1] - a[1])
                    v2 = (c[0] - b[0], c[1] - b[1])
                    l1 = math.hypot(*v1)
                    l2 = math.hypot(*v2)
                    cross = v1[0] * v2[1] - v1[1] * v2[0]
                    dot = v1[0] * v2[0] + v1[1] * v2[1]
                    phi = abs(math.degrees(math.atan2(cross, dot)))
                    if phi < MIN_TURN_DEG or l1 < 1e-9 or l2 < 1e-9:
                        plans.setdefault((id(L), pi), {})[i] = (0.0, 0.0)
                        continue
                    # What renders around the corner decides, not contour
                    # identity: Crispy forms counters by overlap (a single
                    # self-crossing contour, a shell pair, a folded counter
                    # path), and there contour direction or the winding of
                    # OTHER contours says nothing reliable. A corner whose
                    # neighborhood renders as all ink is construction, not a
                    # visible corner (R's leg in its stem, a fold flap):
                    # rounding it pulls the outline back and lets white show
                    # through — or bites the counter. Where another master
                    # pulls the pieces apart, the same corner rounds there.
                    gap = min(_seg_dist(b, q[j], q[(j + 1) % len(q)])
                              for qi, q in enumerate(polys) for j in range(len(q))
                              if not (qi == pi and (j == i or j == (i - 1) % n)))
                    eps = min(PROBE_MAX, 0.25 * gap)
                    if eps < PROBE_MIN:
                        plans.setdefault((id(L), pi), {})[i] = (0.0, 0.0)
                        stats["ambiguous"] += 1
                        continue
                    cls = _white_span(b, (v1[0] / l1, v1[1] / l1),
                                      (v2[0] / l2, v2[1] / l2), eps, polys)
                    if cls in ("hidden", "ambiguous"):
                        plans.setdefault((id(L), pi), {})[i] = (0.0, 0.0)
                        stats[cls] += 1
                        continue
                    if cls == "outer":
                        r = max(max(out_pct * w + out_xtra * xt, out_min) + d_out, 0.0)
                        t, k, r_eff = _fit(r, phi, l1, l2, stats)
                        plans.setdefault((id(L), pi), {})[i] = (t, k)
                        if t > 0:
                            outers.append((b[0], b[1], r_eff, phi))
                    else:
                        inners.append((pi, i, b, phi, l1, l2,
                                       max(max(in_pct * w + in_xtra * xt, in_min) + d_in, 0.0)))
            for pi, i, b, phi, l1, l2, r in inners:
                # A concave corner within an outer round's radius, reached
                # THROUGH ink, is that corner's wall partner: on a stroke
                # thinner than the round, the counter share would let the
                # outer arc cut across and evert the wall. Concentric
                # instead: the outer's radius less the wall thickness.
                # Bold layers put the partner beyond the radius, so their
                # counters keep the small share; a nearer convex corner
                # across white space is not a wall and does not pair.
                for ox, oy, r_o, phi_o in sorted(
                        outers, key=lambda o: (o[0] - b[0]) ** 2 + (o[1] - b[1]) ** 2):
                    d = math.hypot(ox - b[0], oy - b[1])
                    if d >= r_o:
                        continue
                    mid = ((ox + b[0]) / 2.0, (oy + b[1]) / 2.0)
                    if sum(_winding(mid, q) for q in polys) == 0:
                        continue
                    wall = d * math.cos(math.radians(phi_o) / 2.0)
                    if r_o - wall > r:
                        r = r_o - wall
                        stats["concentric"] += 1
                    break
                t, k, _r_eff = _fit(r, phi, l1, l2, stats)
                plans.setdefault((id(L), pi), {})[i] = (t, k)

        for pi, corners in path_corners.items():
            n = len(ref_structure[pi][1])
            for L in layers:
                plan_map = plans.get((id(L), pi), {})
                if axis_max is None:
                    jobs = [(L, plan_map)]
                else:
                    # the twin takes the round; the original keeps the
                    # corner, as a collapsed quad, so both ends interpolate
                    jobs = [(rond_twins[id(L)], plan_map), (L, {})]
                for tgt, pm in jobs:
                    path = tgt.paths[pi]
                    P = _pts(path)
                    new = []
                    for i in range(n):
                        node = path.nodes[i]
                        if i not in corners:
                            new.append(node)
                            continue
                        t, k = pm.get(i, (0.0, 0.0))
                        a, b, c = P[(i - 1) % n], P[i], P[(i + 1) % n]
                        d1 = (b[0] - a[0], b[1] - a[1])
                        l1 = math.hypot(*d1) or 1.0
                        d2 = (c[0] - b[0], c[1] - b[1])
                        l2 = math.hypot(*d2) or 1.0
                        u1 = (d1[0] / l1, d1[1] / l1)
                        u2 = (d2[0] / l2, d2[1] / l2)
                        T1 = (_snap(b[0] - t * u1[0]), _snap(b[1] - t * u1[1]))
                        T2 = (_snap(b[0] + t * u2[0]), _snap(b[1] + t * u2[1]))
                        H1 = (_snap(T1[0] + k * u1[0]), _snap(T1[1] + k * u1[1]))
                        H2 = (_snap(T2[0] - k * u2[0]), _snap(T2[1] - k * u2[1]))
                        for pos, kind, smooth in ((T1, "line", True), (H1, "offcurve", False),
                                                  (H2, "offcurve", False), (T2, "curve", True)):
                            nd = GSNode(pos, kind)
                            nd.smooth = smooth
                            new.append(nd)
                    path.nodes = new
        if axis_max is not None:
            _keep_twins()
        stats["layers"] += len(layers)
    if stats["skipped_layers"]:
        log("round_corners: %d brace layer(s) do not match the masters' structure "
            "and were left alone: %s" % (len(stats["skipped_layers"]),
                                         ", ".join(stats["skipped_layers"][:8])))
    return stats
