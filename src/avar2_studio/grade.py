"""Grade transform — sidecar JSON I/O + the grade-coordinate model.

A **grade** darkens (or lightens) an instance without changing any advance
width, so text never reflows. Unlike a control axis — which the designer draws
by hand — a grade is *fully derived* from its base instance and a single
``grade%`` knob, so it lives as a small declaration and is recomputed whenever
its inputs change (the instance's coords, the masters, or grade%).

The declaration lives in a sibling JSON file, ``<basename>-grade.json``,
parallel to ``<basename>-control.json`` and ``<basename>-avar.csv``::

    {
      "version": 1,
      "enabled": true,          // the Transforms "Grade" toggle
      "default_pct": 0.25,      // global default applied to newly-graded instances
      "instances": [            // one entry per graded instance (sparse anchors)
        {"name": "Bold Condensed", "pct": 0.25}
      ]
    }

Only instances the designer explicitly grades appear in ``instances``; the GRAD
axis interpolates between them. The axis itself (tag ``GRAD``, −10/0/+10) only
materialises in the built font once at least one instance is graded — with none,
there are no brace layers/virtual masters and the axis would collapse.

This module owns the sidecar and the maths. Turning a declaration into brace
layers + virtual masters on the shadow ``.glyphs`` is ``grade_shadow.py`` (it
reuses the shadow/VM machinery from ``control_axes``); the maths here is shared
by both so the UI can preview coords without touching the source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


_SCHEMA_VERSION = 1

# --- OpenType GRAD axis (registered grade axis) ---------------------------
GRAD_TAG = "GRAD"
GRAD_NAME = "Grade"
GRAD_MIN, GRAD_DEFAULT, GRAD_MAX = -10.0, 0.0, 10.0

# --- grade model: one grade% knob drives all three parametric moves -------
# All three scale against the instance's OWN base value, so the move is
# inherently per-instance and never overshoots an axis at extreme aspect
# ratios (an XOPQ tied to dXTRA collapses on wide-light instances). K_XOPQ is
# calibrated so a Bold-Condensed grade matches the original ratio-based grade
# (0.372 * grade% * XOPQ == old 0.476 * grade% * XTRA there). YOPQ moves 1:1
# with grade% — cheap darkness, ~no advance cost. Advance is held exactly by
# per-glyph equalisation downstream, so these are darkness choices, not
# advance ones.
K_XOPQ = 0.372
K_YOPQ = 1.0
PARAM_TAGS = ("XTRA", "XOPQ", "YOPQ")


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------


def sidecar_path_for(source_path: Path) -> Path:
    """Return the conventional grade-sidecar path next to the source."""
    return source_path.parent / f"{source_path.stem}-grade.json"


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------


def load(source_path: Path) -> Dict:
    """Return the sidecar's contents, or an empty schema-shaped dict if it
    doesn't exist or is unreadable."""
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


def is_enabled(source_path: Path) -> bool:
    """Whether the Grade transform toggle is on."""
    return bool(load(source_path).get("enabled"))


def default_pct(source_path: Path) -> float:
    return float(load(source_path).get("default_pct", 0.25))


def list_graded_instances(source_path: Path) -> List[Dict]:
    """Return ``[{name, pct}]`` for every graded instance. Empty when the
    toggle is off, so callers can treat "off" and "no grades" identically."""
    data = load(source_path)
    if not data.get("enabled"):
        return []
    return list(data.get("instances", []))


def find_instance(source_path: Path, name: str) -> Optional[Dict]:
    for entry in load(source_path).get("instances", []):
        if entry.get("name") == name:
            return entry
    return None


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------


def set_enabled(source_path: Path, enabled: bool) -> Dict:
    """Flip the Grade transform toggle. Per-instance grades PERSIST when off —
    the toggle only gates whether the GRAD axis is built."""
    data = load(source_path)
    data["enabled"] = bool(enabled)
    _save(source_path, data)
    return data


def set_default_pct(source_path: Path, pct: float) -> Dict:
    data = load(source_path)
    data["default_pct"] = _validate_pct(pct)
    _save(source_path, data)
    return data


def set_instance_grade(source_path: Path, name: str, pct: Optional[float] = None) -> Dict:
    """Add or update a graded instance. ``pct=None`` uses the current global
    default. Returns the stored ``{name, pct}`` entry."""
    if not name or not str(name).strip():
        raise ValueError("instance name is required")
    name = str(name).strip()
    data = load(source_path)
    value = _validate_pct(data.get("default_pct", 0.25) if pct is None else pct)
    for entry in data["instances"]:
        if entry.get("name") == name:
            entry["pct"] = value
            _save(source_path, data)
            return entry
    entry = {"name": name, "pct": value}
    data["instances"].append(entry)
    _save(source_path, data)
    return entry


