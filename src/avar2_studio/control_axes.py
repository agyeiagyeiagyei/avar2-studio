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
            "layers": dict(ax.get("layers") or {}),
        })
    return {"version": data.get("version") or _SCHEMA_VERSION, "axes": out_axes}


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

    # Always re-copy from original. The shadow is fully derived;
    # incremental updates would just multiply the bug surface.
    shutil.copy2(original_path, shadow_path)

    # Mutate the shadow's axis list. We import glyphsLib lazily so
    # this module stays importable in non-.glyphs contexts (and unit
    # tests don't need to pull glyphsLib for sidecar-only tests).
    from glyphsLib import GSFont
    from glyphsLib.classes import GSAxis

    font = GSFont(str(shadow_path))
    existing_tags = {str(getattr(ax, "axisTag", "")).lower() for ax in font.axes}

    for spec in axes_to_add:
        tag = (spec.get("tag") or "").strip().lower()
        if not tag or tag in existing_tags:
            continue
        new_axis = GSAxis()
        new_axis.axisTag = tag
        new_axis.name = spec.get("display_name") or tag
        font.axes.append(new_axis)
        existing_tags.add(tag)

        default_value = float(spec.get("default", 0))
        # Each master sits at the axis default for this new axis (no
        # master coverage until brace layers exist). Extend each
        # master's coordinate vector accordingly.
        for master in font.masters:
            coords = list(getattr(master, "axes", None) or [])
            coords.append(default_value)
            master.axes = coords

    font.save(str(shadow_path))
    return shadow_path


def remove_shadow(original_path: Path) -> bool:
    """Delete the shadow directory entirely. Called when the sidecar
    becomes empty — original is the sole source again. Returns True
    if anything was removed."""
    shadow_dir = shadow_dir_for(original_path)
    if not shadow_dir.exists():
        return False
    shutil.rmtree(shadow_dir)
    return True
