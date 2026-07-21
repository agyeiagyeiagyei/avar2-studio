#!/usr/bin/env python3
"""
update_config.py

Generate and inject STAT and avar2 sections into config.yaml from CSV mappings.

This script:
1. Validates CSV structure
2. Generates STAT table section
3. Generates avar2 mappings section
4. Validates generated sections
5. Reads existing config.yaml
6. Validates existing config structure
7. Replaces/updates stat and avar2 sections
8. Validates merged config
9. Writes updated config.yaml (with optional backup)
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

import yaml  # PyYAML

from .. import source_font as _source_font
try:
    import ruamel.yaml
    HAS_RUAMEL = True
except ImportError:
    HAS_RUAMEL = False
    print("Warning: ruamel.yaml not available, will use basic YAML formatting", file=sys.stderr)


# ============================================================================
# Shared utilities (from gen_avar2.py and gen_stat.py)
# ============================================================================

@dataclass(frozen=True)
class RowMapping:
    instance_name: str
    in_axes: Dict[str, Decimal]
    out_axes: Dict[str, Decimal]
    out_axis_order: Tuple[str, ...]


def _is_blank(v: Optional[str]) -> bool:
    return v is None or str(v).strip() == ""


def _parse_decimal(raw: str, *, context: str) -> Decimal:
    s = str(raw).strip()
    if s == "":
        raise ValueError(f"Blank numeric value for {context}")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Non-numeric value '{raw}' for {context}")


def _normalize_in_axis_name(col: str) -> str:
    # "WGHT-e" -> "wght" (legacy support)
    # "WGHT" -> "wght" (new format)
    # "CONTRAST-e" -> "cntr"
    # "CONTRAST" -> "cntr"
    name = col.strip()
    # Remove "-e" suffix if present (for backward compatibility)
    if name.endswith("-e"):
        name = name[:-2]
    name = name.lower()
    if name == "contrast":
        return "cntr"
    return name


def _load_font_key_from_config(config_path: Path) -> str:
    """Load and validate font key from config.yaml."""
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.yaml did not parse to a mapping/dict")

    fvar = data.get("fvarInstances")
    if not isinstance(fvar, dict) or not fvar:
        raise ValueError("config.yaml is missing 'fvarInstances' or it is empty")

    keys = list(fvar.keys())
    if len(keys) != 1:
        raise ValueError(
            f"Found {len(keys)} fvarInstances keys in config.yaml; "
            f"use --font-key to choose one. Keys: {keys}"
        )
    return keys[0]


# ============================================================================
# CSV Validation and Reading
# ============================================================================

def validate_csv_structure(csv_path: Path) -> Tuple[str, List[str], List[str]]:
    """Validate CSV has required columns. Returns (name_col, in_cols, out_cols)."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")

        fieldnames = [h.strip() for h in reader.fieldnames]
        name_col = "Instance Name"
        
        if name_col not in fieldnames:
            raise ValueError(f"CSV must include a '{name_col}' column. Found: {fieldnames}")

        # Detect in: columns (traditional axes, with or without "-e" suffix)
        # Support both "WGHT" and "WGHT-e" formats
        in_cols = []
        traditional_axes = {"WGHT", "WDTH", "OPSZ", "CONTRAST"}
        for c in fieldnames:
            # Check if column is a traditional axis (with or without -e suffix)
            col_upper = c.upper()
            if col_upper in traditional_axes or col_upper.endswith("-E"):
                in_cols.append(c)
        
        # No traditional columns is NOT an error: such a CSV maps instance
        # names straight onto parametric axes, which only makes sense for a
        # font whose fvar axes ARE those parametric axes. in_axes stay
        # empty here; main() fills them from the source's fvarInstances
        # (see _fill_identity_in_axes).
        
        out_cols = [c for c in fieldnames if c not in (name_col,) and c not in in_cols]
        if not out_cols:
            raise ValueError("CSV contains no parametric axis columns for 'out:'")

        # Don't require any specific traditional axis tags — a font with just
        # opsz, or just wght, or a custom traditional axis like ROND is valid.
        # The caller decides whether the in_cols are sufficient for its needs.

        return name_col, in_cols, out_cols


def read_csv_mappings(csv_path: Path) -> List[RowMapping]:
    """Read and validate CSV mappings."""
    name_col, in_cols, out_cols = validate_csv_structure(csv_path)
    out_order = tuple(out_cols)

    mappings: List[RowMapping] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = [h.strip() for h in reader.fieldnames]

        for line_no, row in enumerate(reader, start=2):
            row = {k.strip(): (v if v is not None else "") for k, v in row.items()}

            # Skip fully empty rows
            if all(_is_blank(row.get(c, "")) for c in fieldnames):
                continue

            inst_name = str(row.get(name_col, "")).strip()
            if _is_blank(inst_name):
                raise ValueError(f"Line {line_no}: '{name_col}' is blank")

            # Build in_axes
            in_axes: Dict[str, Decimal] = {}
            for c in in_cols:
                raw = row.get(c, "")
                if _is_blank(raw):
                    continue
                in_axes[_normalize_in_axis_name(c)] = _parse_decimal(raw, context=f"line {line_no} / {c}")

            # Build out_axes (STRICT: no blanks)
            out_axes: Dict[str, Decimal] = {}
            for c in out_cols:
                raw = row.get(c, "")
                if _is_blank(raw):
                    raise ValueError(f"Line {line_no}: parametric axis '{c}' is blank (not allowed)")
                out_axes[c] = _parse_decimal(raw, context=f"line {line_no} / {c}")

            # No traditional columns → in_axes stays empty here. main()
            # fills it from the source's instance coordinates via
            # _fill_identity_in_axes (that's what makes CSV edits remap
            # the design instead of compiling to a no-op table).

            # No traditional axis is universally required; if a row's in_axes
            # is empty the mapping is degenerate and gets skipped elsewhere.
            
            # Contrast is optional - only include if present in CSV
            # Don't add cntr to in_axes if not in CSV

            mappings.append(
                RowMapping(
                    instance_name=inst_name,
                    in_axes=in_axes,
                    out_axes=out_axes,
                    out_axis_order=out_order,
                )
            )

    if not mappings:
        raise ValueError("CSV contains no valid mappings")

    return mappings


def _fill_identity_in_axes(
    mappings: List[RowMapping],
    config: Dict,
    font_key: str,
) -> List[RowMapping]:
    """Fill empty ``in_axes`` (parametric-only CSV) from the source's
    instance coordinates (``fvarInstances`` in the config).

    Such a CSV maps instance names straight onto parametric axes, which
    only makes sense for a font whose fvar axes ARE those parametric
    axes. Pinning ``in:`` to where the instance currently sits (per the
    source) makes the avar2 table remap the design when the CSV's values
    diverge from the source — i.e. studio mapping edits actually change
    the built font. A CSV in sync with its source yields identity
    mappings, which fontc rightly omits as a no-op table.

    Tags stay VERBATIM (case-sensitive fvar tags — never through
    ``_normalize_in_axis_name``, which lowercases); SPAC is excluded,
    matching the out: emission in generate_avar2_yaml_string. Instances
    not found in fvarInstances fall back to identity (in: = out:).
    """
    if all(m.in_axes for m in mappings):
        return mappings  # traditional CSV — nothing to fill
    fvar_instances = {
        str(inst.get("name", "")): (inst.get("coordinates") or {})
        for inst in (config.get("fvarInstances", {}).get(font_key) or [])
    }
    out: List[RowMapping] = []
    for m in mappings:
        if m.in_axes:
            out.append(m)
            continue
        coords = fvar_instances.get(m.instance_name)
        if coords:
            # fvarInstances coordinate keys may be lower-cased by the
            # config initialiser (``xopq`` vs the CSV's ``XOPQ``) — match
            # case-insensitively, emit the CSV's verbatim (fvar) tag.
            out_by_upper = {k.upper(): k for k in m.out_axes}
            in_axes = {}
            for ck, cv in coords.items():
                tag = out_by_upper.get(str(ck).upper())
                if tag and tag != "SPAC":
                    in_axes[tag] = Decimal(str(cv))
        else:
            in_axes = {}
        if not in_axes:  # unknown instance, or no usable coords → identity
            in_axes = {k: v for k, v in m.out_axes.items() if k != "SPAC"}
        out.append(replace(m, in_axes=in_axes))
    return out


