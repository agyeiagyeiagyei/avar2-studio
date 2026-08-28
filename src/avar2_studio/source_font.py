"""Format-dispatching helpers for source files.

avar2-studio reads + writes two source formats:

  - ``.glyphs``      via ``glyphsLib`` (``GSFont``)
  - ``.designspace`` via ``fontTools.designspaceLib`` (``DesignSpaceDocument``)

This module is the single place that knows how to switch between them.
Every helper takes the loaded font object as its first argument and
dispatches on its type. Callers (``server.py``) load once, then pass the
same object through each helper for the duration of an HTTP request.

The two-tier instance model lives one level up, in ``server.py``: only
**source-defined** instances pass through the ``*_source_instance``
helpers below. Studio-only instances exist purely as rows in
``{family}-avar.csv`` and never touch the source file unless promoted via
``add_instance_to_source``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fontTools.designspaceLib import (
    AxisDescriptor,
    DesignSpaceDocument,
    InstanceDescriptor,
)
from glyphsLib import GSFont, load as _glyphs_load


def save_font_atomically(font: GSFont, path: Path) -> None:
    """Write a .glyphs file so a reader never sees it half-written.

    ``GSFont.save`` truncates and rewrites in place, and these files run to
    hundreds of kilobytes — wide enough that a build kicked off by the file
    watcher can start parsing while the write is still going. That surfaced as
    "Loading Glyphs file failed: Missing ',' for array at line N", a corrupt
    file that parses cleanly seconds later.

    Writing to a sibling temp file and renaming makes the swap atomic: a reader
    gets either the old complete file or the new one. The temp file is a
    sibling so the rename stays on one filesystem, and it is cleaned up if the
    write fails, leaving the original intact.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        font.save(str(tmp))
        os.replace(str(tmp), str(path))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise



SUPPORTED_SUFFIXES = (".glyphs", ".designspace")


class UnsupportedSourceFormat(Exception):
    """Raised when the source file's suffix isn't one we accept."""


def load_source(path: Path) -> Tuple[object, str]:
    """Load a source file and return ``(font, format_str)``.

    ``format_str`` is one of ``"glyphs"`` or ``"designspace"``.
    Raises :class:`UnsupportedSourceFormat` for any other suffix; callers
    upstream (the CLI) should reject ``.ufo`` and unknown extensions with
    a more specific message before we ever get here.
    """
    suffix = path.suffix.lower()
    if suffix == ".glyphs":
        return _glyphs_load(str(path)), "glyphs"
    if suffix == ".designspace":
        return DesignSpaceDocument.fromfile(str(path)), "designspace"
    raise UnsupportedSourceFormat(
        f"avar2-studio does not understand source files with suffix {suffix!r}. "
        f"Supported: {', '.join(SUPPORTED_SUFFIXES)}"
    )


def reload_source(path: Path) -> Tuple[object, str]:
    """Re-read the source from disk. Convenience alias for ``load_source``."""
    return load_source(path)


def detect_format(path: Path) -> str:
    """Return ``"glyphs"`` or ``"designspace"`` for ``path``'s suffix.

    Raises :class:`UnsupportedSourceFormat` for any other suffix.
    """
    suffix = path.suffix.lower()
    if suffix == ".glyphs":
        return "glyphs"
    if suffix == ".designspace":
        return "designspace"
    raise UnsupportedSourceFormat(
        f"avar2-studio does not understand source files with suffix {suffix!r}. "
        f"Supported: {', '.join(SUPPORTED_SUFFIXES)}"
    )


def get_family_name(font: object, path: Path) -> str:
    """Family name = source file stem, in both formats.

    Locked decision: file-stem-based naming keeps the avar2 build,
    sibling CSV, and built-font filename all in lockstep regardless of
    what ``fontInfo.familyName`` happens to say inside the UFOs or
    ``familyName`` in the .glyphs file.
    """
    return path.stem


# --------------------------------------------------------------------------
# Axes
# --------------------------------------------------------------------------


def get_axes(font: object) -> List[Dict]:
    """Return the source's axes in uniform shape.

    Each entry: ``{tag, name, min, max, default}``.
    """
    if isinstance(font, GSFont):
        return _axes_from_glyphs(font)
    if isinstance(font, DesignSpaceDocument):
        return _axes_from_designspace(font)
    raise TypeError(f"Unexpected font type: {type(font).__name__}")


