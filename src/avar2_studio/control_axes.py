"""Control axes — sidecar JSON I/O.

A **control axis** is one the designer declares in the studio (not in
the source file) with their own min/max/default. The declaration
lives in a sibling JSON file, ``<basename>-control.json``, parallel
to ``<basename>-avar.csv``.

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
          "layers": [                          // flat list, one per brace layer
            {"glyph": "e", "location": {"crbr": -100}},
            {"glyph": "e", "location": {"crbr": 100}},
            {"glyph": "f", "location": {"crbr": 100, "XOPQ": 78}}
          ]
        }
      ]
    }

``layers`` is a flat per-axis list of ``{glyph, location}`` — NOT a
per-glyph object. ``location`` is sparse (only pinned axes) and keyed
by axis tag. Coverage is derived from the unique glyph names in
``layers``; it is not stored. (Legacy ``coverage`` +
``extra_locations`` keys are migrated into ``layers`` on load by
``_normalise`` and never re-emitted.)

Outline storage (model α) is half-wired: the schema carries an
``outline`` value-dump per layer, and ``regenerate_shadow`` restores a
stored outline ahead of the prior-shadow copy and any seed — so a
sidecar holding outlines fully rebuilds the drawings. But
``capture_outlines`` (shadow → sidecar) has no caller yet, so drawn
outlines still live only in the shadow ``.glyphs`` in practice, and
wiping ``.avar2-studio/`` still loses them. See
docs/secondary-parametric-axes.md.
"""

from __future__ import annotations

import copy
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


def save_all(source_path: Path, data: Dict) -> Dict:
    """Replace the WHOLE sidecar with ``data`` (normalised first). Returns
    the payload that was written.

    Used by config-bundle import; the per-axis writers below
    (``add_axis`` / ``set_layers`` / …) stay the interactive API. Caller
    is responsible for validating the payload against the target source
    first (config_port.validate_bundle).
    """
    payload = _normalise(data if isinstance(data, dict) else {})
    _save(source_path, payload)
    return payload


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
        "layers": [],
    }
    data["axes"].append(entry)
    _save(source_path, data)
    return entry