# ============================================================================
# STAT Table Generation
# ============================================================================

def extract_axis_values_from_csv(csv_path: Path) -> Dict[str, List[int]]:
    """Extract unique axis values from CSV, keyed by lowercased tag.

    Returns one list per traditional axis column present in the CSV (wght,
    wdth, opsz, cntr, …). Axes the CSV doesn't include are omitted — the
    caller (generate_stat_section) only emits entries for axes it sees.
    """
    # Map of column-name -> lowercase tag key
    # Recognises both "WGHT" and "WGHT-e" (legacy) styles for the registered
    # axes, plus any other column that looks like a registered-axis tag.
    REGISTERED = {"wght", "wdth", "opsz", "cntr", "slnt", "ital", "grad", "spac"}
    CONTRAST_ALIAS = {"contrast": "cntr"}

    accumulators: Dict[str, set] = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return {}

        # Build column->key mapping once.
        column_keys: Dict[str, str] = {}
        for col in reader.fieldnames:
            stripped = col.strip()
            lowered = stripped.rstrip("-eE").lower().rstrip("-")
            if lowered in CONTRAST_ALIAS:
                lowered = CONTRAST_ALIAS[lowered]
            if lowered in REGISTERED:
                column_keys[stripped] = lowered

        if not column_keys:
            return {}

        for col, key in column_keys.items():
            accumulators[key] = set()

        for row in reader:
            for col, key in column_keys.items():
                raw = row.get(col, "")
                if not raw or not str(raw).strip():
                    continue
                try:
                    accumulators[key].add(int(float(raw)))
                except (ValueError, TypeError):
                    continue

    return {k: sorted(v) for k, v in accumulators.items() if v}


def get_weight_name(value: int) -> str:
    """Map weight value to STAT name."""
    names = {
        100: "Thin",
        200: "ExtraLight",
        300: "Light",
        400: "Regular",
        500: "Medium",
        600: "SemiBold",
        700: "Bold",
        800: "ExtraBold",
        900: "Black",
    }
    return names.get(value, str(value))


def get_width_name(value: int) -> str:
    """Map width value to STAT name."""
    names = {
        40: "Condensed",
        100: "Normal",
        160: "Extended",
        220: "Ultra Extended",
    }
    return names.get(value, str(value))


def generate_stat_section(axis_values: Dict[str, List[int]], font_key: str) -> Dict:
    """Generate STAT table as dict structure."""
    stat_data = {
        font_key: []
    }
    
    # Add Optical Size axis only if there's variation (more than one value)
    if "opsz" in axis_values and len(axis_values["opsz"]) > 1:
        stat_data[font_key].append({
            "name": "Optical Size",
            "tag": "opsz",
            "values": [{"name": f"{v}pt", "value": v} for v in axis_values["opsz"]],
        })

    # Width axis (only if the CSV actually has one)
    if "wdth" in axis_values and axis_values["wdth"]:
        stat_data[font_key].append({
            "name": "Width",
            "tag": "wdth",
            "values": [{"name": get_width_name(v), "value": v} for v in axis_values["wdth"]],
        })

    # Weight axis (only if the CSV actually has one)
    if "wght" in axis_values and axis_values["wght"]:
        weight_entries = []
        for wght_val in axis_values["wght"]:
            weight_entry = {"name": get_weight_name(wght_val), "value": wght_val}
            if wght_val == 400:  # Regular links to Bold
                weight_entry["linkedValue"] = 700
                weight_entry["flags"] = 2
            weight_entries.append(weight_entry)
        stat_data[font_key].append({
            "name": "Weight",
            "tag": "wght",
            "values": weight_entries,
        })

    # Add Contrast axis if present
    if "cntr" in axis_values:
        contrast_axis = {
            "name": "Contrast",
            "tag": "cntr",
            "values": [],
        }
        for cntr_val in axis_values["cntr"]:
            if cntr_val == -10:
                contrast_axis["values"].append({"name": "Low Contrast", "value": cntr_val})
            elif cntr_val == 0:
                contrast_axis["values"].append({"name": "Normal", "value": cntr_val})
            elif cntr_val == 10:
                contrast_axis["values"].append({"name": "High Contrast", "value": cntr_val})
            else:
                contrast_axis["values"].append({"name": str(cntr_val), "value": cntr_val})
        stat_data[font_key].append(contrast_axis)

    return stat_data


def validate_stat_section(stat_data: Dict, font_key: str) -> None:
    """Validate STAT section structure."""
    if font_key not in stat_data:
        raise ValueError(f"STAT section missing font key: {font_key}")

    axes = stat_data[font_key]
    if not isinstance(axes, list):
        raise ValueError("STAT axes must be a list")

    # No traditional axis is universally required — a single-axis font (e.g.
    # opsz-only Jaro) is valid. Just require at least one axis entry.
    if not axes:
        raise ValueError("STAT section must contain at least one axis entry")

    for axis in axes:
        if not isinstance(axis, dict):
            raise ValueError(f"STAT axis must be a dict: {axis}")
        if "tag" not in axis or "name" not in axis or "values" not in axis:
            raise ValueError(f"STAT axis missing required fields: {axis}")
        if not isinstance(axis["values"], list):
            raise ValueError(f"STAT axis values must be a list: {axis['tag']}")


# ============================================================================
# avar2 Generation
# ============================================================================

def _pad_in_envelope(mappings: List[RowMapping], source_axes: List[Dict]) -> List[RowMapping]:
    """Pad the avar2 in: envelope to the source's declared axis ranges.

    fontc derives the built fvar's min/default/max from the avar2 in:
    locations (the default is the source default clamped into the
    envelope). A CSV that lists only its instances — never reaching the
    axis extremes — therefore builds a font whose axes are clamped to
    the instance envelope and whose default may shift to the envelope's
    edge (e.g. a source declaring XTRA 94…3330 building as
    181.2…2744.3 with the default moved to 181.2). Appending identity
    (in: = out:) entries at the per-axis source min/max corners keeps
    the full declared range reachable without remapping anything.
    Corner rows duplicating an existing mapping are dropped downstream.
    """
    if not mappings:
        return mappings
    out_order = mappings[0].out_axis_order
    tags = [t for t in out_order if t != "SPAC"]
    axis_by_tag = {str(a.get("tag")): a for a in source_axes}
    if not tags or not all(t in axis_by_tag for t in tags):
        return mappings  # can't bound an axis we don't have ranges for
    try:
        default_coords = {t: Decimal(str(axis_by_tag[t]["default"])) for t in tags}
    except (KeyError, InvalidOperation, TypeError):
        default_coords = None
    out: List[RowMapping] = list(mappings)
    for label, key in (("Auto: range min", "min"), ("Auto: range max", "max")):
        coords = {t: Decimal(str(axis_by_tag[t][key])) for t in tags}
        # A corner AT the default location (min == default on every
        # axis, the common parametric layout) adds no envelope — the
        # default is reachable by definition — and it normalizes to the
        # avar2 origin, colliding with any at-default mapping row
        # (fontTools: "Locations must be unique"). Skip it.
        if default_coords is not None and coords == default_coords:
            continue
        out.append(RowMapping(label, dict(coords), dict(coords), out_order))
    return out


