"""Config-bundle export/import — the portable studio configuration.

A bundle captures everything the studio authored for one source file so
it can be re-applied on another avar2-studio instance (or another copy
of the same source):

  - control axes ("secondary parametric axes") + brace-layer
    declarations  ← ``<basename>-control.json``
  - avar2 mappings                              ← the avar2 CSV
  - transforms on/off + params                  ← ``<basename>-transforms.json``
  - grade transform (toggle + default + per-instance grade%)
                                                ← ``<basename>-grade.json``

Drawn outlines ride along only if the sidecar already holds them
(model-α ``outline`` fields — see control_axes). Today nothing captures
them into the sidecar automatically, so in practice a bundle carries
locations only and imported brace layers are re-seeded by interpolation
on the target.

Import is **all-or-nothing**: ``validate_bundle`` runs first and
``apply_bundle`` refuses to write anything unless the report is clean.
Validation gates on the target source supplying the "core axis data"
the config depends on — source axes referenced by brace-layer
locations, and the parametric axes the avar2 CSV maps onto.

CSV replace semantics: a bundle with an empty ``avar2_csv`` leaves the
target's CSV untouched (non-destructive default — the CSV is the one
artifact that also syncs back to source instances). Control axes and
transforms are always replaced wholesale.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from importlib import metadata as _importlib_metadata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import control_axes as _control_axes
from . import csv_io as _csv_io
from . import grade as _grade
from . import source_font as _source_font
from .transforms import config as _tx_config
from .transforms import registry as _tx_registry

FORMAT = "avar2-studio-config"
FORMAT_VERSION = 1


def _studio_version() -> str:
    try:
        return _importlib_metadata.version("avar2-studio")
    except Exception:
        return "unknown"


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip()).strip("-")
    return s or "source"


def bundle_filename(family_name: str) -> str:
    return f"{_slug(family_name)}-avar2studio.json"


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def build_export(source_path: Path, csv_path: Optional[Path]) -> Dict:
    """Assemble the bundle dict for ``source_path``. ``csv_path`` is the
    server's currently-resolved avar2 CSV (may be None / nonexistent)."""
    font, _fmt = _source_font.load_source(source_path)
    axes = _source_font.get_axes(font)
    family = _source_font.get_family_name(font, source_path)

    csv_text = ""
    out_columns: List[str] = []
    if csv_path is not None and csv_path.exists():
        csv_text = csv_path.read_text(encoding="utf-8-sig")
        try:
            _, _, _, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, source_path)
            out_columns = list(out_cols)
        except Exception:
            out_columns = []

    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "studio_version": _studio_version(),
        "source": {
            "family_name": family,
            "axes": [
                {
                    "tag": a.get("tag"),
                    "min": a.get("min"),
                    "default": a.get("default"),
                    "max": a.get("max"),
                    "has_master_coverage": a.get("has_master_coverage", True),
                }
                for a in axes
            ],
            "avar2_out_columns": out_columns,
        },
        "control_axes": _control_axes.load(source_path),
        "avar2_csv": csv_text,
        "transforms": _tx_config.load(source_path),
        "grade": _grade.load(source_path),
    }


