# Copyright 2026 Agyei Archer. Licensed under the Apache License, Version 2.0.
# Origin: the GSLayer/GSPath adapters of GlyphAudit/proof/panel/slant_panel.py
# in agyeiagyeiagyei/docrepairtools (same author), extracted for
# avar2-studio's Slant Master tool.
"""GSPath/GSNode adapters for the extrema pipeline, and the per-layer slant.

Duck-typed on ``layer.paths`` / ``path.nodes`` / ``node.position`` (the
way CornerRadii's ``cornerfit.py`` is) so the module imports headless; the
one Glyphs import (GSNode, for the rebuild fallback) is deferred.

Only **X extrema** are fixed: under the horizontal shear of the slant,
dy/dt roots don't move, so top/bottom extrema are preserved and only
left/right (3/9 o'clock) points drift off the true extreme.

Private GlyphsCore selectors (``insertNodeWithPathTime:``,
``removeNode:``, ``removeNodes:``) are used when the path responds to
them and replaced by a node-list rebuild otherwise.
"""

from __future__ import annotations

import math
import traceback

from slant_extrema import (
    balance_extremum_handles,
    cubic_endpoint_tangents,
    cubic_point,
    cubic_x_extrema_ts,
    fit_merged_segment,
    is_vertical_tangent,
    subdivide_cubic_multi,
)
from slant_math import transform_point

#: Deviation budget (font units) for the least-squares merge fit when
#: removing a stale extremum node between two cubics. Above this the old
#: node is kept and reported. (Measured 0.19 on a test circle.)
EXTREMA_GATE = 1.0

#: Position-match tolerance when re-finding recorded extremum nodes after
#: the transform. Covers integer-grid rounding (grid spacing ≤ 1).
POS_TOL = 0.75


def _noop(_msg):
    pass


def _responds(obj, selector):
    try:
        return bool(obj.respondsToSelector_(selector))
    except Exception:
        return hasattr(obj, selector.replace(":", "_"))


def _node_pos(node):
    p = node.position
    return (float(p.x), float(p.y))


def _set_node_pos(node, pt):
    try:
        node.position = (float(pt[0]), float(pt[1]))
    except Exception:
        from Foundation import NSMakePoint
        node.setPosition_(NSMakePoint(float(pt[0]), float(pt[1])))


def _is_oncurve(node):
    return str(node.type) != "offcurve"


def _path_cubic_segments(nodes, closed):
    """Cubic segments of a path as 4-node tuples (on, off, off, on),
    wrapping around for closed paths. Works on a single snapshot of the
    node list so proxy identity (`is`) is usable for lookups."""
    oncurve = [i for i, nd in enumerate(nodes) if _is_oncurve(nd)]
    if not oncurve:
        return []
    pairs = list(zip(oncurve, oncurve[1:]))
    if closed and len(oncurve) > 1:
        pairs.append((oncurve[-1], oncurve[0]))
    segments = []
    for i, j in pairs:
        between = nodes[i + 1:j] if j > i else nodes[i + 1:] + nodes[:j]
        if len(between) == 2:
            segments.append((nodes[i], between[0], between[1], nodes[j]))
    return segments


def _path_segments(nodes, closed):
    """All segments of a path as node tuples: 4-node cubics or 2-node
    lines (quadratic/1-offcurve segments are skipped). Same snapshot
    semantics as `_path_cubic_segments`."""
    oncurve = [i for i, nd in enumerate(nodes) if _is_oncurve(nd)]
    if not oncurve:
        return []
    pairs = list(zip(oncurve, oncurve[1:]))
    if closed and len(oncurve) > 1:
        pairs.append((oncurve[-1], oncurve[0]))
    segments = []
    for i, j in pairs:
        between = nodes[i + 1:j] if j > i else nodes[i + 1:] + nodes[:j]
        if len(between) == 2:
            segments.append((nodes[i], between[0], between[1], nodes[j]))
        elif len(between) == 0:
            segments.append((nodes[i], nodes[j]))
    return segments


def _seg_tangent_at(seg, at_end):
    """Tangent vector of a segment (cubic or line) at its end (or start)."""
    pts = [_node_pos(nd) for nd in seg]
    if len(pts) == 4:
        t0, t1 = cubic_endpoint_tangents(pts)
        return t1 if at_end else t0
    return (pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])


def _seg_points(seg):
    return tuple(_node_pos(nd) for nd in seg)


def _set_nodes(path, nodes):
    """Replace a path's node list (settable property, confirmed in the
    GlyphsCore headers)."""
    path.nodes = nodes


