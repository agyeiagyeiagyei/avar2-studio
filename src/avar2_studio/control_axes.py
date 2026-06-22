"""Control axes — sidecar JSON I/O (v2 slice 1).

A **control axis** is one the designer declares in the studio (not in
the source file) with their own min/max/default. The declaration
lives in a sibling JSON file, ``<basename>-control.json``, parallel
to ``<basename>-avar.csv``. Coverage glyphs + brace-layer outline
snapshots arrive in later v2 slices; v2.0 only handles the axis
declaration itself.

Schema (versioned)::

    {
      "version": 1,
      "axes": [
        {
          "tag": "crbr",
          "display_name": "Crossbar",
          "default": 0,
          "min": -100,
          "max": 100,
          "coverage": [],     // empty in v2.0; populated in v2.1
          "layers": {}        // empty in v2.0; populated by Fontra captures
        }
      ]
    }

Model α (from the design doc): the sidecar is **canonical**. The
shadow source file (v2.x) is derived from ``original + sidecar``;
wiping ``.avar2-studio/`` regenerates it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional


_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------


def sidecar_path_for(source_path: Path) -> Path:
    """Return the conventional sidecar path next to the source.

    Mirrors the ``-avar.csv`` placement so the user can see at a
    glance which files belong to the studio's staging."""
    return source_path.parent / f"{source_path.stem}-control.json"


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------


def load(source_path: Path) -> Dict:
    """Return the sidecar's contents as a dict, or an empty
    schema-shaped dict if it doesn't exist or is unreadable."""
    sidecar = sidecar_path_for(source_path)
    if not sidecar.exists():
        return _empty()
    try:
        with sidecar.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    return _normalise(data)


def list_axes(source_path: Path) -> List[Dict]:
    """Convenience: return just the ``axes`` array."""
    return list(load(source_path).get("axes", []))


def find_axis(source_path: Path, tag: str) -> Optional[Dict]:
    """Look up a single axis entry by tag. Returns None if absent."""
    tag_norm = (tag or "").strip().lower()
    if not tag_norm:
        return None
    for ax in list_axes(source_path):
        if str(ax.get("tag", "")).lower() == tag_norm:
            return ax
    return None


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------


def add_axis(
    source_path: Path,
    tag: str,
    display_name: str,
    default: float,
    min_value: float,
    max_value: float,
) -> Dict:
    """Append a new control-axis declaration. Validates the inputs and
    refuses duplicates (case-insensitive tag match). Returns the
    fully-resolved axis dict on success.

    Raises ``ValueError`` on validation failure — caller (HTTP layer)
    surfaces as 400.
    """
    tag_norm = _validate_tag(tag)
    if not display_name or not display_name.strip():
        raise ValueError("display_name is required")
    try:
        default_f = float(default)
        min_f = float(min_value)
        max_f = float(max_value)
    except (TypeError, ValueError):
        raise ValueError("default, min, and max must be numeric")
    if not (min_f < max_f):
        raise ValueError(f"min ({min_f}) must be strictly less than max ({max_f})")
    if not (min_f <= default_f <= max_f):
        raise ValueError(
            f"default ({default_f}) must lie within [min, max] = [{min_f}, {max_f}]"
        )

    data = load(source_path)
    for existing in data["axes"]:
        if str(existing.get("tag", "")).lower() == tag_norm:
            raise ValueError(f"control axis '{tag_norm}' already exists")

    entry = {
        "tag": tag_norm,
        "display_name": display_name.strip(),
        "default": default_f,
        "min": min_f,
        "max": max_f,
        "coverage": [],
        "layers": {},
    }
    data["axes"].append(entry)
    _save(source_path, data)
    return entry


def remove_axis(source_path: Path, tag: str) -> bool:
    """Delete a control-axis declaration. Returns True if a row was
    removed, False if no axis matched the tag."""
    tag_norm = (tag or "").strip().lower()
    if not tag_norm:
        return False
    data = load(source_path)
    before = len(data["axes"])
    data["axes"] = [
        ax for ax in data["axes"]
        if str(ax.get("tag", "")).lower() != tag_norm
    ]
    if len(data["axes"]) == before:
        return False
    _save(source_path, data)
    return True


