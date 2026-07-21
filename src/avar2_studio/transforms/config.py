"""Transforms config — per-project sidecar JSON I/O.

Which transforms are *enabled* (and their params) is per-project state,
stored in a committed sibling ``<basename>-transforms.json`` next to the
source — mirroring ``-avar.csv`` and ``-control.json``, so authored intent
survives reload / source-swap and versions with the font.

The *available* transforms (the registry) are discovered globally (built-ins
+ ``~/.avar2-studio/transforms/``); this module only tracks on/off + params.

Schema (versioned)::

    {
      "version": 1,
      "transforms": [
        {"type": "spac", "enabled": false, "params": {"min": -20, "max": 40}}
      ]
    }

``transforms`` is an ordered list; the build applies enabled entries
top-to-bottom, each consuming the prior output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

_SCHEMA_VERSION = 1


def _empty() -> Dict:
    return {"version": _SCHEMA_VERSION, "transforms": []}


def sidecar_path_for(source_path: Path) -> Path:
    return Path(source_path).parent / f"{Path(source_path).stem}-transforms.json"


def load(source_path: Path) -> Dict:
    """Return the sidecar contents, or an empty schema-shaped dict if it
    doesn't exist or is unreadable."""
    sidecar = sidecar_path_for(source_path)
    if not sidecar.exists():
        return _empty()
    try:
        with sidecar.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("transforms"), list):
        return _empty()
    return data


def entries(source_path: Path) -> List[Dict]:
    """The raw ordered list of ``{type, enabled, params}`` as stored."""
    out = []
    for e in load(source_path).get("transforms", []):
        if not isinstance(e, dict) or not e.get("type"):
            continue
        out.append({
            "type": str(e["type"]),
            "enabled": bool(e.get("enabled", False)),
            "params": dict(e.get("params") or {}),
        })
    return out


def save(source_path: Path, entries_list: List[Dict]) -> List[Dict]:
    """Persist the ordered transform entries; returns what was stored."""
    from .. import csv_io as _csv_io
    _csv_io.backup_sidecar(sidecar_path_for(source_path))
    cleaned = []
    for e in entries_list:
        if not isinstance(e, dict) or not e.get("type"):
            continue
        cleaned.append({
            "type": str(e["type"]),
            "enabled": bool(e.get("enabled", False)),
            "params": dict(e.get("params") or {}),
        })
    sidecar = sidecar_path_for(source_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("w", encoding="utf-8") as f:
        json.dump({"version": _SCHEMA_VERSION, "transforms": cleaned}, f, indent=2)
        f.write("\n")
    return cleaned
