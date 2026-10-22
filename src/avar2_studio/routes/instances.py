"""Instance routes (audit §3 step e group) — listing, reordering, CRUD,
editing guards, and promotion of studio-only rows into the source."""

import csv
import sys
import threading
from typing import Dict, List

from flask import Blueprint, jsonify, request

from .. import control_axes as _control_axes
from .. import csv_io as _csv_io
from .. import grade as _grade
from .. import instance_aliases as _instance_aliases
from .. import kinetikern as _kinetikern
from .. import server, state
from .. import sidecar_io as _sidecar_io
from .. import source_font as _source_font
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("instances", __name__)


@bp.route('/api/instances', methods=['GET'])
def get_instances():
    """Return source-defined + studio-only instances with origin tags.

    - ``origin: "source"`` — instance is declared inside the .glyphs /
      .designspace file. Edits write back to that file.
    - ``origin: "studio"`` — instance lives only in the sibling CSV.
      Edits write to the CSV; the source is untouched until the user
      explicitly calls ``POST /api/instance/<name>/add-to-source``.
    """
    if state.GLYPHS_PATH is None:
        return jsonify({"instances": []})
    try:
        instances = server.get_instances_from_glyphs(state.GLYPHS_PATH)
        source_names = {entry["name"] for entry in instances}

        # Pull studio-only rows: any CSV row whose Instance Name isn't in
        # the source file. For SOURCE rows, merge the CSV's cells over the
        # source coordinates: the CSV is the STUDIO layer, and an edit
        # saved there (the flyout's "Save to studio") must come back after
        # a reload — the pristine source values ride along as
        # ``source_coordinates`` so the sync dot can still show the
        # source mismatch (orange). Covers every column matching a source
        # axis tag, plus SPAC (transform-injected, never a source axis).
        try:
            source_axis_tags = {str(ax.get("tag", ""))
                                for ax in server.get_axes_from_glyphs(state.GLYPHS_PATH)}
        except Exception:
            source_axis_tags = set()
        csv_path = server._get_preview_csv_path()
        csv_order: Dict[str, int] = {}
        if csv_path and csv_path.exists():
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        name = (row.get("Instance Name") or "").strip()
                        if not name:
                            continue
                        if name not in csv_order:
                            csv_order[name] = len(csv_order)
                        if name in source_names:
                            inst = next((i for i in instances if i["name"] == name), None)
                            if inst is None:
                                continue
                            inst.setdefault("source_coordinates",
                                            dict(inst["coordinates"]))
                            for key, value in row.items():
                                if not key or key == "Instance Name":
                                    continue
                                if key != "SPAC" and key not in source_axis_tags:
                                    continue  # mapping column (WGHT/OPSZ…), not a coordinate
                                cell = (str(value) if value is not None else "").strip()
                                if not cell:
                                    continue  # blank cell → the source value stands
                                try:
                                    inst["coordinates"][key] = float(cell)
                                except (TypeError, ValueError):
                                    pass
                            continue
                        coordinates = {}
                        for key, value in row.items():
                            if not key or key == "Instance Name":
                                continue
                            try:
                                coordinates[key] = float(value)
                            except (TypeError, ValueError):
                                continue
                        instances.append({
                            "name": name,
                            "coordinates": coordinates,
                            "origin": "studio",
                        })
            except Exception as e:
                log.warning(f"Warning: Could not read studio-only rows from CSV: {e}")

        # Per-instance CONTROL-axis values hydrate from the control
        # sidecar so sliders and row previews come back after a reload.
        # Deliberately NOT merged into source_coordinates: they are
        # studio state, and padding that dict would break the CSV-vs-
        # source divergence check (it compares key sets).
        try:
            ctl_values = _control_axes.instance_values(state.ORIGINAL_PATH)
        except Exception:
            ctl_values = {}
        if ctl_values:
            for inst in instances:
                for tag, per_name in ctl_values.items():
                    if inst["name"] in per_name:
                        inst["coordinates"][tag] = per_name[inst["name"]]

        # The CSV row order is the user's authored row order (the drag
        # reorder persists it there). Instances without a CSV row keep
        # their current (source) order, ahead of ordered rows — stable
        # sort, so a CSV-less project is untouched.
        instances.sort(key=lambda i: csv_order.get(i.get("name"), -1))

        return jsonify({"instances": instances})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/instances/order', methods=['PUT'])