def _axes_from_glyphs(font: GSFont) -> List[Dict]:
    axes = font.axes or []
    if not axes:
        return []

    # In .glyphs, the axis min/max are not on the axis object; they're
    # derived from the master grid. Walk masters and reduce.
    axis_ranges = {ax.axisTag: {"min": float("inf"), "max": float("-inf")} for ax in axes}
    for master in font.masters:
        if hasattr(master, "axes") and master.axes:
            for i, axis in enumerate(axes):
                if i < len(master.axes):
                    v = float(master.axes[i])
                    r = axis_ranges[axis.axisTag]
                    r["min"] = min(r["min"], v)
                    r["max"] = max(r["max"], v)

    out: List[Dict] = []
    for axis in axes:
        r = axis_ranges[axis.axisTag]
        lo = r["min"] if r["min"] != float("inf") else 0.0
        hi = r["max"] if r["max"] != float("-inf") else 1000.0
        out.append({
            "tag": axis.axisTag,
            "name": axis.name,
            "min": lo,
            "max": hi,
            "default": lo,
            # "Master coverage" = at least one master sits at a
            # non-default position. When false the axis is declared but
            # gvar has no deltas for it — sliders move and nothing
            # changes until an avar2 mapping is authored.
            "has_master_coverage": lo != hi,
        })
    return out


def _axes_from_designspace(doc: DesignSpaceDocument) -> List[Dict]:
    out: List[Dict] = []
    for ax in doc.axes:
        # An axis has master coverage iff at least one <source>'s
        # <location> for this axis differs from its default value.
        ax_default = float(ax.default)
        covered = False
        for src in doc.sources:
            loc = src.location or {}
            val = loc.get(ax.name)
            if val is not None and float(val) != ax_default:
                covered = True
                break
        out.append({
            "tag": ax.tag,
            "name": ax.name,
            "min": float(ax.minimum),
            "max": float(ax.maximum),
            "default": ax_default,
            "has_master_coverage": covered,
        })
    return out


# --------------------------------------------------------------------------
# Source-defined instances (read)
# --------------------------------------------------------------------------


def get_source_instances(font: object) -> List[Dict]:
    """Return only the instances declared inside the source file.

    Each entry: ``{name, coordinates: {tag: value}}``.

    Studio-only instances (CSV-only rows added through the UI) are NOT
    included; that union happens in ``server.py``.
    """
    if isinstance(font, GSFont):
        return _instances_from_glyphs(font)
    if isinstance(font, DesignSpaceDocument):
        return _instances_from_designspace(font)
    raise TypeError(f"Unexpected font type: {type(font).__name__}")


def get_masters(font: object) -> List[Dict]:
    """Return the source's masters — the parametric design corners.

    Each entry: ``{name, coordinates: {tag: value}}``. The control-axis
    brace flow pre-lists these so the designer places a crbr layer at a
    specific corner (narrowest, widest, …). A brace layer is always a
    full point: a parametric corner × the control-axis value.
    """
    if isinstance(font, GSFont):
        return _masters_from_glyphs(font)
    if isinstance(font, DesignSpaceDocument):
        return _masters_from_designspace(font)
    raise TypeError(f"Unexpected font type: {type(font).__name__}")


def _masters_from_glyphs(font: GSFont) -> List[Dict]:
    axes = font.axes or []
    out: List[Dict] = []
    for master in font.masters:
        coords: Dict[str, float] = {}
        maxes = list(getattr(master, "axes", None) or [])
        for i, axis in enumerate(axes):
            if i < len(maxes):
                coords[axis.axisTag] = float(maxes[i])
        out.append({"name": master.name or "Master", "coordinates": coords})
    return out


def _masters_from_designspace(doc: DesignSpaceDocument) -> List[Dict]:
    name_to_tag = {ax.name: ax.tag for ax in doc.axes}
    out: List[Dict] = []
    for src in doc.sources:
        coords: Dict[str, float] = {}
        for axis_name, value in (src.location or {}).items():
            tag = name_to_tag.get(axis_name)
            if tag is not None:
                coords[tag] = float(value)
        out.append({
            "name": src.name or src.styleName or "Source",
            "coordinates": coords,
        })
    return out


