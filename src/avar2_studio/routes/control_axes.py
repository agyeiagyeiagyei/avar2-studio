"""Control-axis routes (audit §3 step e group). See
docs/guides/secondary-parametric-axes.md."""

import json
from pathlib import Path

from flask import Blueprint, jsonify, request

from .. import control_axes as _control_axes
from .. import grade as _grade
from .. import server, state
from .. import source_font as _source_font
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("control_axes", __name__)


def _source_glyph_sigs():
    """{glyph: master-geometry signature} for the ORIGINAL source, recomputed
    only when the file's mtime changes. /api/control-axes is polled, and a
    glyphsLib parse per poll would be the wrong price for a staleness flag."""
    if state.ORIGINAL_PATH is None or not state.ORIGINAL_PATH.exists():
        return None
    try:
        mtime = state.ORIGINAL_PATH.stat().st_mtime_ns
    except OSError:
        return None
    if state._SOURCE_GLYPH_SIGS[0] == mtime:
        return state._SOURCE_GLYPH_SIGS[1]
    try:
        font, _fmt = _source_font.load_source_cached(state.ORIGINAL_PATH)
        sigs = {g.name: _control_axes.glyph_masters_sig(font, g.name) for g in font.glyphs}
    except Exception as exc:
        log.warning(f"Warning: could not fingerprint source glyphs: {exc}")
        return None
    state._SOURCE_GLYPH_SIGS = (mtime, sigs)
    return sigs


def _designspace_authoring_refusal():
    """Secondary-axis AUTHORING is .glyphs-only (sprint decision T2.15):
    the designspace shadow regeneration ignores corrections (``target``)
    and captured drawings, has no seed stamps and writes non-atomically,
    so edits made through these routes would silently degrade. Reads keep
    working and existing layers still build. Returns a 501 response, or
    None when the source is a .glyphs file."""
    if state.ORIGINAL_PATH is None:
        return None
    fmt = state.SOURCE_FORMAT or _source_font.detect_format(Path(state.ORIGINAL_PATH))
    if fmt == "designspace":
        return jsonify({
            "error": "Secondary parametric axes are authored on .glyphs sources only; "
                     "this project is a .designspace. Existing layers still build, "
                     "but they can't be edited here.",
            "source_format": "designspace",
        }), 501
    return None


