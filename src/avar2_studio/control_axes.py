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
per-glyph object, and it stores no outline/glif data. ``location`` is
sparse (only pinned axes) and keyed by axis tag. Coverage is derived
from the unique glyph names in ``layers``; it is not stored. (Legacy
``coverage`` + ``extra_locations`` keys are migrated into ``layers``
on load by ``_normalise`` and never re-emitted.)

Outline storage is **model β, best-effort**, not the design doc's
model α: drawn brace outlines live only in the shadow ``.glyphs`` and
are preserved across regeneration by reading the previous shadow —
they are never captured into this sidecar. Wiping ``.avar2-studio/``
therefore loses drawn outlines (they re-seed as default-master
copies). Model α (sidecar-canonical outlines, full no-data-loss
regen) is future work. See docs/control-axes.md.
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
    ``{glyph: str, location: {axis_tag: number}}``. Duplicates by
    (glyph, location) are folded — last write wins."""
    out: List[Dict] = []
    seen: set = set()
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
        for k, v in location.items():
            if not isinstance(k, str) or not k.strip():
                continue
            try:
                clean_loc[k.strip()] = float(v)
            except (TypeError, ValueError):
                continue
        if not clean_loc:
            continue
        key = (glyph.strip(), tuple(sorted(clean_loc.items())))
        if key in seen:
            continue
        seen.add(key)
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
                seed_jobs.append((glyph_name, location, dict(pinned)))

        # De-duplicate seed jobs — same (glyph, location) added by
        # multiple paths only needs writing once.
        seen_jobs: set = set()
        for glyph_name, location, pinned in seed_jobs:
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
            if preserved_is_edit:
                layer_paths = preserved["paths"]
                layer_components = preserved["components"]
                layer_anchors = preserved["anchors"]
                layer_width = preserved["width"]
            else:
                interp = _interpolated_seed(glyph, location)
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
            source_label = _brace_source_label(location)
            if source_label:
                brace.userData["xyz.fontra.source-name"] = source_label

            glyph.layers.append(brace)

    font.save(str(shadow_path))
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
