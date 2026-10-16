"""Config-bundle routes (audit §3 step e group) — export/import of the
studio-authored configuration as one portable JSON bundle."""

import json

from flask import Blueprint, Response, jsonify, request

from .. import config_port as _config_port
from .. import control_axes as _control_axes
from .. import grade as _grade
from .. import server, state
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("config", __name__)


@bp.route('/api/config/export', methods=['GET'])
def export_config_bundle():
    """Download the studio-authored configuration (control axes +
    brace-layer declarations, avar2 mappings CSV, transforms) as one
    portable JSON bundle. See config_port.py for the format."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    try:
        # build_export captures drawn outlines into the sidecar first, which
        # is a write next to the watched source — suppress the watcher so an
        # export does not look like a designer edit and trigger a rebuild.
        server._suppress_watcher()
        bundle = _config_port.build_export(state.ORIGINAL_PATH, server._get_avar2_csv_path())
    except Exception as e:
        log.error(f"Error exporting config: {e}")
        return jsonify({"error": str(e)}), 500
    family = (bundle.get("source") or {}).get("family_name") or "source"
    return Response(
        json.dumps(bundle, indent=2) + "\n",
        mimetype="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_config_port.bundle_filename(family)}"'
            )
        },
    )


@bp.route('/api/config/import', methods=['POST'])
def import_config_bundle():
    """Validate (``dry_run: true``) or apply a config bundle.

    All-or-nothing: a failed validation writes nothing and returns 400
    with the report. Applying REPLACES the current studio config
    (control axes + transforms wholesale; the avar2 CSV only when the
    bundle carries one), then regenerates the shadow and rebuilds.
    """
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    body = request.get_json(silent=True) or {}
    bundle = body.get("bundle")
    if bundle is None:
        return jsonify({"error": "POST JSON {bundle: {...}, dry_run: bool}"}), 400
    dry_run = bool(body.get("dry_run", True))
    try:
        report = _config_port.validate_bundle(bundle, state.ORIGINAL_PATH)
    except Exception as e:
        log.error(f"Error validating config bundle: {e}")
        return jsonify({"error": str(e)}), 500
    if dry_run or not report["ok"]:
        return jsonify(report), (200 if dry_run else 400)

    with server.SIDECAR_LOCK:
        try:
            # CSV destination: the CSV the studio is currently editing,
            # else the conventional <stem>-avar.csv next to the ORIGINAL
            # (never the shadow — same rule as every sidecar).
            csv_dest = server._get_avar2_csv_path() or server._get_preview_csv_path()
            report = _config_port.apply_bundle(bundle, state.ORIGINAL_PATH, csv_dest)
        except Exception as e:
            log.error(f"Error applying config bundle: {e}")
            return jsonify({"error": str(e)}), 500

        # Same shadow + build dance as delete_control_axis: re-derive the
        # active source from the imported control axes AND grades, dropping the
        # shadow only when neither needs it; keep the last-good font on failure.
        try:
            server._resolve_active_source()
            if (state.GLYPHS_PATH == state.ORIGINAL_PATH
                    and not _control_axes.list_axes(state.ORIGINAL_PATH)
                    and not _grade.list_graded_instances(state.ORIGINAL_PATH)):
                _control_axes.remove_shadow(state.ORIGINAL_PATH)
            try:
                server.trigger_build()
            except Exception as build_exc:
                log.warning(f"Warning: rebuild after config import failed: {build_exc}")
        except Exception as shadow_exc:
            log.warning(f"Warning: shadow refresh after config import failed: {shadow_exc}")

    return jsonify(report)
