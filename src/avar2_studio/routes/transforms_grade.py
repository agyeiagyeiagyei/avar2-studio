"""Transforms + grade routes (audit §3 step e group).

GRADE lives here rather than in the transforms registry: a grade is
source-level (injects GRAD brace layers onto the shadow, like control
axes — NOT a post-build VF→VF transform), so its endpoints schedule a
shadow rebuild. Per-instance grades persist regardless of the toggle.
The Transforms "Grade" toggle + per-instance grade% ride the same
per-source ``<basename>-grade.json`` sidecar.
"""

from flask import Blueprint, jsonify, request

from .. import grade as _grade
from .. import server, state
from .. import source_font as _source_font
from .. import transforms as _transforms
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("transforms_grade", __name__)


@bp.route('/api/transforms', methods=['GET'])
def list_transforms():
    """Return the available transforms merged with this project's enabled
    state + params."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"transforms": []})
    try:
        _transforms.discover()
        return jsonify({
            "transforms": _transforms.available(state.ORIGINAL_PATH),
            "sidecar_path": str(_transforms.sidecar_path_for(state.ORIGINAL_PATH)),
        })
    except Exception as e:
        log.error(f"Error listing transforms: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/transforms', methods=['PUT'])
@server._sidecar_locked
def update_transforms():
    """Replace the project's transform set. Body::

        {"transforms": [
            {"type": "spac", "enabled": true, "params": {"min": -20, "max": 40}}
        ]}

    Unknown types are dropped; params are coerced against each transform's
    schema. Persists to the sidecar, then rebuilds so the preview + axes
    reflect the new chain (transforms are VF post-processors — no shadow
    rewrite needed)."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    entries = data.get("transforms")
    if not isinstance(entries, list):
        return jsonify({"error": "Body must include 'transforms' as a list."}), 400
    try:
        _transforms.discover()
        previous = {}
        try:
            previous = {t.get("id"): t for t in _transforms.available(state.ORIGINAL_PATH)}
        except Exception as prev_exc:
            log.info(f"transforms: could not read previous state: {prev_exc}")
        # Guardrail: a transform whose engine reads specific axes
        # (round_corners' XOPQ/XTRA radius formula) is refused on a
        # source that doesn't declare them — enabling it would build an
        # inert transform (a silently sharp font must not pass as ok).
        src_tags = server._get_glyphs_axis_tags()
        if src_tags:  # unreadable source → the engine's own raise surfaces instead
            for e in entries:
                if not isinstance(e, dict) or not e.get("enabled"):
                    continue
                t = _transforms.REGISTRY.get(str(e.get("type", "")))
                req = getattr(getattr(t, "spec", None), "requires_axis_tags", ()) if t else ()
                missing = [tag for tag in req if tag.lower() not in src_tags]
                if missing:
                    return jsonify({"error": (
                        f"{t.spec.name} needs the parametric ax{'es' if len(missing) > 1 else 'is'} "
                        f"{', '.join(missing)} — this source doesn't declare "
                        f"{'them' if len(missing) > 1 else 'it'}.")}), 400
        try:
            stored = _transforms.set_active(state.ORIGINAL_PATH, entries)
        except ValueError as ve:
            # Invalid config (e.g. SPAC min >= max) — reject with feedback
            # instead of persisting an enabled-but-doomed transform.
            return jsonify({"error": str(ve)}), 400
        # A change that involves a SOURCE-stage transform (round_corners) —
        # enabling one, disabling one, or changing its SOURCE params —
        # changes the FILE the compiler must read, so it goes through the
        # same regen-and-rebuild path the grade toggle uses. Edits limited
        # to a transform's declared font_stage_param_keys (round_corners'
        # per-style percents) only change its VF->VF apply, so they take
        # the fast trigger_build path instead of re-rounding the shadow.
        def _source_touched():
            for e in stored:
                # ``stored`` is set_active's return: the UI list, whose
                # entries carry "id" (sidecar entries carry "type").
                tid = e.get("type") or e.get("id")
                t = _transforms.REGISTRY.get(tid)
                if t is None or getattr(t.spec, "stage", "font") != "source":
                    continue
                prev = previous.get(tid) or {}
                if bool(e.get("enabled")) != bool(prev.get("enabled")):
                    return True
                if not e.get("enabled"):
                    continue
                fast = set(getattr(t.spec, "font_stage_param_keys", ()) or ())
                new_p = e.get("params") or {}
                old_p = prev.get("params") or {}
                for k in set(new_p) | set(old_p):
                    if k not in fast and new_p.get(k) != old_p.get(k):
                        return True
            return False

        def _resolve_then_build():
            # Synchronous twin of the debounced regen job: same steps, none
            # of its timer bookkeeping (calling the job directly unbalances
            # BACKGROUND_WORK and with it /api/health's ``building``).
            server._resolve_active_source()
            return server.trigger_build()

        rebuild = _resolve_then_build if _source_touched() else server.trigger_build
        # NB: do NOT null VARIABLE_FONT_PATH here. trigger_build() reassigns it
        # on any successful build and leaves it untouched on failure, so the
        # last-good font keeps serving if the rebuild fails.
        build_ok = False
        try:
            build_ok = rebuild()
        except Exception as build_exc:
            log.warning(f"Warning: rebuild after transforms update failed: {build_exc}")
        # A transform can fail even when the base font compiled fine — surface
        # it so the UI doesn't show an enabled transform that silently no-op'd.
        return jsonify({
            "success": True,
            "transforms": stored,
            "build_ok": bool(build_ok),
            "transform_error": state.LAST_TRANSFORM_ERROR,
        })
    except Exception as e:
        log.error(f"Error updating transforms: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _grade_param_ranges():
    """(min, max) per parametric tag, or {} when the source can't be read."""
    ranges = {}
    try:
        axes = _source_font.get_axes(_source_font.load_source_cached(state.ORIGINAL_PATH)[0])
        by_tag = {a["tag"].upper(): a for a in axes}
        for t in _grade.PARAM_TAGS:
            if t in by_tag:
                ranges[t] = (by_tag[t]["min"], by_tag[t]["max"])
    except Exception:
        return {}
    return ranges


def _grade_diagnostics(data, coords, param_ranges):
    """Problems with the CURRENT grade declaration, worst first.

    Each entry is ``{level, code, instance, message, detail}`` where level is
    "error" (the axis won't build, or this instance contributes nothing),
    "warning" (it builds but the result is degraded) or "info".

    The checks exist because the failure modes here are invisible in the
    preview until you drag the slider to an extreme: an instance pinned on the
    XTRA floor has no counters left to open, so every stem unit the grade adds
    lands in the counter and the glyph bleeds shut.
    """
    out = []
    strength = float(data.get("intensity", 1.0))
    capped = bool(data.get("clamp_to_headroom", True))
    entries = list(data.get("instances") or [])
    live = [e for e in entries
            if _grade.effective_pct(e.get("pct", 0.0), strength) > 0]

    # --- axis-level: will GRAD exist at all? ------------------------------
    if not data.get("enabled"):
        out.append({
            "level": "info", "code": "grade_disabled", "instance": None,
            "message": "Grade is switched off, so the GRAD axis is not built.",
            "detail": "Per-instance grades are remembered and return when you switch it back on.",
        })
    elif not entries:
        out.append({
            "level": "error", "code": "no_graded_instances", "instance": None,
            "message": "No instance is graded, so the GRAD axis is not built.",
            "detail": "Grade at least one instance to bring the axis into the font.",
        })
    elif strength <= 0:
        out.append({
            "level": "error", "code": "intensity_zero", "instance": None,
            "message": "Grade intensity is 0, so every grade is cancelled and the GRAD axis is not built.",
            "detail": f"{len(entries)} instance(s) are graded. Raise intensity above 0 to bring them back.",
        })
    elif not live:
        out.append({
            "level": "error", "code": "all_grades_zero", "instance": None,
            "message": "Every graded instance sits at 0%, so the GRAD axis is not built.",
            "detail": "Give at least one instance a grade above 0.",
        })
    elif len(live) == 1:
        out.append({
            "level": "info", "code": "single_anchor", "instance": live[0].get("name"),
            "message": f"Only \u201c{live[0].get('name')}\u201d is graded, so GRAD fades to nothing away from it.",
            "detail": "Grade more instances to carry the axis across the space.",
        })

    if not param_ranges:
        return out

    # --- per-instance --------------------------------------------------
    for entry in entries:
        name = entry.get("name")
        authored = float(entry.get("pct", 0.0) or 0.0)
        pct = _grade.effective_pct(authored, strength)
        base = coords.get(name)
        if base is None:
            out.append({
                "level": "error", "code": "unknown_instance", "instance": name,
                "message": f"\u201c{name}\u201d is graded but no longer exists.",
                "detail": "It was renamed or deleted. Remove the grade or re-add the instance.",
            })
            continue
        if pct <= 0:
            continue

        cap = _grade.max_pct_for(base, param_ranges)
        lo_x = param_ranges.get("XTRA", (None, None))[0]
        x = base.get("XTRA")
        room = None if (x is None or lo_x is None) else max(0.0, x - lo_x)

        # What the model actually delivers here, both directions.
        light, dark = _grade.grade_coords(base, pct, param_ranges,
                                          clamp_to_headroom=capped)
        o = base.get("XOPQ", 0.0)
        want = pct * o / 2.0
        got_dark = dark.get("XOPQ", o) - o
        got_light = o - light.get("XOPQ", o)

        if room == 0:
            if capped:
                out.append({
                    "level": "error", "code": "no_headroom_capped", "instance": name,
                    "message": f"\u201c{name}\u201d cannot darken: its counters are already as tight as the design allows.",
                    "detail": ("XTRA is at its minimum, so there is no counter room to absorb a thicker "
                               "stem. The darkening half of the grade is held at 0 here; the lightening "
                               "half still works. Move the instance off the XTRA floor, or turn off "
                               "\u201climit grade to counter headroom\u201d to allow uncompensated darkening."),
                })
            else:
                out.append({
                    "level": "warning", "code": "no_headroom_uncompensated", "instance": name,
                    "message": f"\u201c{name}\u201d darkens straight into its counters.",
                    "detail": (f"XTRA is at its minimum, so none of the {2 * want:.0f} units of counter "
                               f"relief this grade needs are available: all {want:.0f} units of added stem "
                               "fill the counters. Lower this instance's grade%, or turn on "
                               "\u201climit grade to counter headroom\u201d."),
                })
        elif authored > cap + 1e-9:
            out.append({
                "level": "warning", "code": "pct_over_cap", "instance": name,
                "message": f"\u201c{name}\u201d is graded beyond what its parametric space can deliver.",
                "detail": (f"Grade is {authored:.0%} but only {cap:.0%} fits before an axis hits its limit; "
                           "the excess is discarded, so raising it further changes nothing."),
            })
        elif want > 0 and (got_dark < want - 0.5 or got_light < want - 0.5):
            worst = min(got_dark, got_light)
            out.append({
                "level": "warning", "code": "partially_clamped", "instance": name,
                "message": f"\u201c{name}\u201d only reaches part of its grade.",
                "detail": (f"{worst:.0f} of {want:.0f} stem units are delivered before an axis hits its "
                           "limit, so the two directions are no longer symmetric."),
            })

    order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda d: order.get(d["level"], 3))
    return out