@server._sidecar_locked
def reorder_instances():
    """Persist the instance row order by reordering the CSV rows.

    Body: {"names": ["Instance A", "Instance B", ...]} — the full
    desired order. Rows named here are arranged in this order; rows not
    named keep their relative order after them. Order is display state
    only (the mapping model is order-independent), so no rebuild."""
    data = request.get_json(silent=True) or {}
    names = data.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return jsonify({"error": "Body must include 'names' as a list of strings."}), 400
    csv_path = server._get_preview_csv_path()
    if not csv_path or not csv_path.exists():
        return jsonify({"error": "Preview CSV not available"}), 500
    if server._check_csv_external_edit(csv_path):
        return jsonify({
            "error": "The studio's saved data changed outside this window. Please reload.",
            "reload_required": True
        }), 409
    try:
        import csv
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            rows = list(reader)
        rank = {n: i for i, n in enumerate(names)}
        rows.sort(key=lambda r: rank.get((r.get("Instance Name") or "").strip(),
                                         len(rank)))
        _csv_io.backup_sidecar(csv_path)
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        server._update_csv_modification_time(csv_path)
        return jsonify({"success": True})
    except Exception as e:
        log.error(f"Error reordering instances: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/instance', methods=['POST'])
@server._sidecar_locked
def create_instance():
    """Create a new studio-only instance.

    Two-tier model: created instances are written only to the sibling CSV
    with ``origin: studio``. The source file (.glyphs / .designspace) is
    NOT mutated until the user explicitly promotes via
    ``POST /api/instance/<name>/add-to-source``. This keeps the source
    instance list from getting bloated by exploratory grid points.
    """
    data = request.get_json(silent=True)
    if not data or 'name' not in data or 'coordinates' not in data:
        return jsonify({"error": "Missing 'name' or 'coordinates' in request body"}), 400

    if not isinstance(data['name'], str):
        return jsonify({"error": "'name' must be a string"}), 400
    instance_name = data['name'].strip()
    if not instance_name:
        return jsonify({"error": "Instance name cannot be empty"}), 400

    coordinates = data['coordinates']
    try:
        coordinates = {k: float(v) for k, v in coordinates.items()}
    except (ValueError, TypeError):
        return jsonify({"error": "Coordinates must be numeric"}), 400

    insert_after = data.get('insert_after', None)
    if insert_after:
        insert_after = insert_after.strip()

    # Refuse to create if the name already exists either in the source or in the CSV.
    # The axis list rides the same load: the previous shape read `font`
    # again AFTER the except, so a failed load crashed with
    # UnboundLocalError instead of falling back.
    _excluded = server._non_mapping_axis_tags()
    try:
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
        existing_source_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
        # ``font`` is the TRANSFORMED SHADOW: subtract the injected and
        # control axes or they become CSV mapping columns below.
        source_axes = [a for a in _source_font.get_axes(font)
                       if str(a.get("tag", "")).upper() not in _excluded]
    except Exception:
        existing_source_names = set()
        source_axes = []
    if instance_name in existing_source_names:
        return jsonify({"error": f"Instance '{instance_name}' already exists in the source file"}), 400

    csv_path = server._get_preview_csv_path()
    if not csv_path or not csv_path.exists():
        return jsonify({"error": "Preview CSV not initialized — cannot create studio-only instance"}), 500

    try:
        rows: List[Dict] = []
        fieldnames: List[str] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            for row in reader:
                rows.append(row)

        for row in rows:
            if (row.get("Instance Name") or "").strip() == instance_name:
                return jsonify({"error": f"Instance '{instance_name}' already exists"}), 400

        # Fall back to each axis's declared default when the caller didn't
        # supply a value, NOT to 0. wght=0 is outside the [100, 900] axis
        # range and would silently corrupt the row; defaulting to the
        # source's own declared default value keeps the row valid.

        # Evolve the CSV schema: if the source declares an axis the CSV
        # header doesn't list yet, append it. This handles the case where
        # an existing CSV was bootstrapped before the source gained more
        # axes. Without this, DictWriter rejects rows that carry the new
        # fields. SPAC is no longer special-cased — it's deferred.
        for axis in source_axes:
            upper = axis["tag"].upper()
            if upper == "INSTANCE NAME":
                continue
            if upper not in fieldnames:
                fieldnames.append(upper)
                for row in rows:
                    row.setdefault(upper, str(axis["default"]))

        new_row: Dict[str, str] = {"Instance Name": instance_name}
        for axis in source_axes:
            tag = axis["tag"]
            upper = tag.upper()
            value = coordinates.get(tag, coordinates.get(upper, axis["default"]))
            new_row[upper] = str(value)
        # Per-instance CONTROL-axis values ride along when provided (a
        # duplicate of a corrected row) — stored in the control sidecar,
        # same contract as update_instance.
        try:
            _ctl_tags = {str(a.get("tag", "")).lower()
                         for a in _control_axes.list_axes(state.ORIGINAL_PATH)}
            ctl_values = {t: v for t, v in coordinates.items()
                          if str(t).lower() in _ctl_tags}
            if ctl_values:
                _control_axes.set_instance_values(state.ORIGINAL_PATH, instance_name,
                                                  ctl_values)
        except Exception as exc:
            log.warning(f"Warning: could not save control-axis values: {exc}")

        # Per-instance SPAC rides along when the caller provides one (a
        # duplicate of a spaced row) — same contract as update_instance:
        # mint the column on first use, blanks elsewhere are inert.
        spac_value = coordinates.get('SPAC', coordinates.get('spac'))
        if spac_value is not None:
            if 'SPAC' not in fieldnames:
                fieldnames.append('SPAC')
                for row in rows:
                    row.setdefault('SPAC', "")
            new_row['SPAC'] = str(spac_value)
        for field in fieldnames:
            # Columns beyond the parametric axes (OPSZ/WGHT mapping
            # inputs) start BLANK, never 0: a stamped zero becomes a
            # real avar2 in: value and drags the compiled axis range
            # to it (the recurring unset-becomes-zero bug class).
            new_row.setdefault(field, "")

        if insert_after:
            insert_index = None
            for i, row in enumerate(rows):
                if (row.get("Instance Name") or "").strip() == insert_after:
                    insert_index = i + 1
                    break
            if insert_index is None:
                rows.append(new_row)
            else:
                rows.insert(insert_index, new_row)
        else:
            rows.append(new_row)

        _csv_io.backup_sidecar(csv_path)
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        server._update_csv_modification_time(csv_path)
        log.info(f"Created studio-only instance '{instance_name}' in CSV")
    except Exception as e:
        log.error(f"Error creating studio-only instance in CSV: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to create instance: {e}"}), 500

    # Rebuild so the avar2 build picks up the new studio-only row.
    # (Non-blocking — trigger_build submits to the job queue.)
    server.trigger_build()

    return jsonify({
        "success": True,
        "message": f"Created studio-only instance '{instance_name}'",
        "origin": "studio",
    })


