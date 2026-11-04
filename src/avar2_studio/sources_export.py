"""E1 — "UFO + designspace (zip)": the sources a fontmake/gftools build needs.

The studio reads ``.glyphs`` and ``.designspace`` but only ever wrote built
fonts (TTF/WOFF2), so the Google Fonts team could not rebuild Crispy from what
the designer hands over. This module writes the other half: the masters as
UFOs, a designspace 5 file carrying the avar2 mappings, the declared axis
defaults, the named instances and the hidden axes, plus a README.

What is in the sources (E2): the ``SPAC`` axis as metrics-only twin sources and
the Kinetikern corrections as metrics-only sparse sources, rendered from the same
sidecars the binary-stage injectors read, so a fontmake build of the zip is
spaced. The studio's own preview and TTF keep the injectors (decision 0004's
budget stands); the sidecars stay the authored truth, with two renderers.

What is deliberately NOT in the sources, and the README says so: **the relocated
default** — the studio's TTF moves the parametric defaults
  onto the ORIGIN row at build time (docs/decisions/0001). A designspace
  default must be a master location, so the sources rest at the compiled
  origin and the default row is not in ``<mappings>`` (exactly as gen-avar2
  handles it for the built font).
"""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

import glyphsLib
from fontTools.designspaceLib import DesignSpaceDocument, InstanceDescriptor
from glyphsLib.classes import GSLayer

from .build import config_generator
from .studio_log import get_logger

log = get_logger(__name__)

#: The always-visible note in the export dialog (E2). Spacing now rides the
#: sources, so what is left to warn about is the resting origin. The UI shows
#: the same text; a test pins both against this constant.
RESTING_NOTE = (
    "The sources rest at the compiled origin; the studio's font relocates "
    "its default to the ORIGIN row, so the default row differs there.")


class SourcesError(RuntimeError):
    """The sources cannot be written (a usage error, not a build failure)."""


def _commit() -> str:
    """Best-effort: an explicit env var, else the checkout's short HEAD."""
    if os.environ.get("AVAR2_STUDIO_COMMIT"):
        return os.environ["AVAR2_STUDIO_COMMIT"]
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(Path(__file__).resolve().parents[2]),
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("avar2-studio")
    except Exception:                                   # pragma: no cover - wheel-less
        return "unknown"


#: Axes the studio COMPOSES at build time (grade braces, the Kinetikern/spacing
#: machinery, rounding, slanting, the secondary parametric axes). Their metadata
#: entries exist so the studio can show a slider; they are placeholders, never
#: designspace axes of their own. Shipping them as axes gave Crispy a designspace
#: with duplicate names, `grad`/`spac` axes no master reaches, a default of −1000
#: on `lcwd`/`ymod` and no findable default source — fontmake exited 1.
STUDIO_BUILD_AXES = frozenset(
    {"grad", "spac", "rond", "slnt", "lcwd", "ymod"})


def declared_user_axes(metadata: Dict[str, Dict]) -> List[Tuple[str, Dict]]:
    """``[(tag, entry)]`` for the user axes the metadata declares.

    Parametric axes are the masters' own business (glyphsLib emits them with
    their master ranges); these are the CREATED axes — wght/wdth/opsz and any
    other the designer declared — whose default the mapping is written against.

    Two kinds of entry are skipped, and both matter: the studio's build-time
    placeholders (``STUDIO_BUILD_AXES``) and anything without a declared
    ``default``. A placeholder has no default because nothing is authored at
    it, and ``_default_of`` would silently fall back to ``min`` — −1000 for a
    secondary axis — which is what made fontmake fail on Crispy.
    """
    out = []
    seen = set()
    for _col, entry in (metadata or {}).items():
        tag = (entry.get("registered_tag") or "").strip()
        if not tag or entry.get("is_parametric"):
            continue
        if entry.get("min") is None or entry.get("max") is None:
            continue
        if tag.lower() in STUDIO_BUILD_AXES:
            continue
        if entry.get("default") is None:
            continue
        key = tag.lower()
        if key in seen:                      # one axis per tag, case-insensitive
            continue
        seen.add(key)
        out.append((tag, entry))
    return out


def _default_of(entry: Dict, tag: str) -> float:
    from .pipeline import TRADITIONAL_AXIS_DEFAULTS
    lo, hi = float(entry["min"]), float(entry["max"])
    d = entry.get("default")
    if d is None:
        d = TRADITIONAL_AXIS_DEFAULTS.get(tag.lower(), lo)
    return max(lo, min(hi, float(d)))


