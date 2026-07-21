"""CSV ↔ source-file glue for the avar2 mappings CSV.

Two scripts used to live alongside this module — ``sync-glyphs-to-avar2.py``
and ``match-instances-to-avar2.py`` — and ``server.py`` invoked them
through ``importlib`` gymnastics and subprocess calls. This module folds
both into normal Python, routes their source-file reads through
:mod:`avar2_studio.source_font`, and exposes them as plain importable
functions.

Public surface:

- :func:`get_glyphs_instances` — ``{name: {tag: value}}`` keyed by axis
  tag. Works on .glyphs and .designspace via source_font.
- :func:`read_csv_mappings` — minimal CSV reader (rows + fieldnames),
  used by the sync flow.
- :func:`read_csv_mappings_with_axes` — same but also returns the
  in/out axis column split, derived from the source file's axes.
- :func:`update_csv_from_glyphs` — sync source instances into the CSV.
- :func:`match_instances` — pair source instances with CSV rows for the
  avar2 mapping endpoints.
- :func:`normalize_in_axis_name` — column-name → registered-tag mapping.
- :func:`parse_decimal` — tolerant numeric parse for CSV cells.
- :func:`compare_coordinates` — out-coord drift detection.
"""

from __future__ import annotations

import csv
import shutil
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import source_font


def backup_sidecar(path: Path, keep: int = 20) -> None:
    """Timestamped copy of an authored sidecar (mappings CSV, axis
    metadata, control/transforms JSON) under the project's
    ``.avar2-studio/backups/`` before a rewrite. Bounded to the last
    ``keep`` copies per filename. Never raises — a failed backup must
    not block the write it protects. Two data-loss incidents in one
    week bought this."""
    try:
        p = Path(path)
        if not p.exists():
            return
        parent = p.parent
        # axis-metadata.json already lives inside .avar2-studio/ —
        # don't nest another workdir under it.
        workdir = parent if parent.name == ".avar2-studio" else parent / ".avar2-studio"
        backups = workdir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(str(p), str(backups / f"{p.name}.{stamp}"))
        siblings = sorted(backups.glob(f"{p.name}.*"))
        for old in siblings[:-keep]:
            old.unlink()
    except Exception as e:
        print(f"Warning: sidecar backup failed for {path}: {e}", file=sys.stderr)


# Column-name → axis tag mapping. CSV columns for registered axes are
# usually upper-case (``WGHT``/``WDTH``); the ``-e`` suffix is occasionally
# used on the traditional inputs. Everything else falls through unchanged.
_AXIS_NAME_MAP = {
    "WGHT": "wght",
    "WDTH": "wdth",
    "OPSZ": "opsz",
    "CONTRAST": "cntr",
    "CNTR": "cntr",
}


def normalize_in_axis_name(col_name: str) -> str:
    """Normalize a traditional-axis CSV column name to its registered tag.

    ``WGHT`` / ``WGHT-e`` → ``wght``. Anything unknown is returned
    lower-cased so non-registered columns still round-trip predictably.
    """
    col_upper = col_name.upper()
    if col_upper.endswith("-E"):
        col_upper = col_upper[:-2]
    return _AXIS_NAME_MAP.get(col_upper, col_upper.lower())


def parse_decimal(value: str) -> Optional[Decimal]:
    """Parse a CSV cell to ``Decimal``. Blank / unparseable returns ``None``."""
    if not value or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except (ValueError, InvalidOperation):
        return None


def get_glyphs_instances(source_path: Path) -> Dict[str, Dict[str, float]]:
    """Return source-defined instances keyed by name.

    Shape: ``{instance_name: {axis_tag: value}}``. Order is preserved
    (Python dicts preserve insertion order; ``source_font`` returns
    instances in the source file's order).

    Works for ``.glyphs`` and ``.designspace``.
    """
    font, _fmt = source_font.load_source(source_path)
    instances: Dict[str, Dict[str, float]] = {}
    for entry in source_font.get_source_instances(font):
        name = entry["name"]
        if not name:
            continue
        instances[name] = dict(entry["coordinates"])
    return instances


