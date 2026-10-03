"""Persistent instance renames (studio-side aliases).

The studio never writes the user's original source, so renaming a
SOURCE-defined instance can only edit the shadow — and the shadow is
re-derived from the original on every regeneration, which used to wipe
the rename and resurrect the old name (as a coordinate-duplicate row,
via the instance sync). This map makes the rename durable: it records
ORIGINAL instance name -> studio name, and ``regenerate_shadow`` applies
it every time the shadow is rebuilt, so the studio consistently sees the
new name while the user's file keeps its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


def _path(original_path: Path) -> Path:
    return Path(original_path).parent / ".avar2-studio" / "instance-renames.json"


def load(original_path: Path) -> Dict[str, str]:
    p = _path(original_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str) and k != v}


def save(original_path: Path, aliases: Dict[str, str]) -> None:
    p = _path(original_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(aliases, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record(original_path: Path, old_name: str, new_name: str) -> Dict[str, str]:
    """Record a rename. ``old_name`` may itself be an alias (a chain:
    Def -> Default -> Final collapses to Def -> Final); renaming back to
    the original name drops the entry."""
    aliases = load(original_path)
    source_name = next((k for k, v in aliases.items() if v == old_name), old_name)
    if source_name == new_name:
        aliases.pop(source_name, None)
    else:
        aliases[source_name] = new_name
    save(original_path, aliases)
    return aliases


def apply_to_font(font, aliases: Dict[str, str]) -> int:
    """Rename the font object's instances per the alias map. Returns how
    many instances were renamed."""
    if not aliases:
        return 0
    renamed = 0
    for inst in getattr(font, "instances", None) or []:
        name = str(getattr(inst, "name", ""))
        if name in aliases:
            inst.name = aliases[name]
            renamed += 1
    return renamed
