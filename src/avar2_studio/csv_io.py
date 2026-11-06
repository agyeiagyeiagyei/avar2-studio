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
- :func:`read_csv_spac_overrides` — per-instance SPAC coordinates from
  the CSV's SPAC column (the transforms' pinning contract).
- :func:`update_csv_from_glyphs` — sync source instances into the CSV.
- :func:`match_instances` — pair source instances with CSV rows for the
  avar2 mapping endpoints.
- :func:`normalize_in_axis_name` — column-name → registered-tag mapping.
- :func:`parse_decimal` — tolerant numeric parse for CSV cells.
- :func:`compare_coordinates` — out-coord drift detection.
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import sidecar_io, source_font
from .studio_log import get_logger

log = get_logger(__name__)
def backup_sidecar(path: Path, keep: int = 20) -> None:
    """Timestamped copy of an authored sidecar (mappings CSV, axis
    metadata, control/transforms JSON) under the project's
    ``.avar2-studio/backups/`` before a rewrite. Bounded to the last
    ``keep`` copies per filename. Never raises — a failed backup must
    not block the write it protects. See docs/SIDECARS.md."""
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
        log.warning(f"Warning: sidecar backup failed for {path}: {e}")


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
    font, _fmt = source_font.load_source_cached(source_path)
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
    keys and values; fieldnames preserve original case (stripped, like the
    row keys). Cells beyond the header's width have no column and are
    dropped (DictReader keys them under ``None``).
    """
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = [h.strip() for h in reader.fieldnames] if reader.fieldnames else []
        if not fieldnames:
            raise ValueError("CSV has no header row")
        for row in reader:
            cleaned = {
                k.strip(): (v.strip() if v else "")
                for k, v in row.items()
                if k is not None
            }
            rows.append(cleaned)
    return rows, fieldnames


def read_csv_spac_overrides(csv_path: Path) -> Dict[str, float]:
    """Per-instance SPAC coordinates from the mappings CSV's SPAC column.

    This is the one parse behind the transforms' instance pinning:
    ``builtin_spac._instance_spac_overrides`` reads it for the fontTools
    path, and the width-aware transform hands it to the native injector
    in the transforms JSON (``params.instance_spac``) — the Rust side
    never parses the CSV.

    Blank names/cells are skipped; a missing or unreadable file yields
    ``{}``. A non-numeric cell ends the parse and keeps the rows read so
    far — the reference reader's blanket ``except ValueError`` semantics,
    kept verbatim: the CSV is the data contract, this is its long-standing
    reading."""
    overrides: Dict[str, float] = {}
    try:
        rows, _fieldnames = read_csv_mappings(csv_path)
    except (OSError, ValueError):
        return overrides
    for row in rows:
        name = row.get("Instance Name", "").strip()
        cell = row.get("SPAC", "").strip()
        if not name or not cell:
            continue
        try:
            overrides[name] = float(cell)
        except ValueError:
            break
    return overrides


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
                font, _fmt = source_font.load_source_cached(source_path)
                parametric_axis_tags = {
                    axis["tag"].upper()
                    for axis in source_font.get_axes(font)
                    if axis.get("has_master_coverage", True)
                }
            except Exception as e:
                log.warning(f"Warning: Could not read source file to determine parametric axes: {e}")

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
    remove_missing: bool = False,
    exclude_axes: Optional[set] = None,
) -> bool:
    """Sync source-file instance coordinates into the CSV.

    Adds rows for new source instances and updates coordinates for existing
    ones. ``skip_instances`` shields named rows from sync entirely (used to
    avoid clobbering an in-flight edit).

    ``exclude_axes`` names axis tags that must never become CSV columns. The
    studio's own secondary axes live in the SHADOW font this often reads, and a
    column for one is poison: everything in this CSV that is not a mapping
    input counts as a required parametric output, so its blank cells abort the
    whole avar2 build with "parametric axis 'lcwd' is blank".

    ``remove_missing`` drops rows whose names are absent from the source, and
    DEFAULTS OFF. It used to be unconditional, which quietly destroyed the
    designer's work: studio-only instances live in this CSV and by definition
    are not in the source, so any background sync deleted them. That sync runs
    on every source touch — including the shadow regeneration behind every
    secondary-axis edit — so adding one brace layer wiped every studio-only
    instance in the file.

    A row absent from the source is ambiguous: it may be a studio-only
    instance, or one the designer deleted in Glyphs. Nothing distinguishes
    them, so the safe reading is to keep it. A stale row is visible and
    removable; a deleted one is gone.
    """
    try:
        glyphs_instances = get_glyphs_instances(source_path)
        if not glyphs_instances:
            log.info("No instances found in source file")
            return False

        csv_rows, fieldnames = read_csv_mappings(csv_path)
        if not fieldnames:
            log.info("CSV has no header row")
            return False

        name_col = "Instance Name"
        if name_col not in fieldnames:
            log.info(f"CSV missing '{name_col}' column")
            return False

        all_axes: set = set()
        for coords in glyphs_instances.values():
            all_axes.update(coords.keys())

        # Case-insensitive compare: bootstrap writes uppercase columns
        # (WGHT) while source axis tags are usually lowercase (wght).
        # Without the casefold, the sync would append wght alongside
        # WGHT and the CSV ends up with two columns for the same axis.
        csv_axes_ci = {c.upper(): c for c in fieldnames if c != name_col}
        _excluded = {str(a).upper() for a in (exclude_axes or set())}
        new_axes = {
            a for a in all_axes
            if a.upper() not in csv_axes_ci and a.upper() not in _excluded
        }

        if new_axes:
            log.info(f"New axes found in source file (will add to CSV): {sorted(new_axes)}")
            fieldnames = list(fieldnames) + sorted(new_axes)
            # Refresh the CI map so subsequent lookups find the freshly
            # appended columns too.
            for col in sorted(new_axes):
                csv_axes_ci[col.upper()] = col
            # Backfill EVERY row with the axis's source default, never a
            # blank: a parametric column with blank cells aborts the whole
            # avar2 build ("parametric axis 'ital' is blank"), and only the
            # rows the sync happens to touch would ever be filled. The
            # default is also the truthful value — an instance authored
            # before the axis existed sits at its default — and as an
            # avar2 out-value it is a zero delta, so it changes nothing.
            defaults = {}
            try:
                from . import source_font as _source_font
                font, _fmt = _source_font.load_source_cached(source_path)
                for ax in _source_font.get_axes(font):
                    tag = str(ax.get("tag") or "")
                    defaults[tag.upper()] = ax.get("default", 0)
            except Exception as e:
                log.warning(f"Warning: could not read axis defaults for backfill: {e}")
            for row in csv_rows:
                for axis in new_axes:
                    row[axis] = str(defaults.get(axis.upper(), 0))

        # All axes that resolve to a CSV column (any case) — used for the
        # write loop below so we update WGHT-column for an axis tagged
        # wght instead of creating a parallel wght column.
        existing_axes = {a for a in all_axes if a.upper() in csv_axes_ci}
        if existing_axes:
            log.info(f"Syncing existing axes from source file: {sorted(existing_axes)}")

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
                    log.info(f"Skipping sync for '{instance_name}' (currently being edited)")
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
                if not remove_missing:
                    # Not in the source — but that is not evidence it should
                    # go. Keep it (see remove_missing in the docstring).
                    updated_rows.append(row)
                    continue
                removed_count += 1
                if not dry_run:
                    log.info(f"Removing row: {instance_name} (not in source file)")

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
                log.info(f"Adding new instance: {instance_name}")

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
                log.info(f"Dry run: Would {', '.join(changes)}")
            return bool(updated_count or removed_count or added_count or new_axes)

        if updated_count or removed_count or added_count or new_axes:
            backup_sidecar(csv_path)
            with sidecar_io.atomic_writer(csv_path, encoding="utf-8", newline="") as f:
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
            log.info(f"Updated CSV: {', '.join(changes)}")
        else:
            log.info("No changes needed")
        return True
    except Exception as e:
        log.error(f"Error updating CSV: {e}")
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


MASTER_TOLERANCE = 1.0   # font units: "sits on" a compiled master


def origin_master_report(source_path: Path, out: Dict[str, float]) -> Optional[Dict]:
    """Which compiled master is nearest to a mapping row's parametric ``out``,
    and by how much per axis it misses it (S1(d), the ORIGIN badge tooltip).

    A row whose out lands exactly on a master is what makes build-time
    relocation cheap: the origin is already a point the source interpolates.
    When it is not, the difference is what the relocation has to move, and the
    designer can see it before the build overflows. ``None`` when the source
    has no master carrying any of these tags.
    """
    if not out:
        return None
    # Tags come upper-case from a CSV column ("XTRA") and lower-case from the
    # internal convention ("xtra"); the masters carry whatever the source
    # writes. Compare case-insensitively or a real font silently reports "no
    # master" (E2l: Crispy's ORIGIN row matched nothing this way).
    wanted = {str(tag).lower(): float(value) for tag, value in out.items()}
    font, _fmt = source_font.load_source_cached(source_path)
    best = None
    for master in source_font.get_masters(font):
        shared = {tag: float(value) for tag, value in master["coordinates"].items()
                  if str(tag).lower() in wanted}
        if not shared:
            continue
        delta = {tag: round(wanted[str(tag).lower()] - value, 2)
                 for tag, value in shared.items()}
        worst = max((abs(d) for d in delta.values()), default=0.0)
        if best is None or worst < best[0]:
            best = (worst, master["name"], delta)
    if best is None:
        return None
    worst, name, delta = best
    return {"nearest": name, "on_master": worst <= MASTER_TOLERANCE,
            "max_delta": worst, "delta": delta}


def default_master_name(source_path: Path) -> Optional[str]:
    """The source's DEFAULT master — the master the font rests at unrelocated.

    The master at every axis default. Glyphs' "Variable Font Origin" wins when
    it names a master that exists; a dangling id falls back to the first master,
    which is what the compiler does (Crispy's points at a master id that is not
    in the saved file, so its default master is 47-1-1).
    """
    font, _fmt = source_font.load_source_cached(source_path)
    masters = source_font.get_masters(font) or []
    if not masters:
        return None
    defaults = {ax["tag"]: float(ax["default"])
                for ax in (source_font.get_axes(font) or [])
                if ax.get("tag") and ax.get("default") is not None}
    if defaults:
        for master in masters:
            coords = master["coordinates"]
            if all(tag in coords
                   and abs(coords[tag] - value) <= MASTER_TOLERANCE
                   for tag, value in defaults.items()):
                return master["name"]
    return masters[0]["name"]


def declared_axis_defaults(source_path: Path) -> Dict[str, float]:
    """The DECLARED axis defaults beside the source, keyed by lower-case tag.

    ``<stem>-axis-metadata.json`` (or the legacy ``.avar2-studio/`` copy) is
    where the user axes' resting values live — opsz 144, wght 400. The master
    grid's minima are NOT those: on Crispy the weight axis bottoms out at 1 and
    the font rests at 400, so resolving the ORIGIN row against the minima finds
    no row at all.
    """
    for candidate in (source_path.with_name(f"{source_path.stem}-axis-metadata.json"),
                      source_path.parent / ".avar2-studio" / "axis-metadata.json"):
        if not candidate.exists():
            continue
        try:
            meta = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(col).lower(): float(entry["default"])
                for col, entry in meta.items()
                if isinstance(entry, dict) and entry.get("default") is not None}
    return {}


def origin_row_out(source_path: Path,
                   csv_path: Optional[Path],
                   axis_defaults: Optional[Dict[str, float]] = None,
                   ) -> Optional[Dict[str, float]]:
    """The parametric ``out`` of the CSV's ORIGIN row.

    The ORIGIN row is the one whose traditional ``in`` coordinates are all at
    their declared defaults — the style the font rests at when the export's
    "Set default at" checkbox is off. E2l's dialog asks about that location, so
    it does not have to re-derive it from the instance list.
    """
    if csv_path is None or not Path(csv_path).exists():
        return None
    font, _fmt = source_font.load_source_cached(source_path)
    defaults = dict(axis_defaults or declared_axis_defaults(source_path))
    if not defaults:
        defaults = {str(ax["tag"]).lower(): float(ax["default"])
                    for ax in (source_font.get_axes(font) or [])
                    if ax.get("tag") and ax.get("default") is not None}
    rows, _cols, in_cols, out_cols, _rest = read_csv_mappings_with_axes(
        Path(csv_path), source_path)
    for row in rows:
        ins: Dict[str, float] = {}
        for col in in_cols:
            value = parse_decimal(str(row.get(col, "")).strip())
            if value is not None:
                ins[normalize_in_axis_name(col)] = float(value)
        if not ins or any(tag not in defaults
                          or abs(value - defaults[tag]) > 1e-6
                          for tag, value in ins.items()):
            continue
        out: Dict[str, float] = {}
        for col in out_cols:
            value = parse_decimal(str(row.get(col, "")).strip())
            if value is not None:
                # CSV columns are upper-case tags; the master coordinates and
                # the location the dialog sends are lower-case tags.
                out[str(col).lower()] = float(value)
        return out or None
    return None


def resting_master_report(source_path: Path,
                          location: Optional[Dict[str, float]] = None,
                          csv_path: Optional[Path] = None,
                          axis_defaults: Optional[Dict[str, float]] = None,
                          ) -> Dict:
    """E2l: is the font's resting location the source's DEFAULT master?

    The export dialog warns that the file is larger when it is not — the studio
    rebuilds the variations around the resting style — and the sources note is
    false when it is. The dialog must not guess this from a master list, so it
    asks here with the location the preview actually rests at: the ORIGIN row's
    parametric ``out``, or the "Set default at" location when that is on.

    ``status`` is ``default_master``, ``other_master`` (the file grows but the
    default master is a master the designer can move to) or ``no_master``
    (nothing interpolates there, so a master has to be added). ``unknown`` when
    the ORIGIN row cannot be resolved and no location was passed.
    """
    if not location:
        location = origin_row_out(source_path, csv_path, axis_defaults)
    if not location:
        return {"status": "unknown", "name": None, "default_master": None,
                "max_delta": None, "delta": {}}
    report = origin_master_report(source_path, location)
    default = default_master_name(source_path)
    if report is None or not report["on_master"]:
        status = "no_master"
    elif default is not None and report["nearest"] == default:
        status = "default_master"
    else:
        status = "other_master"
    return {
        "status": status,
        "name": (report or {}).get("nearest"),
        "default_master": default,
        "max_delta": (report or {}).get("max_delta"),
        "delta": (report or {}).get("delta") or {},
    }


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
        out_floats = {k: float(v) for k, v in out_axes.items()}
        results.append({
            "instance_name": instance_name,
            "glyphs_coordinates": glyphs_coords,
            "avar2_mapping": {
                "in": in_axes,
                "out": out_floats,
            },
            "match_status": "matched" if not mismatches else "mismatch",
            "coordinate_mismatches": mismatches,
            "origin_master": origin_master_report(source_path, out_floats),
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