def _declared_axis_defaults(config_path: Optional[Path]) -> Dict[str, Decimal]:
    """Studio-declared defaults for non-parametric axes, keyed by fvar
    tag, from the sibling axis-metadata.json. Mirrors what the
    gen-avar2 shim applies to created axes, so the collision guard
    normalizes in: locations against the same defaults the compiled
    font will actually use."""
    out: Dict[str, Decimal] = {}
    if not config_path:
        return out
    meta_path = Path(config_path).parent / "axis-metadata.json"
    if not meta_path.exists():
        return out
    try:
        import json as _json
        meta = _json.loads(meta_path.read_text(encoding="utf-8")) or {}
        for _col, m in meta.items():
            tag = (m.get("registered_tag") or "").strip()
            if not tag or m.get("is_parametric") or m.get("default") is None:
                continue
            try:
                out[tag] = Decimal(str(m["default"]))
            except InvalidOperation:
                continue
    except Exception:
        return {}
    return out


def _drop_colliding_in_locations(
    mappings: List[RowMapping],
    source_axes: List[Dict],
    declared_defaults: Optional[Dict[str, Decimal]] = None,
) -> List[RowMapping]:
    """Drop mappings whose EFFECTIVE in: location duplicates an earlier
    row's. Raw in: dicts can differ yet normalize identically: a
    missing axis sits at its default, an explicit value can equal the
    default ({} vs {XOPQ: 1} when XOPQ's default is 1), and gen-avar2
    derives a made-axis default from the minimum of its in: values.
    fontTools then dies with "Locations must be unique" deep inside
    gen-avar2 — this guard reports the colliding instances by NAME and
    keeps the first row instead."""
    if not mappings:
        return mappings
    defaults: Dict[str, Decimal] = {}
    for a in (source_axes or []):
        tag = str(a.get("tag"))
        if a.get("default") is not None:
            try:
                defaults[tag] = Decimal(str(a["default"]))
            except InvalidOperation:
                continue
    # ALL tag comparison is case-insensitive from here: in_axes keys
    # arrive in CSV-column case ('WGHT', 'OPSZ') while source tags and
    # declared registered tags are their own cases ('XTRA', 'wght') —
    # a case miss fills a blank cell with 0 instead of the axis
    # default and two colliding rows sail through as "different".
    defaults = {str(k).upper(): v for k, v in defaults.items()}
    # Axes gen-avar2 will CREATE (in: tags with no source axis): their
    # default becomes the axis minimum (gen_fvar_axes assigns
    # defaultValue = min of the in: values).
    source_tags = {str(a.get("tag")).upper() for a in (source_axes or [])}
    for m in mappings:
        for tag, v in m.in_axes.items():
            tag_u = str(tag).upper()
            if tag_u in source_tags:
                continue  # source axis — default already set above
            if tag_u not in defaults or v < defaults[tag_u]:
                defaults[tag_u] = v
    # Studio-declared defaults beat the min-rule — the shim gives the
    # compiled axes these defaults, so effective locations must be
    # judged against them.
    defaults.update({str(k).upper(): v for k, v in (declared_defaults or {}).items()})
    all_tags = sorted({str(t).upper() for m in mappings for t in m.in_axes})
    seen: Dict[tuple, RowMapping] = {}
    out: List[RowMapping] = []
    for m in mappings:
        in_ci = {str(k).upper(): v for k, v in m.in_axes.items()}
        # Compare as FLOATS: Decimal('144.0') (CSV) and Decimal('144')
        # (metadata default) are numerically equal but stringify
        # differently, which let colliding rows sail through.
        loc = tuple(
            (t, float(in_ci.get(t, defaults.get(t, Decimal(0)))))
            for t in all_tags
        )
        if loc in seen:
            prev = seen[loc]
            if prev.out_axes == m.out_axes:
                note = "dropped (one avar2 entry serves both)"
            else:
                note = ("with a DIFFERENT out: — dropped the later row; "
                        "check these instances' mapping values")
            print(
                f"  ⚠ '{m.instance_name}' resolves to the same avar2 input "
                f"location as '{prev.instance_name}' — {note}",
                file=sys.stderr,
            )
            continue
        seen[loc] = m
        out.append(m)
    return out


def _drop_identical_duplicates(mappings: List[RowMapping]) -> List[RowMapping]:
    """Drop rows whose in: AND out: both duplicate an earlier row.

    Benign repeats happen when a source declares two named instances at
    the same location (e.g. a "HighContrast" variant at identical
    coordinates): one mapping serves both, so the second row is dropped
    with a note. Same in: with DIFFERENT out: is a real contradiction —
    those rows stay, for _dedupe_check to raise on.
    """
    seen: Dict[Tuple[Tuple[str, str], ...], str] = {}
    out: List[RowMapping] = []
    for m in mappings:
        key = tuple(
            sorted((k, str(v)) for k, v in list(m.in_axes.items()) + list(m.out_axes.items()))
        )
        if key in seen:
            print(
                f"  ℹ Duplicate mapping '{m.instance_name}' identical to "
                f"'{seen[key]}' — dropped (one avar2 entry serves both)",
                file=sys.stderr,
            )
            continue
        seen[key] = m.instance_name
        out.append(m)
    return out


def _dedupe_check(mappings: List[RowMapping]) -> None:
    """Check for duplicate in: coordinate tuples."""
    seen: Dict[Tuple[Tuple[str, str], ...], str] = {}
    for m in mappings:
        key = tuple((k, str(v)) for k, v in sorted(m.in_axes.items()))
        if key in seen:
            raise ValueError(
                "Duplicate 'in:' coordinates detected:\n"
                f"  First: {seen[key]}\n"
                f"  Second: {m.instance_name}\n"
                f"  Coords: {dict(key)}"
            )
        seen[key] = m.instance_name


def _sort_key(m: RowMapping) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    """Sort key: primary wdth, secondary wght, tertiary -opsz, quaternary cntr.

    Each axis defaults to 0 when absent so single-axis fonts (e.g. opsz-only)
    sort cleanly without raising KeyError.
    """
    zero = Decimal(0)
    wdth = m.in_axes.get("wdth", zero)
    wght = m.in_axes.get("wght", zero)
    opsz = m.in_axes.get("opsz", zero)
    contrast = m.in_axes.get("cntr", zero)
    return (wdth, wght, -opsz, contrast)


