"""Inject Kinetikern corrections into the compiled variable font.

Per corrected instance, every glyph with a delta gets one gvar tuple that
moves only its two horizontal phantom points: the left one by minus the
left delta, the right one by plus the right delta. The outline never moves,
so the shift is exactly metrics (sidebearings and advance), composites stay
rigid (a component ignores its base's phantoms), and nothing else in the
font is touched. HVAR is rebuilt from the phantoms afterwards.

This runs on the compiled font, after the studio's default relocation. A
sparse master in the source would be split into dozens of tuples by that
relocation (measured: 55 to 838 tuples per glyph without any brace), and the
preview font already sits at gvar's 64 KB per-glyph header ceiling; one
small tuple per instance per glyph stays well inside it.

The supports and deltas come from fontTools' VariationModel over the
corrected locations, exactly as a compiler treats sparse masters: every
corrected instance comes out exact, the field blends in between, and
beyond the outermost instance on an axis the correction holds (OpenType
keeps a tuple's full value past its peak when no end coordinate is written).
Locations are quantized to F2Dot14 first, so the model solves on exactly
the regions the font stores: a coordinate that rounds to the default must
be the default (a stored peak of 0 means "axis not involved", and a model
that still saw a sliver of a region there would put a huge delta on it).
A correction at the default location itself is the model's base value and
goes in as a tuple with no axes, which applies everywhere.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._g_l_y_f import USE_MY_METRICS
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib.hvar import add_HVAR
from fontTools.varLib.models import VariationModel, normalizeValue, piecewiseLinearMap

from .. import grade as _grade
from .. import source_font as _source_font
from . import sidecar

PARAM_TAGS = _grade.PARAM_TAGS


def _normalized(font, location: Dict[str, float]) -> Dict[str, float]:
    axes = {a.axisTag: a for a in font["fvar"].axes}
    segments = font["avar"].segments if "avar" in font else {}
    out = {}
    for tag, value in location.items():
        axis = axes.get(tag)
        if axis is None:
            raise ValueError("no fvar axis %r" % (tag,))
        n = normalizeValue(float(value), (axis.minValue, axis.defaultValue, axis.maxValue))
        if segments.get(tag):
            n = piecewiseLinearMap(n, segments[tag])
        n = round(n * 16384) / 16384  # F2Dot14, as gvar stores it
        if n:
            out[tag] = n
    return out


def _point_count(glyph, glyf) -> int:
    if glyph.isComposite():
        return len(glyph.components) + 4
    coords, _ends, _flags = glyph.getCoordinates(glyf)
    return len(coords) + 4


# Private sfnt table recording which gvar tuples inject() added, as
# {"v": 1, "tuples": [[glyph, [[tag, [start, peak, end]], ...]], ...]}.
# strip() removes exactly those — a shape heuristic ("phantom-only on the
# parametric axes") also matched genuine compile output (22 such tuples in
# Crispy's `three`) and deleted it. Every later stage repacks the sfnt with
# its tables intact (fontTools saves, the native SPAC injector's raw
# FontBuilder copy), so the record survives to the next chain run.
MARKER_TABLE = "KKRN"


def _tuple_key(name: str, axes: Dict[str, Tuple[float, float, float]]) -> Tuple[str, Tuple]:
    return (name, tuple(sorted((tag, tuple(float(v) for v in span)) for tag, span in axes.items())))


def _record_injected(font: TTFont, keys: List[Tuple[str, Tuple]]) -> None:
    table = newTable(MARKER_TABLE)
    table.data = json.dumps({"v": 1, "tuples": [[name, [[tag, list(span)] for tag, span in axes]]
                                               for name, axes in keys]}).encode("utf-8")
    font[MARKER_TABLE] = table


def _recorded(font: TTFont) -> set:
    if MARKER_TABLE not in font:
        return set()
    try:
        payload = json.loads(font[MARKER_TABLE].data.decode("utf-8"))
        return {(name, tuple((tag, tuple(float(v) for v in span)) for tag, span in axes))
                for name, axes in payload.get("tuples", [])}
    except (ValueError, AttributeError, TypeError):
        return set()


def strip(font: TTFont) -> int:
    """Remove the tuples this module injected on an earlier chain run (as
    recorded in the marker table), so applying the chain twice lands in
    the same place as once. A font without the record has nothing of ours
    to remove. Returns the number removed."""
    ours = _recorded(font)
    if not ours:
        return 0
    removed = 0
    for name, variations in font["gvar"].variations.items():
        keep = []
        for tv in variations:
            if _tuple_key(name, tv.axes) in ours:
                removed += 1
            else:
                keep.append(tv)
        variations[:] = keep
    del font[MARKER_TABLE]
    return removed


def inject(font: TTFont, corrections: List[Tuple[Dict[str, float], Dict[str, Tuple[int, int]]]]) -> int:
    """``corrections``: per instance, its location in design units and
    ``{compiled glyph name: (left delta, right delta)}``. Returns the number
    of tuples added; rebuilds HVAR when any were."""
    merged: List[Tuple[Dict[str, float], Dict[str, Tuple[int, int]]]] = [({}, {})]  # the default location first
    for location, deltas in corrections:
        loc = _normalized(font, location)
        for known, known_deltas in merged:
            if known == loc:
                known_deltas.update(deltas)
                break
        else:
            merged.append((loc, dict(deltas)))
    if not any(d for _, d in merged):
        return 0
    model = VariationModel([loc for loc, _ in merged])
    glyf, gvar = font["glyf"], font["gvar"]
    added = 0
    injected: List[Tuple[str, Tuple]] = []
    for name in font.getGlyphOrder():
        lefts = [float(d.get(name, (0, 0))[0]) for _, d in merged]
        rights = [float(d.get(name, (0, 0))[1]) for _, d in merged]
        if not any(lefts) and not any(rights):
            continue
        glyph = glyf[name]
        if glyph.isComposite() and any(c.flags & USE_MY_METRICS for c in glyph.components):
            continue  # takes its metrics from a component, which carries its own tuple
        variations = gvar.variations.get(name)
        n = len(variations[0].coordinates) if variations else _point_count(glyph, glyf)
        if variations is None:
            variations = gvar.variations.setdefault(name, [])
        for support, dl, dr in zip(model.supports, model.getDeltas(lefts), model.getDeltas(rights)):
            dl, dr = int(round(dl)), int(round(dr))
            if not dl and not dr:
                continue
            coords = [None] * n
            coords[-4] = (-dl, 0)
            coords[-3] = (dr, 0)
            variations.append(TupleVariation(dict(support), coords))
            injected.append(_tuple_key(name, support))
            added += 1
    if added:
        _record_injected(font, injected)
        add_HVAR(font)
    return added


def instance_locations(source_path: Path) -> Dict[str, Dict[str, float]]:
    """Parametric location per instance name: source-defined instances,
    completed by the avar CSV's rows (the server's _grade_instance_coords,
    reachable from a build worker without the server module)."""
    coords: Dict[str, Dict[str, float]] = {}
    try:
        font, _fmt = _source_font.load_source_cached(source_path)
        for inst in _source_font.get_source_instances(font):
            coords[inst["name"]] = {str(k).upper(): float(v) for k, v in dict(inst.get("coordinates", {})).items()}
    except Exception:
        pass
    csv_path = source_path.parent / f"{source_path.stem}-avar.csv"
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("Instance Name") or "").strip()
                if not name:
                    continue
                c = coords.setdefault(name, {})
                for tag in PARAM_TAGS:
                    if tag in c or row.get(tag) in (None, ""):
                        continue
                    try:
                        c[tag] = float(row[tag])
                    except (TypeError, ValueError):
                        pass
    return coords


def _compiled_names(font: TTFont, source_path: Path) -> Dict[str, str]:
    """{source glyph name: compiled glyph name} by code point (fontc writes
    production names: euro becomes Euro)."""
    cmap = font.getBestCmap() or {}
    out = {}
    try:
        src, fmt = _source_font.load_source_cached(source_path)
    except Exception:
        return out
    if "glyphs" not in str(fmt).lower():
        return out
    for glyph in src.glyphs:
        for u in (getattr(glyph, "unicodes", None) or ([glyph.unicode] if glyph.unicode else [])):
            try:
                compiled = cmap.get(int(str(u), 16))
            except ValueError:
                continue
            if compiled:
                out.setdefault(glyph.name, compiled)
    return out


def apply_to_font(vf_path: Path, source_path: Path, log: Optional[Callable[[str], None]] = None) -> Path:
    """Bake the sidecar's corrections into ``vf_path`` in place (a no-op
    without any). Returns ``vf_path``."""
    log = log or (lambda _m: None)
    entries = sidecar.entries(source_path)
    if not entries:
        return vf_path
    font = TTFont(str(vf_path))
    try:
        if not all(t in font for t in ("fvar", "gvar", "glyf")):
            raise RuntimeError("Kinetikern needs a glyf-based variable font with fvar and gvar")
        stripped = strip(font)
        order = set(font.getGlyphOrder())
        names = _compiled_names(font, source_path)
        locations = instance_locations(source_path)
        corrections = []
        for entry in entries:
            loc = entry.get("location") or locations.get(entry["name"]) or {}
            if any(t not in loc for t in PARAM_TAGS):
                log(f"kinetikern: {entry['name']} has no parametric location; skipped")
                continue
            deltas = {}
            for glyph, (dl, dr) in entry["deltas"].items():
                compiled = names.get(glyph, glyph)
                if compiled in order:
                    deltas[compiled] = (dl, dr)
            corrections.append(({t: float(loc[t]) for t in PARAM_TAGS}, deltas))
        added = inject(font, corrections)
        if added or stripped:
            font.save(str(vf_path))
        log(f"kinetikern: {len(corrections)} instance(s), {added} tuple(s) injected"
            + (f", {stripped} earlier one(s) replaced" if stripped else ""))
    finally:
        font.close()
    return vf_path