def _grade_state_payload():
    """Grade sidecar + per-instance slider caps and diagnostics for the UI."""
    data = _grade.load(state.ORIGINAL_PATH)
    param_ranges = _grade_param_ranges()
    try:
        coords = server._grade_instance_coords()
        caps = {
            name: _grade.max_pct_for(coords[name], param_ranges)
            for name in coords if param_ranges
        }
    except Exception:
        coords, caps = {}, {}
    data["max_pct"] = caps
    try:
        data["diagnostics"] = _grade_diagnostics(data, coords, param_ranges)
    except Exception as exc:
        log.warning(f"Warning: grade diagnostics failed: {exc}")
        data["diagnostics"] = []
    data["sidecar_path"] = str(_grade.sidecar_path_for(state.ORIGINAL_PATH))
    return data


@bp.route('/api/transforms/grade', methods=['GET'])
def get_grade():
    """Return the grade toggle, default, per-instance grades, and slider caps."""
    if state.ORIGINAL_PATH is None:
        return jsonify(_grade._empty())
    try:
        return jsonify(_grade_state_payload())
    except Exception as e:
        log.error(f"Error reading grade: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/transforms/grade', methods=['PUT'])
def set_grade():
    """Set the Grade toggle and/or the global knobs. Body::

        { "enabled": true, "default_pct": 0.25,
          "intensity": 1.0, "clamp_to_headroom": true }

    All fields optional. ``intensity`` scales every authored grade% at once;
    ``clamp_to_headroom`` stops a grade thickening where the counters have no
    room to open. Rebuilds when the built font would change."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        rebuild = False
        if "enabled" in data:
            _grade.set_enabled(state.ORIGINAL_PATH, bool(data["enabled"]))
            rebuild = True  # toggling adds/removes the GRAD axis
        if "default_pct" in data:
            # The default only seeds NEW grades; changing it never alters the
            # built font, so persist without a rebuild.
            _grade.set_default_pct(state.ORIGINAL_PATH, data["default_pct"])
        if "intensity" in data:
            # Scales every authored grade%, so the built font changes.
            _grade.set_intensity(state.ORIGINAL_PATH, data["intensity"])
            rebuild = True
        if "clamp_to_headroom" in data:
            _grade.set_clamp_to_headroom(state.ORIGINAL_PATH, bool(data["clamp_to_headroom"]))
            rebuild = True
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if rebuild:
        server.schedule_shadow_rebuild()
    return jsonify(_grade_state_payload())


@bp.route('/api/instances/<instance_name>/grade', methods=['PUT'])
def set_instance_grade(instance_name: str):
    """Add or update a grade on one instance. Body ``{"pct": 0.25}`` — omit
    ``pct`` to use the global default. Rebuilds."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        entry = _grade.set_instance_grade(state.ORIGINAL_PATH, instance_name, data.get("pct"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    server.schedule_shadow_rebuild()
    return jsonify(entry)


@bp.route('/api/instances/<instance_name>/grade', methods=['DELETE'])
def delete_instance_grade(instance_name: str):
    """Remove a grade from one instance. Rebuilds."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    removed = _grade.remove_instance_grade(state.ORIGINAL_PATH, instance_name)
    if removed:
        server.schedule_shadow_rebuild()
    return jsonify({"removed": removed})