def set_extra_locations(source_path: Path, tag: str, entries: List[Dict]) -> List[Dict]:
    """Replace the axis's ``extra_locations`` list with ``entries``.
    Each entry must be ``{glyph: str, location: {axis_tag: number}}``.
    Returns the canonical-shape stored list.

    Raises ``ValueError`` if the axis tag doesn't exist in the sidecar.
    """
    tag_norm = (tag or "").strip().lower()
    if not tag_norm:
        raise ValueError("tag is required")
    data = load(source_path)
    target = None
    for ax in data["axes"]:
        if str(ax.get("tag", "")).lower() == tag_norm:
            target = ax
            break
    if target is None:
        raise ValueError(f"control axis '{tag_norm}' not found")
    cleaned = _normalise_extra_locations(entries)
    target["extra_locations"] = cleaned
    _save(source_path, data)
    return cleaned


def set_coverage(source_path: Path, tag: str, glyph_names: List[str]) -> List[str]:
    """Replace an axis's coverage list with ``glyph_names``. De-dups,
    preserves the input order (designer ordering carries meaning when
    they group by row/cluster). Returns the canonical stored list.

    Raises ``ValueError`` if the axis tag doesn't exist in the sidecar.
    """
    tag_norm = (tag or "").strip().lower()
    if not tag_norm:
        raise ValueError("tag is required")

    cleaned: List[str] = []
    seen: set = set()
    for name in glyph_names or []:
        if not isinstance(name, str):
            continue
        stripped = name.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        cleaned.append(stripped)

    data = load(source_path)
    target = None
    for ax in data["axes"]:
        if str(ax.get("tag", "")).lower() == tag_norm:
            target = ax
            break
    if target is None:
        raise ValueError(f"control axis '{tag_norm}' not found")
    target["coverage"] = cleaned
    _save(source_path, data)
    return cleaned


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _empty() -> Dict:
    return {"version": _SCHEMA_VERSION, "axes": []}


def _normalise(data: Dict) -> Dict:
    """Fill in missing schema fields so callers can assume a stable
    shape. Doesn't mutate the file on disk — that happens on next
    save."""
    axes = data.get("axes") or []
    out_axes: List[Dict] = []
    for ax in axes:
        if not isinstance(ax, dict):
            continue
        out_axes.append({
            "tag": ax.get("tag", ""),
            "display_name": ax.get("display_name", ax.get("tag", "")),
            "default": float(ax.get("default", 0)),
            "min": float(ax.get("min", -1000)),
            "max": float(ax.get("max", 1000)),
            "coverage": list(ax.get("coverage") or []),
            # extra_locations: list of {glyph, location} entries —
            # additional brace layers beyond the auto-seeded min/max
            # pair. Each location is a sparse dict {axis_tag: value};
            # axes not in the dict interpolate from masters.
            "extra_locations": _normalise_extra_locations(ax.get("extra_locations")),
            "layers": dict(ax.get("layers") or {}),
        })
    return {"version": data.get("version") or _SCHEMA_VERSION, "axes": out_axes}


def _normalise_extra_locations(raw) -> List[Dict]:
    out: List[Dict] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        glyph = entry.get("glyph")
        location = entry.get("location")
        if not isinstance(glyph, str) or not glyph.strip():
            continue
        if not isinstance(location, dict) or not location:
            continue
        clean_loc: Dict[str, float] = {}
        for tag, value in location.items():
            if not isinstance(tag, str) or not tag.strip():
                continue
            try:
                clean_loc[tag.strip()] = float(value)
            except (TypeError, ValueError):
                continue
        if not clean_loc:
            continue
        out.append({"glyph": glyph.strip(), "location": clean_loc})
    return out


