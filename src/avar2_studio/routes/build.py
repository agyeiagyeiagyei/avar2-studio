"""Build/export/font routes (audit §3 step e group) — job submission and
status, the served preview font, exports."""

from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from fontTools.ttLib import TTFont

from .. import server, state
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("build", __name__)


@bp.route('/api/build', methods=['POST'])
def build_font():
    """Submit a build job and wait for the queue to go idle (the wait is
    GIL-free — every other endpoint keeps answering during the build)."""
    try:
        server.JOBS.request_build()
        success = server.JOBS.wait_for_idle()

        if not success:
            return jsonify({"error": state.LAST_BUILD_ERROR or "Build failed"}), 500

        # Get axes after building (for verification)
        axes = server.get_axes_from_built_font(state.VARIABLE_FONT_PATH)

        return jsonify({
            "success": True,
            "font_path": str(state.VARIABLE_FONT_PATH),
            "axes": axes,
            "build_time": state.LAST_BUILD_TIME
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/build/status', methods=['GET'])
def build_status():
    """Job queue state: running job (+phase), queued jobs, last result,
    export sizes."""
    return jsonify(server.JOBS.status())


@bp.route('/api/build/<job_id>', methods=['GET'])
def build_job_status(job_id):
    """One job's state by id (queued / running / finished + outcome). The
    export poll keys on this: ``status().last`` names only the newest
    finished job, so any job finishing inside a poll window went missing."""
    status = server.JOBS.job_status(job_id)
    if status is None:
        return jsonify({"error": "no such job"}), 404
    return jsonify(status)


@bp.route('/api/build/<job_id>', methods=['DELETE'])
def cancel_build(job_id):
    """Cancel a queued or running job (the worker's process group is
    killed; the last-good font keeps serving)."""
    return jsonify({"cancelled": server.JOBS.cancel(job_id)})


@bp.route('/api/export/<job_id>/download', methods=['GET'])
def export_download(job_id):
    """Serve a finished export job's artifact."""
    path = server.JOBS.export_artifact(job_id)
    if path is None:
        return jsonify({"error": "no such export (finished artifacts are "
                                 "kept for a few exports only)"}), 404
    return send_file(str(path), as_attachment=True, download_name=path.name)


@bp.route('/api/font', methods=['GET'])
def get_font():
    """Serve the built variable font (avar2 build preferred, plain VF fallback).

    ``?format=woff2`` serves the background-compressed WOFF2 cache (kept in
    step with the build by the job queue) when it is current — 409 while it
    is stale or missing, so the caller falls back to an export job.
    """
    # Phase 3: SPAC font is deferred and the avar2 build is now the primary
    # output. ``trigger_build()`` sets VARIABLE_FONT_PATH to the avar2 build
    # when it succeeds, or the plain VF when it doesn't, so we just serve
    # whatever's there.
    if request.args.get('format') == 'woff2':
        sizes = server.JOBS.export_sizes()
        if not (state.VARIABLE_FONT_PATH and sizes["woff2_exact"]):
            return jsonify({"error": "woff2 cache is stale or missing"}), 409
        woff2 = Path(state.VARIABLE_FONT_PATH).with_suffix(".woff2")
        return send_file(str(woff2), mimetype='font/woff2',
                         as_attachment=bool(request.args.get('download')),
                         download_name=woff2.name)
    if state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists():
        font_path = state.VARIABLE_FONT_PATH
    else:
        return jsonify({"error": "Variable font not built yet."}), 404

    # ?download=1 → attachment. The Preview tab's Download button is a
    # plain anchor at this URL; with the inline-with-filename disposition
    # the anchor's download attribute never triggered a save, so the
    # button silently did nothing.
    as_attachment = request.args.get('download', '').lower() in ('1', 'true', 'yes')
    response = send_file(
        str(font_path),
        mimetype='font/ttf',
        as_attachment=as_attachment,
        download_name=font_path.name if as_attachment else None,
    )
    # Add cache control headers to prevent browser caching
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    # Add ETag based on file modification time for cache validation
    mtime = font_path.stat().st_mtime
    response.headers['ETag'] = f'"{int(mtime)}"'
    return response


@bp.route('/api/export-font', methods=['POST'])
def export_font():
    """Queue an export job; the artifact is polled for and downloaded.

    Body::

        {"hidden_axes": ["XOPQ", ...],
         "default_location": {"opsz": 12, "wght": 900} | null}

    ``hidden_axes`` sets the fvar HIDDEN flag so font pickers don't
    surface those axes. ``default_location`` re-origins the export: the
    plain build's parametric defaults are rebased to the avar2-mapped
    location of the requested combination (instancer L3, applied by
    the gen-avar2 shim while the font is still plain — fontTools
    refuses partial-instancing once avar2 exists) and the table is
    generated against the requested user-axis defaults, so the font
    OPENS at that style with every axis range intact. The preview's
    served font is never touched.

    Returns 202 + job id; the client polls ``/api/build/status`` and
    fetches ``/api/export/<job id>/download`` when done.
    """
    if state.VARIABLE_FONT_PATH is None or not state.VARIABLE_FONT_PATH.exists():
        return jsonify({"error": "No built font"}), 404
    body = request.get_json(silent=True) or {}
    hidden = body.get("hidden_axes") or []
    default_location = body.get("default_location") or None
    fmt = body.get("format") or "ttf"
    if fmt not in ("ttf", "woff2"):
        return jsonify({"error": "format must be ttf or woff2"}), 400

    if default_location:
        tt = TTFont(str(state.VARIABLE_FONT_PATH), lazy=True)
        fvar_tags = {str(a.axisTag) for a in tt["fvar"].axes}
        tt.close()
        requested = {}
        for t, v in default_location.items():
            if str(t) in fvar_tags:
                try:
                    requested[str(t)] = float(v)
                except (TypeError, ValueError):
                    pass
        if not requested:
            return jsonify({"error": "default_location has no known axis values"}), 400

    job_id = server.JOBS.submit_export(hidden_axes=hidden, default_location=default_location,
                                format=fmt)
    if job_id is None:
        return jsonify({"error": "No source loaded"}), 400
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@bp.route('/api/avar2-font', methods=['GET'])
def get_avar2_font():
    """Serve the avar2 font file."""
    avar2_font_dir = server._get_avar2_font_dir()
    if not avar2_font_dir or not avar2_font_dir.exists():
        return jsonify({"error": "Avar2 font directory not found"}), 404

    # Look for the avar2 font — name comes from the active config's fvarInstances key
    built_filename = server._get_avar2_built_font_filename()
    if not built_filename:
        return jsonify({"error": "Could not determine built font filename"}), 500
    font_file = avar2_font_dir / built_filename

    if not font_file.exists():
        # Tolerate the case where the project workdir has a single avar2 TTF
        # with a different axis order than the config would suggest.
        candidates = list(avar2_font_dir.glob("*.ttf"))
        if len(candidates) == 1:
            font_file = candidates[0]
        else:
            return jsonify({"error": "Avar2 font not built yet"}), 404

    return send_file(
        str(font_file),
        mimetype="font/ttf",
        as_attachment=False,
        download_name=font_file.name
    )
