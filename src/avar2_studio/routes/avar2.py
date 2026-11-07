"""avar2 mapping routes (audit §3 step e group) — the mapping CSV + axis
metadata CRUD, instance/row matching, and the built-font avar evaluation
that drives the slider reflection."""

import json
from pathlib import Path
from typing import Dict, Optional

from flask import Blueprint, jsonify, request

from .. import control_axes as _control_axes
from .. import csv_io as _csv_io
from .. import server, state
from .. import sidecar_io as _sidecar_io
from .. import source_font as _source_font
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("avar2", __name__)


def _validate_axis_tag(tag: str) -> tuple[bool, Optional[str]]:
    """Validate OpenType axis tag. Returns (is_valid, error_message)."""
    if not tag:
        return False, "Tag cannot be empty"
    if len(tag) != 4:
        return False, "Tag must be exactly 4 characters"
    if not tag.islower():
        return False, "Tag must be lowercase"
    if not tag.isalnum():
        return False, "Tag must contain only alphanumeric characters"
    return True, None


@server._sidecar_locked
def _add_missing_instance_to_csv(instance_name: str, glyphs_coords: Dict[str, float], csv_path: Path) -> bool:
    """Add a missing instance to CSV with blank traditional axis values."""
    import csv
    try:
        # Use normalized read function

        rows, fieldnames, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

        if not fieldnames:
            return False

        # Create new row with instance name
        new_row = {"Instance Name": instance_name}

        # Initialize all columns (use normalized fieldnames)
        for col in fieldnames:
            if col == "Instance Name":
                continue
            elif col in in_cols:
                # Traditional axes: blank
                new_row[col] = ""
            elif col in out_cols:
                # Parametric axes: use value from Glyphs (match by uppercase)
                # Find matching glyphs coordinate (case-insensitive)
                glyphs_key = None
                for glyphs_tag in glyphs_coords.keys():
                    if glyphs_tag.upper() == col:
                        glyphs_key = glyphs_tag
                        break
                if glyphs_key:
                    new_row[col] = str(glyphs_coords[glyphs_key])
                else:
                    new_row[col] = ""
            else:
                # Other columns: blank
                new_row[col] = ""

        # Add new row
        rows.append(new_row)

        # Write updated CSV (use normalized fieldnames)
        _csv_io.backup_sidecar(csv_path)
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return True
    except Exception as e:
        log.error(f"Error adding instance to CSV: {e}")
        return False