def _remove_nodes(path, snapshot, doomed):
    """Remove `doomed` (nodes from `snapshot`, a list(path.nodes) taken
    for this mutation) — private selectors first, node-list rebuild
    otherwise."""
    if _responds(path, "removeNodes:"):
        path.removeNodes_(list(doomed))
        return
    if _responds(path, "removeNode:"):
        for nd in doomed:
            path.removeNode_(nd)
        return
    _set_nodes(path, [nd for nd in snapshot if all(nd is not d for d in doomed)])


def record_x_extrema(layer):
    """Positions of on-curve nodes that are true X extrema (vertical
    tangent at a cubic segment endpoint) BEFORE the transform."""
    recorded = []
    for path in layer.paths:
        nodes = list(path.nodes)
        for seg in _path_cubic_segments(nodes, bool(path.closed)):
            t0, t1 = cubic_endpoint_tangents(_seg_points(seg))
            if is_vertical_tangent(t0, tol=0.0):
                recorded.append(_node_pos(seg[0]))
            if is_vertical_tangent(t1, tol=0.0):
                recorded.append(_node_pos(seg[3]))
    return recorded


def insert_new_x_extrema(layer, log=None):
    """Subdivide cubic segments at post-transform X extrema. Returns
    (inserted_count, inserted_positions) — the positions feed the
    handle-harmonization pass.

    Two strategies per path:
    1. GSPath.insertNodeWithPathTime: (keep-shape insertion), verified by
       its return value — it returns nil on failure WITHOUT raising.
    2. Fallback: rebuild the whole node list with our own de Casteljau
       subdivision.
    """
    log = log or _noop
    inserted = 0
    positions = []
    for path in list(layer.paths):
        nodes = list(path.nodes)
        closed = bool(path.closed)

        # (end_node_index, t) ops on the raw snapshot. pathTime uses the
        # index of the segment's END on-curve node + t: insert(3.5) on a
        # 12-node circle subdivides the segment ENDING at node 3.
        ops = []
        for seg in _path_cubic_segments(nodes, closed):
            end_idx = nodes.index(seg[3])
            cubic_pts = _seg_points(seg)
            for t in cubic_x_extrema_ts(cubic_pts):
                ops.append((end_idx, float(t)))
                positions.append(cubic_point(cubic_pts, t))
        if not ops:
            continue

        ok = _responds(path, "insertNodeWithPathTime:")
        if ok:
            for end_idx, t in sorted(ops, key=lambda op: op[0] + op[1], reverse=True):
                try:
                    new_node = path.insertNodeWithPathTime_(end_idx + t)
                    if new_node is None:
                        ok = False
                        break
                except Exception:
                    ok = False
                    log("insertNodeWithPathTime: failed — rebuilding the path\n" + traceback.format_exc())
                    break
        if ok:
            inserted += len(ops)
            continue

        # Fallback: rebuild the node list manually. Rotate closed paths so
        # the list starts on-curve (rotation preserves the shape and makes
        # the rebuild pass linear), then recompute ops against the rotated
        # indices.
        try:
            nodes = list(path.nodes)  # fresh: a partial insert may have landed
            if closed:
                oncurve = [i for i, nd in enumerate(nodes) if _is_oncurve(nd)]
                if not oncurve:
                    continue
                k = oncurve[0]
                if k:
                    nodes = nodes[k:] + nodes[:k]
            rot_ops = []
            for seg in _path_cubic_segments(nodes, closed):
                start_idx = nodes.index(seg[0])
                for t in cubic_x_extrema_ts(_seg_points(seg)):
                    rot_ops.append((start_idx, float(t)))
            inserted += _rebuild_path_with_extrema(path, nodes, closed, rot_ops)
        except Exception:
            log("extrema insertion failed for a path; left unchanged\n" + traceback.format_exc())
    return inserted, positions


def _make_node(pt, node_type):
    from GlyphsApp import GSNode
    try:
        n = GSNode()
    except Exception:
        n = GSNode.alloc().init()
    _set_node_pos(n, pt)
    n.type = node_type  # "curve" / "offcurve" (wrapper coerces)
    return n