def _instances_from_glyphs(font: GSFont) -> List[Dict]:
    axes = font.axes or []
    out: List[Dict] = []
    for instance in font.instances:
        coords: Dict[str, float] = {}
        if hasattr(instance, "axes") and instance.axes:
            for i, axis in enumerate(axes):
                if i < len(instance.axes):
                    coords[axis.axisTag] = float(instance.axes[i])
        out.append({
            "name": instance.name or "Unnamed",
            "coordinates": coords,
        })
    return out


def _instances_from_designspace(doc: DesignSpaceDocument) -> List[Dict]:
    name_to_tag = {ax.name: ax.tag for ax in doc.axes}
    out: List[Dict] = []
    for inst in doc.instances:
        coords: Dict[str, float] = {}
        for axis_name, value in (inst.location or {}).items():
            tag = name_to_tag.get(axis_name)
            if tag is not None:
                coords[tag] = float(value)
        out.append({
            "name": inst.name or inst.styleName or "Unnamed",
            "coordinates": coords,
        })
    return out


# --------------------------------------------------------------------------
# Source-defined instances (write)
# --------------------------------------------------------------------------


def update_source_instance_coords(
    font: object,
    path: Path,
    instance_name: str,
    coordinates: Dict[str, float],
) -> bool:
    """Update an existing source-defined instance's parametric coords."""
    if isinstance(font, GSFont):
        return _update_instance_in_glyphs(font, path, instance_name, coordinates)
    if isinstance(font, DesignSpaceDocument):
        return _update_instance_in_designspace(font, path, instance_name, coordinates)
    raise TypeError(f"Unexpected font type: {type(font).__name__}")


def add_instance_to_source(
    font: object,
    path: Path,
    instance_name: str,
    coordinates: Dict[str, float],
    insert_after_instance_name: Optional[str] = None,
) -> bool:
    """Promote a studio-only instance into the source file's instance list.

    Returns False if the name is already present in the source.
    """
    if isinstance(font, GSFont):
        return _add_instance_to_glyphs(font, path, instance_name, coordinates, insert_after_instance_name)
    if isinstance(font, DesignSpaceDocument):
        return _add_instance_to_designspace(font, path, instance_name, coordinates, insert_after_instance_name)
    raise TypeError(f"Unexpected font type: {type(font).__name__}")


def rename_source_instance(font: object, path: Path, old_name: str, new_name: str) -> bool:
    if isinstance(font, GSFont):
        return _rename_instance_in_glyphs(font, path, old_name, new_name)
    if isinstance(font, DesignSpaceDocument):
        return _rename_instance_in_designspace(font, path, old_name, new_name)
    raise TypeError(f"Unexpected font type: {type(font).__name__}")


def delete_source_instance(font: object, path: Path, instance_name: str) -> bool:
    if isinstance(font, GSFont):
        return _delete_instance_in_glyphs(font, path, instance_name)
    if isinstance(font, DesignSpaceDocument):
        return _delete_instance_in_designspace(font, path, instance_name)
    raise TypeError(f"Unexpected font type: {type(font).__name__}")


# ---- .glyphs write impls -------------------------------------------------


def _update_instance_in_glyphs(font: GSFont, path: Path, name: str, coords: Dict[str, float]) -> bool:
    axes = font.axes or []
    target = None
    for inst in font.instances:
        if inst.name == name:
            target = inst
            break
    if target is None:
        return False

    new_axes = []
    for i, axis in enumerate(axes):
        tag = axis.axisTag
        if tag in coords:
            new_axes.append(coords[tag])
        elif hasattr(target, "axes") and target.axes and i < len(target.axes):
            new_axes.append(target.axes[i])
        else:
            new_axes.append(0.0)
    target.axes = new_axes
    save_font_atomically(font, path)
    return True