@bp.route('/api/instance/<instance_name>', methods=['PUT'])
@server._sidecar_locked
def update_instance(instance_name: str):
    """Update instance coordinates.

    Default behavior writes to both the CSV and the source file (for
    source-defined instances). Pass ``?csv_only=true`` (or set
    ``csv_only`` in the body) to skip the source writeback — the
    flyout's two-action UI uses this for the "Update in avar2-studio"
    path so source instances can be edited without touching .glyphs /
    .designspace.
    """
    data = request.get_json(silent=True)
    if not data or 'coordinates' not in data:
        return jsonify({"error": "Missing 'coordinates' in request body"}), 400

    coordinates = data['coordinates']
    try:
        coordinates = {k: float(v) for k, v in coordinates.items()}
    except (ValueError, TypeError):
        return jsonify({"error": "Coordinates must be numeric"}), 400

    csv_only_flag = (
        request.args.get('csv_only', '').lower() in ('1', 'true', 'yes')
        or bool(data.get('csv_only'))
    )

    # Read-only: the parsed font is only asked for names and axes, and the
    # source writes below go through their own load. The cached loader keeps
    # a slider commit from re-parsing an 11 MB .glyphs (S4).
    font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
    source_instance_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
    is_source_instance = instance_name in source_instance_names
    # GLYPHS_PATH is the TRANSFORMED SHADOW — its axis list includes the
    # injected (slnt/ROND/SPAC) and control (lcwd/ymod) axes, which are
    # never per-instance mapping data. Without this subtraction their
    # values leak into the CSV row and the .glyphs writeback.
    _excluded = server._non_mapping_axis_tags()
    source_axis_tags = {axis["tag"] for axis in _source_font.get_axes(font)
                        if str(axis.get("tag", "")).upper() not in _excluded}

    glyphs_coordinates = {
        tag: value for tag, value in coordinates.items()
        if tag in source_axis_tags
    }
    # The CSV row takes source axes AND SPAC — the per-instance spacing
    # coordinate lives in the CSV only (it's transform-injected, not a
    # source axis, so it never goes to the .glyphs writeback).
    csv_coordinates = {
        tag: value for tag, value in coordinates.items()
        if tag in source_axis_tags or tag == 'SPAC'
    }
    # What only the studio can store: values with no source axis behind
    # them. A full source save still has to write these, or they are lost.
    mapping_only = {tag: value for tag, value in csv_coordinates.items()
                    if tag not in glyphs_coordinates}

    # Per-instance CONTROL-axis values (ymod/lcwd) persist in the control
    # sidecar, never as CSV columns (see
    # docs/decisions/0008-per-instance-control-values-in-sidecar.md).
    # /api/instances hydrates them back; the build stamps them onto
    # matching fvar instances.
    try:
        _ctl_tags = {str(a.get("tag", "")).lower()
                     for a in _control_axes.list_axes(state.ORIGINAL_PATH)}
    except Exception:
        _ctl_tags = set()
    control_values = {tag: value for tag, value in coordinates.items()
                      if str(tag).lower() in _ctl_tags}
    if control_values:
        server._suppress_watcher()
        try:
            _control_axes.set_instance_values(state.ORIGINAL_PATH, instance_name,
                                              control_values)
        except Exception as exc:
            log.warning(f"Warning: could not save control-axis values: {exc}")

    state.EDITING_INSTANCES.discard(instance_name)

    # Source writeback is skipped if the caller asked for CSV-only.
    # It targets the ORIGINAL — the user's file, the one explicit write
    # the flyout's "Save to source file" promises. (It used to target
    # GLYPHS_PATH, the TRANSFORMED SHADOW: a build artifact regenerated
    # from the original, so the "source save" evaporated on the next
    # regen and the user's file never changed.)
    glyphs_updated = False
    if not csv_only_flag and is_source_instance and glyphs_coordinates:
        source_name = server._source_instance_name(instance_name)
        glyphs_updated = server.update_instance_in_glyphs(
            state.ORIGINAL_PATH, source_name, glyphs_coordinates)
        if not glyphs_updated and source_name != instance_name:
            # The alias sidecar can outlive a rename made in Glyphs itself:
            # the source already carries the display name, so the recorded
            # pre-rename name no longer exists. Fall back to the name the
            # shadow listing showed.
            glyphs_updated = server.update_instance_in_glyphs(
                state.ORIGINAL_PATH, instance_name, glyphs_coordinates)
        if not glyphs_updated:
            return jsonify({"error": f"Failed to update instance '{instance_name}' in source file"}), 500
        # The shadow now trails the original — regenerate it BEFORE the
        # CSV sync below reads it, or the sync copies the stale values
        # straight back over the save.
        server._resolve_active_source()

    # CSV writeback fires whenever there are coords to record. For
    # studio-only instances this is the only persistence path; for
    # source instances it keeps the avar2 mapping CSV row in lockstep
    # with the source's <location>.
    csv_writeback_needed = bool(csv_coordinates)
    csv_row_written = False
    if csv_writeback_needed:
        csv_path = server._get_preview_csv_path()
        if csv_path and csv_path.exists():
            try:
                import csv
                rows = []
                with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    fieldnames = list(reader.fieldnames) if reader.fieldnames else []
                    rows = list(reader)

                # Update the row in the CSV when:
                #   - the instance is studio-only (CSV is the source of truth), OR
                #   - csv_only_flag is set (caller wants studio-only behavior
                #     for a source instance — e.g. the flyout's "Update in
                #     avar2-studio" path).
                # For a source instance with csv_only_flag=false, the
                # bottom-of-function sync from source → CSV handles the axis
                # columns. The MAPPING-ONLY values (SPAC) have no axis in the
                # source file, so that sync can never carry them: write them
                # here, or a SPAC draft stays local and the row stays red
                # after "Save to source file" (findings.md, F2).
                should_write_csv_row = csv_only_flag or (not is_source_instance)
                write_values = csv_coordinates if should_write_csv_row else mapping_only
                instance_updated = False
                updated_count = 0
                for row in rows:
                    if row.get("Instance Name", "").strip() == instance_name:
                        if write_values:
                            for tag, value in write_values.items():
                                col = tag.upper()
                                if col not in fieldnames:
                                    if col == 'SPAC':
                                        # Mint the SPAC column on first
                                        # use. It is the one injected axis
                                        # with a per-instance CSV contract:
                                        # /api/instances reads it back, the
                                        # spacing transforms pin it onto
                                        # named fvar instances
                                        # (_instance_spac_overrides), and
                                        # config generation excludes it
                                        # everywhere — so blank cells in
                                        # the other rows are inert.
                                        fieldnames.append(col)
                                        for r in rows:
                                            r.setdefault(col, "")
                                    else:
                                        # Unknown coordinate tag — DON'T
                                        # create a column for it. The
                                        # mappings data models source
                                        # parametric axes plus declared
                                        # mapping axes only; a control-axis
                                        # column (lcwd/ymod) reads as a
                                        # required-but-blank parametric
                                        # axis and aborts the avar2 build.
                                        continue
                                row[col] = str(value)
                        instance_updated = True
                        updated_count += 1

                if instance_updated:
                    if updated_count > 1:
                        log.warning(f"Warning: Found {updated_count} duplicate rows for '{instance_name}', updated all")
                    _csv_io.backup_sidecar(csv_path)
                    with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    server._update_csv_modification_time(csv_path)
                    csv_row_written = bool(should_write_csv_row)
                    log.info(f"Saved '{instance_name}' in the studio")
                else:
                    log.error(f"Error: Instance '{instance_name}' not found in the studio")
            except Exception as e:
                log.warning(f"Warning: Could not update CSV: {e}")
                import traceback
                traceback.print_exc(file=sys.stderr)

    # Sync CSV to pick up source-file coord changes. Skip the in-flight row
    # only when the CSV branch above already wrote it (csv-only / studio
    # rows). A full source writeback deliberately does NOT write the CSV row
    # above, so skipping it left the row stale (202.5) while the .glyphs said
    # 360 — the API then merged the stale CSV over the source and the row
    # stayed red. Skip the sync entirely for csv-only edits — we just wrote
    # the user's edit to CSV and don't want to overwrite it with the
    # source's unchanged values.
    if not csv_only_flag:
        csv_path = server._get_avar2_csv_path()
        if csv_path and csv_path.exists():
            try:
                skip = {instance_name} if csv_row_written else set()
                if _csv_io.update_csv_from_glyphs(state.GLYPHS_PATH, csv_path, skip_instances=skip,
                                                  exclude_axes=server._non_mapping_axis_tags()):
                    server._update_csv_modification_time(csv_path)   # ours, not external
                    log.info("CSV synced after instance update")
            except Exception as e:
                log.warning(f"Warning: Could not sync CSV after update: {e}")

    if csv_only_flag:
        # NO rebuild for auto-saved coordinate edits. The instance
        # rows preview through font-variation-settings on the
        # already-built font, so a rebuild changes nothing they show —
        # it only refreshes the avar2 table, which is consumed on the
        # Preview tab. Health reports ``build_stale`` (CSV newer than
        # the last build); the frontend rebuilds once when the Preview
        # tab opens. Rebuilding per slider settle made every tweak
        # cost a multi-second build and a preview flash.
        pass
    else:
        # Explicit source writeback: rebuild immediately (non-blocking —
        # trigger_build submits to the job queue).
        server.trigger_build()

    message_parts = []
    if glyphs_updated:
        message_parts.append(f"Updated instance '{instance_name}' in source file")
    if csv_writeback_needed:
        message_parts.append(f"Saved '{instance_name}' in the studio")

    return jsonify({
        "success": True,
        "message": "; ".join(message_parts) if message_parts else f"Updated instance '{instance_name}'",
    })