def _fmt_decimal(d: Decimal) -> str:
    """Format decimal for YAML output."""
    if d == d.to_integral_value():
        return str(int(d))
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def generate_avar2_yaml_string(
    mappings: List[RowMapping],
    font_key: str,
    include_group_headers: bool = True,
    skip_opsz_if_no_variation: bool = True,
) -> str:
    """Generate avar2 section as YAML string (preserves comments and formatting).
    
    If skip_opsz_if_no_variation is True, removes opsz from in_axes if all mappings
    have the same opsz value.
    """
    mappings = _drop_identical_duplicates(mappings)
    _dedupe_check(mappings)
    
    # Check for opsz variation if requested
    if skip_opsz_if_no_variation:
        opsz_values = {m.in_axes.get("opsz") for m in mappings if "opsz" in m.in_axes}
        if len(opsz_values) <= 1:
            # Remove opsz from all mappings
            for m in mappings:
                if "opsz" in m.in_axes:
                    # Create new dict without opsz
                    new_in_axes = {k: v for k, v in m.in_axes.items() if k != "opsz"}
                    # Replace the in_axes (RowMapping is frozen, so we need to recreate)
                    # Actually, we can't modify frozen dataclass, so we'll filter during output
    
    mappings = sorted(mappings, key=_sort_key)

    lines: List[str] = []
    lines.append("avar2:")
    lines.append(f"  {font_key}:")

    current_wdth: Optional[Decimal] = None
    current_opsz: Optional[Decimal] = None

    # Determine if we should skip opsz in output
    opsz_values = {m.in_axes.get("opsz") for m in mappings if "opsz" in m.in_axes}
    skip_opsz = skip_opsz_if_no_variation and len(opsz_values) <= 1
    has_wdth = any("wdth" in m.in_axes for m in mappings)

    for m in mappings:
        wdth = m.in_axes.get("wdth")  # May not exist on single-axis fonts
        opsz = m.in_axes.get("opsz")  # May not exist if skipped

        if include_group_headers:
            if has_wdth and wdth is not None and (current_wdth is None or wdth != current_wdth):
                lines.append("")
                lines.append("  # =========================")
                lines.append(f"  # Width = {_fmt_decimal(wdth)}")
                lines.append("  # =========================")
                current_wdth = wdth
                current_opsz = None

            if opsz is not None and (current_opsz is None or opsz != current_opsz):
                lines.append("")
                lines.append(f"  # OPSZ = {_fmt_decimal(opsz)}")
                current_opsz = opsz

        lines.append("")
        # Instance name already includes contrast suffix from CSV expansion
        # (cntr=0 keeps original, cntr=-10/+10 get "Contrast-Min"/"Contrast-Max" suffix)
        lines.append(f"  # {m.instance_name}")
        lines.append("  - in:")

        # Stable ordering for in: (sorted by axis name)
        # Skip opsz if no variation
        in_axes_to_output = {k: v for k, v in m.in_axes.items() if not (skip_opsz and k == "opsz")}
        for k in sorted(in_axes_to_output.keys()):
            lines.append(f"      {k}: {_fmt_decimal(in_axes_to_output[k])}")

        lines.append("    out:")
        # Preserve out-axis order from CSV header, but exclude SPAC (spacing axis is handled separately)
        for k in m.out_axis_order:
            if k != "SPAC":  # Exclude SPAC from avar2 out mappings
                lines.append(f"      {k}: {_fmt_decimal(m.out_axes[k])}")

    lines.append("")
    return "\n".join(lines)


def validate_avar2_yaml(avar2_yaml: str, font_key: str) -> None:
    """Validate avar2 YAML string."""
    try:
        data = yaml.safe_load(avar2_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f"Generated avar2 YAML is invalid: {e}")

    if not isinstance(data, dict) or "avar2" not in data:
        raise ValueError("Generated avar2 YAML missing 'avar2' key")

    if font_key not in data["avar2"]:
        raise ValueError(f"Generated avar2 YAML missing font key: {font_key}")

    entries = data["avar2"][font_key]
    if not isinstance(entries, list):
        raise ValueError("avar2 entries must be a list")

    if len(entries) == 0:
        raise ValueError("avar2 section has no entries")


# ============================================================================
# Config File Merging
# ============================================================================

def load_config(config_path: Path) -> Dict:
    """Load and validate config.yaml."""
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse config.yaml: {e}")

    if not isinstance(data, dict):
        raise ValueError("config.yaml did not parse to a mapping/dict")

    # Validate required keys. fvarInstances is intentionally NOT required:
    # instance-less sources omit it (the gftools builder's schema rejects
    # an EMPTY fvarInstances list, and its own schema marks it optional).
    required_keys = {"sources", "familyName"}
    missing = required_keys - set(data.keys())
    if missing:
        raise ValueError(f"config.yaml missing required keys: {missing}")

    return data


def merge_config(
    config: Dict,
    stat_data: Optional[Dict],
    avar2_yaml: str,
    font_key: str,
    add_contrast: bool = False,
    csv_path: Optional[Path] = None,
) -> Tuple[Dict, str]:
    """Merge stat and avar2 sections into config.

    Returns (updated_config_dict, avar2_yaml_string) for writing.

    ``stat_data=None`` (parametric-only CSV) leaves any existing stat
    section untouched. If add_contrast is True, automatically adds cntr: 0
    to all fvarInstances.
    Note: spacingAxis section is preserved as-is and never updated automatically.
    """
    # Update/replace stat section (skip when the CSV has no traditional axes)
    if stat_data is not None:
        config["stat"] = stat_data

    # Parse avar2 YAML to validate structure, but we'll use the string for output
    avar2_parsed = yaml.safe_load(avar2_yaml)
    config["avar2"] = avar2_parsed["avar2"]
    
    # Note: spacingAxis section is preserved as-is and never updated automatically
    # The spacingAxis min/max values should be set manually in the config file
    
    # If contrast was added, update fvarInstances to include cntr: 0
    if add_contrast:
        fvar_instances = config.get("fvarInstances", {})
        if font_key in fvar_instances:
            for instance in fvar_instances[font_key]:
                if "coordinates" not in instance:
                    instance["coordinates"] = {}
                coords = instance["coordinates"]
                if "cntr" not in coords:
                    coords["cntr"] = 0

    return config, avar2_yaml


def validate_merged_config(config: Dict, font_key: str, require_stat: bool = True) -> None:
    """Validate the merged config structure."""
    # Check stat section (absent is fine for parametric-only CSVs)
    if "stat" not in config:
        if require_stat:
            raise ValueError("Merged config missing 'stat' section")
    elif font_key not in config["stat"]:
        raise ValueError(f"Merged config stat section missing font key: {font_key}")

    # Check avar2 section
    if "avar2" not in config:
        raise ValueError("Merged config missing 'avar2' section")
    if font_key not in config["avar2"]:
        raise ValueError(f"Merged config avar2 section missing font key: {font_key}")

    # Validate YAML can be serialized
    try:
        yaml.dump(config, default_flow_style=False)
    except Exception as e:
        raise ValueError(f"Config cannot be serialized to YAML: {e}")


