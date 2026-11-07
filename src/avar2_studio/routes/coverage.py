"""Glyph-coverage route (audit §3 step e group)."""

from typing import List

from flask import Blueprint, jsonify
from glyphsLib import GSFont

from .. import control_axes as _control_axes
from .. import glyph_coverage as _glyph_coverage
from .. import source_font as _source_font
from .. import state
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("coverage", __name__)


@bp.route('/api/glyph-coverage', methods=['GET'])
def glyph_coverage():
    """Return per-axis glyph coverage (v1 read-only for CONTROL AXES).

    Walks brace layers (``.glyphs``) or alternate-master UFOs
    (``.designspace``) and reports, per axis, the names of glyphs
    that contribute variation along that axis. The frontend uses
    ``covers_count / total_glyphs`` + the ``kind`` classification to
    surface axes under the appropriate panel:

      - ``"universal"`` (100% coverage) → stays under AVAR2 MAPPINGS /
                                          parametric.
      - ``"scoped"``    (anything less) → CONTROL AXES.

    Response shape::

        {
          "axes": [
            {
              "tag": "XOPQ",
              "name": "X-Opacity",
              "covers": ["A", "B", ..., "z"],
              "covers_count": 245,
              "total_glyphs": 245,
              "kind": "universal"
            },
            ...
          ]
        }
    """
    if state.GLYPHS_PATH is None:
        return jsonify({"axes": []})
    try:
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
        coverage = _glyph_coverage.compute_coverage(font)

        # Pair the coverage data with axis names / display info from the
        # already-loaded source axes so the frontend gets one
        # self-sufficient response. Axis tag is the join key.
        source_axes = _source_font.get_axes(font)
        axis_by_tag = {ax["tag"]: ax for ax in source_axes}

        out = []
        for tag, info in coverage.items():
            axis_meta = axis_by_tag.get(tag, {})
            out.append({
                "tag": tag,
                "name": axis_meta.get("name") or tag,
                "covers": info["covers"],
                "covers_count": info["covers_count"],
                "total_glyphs": info["total_glyphs"],
                "kind": info["kind"],
                # Per-glyph brace-layer / alternate-master locations —
                # lets the frontend render source-derived scoped axes
                # in the same layers panel as studio ones (read-only).
                "layers": info.get("layers", []),
                # Design-space axis extremes, same units as the layer
                # locations above — the frontend prefers these over the
                # built font's fvar for coverage classification.
                "min": info.get("min"),
                "default": info.get("default"),
                "max": info.get("max"),
                # ``source`` lets the frontend distinguish source-derived
                # axes (read from brace layers / alternate masters) from
                # studio-declared control axes (read from the sidecar).
                # Only the latter get edit / delete affordances in v2.
                "source": "source",
            })

        # Merge in studio-declared control axes from the sidecar.
        # If an axis tag is in the sidecar, it's authoritatively a
        # studio-declared control axis — re-tag any existing
        # source-derived entry with ``source: "studio"`` and overlay
        # the sidecar's metadata + coverage list. Otherwise append
        # the sidecar entry as a new row.
        #
        # The retag matters once the shadow .glyphs has the axis in
        # its axis list (slice 2.2+): a source-derived entry would
        # otherwise hide the studio origin and the UI loses its
        # edit / delete affordances.
        try:
            total = next(iter(coverage.values()), {}).get("total_glyphs", 0) if coverage else 0
            sidecar_path = state.ORIGINAL_PATH if state.ORIGINAL_PATH is not None else state.GLYPHS_PATH
            by_tag = {str(a["tag"]).lower(): a for a in out}
            sidecar_axes = list(_control_axes.list_axes(sidecar_path))
            # Resolve each studio layer's parametric location to its master
            # corner / instance name, so the layers panel can show it — the
            # same names the shadow's Fontra source labels carry.
            corner_of = None
            if isinstance(font, GSFont) and sidecar_axes:
                try:
                    sidecar_tags = {str(a.get("tag") or "").lower() for a in sidecar_axes}
                    control_idx = {
                        i for i, fax in enumerate(getattr(font, "axes", None) or [])
                        if str(getattr(fax, "axisTag", "")).lower() in sidecar_tags
                    }
                    corner_of = _control_axes.corner_name_resolver(
                        font, control_idx, state.ORIGINAL_PATH
                    )
                except Exception as corner_exc:
                    log.warning(f"Warning: corner-name resolution unavailable: {corner_exc}")
            for ax in sidecar_axes:
                tag_lower = (ax.get("tag") or "").lower()
                if not tag_lower:
                    continue
                existing = by_tag.get(tag_lower)
                # v2.7 unified schema: ``layers`` is the canonical
                # array. Coverage is derived from unique glyph names.
                layers = list(ax.get("layers") or [])
                if corner_of is not None:
                    for entry in layers:
                        corner = corner_of(entry.get("location"))
                        if corner:
                            entry["corner"] = corner
                derived_coverage: List[str] = []
                seen_glyphs: set = set()
                for entry in layers:
                    g = entry.get("glyph")
                    if g and g not in seen_glyphs:
                        seen_glyphs.add(g)
                        derived_coverage.append(g)

                if existing is not None:
                    src_covers = existing.get("covers") or []
                    # Union — derived (designer intent) wins for the
                    # ordering; brace-layer-derived (from shadow) is
                    # secondary. Should overlap perfectly once
                    # regenerate_shadow has run.
                    merged = list(dict.fromkeys([*derived_coverage, *src_covers]))
                    existing["source"] = "studio"
                    existing["name"] = ax.get("display_name") or existing.get("name")
                    existing["covers"] = merged
                    existing["covers_count"] = len(merged)
                    existing["default"] = ax.get("default")
                    existing["min"] = ax.get("min")
                    existing["max"] = ax.get("max")
                    existing["layers"] = layers
                    existing["kind"] = _glyph_coverage._classify(
                        len(merged), existing.get("total_glyphs", total)
                    )
                else:
                    out.append({
                        "tag": tag_lower,
                        "name": ax.get("display_name") or tag_lower,
                        "covers": derived_coverage,
                        "covers_count": len(derived_coverage),
                        "total_glyphs": total,
                        "kind": _glyph_coverage._classify(len(derived_coverage), total),
                        "source": "studio",
                        "default": ax.get("default"),
                        "min": ax.get("min"),
                        "max": ax.get("max"),
                        "layers": layers,
                    })
                    by_tag[tag_lower] = out[-1]
        except Exception as e:
            log.warning(f"Warning: failed to merge control-axes sidecar: {e}")

        # Stable ordering: universal first (least interesting), then
        # scoped. Within each bucket, by tag.
        kind_order = {"universal": 0, "scoped": 1}
        out.sort(key=lambda a: (kind_order.get(a["kind"], 99), a["tag"]))
        # Glyph-name → character map for the layer thumbnails: they
        # render TEXT in the built font, so "eight" must become "8" —
        # typesetting the name only works for single-char names.
        try:
            glyph_chars = _glyph_coverage.compute_glyph_chars(font)
        except Exception:
            glyph_chars = {}
        return jsonify({"axes": out, "glyph_chars": glyph_chars})
    except Exception as e:
        log.error(f"Error in /api/glyph-coverage: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