def _rebuild_path_with_extrema(path, nodes, closed, ops):
    """Rebuild `path.nodes` with the segments named in `ops` subdivided by
    our own solver. `nodes` must be the (rotated, for closed paths)
    pre-insertion snapshot. Returns nodes added."""
    ops_by_start = {}
    for start_idx, t in ops:
        ops_by_start.setdefault(start_idx, []).append(t)

    n = len(nodes)
    oncurve = [i for i, nd in enumerate(nodes) if _is_oncurve(nd)]
    if not oncurve:
        raise ValueError("path has no on-curve nodes")
    pairs = list(zip(oncurve, oncurve[1:]))
    if closed:
        pairs.append((oncurve[-1], n))  # j == n → end node is nodes[0]

    new_nodes = []
    added = 0
    for i, j in pairs:
        new_nodes.append(nodes[i])
        between = nodes[i + 1:j]
        end_node = nodes[j] if j < n else nodes[0]
        ts = ops_by_start.get(i)
        if ts and len(between) == 2:
            cubic = (
                _node_pos(nodes[i]), _node_pos(between[0]),
                _node_pos(between[1]), _node_pos(end_node),
            )
            pieces = subdivide_cubic_multi(cubic, ts)
            for piece in pieces[:-1]:
                new_nodes.append(_make_node(piece[1], "offcurve"))
                new_nodes.append(_make_node(piece[2], "offcurve"))
                new_nodes.append(_make_node(piece[3], "curve"))
                added += 1
            new_nodes.append(_make_node(pieces[-1][1], "offcurve"))
            new_nodes.append(_make_node(pieces[-1][2], "offcurve"))
        else:
            new_nodes.extend(between)
    if not closed:
        new_nodes.extend(nodes[oncurve[-1]:])

    _set_nodes(path, new_nodes)
    return added


def new_extrema_positions(layer, matrix):
    """Font-unit positions where new X-extremum nodes would be inserted on
    `layer` after transforming by `matrix`. Pure computation; the layer
    is not touched."""
    positions = []
    for path in layer.paths:
        nodes = list(path.nodes)
        for seg in _path_cubic_segments(nodes, bool(path.closed)):
            cubic = tuple(transform_point(matrix, *_node_pos(nd)) for nd in seg)
            for t in cubic_x_extrema_ts(cubic):
                positions.append(cubic_point(cubic, t))
    return positions


def remove_stale_x_extrema(layer, recorded_pts, matrix, gate=EXTREMA_GATE, log=None):
    """Delete pre-slant X-extremum nodes that are no longer extremal.

    Policy, established by headless measurement:
    - cubic+cubic: merge with OUR least-squares fit (0.19-unit deviation
      on a test circle, clean handles) — never Glyphs' keep-shape refit,
      which stretches handles.
    - curve+line join (arch-to-stem): KEEP the node. Covering a curve
      plus a straight stem with one cubic is inherently lossy; a smooth
      post-slant join node is harmless.
    - line+line corner: plain removal (exact).

    Returns {"removed", "gated", "line_kept", "removed_positions",
    "kept_positions"}.
    """
    log = log or _noop
    out = {"removed": 0, "gated": 0, "line_kept": 0,
           "removed_positions": [], "kept_positions": []}
    if not recorded_pts:
        return out
    expected = [transform_point(matrix, x, y) for x, y in recorded_pts]
    for path in list(layer.paths):
        # Rescan after every mutation: a removal changes the node list, so
        # segment snapshots go stale (and two recorded extrema can share a
        # segment). Bounded: every iteration either removes a node or
        # marks one processed.
        processed = []
        while True:
            nodes = list(path.nodes)
            segments = _path_segments(nodes, bool(path.closed))
            nd = None
            for cand in nodes:
                if not _is_oncurve(cand):
                    continue
                cx, cy = _node_pos(cand)
                if any(abs(cx - ex) <= POS_TOL and abs(cy - ey) <= POS_TOL
                       for ex, ey in expected) and not any(
                       abs(cx - px) <= POS_TOL and abs(cy - py) <= POS_TOL
                       for px, py in processed):
                    nd = cand
                    break
            if nd is None:
                break
            pos = _node_pos(nd)
            processed.append(pos)
            prev_seg = next((s for s in segments if s[-1] is nd), None)
            next_seg = next((s for s in segments if s[0] is nd), None)
            # Still extremal if either adjacent segment keeps a (near-)
            # vertical tangent at this node — leave it alone.
            still_extremal = False
            if prev_seg is not None:
                still_extremal |= is_vertical_tangent(_seg_tangent_at(prev_seg, True))
            if next_seg is not None:
                still_extremal |= is_vertical_tangent(_seg_tangent_at(next_seg, False))
            if still_extremal:
                continue
            if prev_seg is not None and next_seg is not None:
                if len(prev_seg) < 4 or len(next_seg) < 4:
                    if len(prev_seg) == 2 and len(next_seg) == 2:
                        try:
                            _remove_nodes(path, nodes, [nd])
                            out["removed"] += 1
                            out["removed_positions"].append(pos)
                        except Exception:
                            log("node removal failed\n" + traceback.format_exc())
                            out["gated"] += 1
                            out["kept_positions"].append(pos)
                    else:
                        out["line_kept"] += 1
                        out["kept_positions"].append(pos)
                    continue
                fit = fit_merged_segment(_seg_points(prev_seg), _seg_points(next_seg))
                if fit is None or fit[2] > gate:
                    out["gated"] += 1
                    out["kept_positions"].append(pos)
                    continue
                h1, h2, _dev = fit
                try:
                    _set_node_pos(prev_seg[1], h1)   # prev's entry handle
                    _set_node_pos(next_seg[2], h2)   # next's exit handle
                    _remove_nodes(path, nodes, [nd, prev_seg[2], next_seg[1]])
                    out["removed"] += 1
                    out["removed_positions"].append(pos)
                except Exception:
                    log("merge removal failed\n" + traceback.format_exc())
                    out["gated"] += 1
                    out["kept_positions"].append(pos)
                continue
            # Node adjacent to a quadratic segment (skipped by
            # _path_segments): leave it rather than guessing.
            out["gated"] += 1
            out["kept_positions"].append(pos)
    return out