def write_config(
    config: Dict,
    avar2_yaml: str,
    config_path: Path,
    backup: bool = True,
) -> None:
    """Write config.yaml, replacing stat and avar2 sections.
    
    Uses a hybrid approach: writes most of config as YAML, but injects
    the pre-formatted avar2 YAML string to preserve comments.
    """
    if backup:
        backup_path = config_path.with_suffix(".yaml.backup")
        if config_path.exists():
            shutil.copy2(config_path, backup_path)
            print(f"Created backup: {backup_path}", file=sys.stderr)

    # Read original file
    original_text = config_path.read_text(encoding="utf-8")
    original_lines = original_text.splitlines()

    # Find section boundaries using regex-like approach
    stat_start_idx = None
    stat_end_idx = None
    avar2_start_idx = None
    avar2_end_idx = None
    fvar_instances_start_idx = None
    fvar_instances_end_idx = None

    in_stat = False
    in_avar2 = False
    in_fvar_instances = False
    stat_indent = None
    avar2_indent = None
    fvar_instances_indent = None

    for i, line in enumerate(original_lines):
        stripped = line.strip()
        
        # Detect stat section
        if stripped == "stat:" or stripped.startswith("stat:"):
            stat_start_idx = i
            in_stat = True
            stat_indent = len(line) - len(line.lstrip())
            continue
        
        # Detect fvarInstances section
        if stripped == "fvarInstances:" or stripped.startswith("fvarInstances:"):
            fvar_instances_start_idx = i
            in_fvar_instances = True
            fvar_instances_indent = len(line) - len(line.lstrip())
            if in_stat:
                stat_end_idx = i
                in_stat = False
            continue
        
        # Detect avar2 section
        if stripped == "avar2:" or stripped.startswith("avar2:"):
            avar2_start_idx = i
            in_avar2 = True
            avar2_indent = len(line) - len(line.lstrip())
            if in_stat:
                stat_end_idx = i
                in_stat = False
            if in_fvar_instances:
                fvar_instances_end_idx = i
                in_fvar_instances = False
            continue
        
        # Detect end of sections (top-level key)
        if in_stat or in_avar2 or in_fvar_instances:
            if not line.strip():  # Blank line, continue
                continue
            current_indent = len(line) - len(line.lstrip())
            # If we hit a line with same or less indent than section start, and it's a key
            if ":" in line and not line.strip().startswith("#"):
                if in_stat and current_indent <= stat_indent and stripped != "stat:":
                    stat_end_idx = i
                    in_stat = False
                if in_fvar_instances and current_indent <= fvar_instances_indent and stripped != "fvarInstances:":
                    fvar_instances_end_idx = i
                    in_fvar_instances = False
                if in_avar2 and current_indent <= avar2_indent and stripped != "avar2:":
                    avar2_end_idx = i
                    in_avar2 = False

    # If still in section at end of file
    if in_stat:
        stat_end_idx = len(original_lines)
    if in_fvar_instances:
        fvar_instances_end_idx = len(original_lines)
    if in_avar2:
        avar2_end_idx = len(original_lines)

    # Build new file
    new_lines = []

    # Determine which sections need to be updated
    # Order: spacingAxis, fvarInstances, stat, avar2 (in file order)
    
    # Find spacingAxis section
    spacing_axis_start_idx = None
    spacing_axis_end_idx = None
    in_spacing_axis = False
    spacing_axis_indent = None
    
    for i, line in enumerate(original_lines):
        stripped = line.strip()
        if stripped == "spacingAxis:" or stripped.startswith("spacingAxis:"):
            spacing_axis_start_idx = i
            in_spacing_axis = True
            spacing_axis_indent = len(line) - len(line.lstrip())
            continue
        if in_spacing_axis:
            if not line.strip():
                continue
            current_indent = len(line) - len(line.lstrip())
            if ":" in line and not line.strip().startswith("#"):
                if current_indent <= spacing_axis_indent and stripped != "spacingAxis:":
                    spacing_axis_end_idx = i
                    in_spacing_axis = False
    if in_spacing_axis:
        spacing_axis_end_idx = len(original_lines)
    
    # Find the first section that needs updating (excluding spacingAxis for now)
    first_update_idx = None
    for idx in [fvar_instances_start_idx, stat_start_idx, avar2_start_idx]:
        if idx is not None and (first_update_idx is None or idx < first_update_idx):
            first_update_idx = idx
    
    # Part before first section to update (but handle spacingAxis separately)
    if spacing_axis_start_idx is not None and "spacingAxis" in config:
        # Write everything before spacingAxis
        new_lines.extend(original_lines[:spacing_axis_start_idx])
        
        # Write updated spacingAxis
        spacing_yaml_lines = yaml.dump(
            {"spacingAxis": config["spacingAxis"]},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).strip().splitlines()
        new_lines.extend(spacing_yaml_lines)
        new_lines.append("")
        
        # Skip original spacingAxis section, continue from after it
        if spacing_axis_end_idx is not None:
            # Find next section after spacingAxis
            next_section_start = first_update_idx if first_update_idx is not None else len(original_lines)
            if spacing_axis_end_idx < next_section_start:
                # Add any blank lines between spacingAxis and next section
                between_lines = original_lines[spacing_axis_end_idx:next_section_start]
                for line in between_lines:
                    if line.strip() or (new_lines and new_lines[-1].strip()):
                        new_lines.append(line)
    elif first_update_idx is not None:
        new_lines.extend(original_lines[:first_update_idx])
    else:
        new_lines.extend(original_lines)

    # Insert fvarInstances section if it needs updating
    if fvar_instances_start_idx is not None:
        fvar_yaml_lines = yaml.dump(
            {"fvarInstances": config["fvarInstances"]},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).strip().splitlines()
        new_lines.extend(fvar_yaml_lines)
        new_lines.append("")
        
        # Add lines between fvarInstances and next section (if any)
        if fvar_instances_end_idx is not None:
            # Determine next section
            next_section_start = None
            if stat_start_idx is not None and stat_start_idx > fvar_instances_end_idx:
                next_section_start = stat_start_idx
            elif avar2_start_idx is not None and avar2_start_idx > fvar_instances_end_idx:
                next_section_start = avar2_start_idx
            
            if next_section_start is not None:
                between_lines = original_lines[fvar_instances_end_idx:next_section_start]
                # Filter out excessive blank lines
                for line in between_lines:
                    if line.strip() or (new_lines and new_lines[-1].strip()):
                        new_lines.append(line)
    
    # Insert stat section (add if missing, replace if exists)
    if stat_start_idx is not None:
        # Replace existing stat section
        stat_yaml_lines = yaml.dump(
            {"stat": config["stat"]},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).strip().splitlines()
        new_lines.extend(stat_yaml_lines)
        new_lines.append("")
    elif "stat" in config and avar2_start_idx is not None:
        # Add stat section before avar2 if it doesn't exist
        stat_yaml_lines = yaml.dump(
            {"stat": config["stat"]},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).strip().splitlines()
        new_lines.extend(stat_yaml_lines)
        new_lines.append("")
    elif "stat" in config and fvar_instances_end_idx is not None:
        # Add stat section after fvarInstances if avar2 doesn't exist
        stat_yaml_lines = yaml.dump(
            {"stat": config["stat"]},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ).strip().splitlines()
        new_lines.extend(stat_yaml_lines)
        new_lines.append("")

    # Part between stat and avar2
    if stat_start_idx is not None and avar2_start_idx is not None:
        if stat_end_idx is not None and avar2_start_idx > stat_end_idx:
            # Skip blank lines between sections
            between = original_lines[stat_end_idx:avar2_start_idx]
            new_lines.extend([l for l in between if l.strip() or not new_lines or new_lines[-1].strip()])
    elif "stat" in config and stat_start_idx is None and avar2_start_idx is not None and fvar_instances_end_idx is not None:
        # STAT was just added, handle spacing between fvarInstances and avar2
        # Skip blank lines between fvarInstances and avar2 (STAT is now in between)
        between = original_lines[fvar_instances_end_idx:avar2_start_idx]
        # Filter out excessive blank lines
        for line in between:
            if line.strip() or (new_lines and new_lines[-1].strip()):
                new_lines.append(line)

    # Insert avar2 section (preserve formatting with comments)
    if avar2_start_idx is not None:
        new_lines.extend(avar2_yaml.strip().splitlines())
        if new_lines and new_lines[-1].strip():  # Only add blank line if last line isn't blank
            new_lines.append("")
    else:
        # No avar2 section in the original file — append it. Without this,
        # a freshly initialised config silently loses the generated avar2:
        # the build "succeeds" but the table never lands in the font.
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.extend(avar2_yaml.strip().splitlines())
        if new_lines and new_lines[-1].strip():
            new_lines.append("")

    # Part after avar2
    if avar2_start_idx is not None and avar2_end_idx is not None:
        after = original_lines[avar2_end_idx:]
        # Skip initial blank lines
        skip_blank = True
        for line in after:
            if skip_blank and not line.strip():
                continue
            skip_blank = False
            new_lines.append(line)
    elif avar2_start_idx is None and stat_start_idx is not None:
        # avar2 was added, but stat existed - nothing after
        pass

    # Write file
    output = "\n".join(new_lines)
    if not output.endswith("\n"):
        output += "\n"
    config_path.write_text(output, encoding="utf-8")


