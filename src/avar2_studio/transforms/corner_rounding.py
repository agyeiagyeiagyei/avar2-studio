"""Corner rounding — the geometry behind the ``round_corners`` transform.

Inserts a circular round at every line-line corner of a ``.glyphs``
font, IN EVERY LAYER THAT INTERPOLATES — the masters and every brace
layer (drawn corrections, computed control-axis seeds, grade braces) —
so the layers stay point-compatible: one rounded corner is one on-curve
node replaced by four (tangent, two handles, tangent) everywhere.

The RADIUS is per layer, proportional to the stroke it represents:

  outer corners (ink-convex)   ``outer_pct`` % of the layer's XOPQ
  inner corners (ink-concave)  ``inner_pct`` % of it

with floors for the light end, clamped to the room the corner's shorter
segment leaves, collapsing to the corner point where a hairline leaves
none. Corners are classified per layer, because counters can evert
between masters. A correction layer draws the outline "as if at"
another parametric point, so its radius follows its TARGET's XOPQ when
the control sidecar declares one.

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


def _pts(path) -> List[Tuple[float, float]]:
    return [(float(n.position.x), float(n.position.y)) for n in path.nodes]

def _area(p) -> float:
    return sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1]
               for i in range(len(p))) / 2.0

def _inside(pt, poly) -> bool:
    x, y = pt
    c = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c

def _snap(v: float) -> float:
    return float(math.floor(v + 0.5))

def _structure(layer):
    return [(bool(p.closed), tuple(str(n.type) for n in p.nodes)) for p in layer.paths]


def xopq_index(font) -> Optional[int]:
    for i, ax in enumerate(font.axes):
        if str(getattr(ax, "axisTag", "")).upper() == "XOPQ":
            return i
    return None


def control_targets(control_sidecar: Optional[dict]) -> Dict[str, List[Tuple[dict, float]]]:
    """glyph name -> [(location, target XOPQ)] from a ``-control.json``
    payload (``control_axes.load``). Only layers that declare a target
    with an XOPQ appear — the rest round by their own location."""
    out: Dict[str, List[Tuple[dict, float]]] = {}
    for ax in (control_sidecar or {}).get("axes", []):
        for rec in ax.get("layers", []):
            target = rec.get("target") or {}
            if "XOPQ" in target:
                out.setdefault(str(rec.get("glyph")), []).append(
                    (dict(rec.get("location") or {}), float(target["XOPQ"])))
    return out


def _layer_xopq(font, glyph_name, layer, master_by_id, xopq_idx, targets) -> Optional[float]:
    """The stroke weight a layer's drawing represents."""
    coords = None
    attrs = getattr(layer, "attributes", None)
    if attrs is not None:
        try:
            coords = attrs.get("coordinates")
        except AttributeError:
            coords = None
    if isinstance(coords, (list, tuple)):
        location = {str(getattr(ax, "axisTag", "")): float(v)
                    for ax, v in zip(font.axes, coords)}
        for rec_loc, target in targets.get(glyph_name, []):
            if all(abs(location.get(k, 1e9) - float(v)) <= 0.51 for k, v in rec_loc.items()):
                return target
        if xopq_idx < len(coords):
            return float(coords[xopq_idx])
        return None
    master = master_by_id.get(str(layer.layerId))
    if master is None:
        return None
    axes = list(getattr(master, "axes", None) or [])
    return float(axes[xopq_idx]) if xopq_idx < len(axes) else None


def round_font(font, params: dict, targets=None, log=None) -> dict:
    """Round every corner of ``font`` in place. Returns stats:
    ``corners``, ``layers``, ``collapsed``, ``clamped``, ``skipped_layers``
    (brace layers whose structure does not match the masters', left
    alone and named)."""
    log = log or (lambda _m: None)
    targets = targets or {}
    out_pct = float(params.get("outer_pct", 15.0)) / 100.0
    in_pct = float(params.get("inner_pct", 5.0)) / 100.0
    out_min = float(params.get("outer_min", 2.0))
    in_min = float(params.get("inner_min", 1.0))
    stats = {"corners": 0, "layers": 0, "collapsed": 0, "clamped": 0, "skipped_layers": []}

    xopq_idx = xopq_index(font)
    if xopq_idx is None:
        log("round_corners: no XOPQ axis — the stroke rule has nothing to follow; nothing rounded")
        return stats
    masters = list(font.masters)
    master_by_id = {str(m.id): m for m in masters}
    # glyphsLib is imported lazily, as everywhere else in this package.
    from glyphsLib.classes import GSNode

    for glyph in font.glyphs:
        master_layers = [glyph.layers[m.id] for m in masters if glyph.layers[m.id] is not None]
        if not master_layers:
            continue
        ref_structure = _structure(master_layers[0])
        if any(_structure(L) != ref_structure for L in master_layers[1:]):
            # Masters that do not interpolate cannot be rounded in step.
            stats["skipped_layers"].append("%s (masters differ)" % glyph.name)
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
            if not corners:
                continue

            plan = {}   # id(layer) -> {node index: (t, k)}
            for L in layers:
                w = _layer_xopq(font, glyph.name, L, master_by_id, xopq_idx, targets)
                if w is None:
                    w = float(masters[0].axes[xopq_idx])
                P = _pts(L.paths[pi])
                s = 1 if _area(P) > 0 else -1
                polys = [_pts(p) for p in L.paths]
                is_counter = sum(1 for qi, q in enumerate(polys)
                                 if qi != pi and _inside(polys[pi][0], q)) % 2 == 1
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
                        plan.setdefault(id(L), {})[i] = (0.0, 0.0)
                        continue
                    outer = ((cross > 0) == (s > 0)) != is_counter
                    r = max(out_pct * w, out_min) if outer else max(in_pct * w, in_min)
                    tan_half = math.tan(math.radians(phi) / 2.0)
                    t = r * tan_half
                    t_max = math.floor(ROOM * min(l1, l2))
                    if t > t_max:
                        t = float(t_max)
                        stats["clamped"] += 1
                    if t < 1.0:
                        plan.setdefault(id(L), {})[i] = (0.0, 0.0)
                        stats["collapsed"] += 1
                        continue
                    r_eff = t / tan_half
                    k = (4.0 / 3.0) * math.tan(math.radians(phi) / 4.0) * r_eff
                    plan.setdefault(id(L), {})[i] = (t, k)

            for L in layers:
                path = L.paths[pi]
                P = _pts(path)
                new = []
                for i in range(n):
                    node = path.nodes[i]
                    if i not in corners:
                        new.append(node)
                        continue
                    t, k = plan.get(id(L), {}).get(i, (0.0, 0.0))
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
            stats["corners"] += len(corners)
        stats["layers"] += len(layers)
    if stats["skipped_layers"]:
        log("round_corners: %d brace layer(s) do not match the masters' structure "
            "and were left alone: %s" % (len(stats["skipped_layers"]),
                                         ", ".join(stats["skipped_layers"][:8])))
    return stats
