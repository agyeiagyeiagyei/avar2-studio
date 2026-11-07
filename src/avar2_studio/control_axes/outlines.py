"""Brace-layer outline serialise/restore and geometry signatures
(split from the ``control_axes`` module, audit §4).
"""

from __future__ import annotations

from typing import Dict, Optional

from ..studio_log import get_logger

log = get_logger(__name__)


def _paths_sig(paths):
    """A comparable signature of an outline's node positions — used to
    tell an unedited seed (identical to the default master) from a
    genuinely drawn outline."""
    return tuple(
        (round(float(n.position.x), 2), round(float(n.position.y), 2))
        for p in (paths or [])
        for n in (getattr(p, "nodes", None) or [])
    )


def glyph_masters_sig(font, glyph_name: str) -> str:
    """Signature of one glyph's MASTER geometry — every master layer's nodes
    and width, in master order.

    Stored on a captured drawing as ``source_sig`` so the studio can say
    whether the masters that drawing was made over have changed since. A
    per-glyph hash is deliberately coarse: it flags any master edit to the
    glyph, not just ones near the layer's location, because the honest
    statement is "the source this was drawn against moved", and a designer
    can judge the rest from the overlay.
    """
    import hashlib

    glyph = font.glyphs[glyph_name] if font is not None else None
    if glyph is None:
        return ""
    parts = []
    for m in font.masters:
        layer = glyph.layers[m.id]
        if layer is None:
            parts.append("-")
            continue
        parts.append(_geometry_sig(layer.paths, layer.width))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _geometry_sig(paths, width) -> str:
    """A short, stable signature of a layer's geometry, stored on the seeded
    brace layer so a later edit can be told apart from an untouched seed."""
    import hashlib

    parts = [f"{float(width or 0):.2f}"]
    for p in paths or []:
        for n in getattr(p, "nodes", None) or []:
            parts.append(f"{float(n.position.x):.2f},{float(n.position.y):.2f}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _parts_to_outline(src_paths, src_components, src_anchors, width) -> Dict:
    """Serialise brace-layer geometry to plain JSON for the sidecar.

    Deliberately a value dump rather than glif XML: it round-trips through
    ``json`` unchanged, so a config bundle carries the drawing verbatim.
    Takes the parts rather than a layer because ``_extract_brace_outlines``
    hands back exactly these lists.
    """
    paths = []
    for p in src_paths or []:
        paths.append({
            "closed": bool(getattr(p, "closed", True)),
            "nodes": [
                [float(n.position.x), float(n.position.y), str(n.type)]
                for n in (getattr(p, "nodes", None) or [])
            ],
        })
    components = []
    for c in src_components or []:
        try:
            # glyphsLib ≥6.16: GSComponent.transform is a plain tuple;
            # earlier versions wrap it in Transform (.value).
            xform = [float(v) for v in getattr(c.transform, "value", c.transform)]
        except Exception:
            xform = None
        entry = {"name": getattr(c, "name", None)}
        if xform:
            entry["transform"] = xform
        components.append(entry)
    anchors = [
        {"name": a.name, "x": float(a.position.x), "y": float(a.position.y)}
        for a in (src_anchors or [])
    ]
    return {
        "width": float(width or 0),
        "paths": paths,
        "components": components,
        "anchors": anchors,
    }


def _outline_to_layer_data(outline) -> Optional[Dict[str, object]]:
    """Inverse of ``_layer_to_outline``: rebuild glyphsLib objects, in the
    shape regenerate_shadow's seeding block expects. Returns None for a
    missing or malformed entry so the caller falls back to seeding."""
    if not isinstance(outline, dict) or outline.get("paths") is None:
        return None
    try:
        from glyphsLib.classes import GSAnchor, GSComponent, GSNode, GSPath
        from glyphsLib.types import Point, Transform

        paths = []
        for p in outline.get("paths") or []:
            gp = GSPath()
            gp.closed = bool(p.get("closed", True))
            for node in p.get("nodes") or []:
                x, y, ntype = node[0], node[1], (node[2] if len(node) > 2 else "line")
                gp.nodes.append(GSNode((float(x), float(y)), ntype))
            paths.append(gp)
        components = []
        for c in outline.get("components") or []:
            if not c.get("name"):
                continue
            gc = GSComponent(c["name"])
            if c.get("transform"):
                gc.transform = Transform(*[float(v) for v in c["transform"]])
            components.append(gc)
        # Anchor positions must be glyphsLib Point objects: a plain tuple
        # serializes as a quoted string ('pos = "(70, 0)"'), which
        # glyphs-reader refuses to parse — a stored outline with anchors
        # then breaks the fontc shadow build.
        def whole(v):
            f = float(v)
            return int(f) if f == int(f) else f
        anchors = [
            GSAnchor(a.get("name"), Point(whole(a.get("x", 0)), whole(a.get("y", 0))))
            for a in (outline.get("anchors") or [])
        ]
        return {
            "paths": paths,
            "components": components,
            "anchors": anchors,
            "width": float(outline.get("width", 0) or 0),
        }
    except Exception as exc:
        log.warning(f"Warning: could not restore a stored outline: {exc}")
        return None
