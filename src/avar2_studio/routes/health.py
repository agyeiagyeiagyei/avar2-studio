"""Health route (audit §3 step e group)."""

import os

from flask import Blueprint, jsonify

from .. import control_axes as _control_axes
from .. import server, state
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("health", __name__)


@bp.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint.

    ``last_build_status`` is one of:

    * ``ok`` — the last build produced the font the preview expects;
    * ``degraded`` — the font IS the avar2 build (every axis, avar2 table)
      but its default could not be relocated onto the ORIGIN row, so the
      preview rests at the compiled origin. ``origin_relocation_error``
      carries the reason; the build gate stays OPEN so the ORIGIN row can be
      put back (deliberately not ``failed``);
    * ``failed`` — the source does not compile: the last-good font keeps
      serving and every mutating route answers 409.
    """
    try:
        family_name = None
        if state.GLYPHS_PATH:
            family_name = server.get_font_family_name(state.GLYPHS_PATH) or state.GLYPHS_PATH.stem

        return jsonify({
            "status": "ok",
            # Hosted shared-demo instance (AVAR2_STUDIO_DEMO=1): the
            # frontend shows the landing overlay + shared-session note.
            "demo": os.environ.get("AVAR2_STUDIO_DEMO") == "1",
            "glyphs_path": str(state.GLYPHS_PATH) if state.GLYPHS_PATH else None,
            # original_path is the path the USER pointed at. glyphs_path
            # is the ACTIVE build/read path which becomes the shadow
            # whenever CONTROL AXES are declared. Frontend uses the
            # former as the "source identity" for swap detection so
            # declaring a control axis doesn't look like a swap.
            "original_path": str(state.ORIGINAL_PATH) if state.ORIGINAL_PATH else None,
            "source_format": state.SOURCE_FORMAT,
            "font_built": state.VARIABLE_FONT_PATH.exists() if state.VARIABLE_FONT_PATH else False,
            "family_name": family_name,
            "vf_family_id": f"{family_name}-VF" if family_name else None,
            # Report the ACTUAL served font's name (VARIABLE_FONT_PATH is what
            # /api/font serves and the download saves) so a transform-injected
            # name like ``…[SPAC].ttf`` matches the bytes. Fall back to the
            # avar2-dir derivation before the first build completes.
            "built_font_filename": (
                state.VARIABLE_FONT_PATH.name if state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists()
                else server._get_avar2_built_font_filename()
            ),
            "last_build_time": state.LAST_BUILD_TIME,
            "last_build_status": state.LAST_BUILD_STATUS,
            "last_build_error": state.LAST_BUILD_ERROR,
            # {sidecar path: parse error} — a corrupt control sidecar reads as
            # empty but is never overwritten; the UI should say so.
            "sidecar_errors": _control_axes.load_errors(),
            "transform_error": state.LAST_TRANSFORM_ERROR,
            # Non-null ⇒ the served font is the plain fallback build;
            # the avar2 build is failing for this reason.
            "avar2_error": state.LAST_AVAR2_ERROR,
            # Non-null ⇒ the avar2 font IS served (all axes) but its default
            # could not be moved onto the ORIGIN row; last_build_status reads
            # "degraded". Revert the ORIGIN row to clear it.
            "origin_relocation_error": state.LAST_ORIGIN_RELOCATION_ERROR,
            "origin_relocation_memo": state.LAST_ORIGIN_RELOCATION_MEMO,
            "grade_error": state.LAST_GRADE_ERROR,
            "source_transform_error": state.LAST_SOURCE_TRANSFORM_ERROR,
            "origin_warning": state.LAST_ORIGIN_WARNING,
            # True ⇒ the mappings CSV changed after the last build —
            # the served font's avar2 table is stale. Auto-saved
            # coordinate edits set this instead of rebuilding; the
            # Preview tab rebuilds once on open when it's true.
            "build_stale": server._is_build_stale(),
            # Fold in background regen/rebuild work: the layer save returns
            # instantly, so the queue's state alone would read false while the
            # shadow is still regenerating and the preview is stale.
            "building": server.JOBS.is_building() or state.BACKGROUND_WORK > 0,
            "build_job": server.JOBS.status().get("running"),
        })
    except Exception as e:
        log.error(f"Error in health endpoint: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