@bp.route('/api/instance/<instance_name>/rename', methods=['PUT'])
@server._sidecar_locked
def rename_instance(instance_name: str):
    """Rename an instance in the Glyphs file."""
    data = request.get_json(silent=True)
    if not data or 'new_name' not in data:
        return jsonify({"error": "Missing 'new_name' in request body"}), 400

    if not isinstance(data['new_name'], str):
        return jsonify({"error": "'new_name' must be a string"}), 400
    new_name = data['new_name'].strip()
    if not new_name:
        return jsonify({"error": "New instance name cannot be empty"}), 400

    if new_name == instance_name:
        return jsonify({"error": "New name is the same as current name"}), 400

    try:
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
        source_instance_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
        is_source_instance = instance_name in source_instance_names

        if is_source_instance:
            success = server.rename_instance_in_glyphs(state.GLYPHS_PATH, instance_name, new_name)
            if success and state.ORIGINAL_PATH is not None:
                # The rename just edited the SHADOW, which is re-derived
                # from the original on every regeneration — record it so
                # regenerate_shadow re-applies it and the old name never
                # resurrects.
                try:
                    _instance_aliases.record(state.ORIGINAL_PATH, instance_name, new_name)
                except Exception as alias_exc:
                    log.warning(f"Warning: could not persist instance rename: {alias_exc}")
        else:
            # Studio-only: rename happens in CSV only, below.
            success = True

        if success:
            # Keep any grade attached to this instance across the rename.
            try:
                _grade.rename_instance(state.ORIGINAL_PATH, instance_name, new_name)
            except Exception:
                pass
            try:
                _kinetikern.sidecar.rename_instance(state.ORIGINAL_PATH, instance_name, new_name)
            except Exception:
                pass
            # Same for per-instance control-axis values.
            try:
                _control_axes.rename_instance_values(state.ORIGINAL_PATH,
                                                     instance_name, new_name)
            except Exception:
                pass
            # Update preview CSV if it exists
            csv_path = server._get_preview_csv_path()
            if csv_path and csv_path.exists():
                try:
                    rows = []
                    fieldnames = []
                    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                        reader = csv.DictReader(f)
                        fieldnames = list(reader.fieldnames) if reader.fieldnames else []
                        for row in reader:
                            if row.get("Instance Name", "").strip() == instance_name:
                                row["Instance Name"] = new_name
                            rows.append(row)

                    # Write updated CSV
                    _csv_io.backup_sidecar(csv_path)
                    with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)

                    # Update modification time cache
                    server._update_csv_modification_time(csv_path)

                    log.info(f"Renamed instance '{instance_name}' to '{new_name}' in preview CSV")
                except Exception as e:
                    log.warning(f"Warning: Could not update instance name in preview CSV: {e}")
                    import traceback
                    traceback.print_exc()
            server.trigger_build()

            return jsonify({"success": True, "message": f"Renamed instance '{instance_name}' to '{new_name}'"})
        else:
            return jsonify({"error": f"Failed to rename instance '{instance_name}'"}), 500
    except ValueError as e:
        # Duplicate name error
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/instance/<instance_name>/add-to-source', methods=['POST'])
def add_instance_to_source(instance_name: str):
    """Promote a studio-only instance into the source file's instance list.

    Reads the instance's parametric coordinates from its CSV row and
    appends a matching <instance> / GSInstance to the source. After this
    succeeds, the next ``GET /api/instances`` returns the instance with
    ``origin: source`` and the UI's studio-only badge disappears.
    """
    try:
        # Promotion writes the ORIGINAL (the user's file) — loading and
        # saving the shadow here would both lose the write on the next
        # regen AND risk leaking shadow artifacts if saved elsewhere.
        font, _fmt = _source_font.load_source_cached(state.ORIGINAL_PATH)
        source_instance_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
        if instance_name in source_instance_names:
            return jsonify({"error": f"Instance '{instance_name}' is already in the source file"}), 400

        # Pull the studio-only row from the CSV to get its coordinates.
        csv_path = server._get_preview_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "Preview CSV not available"}), 500

        coords: Dict[str, float] = {}
        found = False
        source_axis_tags = {axis["tag"] for axis in _source_font.get_axes(font)}
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row.get("Instance Name") or "").strip() != instance_name:
                    continue
                found = True
                # CSV columns are upper-case axis tags (XOPQ/YOPQ/XTRA/…)
                # whereas the source uses the original tag case.
                for key, value in row.items():
                    if not key or key == "Instance Name" or key.upper() == "SPAC":
                        continue
                    tag_candidates = [key, key.upper(), key.lower()]
                    matched_tag = next((t for t in tag_candidates if t in source_axis_tags), None)
                    if matched_tag is None:
                        continue
                    try:
                        coords[matched_tag] = float(value)
                    except (TypeError, ValueError):
                        continue
                break

        if not found:
            return jsonify({"error": f"Instance '{instance_name}' not found in the studio"}), 404

        ok = _source_font.add_instance_to_source(font, state.ORIGINAL_PATH, instance_name, coords)
        if not ok:
            return jsonify({"error": f"Failed to add '{instance_name}' to source file"}), 500

        # For .glyphs sources, also poke Glyphs.app to reload the document.
        if server._source_is_glyphs(state.ORIGINAL_PATH):
            server._force_reload_glyphs_document(state.ORIGINAL_PATH, font_object=font)

        # The shadow (and with it the instances listing) trails the
        # original until regenerated — resolve now so the origin flip
        # is visible immediately; rebuild in the background.
        server._resolve_active_source()
        threading.Thread(target=lambda: server.trigger_build(), daemon=True).start()

        return jsonify({
            "success": True,
            "message": f"Added '{instance_name}' to source file",
            "origin": "source",
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/instance/<instance_name>/editing', methods=['POST'])
def register_editing_instance(instance_name: str):
    """Register an instance as being edited (protect from sync)."""
    state.EDITING_INSTANCES.add(instance_name)
    return jsonify({"success": True, "message": f"Instance '{instance_name}' registered as editing"})


@bp.route('/api/instance/<instance_name>', methods=['DELETE'])
@server._sidecar_locked
def delete_instance(instance_name: str):
    """Delete or demote an instance. Three modes selected by query param:

    - default (no param)        — remove from source file AND CSV
    - ``?csv_only=true``        — remove from CSV; keep source declaration
    - ``?source_only=true``     — remove from source; keep CSV row (DEMOTE
                                  a source instance to studio-only)

    The two ``_only`` modes are mutually exclusive; specifying both
    returns 400.
    """
    csv_only_flag = request.args.get('csv_only', '').lower() in ('1', 'true', 'yes')
    source_only_flag = request.args.get('source_only', '').lower() in ('1', 'true', 'yes')

    if csv_only_flag and source_only_flag:
        return jsonify({"error": "csv_only and source_only are mutually exclusive."}), 400

    # For source-file writeback we still require the Glyphs file not be
    # holding unsaved edits in the Glyphs.app process. CSV-only deletes
    # don't touch the source, so the check doesn't apply.
    touches_source = not csv_only_flag  # both default and source_only touch source
    if touches_source and server._check_glyphs_file_unsaved_changes(state.GLYPHS_PATH):
        return jsonify({"error": "Glyphs file has unsaved changes. Please save the file first."}), 409

    try:
        state.EDITING_INSTANCES.discard(instance_name)

        # A full delete (not a demote / csv-only) removes the instance
        # entirely, so drop any grade attached to it. Demote/csv-only keep
        # the instance around, so the grade stays.
        if not csv_only_flag and not source_only_flag:
            try:
                _grade.remove_instance_grade(state.ORIGINAL_PATH, instance_name)
            except Exception:
                pass
            try:
                _kinetikern.sidecar.remove_entry(state.ORIGINAL_PATH, instance_name)
            except Exception:
                pass
            try:
                _control_axes.remove_instance_values(state.ORIGINAL_PATH, instance_name)
            except Exception:
                pass

        # Source-file delete fires for default + source_only, but only for
        # rows the source actually declares. A studio-only row has nothing
        # to delete there; the default DELETE (API clients, older UIs) used
        # to fail with a 500 instead of removing its CSV row.
        if touches_source:
            font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
            source_names = {entry["name"] for entry in
                            _source_font.get_source_instances(font)}
            if instance_name in source_names:
                source_name = server._source_instance_name(instance_name)
                glyphs_deleted = server.delete_instance_in_glyphs(
                    state.ORIGINAL_PATH, source_name)
                if not glyphs_deleted and source_name != instance_name:
                    # Stale alias: the source already carries the display
                    # name (renamed in Glyphs itself).
                    glyphs_deleted = server.delete_instance_in_glyphs(
                        state.ORIGINAL_PATH, instance_name)
                if not glyphs_deleted:
                    return jsonify({"error": f"Failed to delete instance '{instance_name}' from source file"}), 500
                # Regenerate the shadow from the now-updated original so the
                # listing stops serving the deleted instance.
                server._resolve_active_source()
            else:
                log.info("Instance %r is not in the source; removing its CSV row only",
                         instance_name)

        # CSV delete fires for default + csv_only. source_only PRESERVES
        # the CSV row — that's the "demote to studio-only" semantic.
        csv_path = server._get_preview_csv_path() if not source_only_flag else None
        if csv_path and csv_path.exists():
            try:
                import csv
                # Read CSV
                rows = []
                fieldnames = []
                with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    fieldnames = list(reader.fieldnames) if reader.fieldnames else []
                    for row in reader:
                        # Skip the instance being deleted
                        if row.get("Instance Name", "").strip() != instance_name:
                            rows.append(row)

                # Write updated CSV (without deleted instance)
                _csv_io.backup_sidecar(csv_path)
                with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

                # Update modification time cache
                server._update_csv_modification_time(csv_path)
                log.info(f"Removed instance '{instance_name}' from preview CSV")
            except Exception as e:
                log.warning(f"Warning: Could not remove instance from preview CSV: {e}")
                import traceback
                traceback.print_exc()
        server.trigger_build()

        if csv_only_flag:
            msg = f"Deleted instance '{instance_name}' from CSV (source untouched)"
        elif source_only_flag:
            msg = f"Demoted instance '{instance_name}' — removed from source, CSV row kept"
        else:
            msg = f"Deleted instance '{instance_name}' from source file and CSV"
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/instance/<instance_name>/editing', methods=['DELETE'])
def unregister_editing_instance(instance_name: str):
    """Unregister an instance from editing (allow sync)."""
    state.EDITING_INSTANCES.discard(instance_name)
    return jsonify({"success": True, "message": f"Instance '{instance_name}' unregistered from editing"})
