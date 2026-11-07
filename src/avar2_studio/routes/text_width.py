"""Text-width route (audit §3 step e group) — shape text against the
served build at arbitrary axis coordinates."""

import json

from flask import Blueprint, jsonify, request
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

from .. import state
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("text_width", __name__)


@bp.route('/api/text-width', methods=['GET'])
def get_text_width():
    """
    Measure advance width of text at specific variable font coordinates.

    Query parameters:
    - text: Text string to measure (default: sample text)
    - coordinates: JSON object with axis coordinates (e.g., {"XTRA": 627.0, "XOPQ": 187.672})
    - font_size_rem: Font size in rem units (default: 2.0)

    Returns:
    - width_pixels: Total advance width in pixels
    - width_font_units: Total advance width in font units
    - width_em: Total advance width in em units
    - upm: Units per em from font
    """
    try:
        import os
        import tempfile

        # Get parameters
        text = request.args.get('text', 'The Quick Brown Fox Jumps Over The Lazy Dog 0123456789 &!')
        font_size_rem = float(request.args.get('font_size_rem', 2.0))

        # Parse coordinates from JSON string
        coordinates_json = request.args.get('coordinates', '{}')
        try:
            coordinates = json.loads(coordinates_json)
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid coordinates JSON"}), 400

        # Measure against whatever the avar2 build produced (the same font
        # the preview is rendering). SPAC-axis lookup used to run here as a
        # preferred path, but SPAC is deferred to v2 — falling back to
        # VARIABLE_FONT_PATH keeps the measurement consistent with the
        # preview and avoids regenerating UFOs every time the user types.
        if state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists():
            font_path = state.VARIABLE_FONT_PATH
        else:
            return jsonify({"error": "Variable font not built yet."}), 404

        # Load font
        font = TTFont(str(font_path))

        # Get UPM (units per em)
        upm = font['head'].unitsPerEm

        # Check if font has fvar table (variable font)
        if 'fvar' not in font:
            # Use font as-is for non-variable fonts
            instance_font = font
        else:
            # Get axis tags from fvar table
            fvar = font['fvar']
            # Map lowercase input tags to original fvar axis tags
            axis_tag_map = {axis.axisTag.lower(): axis.axisTag for axis in fvar.axes}

            # Build location dictionary for instancer
            # Use original axis tag case from fvar table
            location = {}
            for tag, value in coordinates.items():
                tag_lower = tag.lower()
                if tag_lower in axis_tag_map:
                    # Use original case from fvar table
                    original_tag = axis_tag_map[tag_lower]
                    location[original_tag] = float(value)

            # Set other axes to their defaults from fvar table
            for axis in fvar.axes:
                if axis.axisTag not in location:
                    location[axis.axisTag] = axis.defaultValue

            # Create a temporary instance font
            with tempfile.NamedTemporaryFile(suffix='.ttf', delete=False) as tmp_file:
                tmp_path = tmp_file.name

            try:
                # Instantiate the font at the specified location
                instance_font = instancer.instantiateVariableFont(font, location)
                instance_font.save(tmp_path)
            except Exception as e:
                log.error(f"Error during instantiation: {e}")
                import traceback
                traceback.print_exc()
                return jsonify({"error": f"Font instantiation failed: {e}"}), 500
            finally:
                # Clean up temporary file
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        # Shape the text with uharfbuzz against the instantiated font.
        # This applies kerning (GPOS) and substitutions (GSUB) the same way
        # a browser shaper does, so the displayed Width matches the laid-out
        # width — not just the sum of raw hmtx advances. The per-glyph
        # breakdown also falls out of the same shape() call, which is what
        # the grade-master delta view needs.
        from io import BytesIO

        import uharfbuzz as hb

        font_buffer = BytesIO()
        instance_font.save(font_buffer)
        font_bytes = font_buffer.getvalue()

        hb_face = hb.Face(font_bytes)
        hb_font = hb.Font(hb_face)
        hb_font.scale = (upm, upm)  # advances in font units, not 26.6 fixed-point

        hb_buf = hb.Buffer()
        hb_buf.add_str(text)
        hb_buf.guess_segment_properties()
        hb.shape(hb_font, hb_buf)

        infos = hb_buf.glyph_infos
        positions = hb_buf.glyph_positions
        glyph_order = instance_font.getGlyphOrder()

        total_width_font_units = 0.0
        per_glyph = []
        for i, (info, pos) in enumerate(zip(infos, positions)):
            advance = float(pos.x_advance)
            total_width_font_units += advance

            gid = info.codepoint  # post-shaping this is the glyph id
            glyph_name = glyph_order[gid] if 0 <= gid < len(glyph_order) else f"gid{gid}"

            # In uharfbuzz, ``cluster`` is the input-string index this glyph
            # came from. Ligatures collapse multiple input chars into one
            # cluster; decompositions split one input char into many. For
            # Latin without GSUB it's 1:1.
            cluster = info.cluster
            next_cluster = infos[i + 1].cluster if i + 1 < len(infos) else len(text)
            input_text = text[cluster:next_cluster]

            per_glyph.append({
                "glyph_id": gid,
                "glyph_name": glyph_name,
                "cluster": cluster,
                "text": input_text,
                "advance_font_units": advance,
            })

        # Convert to pixels (assuming 16px = 1rem, the browser default)
        pixels_per_rem = 16.0
        font_size_pixels = font_size_rem * pixels_per_rem
        width_pixels = (total_width_font_units / upm) * font_size_pixels
        width_em = total_width_font_units / upm

        return jsonify({
            "width_pixels": width_pixels,
            "width_font_units": total_width_font_units,
            "width_em": width_em,
            "upm": upm,
            "font_size_rem": font_size_rem,
            "font_size_pixels": font_size_pixels,
            "per_glyph": per_glyph,
        })

    except Exception as e:
        log.error(f"Error measuring text width: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