def read_csv_mappings(csv_path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    """Minimal CSV reader for the sync flow.

    Returns ``(rows, fieldnames)``. Rows are dicts with whitespace-stripped
    keys and values; fieldnames preserve original case.
    """
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames) if reader.fieldnames else []
        if not fieldnames:
            raise ValueError("CSV has no header row")
        for row in reader:
            cleaned = {k.strip(): (v.strip() if v else "") for k, v in row.items()}
            rows.append(cleaned)
    return rows, fieldnames


def read_csv_mappings_with_axes(
    csv_path: Path,
    source_path: Optional[Path] = None,
) -> Tuple[List[Dict[str, str]], List[str], List[str], List[str], Dict[str, str]]:
    """Read the CSV and split its columns into traditional (in) and parametric (out).

    The source file is the source of truth for which axes are parametric —
    every CSV column whose upper-cased name matches a source axis tag is
    parametric (``out_cols``); the rest are traditional (``in_cols``).

    Returns ``(rows, fieldnames, in_cols, out_cols, fieldname_mapping)``
    where ``fieldname_mapping`` maps normalized (uppercase) column names
    back to their original case for writeback.
    """
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        original_fieldnames = list(reader.fieldnames) if reader.fieldnames else []
        if not original_fieldnames:
            raise ValueError("CSV has no header row")

        normalized_fieldnames: List[str] = []
        fieldname_mapping: Dict[str, str] = {}
        for h in original_fieldnames:
            original = h.strip()
            normalized = original.upper() if original != "Instance Name" else original
            normalized_fieldnames.append(normalized)
            fieldname_mapping[normalized] = original

        name_col = "Instance Name"
        if name_col not in normalized_fieldnames:
            raise ValueError(
                f"CSV must include '{name_col}' column. Found: {normalized_fieldnames}"
            )

        # Classify each CSV column as parametric (out) or traditional (in).
        # An axis is *parametric* iff it's declared in the source AND has
        # master coverage. Source axes WITHOUT master coverage are still
        # avar2 mapping inputs — they only exist to be routed via the
        # avar2 table — so they go in in_cols and surface in the
        # AVAR2 MAPPINGS UI section.
        parametric_axis_tags: set = set()
        if source_path and source_path.exists():
            try:
                font, _fmt = source_font.load_source(source_path)
                parametric_axis_tags = {
                    axis["tag"].upper()
                    for axis in source_font.get_axes(font)
                    if axis.get("has_master_coverage", True)
                }
            except Exception as e:
                print(
                    f"Warning: Could not read source file to determine parametric axes: {e}",
                    file=sys.stderr,
                )

        in_cols: List[str] = []
        out_cols: List[str] = []
        for col in normalized_fieldnames:
            if col == name_col:
                continue
            (out_cols if col.upper() in parametric_axis_tags else in_cols).append(col)

        for row in reader:
            cleaned: Dict[str, str] = {}
            for k, v in row.items():
                original_key = k.strip()
                normalized_key = (
                    original_key.upper() if original_key != "Instance Name" else original_key
                )
                cleaned[normalized_key] = v.strip() if v else ""
            rows.append(cleaned)

    return rows, normalized_fieldnames, in_cols, out_cols, fieldname_mapping


