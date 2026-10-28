"""Kinetikern routes (audit §3 step e group) — per-instance spacing
corrections against the current build."""

import json
from typing import Dict, Optional

from flask import Blueprint, jsonify, request

from .. import grade as _grade
from .. import kinetikern as _kinetikern
from .. import server, state
from .. import source_font as _source_font
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("kinetikern", __name__)


@bp.route('/api/kinetikern/propose', methods=['GET'])
def kinetikern_propose():
    """Kinetikern2 Pass 1 (sidebearings) for one instance (``instance=<name>``)
    or any location of the built font (``coordinates=<JSON {tag: value}>``),
    at ``looseness`` (-1 tight … +1 loose; 0 is the engine's calibrated
    middle). Returns current and proposed sidebearings per encoded glyph.
    Nothing is written."""
    if not (state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists()):
        return jsonify({"error": "Variable font not built yet."}), 404
    if _kinetikern.engine_path() is None:
        return jsonify({"error": "Kinetikern2 engine not built (native/kinetikern2/README.md)."}), 503
    name = request.args.get("instance")
    if name:
        coords = server._grade_instance_coords().get(name)
        if not coords:
            return jsonify({"error": f"Unknown instance: {name}"}), 404
        location = {t: coords[t] for t in _grade.PARAM_TAGS if t in coords}
    else:
        try:
            location = json.loads(request.args.get("coordinates", ""))
        except json.JSONDecodeError:
            return jsonify({"error": "instance or coordinates (JSON) required"}), 400
    try:
        looseness = float(request.args.get("looseness", 0.0))
    except ValueError:
        return jsonify({"error": "looseness must be a number"}), 400
    try:
        out = _kinetikern.propose(state.VARIABLE_FONT_PATH, location, looseness)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    out["instance"] = name
    # what the sidecar already holds for this instance, by glyph: the build
    # that carries it shows ``cur`` = baseline + applied
    stored = _kinetikern.sidecar.find(state.ORIGINAL_PATH, name) if name else None
    applied = (stored or {}).get("deltas", {})
    source_names = _kinetikern_source_names() if applied else {}
    for g in out["glyphs"]:
        g["applied"] = applied.get(source_names.get(g["unicode"], g["name"]), [0, 0])
    out["looseness_applied"] = stored.get("looseness") if stored else None
    return jsonify(out)