@bp.route('/api/mapped-location', methods=['GET'])
def mapped_location():
    """Evaluate the BUILT font's avar table at a user-space location.

    Returns the effective post-mapping value of every fvar axis,
    denormalized back to user space. The preview uses this to make the
    parametric sliders FOLLOW the avar2 mappings as the designer drags
    wght/opsz — computed from the compiled table itself (fontTools
    ``avar.renormalizeLocation``), not a JS reimplementation of the
    mapping model, so what the sliders show is exactly what the font
    does. Fonts without an avar table just echo the input location.
    """
    if state.VARIABLE_FONT_PATH is None or not state.VARIABLE_FONT_PATH.exists():
        return jsonify({"error": "No built font"}), 404
    try:
        coords = json.loads(request.args.get("coordinates", "{}")) or {}
    except (ValueError, TypeError):
        return jsonify({"error": "coordinates must be a JSON object"}), 400

    try:
        return jsonify({"mapped": server._evaluate_mapped_location(coords)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/avar2/instances', methods=['GET'])
def get_avar2_instances():
    """Get instances matched to avar2 mappings. Automatically adds missing instances to CSV."""
    try:
        csv_path = server._get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({
                "error": "avar2-mappings.csv not found",
                "suggestion": "Set CSV path or place avar2-mappings.csv in same directory as Glyphs file"
            }), 404

        # Import matching function

        # Get matches
        matches = _csv_io.match_instances(state.GLYPHS_PATH, csv_path)

        # Add missing instances to CSV — unless an existing row already
        # sits at the instance's exact parametric location. A RENAMED row
        # keeps its coordinates, and the source still carries the old
        # name (the studio never writes the original): re-adding by name
        # would resurrect the old instance as a coordinate duplicate on
        # every refresh after a rename.
        rows, _fns, _in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)
        existing_locs = []
        for row in rows:
            loc = {}
            for col in out_cols:
                v = (row.get(col) or "").strip()
                if v:
                    try:
                        loc[col.upper()] = float(v)
                    except ValueError:
                        pass
            if loc:
                existing_locs.append(loc)

        out_upper = {str(c).upper() for c in out_cols}

        def _row_already_at(coords):
            # Compare only over the CSV's parametric columns: a source
            # instance also reports control axes (lcwd/ymod at 0) that
            # rows never carry.
            cu = {str(k).upper(): float(v) for k, v in (coords or {}).items()
                  if str(k).upper() in out_upper}
            if not cu:
                return False
            return any(
                all(abs(loc.get(k, 1e9) - v) <= 0.05 for k, v in cu.items())
                for loc in existing_locs
            )

        added_count = 0
        for match in matches:
            if match.get("match_status") == "missing_in_csv":
                instance_name = match["instance_name"]
                glyphs_coords = match.get("glyphs_coordinates", {})
                if _row_already_at(glyphs_coords):
                    continue
                if _add_missing_instance_to_csv(instance_name, glyphs_coords, csv_path):
                    added_count += 1

        # If we added instances, reload matches
        if added_count > 0:
            matches = _csv_io.match_instances(state.GLYPHS_PATH, csv_path)

        # And keep renamed instances out of the LISTING too: a source
        # instance whose location an existing row holds is that row, not
        # a second instance to display.
        matches = [m for m in matches
                   if not (m.get("match_status") in ("missing_in_csv", "mismatch")
                           and not any(r.get("Instance Name", "").strip() == m.get("instance_name")
                                       for r in rows)
                           and _row_already_at(m.get("glyphs_coordinates", {})))]

        return jsonify({
            "instances": matches,
            "csv_path": str(csv_path),
            "added_instances": added_count
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/avar2/axes', methods=['GET'])
def get_avar2_axes():
    """Get traditional axes (in:) and parametric axes (out:) from CSV, including metadata."""
    try:
        csv_path = server._get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({
                "error": "avar2-mappings.csv not found"
            }), 404


        rows, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

        # Normalize traditional axis names
        traditional_axes = [_csv_io.normalize_in_axis_name(col) for col in in_cols]

        # Load metadata
        metadata = server._load_axis_metadata()

        # Get parametric axes from the source file and populate metadata.
        glyphs_axes_info = {}
        if state.GLYPHS_PATH and state.GLYPHS_PATH.exists():
            try:
                font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
                for axis in _source_font.get_axes(font):
                    tag = axis["tag"]
                    tag_upper = tag.upper()
                    glyphs_axes_info[tag_upper] = {
                        "display_name": axis.get("name") or tag_upper,
                        "registered_tag": tag.lower(),
                        # Only axes with master coverage are truly
                        # parametric. Empty axes (declared in source but
                        # no master deltas) are user-editable avar2 inputs.
                        "is_parametric": axis.get("has_master_coverage", True),
                        "min": float(axis["min"]),
                        "max": float(axis["max"]),
                    }
            except Exception as e:
                log.warning(f"Warning: Could not read axes from source file: {e}")

        # Build axes with metadata
        # If axis doesn't exist in metadata, create default entry and save it
        axes_with_metadata = {}
        metadata_updated = False

        # First, populate parametric axes from Glyphs file (these are in out_cols)
        for col in out_cols:
            col_upper = col.upper()
            if col_upper in glyphs_axes_info:
                # This is a parametric axis from Glyphs file
                glyphs_info = glyphs_axes_info[col_upper]
                if col_upper not in metadata:
                    metadata[col_upper] = {
                        "display_name": glyphs_info["display_name"],
                        "registered_tag": glyphs_info["registered_tag"],
                        "is_parametric": glyphs_info.get("is_parametric", True),
                        "min": glyphs_info.get("min", 0.0),
                        "max": glyphs_info.get("max", 1000.0)
                    }
                    metadata_updated = True
                else:
                    desired_parametric = glyphs_info.get("is_parametric", True)
                    if metadata[col_upper].get("is_parametric") != desired_parametric:
                        metadata[col_upper]["is_parametric"] = desired_parametric
                        metadata_updated = True
                    # Always update display_name, registered_tag, min, and max from Glyphs (source of truth)
                    if metadata[col_upper].get("display_name") != glyphs_info["display_name"]:
                        metadata[col_upper]["display_name"] = glyphs_info["display_name"]
                        metadata_updated = True
                    if metadata[col_upper].get("registered_tag") != glyphs_info["registered_tag"]:
                        metadata[col_upper]["registered_tag"] = glyphs_info["registered_tag"]
                        metadata_updated = True
                    # Update min/max from Glyphs (parametric axes should always match Glyphs)
                    if metadata[col_upper].get("min") != glyphs_info.get("min", 0.0):
                        metadata[col_upper]["min"] = glyphs_info.get("min", 0.0)
                        metadata_updated = True
                    if metadata[col_upper].get("max") != glyphs_info.get("max", 1000.0):
                        metadata[col_upper]["max"] = glyphs_info.get("max", 1000.0)
                        metadata_updated = True
                axes_with_metadata[col_upper] = metadata[col_upper]
            else:
                # Parametric axis not found in Glyphs (shouldn't happen, but handle gracefully)
                if col_upper not in metadata:
                    normalized_tag = _csv_io.normalize_in_axis_name(col)
                    metadata[col_upper] = {
                        "display_name": col,
                        "registered_tag": normalized_tag,
                        "is_parametric": True,
                        "min": -1000,
                        "max": 1000
                    }
                    metadata_updated = True
                axes_with_metadata[col_upper] = metadata[col_upper]

        # Then, handle traditional axes (not in Glyphs file)
        # Map of normalized tags to default display names
        default_display_names = {
            "wght": "Weight",
            "wdth": "Width",
            "opsz": "Optical Size",
            "cntr": "Contrast",
            "spac": "Spacing",
            "grad": "Grade",
            "slnt": "Slant",
            "ital": "Italic"
        }

        for col in in_cols:
            if col not in metadata:
                # Create default entry for traditional axis (not in Glyphs file)
                normalized_tag = _csv_io.normalize_in_axis_name(col)
                # Use proper display name if available, otherwise use column name
                default_display_name = default_display_names.get(normalized_tag.lower(), col)
                # Seed with the axis's real extent where we can recover it —
                # the placeholder only stands in when nothing defines the axis.
                derived = server._derive_traditional_range(normalized_tag, rows, col)
                axis_min, axis_max = derived or (server._PLACEHOLDER_MIN, server._PLACEHOLDER_MAX)
                metadata[col] = {
                    "display_name": default_display_name,
                    "registered_tag": normalized_tag,
                    "min": axis_min,
                    "max": axis_max,
                    "is_parametric": False  # Mark as traditional (not in Glyphs file)
                }
                metadata_updated = True
            else:
                # The flag is DERIVED from the source's real axes, never
                # trusted from the file: a stale is_parametric=true (from
                # an era when the tag WAS a source axis) otherwise locks
                # the mapping slider and the edit/delete modal read-only
                # forever.
                normalized_tag = _csv_io.normalize_in_axis_name(col)
                glyphs_axis_tags = server._get_glyphs_axis_tags()
                desired_flag = normalized_tag in glyphs_axis_tags
                if metadata[col].get("is_parametric") != desired_flag:
                    metadata[col]["is_parametric"] = desired_flag
                    metadata_updated = True

                # Update display_name if it's still the column name (tag) - migrate to proper display name
                normalized_tag = _csv_io.normalize_in_axis_name(col)
                if metadata[col].get("display_name") == col and normalized_tag.lower() in default_display_names:
                    metadata[col]["display_name"] = default_display_names[normalized_tag.lower()]
                    metadata_updated = True

            axes_with_metadata[col] = metadata[col]

        # Repair traditional axes still stuck on the placeholder extent —
        # entries seeded before their CSV column had values, or before the
        # transform that injects them was enabled. Parametric axes already
        # re-sync from the source above; this gives traditional axes the
        # same self-healing instead of leaking -1000/1000 into real fvar.
        if server._repair_placeholder_ranges(metadata, rows, in_cols):
            metadata_updated = True

        # Save updated metadata if we added any new axes
        if metadata_updated:
            server._save_axis_metadata(metadata)

        # Ensure every axis entry has a numeric `default` so the frontend
        # can initialise sliders without hardcoding values.
        TRADITIONAL_DEFAULTS = server.TRADITIONAL_AXIS_DEFAULTS
        for entry in axes_with_metadata.values():
            if "default" in entry and isinstance(entry["default"], (int, float)):
                continue
            tag = (entry.get("registered_tag") or "").lower()
            if entry.get("is_parametric"):
                # Parametric: prefer min (matches get_axes_from_glyphs behaviour).
                entry["default"] = entry.get("min", 0.0)
            elif tag in TRADITIONAL_DEFAULTS:
                entry["default"] = TRADITIONAL_DEFAULTS[tag]
            else:
                lo = entry.get("min", 0.0) or 0.0
                hi = entry.get("max", 0.0) or 0.0
                entry["default"] = (lo + hi) / 2.0

        # Exclude control axes — they belong to the CONTROL AXES
        # section, not AVAR2 MAPPINGS. A control axis has no master
        # coverage (so it would otherwise be misclassified as a
        # traditional/avar2 target), but it's neither parametric nor an
        # avar2 mapping input; it's driven by brace layers.
        control_tags_upper = set()
        if state.ORIGINAL_PATH is not None:
            try:
                control_tags_upper = {
                    str(a.get("tag", "")).upper()
                    for a in _control_axes.list_axes(state.ORIGINAL_PATH)
                    if a.get("tag")
                }
            except Exception:
                control_tags_upper = set()
        if control_tags_upper:
            axes_with_metadata = {
                k: v for k, v in axes_with_metadata.items()
                if k.upper() not in control_tags_upper
            }
            in_cols = [
                c for c in in_cols
                if c.upper() not in control_tags_upper
                and _csv_io.normalize_in_axis_name(c).upper() not in control_tags_upper
            ]
            traditional_axes = [
                t for t in traditional_axes if t.upper() not in control_tags_upper
            ]

        return jsonify({
            "traditional_axes": {
                "columns": in_cols,
                "normalized": traditional_axes
            },
            "parametric_axes": out_cols,  # All parametric axes from Glyphs file
            "metadata": axes_with_metadata  # Includes all axes with is_parametric flag + default
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/avar2/axis', methods=['POST'])
@server._sidecar_locked
def add_avar2_axis():
    """Add a new traditional axis to the CSV."""
    try:
        csv_path = server._get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404

        # Check for external edits
        if server._check_csv_external_edit(csv_path):
            return jsonify({
                "error": "The studio's saved data changed outside this window. Please reload.",
                "reload_required": True
            }), 409

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        axis_name = data.get("axis_name")  # CSV column header (e.g., "WGHT")
        display_name = data.get("display_name", axis_name)  # Display name (e.g., "Weight")
        registered_tag = data.get("registered_tag", "").lower()  # OpenType tag (e.g., "wght")
        default_value = data.get("default_value", 0)
        min_value = data.get("min", -1000)
        max_value = data.get("max", 1000)

        # Validate inputs
        if not axis_name:
            return jsonify({"error": "axis_name is required"}), 400

        is_valid, error_msg = _validate_axis_tag(registered_tag)
        if not is_valid:
            return jsonify({"error": f"Invalid registered tag: {error_msg}"}), 400

        # Check if tag exists in Glyphs file (parametric axes) - cannot add traditional axis with same tag
        glyphs_axis_tags = server._get_glyphs_axis_tags()
        if registered_tag.lower() in glyphs_axis_tags:
            return jsonify({
                "error": f"Registered tag '{registered_tag}' already exists in Glyphs file as a parametric axis. Traditional axes cannot use tags that exist in the Glyphs file."
            }), 400

        if min_value < -1000 or min_value > 1000:
            return jsonify({"error": "min must be between -1000 and 1000"}), 400
        if max_value < -1000 or max_value > 1000:
            return jsonify({"error": "max must be between -1000 and 1000"}), 400
        if min_value >= max_value:
            return jsonify({"error": "min must be less than max"}), 400

        # Normalized up front: the duplicate-tag check below compares against
        # it, and it used to be assigned only after that loop — a
        # use-before-assignment that stayed hidden until the font had axis
        # metadata for the loop to iterate over.
        axis_name_normalized = axis_name.upper()

        # Check for duplicate registered tags in existing metadata
        metadata = server._load_axis_metadata()
        for existing_axis, existing_meta in metadata.items():
            if existing_meta.get("registered_tag") == registered_tag and existing_axis != axis_name_normalized:
                return jsonify({"error": f"Registered tag '{registered_tag}' already used by axis '{existing_axis}'"}), 400

        # Read CSV using normalized function

        rows, fieldnames, _, _, fieldname_mapping = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

        # Check if axis already exists (case-insensitive)
        if axis_name_normalized in fieldnames:
            return jsonify({"error": f"Axis '{axis_name}' already exists"}), 400

        # Add new column to CSV (use normalized name). Existing rows get
        # a BLANK cell, not the axis default: the designer hasn't
        # authored a mapping value for them yet, and a stamped number
        # becomes real data — it feeds the avar2 in: locations and drags
        # the built axis's range/default to wherever the stamp landed
        # (the "opsz slider goes to 0" bug). Blank renders as "—" in the
        # UI and is omitted from the mapping until assigned.
        fieldnames.append(axis_name_normalized)
        for row in rows:
            row[axis_name_normalized] = ""

        # Write updated CSV (use normalized fieldnames)
        import csv
        _csv_io.backup_sidecar(csv_path)
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        server._update_csv_modification_time(csv_path)

        # Save metadata (use normalized name)
        # Check if this is a parametric axis (shouldn't happen, but be safe)
        is_parametric = False
        glyphs_axis_tags = server._get_glyphs_axis_tags()
        if registered_tag.lower() in glyphs_axis_tags:
            is_parametric = True

        metadata[axis_name_normalized] = {
            "display_name": display_name,
            "registered_tag": registered_tag,
            "is_parametric": is_parametric,
            "min": min_value,
            # The declared default was previously dropped on the floor —
            # the modal collects it, so record it. (It feeds the axis
            # metadata consumers; the CSV backfill deliberately does NOT
            # use it, see above.)
            "default": default_value,
            "max": max_value
        }
        server._save_axis_metadata(metadata)

        return jsonify({
            "success": True,
            "axis_name": axis_name_normalized,
            "metadata": metadata[axis_name_normalized]
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/avar2/axis/<axis_name>', methods=['PUT'])
@server._sidecar_locked
def update_avar2_axis(axis_name: str):
    """Update axis metadata. Cannot edit axes that exist in Glyphs file (parametric axes)."""
    try:
        csv_path = server._get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404

        # Check for external edits
        if server._check_csv_external_edit(csv_path):
            return jsonify({
                "error": "The studio's saved data changed outside this window. Please reload.",
                "reload_required": True
            }), 409

        # Check if this axis exists in Glyphs file (parametric axis) - cannot edit

        _, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

        # Normalize axis_name to uppercase for lookup
        axis_name_normalized = axis_name.upper()

        # If axis is in parametric axes (exists in Glyphs file), cannot edit
        if axis_name_normalized in out_cols:
            return jsonify({
                "error": f"Cannot edit axis '{axis_name}' - it exists in the Glyphs file as a parametric axis. Parametric axes are managed in the Glyphs file, not in avar2 mappings."
            }), 403

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        new_display_name = data.get("display_name")
        new_registered_tag = data.get("registered_tag", "").lower()
        new_min = data.get("min")
        new_max = data.get("max")

        # Normalize axis_name to uppercase for lookup
        axis_name_normalized = axis_name.upper()

        # Load metadata
        metadata = server._load_axis_metadata()
        if axis_name_normalized not in metadata:
            return jsonify({"error": f"Axis '{axis_name}' not found in metadata"}), 404

        # Validate registered tag if provided
        if new_registered_tag:
            is_valid, error_msg = _validate_axis_tag(new_registered_tag)
            if not is_valid:
                return jsonify({"error": f"Invalid registered tag: {error_msg}"}), 400

            # Check if tag exists in Glyphs file (parametric axes) - cannot use same tag
            glyphs_axis_tags = server._get_glyphs_axis_tags()
            if new_registered_tag.lower() in glyphs_axis_tags:
                return jsonify({
                    "error": f"Registered tag '{new_registered_tag}' already exists in Glyphs file as a parametric axis. Traditional axes cannot use tags that exist in the Glyphs file."
                }), 400

            # Check for duplicate tags in existing metadata
            for existing_axis, existing_meta in metadata.items():
                if existing_axis != axis_name_normalized and existing_meta.get("registered_tag") == new_registered_tag:
                    return jsonify({"error": f"Registered tag '{new_registered_tag}' already used by axis '{existing_axis}'"}), 400

        # Validate min/max if provided
        if new_min is not None:
            if new_min < -1000 or new_min > 1000:
                return jsonify({"error": "min must be between -1000 and 1000"}), 400
        if new_max is not None:
            if new_max < -1000 or new_max > 1000:
                return jsonify({"error": "max must be between -1000 and 1000"}), 400
        if new_min is not None and new_max is not None and new_min >= new_max:
            return jsonify({"error": "min must be less than max"}), 400
        new_default = data.get("default")
        if new_default is not None and new_min is not None and new_max is not None:
            if not (new_min <= new_default <= new_max):
                return jsonify({"error": "default must be between min and max"}), 400

        # Read CSV
        import csv
        rows = []
        fieldnames = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            for row in reader:
                rows.append(row)

        if axis_name not in fieldnames:
            return jsonify({"error": f"Axis '{axis_name}' not found in the studio data"}), 404

        # Update metadata (use normalized name)
        if axis_name_normalized not in metadata:
            # Create metadata entry if it doesn't exist
            metadata[axis_name_normalized] = {
                "display_name": axis_name_normalized,
                "registered_tag": _csv_io.normalize_in_axis_name(axis_name_normalized),
                "min": -1000,
                "max": 1000
            }

        current_meta = metadata[axis_name_normalized]
        if new_display_name:
            current_meta["display_name"] = new_display_name
        if new_registered_tag:
            current_meta["registered_tag"] = new_registered_tag
        if new_min is not None:
            current_meta["min"] = new_min
        if new_max is not None:
            current_meta["max"] = new_max
        if new_default is not None:
            # Compiles into the built axis via the gen-avar2 shim's
            # declared ranges — where the axis RESTS, and the one
            # location avar2 cannot remap.
            current_meta["default"] = new_default

        # Ensure is_parametric flag is preserved (don't allow changing it via edit)
        # It should already be set correctly from initialization

        # If axis_name was different case, update metadata key
        if axis_name_normalized != axis_name and axis_name in metadata:
            del metadata[axis_name]

        metadata[axis_name_normalized] = current_meta
        server._save_axis_metadata(metadata)

        # Write CSV back (use normalized fieldnames)
        import csv
        _csv_io.backup_sidecar(csv_path)
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        server._update_csv_modification_time(csv_path)

        return jsonify({
            "success": True,
            "axis_name": axis_name_normalized,
            "metadata": current_meta
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/avar2/axis/<axis_name>', methods=['DELETE'])
@server._sidecar_locked
def delete_avar2_axis(axis_name: str):
    """Delete a mapping axis: drop its CSV column and its metadata
    entry, then rebuild (the axis leaves the compiled fvar). Parametric
    (out) columns are refused — they're managed in the source file.

    The static demo has had this since the mapping-axis arc (e2e §25);
    the desktop server simply never grew the endpoint, so the delete
    affordance was dead here."""
    try:
        csv_path = server._get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404
        if server._check_csv_external_edit(csv_path):
            return jsonify({
                "error": "The studio's saved data changed outside this window. Please reload.",
                "reload_required": True
            }), 409

        _, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)
        axis_name_normalized = axis_name.upper()
        if axis_name_normalized in out_cols:
            return jsonify({
                "error": f"Cannot delete axis '{axis_name}' - it exists in the Glyphs "
                         "file as a parametric axis. Parametric axes are managed in "
                         "the Glyphs file, not in avar2 mappings."
            }), 403

        import csv
        rows = []
        fieldnames = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            rows = list(reader)
        matches = [c for c in fieldnames if c.upper() == axis_name_normalized]
        if not matches:
            return jsonify({"error": f"Axis '{axis_name}' not found in the studio data"}), 404

        kept = [c for c in fieldnames if c.upper() != axis_name_normalized]
        _csv_io.backup_sidecar(csv_path)
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=kept)
            writer.writeheader()
            writer.writerows([{k: v for k, v in row.items() if k in kept} for row in rows])
        server._update_csv_modification_time(csv_path)

        metadata = server._load_axis_metadata()
        if axis_name_normalized in metadata:
            del metadata[axis_name_normalized]
            server._save_axis_metadata(metadata)

        # The axis set changed — the compiled fvar must follow.
        build_ok = False
        try:
            build_ok = server.trigger_build()
        except Exception as build_exc:
            log.warning(f"Warning: rebuild after axis delete failed: {build_exc}")
        return jsonify({"success": True, "deleted": axis_name_normalized,
                        "build_ok": bool(build_ok)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/avar2/mapping/<instance_name>/<axis_name>', methods=['PUT'])
@server._sidecar_locked
def update_avar2_mapping(instance_name: str, axis_name: str):
    """Update a single cell value in the CSV."""
    try:
        csv_path = server._get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404

        # Check for external edits
        if server._check_csv_external_edit(csv_path):
            return jsonify({
                "error": "The studio's saved data changed outside this window. Please reload.",
                "reload_required": True
            }), 409

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        new_value = data.get("value")
        if new_value is None:
            return jsonify({"error": "value is required"}), 400

        # Validate value is numeric
        try:
            float_value = float(new_value)
        except (ValueError, TypeError):
            return jsonify({"error": "value must be a number"}), 400

        # Normalize axis_name to uppercase for lookup
        axis_name_normalized = axis_name.upper()

        # Load metadata to check range
        metadata = server._load_axis_metadata()
        if axis_name_normalized in metadata:
            axis_meta = metadata[axis_name_normalized]
            min_val = axis_meta.get("min", -1000)
            max_val = axis_meta.get("max", 1000)
            if float_value < min_val or float_value > max_val:
                return jsonify({
                    "error": f"Value {float_value} is outside allowed range [{min_val}, {max_val}]"
                }), 400

        # Read CSV using normalized function

        rows, fieldnames, _, _, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

        if axis_name_normalized not in fieldnames:
            return jsonify({"error": f"Axis '{axis_name}' not found in the studio data"}), 404

        # Find and update the instance
        instance_found = False
        for row in rows:
            if row.get("Instance Name", "").strip() == instance_name:
                row[axis_name_normalized] = str(float_value)
                instance_found = True
                break

        if not instance_found:
            return jsonify({"error": f"Instance '{instance_name}' not found in the studio"}), 404

        # Write updated CSV (use normalized fieldnames)
        import csv
        _csv_io.backup_sidecar(csv_path)
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        server._update_csv_modification_time(csv_path)

        return jsonify({
            "success": True,
            "instance_name": instance_name,
            "axis_name": axis_name_normalized,
            "value": float_value
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