def update_csv_from_glyphs(
    source_path: Path,
    csv_path: Path,
    dry_run: bool = False,
    skip_instances: Optional[set] = None,
) -> bool:
    """Sync source-file instance coordinates into the CSV.

    Adds rows for new source instances, updates coordinates for
    existing ones, removes rows whose names no longer appear in the
    source. Studio-only rows whose names aren't in the source ARE
    removed too — this preserves the historical sync semantics.
    ``skip_instances`` shields named rows from sync entirely (used to
    avoid clobbering an in-flight edit).
    """
    try:
        glyphs_instances = get_glyphs_instances(source_path)
        if not glyphs_instances:
            print("No instances found in source file", file=sys.stderr)
            return False

        csv_rows, fieldnames = read_csv_mappings(csv_path)
        if not fieldnames:
            print("CSV has no header row", file=sys.stderr)
            return False

        name_col = "Instance Name"
        if name_col not in fieldnames:
            print(f"CSV missing '{name_col}' column", file=sys.stderr)
            return False

        all_axes: set = set()
        for coords in glyphs_instances.values():
            all_axes.update(coords.keys())

        # Case-insensitive compare: bootstrap writes uppercase columns
        # (WGHT) while source axis tags are usually lowercase (wght).
        # Without the casefold, the sync would append wght alongside
        # WGHT and the CSV ends up with two columns for the same axis.
        csv_axes_ci = {c.upper(): c for c in fieldnames if c != name_col}
        new_axes = {a for a in all_axes if a.upper() not in csv_axes_ci}

        if new_axes:
            print(
                f"New axes found in source file (will add to CSV): {sorted(new_axes)}",
                file=sys.stderr,
            )
            fieldnames = list(fieldnames) + sorted(new_axes)
            # Refresh the CI map so subsequent lookups find the freshly
            # appended columns too.
            for col in sorted(new_axes):
                csv_axes_ci[col.upper()] = col
            for row in csv_rows:
                for axis in new_axes:
                    row[axis] = ""

        # All axes that resolve to a CSV column (any case) — used for the
        # write loop below so we update WGHT-column for an axis tagged
        # wght instead of creating a parallel wght column.
        existing_axes = {a for a in all_axes if a.upper() in csv_axes_ci}
        if existing_axes:
            print(
                f"Syncing existing axes from source file: {sorted(existing_axes)}",
                file=sys.stderr,
            )

        glyphs_coords: Dict[str, Dict[str, float]] = {
            name: {axis: coords.get(axis) for axis in all_axes if axis in coords}
            for name, coords in glyphs_instances.items()
        }
        glyphs_instance_names = set(glyphs_coords)

        updated_rows: List[Dict[str, str]] = []
        updated_count = removed_count = added_count = 0
        skip_instances = skip_instances or set()

        for row in csv_rows:
            instance_name = row.get(name_col, "").strip()
            if instance_name in skip_instances:
                updated_rows.append(row)
                glyphs_instance_names.discard(instance_name)
                if not dry_run:
                    print(
                        f"Skipping sync for '{instance_name}' (currently being edited)",
                        file=sys.stderr,
                    )
                continue

            if instance_name in glyphs_coords:
                coords = glyphs_coords[instance_name]
                for axis in all_axes:
                    if axis not in coords:
                        continue
                    # Resolve to whatever case the CSV column has — keeps
                    # WGHT and wght from coexisting in the same file.
                    col = csv_axes_ci.get(axis.upper())
                    if not col:
                        continue
                    old_value = row.get(col, "")
                    new_value = str(coords[axis])
                    if old_value != new_value:
                        row[col] = new_value
                        updated_count += 1
                updated_rows.append(row)
                glyphs_instance_names.discard(instance_name)
            else:
                removed_count += 1
                if not dry_run:
                    print(
                        f"Removing row: {instance_name} (not in source file)",
                        file=sys.stderr,
                    )

        for instance_name in glyphs_instance_names:
            coords = glyphs_coords[instance_name]
            new_row: Dict[str, str] = {name_col: instance_name}
            for col in fieldnames:
                if col == name_col:
                    continue
                new_row[col] = str(coords[col]) if col in coords else ""
            updated_rows.append(new_row)
            added_count += 1
            if not dry_run:
                print(f"Adding new instance: {instance_name}", file=sys.stderr)

        if dry_run:
            changes: List[str] = []
            if updated_count:
                changes.append(f"update {updated_count} values")
            if removed_count:
                changes.append(f"remove {removed_count} rows")
            if added_count:
                changes.append(f"add {added_count} instances")
            if new_axes:
                changes.append(f"add {len(new_axes)} new axis columns")
            if changes:
                print(f"Dry run: Would {', '.join(changes)}", file=sys.stderr)
            return bool(updated_count or removed_count or added_count or new_axes)

        if updated_count or removed_count or added_count or new_axes:
            backup_sidecar(csv_path)
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_rows)
            changes = []
            if updated_count:
                changes.append(f"{updated_count} values updated")
            if removed_count:
                changes.append(f"{removed_count} rows removed")
            if added_count:
                changes.append(f"{added_count} instances added")
            if new_axes:
                changes.append(f"{len(new_axes)} new axis columns added")
            print(f"Updated CSV: {', '.join(changes)}", file=sys.stderr)
        else:
            print("No changes needed", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Error updating CSV: {e}", file=sys.stderr)
        return False


def compare_coordinates(
    glyphs_coords: Dict[str, float],
    csv_out_coords: Dict[str, Decimal],
    tolerance: float = 0.01,
) -> List[Dict]:
    """Diff source-instance coords against the CSV's parametric values."""
    mismatches: List[Dict] = []
    for axis_tag, glyphs_value in glyphs_coords.items():
        csv_value = csv_out_coords.get(axis_tag)
        if csv_value is None:
            mismatches.append({
                "axis": axis_tag,
                "glyphs_value": glyphs_value,
                "csv_value": None,
                "differs": True,
                "difference": None,
                "reason": "missing_in_csv",
            })
            continue
        csv_float = float(csv_value)
        difference = abs(glyphs_value - csv_float)
        if difference > tolerance:
            mismatches.append({
                "axis": axis_tag,
                "glyphs_value": glyphs_value,
                "csv_value": csv_float,
                "differs": True,
                "difference": difference,
                "reason": "mismatch",
            })
    return mismatches


def match_instances(
    source_path: Path,
    csv_path: Path,
    tolerance: float = 0.01,
) -> List[Dict]:
    """Pair source instances with CSV rows. See the original docstring in
    ``match-instances-to-avar2.py`` for the result shape.
    """
    glyphs_instances = get_glyphs_instances(source_path)
    csv_rows, _, in_cols, out_cols, _ = read_csv_mappings_with_axes(csv_path, source_path)
    name_col = "Instance Name"

    csv_by_name: Dict[str, Dict[str, str]] = {}
    for row in csv_rows:
        name = row.get(name_col, "").strip()
        if name:
            csv_by_name[name] = row

    results: List[Dict] = []

    for instance_name, glyphs_coords in glyphs_instances.items():
        csv_row = csv_by_name.get(instance_name)
        if csv_row is None:
            results.append({
                "instance_name": instance_name,
                "glyphs_coordinates": glyphs_coords,
                "avar2_mapping": None,
                "match_status": "missing_in_csv",
                "coordinate_mismatches": [],
            })
            continue

        in_axes: Dict[str, float] = {}
        for col in in_cols:
            value_str = csv_row.get(col, "").strip()
            if value_str:
                decimal_val = parse_decimal(value_str)
                if decimal_val is not None:
                    in_axes[normalize_in_axis_name(col)] = float(decimal_val)

        out_axes: Dict[str, Decimal] = {}
        for col in out_cols:
            value_str = csv_row.get(col, "").strip()
            if value_str:
                decimal_val = parse_decimal(value_str)
                if decimal_val is not None:
                    out_axes[col] = decimal_val

        mismatches = compare_coordinates(glyphs_coords, out_axes, tolerance)
        results.append({
            "instance_name": instance_name,
            "glyphs_coordinates": glyphs_coords,
            "avar2_mapping": {
                "in": in_axes,
                "out": {k: float(v) for k, v in out_axes.items()},
            },
            "match_status": "matched" if not mismatches else "mismatch",
            "coordinate_mismatches": mismatches,
        })

    for instance_name, csv_row in csv_by_name.items():
        if instance_name in glyphs_instances:
            continue
        in_axes = {}
        for col in in_cols:
            value_str = csv_row.get(col, "").strip()
            if value_str:
                decimal_val = parse_decimal(value_str)
                if decimal_val is not None:
                    in_axes[normalize_in_axis_name(col)] = float(decimal_val)
        out_axes_floats: Dict[str, float] = {}
        for col in out_cols:
            value_str = csv_row.get(col, "").strip()
            if value_str:
                decimal_val = parse_decimal(value_str)
                if decimal_val is not None:
                    out_axes_floats[col] = float(decimal_val)
        results.append({
            "instance_name": instance_name,
            "glyphs_coordinates": None,
            "avar2_mapping": {
                "in": in_axes,
                "out": out_axes_floats,
            },
            "match_status": "missing_in_glyphs",
            "coordinate_mismatches": [],
        })

    return results