@bp.route('/api/kinetikern', methods=['GET'])
def kinetikern_state():
    """The Kinetikern sidecar: every corrected instance with its looseness
    and per-glyph deltas (``sidecar.load``)."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    return jsonify(_kinetikern.sidecar.load(state.ORIGINAL_PATH))


@bp.route('/api/kinetikern', methods=['PUT'])
def kinetikern_set_enabled():
    """Turn the family's Kinetikern spacing on or off — body
    ``{"enabled": bool}``.

    The sidecar's ``enabled`` flag is what the build honours (``entries()``
    returns nothing while it is off), but nothing could reach it from the UI:
    the only off-switch was hand-editing the sidecar (K1). Toggling rewrites
    the sidecar and schedules ONE shadow rebuild, so the built font follows.
    Entries are kept while off — turning it back on restores them.
    """
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"error": "Body must include 'enabled' (true/false)"}), 400
    before = bool(_kinetikern.sidecar.load(state.ORIGINAL_PATH).get("enabled", True))
    after = bool(data["enabled"])
    stored = _kinetikern.sidecar.set_enabled(state.ORIGINAL_PATH, after)
    if after != before:
        server.schedule_shadow_rebuild()
    return jsonify(stored)


def _kinetikern_apply_one(name: str, coords: Dict[str, float], looseness: float,
                          location: Optional[Dict[str, float]] = None) -> Dict:
    """Space ``name`` at ``looseness`` against the current build and store the
    result as deltas. The engine's targets are absolute, so the stored delta
    is the existing one plus the move the current build still needs; once
    that build carries the entry a re-apply changes nothing."""
    location = {t: coords[t] for t in _grade.PARAM_TAGS if t in coords}
    out = _kinetikern.propose(state.VARIABLE_FONT_PATH, location, looseness)
    existing = (_kinetikern.sidecar.find(state.ORIGINAL_PATH, name) or {}).get("deltas", {})
    source_names = _kinetikern_source_names()
    deltas = {}
    for g in out["glyphs"]:
        if not g["spaced"]:
            continue
        glyph = source_names.get(g["unicode"], g["name"])
        old = existing.get(glyph, [0, 0])
        dl = g["new"]["lsb"] - g["cur"]["lsb"] + old[0]
        dr = g["new"]["rsb"] - g["cur"]["rsb"] + old[1]
        if dl or dr:
            deltas[glyph] = [dl, dr]
    return _kinetikern.sidecar.set_entry(state.ORIGINAL_PATH, name, looseness, deltas, location=location)


def _kinetikern_source_names() -> Dict[int, str]:
    """{code point: source glyph name}. The bake works on the source, whose
    names fontc replaces with production names in the build (euro -> Euro)."""
    try:
        font, fmt = _source_font.load_source_cached(state.ORIGINAL_PATH)
    except Exception:
        return {}
    if "glyphs" not in str(fmt).lower():
        return {}
    names = {}
    for glyph in font.glyphs:
        for u in (getattr(glyph, "unicodes", None) or ([glyph.unicode] if glyph.unicode else [])):
            try:
                names.setdefault(int(str(u), 16), glyph.name)
            except ValueError:
                pass
    return names


def _kinetikern_ready():
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    if not (state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists()):
        return jsonify({"error": "Variable font not built yet."}), 404
    if _kinetikern.engine_path() is None:
        return jsonify({"error": "Kinetikern2 engine not built (native/kinetikern2/README.md)."}), 503
    return None


@bp.route('/api/kinetikern/instances/<instance_name>', methods=['PUT'])
def kinetikern_apply_instance(instance_name: str):
    """Space one instance with Pass 1 at body ``{"looseness": t}`` and store
    the deltas. Rebuilds."""
    blocked = _kinetikern_ready()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    location = data.get("location")
    if location:
        try:
            coords = {str(t).upper(): float(location[t]) for t in location}
        except (TypeError, ValueError):
            return jsonify({"error": "location values must be numbers"}), 400
        if any(t not in coords for t in _grade.PARAM_TAGS):
            return jsonify({"error": f"location needs {', '.join(_grade.PARAM_TAGS)}"}), 400
    else:
        coords = server._grade_instance_coords().get(instance_name)
        if not coords:
            return jsonify({"error": f"Unknown instance: {instance_name}"}), 404
    try:
        looseness = float(data.get("looseness", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "looseness must be a number"}), 400
    entry = _kinetikern_apply_one(instance_name, coords, looseness, location=coords if location else None)
    server.schedule_shadow_rebuild()
    return jsonify(entry)


@bp.route('/api/kinetikern/instances/<instance_name>', methods=['DELETE'])
def kinetikern_revert_instance(instance_name: str):
    """Drop one instance's correction. Rebuilds."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    removed = _kinetikern.sidecar.remove_entry(state.ORIGINAL_PATH, instance_name)
    if removed:
        server.schedule_shadow_rebuild()
    return jsonify({"removed": removed})


@bp.route('/api/kinetikern/all', methods=['PUT'])
def kinetikern_apply_all():
    """Space every instance that has parametric coordinates at one
    ``looseness``, so the whole family is pinned consistently. Rebuilds once."""
    blocked = _kinetikern_ready()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    try:
        looseness = float(data.get("looseness", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "looseness must be a number"}), 400
    applied = []
    for name, coords in server._grade_instance_coords().items():
        if all(t in coords for t in _grade.PARAM_TAGS):
            _kinetikern_apply_one(name, coords, looseness)
            applied.append(name)
    if applied:
        server.schedule_shadow_rebuild()
    return jsonify({"applied": applied, "looseness": looseness})