def harmonize_new_extrema(layer, inserted_positions, gate=4.0, log=None):
    """Balance the handle pairs adjacent to each newly inserted extremum
    node (snap vertical, equalize length within the deviation budget).
    Returns the number of nodes harmonized."""
    log = log or _noop
    balanced = 0
    if not inserted_positions:
        return balanced
    for path in list(layer.paths):
        nodes = list(path.nodes)
        segments = _path_cubic_segments(nodes, bool(path.closed))
        targets = [
            nd for nd in nodes
            if _is_oncurve(nd)
            and any(
                abs(_node_pos(nd)[0] - ex) <= 0.5
                and abs(_node_pos(nd)[1] - ey) <= 0.5
                for ex, ey in inserted_positions
            )
        ]
        for nd in targets:
            prev_seg = next((s for s in segments if s[3] is nd), None)
            next_seg = next((s for s in segments if s[0] is nd), None)
            if prev_seg is None or next_seg is None:
                continue
            result = balance_extremum_handles(
                _seg_points(prev_seg), _seg_points(next_seg), gate=gate,
            )
            if result is None:
                continue
            new_prev, new_next = result
            try:
                _set_node_pos(prev_seg[2], new_prev[2])
                _set_node_pos(next_seg[1], new_next[1])
                balanced += 1
            except Exception:
                log("handle harmonization failed\n" + traceback.format_exc())
    return balanced


def _is_identity(linear, tol=1e-6):
    return (abs(linear[0] - 1) < tol and abs(linear[1]) < tol
            and abs(linear[2]) < tol and abs(linear[3] - 1) < tol)


def _capture_components(layer, stats, log):
    """Components that stay attached through the shear. A component with
    a non-identity linear part (flips, rotations) cannot be expressed
    against its already-slanted base, so it is decomposed and counted."""
    kept = []
    for comp in list(getattr(layer, "components", None) or []):
        try:
            t = tuple(float(v) for v in comp.transform)
        except Exception:
            t = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        if _is_identity(t[:4]):
            kept.append((comp, t))
            continue
        try:
            comp.decompose()
            stats["components_decomposed"] += 1
        except Exception:
            log("component decompose failed\n" + traceback.format_exc())
            kept.append((comp, t))
    return kept


def _snap(value, grid):
    """The multiple of `grid` nearest to `value`; a half goes up, which
    is how Glyphs rounds coordinates."""
    return math.floor(value / grid + 0.5) * grid


def round_to_grid(layer, grid):
    """Put every node and anchor of `layer` on a multiple of `grid`
    (font units). A shear leaves them between grid points, and Glyphs
    rounds a layer only when something moves it afterwards."""
    points = [nd for path in layer.paths for nd in path.nodes]
    points += list(getattr(layer, "anchors", None) or [])
    for pt in points:
        x, y = _node_pos(pt)
        target = (_snap(x, grid), _snap(y, grid))
        _set_node_pos(pt, target)
        if _node_pos(pt) != target:
            # Glyphs takes a move too small to notice for no move at all,
            # and leaves 798.99999 where 799 was asked for. Go by way of
            # elsewhere.
            _set_node_pos(pt, (target[0] + 1.0, target[1] + 1.0))
            _set_node_pos(pt, target)


