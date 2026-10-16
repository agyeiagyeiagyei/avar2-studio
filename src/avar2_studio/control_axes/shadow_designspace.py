"""Shadow ``.designspace`` regeneration — pooled sparse UFOs (split
from the ``control_axes`` module, audit §4).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional

from . import shadow_glyphs


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
    import ufoLib2
    from fontTools.designspaceLib import (
        AxisDescriptor,
        DesignSpaceDocument,
        SourceDescriptor,
    )

    shadow_dir = shadow_glyphs.shadow_dir_for(original_path)
    shadow_path = shadow_glyphs.shadow_path_for(original_path)
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
        tag = (spec.get("tag") or "").strip()
        if not tag:
            continue
        entry = {
            "tag": tag,
            "name": spec.get("display_name") or tag,
            "default": float(spec.get("default") or 0),
            "layers": list(spec.get("layers") or []),
        }
        control_axes.append(entry)
        if tag.lower() in existing_tags:
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
        existing_tags.add(tag.lower())

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
    control_tags = {e["tag"].lower() for e in control_axes}
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
    for _key, pool in sorted(pools.items()):
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
