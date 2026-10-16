"""Control-axis sidecar: schema, I/O and validation (split from the
``control_axes`` module, audit §4) — see docs/SIDECARS.md.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Dict, List, Optional

from .. import sidecar_io


def _locked(fn):
    """Load → mutate → save under the shared sidecar lock (see sidecar_io)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with sidecar_io.LOCK:
            return fn(*args, **kwargs)
    return wrapper


_SCHEMA_VERSION = 1

# Keys this module understands. Anything else in a sidecar is carried
# through load → save untouched, so a file written by a newer studio (or
# a sibling tool) is never silently stripped by an older one.
_KNOWN_TOP_KEYS = {"version", "axes", "instance_values"}
_KNOWN_AXIS_KEYS = {"tag", "display_name", "default", "min", "max", "layers",
                    "coverage", "extra_locations"}
_KNOWN_LAYER_KEYS = {"glyph", "location", "target", "outline", "source_sig"}


class SidecarError(ValueError):
    """The control sidecar cannot be written in its current state."""


class SidecarCorruptError(SidecarError):
    """The file on disk failed to parse: refuse to overwrite it, the user's
    layers may still be recoverable from it."""


class SidecarVersionError(SidecarError):
    """The file carries a schema version newer than this studio knows."""


# {sidecar path: parse error} for files that failed to load; readers get
# an empty schema (the studio keeps working), writers refuse until the
# file is repaired or removed. Surfaced by /api/health as sidecar_errors.
_LOAD_ERRORS: Dict[str, str] = {}


def load_errors() -> Dict[str, str]:
    return dict(_LOAD_ERRORS)


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
    key = str(sidecar)
    if not sidecar.exists():
        _LOAD_ERRORS.pop(key, None)
        return _empty()
    try:
        with sidecar.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _LOAD_ERRORS[key] = str(exc)
        return _empty()
    if not isinstance(data, dict):
        _LOAD_ERRORS[key] = "top level is not a JSON object"
        return _empty()
    _LOAD_ERRORS.pop(key, None)
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


def instance_values(source_path: Path) -> Dict[str, Dict[str, float]]:
    """Per-instance control-axis values, ``{tag: {instance name: value}}``.
    The studio's per-row ymod/lcwd authoring — the control-axis analogue
    of the CSV's SPAC column — never a CSV column itself (see
    docs/decisions/0008-per-instance-control-values-in-sidecar.md)."""
    return dict(load(source_path).get("instance_values") or {})


_INSTANCE_VALUES_LOCK = sidecar_io.LOCK  # one lock for every sidecar writer


def set_instance_values(source_path: Path, instance_name: str,
                        values: Dict[str, float]) -> bool:
    """Record ``{tag: value}`` for one instance. Values are clamped to
    the axis range; a value at the axis default clears the entry (the
    default is the absence). Returns True when the sidecar changed."""
    if not instance_name or not values:
        return False
    with _INSTANCE_VALUES_LOCK:
        data = load(source_path)
        axes_by_tag = {str(a.get("tag", "")).lower(): a for a in data.get("axes", [])}
        store = data.setdefault("instance_values", {})
        changed = False
        for tag, value in values.items():
            ax = axes_by_tag.get(str(tag).strip().lower())
            if ax is None:
                continue
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            v = min(float(ax.get("max", v)), max(float(ax.get("min", v)), v))
            key = str(ax.get("tag"))
            per = store.setdefault(key, {})
            if abs(v - float(ax.get("default", 0))) < 1e-9:
                if instance_name in per:
                    del per[instance_name]
                    changed = True
            elif per.get(instance_name) != v:
                per[instance_name] = v
                changed = True
        if changed:
            _save(source_path, _normalise(data))
        return changed


def rename_instance_values(source_path: Path, old_name: str, new_name: str) -> bool:
    """Carry per-instance values across an instance rename."""
    with _INSTANCE_VALUES_LOCK:
        data = load(source_path)
        changed = False
        for per in (data.get("instance_values") or {}).values():
            if old_name in per:
                per[new_name] = per.pop(old_name)
                changed = True
        if changed:
            _save(source_path, _normalise(data))
        return changed


def remove_instance_values(source_path: Path, instance_name: str) -> bool:
    """Drop every per-instance value for a deleted instance."""
    with _INSTANCE_VALUES_LOCK:
        data = load(source_path)
        changed = False
        for per in (data.get("instance_values") or {}).values():
            if instance_name in per:
                del per[instance_name]
                changed = True
        if changed:
            _save(source_path, _normalise(data))
        return changed


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------


@_locked
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


@_locked
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
        raise ValueError("default, min, and max must be numeric") from None
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


@_locked
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
            raise ValueError(f"{label} must be numeric") from None

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
        # Location keys carry the AUTHORED tag case; match case-insensitively
        # or the range check is silently skipped for uppercase tags.
        v = next((val for k, val in loc.items() if str(k).lower() == tag_norm),
                 None)
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


@_locked
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


@_locked
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