def _is_default_row(row, declared: Dict[str, Tuple[float, float, float]]) -> bool:
    """True when the row's input sits at the declared defaults (tolerance as
    the pipeline uses). Such a row cannot be a designspace mapping: its input
    IS the default, which maps to itself."""
    if not row.in_axes:
        return False
    for tag, value in row.in_axes.items():
        trip = declared.get(tag)
        if not trip:
            return False
        if abs(float(value) - float(trip[1])) > 1e-6:
            return False
    return True


def _prepare_rows(rows, font, source_path: Path):
    """The build's own row preparation: ``_fill_identity_in_axes``.

    E1d: an unfinished CSV row with no traditional inputs reached the
    designspace as ``<input />``, which fontmake reads as THE default
    location — every other mapping then landed offset by that row's output
    (4,913 u on the real Crispy). The studio build has always filled such a
    row from the source's instance coordinates (identity when the row names
    an instance the source does not declare), so the export calls the same
    function with the same config shape instead of keeping a copy.
    """
    from . import source_font

    family = source_font.get_family_name(font, Path(source_path))
    tags = sorted(a["tag"] for a in source_font.get_axes(font) if a.get("tag"))
    font_key = f"{family}[{','.join(tags)}].ttf" if tags else f"{family}-VF.ttf"
    fvar_instances = [
        {"name": entry["name"],
         "coordinates": {str(t).lower(): float(v)
                         for t, v in entry["coordinates"].items()}}
        for entry in source_font.get_source_instances(font)
    ]
    config = {"fvarInstances": {font_key: fvar_instances}}
    return config_generator._fill_identity_in_axes(rows, config, font_key)


SPAC_TAG = "SPAC"
SPAC_NAME = "Spacing"


def _spacing_transform(source_path: Path) -> Optional[dict]:
    """The enabled SPAC transform entry, or None when spacing is off."""
    from .transforms import config as _tx_config

    for entry in _tx_config.entries(Path(source_path)):
        if entry.get("type") in ("spac_widthaware", "spac") and entry.get("enabled"):
            return entry
    return None


#: The compiler draws a `.notdef` box this wide (0.4 × upm) when the source's
#: `.notdef` has no outline. The injector's mean ink width includes it — measured
#: on a compiled Crispy Mini: bounds (100, -300, 900, 1600) at upm 2000 — so the
#: renderer must too, or every width-aware factor comes out ~11 % high.
NOTDEF_INK_RATIO = 0.4


def _ink_widths(font) -> Dict[str, float]:
    """``{glyph: ink width}`` for one ufoLib2 font, components included.

    Mirrors ``builtin_spac_widthaware._ink_width`` over the font a compiler
    would emit: glyphs whose ink is zero are skipped, and an empty ``.notdef``
    counts as the box the compiler substitutes for it.
    """
    layer = font.layers.defaultLayer
    upm = float(getattr(font.info, "unitsPerEm", 1000) or 1000)
    out = {}
    for name in font.keys():
        bounds = font[name].getBounds(layer)
        width = (bounds.xMax - bounds.xMin) if bounds is not None else 0.0
        if name == ".notdef" and width <= 0:
            width = NOTDEF_INK_RATIO * upm
        if width > 0:
            out[name] = float(width)
    # glyphsLib does not write `.notdef` when the source has no outline for it,
    # but every compiler emits the box — so the basis adds it here.
    out.setdefault(".notdef", NOTDEF_INK_RATIO * upm)
    return out


def _ink_widths_from_build(font_path: Path) -> Dict[str, float]:
    """``{glyph: ink width}`` measured on the BUILT font at its default.

    E2i: the injector normalises against the built font's own average ink, at
    the studio's relocated default — not the designspace's default source. On
    Crispy those are different designs: the default source is the 47-1-1
    hairline master (mean ink 96.3 u) while the studio rests at the relocated
    default (mean ~1,750 u), so every SPAC amount came out ~18× too strong
    (`i` +6 u against the injector's +58). The measurement is the injector's
    own function, so the two renderers can only agree.
    """
    from fontTools.ttLib import TTFont

    from .transforms.builtin_spac_widthaware import _ink_width

    font = TTFont(str(font_path))
    try:
        glyph_set = font.getGlyphSet()
        out = {}
        for name in font.getGlyphOrder():
            ink = _ink_width(glyph_set, name)
            if ink > 0:
                out[name] = float(ink)
        return out
    finally:
        font.close()


def _shift_metrics(glyph, dx: int) -> None:
    """Move the drawing right by ``dx`` (the LSB grows), origin unchanged."""
    for contour in glyph:
        for point in contour:
            point.x += dx
    for component in glyph.components:
        a, b, c, d, ox, oy = component.transformation
        component.transformation = (a, b, c, d, ox + dx, oy)


