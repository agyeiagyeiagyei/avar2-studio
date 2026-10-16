"""Brace-layer / corner naming helpers (split from the ``control_axes``
module, audit §4).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict

from ..studio_log import get_logger

log = get_logger(__name__)


def _non_parametric_indices(font, control_axis_indices) -> set:
    """The control axes plus the studio's other shadow-injected axes
    (GRAD from grade, ROND from rounding): none are parametric
    dimensions, so corner/instance naming must key locations on the
    SOURCE parametric axes alone. Without this, enabling grade appended
    GRAD to the shadow's axis list and silently un-named every corner —
    the CSV has no GRAD column (every instance-row lookup KeyErrored)
    and source instances' shorter axis vectors were skipped outright."""
    out = set(control_axis_indices)
    for i, ax in enumerate(getattr(font, "axes", None) or []):
        if str(getattr(ax, "axisTag", "")).upper() in ("GRAD", "ROND"):
            out.add(i)
    return out


def corner_name_maps(font, control_axis_indices, original_path=None):
    """Master-corner and instance names keyed by parametric coordinate tuple.

    The key is the location across the font's axes minus the control axes
    (and the other studio-injected axes — see ``_non_parametric_indices``),
    rounded to 0.1 — the add modal's pin granularity. Masters come from the
    font itself; instances from the font's own list plus the studio-only
    rows in the sibling ``-avar.csv`` (when ``original_path`` is given).
    Shared by the shadow labeler and the layers panel so both name a layer
    the same way.
    """
    control_axis_indices = _non_parametric_indices(font, control_axis_indices)
    param_indices = [
        i for i in range(len(getattr(font, "axes", None) or []))
        if i not in control_axis_indices
    ]

    def _key(values):
        return tuple(round(float(v), 1) for v in values)

    masters: Dict[tuple, str] = {}
    for master in getattr(font, "masters", None) or []:
        maxes = list(getattr(master, "axes", None) or [])
        if param_indices and len(maxes) <= max(param_indices):
            continue
        masters[_key(maxes[i] for i in param_indices)] = master.name

    instances: Dict[tuple, str] = {}
    for inst in getattr(font, "instances", None) or []:
        iaxes = list(getattr(inst, "axes", None) or [])
        if param_indices and len(iaxes) <= max(param_indices):
            continue
        instances.setdefault(_key(iaxes[i] for i in param_indices), inst.name)

    param_tags = [str(getattr(font.axes[i], "axisTag", "")) for i in param_indices]
    avar_csv = None
    if original_path is not None:
        avar_csv = Path(original_path).parent / f"{Path(original_path).stem}-avar.csv"
    if param_tags and avar_csv is not None and avar_csv.exists():
        try:
            with avar_csv.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    name = (row.get("Instance Name") or "").strip()
                    if not name:
                        continue
                    rowvals = {str(k).strip().lower(): v for k, v in row.items() if k}
                    try:
                        values = [float(rowvals[tag.lower()]) for tag in param_tags]
                    except (KeyError, TypeError, ValueError):
                        continue
                    instances.setdefault(_key(values), name)
        except Exception as exc:
            log.warning(f"Warning: could not read instance corners from {avar_csv}: {exc}")

    return masters, instances


def corner_name_resolver(font, control_axis_indices, original_path=None):
    """Resolve a sparse tag-keyed location (the sidecar's ``location``
    shape) to a master-corner or instance name — None when neither matches.
    Unpinned parametric axes take the default master's value, mirroring how
    regenerate_shadow expands the same sparse dict into a full vector."""
    masters, instances = corner_name_maps(font, control_axis_indices, original_path)
    control_axis_indices = _non_parametric_indices(font, control_axis_indices)
    axes = list(getattr(font, "axes", None) or [])
    param = [
        (i, str(getattr(ax, "axisTag", "")).lower())
        for i, ax in enumerate(axes)
        if i not in control_axis_indices
    ]
    master_list = list(getattr(font, "masters", None) or [])
    default_vec = list(getattr(master_list[0], "axes", None) or []) if master_list else []

    def resolve(location):
        loc = {str(k).lower(): v for k, v in (location or {}).items()}
        values = []
        for i, tag in param:
            v = loc.get(tag)
            if v is None:
                if i >= len(default_vec):
                    return None
                v = default_vec[i]
            try:
                values.append(round(float(v), 1))
            except (TypeError, ValueError):
                return None
        key = tuple(values)
        return masters.get(key) or instances.get(key)

    return resolve


def _fmt_coord(value: float) -> str:
    """Format a coordinate the way Glyphs.app's brace-layer-name
    convention does: integer when whole, else fixed precision."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"
