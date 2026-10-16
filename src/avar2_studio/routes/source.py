"""Source routes — reads off the active source and the built font
(audit §3 step e group): masters, axes, the Glyphs.app document status."""

from pathlib import Path

from flask import Blueprint, jsonify

from .. import control_axes as _control_axes
from .. import csv_io as _csv_io
from .. import grade as _grade
from .. import server, state
from .. import source_font as _source_font
from .. import transforms as _transforms
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("source", __name__)


@bp.route('/api/masters', methods=['GET'])
def get_masters():
    """Return the source's masters — the parametric design corners.

    Pre-listed in the control-axis brace flow so the designer places a
    crbr layer at a specific corner. Read from the active source
    (shadow when control axes are live); master parametric coordinates
    are identical either way.
    """
    if state.GLYPHS_PATH is None:
        return jsonify({"masters": []})
    try:
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
        return jsonify({"masters": _source_font.get_masters(font)})
    except Exception as e:
        log.error(f"Error reading masters: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/axes', methods=['GET'])
def get_axes():
    """Get axes from the source file.

    Always reads from the source rather than the built font: the source
    carries semantic information the built font drops (notably
    ``has_master_coverage`` — true iff at least one master sits at a
    non-default position for the axis). The built-font path
    (``get_axes_from_built_font``) is kept around for verification but
    isn't the API path anymore.
    """
    if state.GLYPHS_PATH is None:
        return jsonify({"axes": []})
    try:
        axes = server.get_axes_from_glyphs(state.GLYPHS_PATH)

        # CONTROL AXES overlay. The source/shadow .glyphs may or may
        # not have the control axis yet (depending on slice — slice 2
        # generates a shadow but builds from the original, so the
        # source-derived axes won't include the control axis). Either
        # way the sidecar carries the canonical declaration; merge
        # it in so the frontend gets a unified list.
        if state.ORIGINAL_PATH is not None:
            try:
                sidecar_axes = list(_control_axes.list_axes(state.ORIGINAL_PATH))
                if sidecar_axes:
                    by_tag = {str(ax.get("tag", "")).lower(): ax for ax in axes}
                    for spec in sidecar_axes:
                        tag_lower = str(spec.get("tag", "")).lower()
                        if not tag_lower:
                            continue
                        existing = by_tag.get(tag_lower)
                        if existing is not None:
                            # Source already declares this tag — overlay
                            # the designer's range on top.
                            existing["min"] = float(spec["min"])
                            existing["max"] = float(spec["max"])
                            existing["default"] = float(spec["default"])
                            existing["name"] = spec.get("display_name") or existing.get("name")
                            existing["has_master_coverage"] = True
                            existing["is_control_axis"] = True
                        else:
                            # Source doesn't know about this axis yet
                            # (slice 2: no shadow swap). Synthesise an
                            # entry from sidecar so the slider still
                            # renders. font-variation-settings on a
                            # non-existent axis is silently ignored by
                            # the browser, so moving the slider has no
                            # visual effect — exactly what we want
                            # until brace layers exist in slice 3+.
                            axes.append({
                                "tag": tag_lower,
                                "name": spec.get("display_name") or tag_lower,
                                "min": float(spec["min"]),
                                "max": float(spec["max"]),
                                "default": float(spec["default"]),
                                "has_master_coverage": True,
                                "is_control_axis": True,
                            })
            except Exception as overlay_exc:
                log.warning(f"Warning: control-axes overlay on /api/axes failed: {overlay_exc}")

        # GRADE axis overlay. GRAD lives in the shadow but its range comes from
        # VIRTUAL masters (no real master spans it), so get_axes_from_glyphs
        # reports it as [0,0] — and the built-font overlay below then skips it
        # as "already present". When grade is enabled with ≥1 graded instance,
        # surface the real -10..+10 range so the sidebar renders a usable
        # slider. Like SPAC it's a live-preview axis (transform_injected), not
        # per-instance data.
        if state.ORIGINAL_PATH is not None:
            try:
                if _grade.list_graded_instances(state.ORIGINAL_PATH):
                    tag_lower = _grade.GRAD_TAG.lower()
                    entry = next((a for a in axes if str(a.get("tag", "")).lower() == tag_lower), None)
                    if entry is None:
                        entry = {"tag": _grade.GRAD_TAG}
                        axes.append(entry)
                    entry["name"] = _grade.GRAD_NAME
                    entry["min"] = _grade.GRAD_MIN
                    entry["default"] = _grade.GRAD_DEFAULT
                    entry["max"] = _grade.GRAD_MAX
                    entry["has_master_coverage"] = True
                    entry["transform_injected"] = True
                    entry["is_grade_axis"] = True
            except Exception as grade_exc:
                log.warning(f"Warning: grade overlay on /api/axes failed: {grade_exc}")

        # TRANSFORM-AXES overlay. Transforms can add whole axes WITH twin
        # masters (round_corners' ROND), which reach this list through the
        # TRANSFORMED source and so carry real master coverage — but like
        # SPAC and GRAD they are build products, not per-instance data.
        # Mark every enabled transform's declared axis so the frontend
        # keeps it out of instance saves and CSV sync.
        if state.ORIGINAL_PATH is not None:
            try:
                injected_tags = set()
                for stage in ("font", "source"):
                    for t, _params in _transforms.registry.active(state.ORIGINAL_PATH, stage=stage):
                        tag = getattr(t.spec, "injected_axis_tag", None)
                        if tag:
                            injected_tags.add(tag.lower())
                for a in axes:
                    if str(a.get("tag", "")).lower() in injected_tags:
                        a["transform_injected"] = True
            except Exception as inj_exc:
                log.warning(f"Warning: transform-axes overlay on /api/axes failed: {inj_exc}")

        # BUILT-FONT overlay. Post-build transforms (e.g. SPAC) inject fvar
        # axes that have no source master, so get_axes_from_glyphs can't see
        # them. Read the built font's fvar and surface any tag not already
        # present. Two kinds:
        #   - avar2 USER axes (the CSV's in-columns — wght/opsz): mapping
        #     targets with no masters of their own → has_master_coverage
        #     False, so the Preview tab files them under USER AXES.
        #   - transform-injected axes (SPAC): treated as parametric sliders
        #     (has_master_coverage=True → draggable; not a control axis →
        #     sits with XTRA/XOPQ/YOPQ).
        if state.VARIABLE_FONT_PATH is not None and Path(state.VARIABLE_FONT_PATH).exists():
            try:
                user_axis_tags = set()
                csv_path = server._get_avar2_csv_path()
                if csv_path and csv_path.exists():
                    try:
                        _, _, in_cols, _, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)
                        user_axis_tags = {
                            _csv_io.normalize_in_axis_name(c).lower() for c in in_cols
                        }
                        user_axis_tags.discard("spac")  # transform-injected, stays parametric
                    except Exception:
                        user_axis_tags = set()
                have = {str(a.get("tag", "")).lower() for a in axes}
                built_axes = list(server.get_axes_from_built_font(state.VARIABLE_FONT_PATH))
                # DEFAULT overlay for axes the source already lists: the
                # build relocates the parametric defaults to the ORIGIN
                # row's location (the default-origin master), so the
                # BUILT font's defaults are where the sliders must rest
                # — the source still carries the raw compile origin
                # (e.g. the hairline corner). Identity when no
                # relocation happened. Ranges stay source-derived.
                built_default_by_tag = {
                    str(b.get("tag", "")).lower(): b.get("default") for b in built_axes
                }
                for a in axes:
                    bd = built_default_by_tag.get(str(a.get("tag", "")).lower())
                    if bd is not None:
                        a["default"] = bd
                for b in built_axes:
                    tag_lower = str(b.get("tag", "")).lower()
                    if tag_lower in have:
                        continue
                    is_user_axis = tag_lower in user_axis_tags
                    axes.append({
                        "tag": b["tag"],
                        "name": b.get("name", b["tag"]),
                        "min": b["min"],
                        "max": b["max"],
                        "default": b["default"],
                        "has_master_coverage": not is_user_axis,
                        # Built-font-only and not an avar2 user axis ⇒
                        # injected by a post-build transform (SPAC).
                        # Injected axes are LIVE PREVIEW state, never
                        # per-instance data: the frontend must exclude
                        # them from dirtiness checks and save payloads,
                        # or a dragged Spacing slider turns every dot
                        # permanently red ("saves do nothing").
                        "transform_injected": not is_user_axis,
                    })
            except Exception as built_exc:
                log.warning(f"Warning: built-font overlay on /api/axes failed: {built_exc}")

        return jsonify({"axes": axes})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/glyphs-file-status', methods=['GET'])
def glyphs_file_status():
    """Check if Glyphs file has unsaved changes."""
    if state.GLYPHS_PATH is None:
        return jsonify({"has_unsaved_changes": False, "file_path": None})
    try:
        has_unsaved = server._check_glyphs_file_unsaved_changes(state.GLYPHS_PATH)
        return jsonify({
            "has_unsaved_changes": has_unsaved,
            "file_path": str(state.GLYPHS_PATH)
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "has_unsaved_changes": False
        }), 500