def update_axis(
    source_path: Path,
    tag: str,
    *,
    display_name: Optional[str] = None,
    default: Optional[float] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> Dict:
    """Edit an existing control-axis declaration. Tag is immutable —
    renames require delete + re-add (the tag threads through every
    brace layer's location dict, so a rename would need to migrate
    every layer too — not worth the complexity for v1).

    Any field left as ``None`` is left unchanged. Validates that
    every existing layer's value on this axis still falls inside
    the new [min, max] before persisting.

    Returns the updated axis dict. Raises ``ValueError`` on
    validation failure — caller (HTTP layer) surfaces as 400.
    """
    tag_norm = (tag or "").strip().lower()
    if not tag_norm:
        raise ValueError("tag is required")
    data = load(source_path)
    entry = None
    for ax in data["axes"]:
        if str(ax.get("tag", "")).lower() == tag_norm:
            entry = ax
            break
    if entry is None:
        raise ValueError(f"control axis '{tag_norm}' not found")

    new_name = entry["display_name"] if display_name is None else display_name
    if display_name is not None:
        if not new_name or not new_name.strip():
            raise ValueError("display_name is required")
        new_name = new_name.strip()

    def _num(v, label):
        try:
            return float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be numeric")

    new_min = _num(min_value, "min") if min_value is not None else float(entry["min"])
    new_max = _num(max_value, "max") if max_value is not None else float(entry["max"])
    new_default = _num(default, "default") if default is not None else float(entry["default"])
    if not (new_min < new_max):
        raise ValueError(f"min ({new_min}) must be strictly less than max ({new_max})")
    if not (new_min <= new_default <= new_max):
        raise ValueError(
            f"default ({new_default}) must lie within [min, max] = [{new_min}, {new_max}]"
        )

    for layer in entry.get("layers", []) or []:
        loc = layer.get("location") or {}
        v = loc.get(tag_norm)
        if v is None:
            continue
        if not (new_min <= float(v) <= new_max):
            raise ValueError(
                f"layer for glyph '{layer.get('glyph', '?')}' sits at "
                f"{tag_norm}={v}, outside the new range [{new_min}, {new_max}]. "
                "Delete or move that layer before narrowing the range."
            )

    entry["display_name"] = new_name
    entry["min"] = new_min
    entry["max"] = new_max
    entry["default"] = new_default
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


def set_layers(source_path: Path, tag: str, entries: List[Dict]) -> List[Dict]:
    """Replace an axis's unified ``layers`` list. Each entry shape:
    ``{glyph: str, location: {axis_tag: number}}``. De-duplicates by
    (glyph, location). Returns the canonical-shape stored list.

    Raises ``ValueError`` if the axis tag doesn't exist in the
    sidecar.
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
    cleaned = _normalise_layers(entries)
    target["layers"] = cleaned
    _save(source_path, data)
    return cleaned


def apply_layer_delta(source_path: Path, tag: str, add=None, remove=None) -> List[Dict]:
    """Merge a delta into an axis's layers, using the ON-DISK list as the base.

    ``set_layers`` replaces the whole list, which is a lost-update hazard: the
    caller builds that list from its own cached copy, so a save made while the
    cache is stale silently drops every layer the caller didn't know about
    (authored layers appear to "reset"). A delta only states what changed, so
    concurrent edits compose instead of clobbering.

    ``add``/``remove`` are entry lists shaped like ``layers``. Removal matches
    on (glyph, location); removals are applied before additions, so a
    replace is ``remove=[old], add=[new]``. Returns the stored list.
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

    current = _normalise_layers(target.get("layers") or [])
    remove_keys = {_layer_key(e) for e in _normalise_layers(remove or [])}
    kept = [e for e in current if _layer_key(e) not in remove_keys]
    merged = _normalise_layers(kept + _normalise_layers(add or []))
    target["layers"] = merged
    _save(source_path, data)
    return merged


def reseed_layers(
    source_path: Path,
    tag: str,
    layers: Optional[List[Dict]] = None,
    force: bool = False,
) -> Dict:
    """Drop stored drawings so the next regeneration re-seeds from the CURRENT
    source, and report what that would cost.

    A hand-drawn brace layer's outline is captured into the sidecar, and
    ``regenerate_shadow`` restores it in preference to re-seeding. That is what
    lets a drawing survive the shadow being wiped — but it is also what freezes
    the layer against later edits to the masters it was drawn over. Re-seeding
    is how an updated source gets pulled through, and it DISCARDS the drawing,
    so any layer holding one is refused unless ``force``.

    ``layers`` selects entries by ``{glyph, location}``; omit it for the whole
    axis. Correction layers (those carrying a ``target``) are reported
    separately: they are recomputed on every regeneration already, so there is
    nothing to re-seed and nothing at risk.

    Returns ``{reseeded, blocked, computed, clean, missing}`` — each a list of
    ``{glyph, location}`` — and does not save when nothing changed.
    """
    tag_norm = (tag or "").strip().lower()
    data = load(source_path)
    axis = None
    for ax in data.get("axes") or []:
        if str(ax.get("tag", "")).lower() == tag_norm:
            axis = ax
            break
    if axis is None:
        raise KeyError(f"control axis {tag!r} not found")

    entries = axis.get("layers") or []
    wanted = None
    if layers is not None:
        wanted = {_layer_key(_normalise_one(e)) for e in layers}
        wanted.discard(("", ()))

    result = {"reseeded": [], "blocked": [], "computed": [], "clean": [],
              "missing": []}
    changed = 0
    matched = set()
    for entry in entries:
        key = _layer_key(entry)
        if wanted is not None and key not in wanted:
            continue
        matched.add(key)
        ident = {"glyph": entry.get("glyph"), "location": entry.get("location")}
        if entry.get("target"):
            result["computed"].append(ident)
            continue
        if not entry.get("outline"):
            # Nothing stored: this layer already re-seeds from the source on
            # every regeneration, so it is up to date by construction.
            result["clean"].append(ident)
            continue
        if not force:
            result["blocked"].append(ident)
            continue
        entry.pop("outline", None)
        result["reseeded"].append(ident)
        changed += 1

    if wanted is not None:
        for key in sorted(wanted - matched):
            result["missing"].append({"glyph": key[0], "location": dict(key[1])})

    if changed:
        _save(source_path, data)
    return result


def _normalise_one(entry: Dict) -> Dict:
    """Normalise a single caller-supplied layer identity the same way the
    stored list was, so keys compare equal."""
    got = _normalise_layers([entry])
    return got[0] if got else {"glyph": "", "location": {}}


def _layer_key(entry: Dict):
    """Identity of a normalised layer entry — mirrors the dedup key in
    ``_normalise_layers`` so delta removal matches what's stored."""
    loc = entry.get("location") or {}
    return (str(entry.get("glyph", "")).strip(), tuple(sorted(loc.items())))


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _empty() -> Dict:
    return {"version": _SCHEMA_VERSION, "axes": []}


def _normalise(data: Dict) -> Dict:
    """Fill in missing schema fields so callers can assume a stable
    shape. Doesn't mutate the file on disk — that happens on next
    save.

    Schema migration: pre-v2.4 sidecars stored ``coverage: [...]``
    (implicit auto-seeds at axis-min/max) + ``extra_locations: [...]``
    (custom layers). v2.7 unifies these into a single ``layers``
    array with every brace layer explicit. We migrate-on-load:
    each old ``coverage`` glyph synthesises two layers (at min and
    max); old ``extra_locations`` entries copy through. The
    migrated shape persists on the next save.
    """
    axes = data.get("axes") or []
    out_axes: List[Dict] = []
    for ax in axes:
        if not isinstance(ax, dict):
            continue
        min_v = float(ax.get("min", -1000))
        max_v = float(ax.get("max", 1000))
        tag = (ax.get("tag") or "").strip().lower()

        # Migrate legacy schema if needed.
        layers = ax.get("layers")
        if not isinstance(layers, list):
            # Old shape (or absent) — synthesise from coverage + extra_locations.
            layers = []
            for g in (ax.get("coverage") or []):
                if not isinstance(g, str) or not g.strip():
                    continue
                layers.append({"glyph": g.strip(), "location": {tag: min_v}})
                layers.append({"glyph": g.strip(), "location": {tag: max_v}})
            for entry in (ax.get("extra_locations") or []):
                if not isinstance(entry, dict):
                    continue
                if entry.get("glyph") and entry.get("location"):
                    layers.append(entry)
        layers = _normalise_layers(layers)

        out_axes.append({
            "tag": ax.get("tag", ""),
            "display_name": ax.get("display_name", ax.get("tag", "")),
            "default": float(ax.get("default", 0)),
            "min": min_v,
            "max": max_v,
            "layers": layers,
        })
    return {"version": data.get("version") or _SCHEMA_VERSION, "axes": out_axes}


def _normalise_layers(raw) -> List[Dict]:
    """Validate + dedup the unified layers list. Each entry shape:
    ``{glyph: str, location: {axis_tag: number}, target?: {axis_tag: number}}``.
    Duplicates by (glyph, location) are folded — last write wins.

    ``target`` is the optional CORRECTION: parametric overrides naming
    the location the outline should be computed *as if at*. A layer at
    the wide-ultra corner with ``target {XOPQ: 1100}`` renders the glyph
    interpolated at XOPQ 1100 — how a lowercase correction axis reduces
    stem weight at a corner the global axes drive to 1462. Layers with a
    target are always (re)computed by ``regenerate_shadow``; layers
    without one seed once and keep whatever Fontra draws."""
    out: List[Dict] = []
    seen: set = set()
    if not isinstance(raw, list):
        return out

    def _clean_numbers(d):
        clean: Dict[str, float] = {}
        if not isinstance(d, dict):
            return clean
        for k, v in d.items():
            if not isinstance(k, str) or not k.strip():
                continue
            try:
                clean[k.strip()] = float(v)
            except (TypeError, ValueError):
                continue
        return clean

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        glyph = entry.get("glyph")
        location = entry.get("location")
        if not isinstance(glyph, str) or not glyph.strip():
            continue
        if not isinstance(location, dict) or not location:
            continue
        clean_loc = _clean_numbers(location)
        if not clean_loc:
            continue
        key = (glyph.strip(), tuple(sorted(clean_loc.items())))
        if key in seen:
            continue
        seen.add(key)
        item: Dict = {"glyph": glyph.strip(), "location": clean_loc}
        clean_target = _clean_numbers(entry.get("target"))
        if clean_target:
            item["target"] = clean_target
        # A captured DRAWING (model α). Carried through untouched so a
        # sidecar — and the config bundle built from it — is a complete
        # backup of hand-drawn brace layers, not just their locations.
        outline = entry.get("outline")
        if isinstance(outline, dict) and outline.get("paths") is not None:
            item["outline"] = outline
        out.append(item)
    return out


def _save(source_path: Path, data: Dict) -> None:
    sidecar = sidecar_path_for(source_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # Authored brace layers are the hardest data in the studio to
    # recreate — keep bounded timestamped copies before every rewrite.
    from . import csv_io as _csv_io
    _csv_io.backup_sidecar(sidecar)
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
    axes_to_add = list_axes(original_path)
    if not axes_to_add:
        # Sidecar empty — caller can remove any pre-existing shadow.
        return None

    suffix = original_path.suffix.lower()
    if suffix == ".designspace":
        return _regenerate_shadow_designspace(original_path, axes_to_add)
    if suffix != ".glyphs":
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
    # Newly-declared control axes needing a Virtual Master so glyphsLib
    # gives them a real range (see the Virtual Master block below).
    new_control_axes: List[Dict] = []

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
        new_control_axes.append({
            "name": new_axis.name,
            "index": axis_index_by_tag[tag],
            "min": float(spec.get("min", default_value)),
            "max": float(spec.get("max", default_value)),
            "default": default_value,
        })
        for master in font.masters:
            coords = list(getattr(master, "axes", None) or [])
            coords.append(default_value)
            master.axes = coords

        # Pad EXISTING brace-layer coordinates with the new axis's
        # default. Without this, every old brace in the source
        # ships a shorter coords list than the (now-larger) axis
        # count, and Glyphs/Fontra projects them onto the lower-D
        # subspace — which collides with the brace layers we
        # author at the same N-D projection. User-visible symptom:
        # Fontra's Glyph Sources panel shows "locations must be
        # unique" with multiple duplicate rows at master corners.
        for glyph in font.glyphs:
            for layer in glyph.layers:
                attrs = getattr(layer, "attributes", None)
                if attrs is None:
                    continue
                coords = attrs.get("coordinates") if hasattr(attrs, "get") else None
                if not isinstance(coords, list):
                    continue
                while len(coords) < len(font.axes):
                    coords.append(default_value)
                attrs["coordinates"] = coords

    # Virtual Masters — the crux of making a control axis EDITABLE in
    # Fontra. A control axis only varies via brace layers; no master
    # spans it (every master sits at the axis default). glyphsLib
    # derives axis ranges from master coordinates, so such an axis
    # comes out as [default, default] — zero width — and fontra-glyphs
    # then DROPS it. With the axis gone, brace coordinates mis-map and
    # several layers collapse onto the same location, which Fontra
    # demotes to non-editable *background* layers and flags as
    # "locations must be unique". A Virtual Master declares the axis
    # extreme(s) without a full master, giving glyphsLib a real range
    # so the axis survives into Fontra and each brace becomes a
    # distinct, editable source. One VM per extreme that differs from
    # the axis default (edge-default axes need only the far extreme;
    # interior-default axes need both). fontc reads the range from the
    # axis declaration directly, so the preview build is unaffected.
    if font.masters and new_control_axes:
        from glyphsLib.classes import GSCustomParameter
        base_coords = list(getattr(font.masters[0], "axes", None) or [])
        axis_labels = [
            str(getattr(a, "name", "") or getattr(a, "axisTag", "")) for a in font.axes
        ]
        for cax in new_control_axes:
            idx = cax["index"]
            extremes = []
            if cax["min"] < cax["default"]:
                extremes.append(cax["min"])
            if cax["max"] > cax["default"]:
                extremes.append(cax["max"])
            for extreme in extremes:
                point = list(base_coords)
                if idx < len(point):
                    point[idx] = extreme
                value = [
                    {"Axis": axis_labels[i], "Location": point[i]}
                    for i in range(min(len(axis_labels), len(point)))
                ]
                font.customParameters.append(
                    GSCustomParameter("Virtual Master", value)
                )

    # Tag → axis-index map across the full final axis list. Used to
    # resolve sparse {axis_tag: value} dicts from the unified
    # ``layers`` list into full N-D coordinate vectors.
    full_axis_index_by_tag: Dict[str, int] = {}
    for i, ax in enumerate(font.axes):
        full_axis_index_by_tag[str(getattr(ax, "axisTag", "")).lower()] = i
    tag_by_index = {i: t for t, i in full_axis_index_by_tag.items()}

    # A brace layer is a full point: a parametric master corner × the
    # control-axis value. Label it by that corner + value, so Fontra
    # reads "XTRA3330-XOPQ2-YOPQ2 · crbr 20" — the corner design at
    # crbr=20 — not a generic "crbr = 20".
    control_axis_indices = set()
    for spec in axes_to_add:
        t = str(spec.get("tag") or "").strip().lower()
        if t in full_axis_index_by_tag:
            control_axis_indices.add(full_axis_index_by_tag[t])
    corner_name_by_parametric: Dict[tuple, str] = {}
    for master in font.masters:
        maxes = list(getattr(master, "axes", None) or [])
        param_key = tuple(
            maxes[i] for i in range(len(maxes)) if i not in control_axis_indices
        )
        corner_name_by_parametric[param_key] = master.name

    def _brace_source_label(loc):
        param_key = tuple(
            loc[i] for i in range(len(loc)) if i not in control_axis_indices
        )
        corner = corner_name_by_parametric.get(param_key)
        ctrl = ", ".join(
            f"{tag_by_index.get(i, i)} {_fmt_coord(loc[i])}"
            for i in sorted(control_axis_indices)
            if i < len(loc)
        )
        if corner and ctrl:
            return f"{corner} · {ctrl}"
        return ctrl or (corner or "")

    # Parametric interpolation model. A seeded brace should start as the
    # glyph's NATURAL shape at its parametric location — not a copy of
    # the default master. Without this, a brace at a non-default
    # parametric corner shows the (thin) default-master outline, which
    # reads as "same as default" in Fontra until edited. We build a
    # variation model over the real masters' parametric coordinates
    # (control axes excluded — no masters span them) and interpolate
    # node positions at the brace's location. Exact at a master corner;
    # interpolated in between.
    from fontTools.varLib.models import VariationModel, normalizeLocation

    param_axis_indices = [
        i for i in range(len(font.axes)) if i not in control_axis_indices
    ]
    _param_triples = {}
    for i in param_axis_indices:
        vals = [
            float(m.axes[i])
            for m in font.masters
            if i < len(getattr(m, "axes", None) or [])
        ]
        if vals:
            _param_triples[i] = (min(vals), float(font.masters[0].axes[i]), max(vals))

    def _param_norm(coords):
        loc = {}
        for i in param_axis_indices:
            lo, dflt, hi = _param_triples.get(i, (0.0, 0.0, 0.0))
            v = float(coords[i]) if i < len(coords) else dflt
            loc[str(i)] = (
                0.0 if hi == lo
                else normalizeLocation({str(i): v}, {str(i): (lo, dflt, hi)})[str(i)]
            )
        return loc

    try:
        _interp_model = VariationModel(
            [_param_norm(list(getattr(m, "axes", None) or [])) for m in font.masters]
        )
    except Exception:
        _interp_model = None

    def _interpolated_seed(glyph, location):
        """(paths, width) interpolated for a brace at ``location``, or
        None if the masters aren't outline-compatible for this glyph."""
        if _interp_model is None:
            return None
        mlayers = []
        for m in font.masters:
            ml = next(
                (
                    l for l in glyph.layers
                    if l.associatedMasterId == m.id
                    and not (dict(getattr(l, "attributes", None) or {}).get("coordinates"))
                ),
                None,
            )
            if ml is None:
                return None
            mlayers.append(ml)
        ref_paths = list(mlayers[0].paths or [])
        for ml in mlayers:
            mps = list(ml.paths or [])
            if len(mps) != len(ref_paths):
                return None
            for rp, mp in zip(ref_paths, mps):
                if len(rp.nodes) != len(mp.nodes):
                    return None
        loc = _param_norm(location)
        # Seeding the memo with the owning layer is load-bearing, not a micro-
        # optimisation. GSPath.parent -> GSLayer -> GSGlyph -> GSFont, so a
        # plain deepcopy walks that backref and clones the ENTIRE font for
        # every brace layer (~750ms each; 18s across 24 layers — 92% of a
        # rebuild). Mapping the layer to None stops the walk at the boundary:
        # identical paths/nodes/types, ~750x faster.
        new_paths = copy.deepcopy(ref_paths, {id(mlayers[0]): None})
        for pi, rp in enumerate(new_paths):
            for ni in range(len(rp.nodes)):
                xs = [float(mlayers[mi].paths[pi].nodes[ni].position.x) for mi in range(len(mlayers))]
                ys = [float(mlayers[mi].paths[pi].nodes[ni].position.y) for mi in range(len(mlayers))]
                rp.nodes[ni].position = (
                    _interp_model.interpolateFromMasters(loc, xs),
                    _interp_model.interpolateFromMasters(loc, ys),
                )
        widths = [float(ml.width) for ml in mlayers]
        width = _interp_model.interpolateFromMasters(loc, widths)
        return new_paths, width

    # Seed brace layers from the unified ``layers`` list (v2.7).
    # Every brace layer is explicit — auto-seeds at axis-min/max are
    # gone. Designer authored each location through the
    # AddBraceLayerModal. Each layer is a copy of the default
    # master's outline (or a preserved outline from the previous
    # shadow if Fontra had drawn one there) so the brace exists as
    # a structural target.
    if font.masters:
        default_master = font.masters[0]
        default_master_id = default_master.id
        default_loc = list(getattr(default_master, "axes", None) or [])

        seed_jobs: List[tuple] = []  # (glyph_name, location_list)
        for spec in axes_to_add:
            for entry in spec.get("layers") or []:
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
                # Correction target: the outline is computed as if the
                # glyph sat at the layer's parametric location with these
                # overrides applied (parametric axes only — a target on
                # the control axis itself is meaningless).
                seed_location = None
                target = entry.get("target") or {}
                if isinstance(target, dict) and target:
                    seed_location = list(location)
                    applied = False
                    for t_tag, t_value in target.items():
                        idx = full_axis_index_by_tag.get(str(t_tag).lower())
                        if idx is None or idx in control_axis_indices:
                            continue
                        try:
                            seed_location[idx] = float(t_value)
                            applied = True
                        except (TypeError, ValueError):
                            continue
                    if not applied:
                        seed_location = None
                seed_jobs.append((glyph_name, location, dict(pinned), seed_location,
                                  entry.get("outline")))

        # De-duplicate seed jobs — same (glyph, location) added by
        # multiple paths only needs writing once.
        seen_jobs: set = set()
        for glyph_name, location, pinned, seed_location, stored_outline in seed_jobs:
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
            # Keep a preserved outline only if it's a genuine EDIT — i.e.
            # it differs from the default master. Old seeds (and this
            # loop's own fallback) copied the default master, so a
            # preserved outline that's byte-identical to it is an
            # unedited seed we should re-interpolate to the glyph's
            # natural shape at this location. A drawn outline differs
            # from the master and is preserved.
            preserved_is_edit = (
                preserved is not None
                and _paths_sig(preserved.get("paths")) != _paths_sig(default_layer.paths)
            )
            # A correction layer (seed_location set) is COMPUTED, not
            # drawn: it is re-derived from its target on every regen so
            # changing the target in the studio takes effect. Fontra
            # edits on such a layer are therefore overwritten — drop the
            # target to hand-draw it.
            if seed_location is not None:
                preserved_is_edit = False
            # A drawing captured into the sidecar (model α) outranks both the
            # prior shadow and the seed: it is the portable copy, so restoring
            # a sidecar into an empty workspace reproduces the drawn outlines.
            restored = _outline_to_layer_data(stored_outline) if seed_location is None else None
            if restored is not None:
                layer_paths = restored["paths"]
                layer_components = restored["components"]
                layer_anchors = restored["anchors"]
                layer_width = restored["width"]
            elif preserved_is_edit:
                layer_paths = preserved["paths"]
                layer_components = preserved["components"]
                layer_anchors = preserved["anchors"]
                layer_width = preserved["width"]
            else:
                interp = _interpolated_seed(glyph, seed_location or location)
                if interp is not None:
                    layer_paths, layer_width = interp
                else:
                    # Fallback: masters not outline-compatible for this
                    # glyph — copy the default master.
                    layer_paths = list(default_layer.paths) if default_layer.paths else []
                    layer_width = default_layer.width
                layer_components = (
                    list(default_layer.components) if default_layer.components else []
                )
                layer_anchors = (
                    list(default_layer.anchors) if default_layer.anchors else []
                )

            brace = GSLayer()
            brace.associatedMasterId = default_master_id
            brace.attributes = {"coordinates": location}
            brace.paths = layer_paths
            brace.components = layer_components
            brace.anchors = layer_anchors
            brace.width = layer_width
            brace.name = "{" + ", ".join(_fmt_coord(v) for v in location) + "}"

            # Fontra sidebar label: the parametric corner this view
            # edits + the control-axis value ("XTRA3330-XOPQ2-YOPQ2 ·
            # crbr 20"). fontra-glyphs prefers
            # userData["xyz.fontra.source-name"] over the positional
            # "{...}" name; the "{...}" name stays for glyphsLib brace
            # recognition.
            # Stamp what we seeded. capture_outlines compares the layer's
            # current geometry against this to tell a hand-drawn edit from an
            # untouched seed — without it, capturing would freeze seeds into
            # the sidecar and stop them re-interpolating when masters change.
            brace.userData["xyz.avar2studio.seed-sig"] = _geometry_sig(
                layer_paths, layer_width
            )

            source_label = _brace_source_label(location)
            if source_label and seed_location is not None:
                # Name the correction so the Fontra source list says what
                # this computed view is: "… · crbr 100 → as if XOPQ 1100".
                as_if = ", ".join(
                    f"{font.axes[i].axisTag}{_fmt_coord(v)}"
                    for i, v in enumerate(seed_location)
                    if i < len(location) and float(v) != float(location[i])
                )
                if as_if:
                    source_label = f"{source_label} → as if {as_if}"
            if source_label:
                brace.userData["xyz.fontra.source-name"] = source_label

            glyph.layers.append(brace)

    from .source_font import save_font_atomically
    save_font_atomically(font, shadow_path)
    return shadow_path


def _paths_sig(paths):
    """A comparable signature of an outline's node positions — used to
    tell an unedited seed (identical to the default master) from a
    genuinely drawn outline."""
    return tuple(
        (round(float(n.position.x), 2), round(float(n.position.y), 2))
        for p in (paths or [])
        for n in (getattr(p, "nodes", None) or [])
    )


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


def _studio_ufo_slug(pins: Dict[str, float]) -> str:
    """Deterministic filesystem-safe slug for a pooled studio UFO,
    derived from the pinned (sparse) location: ``{"crbr": -100,
    "XOPQ": 78}`` → ``XOPQ78-crbrn100``. ``-`` → ``n``, ``.`` → ``p``
    so the location round-trips through a directory name."""
    parts = []
    for tag in sorted(pins, key=str):
        val = f"{float(pins[tag]):g}".replace("-", "n").replace(".", "p")
        parts.append(f"{tag}{val}")
    return "-".join(parts) or "default"


def _regenerate_shadow_designspace(
    original_path: Path, axes_to_add: List[Dict]
) -> Optional[Path]:
    """Designspace twin of the .glyphs shadow: derive
    ``.avar2-studio/shadow/<basename>.designspace`` from
    ``original + sidecar``.

    Where .glyphs braces are layers inside one file, the designspace
    equivalent is a POOLED SPARSE UFO per unique brace location —
    one UFO holding every applicable glyph pinned at that location,
    attached as an extra ``<source>``. Pooled UFOs are named
    ``<stem>-studio-<slug>.ufo`` and are wholly studio-owned, which
    makes outline preservation trivial: a pooled UFO that still
    matches a sidecar location is KEPT as-is across regenerations
    (drawn outlines and all); only missing glyphs are seeded into it
    and stale pools are deleted. The original designspace + its UFOs
    are re-copied fresh every time, exactly like the .glyphs path.

    Seeds prefer the glyph's natural (interpolated) shape at the
    brace's parametric location via fontmake's Instantiator, falling
    back to a copy of the default source's glyph.
    """
    from fontTools.designspaceLib import (
        AxisDescriptor,
        DesignSpaceDocument,
        SourceDescriptor,
    )
    import ufoLib2

    shadow_dir = shadow_dir_for(original_path)
    shadow_path = shadow_path_for(original_path)
    shadow_dir.mkdir(parents=True, exist_ok=True)
    stem = original_path.stem
    studio_prefix = f"{stem}-studio-"

    doc = DesignSpaceDocument.fromfile(str(original_path))
    base_dir = original_path.parent

    # ---- 1. Fresh copies of the original's source UFOs -------------
    for src in doc.sources:
        src_path = Path(src.path) if src.path else base_dir / (src.filename or "")
        if not src_path.exists():
            raise FileNotFoundError(f"designspace source missing: {src_path}")
        dst = shadow_dir / src_path.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src_path, dst)
        src.path = str(dst)
        src.filename = src_path.name

    # ---- 2. Append sidecar axes --------------------------------------
    existing_tags = {str(a.tag or "").lower() for a in doc.axes}
    control_axes: List[Dict] = []
    for spec in axes_to_add:
        tag = (spec.get("tag") or "").strip().lower()
        if not tag:
            continue
        entry = {
            "tag": tag,
            "name": spec.get("display_name") or tag,
            "default": float(spec.get("default") or 0),
            "layers": list(spec.get("layers") or []),
        }
        control_axes.append(entry)
        if tag in existing_tags:
            continue
        ax = AxisDescriptor()
        ax.tag = tag
        ax.name = entry["name"]
        lo = float(spec.get("min") or 0)
        hi = float(spec.get("max") or 0)
        ax.minimum = min(lo, hi, entry["default"])
        ax.maximum = max(lo, hi, entry["default"])
        ax.default = entry["default"]
        doc.addAxis(ax)
        existing_tags.add(tag)

    axis_name_by_tag = {str(a.tag or "").lower(): a.name for a in doc.axes}
    axis_default_by_name = {a.name: float(a.default or 0) for a in doc.axes}

    # Every existing source sits at the new axes' defaults.
    for src in doc.sources:
        loc = dict(src.location or {})
        for name, dflt in axis_default_by_name.items():
            loc.setdefault(name, dflt)
        src.location = loc

    default_src = doc.findDefault() or (doc.sources[0] if doc.sources else None)
    if default_src is None:
        raise ValueError("designspace has no sources")
    default_ufo = ufoLib2.Font.open(default_src.path)

    # ---- 3. Desired pools: unique pinned location → glyph set -------
    pools: Dict[tuple, Dict] = {}
    for entry in control_axes:
        for layer in entry["layers"]:
            glyph_name = layer.get("glyph")
            pinned_raw = layer.get("location") or {}
            if not glyph_name or not pinned_raw:
                continue
            pinned = {}
            for pin_tag, pin_val in pinned_raw.items():
                name = axis_name_by_tag.get(str(pin_tag).lower())
                if name is None:
                    continue
                try:
                    pinned[str(pin_tag).lower()] = float(pin_val)
                except (TypeError, ValueError):
                    continue
            if not pinned:
                continue
            key = tuple(sorted(pinned.items()))
            pool = pools.setdefault(key, {"pins": pinned, "glyphs": set()})
            pool["glyphs"].add(glyph_name)

    # Parametric (non-control) part of each pool location, for
    # natural-shape seeding. Control tags aren't in the ORIGINAL doc,
    # so the Instantiator location holds parametric pins only.
    control_tags = {e["tag"] for e in control_axes}
    generator = None
    seed_cache: Dict[tuple, object] = {}

    def _natural_seed_font(pins: Dict[str, float]):
        """UFO interpolated at the pool's parametric sub-location, or
        None when the pool sits at the parametric default / the
        Instantiator can't run (falls back to default-source copy)."""
        nonlocal generator
        param_pins = {
            axis_name_by_tag[t]: v for t, v in pins.items()
            if t not in control_tags and t in axis_name_by_tag
        }
        if not param_pins:
            return None
        cache_key = tuple(sorted(param_pins.items()))
        if cache_key in seed_cache:
            return seed_cache[cache_key]
        result = None
        try:
            from fontmake.instantiator import Instantiator
            from fontTools.designspaceLib import InstanceDescriptor

            if generator is None:
                orig_doc = DesignSpaceDocument.fromfile(str(original_path))
                orig_doc.loadSourceFonts(ufoLib2.Font.open)
                generator = Instantiator.from_designspace(
                    orig_doc, round_geometry=True
                )
            inst = InstanceDescriptor()
            inst.styleName = "StudioSeed"
            loc = {
                a.name: float(a.default or 0)
                for a in doc.axes
                if str(a.tag or "").lower() not in control_tags
            }
            loc.update(param_pins)
            inst.location = loc
            result = generator.generate_instance(inst)
        except Exception as exc:  # non-interpolatable → default copy
            print(f"  [control-axes] natural seed failed ({exc}); "
                  f"seeding from default source")
        seed_cache[cache_key] = result
        return result

    # ---- 4. Create / reconcile pooled UFOs + sources ----------------
    wanted_ufo_names = set()
    for key, pool in sorted(pools.items()):
        pins = pool["pins"]
        ufo_name = f"{studio_prefix}{_studio_ufo_slug(pins)}.ufo"
        wanted_ufo_names.add(ufo_name)
        ufo_path = shadow_dir / ufo_name

        label = ", ".join(f"{t} {v:g}" for t, v in sorted(pins.items()))

        if ufo_path.exists():
            pooled = ufoLib2.Font.open(ufo_path)
        else:
            pooled = ufoLib2.Font()
            for attr in (
                "unitsPerEm", "ascender", "descender", "xHeight",
                "capHeight", "italicAngle", "familyName",
            ):
                val = getattr(default_ufo.info, attr, None)
                if val is not None:
                    setattr(pooled.info, attr, val)
            pooled.info.styleName = label

        pooled_layer = pooled.layers.defaultLayer
        seed_font = None
        for glyph_name in sorted(pool["glyphs"]):
            if glyph_name in pooled_layer:
                continue  # studio-owned: existing (possibly drawn) wins
            if seed_font is None:
                seed_font = _natural_seed_font(pins) or default_ufo
            if glyph_name not in seed_font:
                if glyph_name in default_ufo:
                    seed_font = default_ufo
                else:
                    print(f"  [control-axes] glyph '{glyph_name}' not in "
                          f"default source — skipping seed")
                    continue
            pooled_layer.insertGlyph(seed_font[glyph_name], name=glyph_name)
        # Reconcile: drop glyphs no longer covered at this location.
        for glyph_name in [g for g in pooled_layer.keys()
                           if g not in pool["glyphs"]]:
            del pooled_layer[glyph_name]
        pooled.save(ufo_path, overwrite=True)

        src = SourceDescriptor()
        src.filename = ufo_name
        src.path = str(ufo_path)
        src.familyName = getattr(default_src, "familyName", None)
        src.styleName = label
        src.name = label
        loc = dict(axis_default_by_name)
        for t, v in pins.items():
            name = axis_name_by_tag.get(t)
            if name is not None:
                loc[name] = v
        src.location = loc
        doc.addSource(src)

    # Stale pools from a previous sidecar shape.
    for stale in shadow_dir.glob(f"{studio_prefix}*.ufo"):
        if stale.name not in wanted_ufo_names:
            shutil.rmtree(stale, ignore_errors=True)

    doc.write(str(shadow_path))
    return shadow_path


def _geometry_sig(paths, width) -> str:
    """A short, stable signature of a layer's geometry, stored on the seeded
    brace layer so a later edit can be told apart from an untouched seed."""
    import hashlib

    parts = [f"{float(width or 0):.2f}"]
    for p in paths or []:
        for n in getattr(p, "nodes", None) or []:
            parts.append(f"{float(n.position.x):.2f},{float(n.position.y):.2f}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _parts_to_outline(src_paths, src_components, src_anchors, width) -> Dict:
    """Serialise brace-layer geometry to plain JSON for the sidecar.

    Deliberately a value dump rather than glif XML: it round-trips through
    ``json`` unchanged, so a config bundle carries the drawing verbatim.
    Takes the parts rather than a layer because ``_extract_brace_outlines``
    hands back exactly these lists.
    """
    paths = []
    for p in src_paths or []:
        paths.append({
            "closed": bool(getattr(p, "closed", True)),
            "nodes": [
                [float(n.position.x), float(n.position.y), str(n.type)]
                for n in (getattr(p, "nodes", None) or [])
            ],
        })
    components = []
    for c in src_components or []:
        try:
            xform = [float(v) for v in c.transform.value]
        except Exception:
            xform = None
        entry = {"name": getattr(c, "name", None)}
        if xform:
            entry["transform"] = xform
        components.append(entry)
    anchors = [
        {"name": a.name, "x": float(a.position.x), "y": float(a.position.y)}
        for a in (src_anchors or [])
    ]
    return {
        "width": float(width or 0),
        "paths": paths,
        "components": components,
        "anchors": anchors,
    }


def _outline_to_layer_data(outline) -> Optional[Dict[str, object]]:
    """Inverse of ``_layer_to_outline``: rebuild glyphsLib objects, in the
    shape regenerate_shadow's seeding block expects. Returns None for a
    missing or malformed entry so the caller falls back to seeding."""
    if not isinstance(outline, dict) or outline.get("paths") is None:
        return None
    try:
        from glyphsLib.classes import GSAnchor, GSComponent, GSNode, GSPath
        from glyphsLib.types import Transform

        paths = []
        for p in outline.get("paths") or []:
            gp = GSPath()
            gp.closed = bool(p.get("closed", True))
            for node in p.get("nodes") or []:
                x, y, ntype = node[0], node[1], (node[2] if len(node) > 2 else "line")
                gp.nodes.append(GSNode((float(x), float(y)), ntype))
            paths.append(gp)
        components = []
        for c in outline.get("components") or []:
            if not c.get("name"):
                continue
            gc = GSComponent(c["name"])
            if c.get("transform"):
                gc.transform = Transform(*[float(v) for v in c["transform"]])
            components.append(gc)
        anchors = [
            GSAnchor(a.get("name"), (float(a.get("x", 0)), float(a.get("y", 0))))
            for a in (outline.get("anchors") or [])
        ]
        return {
            "paths": paths,
            "components": components,
            "anchors": anchors,
            "width": float(outline.get("width", 0) or 0),
        }
    except Exception as exc:
        print(f"Warning: could not restore a stored outline: {exc}", file=sys.stderr)
        return None


def capture_outlines(source_path: Path) -> int:
    """Copy hand-drawn brace outlines out of the shadow and INTO the sidecar.

    This is what makes ``-control.json`` (and any config bundle built from it) a
    real backup: without it the sidecar holds only layer locations and every
    drawing lives solely in ``.avar2-studio/shadow/``, which is derived and gets
    wiped. Layers that still match their computed seed are left alone so the
    sidecar doesn't fill with no-ops, and correction layers (those carrying a
    ``target``) are skipped because they are recomputed on every regeneration.

    Returns the number of layers captured.
    """
    shadow = shadow_path_for(source_path)
    if not shadow.exists():
        return 0
    drawn = _extract_brace_outlines(shadow)
    if not drawn:
        return 0

    from glyphsLib import GSFont

    try:
        shadow_font = GSFont(str(shadow))
    except Exception as exc:
        print(f"Warning: could not read the shadow to capture outlines: {exc}", file=sys.stderr)
        return 0
    axis_tags = [str(a.axisTag) for a in shadow_font.axes]
    # A sidecar location is SPARSE. regenerate_shadow fills the gaps with the
    # default master's coordinates, so the brace layer's stored coordinates —
    # and therefore the extractor's key — use those, not zeros.
    default_axes = list(getattr(shadow_font.masters[0], "axes", None) or []) if shadow_font.masters else []
    defaults = {
        tag: float(default_axes[i]) if i < len(default_axes) else 0.0
        for i, tag in enumerate(axis_tags)
    }

    data = load(source_path)
    captured = 0
    for ax in data.get("axes") or []:
        for entry in ax.get("layers") or []:
            if entry.get("target"):
                continue  # computed, not drawn
            loc = entry.get("location") or {}
            # Match case-insensitively: sidecar tags are stored as the designer
            # typed them, the shadow's axis tags are canonical.
            lower = {str(k).lower(): v for k, v in loc.items()}
            key_loc = tuple(
                float(lower.get(tag.lower(), defaults[tag])) for tag in axis_tags
            )
            found = drawn.get((entry.get("glyph"), key_loc))
            if not found:
                continue
            # Untouched seeds still carry the signature they were stamped with;
            # skip those so the sidecar only ever holds real drawing.
            seed_sig = found.get("seed_sig")
            if seed_sig and seed_sig == _geometry_sig(found.get("paths"), found.get("width")):
                continue
            serialised = _parts_to_outline(
                found.get("paths"), found.get("components"),
                found.get("anchors"), found.get("width"),
            )
            if entry.get("outline") == serialised:
                continue
            entry["outline"] = serialised
            captured += 1
    if captured:
        _save(source_path, data)
    return captured


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
                # Stamped when the layer was seeded; lets capture_outlines tell
                # an untouched seed from a hand-drawn edit.
                "seed_sig": (getattr(layer, "userData", None) or {}).get(
                    "xyz.avar2studio.seed-sig"
                ),
            }
    return out


# sys is referenced in _extract_brace_outlines via print(file=sys.stderr).
# Imported here rather than at module top to keep the rest of the
# module unaware of stderr plumbing.
import sys