@bp.route('/api/control-axes', methods=['GET'])
def list_control_axes():
    """Return the sidecar's control-axis declarations."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"axes": []})
    try:
        # Strip the captured drawings. They are large path dumps the UI never
        # reads, and this endpoint is polled — but WHETHER a layer holds one
        # matters, because that is the hand work a re-seed would discard.
        sigs = _source_glyph_sigs()
        slim = []
        for ax in _control_axes.list_axes(state.ORIGINAL_PATH):
            entry = dict(ax)
            layers = []
            for layer in (ax.get("layers") or []):
                layer = layer or {}
                has_outline = bool(layer.get("outline"))
                stored = layer.get("source_sig")
                # A drawing is stale when the masters it was made over have
                # changed since capture. Only answerable for drawings that
                # recorded their source_sig; older captures report unknown.
                if has_outline and stored and sigs is not None:
                    source_changed = sigs.get(layer.get("glyph")) != stored
                else:
                    source_changed = None
                layers.append(dict(
                    {k: v for k, v in layer.items() if k not in ("outline", "source_sig")},
                    has_outline=has_outline,
                    source_changed=source_changed,
                ))
            entry["layers"] = layers
            slim.append(entry)
        return jsonify({
            "axes": slim,
            "sidecar_path": str(_control_axes.sidecar_path_for(state.ORIGINAL_PATH)),
        })
    except Exception as e:
        log.error(f"Error listing control axes: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/control-axes', methods=['POST'])
def create_control_axis():
    """Declare a new control axis. JSON body::

        { "tag": "crbr", "display_name": "Crossbar",
          "default": 0, "min": -100, "max": 100 }

    Persists to the sibling ``<basename>-control.json``, regenerates
    the shadow .glyphs file (with the new axis added to the source's
    axis list + each master extended at the axis default), and
    triggers a font rebuild. The slider appears in the preview
    immediately; until coverage glyphs + brace layers exist (later
    slices) moving it has no visual effect because every glyph sits
    at the axis default on every master.
    """
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    # Both source formats author through a shadow now: .glyphs gets
    # brace layers inside a shadow copy, .designspace gets pooled
    # sparse UFO sources next to a shadow document.
    data = request.get_json(silent=True) or {}
    try:
        entry = _control_axes.add_axis(
            state.ORIGINAL_PATH,
            tag=data.get("tag", ""),
            display_name=data.get("display_name", ""),
            default=data.get("default", 0),
            min_value=data.get("min", 0),
            max_value=data.get("max", 0),
        )

        # Generate / refresh the shadow for slice 3+ to consume.
        # Build stays pointed at the original — see _apply_source_path.
        shadow = None
        try:
            shadow = _control_axes.regenerate_shadow(state.ORIGINAL_PATH)
        except Exception as shadow_exc:
            log.warning(f"Warning: shadow regeneration after add failed: {shadow_exc}")

        return jsonify({"success": True, "axis": entry, "shadow_path": str(shadow) if shadow else None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        log.error(f"Error creating control axis: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/control-axes/<tag>/open-editor', methods=['POST'])
def open_control_axis_in_editor(tag: str):
    """Spin up Fontra and return the iframe URL the frontend should
    load. Studio-authored axes edit the shadow copy (the coverage
    save regenerates it with seed brace layers). When no shadow
    exists — source-derived axes on a .designspace or plain .glyphs —
    Fontra opens the original source directly, flagged in the
    response as ``editing_original``.
    """
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400

    # Route by the AXIS, not by global shadow existence. A studio
    # (sidecar-declared) axis only exists in the shadow — opening the
    # original for it would let Fontra write into the user's real file
    # at a location that isn't even declared there. Conversely a
    # source-derived axis's layers live in the original — opening the
    # shadow for it would strand the designer's edits in a copy that
    # the next regeneration rebuilds from the original.
    try:
        is_studio_axis = any(
            (ax.get("tag") or "").lower() == tag.lower()
            for ax in _control_axes.list_axes(state.ORIGINAL_PATH)
        )
    except Exception:
        is_studio_axis = False

    if is_studio_axis:
        if not _control_axes.shadow_exists(state.ORIGINAL_PATH):
            # No coverage / no shadow yet — there's nothing to edit.
            return jsonify({
                "error": "No shadow file yet. Add coverage glyphs first so the studio can seed brace layers."
            }), 400
        # Studio-authored axes: Fontra edits the shadow copy; the
        # user's original source stays untouched.
        target_path = _control_axes.shadow_path_for(state.ORIGINAL_PATH)
        # The shadow/ dir contains only the shadow file, so serving
        # the directory exposes nothing else.
        content_root = target_path.parent
        editing_original = False
    else:
        # Source-derived axis (brace layers in the .glyphs, alternate
        # masters in the .designspace): its source of truth IS the
        # original file, so open it directly. Fontra saves write to
        # the user's actual source; the frontend surfaces that. Pass
        # the FILE as the root (Fontra's single-file mode) so sibling
        # font projects in the same folder aren't exposed.
        target_path = state.ORIGINAL_PATH
        content_root = target_path
        editing_original = True

    # Record the session for the proxy (CSS variant) and the injected
    # shim (which layers to show + batch-enable). Studio-axis sessions
    # expose the designspace-navigation panel trimmed to this axis's
    # studio layers; everything else keeps the bare-canvas UI.
    spec = next(
        (
            ax for ax in (_control_axes.list_axes(state.ORIGINAL_PATH) or [])
            if (ax.get("tag") or "").lower() == tag.lower()
        ),
        None,
    ) if is_studio_axis else None
    # The glyph is the frontend's to know (it builds Fontra's URL fragment from
    # it); recording it here lets the injected shim ask for THIS layer's
    # reference measurements without reaching into Fontra's scene state.
    body = request.get_json(silent=True) or {}
    state.FONTRA_EDITOR_SESSION = {
        "studio": bool(is_studio_axis and not editing_original),
        # Authored tag case (fvar tags are case-sensitive; the shim matches
        # layer labels printed in authored case).
        "tag": (spec or {}).get("tag") or tag,
        "axis_name": (spec or {}).get("display_name") or (spec or {}).get("tag") or tag,
        "axis_default": float((spec or {}).get("default") or 0.0),
        "glyph": (body.get("glyph") or "").strip() or None,
    }

    try:
        port = server._ensure_fontra_running(content_root, watch_file=target_path)
    except Exception as exc:
        return jsonify({"error": f"Failed to start Fontra: {exc}"}), 500

    project = target_path.name  # e.g. "CrispyMini.glyphs"
    # Return the same-origin URL through avar2-studio's reverse
    # proxy. This is what unlocks CSS injection (the focused-UI
    # stylesheet that hides irrelevant Fontra panels) — cross-origin
    # iframes don't expose their DOM. The direct ``:8001`` URL is
    # still useful as a "Open in new tab" escape hatch.
    same_origin_url = f"/fontra/editor.html?project={project}"
    direct_url = f"http://127.0.0.1:{port}/editor.html?project={project}"
    return jsonify({
        "success": True,
        "url": same_origin_url,
        "direct_url": direct_url,
        "port": port,
        "project": project,
        "tag": (spec or {}).get("tag") or tag,
        # True when Fontra is serving the user's actual source (no
        # shadow) — saves write straight to it. The frontend shows a
        # notice so that's never a surprise.
        "editing_original": editing_original,
    })


@bp.route('/api/control-axes/<tag>/layers', methods=['PUT'])
def set_control_axis_layers(tag: str):
    """Replace an axis's unified ``layers`` list. Body::

        {"layers": [
            {"glyph": "e", "location": {"crbr": -100}},
            {"glyph": "e", "location": {"crbr": 100}},
            {"glyph": "e", "location": {"crbr": -50, "XOPQ": 407}}
        ]}

    Every brace layer is explicit — no auto seeding at axis-min/max.
    Each entry pins a brace layer at a specific N-D location for one
    glyph. Axes omitted from ``location`` interpolate from masters.
    Saves to sidecar, regenerates the shadow with the new layers,
    and triggers a rebuild. Switches the build path to the shadow
    when there's at least one layer; otherwise reverts to original.
    """
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    entries = data.get("layers")
    if not isinstance(entries, list):
        return jsonify({"error": "Body must include 'layers' as a list."}), 400
    try:
        # Whole-list replace. Prefer the delta endpoint for interactive edits —
        # this one overwrites whatever the caller didn't know about.
        with server.SIDECAR_LOCK:
            stored = _control_axes.set_layers(state.ORIGINAL_PATH, tag, entries)
        server.schedule_shadow_rebuild()
        return jsonify({"success": True, "tag": tag, "layers": stored})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        log.error(f"Error setting control-axis layers: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/control-axes/<tag>/layers/delta', methods=['POST'])
def patch_control_axis_layers(tag: str):
    """Add and/or remove brace layers without sending the whole list. Body::

        {"add": [{"glyph": "e", "location": {...}}],
         "remove": [{"glyph": "e", "location": {...}}]}

    The on-disk list is the base, so an edit made while the client's copy is
    stale composes instead of clobbering — the whole-list PUT silently drops
    layers the caller hadn't loaded yet, which reads as layers "resetting".
    Removals apply before additions, so a replace is remove+add. Serialized on
    SIDECAR_LOCK so concurrent edits can't interleave.
    """
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    add = data.get("add") or []
    remove = data.get("remove") or []
    if not isinstance(add, list) or not isinstance(remove, list):
        return jsonify({"error": "'add' and 'remove' must be lists."}), 400
    if not add and not remove:
        return jsonify({"error": "Nothing to do: provide 'add' and/or 'remove'."}), 400
    try:
        with server.SIDECAR_LOCK:
            stored = _control_axes.apply_layer_delta(state.ORIGINAL_PATH, tag, add=add, remove=remove)
        server.schedule_shadow_rebuild()
        return jsonify({"success": True, "tag": tag, "layers": stored})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        log.error(f"Error setting control-axis layers: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _measure_strokes(glyph_set, hmtx, name):
    """Vertical-stem and horizontal-bar thickness for one glyph.

    A horizontal scanline across the letter cuts its vertical stems; a vertical
    scanline cuts its horizontal bars. Ink runs on each are the stroke widths.
    Returns the thinnest of each, which is what "how heavy is this stroke"
    means for a correction like ymod, or None where the scan is degenerate.
    """
    from fontTools.pens.recordingPen import DecomposingRecordingPen

    pen = DecomposingRecordingPen(glyph_set)
    try:
        glyph_set[name].draw(pen)
    except Exception:
        return None
    contours, cur = [], []
    for op, args in pen.value:
        if op == "moveTo":
            cur = [args[0]]
        elif op in ("lineTo", "curveTo", "qCurveTo"):
            cur.extend([p for p in args if p])
        elif op == "closePath":
            if cur:
                contours.append(cur)
                cur = []
    if cur:
        contours.append(cur)
    if not contours:
        return None

    def runs(coord, vertical):
        vals = []
        for c in contours:
            for i in range(len(c)):
                p, q = c[i], c[(i + 1) % len(c)]
                a1, b1, a2, b2 = (p[0], p[1], q[0], q[1]) if vertical else (p[1], p[0], q[1], q[0])
                if (a1 <= coord < a2) or (a2 <= coord < a1):
                    vals.append(b1 + (coord - a1) * (b2 - b1) / (a2 - a1))
        vals.sort()
        return [vals[i + 1] - vals[i] for i in range(0, len(vals) - 1, 2)]

    # Scan relative to the glyph's OWN ink extent rather than a font metric:
    # self-correcting for any glyph at any location, and it keeps this in step
    # with the live measurement the editor HUD runs on Fontra's in-memory path.
    ys = [pt[1] for c in contours for pt in c]
    if not ys:
        return None
    y_min, y_max = min(ys), max(ys)
    advance = hmtx[name][0]
    # Sample the stems low in the letter (crossbars usually sit near the
    # middle, where a horizontal scan reads one solid run instead of two stems).
    stems = [r for r in runs(y_min + (y_max - y_min) * 0.25, False) if r > 5]
    bars = [r for r in runs(advance * 0.5, True) if r > 5]
    out = {"advance": round(float(advance), 1)}
    if stems:
        out["stem"] = round(float(min(stems)), 1)
    if bars:
        out["bar"] = round(float(min(bars)), 1)
    if stems and bars:
        out["contrast"] = round(float(min(stems)) / float(min(bars)), 2)
    # The measured polygon itself, for the HUD's overlay preview: what the
    # scanlines actually cut (off-curves as vertices), so the drawing and
    # the numbers can never disagree.
    out["contours"] = [[[round(float(x), 1), round(float(y), 1)] for x, y in c]
                       for c in contours]
    return out


@bp.route('/api/control-axes/<tag>/reseed', methods=['POST'])
def reseed_control_axis_layers(tag: str):
    """Re-seed brace layers from the CURRENT source. Body::

        { "layers": [{"glyph": "e", "location": {...}}], "force": false }

    Omit ``layers`` to target the whole axis.

    A drawn layer's outline is captured into the sidecar and restored in
    preference to re-seeding, so it survives the shadow being wiped — and stays
    frozen against later edits to the masters it was drawn over. Re-seeding
    pulls the updated source through and DISCARDS the drawing, so layers
    holding one come back as 409 ``blocked`` unless ``force`` is set. There is
    no undo once the sidecar is written.
    """
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    body = request.get_json(silent=True) or {}
    layers = body.get("layers")
    if layers is not None and not isinstance(layers, list):
        return jsonify({"error": "layers must be a list of {glyph, location}"}), 400
    force = bool(body.get("force"))
    try:
        result = _control_axes.reseed_layers(state.ORIGINAL_PATH, tag, layers, force)
    except KeyError:
        return jsonify({"error": f"control axis '{tag}' not found"}), 404
    except Exception as e:
        log.error(f"Error re-seeding control axis layers: {e}")
        return jsonify({"error": str(e)}), 500

    if result["blocked"]:
        names = ", ".join(sorted({str(b["glyph"]) for b in result["blocked"]}))
        return jsonify({
            "error": "Some layers hold hand-drawn outlines",
            "detail": ("Re-seeding replaces the drawing with a fresh "
                       "interpolation of the current source, and cannot be "
                       "undone. Affected glyphs: %s." % names),
            **result,
        }), 409

    if result["reseeded"]:
        server.schedule_shadow_rebuild()
    return jsonify(result)


@bp.route('/api/control-axes/<tag>/reference-metrics', methods=['GET'])
def control_axis_reference_metrics(tag: str):
    """Stroke thicknesses for a layer's glyph and its reference glyphs, all cut
    at that layer's exact coordinates with the secondary axis OFF.

    Drawing a horizontal correction means matching a glyph's bars to the ones
    that already read correctly — E's to H's, at the same designspace point.
    Fontra can measure what you have drawn (its ruler stays enabled in studio
    sessions) but it cannot tell you what to aim AT, because the reference is
    another glyph. These are those target numbers.

    The edited glyph's own figures are its pre-edit state — where you started,
    not what is on screen once you begin drawing.

    Query: ``glyph`` (required), ``index`` (which layer, default 0),
    ``refs`` (comma-separated reference glyphs, default "H,N,O").
    """
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    if not (state.VARIABLE_FONT_PATH and Path(state.VARIABLE_FONT_PATH).exists()):
        return jsonify({"error": "Font not built yet"}), 404
    glyph = (request.args.get("glyph") or "").strip()
    if not glyph:
        return jsonify({"error": "glyph is required"}), 400
    try:
        index = int(request.args.get("index") or 0)
    except ValueError:
        index = 0
    refs = [g.strip() for g in (request.args.get("refs") or "H,N,O").split(",") if g.strip()]
    # The EDITED layer's location, sent live by the Fontra shim (scene
    # fontLocationSourceMapped — keyed by axis NAME or tag). Without it
    # the lookup below falls back to the glyph's FIRST sidecar layer,
    # which is simply the wrong location whenever a glyph carries
    # several layers on the axis (e's six ymod layers): the HUD then
    # shows a self-consistent guide and overlay that are both measured
    # somewhere else.
    explicit_location = None
    loc_param = request.args.get("location")
    if loc_param:
        try:
            parsed = json.loads(loc_param)
            if isinstance(parsed, dict):
                explicit_location = {str(k): float(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            return jsonify({"error": "location must be a JSON object of axis: value"}), 400

    axis = _control_axes.find_axis(state.ORIGINAL_PATH, tag)
    if axis is None:
        return jsonify({"error": f"No secondary axis '{tag}'"}), 404
    layers = [lyr for lyr in (axis.get("layers") or []) if lyr.get("glyph") == glyph]
    if not layers and explicit_location is None:
        return jsonify({"error": f"'{glyph}' has no layers on '{tag}'"}), 404
    layer = layers[min(index, len(layers) - 1)] if layers else {}

    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    try:
        font = TTFont(str(state.VARIABLE_FONT_PATH))
        fvar_axes = {a.axisTag: a for a in font["fvar"].axes}
        if explicit_location is not None:
            # Normalize keys: the scene speaks axis NAMES (source space);
            # the sidecar and fvar speak tags. Accept both.
            name_to_tag = {}
            try:
                src_font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
                for ax in _source_font.get_axes(src_font):
                    name_to_tag[str(ax.get("name") or "").lower()] = str(ax.get("tag") or "")
                    name_to_tag[str(ax.get("tag") or "").lower()] = str(ax.get("tag") or "")
            except Exception:
                pass
            for a_tag in fvar_axes:
                name_to_tag.setdefault(a_tag.lower(), a_tag)
            stored = {}
            for k, v in explicit_location.items():
                mapped = name_to_tag.get(str(k).lower())
                if mapped:
                    stored[mapped.lower()] = v
        else:
            stored = {str(k).lower(): v for k, v in (layer.get("location") or {}).items()}
        location = {}
        for a_tag, a in fvar_axes.items():
            if a_tag.lower() == tag.lower():
                location[a_tag] = a.defaultValue
            elif a_tag.lower() in stored:
                location[a_tag] = float(stored[a_tag.lower()])
            else:
                location[a_tag] = a.defaultValue
        try:
            inst = instantiateVariableFont(font, location, inplace=False)
        except NotImplementedError:
            font = TTFont(str(state.VARIABLE_FONT_PATH))
            if "avar" in font:
                del font["avar"]
            inst = instantiateVariableFont(font, location, inplace=False)

        gs = inst.getGlyphSet()
        hmtx = inst["hmtx"]
        os2 = inst["OS/2"] if "OS/2" in inst else None
        x_height = float(getattr(os2, "sxHeight", 0) or 0) or 1000.0
        cap_height = float(getattr(os2, "sCapHeight", 0) or 0) or 1400.0

        order = set(inst.getGlyphOrder())
        measured = {}
        for name in [glyph] + [r for r in refs if r != glyph]:
            if name not in order:
                continue
            m = _measure_strokes(gs, hmtx, name)
            if m:
                measured[name] = m
        return jsonify({
            "tag": tag,
            "glyph": glyph,
            "location": {k: v for k, v in location.items() if k.lower() != tag.lower()},
            "metrics": measured,
            # The scan geometry, so a caller measuring the LIVE glyph in the
            # editor can use the same rule and produce comparable numbers.
            "x_height": round(x_height, 1),
            "cap_height": round(cap_height, 1),
            "note": "Figures for the edited glyph are its pre-edit state.",
        })
    except Exception as e:
        log.error(f"Error measuring reference metrics: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/control-axes/<tag>', methods=['PATCH'])
def update_control_axis(tag: str):
    """Edit an existing control axis's display_name / min / max /
    default. Tag is immutable — a rename would need to migrate every
    layer's location dict. Any field left absent from the body is
    left unchanged. Regenerates the shadow after a successful edit
    so the preview reflects the new range immediately."""
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        entry = _control_axes.update_axis(
            state.ORIGINAL_PATH,
            tag,
            display_name=data.get("display_name"),
            default=data.get("default"),
            min_value=data.get("min"),
            max_value=data.get("max"),
        )
        try:
            # Full re-resolve, not a bare regenerate_shadow: the compile
            # source is the TRANSFORMED shadow (grade + source-stage
            # transforms layered on the control shadow), and refreshing
            # only stage 1 left it stale — an axis-default edit then
            # rebuilt with the OLD default whenever grade/rounding was
            # enabled. See docs/decisions/0011-ymod-baked-corrections.md.
            server._resolve_active_source()
            # See set_control_axis_layers: keep the last-good font on failure.
            try:
                server.trigger_build()
            except Exception as build_exc:
                log.warning(f"Warning: rebuild after axis update failed: {build_exc}")
        except Exception as shadow_exc:
            log.warning(f"Warning: shadow refresh after update failed: {shadow_exc}")
        return jsonify({"success": True, "axis": entry})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        log.error(f"Error updating control axis: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/control-axes/<tag>', methods=['DELETE'])
def delete_control_axis(tag: str):
    """Remove a control-axis declaration from the sidecar. If the
    sidecar still has any axes, regenerate the shadow without the
    deleted one; if it's now empty, remove the shadow entirely so
    the build pipeline falls back to the original source."""
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    try:
        removed = _control_axes.remove_axis(state.ORIGINAL_PATH, tag)
        if not removed:
            return jsonify({"error": f"control axis '{tag}' not found"}), 404

        # Re-derive the shadow + build target. If the sidecar still
        # has any axis with brace layers, the shadow stays active.
        # Otherwise the shadow can be dropped and the build reverts to
        # the original. (Gate on ``layers`` — the flat per-axis list
        # of {glyph, location}; ``coverage`` is a legacy key the
        # current sidecar never emits, so gating on it left the build
        # stuck on the original.)
        try:
            # Re-derive the active source from the REMAINING control axes and
            # any grades — grade must survive deleting an unrelated control axis.
            server._resolve_active_source()
            # Drop the shadow dir only when NEITHER feature still needs it.
            if (state.GLYPHS_PATH == state.ORIGINAL_PATH
                    and not _control_axes.list_axes(state.ORIGINAL_PATH)
                    and not _grade.list_graded_instances(state.ORIGINAL_PATH)):
                _control_axes.remove_shadow(state.ORIGINAL_PATH)
            # See set_control_axis_layers: keep the last-good font on failure.
            try:
                server.trigger_build()
            except Exception as build_exc:
                log.warning(f"Warning: rebuild after delete failed: {build_exc}")
        except Exception as shadow_exc:
            log.warning(f"Warning: shadow refresh after delete failed: {shadow_exc}")

        return jsonify({"success": True, "tag": tag})
    except Exception as e:
        log.error(f"Error deleting control axis: {e}")
        return jsonify({"error": str(e)}), 500
