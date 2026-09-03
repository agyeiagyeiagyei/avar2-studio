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

# --- grade model: PURE WEIGHT (one grade% knob) ---------------------------
# The grade adds WEIGHT: XOPQ (stems) + YOPQ (horizontals) are the driver, and
# XTRA (counters) follows to hold the width — so it reads as a bolder weight,
# not a condense. grade% is the stem-weight change; COMP_RATIO is the counter
# tightening per unit of stem thickening needed to hold advance (~2.0, stable
# across weights — 1 unit of XOPQ widens ~2 units, so XTRA reclaims ~2). YOPQ
# tracks the stem weight 1:1. Advance is held EXACTLY by per-glyph equalisation
# downstream; XTRA just keeps the equalisation trims small, so these are
# darkness-character choices, not advance ones.
#   dXOPQ = grade% * XOPQ            (stem weight, driver)
#   dYOPQ = grade% * YOPQ            (horizontal weight, driver)
#   dXTRA = COMP_RATIO * dXOPQ       (counters follow to hold width)
# Because XTRA scales with the STEM move (small on light styles), the grade
# stays weight-led across the range instead of leading with counter-tightening
# on the light end.
K_YOPQ = 1.0
COMP_RATIO = 2.0
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


def intensity(source_path: Path) -> float:
    """Global multiplier on EVERY instance's grade%.

    One knob to ratchet the whole axis up or down without re-tuning each
    instance: the built grade is ``pct * intensity``. 1.0 is "as authored".
    """
    return float(load(source_path).get("intensity", 1.0))


def clamp_to_headroom(source_path: Path) -> bool:
    """Whether the stem move is limited to the counter headroom available.

    On (default), a grade never thickens where the counters cannot open to
    absorb it. Off restores the older behaviour, where the stems move by the
    full grade% and any shortfall lands in the counters.
    """
    return bool(load(source_path).get("clamp_to_headroom", True))


def effective_pct(pct: float, strength: float) -> float:
    """The grade% actually built: the authored value scaled by ``intensity``."""
    try:
        return max(0.0, float(pct)) * max(0.0, float(strength))
    except (TypeError, ValueError):
        return 0.0


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


def set_intensity(source_path: Path, value: float) -> Dict:
    """Set the global grade multiplier. 0 disables every grade without
    forgetting the authored per-instance values."""
    data = load(source_path)
    data["intensity"] = _validate_pct(value)
    _save(source_path, data)
    return data


