"""Kinetikern sidecar — ``<basename>-kinetikern.json`` next to the source.

Per corrected instance: the looseness it was spaced at and, per glyph, how
far each side moved relative to what the masters interpolate to at that
instance (font units, whole numbers). Deltas, not absolute values, so a later
recalibration of the masters carries the corrections along::

    {
      "version": 1,
      "enabled": true,
      "instances": [
        {"name": "Narrow 400 MinOpsz", "looseness": 0.0,
         "deltas": {"H": [83, 83], "n": [65, 66]}}
      ]
    }

An entry may carry ``location`` ({XTRA, XOPQ, YOPQ}) for a spot that is not
a named instance; otherwise the bake resolves the instance's current
parametric coordinates, so the correction follows the instance if it moves.
Turning a declaration into brace layers is ``bake.py``.
"""

from __future__ import annotations

from .. import sidecar_io


def _locked(fn):
    """Load → mutate → save under the shared sidecar lock (see sidecar_io)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with sidecar_io.LOCK:
            return fn(*args, **kwargs)
    return wrapper

import functools
import json
from pathlib import Path
from typing import Dict, List, Optional

_SCHEMA_VERSION = 1


def sidecar_path_for(source_path: Path) -> Path:
    return source_path.parent / f"{source_path.stem}-kinetikern.json"


def _empty() -> Dict:
    return {"version": _SCHEMA_VERSION, "enabled": True, "instances": []}


def _normalise(data: Dict) -> Dict:
    out = _empty()
    out["enabled"] = bool(data.get("enabled", True))
    for raw in data.get("instances", []) or []:
        if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
            continue
        deltas = {}
        for glyph, pair in (raw.get("deltas") or {}).items():
            try:
                dl, dr = int(round(float(pair[0]))), int(round(float(pair[1])))
            except (TypeError, ValueError, IndexError):
                continue
            if dl or dr:
                deltas[str(glyph)] = [dl, dr]
        entry = {"name": str(raw["name"]).strip(), "looseness": float(raw.get("looseness", 0.0)), "deltas": deltas}
        loc = raw.get("location")
        if isinstance(loc, dict) and loc:
            entry["location"] = {str(k).upper(): float(v) for k, v in loc.items()}
        out["instances"].append(entry)
    return out


def load(source_path: Path) -> Dict:
    sidecar = sidecar_path_for(source_path)
    if not sidecar.exists():
        return _empty()
    try:
        with sidecar.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty()
    return _normalise(data) if isinstance(data, dict) else _empty()


def _save(source_path: Path, data: Dict) -> None:
    sidecar = sidecar_path_for(source_path)
    sidecar_io.write_json(sidecar, _normalise(data))


def entries(source_path: Path) -> List[Dict]:
    """The corrections to bake: every entry with a delta, or none when the
    toggle is off."""
    data = load(source_path)
    if not data.get("enabled"):
        return []
    return [e for e in data["instances"] if e["deltas"]]


def find(source_path: Path, name: str) -> Optional[Dict]:
    for entry in load(source_path)["instances"]:
        if entry["name"] == name:
            return entry
    return None


@_locked
def set_enabled(source_path: Path, enabled: bool) -> Dict:
    data = load(source_path)
    data["enabled"] = bool(enabled)
    _save(source_path, data)
    return data


@_locked
def set_entry(source_path: Path, name: str, looseness: float, deltas: Dict[str, List[int]],
              location: Optional[Dict[str, float]] = None) -> Dict:
    """Store (or replace) one instance's correction. Returns the stored entry."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("instance name is required")
    data = load(source_path)
    entry = {"name": name, "looseness": float(looseness), "deltas": dict(deltas)}
    if location:
        entry["location"] = dict(location)
    data["instances"] = [e for e in data["instances"] if e["name"] != name] + [entry]
    _save(source_path, data)
    return find(source_path, name)


@_locked
def remove_entry(source_path: Path, name: str) -> bool:
    data = load(source_path)
    kept = [e for e in data["instances"] if e["name"] != name]
    if len(kept) == len(data["instances"]):
        return False
    data["instances"] = kept
    _save(source_path, data)
    return True


@_locked
def rename_instance(source_path: Path, old_name: str, new_name: str) -> bool:
    data = load(source_path)
    changed = False
    for entry in data["instances"]:
        if entry["name"] == old_name:
            entry["name"] = new_name
            changed = True
    if changed:
        _save(source_path, data)
    return changed