@_locked
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
    removed = [e for e in current if _layer_key(e) in remove_keys]
    added = _normalise_layers(add or [])
    # Carry a captured drawing across a replace (remove + re-add at new
    # coordinates): the desktop layers list is served WITHOUT outline/
    # source_sig (they are large and the endpoint is polled), so the edit
    # modal cannot carry them itself — without this, editing a drawn
    # layer's coordinates silently discards the drawing. One-to-one per
    # glyph, in request order; never onto a computed (target) entry.
    donors: Dict[str, List[Dict]] = {}
    for e in removed:
        if e.get("outline"):
            donors.setdefault(e["glyph"], []).append(e)
    for a in added:
        if a.get("outline") or a.get("target"):
            continue
        pool = donors.get(a["glyph"])
        if not pool:
            continue
        donor = pool.pop(0)
        a["outline"] = donor["outline"]
        if donor.get("source_sig"):
            a["source_sig"] = donor["source_sig"]
    merged = _normalise_layers(kept + added)
    target["layers"] = merged
    _save(source_path, data)
    return merged


@_locked
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
    return {"version": _SCHEMA_VERSION, "axes": [], "instance_values": {}}


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
        tag = (ax.get("tag") or "").strip()

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

        axis_entry = {
            "tag": ax.get("tag", ""),
            "display_name": ax.get("display_name", ax.get("tag", "")),
            "default": float(ax.get("default", 0)),
            "min": min_v,
            "max": max_v,
            "layers": layers,
        }
        for k, v in ax.items():
            if k not in _KNOWN_AXIS_KEYS:
                axis_entry.setdefault(k, v)
        out_axes.append(axis_entry)
    # Per-instance control-axis values: {tag: {instance name: value}}.
    # Carried through normalisation so layer edits (which round-trip the
    # whole sidecar) never wipe them.
    instance_values: Dict[str, Dict[str, float]] = {}
    raw_values = data.get("instance_values")
    if isinstance(raw_values, dict):
        for tag, per_name in raw_values.items():
            if not isinstance(per_name, dict) or not str(tag).strip():
                continue
            clean: Dict[str, float] = {}
            for name, v in per_name.items():
                if not isinstance(name, str) or not name.strip():
                    continue
                try:
                    clean[name] = float(v)
                except (TypeError, ValueError):
                    continue
            if clean:
                instance_values[str(tag).strip()] = clean
    out = {"version": data.get("version") or _SCHEMA_VERSION, "axes": out_axes,
           "instance_values": instance_values}
    for k, v in data.items():
        if k not in _KNOWN_TOP_KEYS:
            out.setdefault(k, v)
    return out


def _normalise_layers(raw) -> List[Dict]:
    """Validate + dedup the unified layers list. Each entry shape:
    ``{glyph: str, location: {axis_tag: number}, target?: {axis_tag: number}}``.
    Duplicates by (glyph, location) are folded — FIRST write wins (the
    sidecar's stored order is the authority; a dup later in the list is
    the stale copy).

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
            sig = entry.get("source_sig")
            if isinstance(sig, str) and sig:
                item["source_sig"] = sig
        for k, v in entry.items():
            if k not in _KNOWN_LAYER_KEYS:
                item.setdefault(k, v)
        out.append(item)
    return out


def _save(source_path: Path, data: Dict) -> None:
    sidecar = sidecar_path_for(source_path)
    err = _LOAD_ERRORS.get(str(sidecar))
    if err and sidecar.exists():
        raise SidecarCorruptError(
            f"{sidecar.name} could not be read ({err}); the studio will not overwrite it "
            f"— repair or move the file, then retry")
    try:
        version = int(data.get("version") or _SCHEMA_VERSION)
    except (TypeError, ValueError):
        version = _SCHEMA_VERSION
    if version > _SCHEMA_VERSION:
        raise SidecarVersionError(
            f"{sidecar.name} was written by a newer studio (schema {version}, this one "
            f"knows {_SCHEMA_VERSION}); update the studio before editing it")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # Authored brace layers are the hardest data in the studio to
    # recreate — keep bounded timestamped copies before every rewrite.
    from .. import csv_io as _csv_io
    _csv_io.backup_sidecar(sidecar)
    # Atomic replace: concurrent request threads each rewrite the whole
    # file, and interleaved plain writes have torn it (valid JSON with
    # trailing garbage) — which load() reads as "no control axes at
    # all". Write-to-temp + rename keeps every reader on a whole file.
    sidecar_io.write_json(sidecar, data)


def _validate_tag(tag: str) -> str:
    """Enforce OpenType tag rules: exactly 4 characters, ASCII
    printable. Case is preserved: fvar tags are case-sensitive, and
    unregistered axes are conventionally uppercase (``XOPQ``) while
    registered ones are lowercase (``wght``). Duplicate checks stay
    case-insensitive so two axes can never differ by case alone.
    Returns the stripped tag.
    """
    if not tag:
        raise ValueError("tag is required")
    stripped = tag.strip()
    if len(stripped) != 4:
        raise ValueError(f"tag must be exactly 4 characters (got '{stripped}', {len(stripped)} chars)")
    if not all(33 <= ord(c) <= 126 for c in stripped):
        raise ValueError(f"tag must be ASCII-printable (got '{stripped}')")
    return stripped