def _add_instance_to_glyphs(
    font: GSFont,
    path: Path,
    name: str,
    coords: Dict[str, float],
    insert_after: Optional[str],
) -> bool:
    for inst in font.instances:
        if inst.name == name:
            return False

    from glyphsLib.classes import GSInstance
    new_instance = GSInstance()
    new_instance.name = name

    axes = font.axes or []
    new_axes = []
    for axis in axes:
        new_axes.append(coords.get(axis.axisTag, 0.0))
    new_instance.axes = new_axes

    insert_index = None
    if insert_after:
        for i, inst in enumerate(font.instances):
            if inst.name == insert_after:
                insert_index = i + 1
                break
    if insert_index is not None:
        font.instances.insert(insert_index, new_instance)
    else:
        font.instances.append(new_instance)
    save_font_atomically(font, path)
    return True


def _rename_instance_in_glyphs(font: GSFont, path: Path, old_name: str, new_name: str) -> bool:
    for inst in font.instances:
        if inst.name == new_name:
            raise ValueError(f"Instance '{new_name}' already exists")
    target = None
    for inst in font.instances:
        if inst.name == old_name:
            target = inst
            break
    if target is None:
        return False
    target.name = new_name
    save_font_atomically(font, path)
    return True


def _delete_instance_in_glyphs(font: GSFont, path: Path, name: str) -> bool:
    target = None
    for inst in font.instances:
        if inst.name == name:
            target = inst
            break
    if target is None:
        return False
    font.instances.remove(target)
    save_font_atomically(font, path)
    return True


# ---- .designspace write impls --------------------------------------------


def _ds_find_instance(doc: DesignSpaceDocument, name: str) -> Optional[InstanceDescriptor]:
    for inst in doc.instances:
        candidate = inst.name or inst.styleName
        if candidate == name:
            return inst
    return None


def _ds_axis_name_lookup(doc: DesignSpaceDocument) -> Dict[str, str]:
    """Map axis tag → axis name. Designspace locations key by name, not tag."""
    return {ax.tag: ax.name for ax in doc.axes}


def _update_instance_in_designspace(doc: DesignSpaceDocument, path: Path, name: str, coords: Dict[str, float]) -> bool:
    target = _ds_find_instance(doc, name)
    if target is None:
        return False
    tag_to_name = _ds_axis_name_lookup(doc)
    location = dict(target.location or {})
    for tag, value in coords.items():
        axis_name = tag_to_name.get(tag)
        if axis_name is not None:
            location[axis_name] = float(value)
    target.location = location
    doc.write(str(path))
    return True


def _add_instance_to_designspace(
    doc: DesignSpaceDocument,
    path: Path,
    name: str,
    coords: Dict[str, float],
    insert_after: Optional[str],
) -> bool:
    if _ds_find_instance(doc, name) is not None:
        return False

    tag_to_name = _ds_axis_name_lookup(doc)
    location: Dict[str, float] = {}
    for ax in doc.axes:
        # Seed with axis default, then overlay supplied coords.
        location[ax.name] = float(ax.default)
    for tag, value in coords.items():
        axis_name = tag_to_name.get(tag)
        if axis_name is not None:
            location[axis_name] = float(value)

    new_instance = InstanceDescriptor()
    new_instance.name = name
    new_instance.styleName = name
    # familyName: pull from the first source if present, falls back to path.stem.
    family = None
    if doc.sources:
        family = doc.sources[0].familyName
    new_instance.familyName = family or path.stem
    new_instance.location = location

    insert_index = None
    if insert_after:
        for i, inst in enumerate(doc.instances):
            candidate = inst.name or inst.styleName
            if candidate == insert_after:
                insert_index = i + 1
                break
    if insert_index is not None:
        doc.instances.insert(insert_index, new_instance)
    else:
        doc.instances.append(new_instance)
    doc.write(str(path))
    return True


def _rename_instance_in_designspace(doc: DesignSpaceDocument, path: Path, old_name: str, new_name: str) -> bool:
    if _ds_find_instance(doc, new_name) is not None:
        raise ValueError(f"Instance '{new_name}' already exists")
    target = _ds_find_instance(doc, old_name)
    if target is None:
        return False
    target.name = new_name
    target.styleName = new_name
    doc.write(str(path))
    return True


def _delete_instance_in_designspace(doc: DesignSpaceDocument, path: Path, name: str) -> bool:
    target = _ds_find_instance(doc, name)
    if target is None:
        return False
    doc.instances.remove(target)
    doc.write(str(path))
    return True