# ============================================================================
# Main
# ============================================================================

# Import contrast expansion from separate module
try:
    from expand_contrast import expand_csv_with_contrast
except ImportError:
    # Fallback if running from different directory
    import importlib.util
    expand_contrast_path = Path(__file__).parent / "expand_contrast.py"
    if expand_contrast_path.exists():
        spec = importlib.util.spec_from_file_location("expand_contrast", expand_contrast_path)
        expand_contrast = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(expand_contrast)
        expand_csv_with_contrast = expand_contrast.expand_csv_with_contrast
    else:
        expand_csv_with_contrast = None


def expand_csv_with_opsz(
    csv_path: Path, 
    opsz_config_path: Optional[Path] = None,
    output_path: Optional[Path] = None
) -> Path:
    """
    Expand CSV with MinOPSZ and MaxOPSZ rows based on opsz.yaml configuration.
    
    For each base row (OPSZ=48), creates 2 additional rows:
    - MinOPSZ (OPSZ=12): Adjusted XOPQ/YOPQ/XTRA with -MinOPSZ suffix
    - MaxOPSZ (OPSZ=72): Adjusted XOPQ/YOPQ/XTRA with -MaxOPSZ suffix
    
    Returns path to expanded CSV.
    """
    if opsz_config_path is None:
        opsz_config_path = csv_path.parent / "opsz.yaml"
    
    if not opsz_config_path.exists():
        raise FileNotFoundError(f"opsz.yaml not found at {opsz_config_path}")
    
    # Load opsz config
    with opsz_config_path.open('r') as f:
        config = yaml.safe_load(f)
    
    base_opsz = config['base_opsz']
    min_opsz = config['min_opsz']
    max_opsz = config['max_opsz']
    weight_points = sorted(config['weight_adjustments'], key=lambda x: x['weight'])
    xtra_condensed = config['xtra_adjustments']['condensed_widths']
    
    def interpolate_multiplier(weight: int, axis: str) -> Tuple[float, float]:
        """Interpolate multiplier for given weight and axis."""
        if weight <= weight_points[0]['weight']:
            return (
                weight_points[0]['min_opsz_multipliers'][axis],
                weight_points[0]['max_opsz_multipliers'][axis]
            )
        if weight >= weight_points[-1]['weight']:
            return (
                weight_points[-1]['min_opsz_multipliers'][axis],
                weight_points[-1]['max_opsz_multipliers'][axis]
            )
        
        for i in range(len(weight_points) - 1):
            w1, w2 = weight_points[i]['weight'], weight_points[i+1]['weight']
            if w1 <= weight <= w2:
                t = (weight - w1) / (w2 - w1)
                min_m1 = weight_points[i]['min_opsz_multipliers'][axis]
                min_m2 = weight_points[i+1]['min_opsz_multipliers'][axis]
                max_m1 = weight_points[i]['max_opsz_multipliers'][axis]
                max_m2 = weight_points[i+1]['max_opsz_multipliers'][axis]
                min_mult = min_m1 + (min_m2 - min_m1) * t
                max_mult = max_m1 + (max_m2 - max_m1) * t
                return min_mult, max_mult
        
        return 1.0, 1.0
    
    # Determine output path
    if output_path is None:
        # Use temporary file if no output path specified
        output_path = Path(tempfile.mktemp(suffix='.csv', prefix='avar2-mappings_with_opsz_'))
    
    rows = []
    with csv_path.open('r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        for row in reader:
            rows.append(row)
    
    # Generate new rows
    new_rows = []
    
    for row in rows:
        # Check for missing required values first
        wght_str = row.get('WGHT', '').strip()
        wdth_str = row.get('WDTH', '').strip()
        if not wght_str or not wdth_str:
            # Skip rows with missing required values entirely from opsz expansion
            # These rows will remain in the original CSV and won't be expanded
            # (They can't be used for opsz generation without WGHT/WDTH)
            continue
        
        # Only generate opsz rows if this row has the base opsz
        # Handle empty string or missing OPSZ value
        opsz_value = row.get('OPSZ', '') or str(base_opsz)
        if not opsz_value.strip():
            opsz_value = str(base_opsz)
        if int(opsz_value) != base_opsz:
            # Keep non-base-opsz rows as-is
            new_rows.append(row.copy())
            continue
        
        # Keep base row (OPSZ=base_opsz)
        base_row = row.copy()
        # Ensure SPAC is set for base row (0 for base OPSZ)
        if 'SPAC' in base_row:
            base_row['SPAC'] = base_row.get('SPAC', '').strip() or '0'
        new_rows.append(base_row)
        
        wght = int(wght_str)
        wdth = int(wdth_str)
        base_xopq = float(row.get('XOPQ', '0') or '0')
        base_yopq = float(row.get('YOPQ', '0') or '0')
        base_xtra = float(row.get('XTRA', '0') or '0')
        instance_name = row.get('Instance Name', '')
        
        # Get multipliers
        min_xopq_mult, max_xopq_mult = interpolate_multiplier(wght, 'xopq')
        min_yopq_mult, max_yopq_mult = interpolate_multiplier(wght, 'yopq')
        
        # Apply XTRA adjustments for condensed
        min_xtra_mult = xtra_condensed['min_opsz_multiplier'] if wdth < 100 else 1.0
        max_xtra_mult = xtra_condensed['max_opsz_multiplier'] if wdth < 100 else 1.0
        
        # Calculate adjusted values
        min_xopq = base_xopq * min_xopq_mult
        max_xopq = base_xopq * max_xopq_mult
        min_yopq = base_yopq * min_yopq_mult
        max_yopq = base_yopq * max_yopq_mult
        min_xtra = base_xtra * min_xtra_mult
        max_xtra = base_xtra * max_xtra_mult
        
        # Create MinOPSZ row
        min_row = row.copy()
        min_row['Instance Name'] = f"{instance_name}-MinOPSZ"
        min_row['OPSZ'] = str(min_opsz)
        min_row['XOPQ'] = f"{min_xopq:.3f}"
        min_row['YOPQ'] = f"{min_yopq:.3f}"
        min_row['XTRA'] = f"{min_xtra:.3f}"
        # Set SPAC: +20 for MinOPSZ
        if 'SPAC' in min_row:
            min_row['SPAC'] = '20'
        else:
            min_row['SPAC'] = '20'
        new_rows.append(min_row)
        
        # Create MaxOPSZ row
        max_row = row.copy()
        max_row['Instance Name'] = f"{instance_name}-MaxOPSZ"
        max_row['OPSZ'] = str(max_opsz)
        max_row['XOPQ'] = f"{max_xopq:.3f}"
        max_row['YOPQ'] = f"{max_yopq:.3f}"
        max_row['XTRA'] = f"{max_xtra:.3f}"
        # Set SPAC: -20 for MaxOPSZ
        if 'SPAC' in max_row:
            max_row['SPAC'] = '-20'
        else:
            max_row['SPAC'] = '-20'
        new_rows.append(max_row)
    
    # Write output
    with output_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(new_rows)
    
    return output_path


def check_opsz_variation(csv_path: Path) -> bool:
    """Check if CSV has variation in OPSZ column (more than one unique value)."""
    opsz_values = set()
    with csv_path.open('r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            opsz = row.get('OPSZ', '').strip()
            if opsz:
                opsz_values.add(opsz)
    return len(opsz_values) > 1


def update_config(
    csv_path: Path,
    config_path: Path,
    font_key: Optional[str] = None,
    backup: bool = False,
    dry_run: bool = False,
    source_path: Optional[Path] = None,
    fill_in_defaults: bool = False,
) -> None:
    """Generate STAT + avar2 sections from a CSV and merge them into config.yaml.

    Raises ``RuntimeError`` on failure. Designed to be called in-process from
    the server's build pipeline so we don't pay subprocess startup cost on
    every build-on-save. ``source_path`` (the active source) lets the avar2
    in: envelope be padded to the declared axis ranges — without it the
    built fvar axes clamp to the CSV's instance envelope.
    """
    argv: List[str] = [
        "--csv", str(csv_path),
        "--config", str(config_path),
    ]
    if font_key:
        argv += ["--font-key", font_key]
    if not backup:
        argv.append("--no-backup")
    if dry_run:
        argv.append("--dry-run")
    if source_path:
        argv += ["--source", str(source_path)]
    if fill_in_defaults:
        argv.append("--fill-in-defaults")

    rc = main(argv)
    if rc != 0:
        raise RuntimeError(
            f"update_config failed (exit code {rc}) for csv={csv_path} config={config_path}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate and inject STAT and avar2 sections into config.yaml from CSV."
    )
    ap.add_argument("--csv", required=True, type=Path, help="Path to CSV exported from spreadsheet.")
    ap.add_argument("--config", required=True, type=Path, help="Path to config.yaml to update.")
    ap.add_argument("--font-key", default=None, help="Override font key (otherwise auto-detected).")
    ap.add_argument("--no-backup", action="store_true", help="Don't create backup of config.yaml.")
    ap.add_argument("--dry-run", action="store_true", help="Validate only, don't write changes.")
    ap.add_argument("--add-contrast", action="store_true", help="Expand CSV with contrast variations before processing.")
    ap.add_argument("--add-opsz", action="store_true", help="Expand CSV with MinOPSZ and MaxOPSZ rows based on opsz.yaml.")
    ap.add_argument("--save-opsz-csv", action="store_true", help="Save expanded opsz CSV to file (default: use temporary file, auto-deleted).")
    ap.add_argument("--source", type=Path, default=None,
                    help="Source file (.glyphs/.designspace). Its declared axis ranges pad the avar2 in: envelope so fontc doesn't clamp the built fvar axes to the CSV's instance envelope.")
    ap.add_argument("--fill-in-defaults", action="store_true",
                    help="Materialize blank in: cells at the declared axis defaults. "
                         "Blank normally means 'at default', which rebinds if a build "
                         "relocates the default (export-with-default) — aliasing "
                         "explicitly-authored rows onto the new origin. Filling keeps "
                         "every row's location fixed regardless of the compiled defaults.")
    args = ap.parse_args(argv)

    try:
        # Step 1: Validate CSV structure
        print("Step 1: Validating CSV structure...", file=sys.stderr)
        validate_csv_structure(args.csv)
        print("  ✓ CSV structure valid", file=sys.stderr)

        # Step 2: Read CSV mappings (with optional contrast/opsz expansion)
        print("Step 2: Reading CSV mappings...", file=sys.stderr)
        csv_to_use = args.csv
        
        # Check opsz variation before expansion (for avar2 generation later)
        has_opsz_variation_before = check_opsz_variation(csv_to_use)
        
        if args.add_opsz:
            print("  Expanding CSV with opsz variations...", file=sys.stderr)
            # Determine output path based on flag
            if args.save_opsz_csv:
                opsz_output = csv_to_use.parent / f"{csv_to_use.stem}_with_opsz{csv_to_use.suffix}"
            else:
                opsz_output = None  # Will use temporary file
            csv_to_use = expand_csv_with_opsz(csv_to_use, output_path=opsz_output)
            if args.save_opsz_csv:
                print(f"  ✓ Expanded CSV written to: {csv_to_use}", file=sys.stderr)
            else:
                print(f"  ✓ Expanded CSV generated (temporary file)", file=sys.stderr)
        
        if args.add_contrast:
            print("  Expanding CSV with contrast variations...", file=sys.stderr)
            csv_to_use = expand_csv_with_contrast(
                csv_to_use,
                # Asymmetric scaling for balanced perceptual impact
                # Negative side needs stronger scaling to match positive side's visual impact
                negative_factor=Decimal("1.20"),  # Higher factor for negative (less contrast) side
                negative_offset=Decimal("60"),    # Larger offset for negative side
                positive_factor=Decimal("0.85"),  # Moderate factor for positive (more contrast) side
                positive_offset=Decimal("30"),    # Moderate offset for positive side
            )
            print(f"  ✓ Expanded CSV written to: {csv_to_use}", file=sys.stderr)
        
        mappings = read_csv_mappings(csv_to_use)
        print(f"  ✓ Read {len(mappings)} mappings", file=sys.stderr)
        
        # Store path for cleanup if temporary file was used
        temp_opsz_file = None
        if args.add_opsz and not args.save_opsz_csv:
            temp_opsz_file = csv_to_use

        # Step 3: Load and validate existing config
        print("Step 3: Loading existing config.yaml...", file=sys.stderr)
        config = load_config(args.config)

        # The config's sources: must track the ACTIVE source path — a
        # workspace migration or rename leaves the stored path stale,
        # and the builder would try to read a file that no longer
        # exists (FileNotFoundError deep inside glyphsLib). NB:
        # write_config splices generated sections into the ON-DISK
        # text, so mutating the dict alone is not enough — patch the
        # sources: block in the file itself.
        if args.source and args.source.exists():
            resolved = str(Path(args.source).resolve())
            if config.get("sources") != [resolved]:
                config["sources"] = [resolved]
                import re as _re_src
                text = Path(args.config).read_text(encoding="utf-8")
                text2, n = _re_src.subn(
                    r"(?ms)^sources:\n(?:[ \t]*-[^\n]*\n)+",
                    "sources:\n- " + resolved + "\n",
                    text,
                    count=1,
                )
                if n:
                    Path(args.config).write_text(text2, encoding="utf-8")
                    print(f"  ✓ sources: repointed to {resolved}", file=sys.stderr)
        print("  ✓ Config loaded and validated", file=sys.stderr)

        # Step 4: Determine font key. The builder names the VF after
        # the ACTIVE source's axes — "<family>[<sorted axis tags>].ttf"
        # — so when a source is available, derive from it. The key
        # stored in the config goes stale whenever the axis set
        # changes (e.g. a shadow adds a studio control axis: gen-avar2
        # then looks up the tstx-bearing filename and KeyErrors on the
        # old entry), so it is only a fallback.
        if args.font_key:
            font_key = args.font_key
        else:
            font_key = None
            if args.source and args.source.exists():
                try:
                    font, _fmt = _source_font.load_source(args.source)
                    family = _source_font.get_family_name(font, args.source)
                    tags = sorted(
                        a["tag"] for a in _source_font.get_axes(font) if a.get("tag")
                    )
                    font_key = (
                        f"{family}[{','.join(tags)}].ttf" if tags else f"{family}-VF.ttf"
                    )
                except Exception as e:
                    print(f"  Warning: font key from source failed: {e}", file=sys.stderr)
            if font_key is None:
                font_key = _load_font_key_from_config(args.config)
        print(f"  ✓ Font key: {font_key}", file=sys.stderr)

        # Filename-keyed config sections go stale the same way the
        # font key does — gen-fvar-instances looks up the BUILT
        # filename in ``fvarInstances`` and KeyErrors on the old
        # entry. Re-key in place (textual, single key line) so the
        # rest of the file — comments included — is untouched.
        try:
            cfg_text = Path(args.config).read_text(encoding="utf-8")
            fvar_keys = list((config.get("fvarInstances") or {}).keys())
            if len(fvar_keys) == 1 and fvar_keys[0] != font_key:
                import re as _re_key
                cfg_text2, n_rekey = _re_key.subn(
                    rf"(?m)^(\s+){_re_key.escape(fvar_keys[0])}:\s*$",
                    lambda m: f"{m.group(1)}{font_key}:",
                    cfg_text,
                    count=1,
                )
                if n_rekey:
                    Path(args.config).write_text(cfg_text2, encoding="utf-8")
                    config["fvarInstances"][font_key] = config["fvarInstances"].pop(
                        fvar_keys[0]
                    )
                    print(
                        f"  ✓ fvarInstances: re-keyed {fvar_keys[0]} → {font_key}",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"  Warning: fvarInstances re-key failed: {e}", file=sys.stderr)

        # Parametric-only CSV: pin in: to the source's instance
        # coordinates so edited CSV values remap the design (an in-sync
        # CSV yields identity mappings, which fontc omits as a no-op).
        mappings = _fill_identity_in_axes(mappings, config, font_key)

        # fontc derives the built fvar's min/default/max from the avar2
        # in: envelope — pad it to the source's declared axis ranges or
        # the built font's axes clamp to the CSV's instance envelope.
        source_axes_list: List[Dict] = []
        if args.source and args.source.exists():
            try:
                font, _fmt = _source_font.load_source(args.source)
                source_axes_list = _source_font.get_axes(font)
                mappings = _pad_in_envelope(mappings, source_axes_list)
            except Exception as e:
                print(f"  Warning: could not pad avar2 range envelope: {e}", file=sys.stderr)

        if args.fill_in_defaults:
            # Materialize blank cells at the DECLARED defaults so each
            # row's location stays fixed even when the compiled default
            # moves (export-with-default). Applies to the traditional
            # (non-source) in: axes only.
            source_tags_u = {str(a.get("tag", "")).upper() for a in source_axes_list}
            declared = {
                str(k).upper(): v
                for k, v in _declared_axis_defaults(args.config).items()
            }
            trad_tags = {
                str(t)
                for m in mappings
                for t in m.in_axes
                if str(t).upper() not in source_tags_u
            }
            filled: List[RowMapping] = []
            filled_count = 0
            for m in mappings:
                in_axes = dict(m.in_axes)
                present_u = {str(k).upper() for k in in_axes}
                for t in trad_tags:
                    if str(t).upper() in present_u:
                        continue
                    d = declared.get(str(t).upper())
                    if d is not None:
                        in_axes[t] = d
                        filled_count += 1
                filled.append(replace(m, in_axes=in_axes))
            mappings = filled
            if filled_count:
                print(f"  ℹ Filled {filled_count} blank in: cells at declared defaults", file=sys.stderr)

        # Two mappings that normalize to the SAME in: location crash
        # fontTools ("Locations must be unique") deep inside gen-avar2
        # — catch it here, with instance names, before the build.
        mappings = _drop_colliding_in_locations(
            mappings, source_axes_list, _declared_axis_defaults(args.config)
        )

        # Step 5: Generate STAT section
        print("Step 5: Generating STAT section...", file=sys.stderr)
        # Use expanded CSV if opsz or contrast was added, otherwise use original
        stat_csv = csv_to_use if (args.add_opsz or args.add_contrast) else args.csv
        axis_values = extract_axis_values_from_csv(stat_csv)
        # Remove opsz from STAT if there's no variation (only one value)
        if len(axis_values.get("opsz", [])) <= 1:
            axis_values.pop("opsz", None)
            print("  ℹ OPSZ axis skipped in STAT (no variation)", file=sys.stderr)
        # SPAC is transform-injected, not a STAT axis this generator emits —
        # without the drop, a CSV whose only registered column is SPAC
        # produces an empty section and fails validation.
        axis_values.pop("spac", None)
        if axis_values:
            stat_data = generate_stat_section(axis_values, font_key)
            validate_stat_section(stat_data, font_key)
            print(f"  ✓ STAT section generated ({len(stat_data[font_key])} axes)", file=sys.stderr)
        else:
            # Parametric-only CSV (identity in: case): no registered
            # (traditional) axes to describe, so STAT has nothing to say.
            # Leave any existing stat section in the config untouched.
            stat_data = None
            print("  ℹ No traditional axes in CSV — STAT section skipped", file=sys.stderr)

        # Step 6: Generate avar2 section
        print("Step 6: Generating avar2 section...", file=sys.stderr)
        # Check if opsz has variation (after expansions)
        has_opsz_variation_after = check_opsz_variation(csv_to_use)
        avar2_yaml = generate_avar2_yaml_string(
            mappings, 
            font_key,
            include_group_headers=True,
            skip_opsz_if_no_variation=not has_opsz_variation_after
        )
        validate_avar2_yaml(avar2_yaml, font_key)
        # Count entries
        avar2_parsed = yaml.safe_load(avar2_yaml)
        entry_count = len(avar2_parsed["avar2"][font_key])
        print(f"  ✓ avar2 section generated ({entry_count} entries)", file=sys.stderr)

        # Step 7: Merge into config
        print("Step 7: Merging sections into config...", file=sys.stderr)
        merged_config, avar2_yaml_final = merge_config(
            config, stat_data, avar2_yaml, font_key, 
            add_contrast=args.add_contrast,
            csv_path=csv_to_use
        )
        if args.add_contrast:
            # Verify fvarInstances were updated
            fvar_inst = merged_config.get("fvarInstances", {}).get(font_key, [])
            all_have_cntr = all("cntr" in inst.get("coordinates", {}) for inst in fvar_inst)
            if all_have_cntr:
                print(f"  ✓ fvarInstances updated with cntr: 0 ({len(fvar_inst)} instances)", file=sys.stderr)
        validate_merged_config(merged_config, font_key, require_stat=stat_data is not None)
        print("  ✓ Config merged and validated", file=sys.stderr)

        # Step 8: Write config (or dry-run)
        if args.dry_run:
            print("Step 8: DRY RUN - Config validated, no changes written", file=sys.stderr)
        else:
            print("Step 8: Writing updated config.yaml...", file=sys.stderr)
            write_config(merged_config, avar2_yaml_final, args.config, backup=not args.no_backup)
            print(f"  ✓ Config written: {args.config}", file=sys.stderr)

        print("\n✓ All steps completed successfully!", file=sys.stderr)
        
        # Clean up temporary opsz file if it was created
        if 'temp_opsz_file' in locals() and temp_opsz_file and temp_opsz_file.exists():
            try:
                temp_opsz_file.unlink()
            except Exception:
                pass  # Ignore cleanup errors
        
        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