# --------------------------------------------------------------------------
# Validate
# --------------------------------------------------------------------------


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_bundle(bundle: Dict, source_path: Path) -> Dict:
    """Check ``bundle`` against the target ``source_path``. Returns the
    report dict ``{ok, errors, warnings, summary}``. Nothing is written.
    """
    errors: List[str] = []
    warnings: List[str] = []
    summary = {"axes": 0, "layers": 0, "mapping_rows": 0, "transforms": 0, "grades": 0}

    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        return {
            "ok": False,
            "errors": ["Not an avar2-studio config bundle (missing 'format' marker)."],
            "warnings": [],
            "summary": summary,
        }
    if bundle.get("format_version") != FORMAT_VERSION:
        errors.append(
            f"Unsupported bundle format_version {bundle.get('format_version')!r} "
            f"(this studio understands {FORMAT_VERSION})."
        )

    try:
        font, fmt = _source_font.load_source(source_path)
    except Exception as e:
        return {
            "ok": False,
            "errors": [f"Could not read the loaded source for validation: {e}"],
            "warnings": [],
            "summary": summary,
        }

    src_axes = _source_font.get_axes(font)
    axis_by_tag = {str(a.get("tag", "")): a for a in src_axes}
    parametric_tags = {
        str(a.get("tag", "")).upper()
        for a in src_axes
        if a.get("has_master_coverage", True)
    }

    # ---- control axes -------------------------------------------------
    ca = bundle.get("control_axes") or {}
    bundle_axes = [ax for ax in (ca.get("axes") or []) if isinstance(ax, dict)]
    declared_tags = set()
    if bundle_axes and fmt != "glyphs":
        errors.append(
            "This bundle declares control axes, but studio authoring is "
            ".glyphs-only — the loaded source is a .designspace. The "
            "control_axes section cannot be imported here."
        )
        bundle_axes = []  # don't cascade per-layer errors on top

    glyph_names = set()
    if bundle_axes and fmt == "glyphs":
        glyph_names = {str(g.name) for g in font.glyphs}

    for ax in bundle_axes:
        raw_tag = str(ax.get("tag", "")).strip()
        tag = raw_tag.lower()
        try:
            _control_axes._validate_tag(raw_tag)
        except ValueError as e:
            errors.append(f"control axis '{raw_tag}': {e}")
            continue
        if tag in {t.lower() for t in axis_by_tag}:
            errors.append(
                f"control axis '{tag}' collides with a source axis of the same tag."
            )
        if tag in declared_tags:
            errors.append(f"control axis '{tag}' appears twice in the bundle.")
        declared_tags.add(tag)
        min_v, max_v, def_v = _num(ax.get("min")), _num(ax.get("max")), _num(ax.get("default"))
        if min_v is None or max_v is None or def_v is None:
            errors.append(f"control axis '{tag}': min/default/max must be numbers.")
        elif not (min_v <= def_v <= max_v) or min_v == max_v:
            errors.append(
                f"control axis '{tag}': need min ≤ default ≤ max and min < max "
                f"(got {min_v}/{def_v}/{max_v})."
            )

    # Layer locations may pin any source axis OR any control axis in the bundle.
    known_loc_axes = set(axis_by_tag.keys()) | declared_tags
    for ax in bundle_axes:
        tag = str(ax.get("tag", "")).strip().lower()
        own_min, own_max = _num(ax.get("min")), _num(ax.get("max"))
        for layer in (ax.get("layers") or []):
            if not isinstance(layer, dict):
                continue
            summary["layers"] += 1
            glyph = str(layer.get("glyph", "")).strip()
            if glyph and glyph not in glyph_names:
                errors.append(
                    f"layer on axis '{tag}': glyph '{glyph}' does not exist in this source."
                )
            loc = layer.get("location") or {}
            for atag, val in loc.items():
                if atag not in known_loc_axes:
                    errors.append(
                        f"layer on axis '{tag}' (glyph '{glyph}'): pins axis '{atag}', "
                        f"which this source does not have."
                    )
                    continue
                v = _num(val)
                if v is None:
                    errors.append(
                        f"layer on axis '{tag}' (glyph '{glyph}'): location '{atag}' "
                        f"is not a number ({val!r})."
                    )
                    continue
                if atag in axis_by_tag:
                    lo, hi = _num(axis_by_tag[atag].get("min")), _num(axis_by_tag[atag].get("max"))
                else:
                    lo, hi = own_min, own_max
                if lo is not None and hi is not None and not (lo <= v <= hi):
                    errors.append(
                        f"layer on axis '{tag}' (glyph '{glyph}'): {atag}={v} is outside "
                        f"this source's {atag} range [{lo}, {hi}]."
                    )
    summary["axes"] = len(bundle_axes)

    # ---- avar2 CSV -----------------------------------------------------
    csv_text = bundle.get("avar2_csv") or ""
    if csv_text.strip():
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            fieldnames = [h.strip() for h in (reader.fieldnames or [])]
            if "Instance Name" not in fieldnames:
                raise ValueError(f"CSV must include 'Instance Name' column. Found: {fieldnames}")
            recorded_out = {
                str(c).upper()
                for c in ((bundle.get("source") or {}).get("avar2_out_columns") or [])
            }
            missing = sorted(c for c in recorded_out if c not in parametric_tags)
            for c in missing:
                errors.append(
                    f"avar2 mappings route onto parametric axis '{c}', which this "
                    f"source does not provide (or has no master coverage for)."
                )
            rows = list(reader)
            summary["mapping_rows"] = len(rows)
            numeric_cols = [c for c in fieldnames if c != "Instance Name"]
            for row in rows:
                for c in numeric_cols:
                    cell = (row.get(c) or "").strip()
                    if cell and _num(cell) is None:
                        errors.append(
                            f"avar2 mappings: row '{row.get('Instance Name', '?')}' has a "
                            f"non-numeric value in column '{c}' ({cell!r})."
                        )
        except ValueError as e:
            errors.append(f"avar2 mappings CSV: {e}")
    else:
        warnings.append(
            "Bundle contains no avar2 mappings — the target's existing CSV "
            "will be left untouched."
        )

    # ---- transforms -----------------------------------------------------
    tx = bundle.get("transforms") or {}
    entries = [e for e in (tx.get("transforms") or []) if isinstance(e, dict)]
    summary["transforms"] = len(entries)
    _tx_registry.discover()  # idempotent
    for e in entries:
        ttype = str(e.get("type", ""))
        if ttype not in _tx_registry.REGISTRY:
            errors.append(
                f"transform '{ttype}' is not installed on this instance "
                f"(user transforms live in ~/.avar2-studio/transforms/)."
            )
    if not any(str(e.get("type", "")) not in _tx_registry.REGISTRY for e in entries):
        try:
            _tx_registry.validate(entries)
        except ValueError as e:
            errors.append(f"transforms: {e}")

    # ---- grade ----------------------------------------------------------
    # Grade entries reference instances by name; a graded name absent on the
    # target is harmless (the build skips instances it can't resolve), so we
    # only validate structure here and warn about unknown names.
    gr = bundle.get("grade") or {}
    grade_instances = [g for g in (gr.get("instances") or []) if isinstance(g, dict)]
    summary["grades"] = len(grade_instances)
    known_names = {i.get("name") for i in _source_font.get_source_instances(font)}
    try:
        csv_names_text = bundle.get("avar2_csv") or ""
        if csv_names_text.strip():
            for row in csv.DictReader(io.StringIO(csv_names_text)):
                known_names.add((row.get("Instance Name") or "").strip())
    except Exception:
        pass
    for g in grade_instances:
        name = g.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("grade: an entry is missing an instance name.")
            continue
        if _num(g.get("pct")) is None:
            errors.append(f"grade '{name}': grade% is not a number ({g.get('pct')!r}).")
        elif known_names and name not in known_names:
            warnings.append(
                f"grade targets instance '{name}', which isn't in this source or its "
                f"avar2 mappings — it will be ignored until that instance exists."
            )

    return {"ok": not errors, "errors": errors, "warnings": warnings, "summary": summary}


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------


def apply_bundle(bundle: Dict, source_path: Path, csv_path: Optional[Path]) -> Dict:
    """Write the three sidecars. Caller must have run ``validate_bundle``
    first (we re-run it and refuse on errors). Does NOT regenerate the
    shadow or rebuild — that's the HTTP layer's job (it owns GLYPHS_PATH).

    ``csv_path`` is where the avar2 CSV should be written when the bundle
    carries one — resolved by the server, which knows its own CSV search
    order. Bundle with empty ``avar2_csv`` → CSV left untouched.
    """
    report = validate_bundle(bundle, source_path)
    if not report["ok"]:
        return report

    _control_axes.save_all(source_path, bundle.get("control_axes") or {})

    csv_text = bundle.get("avar2_csv") or ""
    if csv_text.strip() and csv_path is not None:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text(csv_text, encoding="utf-8-sig")

    _tx_registry.discover()
    cleaned = _tx_registry.validate(bundle.get("transforms", {}).get("transforms") or [])
    _tx_config.save(source_path, cleaned)

    # Grade is replaced wholesale, same as transforms.
    _grade.save_all(source_path, bundle.get("grade") or {})

    return report