def _save(source_path: Path, data: Dict) -> None:
    sidecar = sidecar_path_for(source_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _validate_tag(tag: str) -> str:
    """Enforce OpenType tag rules: exactly 4 characters, ASCII
    printable, lowercase canonical form. Returns the normalised tag.
    """
    if not tag:
        raise ValueError("tag is required")
    stripped = tag.strip()
    if len(stripped) != 4:
        raise ValueError(f"tag must be exactly 4 characters (got '{stripped}', {len(stripped)} chars)")
    if not all(33 <= ord(c) <= 126 for c in stripped):
        raise ValueError(f"tag must be ASCII-printable (got '{stripped}')")
    return stripped.lower()


# --------------------------------------------------------------------------
# Shadow source file (v2 slice 2)
#
# The studio never writes to the original source. When the user declares
# at least one control axis, we generate a SHADOW .glyphs file at
# ``<original-dir>/.avar2-studio/shadow/<basename>`` derived from
# ``original + sidecar``. The build pipeline points at the shadow when
# it exists; otherwise it falls back to the original (so users who
# never declare control axes pay no shadow cost).
#
# v2 scope is .glyphs only — .designspace shadow management is heavier
# (per-location UFO masters) and deferred to v2.5 per the design doc.
# --------------------------------------------------------------------------


def shadow_dir_for(original_path: Path) -> Path:
    """Per-source shadow directory: sibling to the original under the
    studio's existing ``.avar2-studio/`` workdir."""
    return original_path.parent / ".avar2-studio" / "shadow"


def shadow_path_for(original_path: Path) -> Path:
    """Path to the shadow source file. Same basename as the original
    so external tooling (Glyphs.app, fontmake) treats the two as
    structurally identical."""
    return shadow_dir_for(original_path) / original_path.name


def shadow_exists(original_path: Path) -> bool:
    return shadow_path_for(original_path).exists()


def regenerate_shadow(original_path: Path) -> Optional[Path]:
    """Derive the shadow from ``original + sidecar``:

      1. Wipe and re-copy the original to the shadow path.
      2. Mutate the shadow .glyphs file via glyphsLib to add every
         control axis from the sidecar that isn't already in the
         font's axis list. Each master's coordinate vector is
         extended with the new axis's default value, since masters
         sit at the axis default until brace layers exist.

    Returns the shadow path on success, ``None`` if regeneration
    isn't applicable (e.g., sidecar is empty — caller should treat
    that as "no shadow needed"). Raises on hard failures so the
    HTTP layer can 500.
    """
    if original_path.suffix.lower() != ".glyphs":
        # .designspace shadow handling lands in v2.5. Skip silently
        # so v2 slice 2 still ships with the .glyphs path working.
        return None

    axes_to_add = list_axes(original_path)
    if not axes_to_add:
        # Sidecar empty — caller can remove any pre-existing shadow.
        return None

    shadow_path = shadow_path_for(original_path)
    shadow_dir_for(original_path).mkdir(parents=True, exist_ok=True)

    # Preserve outlines the designer has already drawn in the
    # previous shadow (model β-with-best-effort during the v2.5a
    # window — model α with full sidecar back-capture is a later
    # slice). We read the existing shadow BEFORE the copy-from-
    # original wipes it, then merge any matching brace-layer
    # outlines back in after the axes get re-applied.
    preserved_layers = _extract_brace_outlines(shadow_path) if shadow_path.exists() else {}

    # Always re-copy from original. The shadow is fully derived;
    # incremental updates would just multiply the bug surface.
    shutil.copy2(original_path, shadow_path)

    # Mutate the shadow's axis list. We import glyphsLib lazily so
    # this module stays importable in non-.glyphs contexts (and unit
    # tests don't need to pull glyphsLib for sidecar-only tests).
    from glyphsLib import GSFont
    from glyphsLib.classes import GSAxis, GSLayer

    font = GSFont(str(shadow_path))
    existing_tags = {str(getattr(ax, "axisTag", "")).lower() for ax in font.axes}

    # Each control axis's index in the eventual axis list (so we can
    # find the right slot when writing brace-layer coordinates).
    axis_index_by_tag: Dict[str, int] = {}

    for spec in axes_to_add:
        tag = (spec.get("tag") or "").strip().lower()
        if not tag:
            continue
        if tag in existing_tags:
            # Pre-existing source axis — find its index for brace
            # layer indexing.
            for i, src_ax in enumerate(font.axes):
                if str(getattr(src_ax, "axisTag", "")).lower() == tag:
                    axis_index_by_tag[tag] = i
                    break
            continue

        new_axis = GSAxis()
        new_axis.axisTag = tag
        new_axis.name = spec.get("display_name") or tag
        font.axes.append(new_axis)
        axis_index_by_tag[tag] = len(font.axes) - 1
        existing_tags.add(tag)

        default_value = float(spec.get("default", 0))
        for master in font.masters:
            coords = list(getattr(master, "axes", None) or [])
            coords.append(default_value)
            master.axes = coords

    # Tag → axis-index map across the full final axis list. Used by
    # both the auto-seed (axis-min/max) loop and the extra_locations
    # loop below to resolve sparse {axis_tag: value} dicts.
    full_axis_index_by_tag: Dict[str, int] = {}
    for i, ax in enumerate(font.axes):
        full_axis_index_by_tag[str(getattr(ax, "axisTag", "")).lower()] = i

    # Seed brace layers for coverage glyphs (v2 slice 3) + any
    # custom-location entries from extra_locations (v2 slice 4).
    # For each control axis with coverage, every covered glyph gets
    # two seed brace layers (axis-min, axis-max). For each
    # extra_location entry, an additional brace layer at the full
    # location vector derived by overlaying the sparse pin on the
    # default master's position. Each layer is a copy of the
    # default master's outline (or a preserved outline from a
    # previous shadow regenerate) so the brace exists as a
    # structural target. v2 slice 5 (Fontra) lets the designer
    # replace these seeded outlines with actual alternate drawings.
    if font.masters:
        default_master = font.masters[0]
        default_master_id = default_master.id
        default_loc = list(getattr(default_master, "axes", None) or [])

        # Build the seed-location list per (axis, glyph). Two flavours:
        # (1) auto seeds at axis-min/max for every coverage glyph;
        # (2) extra_locations as the designer pinned them.
        seed_jobs: List[tuple] = []  # (glyph_name, location_list, axis_tag_for_idempotency_check)
        for spec in axes_to_add:
            tag = (spec.get("tag") or "").strip().lower()
            if not tag:
                continue
            axis_index = axis_index_by_tag.get(tag)
            if axis_index is None:
                continue

            coverage = list(spec.get("coverage") or [])
            if coverage:
                for seed_value in [float(spec.get("min", 0)), float(spec.get("max", 0))]:
                    for glyph_name in coverage:
                        location = list(default_loc)
                        location[axis_index] = seed_value
                        seed_jobs.append((glyph_name, location))

            # extra_locations (slice 4): per-glyph, full N-D location
            # built by overlaying sparse pins on the default-master
            # coordinates.
            for entry in spec.get("extra_locations") or []:
                glyph_name = entry.get("glyph")
                if not glyph_name:
                    continue
                pinned = entry.get("location") or {}
                location = list(default_loc)
                pinned_any = False
                for pin_tag, pin_value in pinned.items():
                    idx = full_axis_index_by_tag.get(str(pin_tag).lower())
                    if idx is None:
                        continue
                    try:
                        location[idx] = float(pin_value)
                        pinned_any = True
                    except (TypeError, ValueError):
                        continue
                if not pinned_any:
                    continue
                seed_jobs.append((glyph_name, location))

        # De-duplicate seed jobs — same (glyph, location) added by
        # multiple paths only needs writing once.
        seen_jobs: set = set()
        for glyph_name, location in seed_jobs:
            job_key = (glyph_name, tuple(location))
            if job_key in seen_jobs:
                continue
            seen_jobs.add(job_key)

            glyph = font.glyphs[glyph_name] if glyph_name in font.glyphs else None
            if glyph is None:
                print(
                    f"  [control-axes] glyph '{glyph_name}' not in source — skipping seed layer",
                )
                continue
            default_layer = next(
                (l for l in glyph.layers if l.associatedMasterId == default_master_id),
                None,
            )
            if default_layer is None:
                continue

            # Skip if a brace layer at this exact location already
            # exists (idempotent regeneration; also catches collisions
            # between an auto-seed and an extra_location that happen
            # to land at the same point).
            if any(
                list(dict(getattr(l, "attributes", None) or {}).get("coordinates") or [])
                == location
                for l in glyph.layers
            ):
                continue

            # If the previous shadow had drawn outlines at this
            # (glyph, location) — preserve them. Otherwise seed with
            # the default master's outline so the brace layer is a
            # no-op until the designer edits.
            preserved = preserved_layers.get(
                (glyph_name, tuple(float(v) for v in location))
            )
            if preserved is not None:
                layer_paths = preserved["paths"]
                layer_components = preserved["components"]
                layer_anchors = preserved["anchors"]
                layer_width = preserved["width"]
            else:
                layer_paths = list(default_layer.paths) if default_layer.paths else []
                layer_components = (
                    list(default_layer.components) if default_layer.components else []
                )
                layer_anchors = (
                    list(default_layer.anchors) if default_layer.anchors else []
                )
                layer_width = default_layer.width

            brace = GSLayer()
            brace.associatedMasterId = default_master_id
            brace.attributes = {"coordinates": location}
            brace.paths = layer_paths
            brace.components = layer_components
            brace.anchors = layer_anchors
            brace.width = layer_width
            brace.name = "{" + ", ".join(_fmt_coord(v) for v in location) + "}"
            glyph.layers.append(brace)

    font.save(str(shadow_path))
    return shadow_path


def _fmt_coord(value: float) -> str:
    """Format a coordinate the way Glyphs.app's brace-layer-name
    convention does: integer when whole, else fixed precision."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def remove_shadow(original_path: Path) -> bool:
    """Delete the shadow directory entirely. Called when the sidecar
    becomes empty — original is the sole source again. Returns True
    if anything was removed."""
    shadow_dir = shadow_dir_for(original_path)
    if not shadow_dir.exists():
        return False
    shutil.rmtree(shadow_dir)
    return True


def _extract_brace_outlines(shadow_path: Path) -> Dict[tuple, Dict[str, object]]:
    """Read every brace layer from a shadow .glyphs and return a
    map keyed by ``(glyph_name, location_tuple)`` to the layer's
    outline data (paths / components / anchors / width). Used by
    regenerate_shadow to preserve drawn outlines across regenerations.

    The location tuple is the layer's ``attributes.coordinates`` as a
    tuple of floats. We treat any layer carrying a coordinates entry
    as a brace layer — including the ones we authored ourselves at
    axis-min / axis-max. Layers at master positions and default-master
    layers (no coordinates attribute) are skipped.
    """
    if not shadow_path.exists():
        return {}
    try:
        from glyphsLib import GSFont
        font = GSFont(str(shadow_path))
    except Exception as exc:
        print(f"Warning: failed to read previous shadow for outline preservation: {exc}", file=sys.stderr)
        return {}

    out: Dict[tuple, Dict[str, object]] = {}
    for glyph in font.glyphs:
        for layer in glyph.layers:
            attrs = getattr(layer, "attributes", None) or {}
            coords = attrs.get("coordinates") if hasattr(attrs, "get") else None
            if not coords:
                continue
            # Normalise coords to a tuple of floats. glyphsLib gives us
            # a list; tuple makes it hashable for dict keys.
            try:
                if isinstance(coords, (list, tuple)):
                    loc = tuple(float(v) for v in coords)
                elif hasattr(coords, "items"):
                    # Dict-shaped {axis_index: value}. Preserve key order
                    # by sorting on int key.
                    loc = tuple(
                        float(v) for _, v in sorted(coords.items(), key=lambda kv: int(kv[0]))
                    )
                else:
                    continue
            except (TypeError, ValueError):
                continue
            out[(glyph.name, loc)] = {
                "paths": list(layer.paths) if layer.paths else [],
                "components": list(layer.components) if layer.components else [],
                "anchors": list(layer.anchors) if layer.anchors else [],
                "width": layer.width,
            }
    return out


# sys is referenced in _extract_brace_outlines via print(file=sys.stderr).
# Imported here rather than at module top to keep the rest of the
# module unaware of stderr plumbing.
import sys