def _restore_components(kept, matrix, grid=0.0):
    """After `applyTransform` composed the shear into each component, put
    back the identity linear part and the sheared TRANSLATION only: the
    base glyph is slanted in the same master, so the composite outline
    S(B + t) = S(B) + L·t needs no second shear and no pivot offset.
    With a `grid` the translation lands on it."""
    for comp, t in kept:
        tx = matrix[0] * t[4] + matrix[2] * t[5]
        ty = matrix[1] * t[4] + matrix[3] * t[5]
        if grid:
            tx, ty = _snap(tx, grid), _snap(ty, grid)
        try:
            comp.transform = (t[0], t[1], t[2], t[3], tx, ty)
        except Exception:
            try:
                comp.position = (tx, ty)
            except Exception:
                pass


def ink_span(layer, angle_deg=0.0, pivot_y=0.0):
    """(left, right) of the layer's ink measured along a slant: the x
    extent of the outline once it is un-slanted by `angle_deg` around
    `pivot_y`. That is how Glyphs measures the sidebearings of a master
    with an italic angle, the pivot being half its x-height (measured on
    3.5.1: any other height is tens of units out). Angle 0 gives the
    upright extent. None for a layer without outlines.

    Read from the nodes, so it answers for a detached copy too — Glyphs
    reports empty `bounds` for a layer no glyph holds. The extremes of
    cubic segments are found on the curve; a quadratic segment counts
    with its on-curve nodes only.
    """
    tan = math.tan(math.radians(angle_deg))

    def upright(pt):
        return (pt[0] - tan * (pt[1] - pivot_y), pt[1])

    xs = []
    for path in layer.paths:
        nodes = list(path.nodes)
        xs += [upright(_node_pos(nd))[0] for nd in nodes if _is_oncurve(nd)]
        for seg in _path_cubic_segments(nodes, bool(path.closed)):
            cubic = tuple(upright(_node_pos(nd)) for nd in seg)
            xs += [cubic_point(cubic, t)[0] for t in cubic_x_extrema_ts(cubic)]
    if not xs:
        return None
    return (min(xs), max(xs))


def layer_structure(layer):
    """Per path: closed flag and node types in list order — what has to
    match between two masters for them to interpolate. Equal node COUNTS
    are not enough: an extremum inserted and another removed can leave
    the count alone and still move the start node."""
    return [(bool(path.closed), tuple(str(nd.type) for nd in path.nodes))
            for path in layer.paths]


def slant_layer(layer, matrix, fix_extrema=False, keep_components=False, log=None, grid=0.0):
    """Shear `layer` in place by `matrix` and, optionally, repair its X
    extrema. `keep_components` leaves components attached (their
    translation is re-derived; see `_restore_components`); otherwise the
    caller has decomposed the layer already. With a `grid` (font units,
    0 for none) the result is rounded to it, last of all.

    Returns a stats dict: inserted, removed, gated, balanced, line_kept,
    components_decomposed, structure_changed (the extrema pass left the
    paths with another `layer_structure`), and the positions for an
    overlay — inserted_positions, removed_positions, kept_positions.
    """
    log = log or _noop
    stats = {"inserted": 0, "removed": 0, "gated": 0, "balanced": 0,
             "line_kept": 0, "components_decomposed": 0, "structure_changed": False,
             "inserted_positions": [], "removed_positions": [], "kept_positions": []}
    kept = _capture_components(layer, stats, log) if keep_components else []
    recorded = record_x_extrema(layer) if fix_extrema else []
    structure = layer_structure(layer) if fix_extrema else None
    layer.applyTransform(matrix)
    if kept:
        _restore_components(kept, matrix, grid)
    if fix_extrema:
        ins, positions = insert_new_x_extrema(layer, log)
        stats["inserted"] += ins
        stats["inserted_positions"] = positions
        rem = remove_stale_x_extrema(layer, recorded, matrix, log=log)
        for k in ("removed", "gated", "line_kept"):
            stats[k] += rem[k]
        stats["removed_positions"] = rem["removed_positions"]
        stats["kept_positions"] = rem["kept_positions"]
        stats["balanced"] += harmonize_new_extrema(layer, positions, log=log)
        stats["structure_changed"] = layer_structure(layer) != structure
    if grid:
        round_to_grid(layer, grid)
    return stats


def extrema_summary(stats):
    """One-line report of an extrema pass, or "" when nothing ran."""
    if not any(stats.get(k) for k in ("inserted", "removed", "gated", "line_kept", "balanced")):
        return ""
    msg = "extrema +%d −%d" % (stats["inserted"], stats["removed"])
    if stats.get("balanced"):
        msg += ", %d balanced" % stats["balanced"]
    if stats.get("gated"):
        msg += ", %d kept (shape gate)" % stats["gated"]
    if stats.get("line_kept"):
        msg += ", %d kept (line joins)" % stats["line_kept"]
    return msg