def set_clamp_to_headroom(source_path: Path, value: bool) -> Dict:
    data = load(source_path)
    data["clamp_to_headroom"] = bool(value)
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
    clamp_to_headroom: bool = True,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Derive (light, dark) parametric coords for a grade at ``base``.

    ``base`` and the returned dicts are keyed by parametric tag
    (XTRA/XOPQ/YOPQ). ``param_ranges`` maps tag -> (min, max). Dark = more
    weight (higher XOPQ/YOPQ, lower XTRA); light = the inverse.

    The stem move (the driver) is limited to what BOTH the driver's own range
    and the follower's headroom can absorb, then the horizontals are scaled by
    the fraction actually achieved. Two things fall out of that:

    * An instance already at the XOPQ edge darkens by nothing, and its counters
      and bars stay put — otherwise the "dark" brace is a pure condense inside
      a held advance and reads as deformed spacing rather than weight.
    * An instance pinned at the XTRA floor (the widest/heaviest corners, where
      counters are already as tight as the design allows) has NO room to open
      counters, so with ``clamp_to_headroom`` it does not thicken either.
      Without the cap every added stem unit lands in the counters and the grade
      bleeds shut. Callers that prefer the uncompensated look — advance is held
      exactly by per-glyph equalisation downstream regardless — pass False.

    (Mirrored in the wasm port, braces.rs.)
    """
    x, o, y = base.get("XTRA", 0.0), base.get("XOPQ", 0.0), base.get("YOPQ", 0.0)
    dO_half = pct * o / 2.0            # requested stem half-move (driver)
    dY_half = pct * K_YOPQ * y / 2.0   # requested horizontal half-move

    def rng(tag):
        return param_ranges.get(tag, (float("-inf"), float("inf")))

    def clamp(tag, v):
        lo, hi = rng(tag)
        return max(lo, min(hi, v))

    lo_o, hi_o = rng("XOPQ")
    lo_x, hi_x = rng("XTRA")

    # Room for the driver, in driver units, on each side.
    dark_room = max(0.0, hi_o - o)
    light_room = max(0.0, o - lo_o)
    if clamp_to_headroom and COMP_RATIO > 0:
        # Darkening spends XTRA downward, lightening spends it upward.
        dark_room = min(dark_room, max(0.0, (x - lo_x) / COMP_RATIO))
        light_room = min(light_room, max(0.0, (hi_x - x) / COMP_RATIO))

    dark_dO = min(dO_half, dark_room)
    light_dO = min(dO_half, light_room)

    # Horizontals track the ACHIEVED stem move, so the grade stays weight-led:
    # if the stems cannot thicken, the bars must not thicken on their own.
    s_dark = (dark_dO / dO_half) if dO_half > 0 else 0.0
    s_light = (light_dO / dO_half) if dO_half > 0 else 0.0

    light = {
        "XTRA": clamp("XTRA", x + COMP_RATIO * light_dO),
        "XOPQ": clamp("XOPQ", o - light_dO),
        "YOPQ": clamp("YOPQ", y - s_light * dY_half),
    }
    dark = {
        "XTRA": clamp("XTRA", x - COMP_RATIO * dark_dO),
        "XOPQ": clamp("XOPQ", o + dark_dO),
        "YOPQ": clamp("YOPQ", y + s_dark * dY_half),
    }
    return light, dark


def max_pct_for(base: Dict[str, float], param_ranges: Dict[str, Tuple[float, float]]) -> float:
    """Largest grade% before any axis would clamp at ``base`` — the value the
    UI uses to bound the slider so a grade the parametric space can't deliver
    is simply unreachable. Returns a generous cap (2.0) when nothing binds.

    A cap of exactly 0 is a REAL answer, not a missing one: an instance sitting
    on the XTRA floor has no counter headroom, so no grade% is deliverable
    there. Filtering it out (``c > 0``) silently advertised the next-loosest
    axis's cap instead, which is how the widest/heaviest instances came to
    offer grades that could only bleed into the counters.
    """
    o = base.get("XOPQ")
    if o is None or o <= 0:
        return 2.0
    caps: List[float] = []

    def bound(tag: str, v: float, half_per_pct: float) -> None:
        # half-move at a given pct = half_per_pct * pct; keep v in [lo, hi].
        lo, hi = param_ranges.get(tag, (float("-inf"), float("inf")))
        if half_per_pct <= 0:
            return
        if lo > float("-inf"):
            caps.append((v - lo) / half_per_pct)
        if hi < float("inf"):
            caps.append((hi - v) / half_per_pct)

    # XOPQ (driver): half-move = pct*o/2
    bound("XOPQ", o, o / 2.0)
    # YOPQ (driver): half-move = pct*K_YOPQ*y/2
    y = base.get("YOPQ")
    if y and y > 0:
        bound("YOPQ", y, K_YOPQ * y / 2.0)
    # XTRA (follower): half-move = COMP_RATIO*pct*o/2 — scales with XOPQ, not XTRA
    x = base.get("XTRA")
    if x and x > 0:
        bound("XTRA", x, COMP_RATIO * o / 2.0)
    return min([c for c in caps if c >= 0] + [2.0])


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _empty() -> Dict:
    return {
        "version": _SCHEMA_VERSION,
        "enabled": False,
        "default_pct": 0.25,
        "intensity": 1.0,
        "clamp_to_headroom": True,
        "instances": [],
    }


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
    try:
        strength = float(data.get("intensity", 1.0))
    except (TypeError, ValueError):
        strength = 1.0
    return {
        "version": data.get("version") or _SCHEMA_VERSION,
        "enabled": bool(data.get("enabled", False)),
        "default_pct": max(0.0, dflt),
        "intensity": max(0.0, strength),
        "clamp_to_headroom": bool(data.get("clamp_to_headroom", True)),
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