def _fit_dx(dx: int, width: float, composite: bool) -> int:
    """Cap a NEGATIVE move so the twin's advance cannot go below 1 u.

    A UFO/designspace source cannot carry a negative advance width, and
    fontTools refuses to compile one. Crispy's hairline masters are narrower
    than the amount the injector applies at SPAC −100 (hairline `n` advance
    166 u against a 258 u tightening), so the exact amount is not representable
    there and the export caps it instead of writing a font fontmake rejects.
    Everywhere the design can carry the amount the twin is exact; the default
    design (where the injector's amounts come from) always can.
    """
    if dx >= 0:
        return dx
    if composite:                       # advance moves by dx
        lowest = 1 - int(width)
    else:                               # advance moves by 2·dx
        lowest = -((int(width) - 1) // 2)
    return max(dx, lowest)


def _use_my_metrics(glyph) -> bool:
    """True when the compiler takes this composite's metrics from a component.

    ufoLib2 exposes no per-component lib in this version, and the flag is
    glyph-level in both conventions glyphsLib writes, so it is read there.
    """
    lib = glyph.lib or {}
    return bool(lib.get("public.useMyMetrics") or lib.get("com.glyphs.useMyMetrics"))


def _is_transformed(transformation) -> bool:
    """True when a component is more than a translation (scale/flip/rotate).

    glyphsLib's ``--decompose-transformed-components``, which the studio's
    build passes, flattens these; a twin has to match it glyph for glyph, or a
    flipped ``w`` tracks like a composite there and like a simple glyph here
    (E2j).
    """
    a, b, c, d, _ox, _oy = transformation
    return (abs(a - 1) > 1e-9 or abs(b) > 1e-9
            or abs(c) > 1e-9 or abs(d - 1) > 1e-9)


def _flatten(glyph, provider, name: str) -> bool:
    """Replace ``glyph``'s components with their outlines, in place."""
    if not glyph.components:
        return False
    from fontTools.pens.pointPen import SegmentToPointPen
    from fontTools.pens.recordingPen import DecomposingRecordingPen

    pen = DecomposingRecordingPen(provider)
    provider[name].draw(pen)
    glyph.clearContours()
    glyph.clearComponents()
    pen.replay(SegmentToPointPen(glyph.getPointPen()))
    return True


def _decompose_transformed(glyph, original_font, name: str) -> bool:
    """Flatten ``glyph``'s components from the UNSHIFTED original.

    The original font is used on purpose: the twin's own base glyphs already
    carry their SPAC shift by the time this runs, and drawing through them
    would bake that shift in twice (E2j's double-shifted composites).
    """
    if not any(_is_transformed(c.transformation) for c in glyph.components):
        return False
    return _flatten(glyph, original_font, name)


def _decompose_transformed_sources(doc: DesignSpaceDocument) -> int:
    """Give every source the same glyph structure, as the studio's build does.

    Two shapes are normalised away, both because fontmake refuses a designspace
    whose masters disagree ("differing number of contours in glyph U") and
    because the studio's fontc build decomposes them anyway:

    * a component that is more than a translation (the flipped ``w``/``W``),
      which fontc's ``--decompose-transformed-components`` flattens;
    * a glyph that is a composite in some sources and contours in others
      (``colon``), which no compiler can interpolate.

    Plain composites stay composites: the injector's composite rule depends on
    them.
    """
    signatures: Dict[str, set] = {}
    targets = set()
    for source in doc.sources:
        font = source.font
        if font is None:
            continue
        for layer in font.layers:
            for name in layer.keys():
                glyph = layer[name]
                signatures.setdefault(name, set()).add(
                    (len(glyph.contours), len(glyph.components)))
                if any(_is_transformed(c.transformation)
                       for c in glyph.components):
                    targets.add(name)
    targets |= {name for name, seen in signatures.items() if len(seen) > 1}

    done = 0
    for source in doc.sources:
        font = source.font
        if font is None:
            continue
        for layer in font.layers:
            for name in list(layer.keys()):
                if name in targets and _flatten(layer[name], layer, name):
                    done += 1
    if done:
        log.info("sources_export: %d components decomposed (%d glyphs)",
                 done, len(targets))
    return done


def _shift_spacing(twin, original, inks: Dict[str, float], mean_ink: float, *,
                   kind: str, value: float, bias: float, scale: float) -> None:
    """Move one twin's metrics as the injector moves its phantom points.

    Simple glyph: ``advance + 2·dx`` with the drawing moved right by ``dx``, so
    both sidebearings open equally. Composite (the injector moves the LEFT
    phantom only): the advance moves by ``dx`` and the left side by ``dx``, the
    right one not at all — so each component's offset is corrected by the shift
    its base already carries, or the composite inherits the base's move AND its
    own (Cacute opened +246 and closed −123; E2j). A composition the studio's
    build decomposes is flattened first and then tracks like a simple glyph.
    """
    shifts: Dict[str, int] = {}
    for name, ink in inks.items():
        if name not in twin:
            continue
        glyph = twin[name]
        _decompose_transformed(glyph, original, name)
        composite = bool(glyph.components)
        if composite and _use_my_metrics(glyph):
            continue
        amount = (value if kind == "spac"
                  else value * ((ink / mean_ink) ** bias) * scale)
        dx = round(amount)
        if dx < 0:
            dx = _fit_dx(dx, glyph.width, composite)
        if dx:
            shifts[name] = dx

    for name, dx in shifts.items():
        glyph = twin[name]
        if glyph.components:
            for component in glyph.components:
                base_shift = shifts.get(component.baseGlyph, 0)
                a, b, c, d, ox, oy = component.transformation
                component.transformation = (a, b, c, d, ox + dx - base_shift, oy)
            glyph.width += dx
        else:
            _shift_metrics(glyph, dx)
            glyph.width += 2 * dx


def _add_spacing_axis(doc: DesignSpaceDocument, source_path: Path,
                      ink_font: Optional[Path] = None) -> Optional[str]:
    """Add ``SPAC`` as metrics-only twin sources (E2).

    Per master and per end of the range, a twin source with the same outlines and
    every inked glyph's sidebearings moved by the enabled transform's per-glyph
    amount, so the axis changes metrics and nothing else. The binary-stage
    injector moves phantom points; this is the same arithmetic at source level —
    a simple glyph takes ``+d`` per side (advance ``+2d``), a composite only its
    left side (advance ``+d``), and a ``USE_MY_METRICS`` composite is left alone
    because it inherits its base's delta.

    The transform is linear in SPAC, so the axis is defined by its ends — and
    BOTH non-zero ends need a twin, symmetric range or not. With only the
    maximum, nothing sits below the default, so a fontmake build is flat at
    negative SPAC while the injector tightens (crispy-mini at −40: 0.0 u vs
    −88 u; Crispy is −100…100) — E2h.
    """
    entry = _spacing_transform(source_path)
    if entry is None:
        return None
    params = entry.get("params") or {}
    lo = float(params.get("min", -20))
    hi = float(params.get("max", 40))
    kind = entry["type"]
    bias = float(params.get("bias", 1.0))
    scale = float(params.get("scale", 1.0))
    if hi == 0 and lo == 0:
        return None

    # The injector normalises against the built font's own average ink width at
    # ITS default — the studio's relocated default, not the designspace's
    # default source — so the renderer measures the built font when it has one
    # (E2i) and only falls back to the default source otherwise (a caller with
    # no build, e.g. a test).
    if ink_font is not None and Path(ink_font).exists():
        inks = _ink_widths_from_build(Path(ink_font))
    else:
        default_source = next(
            (s for s in doc.sources if not s.layerName), doc.sources[0])
        inks = _ink_widths(default_source.font)
    mean_ink = (sum(inks.values()) / len(inks)) if inks else 0.0

    import copy as _copy

    from fontTools.designspaceLib import AxisDescriptor, SourceDescriptor

    axis = AxisDescriptor()
    axis.tag = SPAC_TAG
    axis.name = SPAC_NAME
    axis.minimum = lo
    axis.default = 0.0
    axis.maximum = hi
    doc.addAxis(axis)

    # One twin per non-zero end. A symmetric range is NOT an exception: an
    # axis with no master below the default has no negative side at all.
    ends = [hi] if lo == hi else [hi, lo]
    twins = []
    # Only the masters get twins: a sparse brace source is an extra layer of its
    # master's UFO and rides along in the deep copy.
    for source in [s for s in doc.sources if not s.layerName]:
        for value in ends:
            twin_font = _copy.deepcopy(source.font)
            _shift_spacing(twin_font, source.font, inks, mean_ink, kind=kind,
                           value=value, bias=bias, scale=scale)
            twin = SourceDescriptor()
            twin.font = twin_font
            twin.familyName = source.familyName
            twin.styleName = f"{source.styleName or source.name} SPAC{value:g}"
            twin.name = twin.styleName
            slug = "".join(ch if ch.isalnum() else "-" for ch in twin.styleName)
            twin.filename = f"{slug}.ufo"
            twin.location = dict(source.location, **{SPAC_NAME: value})
            twins.append(twin)
    for twin in twins:
        doc.addSource(twin)
    log.info("sources_export: SPAC as %d metrics-only twin sources (%s, %g..%g)",
             len(twins), kind, lo, hi)
    return SPAC_TAG


def _add_kinetikern_braces(font, kinetikern: Dict, rows) -> int:
    """Write the Kinetikern corrections as metrics-only brace layers (E2).

    For every entry in the sidecar (while the family switch is on) and every
    glyph it moved, a brace at the style's PARAMETRIC location carrying the
    interpolated outline with the LSB and advance moved exactly as
    ``kinetikern/inject.py`` moves the phantom points: the left side by
    ``left``, the advance by ``left + right``. Composites keep their own
    metrics — the bake leaves them rigid too.

    Returns the number of layers written; 0 when the switch is off or nothing
    is corrected, which is what "with Kinetikern off, no braces are written"
    means.
    """
    if not kinetikern or not kinetikern.get("enabled"):
        return 0
    entries = [e for e in (kinetikern.get("instances") or []) if e.get("deltas")]
    masters = list(font.masters)
    if not entries or not masters:
        return 0

    from fontTools.varLib.models import VariationModel, normalizeLocation

    axes_tags = [str(getattr(a, "axisTag", "")).upper() for a in font.axes]
    index_of = {tag: i for i, tag in enumerate(axes_tags)}
    base = masters[0]
    base_loc = [float(v) for v in (base.axes or [])]
    if not base_loc:
        return 0

    def _norm(loc_values):
        axis_ranges = {}
        for i, _tag in enumerate(axes_tags):
            values = [float(m.axes[i]) for m in masters if i < len(m.axes)]
            axis_ranges[str(i)] = (min(values), base_loc[i], max(values))
        return normalizeLocation(
            {str(i): v for i, v in enumerate(loc_values)}, axis_ranges)

    try:
        model = VariationModel([_norm([float(v) for v in m.axes])
                                for m in masters])
    except Exception as exc:            # incompatible master set: no braces
        log.warning("sources_export: Kinetikern braces skipped (%s)", exc)
        return 0

    by_name = {r.instance_name: r for r in rows}
    written = 0
    seen = set()                        # (glyph, brace name) written once
    for entry in entries:
        row = by_name.get(entry["name"])
        location = dict(entry.get("location") or {})
        if row is not None:
            for tag, value in row.out_axes.items():
                location.setdefault(str(tag).upper(), float(value))
        values = list(base_loc)
        for tag, value in location.items():
            if tag in index_of:
                values[index_of[tag]] = float(value)
        # Glyphs 3 stores a brace layer's location in ``attributes.coordinates``
        # (the name is a human label), and that is what glyphsLib turns into a
        # sparse source — a `{x, y, z}` name alone produces an inert layer.
        brace_name = f"Kinetikern {entry['name']}"
        for glyph_name, (left, right) in sorted(entry["deltas"].items()):
            glyph = font.glyphs[glyph_name] if glyph_name in font.glyphs else None
            if glyph is None or (glyph_name, brace_name) in seen:
                continue
            master_layers = []
            for master in masters:
                layer = next((ly for ly in glyph.layers
                              if ly.layerId == master.id), None)
                if layer is None:
                    break
                master_layers.append(layer)
            if len(master_layers) != len(masters):
                continue
            ref = master_layers[0]
            ref_paths = list(ref.paths or [])
            compatible = all(len(list(ly.paths or [])) == len(ref_paths)
                             and all(len(rp.nodes) == len(mp.nodes)
                                     for rp, mp in zip(ref_paths, ly.paths))
                             for ly in master_layers)
            if not compatible:
                continue
            new_paths = copy.deepcopy(ref_paths, {id(ref): None})
            for pi, path in enumerate(new_paths):
                for ni in range(len(path.nodes)):
                    xs = [float(ly.paths[pi].nodes[ni].position.x)
                          for ly in master_layers]
                    ys = [float(ly.paths[pi].nodes[ni].position.y)
                          for ly in master_layers]
                    path.nodes[ni].position = (
                        model.interpolateFromMasters(_norm(values), xs) + left,
                        model.interpolateFromMasters(_norm(values), ys),
                    )
            widths = [float(ly.width) for ly in master_layers]
            layer = GSLayer()
            layer.name = brace_name
            layer.attributes = {"coordinates": list(values)}
            layer.associatedMasterId = base.id
            layer.layerId = str(uuid.uuid4()).upper()
            layer.paths = new_paths
            layer.width = model.interpolateFromMasters(_norm(values), widths) \
                + left + right
            glyph.layers.append(layer)
            seen.add((glyph_name, brace_name))
            written += 1
    if written:
        log.info("sources_export: %d Kinetikern brace layers", written)
    return written


def _spacing_summary(kinetikern: Optional[Dict], spac_tag: Optional[str]):
    """What the README lists under "Composed into the masters" for spacing."""
    out = []
    if spac_tag:
        out.append(f"the {SPAC_TAG} axis as metrics-only twin sources")
    if kinetikern and kinetikern.get("enabled") and kinetikern.get("instances"):
        out.append("Kinetikern corrections as metrics-only brace layers")
    return out


def _validate_designspace(doc: DesignSpaceDocument, rows=None,
                          axis_names: Optional[Dict[str, str]] = None,
                          default_row_name: Optional[str] = None) -> None:
    """Refuse a designspace fontmake cannot use, with the reason named.

    Three things went wrong on the real Crispy and none of them raised: duplicate
    axis names, an axis no source sits at (so no default source exists), and a
    default that fell back to an axis minimum. A zip that builds is the whole
    point of the export, so these are errors here rather than surprises in
    fontmake's log.

    E1d adds the mapping half: a mapping whose input is empty — or whose input
    dimensions all sit at the axes' defaults, which is the same location —
    shifts every other mapping by its output in fontmake's avar2.
    """
    tags = [a.tag for a in doc.axes]
    names = [a.name for a in doc.axes]
    for label, values in (("tags", tags), ("names", names)):
        lowered = [str(v).lower() for v in values]
        dupes = {v for v in lowered if lowered.count(v) > 1}
        if dupes:
            raise SourcesError(
                f"the designspace would carry duplicate axis {label}: "
                f"{', '.join(sorted(dupes))} (axes: {values})")
    if doc.findDefault() is None:
        missing = [f"{a.tag}={a.default}" for a in doc.axes
                   if not any(s.location.get(a.name) == a.default
                              for s in doc.sources)]
        raise SourcesError(
            "no source sits at the axes' defaults, so the designspace has no "
            f"default source (axes whose default nothing reaches: "
            f"{', '.join(missing) or 'unknown'}) — fontmake cannot build it")
    if rows is None:
        return
    defaults = {}
    for axis in doc.axes:
        defaults[axis.name] = float(axis.default)
        defaults[str(axis.tag).upper()] = float(axis.default)
    for row in rows:
        if default_row_name is not None and row.instance_name == default_row_name:
            continue
        if not row.in_axes:
            raise SourcesError(
                f"mapping row {row.instance_name!r} has no input: an empty "
                "<input/> IS the default location, so fontmake would offset "
                "every other mapping by this row's output")
        at_default = True
        for tag, value in row.in_axes.items():
            axis_default = defaults.get(_axis_name(tag, axis_names or {}))
            if axis_default is None:
                axis_default = defaults.get(str(tag).upper())
            if axis_default is None or abs(float(value) - axis_default) > 1e-9:
                at_default = False
                break
        if at_default:
            raise SourcesError(
                f"mapping row {row.instance_name!r} has an input at the "
                "default location, which fontmake would apply to every other "
                "mapping as an offset — give the row its own location or "
                "remove it")


def _mapping_xml(doc: DesignSpaceDocument, rows, declared, axis_names: Dict[str, str],
                 default_row_name: Optional[str]) -> ET.Element:
    """The designspace 5 ``<mappings>``: one entry per NON-default CSV row."""
    mappings = ET.Element("mappings")
    for row in rows:
        if default_row_name is not None and row.instance_name == default_row_name:
            continue
        mapping = ET.SubElement(mappings, "mapping")
        src = ET.SubElement(mapping, "input")
        for tag, value in sorted(row.in_axes.items()):
            ET.SubElement(src, "dimension", name=_axis_name(tag, axis_names),
                          xvalue=_fmt(value))
        dst = ET.SubElement(mapping, "output")
        for tag, value in sorted(row.out_axes.items()):
            ET.SubElement(dst, "dimension", name=_axis_name(tag, axis_names),
                          xvalue=_fmt(value))
    return mappings


def _axis_name(tag: str, axis_names: Dict[str, str]) -> str:
    """A dimension's ``name`` is the designspace axis NAME, never its tag.

    designspace 5 resolves mapping dimensions by ``axis.name`` (that is what
    ``designspaceLib`` hands fontmake); a tag there makes fontmake log "some
    output axes are not in the variable-font" and drop the mapping, which is
    how E1 first shipped a zip that built a font with no ``avar`` at all.
    The CSV rows carry uppercase tags, so the lookup is case-insensitive.
    """
    return axis_names.get(str(tag).lower(), tag)


def _fmt(value) -> str:
    f = float(value)
    return str(int(f)) if f == int(f) else repr(f)


def _write_designspace(doc: DesignSpaceDocument, path: Path, *, hidden_axes: Iterable[str],
                       mappings: ET.Element) -> None:
    """Write ``doc``, then make it designspace 5: hidden axes + ``<mappings>``.

    fontTools 4.68 READS both (``AxisDescriptor.hidden``,
    ``doc.axisMappings``) but does not write them, so the XML is patched
    rather than hand-rolled — the sources/instances/lib half stays glyphsLib's.
    """
    doc.formatVersion = "5.0"
    doc.write(str(path))
    # glyphsLib hands back one UFO `Font` per master (the sparse brace sources
    # are extra LAYERS of that UFO, referenced by ``layer`` in the XML), so the
    # UFOs are written here: doc.write() only writes the designspace.
    for source in doc.sources:
        if source.font is None or not source.filename:
            continue
        target = path.parent / source.filename
        if target.exists():
            continue
        source.font.save(str(target))

    tree = ET.parse(path)
    root = tree.getroot()
    root.set("format", "5.0")
    hidden = {h.strip() for h in hidden_axes}
    axes_el = root.find("axes")
    if axes_el is not None:
        for axis_el in axes_el.findall("axis"):
            if axis_el.get("tag") in hidden:
                axis_el.set("hidden", "1")
        # designspace 5 puts <mappings> INSIDE <axes> (fontTools reads
        # `.axes/mappings` and writes it there too), after the axis elements.
        axes_el.append(mappings)
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def _readme(family: str, source: Path, *, composed: Sequence[str],
            default_row_name: Optional[str], designspace_name: str,
            spacing: Sequence[str] = ()) -> str:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    parts = list(composed) + list(spacing)
    composed_text = ", ".join(parts) if parts else "nothing"
    default_text = (
        f"The default row ({default_row_name}) is not in the designspace "
        "<mappings>: its input sits at the axis defaults, which map to "
        "themselves. The sources rest at the compiled origin; the studio's TTF "
        "relocates that default at build time "
        "(docs/decisions/0001-build-time-default-relocation.md)."
        if default_row_name else
        "No row sits at the axis defaults, so the sources rest at the compiled "
        "origin; the studio's TTF relocates a default at build time "
        "(docs/decisions/0001-build-time-default-relocation.md).")
    return (
        f"Sources for {family}\n"
        f"==================\n\n"
        f"Written by avar2-studio {_version()} (commit {_commit()}).\n"
        f"Source file: {source.name}\n"
        f"sha256: {digest}\n\n"
        f"Composed into the masters: {composed_text}\n\n"
        f"{default_text}\n\n"
        f"{RESTING_NOTE}\n\n"
        f"Build:\n"
        f"    fontmake -m {designspace_name} -o variable\n"
        f"    # or, through the studio's own toolchain:\n"
        f"    gftools builder {designspace_name}\n"
    )


def build_sources_zip(source_path: Path, out_zip: Path, *,
                      csv_path: Optional[Path] = None,
                      metadata: Optional[Dict[str, Dict]] = None,
                      family_name: Optional[str] = None,
                      hidden_axes: Iterable[str] = (),
                      composed: Sequence[str] = (),
                      kinetikern: Optional[Dict] = None,
                      original_path: Optional[Path] = None,
                      ink_font: Optional[Path] = None) -> Path:
    """Write ``<family>-sources.zip`` next to nothing else: masters, a
    designspace 5 and a README. Returns the zip path.

    ``source_path`` must be the ACTIVE source (the staged shadow when control
    axes exist), so the masters carry the braces the studio composed.
    ``original_path`` is the designer's file: the sidecars that are not already
    passed in (the transforms list, which decides whether a SPAC axis exists)
    live beside IT, never beside the staged copy — the worker stages the active
    source as ``.avar2-studio/active-<name>.glyphs``, so a lookup from
    ``source_path`` finds nothing and the export silently loses the spacing.
    """
    source_path = Path(source_path)
    # Sidecars resolve against the designer's file (ORIGINAL_PATH), never the
    # staged active copy. See the docstring.
    sidecar_base = Path(original_path) if original_path else source_path
    if csv_path is None or not Path(csv_path).exists():
        raise SourcesError("the mappings are not authored yet, so there is "
                           "nothing to map in the designspace")
    font, fmt = _load(source_path)
    if "glyphs" not in str(fmt).lower():
        raise SourcesError(
            "sources can only be written from a .glyphs file; this project's "
            "source is a .designspace")

    family = family_name or getattr(font, "familyName", None) or source_path.stem
    rows = _prepare_rows(config_generator.read_csv_mappings(Path(csv_path)),
                         font, source_path)
    user_axes = declared_user_axes(metadata or {})
    declared = {tag: (float(e["min"]), _default_of(e, tag), float(e["max"]))
                for tag, e in user_axes}

    # E2: the Kinetikern corrections ride the sources as metrics-only braces.
    # The cached font is shared with the build path, so a fresh parse is used
    # here: the export must never leave studio state mutated behind it (a second
    # export with Kinetikern off would otherwise still carry the braces).
    if kinetikern and kinetikern.get("enabled") and kinetikern.get("instances"):
        font = glyphsLib.load(str(source_path))
    _add_kinetikern_braces(font, kinetikern or {}, rows)

    doc = glyphsLib.to_designspace(font)
    # E2j: the studio's compiler decomposes transformed components; the
    # sources carry the same glyph structure so fontmake accepts them and the
    # twins' arithmetic matches the studio's font glyph for glyph.
    _decompose_transformed_sources(doc)
    # E2: spacing rides the sources as a metrics-only SPAC axis rendered from
    # the transforms sidecar, so a fontmake build of the zip is spaced.
    spac_tag = _add_spacing_axis(doc, sidecar_base, ink_font=ink_font)
    # Created user axes: declared min/default/max, appended after the
    # parametric ones glyphsLib emitted from the masters.
    existing = {a.tag.lower() for a in doc.axes}
    for tag, entry in user_axes:
        if tag.lower() in existing:
            continue
        from fontTools.designspaceLib import AxisDescriptor
        axis = AxisDescriptor()
        axis.tag = tag
        axis.name = entry.get("display_name") or tag
        axis.minimum, axis.default, axis.maximum = declared[tag]
        doc.addAxis(axis)
    axis_names = {a.tag.lower(): a.name for a in doc.axes}
    for a in doc.axes:                       # parametric tags are upper-case
        axis_names.setdefault(a.tag.upper().lower(), a.name)

    # Instances = the named rows, at their user-axis locations. A row with a
    # SPAC coordinate pins it, exactly as the studio's font does.
    spac_by_style = {}
    if spac_tag:
        from .csv_io import read_csv_spac_overrides

        spac_by_style = read_csv_spac_overrides(Path(csv_path))
    doc.instances = []
    for row in rows:
        location = {axis_names.get(t, t): float(v)
                    for t, v in row.in_axes.items()}
        if not location:
            continue
        if spac_tag and row.instance_name in spac_by_style:
            location[axis_names.get(spac_tag.lower(), SPAC_NAME)] = float(
                spac_by_style[row.instance_name])
        inst = InstanceDescriptor()
        inst.styleName = row.instance_name
        inst.familyName = family
        inst.location = location
        doc.instances.append(inst)

    default_rows = [r.instance_name for r in rows if _is_default_row(r, declared)]
    if len(default_rows) > 1:
        raise SourcesError(
            "more than one mapping row sits at the axis defaults — the studio "
            "builds this fine only because it dedupes first; fix the mappings")
    _validate_designspace(doc, rows=rows, axis_names=axis_names,
                          default_row_name=default_rows[0] if default_rows else None)

    mappings = _mapping_xml(doc, rows, declared, axis_names,
                            default_rows[0] if default_rows else None)

    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_zip.parent / (out_zip.stem + "-tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        ds_name = f"{family.replace(' ', '')}.designspace"
        ds_path = tmp / ds_name
        _write_designspace(doc, ds_path, hidden_axes=hidden_axes, mappings=mappings)
        (tmp / "README.txt").write_text(
            _readme(family, source_path, composed=composed,
                    default_row_name=default_rows[0] if default_rows else None,
                    designspace_name=ds_name,
                    spacing=_spacing_summary(kinetikern, spac_tag)), encoding="utf-8")
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(tmp.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(tmp).as_posix())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    log.info("sources written to %s (%d rows, %d axes)",
             out_zip.name, len(rows), len(doc.axes))
    return out_zip


def _load(source_path: Path):
    """The parsed source, with the same caching the build path uses."""
    from . import source_font
    return source_font.load_source_cached(Path(source_path))