def remove_instance_grade(source_path: Path, name: str) -> bool:
    """Remove a graded instance. Returns True if a row was removed."""
    data = load(source_path)
    before = len(data["instances"])
    data["instances"] = [e for e in data["instances"] if e.get("name") != name]
    if len(data["instances"]) == before:
        return False
    _save(source_path, data)
    return True


def save_all(source_path: Path, data: Dict) -> Dict:
    """Replace the WHOLE grade sidecar with ``data`` (normalised first).
    Returns the stored payload. Used by config-bundle import; the per-field
    setters above stay the interactive API."""
    _save(source_path, data if isinstance(data, dict) else _empty())
    return load(source_path)


def rename_instance(source_path: Path, old_name: str, new_name: str) -> bool:
    """Keep a grade attached to its instance across a rename. Returns True if
    an entry was renamed."""
    data = load(source_path)
    changed = False
    for entry in data["instances"]:
        if entry.get("name") == old_name:
            entry["name"] = new_name
            changed = True
    if changed:
        _save(source_path, data)
    return changed


# --------------------------------------------------------------------------
# The grade-coordinate model (pure — shared by preview + shadow generation)
# --------------------------------------------------------------------------


def grade_coords(
    base: Dict[str, float],
    pct: float,
    param_ranges: Dict[str, Tuple[float, float]],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Derive (light, dark) parametric coords for a grade at ``base``.

    ``base`` and the returned dicts are keyed by parametric tag
    (XTRA/XOPQ/YOPQ). ``param_ranges`` maps tag → (min, max) for clamping so a
    grade never asks for an out-of-range coordinate. Light = higher XTRA /
    lower XOPQ / lower YOPQ; dark = the inverse.
    """
    x, o, y = base.get("XTRA", 0.0), base.get("XOPQ", 0.0), base.get("YOPQ", 0.0)
    dX = pct * x
    dO = pct * K_XOPQ * o
    dY = pct * K_YOPQ * y

    def clamp(tag, v):
        lo, hi = param_ranges.get(tag, (float("-inf"), float("inf")))
        return max(lo, min(hi, v))

    light = {
        "XTRA": clamp("XTRA", x + dX / 2),
        "XOPQ": clamp("XOPQ", o - dO / 2),
        "YOPQ": clamp("YOPQ", y - dY / 2),
    }
    dark = {
        "XTRA": clamp("XTRA", x - dX / 2),
        "XOPQ": clamp("XOPQ", o + dO / 2),
        "YOPQ": clamp("YOPQ", y + dY / 2),
    }
    return light, dark


def max_pct_for(base: Dict[str, float], param_ranges: Dict[str, Tuple[float, float]]) -> float:
    """Largest grade% before any axis would clamp at ``base`` — the value the
    UI uses to bound the slider so a grade the parametric space can't deliver
    is simply unreachable. Returns a generous cap (2.0) when nothing binds."""
    caps = []
    for tag, k in (("XTRA", 1.0), ("XOPQ", K_XOPQ), ("YOPQ", K_YOPQ)):
        v = base.get(tag)
        if v is None or v <= 0 or k <= 0:
            continue
        lo, hi = param_ranges.get(tag, (float("-inf"), float("inf")))
        # half-move = pct*k*v/2 must keep v within [lo, hi] on both sides
        if lo > float("-inf"):
            caps.append(2.0 * (v - lo) / (k * v))
        if hi < float("inf"):
            caps.append(2.0 * (hi - v) / (k * v))
    return min([c for c in caps if c > 0] + [2.0])


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _empty() -> Dict:
    return {"version": _SCHEMA_VERSION, "enabled": False, "default_pct": 0.25, "instances": []}


def _normalise(data: Dict) -> Dict:
    instances: List[Dict] = []
    seen = set()
    for entry in data.get("instances") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        if name in seen:
            continue
        seen.add(name)
        try:
            pct = float(entry.get("pct", 0.25))
        except (TypeError, ValueError):
            pct = 0.25
        instances.append({"name": name, "pct": max(0.0, pct)})
    try:
        dflt = float(data.get("default_pct", 0.25))
    except (TypeError, ValueError):
        dflt = 0.25
    return {
        "version": data.get("version") or _SCHEMA_VERSION,
        "enabled": bool(data.get("enabled", False)),
        "default_pct": max(0.0, dflt),
        "instances": instances,
    }


def _validate_pct(pct) -> float:
    try:
        v = float(pct)
    except (TypeError, ValueError):
        raise ValueError("grade% must be numeric")
    if v < 0:
        raise ValueError("grade% must be non-negative")
    return v


def _save(source_path: Path, data: Dict) -> None:
    sidecar = sidecar_path_for(source_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalise(data)
    payload["enabled"] = bool(data.get("enabled", payload["enabled"]))
    with sidecar.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
