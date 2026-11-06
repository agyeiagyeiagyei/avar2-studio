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

from . import spac_amounts
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


def _ink_widths_from_build(font_path: Path, location=None) -> Dict[str, float]:
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
        # E2i-c (B): instancing the build at a master's own location is the only
        # way the export and the studio's injector read the same ink — the
        # injector can only ever see the built font, never the UFO masters.
        glyph_set = (font.getGlyphSet(location=location) if location
                     else font.getGlyphSet())
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

    Shared with the injector through :mod:`avar2_studio.spac_amounts`, so both
    renderers floor the same corner value to the same integer.
    """
    return spac_amounts.fit_dx(dx, width, composite)


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


class _Layered:
    """Glyph lookup across layers: the sparse one first, then the default.

    `_flatten` and the sparse-layer pass draw through a provider; inside a
    sparse layer a base may live in the default layer, so the provider has to
    see both."""

    def __init__(self, *layers):
        self.layers = layers

    def __getitem__(self, name):
        for layer in self.layers:
            if name in layer:
                return layer[name]
        raise KeyError(name)


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


def _production_to_source(font) -> Dict[str, str]:
    """``{built-font glyph name: UFO glyph name}``, from the source's own lib.

    glyphsLib writes ``public.postscriptNames`` (source → production) into every
    UFO it emits; the compiler applies it, so the built font's glyph order and
    the UFOs' disagree exactly where a glyph has a production name (E2i-b).
    """
    names = (getattr(font, "lib", None) or {}).get("public.postscriptNames") or {}
    out: Dict[str, str] = {}
    for source_name, production in names.items():
        out[str(production)] = str(source_name)
    return out


def _decompose_transformed_sources(doc: DesignSpaceDocument) -> int:
    """Give every source the same glyph structure, as the studio's build does.

    Three shapes are normalised away, all because fontmake refuses a designspace
    whose masters disagree and because the studio's fontc build flattens them
    anyway:

    * a component that is more than a translation (the flipped ``w``/``W``),
      which fontc's ``--decompose-transformed-components`` flattens;
    * a MIXED glyph — components AND contours (``Q`` = ``O`` + a tail,
      ``cent`` = ``c`` + a bar): a TrueType composite cannot carry its own
      contours, so every compiler decomposes the components and the glyph
      tracks as a simple one (E2j-b);
    * a glyph that is a composite in some sources and contours in others
      (``colon``), which no compiler can interpolate.

    Plain composites stay composites: the injector's composite rule depends on
    them.
    """
    signatures: Dict[str, set] = {}
    bases_by_glyph: Dict[str, set] = {}
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
                bases_by_glyph.setdefault(name, set()).update(
                    c.baseGlyph for c in glyph.components)
                if any(_is_transformed(c.transformation)
                       for c in glyph.components):
                    targets.add(name)
                if glyph.components and glyph.contours:
                    targets.add(name)
    targets |= {name for name, seen in signatures.items() if len(seen) > 1}

    # nine's model has to be read BEFORE the flatten pass, which removes the
    # components it interpolates (advance and transform, per master).
    composite_models = {name: _composite_model(doc, name) for name in targets}
    mixed_models = {name: _mixed_model(doc, name) for name in targets}

    done = 0
    originals: Dict[str, object] = {}
    for source in doc.sources:
        font = source.font
        if font is None:
            continue
        for layer in font.layers:
            for name in list(layer.keys()):
                if name in targets:
                    # Keep the glyph WITH its components/own contours: the
                    # sparse-layer pass rebuilds from this, not from the
                    # already flattened copy.
                    originals.setdefault(name, copy.deepcopy(layer[name]))
                if name in targets and _flatten(layer[name], layer, name):
                    done += 1
    if done:
        log.info("sources_export: %d components decomposed (%d glyphs)",
                 done, len(targets))

    # A decomposed glyph must exist wherever one of its bases has a sparse
    # layer, or fontmake interpolates it from the masters alone and drops that
    # correction: `nine` is a 180°-rotated `six`, `six` carries the grade
    # braces, and `nine` had none, so its lsb drifted 13 u at the 900 rows.
    #
    # The layer takes NINE's own advance and component transform, interpolated
    # from nine's OWN masters at that location, with the base's layer outline
    # under that transform (verifier, 2026-11-06 03:15 — copying nine's value
    # from its parent master, 127, was the earlier attempt's error).
    added = 0
    unbuilt: Dict[str, str] = {}
    for source in doc.sources:
        if source.font is None or not source.layerName:
            continue
        layer = source.font.layers[source.layerName]
        default = source.font.layers.defaultLayer
        if layer is None or layer is default:
            continue
        present = set(layer.keys())
        loc = _design_norm(doc, source.location or {})
        # Iterate the TARGETS, not the default layer's keys: a master need not
        # carry every glyph (nine is missing from some masters outright).
        for name in sorted(targets):
            if name in present:
                continue
            bases = bases_by_glyph.get(name, set()) & present
            if not bases:
                continue
            composed = composite_models.get(name)
            if composed is not None:
                model, series = composed
                dx_width = model.interpolateFromMasters(
                    loc, list(series["width"]))
                dx_tx = model.interpolateFromMasters(loc, list(series["tx"]))
                dx_ty = model.interpolateFromMasters(loc, list(series["ty"]))
                glyph = layer.newGlyph(name)
                provider = _Layered(layer, default)
                from fontTools.pens.pointPen import SegmentToPointPen
                from fontTools.pens.recordingPen import DecomposingRecordingPen
                from fontTools.pens.transformPen import TransformPen

                recording = DecomposingRecordingPen(provider)
                provider[sorted(bases)[0]].draw(recording)
                recording.replay(TransformPen(
                    SegmentToPointPen(glyph.getPointPen()),
                    (series["matrix"][0], series["matrix"][1],
                     series["matrix"][2], series["matrix"][3], dx_tx, dx_ty)))
                glyph.width = round(dx_width)
                added += 1
                continue

            # MIXED glyphs (`cent` = `c` + its own bar, `Q` = `O` + a tail): the
            # layer is the glyph's OWN contours interpolated from its own
            # masters, its component under the transform interpolated at L, and
            # its own interpolated advance. Built from the pre-flatten glyph so
            # the contour order matches the masters (verifier, 2026-11-06).
            mixed = mixed_models.get(name)
            if mixed is None:
                # A glyph whose base carries a layer but which the models could
                # not rebuild drifts exactly like `cent` did — say so instead of
                # skipping in silence (verifier, 2026-11-06 12:24).
                unbuilt.setdefault(name, _unbuilt_reason(originals.get(name)))
                continue
            model, point_sets, xforms, widths = mixed
            own = []
            for ci, contour in enumerate(point_sets[0]):
                rebuilt = []
                for pi in range(len(contour)):
                    x = model.interpolateFromMasters(
                        loc, [sets[ci][pi][0] for sets in point_sets])
                    y = model.interpolateFromMasters(
                        loc, [sets[ci][pi][1] for sets in point_sets])
                    rebuilt.append((x, y, contour[pi][2], contour[pi][3]))
                own.append(rebuilt)
            parts = [[model.interpolateFromMasters(
                loc, [xf[ci][i] for xf in xforms]) for i in range(6)]
                for ci in range(len(xforms[0]))]
            pieces = copy.deepcopy(originals[name])
            pieces.clearContours()
            pen = pieces.getPointPen()
            for rebuilt in own:
                pen.beginPath()
                for x, y, segment, smooth in rebuilt:
                    pen.addPoint((x, y), segmentType=segment, smooth=smooth)
                pen.endPath()
            for component, transform in zip(pieces.components, parts):
                component.transformation = tuple(transform)
            pieces.width = round(model.interpolateFromMasters(loc, widths))
            layer.insertGlyph(pieces, name)
            _flatten(layer[name], _Layered(layer, default), name)
            added += 1
    if unbuilt:
        listed = ", ".join(f"{name} ({reason})"
                           for name, reason in sorted(unbuilt.items())[:12])
        more = (f" and {len(unbuilt) - 12} more" if len(unbuilt) > 12 else "")
        log.warning("sources_export: %d glyph(s) have a base with sparse layers "
                    "but got no layer of their own, so they drift at those "
                    "locations: %s%s", len(unbuilt), listed, more)
    if added:
        log.info("sources_export: %d sparse layers added for decomposed glyphs",
                 added)
    return done


def _unbuilt_reason(glyph) -> str:
    """Why the sparse pass could not build a layer for this glyph.

    A glyph whose base carries sparse layers must get its own layer at those
    locations; when the models decline, the glyph drifts there (that is how
    `cent` missed 1,128 u). The reason goes into the build log so a designer can
    act on it instead of discovering the drift in the font.
    """
    if glyph is None:
        return "not in the masters"
    components = len(getattr(glyph, "components", []) or [])
    if components and not glyph.contours:
        return f"{components} component(s), not one transformed component"
    if components:
        return "own contours + components, masters incompatible"
    return "neither own contours nor a component"


def _mixed_model(doc, name: str):
    """Own contour points, component transforms and advance across the masters.

    A MIXED glyph keeps contours of its own (`cent` = `c` + a bar, `Q` = `O` + a
    tail); when one of its bases has a sparse layer, that layer must still carry
    the glyph's OWN shape, interpolated from its own masters, or the layer pins
    the whole glyph (verifier, 2026-11-06 10:02: cent missed `c`'s LCWD layers
    by up to 1,128 u).

    Every component takes its OWN interpolated transform — a glyph built from two
    different bases would otherwise hold one of them at the base master's
    position. All masters must agree on the component list, or the glyph cannot
    be interpolated at all and the caller reports it.
    """
    from fontTools.varLib.models import VariationModel

    locations, point_sets, xforms, widths = [], [], [], []
    bases = None
    for source in doc.sources:
        if source.layerName or source.font is None:
            continue
        default = source.font.layers.defaultLayer
        if name not in default:
            continue
        glyph = default[name]
        if not glyph.contours or not glyph.components:
            continue
        points = [[(p.x, p.y, p.type, p.smooth) for p in contour.points]
                  for contour in glyph.contours]
        if point_sets and [len(c) for c in points] != [len(c) for c in point_sets[0]]:
            return None                        # incompatible masters
        names = [c.baseGlyph for c in glyph.components]
        if bases is None:
            bases = names
        elif names != bases:
            return None                        # components differ between masters
        locations.append(_design_norm(doc, source.location or {}))
        point_sets.append(points)
        xforms.append(tuple(tuple(c.transformation) for c in glyph.components))
        widths.append(float(glyph.width))
    if len(locations) < 2 or not point_sets:
        return None
    if not any(all(abs(v) < 1e-9 for v in loc.values()) for loc in locations):
        return None                            # the model needs a base
    model = VariationModel(locations, axisOrder=sorted(
        {tag for loc in locations for tag in loc}))
    return model, point_sets, xforms, widths


def _design_norm(doc, location: Dict[str, float]) -> Dict[str, float]:
    """A source location in the designspace's own normalised space."""
    out = {}
    for axis in doc.axes:
        value = location.get(axis.name)
        if value is None or axis.default is None:
            continue
        lo, df, hi = axis.minimum, axis.default, axis.maximum
        span = (hi - df) if value > df else (df - lo)
        out[axis.tag] = ((value - df) / span) if span else 0.0
    return out


def _composite_model(doc, name: str):
    """Interpolate one glyph's own advance and single-component transform.

    Returns ``(model, series)`` or None when the glyph is not a plain
    transformed component in every master. ``series`` carries the per-master
    values and the constant rotation matrix."""
    from fontTools.varLib.models import VariationModel

    locations, widths, xs, ys, matrices = [], [], [], [], []
    for source in doc.sources:
        if source.layerName or source.font is None:
            continue
        default = source.font.layers.defaultLayer
        if name not in default:
            continue
        glyph = default[name]                    # a master without it is not a
        if len(glyph.components) != 1:           # corner of this glyph's model
            continue
        if glyph.contours:
            # A MIXED glyph (Q = O + a tail) cannot be rebuilt from its base's
            # outline: the tail belongs to the glyph itself.
            continue
        if not _is_transformed(glyph.components[0].transformation):
            continue
        a, b, c, d, tx, ty = glyph.components[0].transformation
        locations.append(_design_norm(doc, source.location or {}))
        widths.append(float(glyph.width))
        xs.append(float(tx))
        ys.append(float(ty))
        matrices.append((a, b, c, d))
    if not locations or len(set(matrices)) != 1:
        return None
    if not any(all(abs(v) < 1e-9 for v in loc.values()) for loc in locations):
        return None                              # the model needs a base
    model = VariationModel(locations, axisOrder=sorted(
        {tag for loc in locations for tag in loc}))
    return model, {"width": widths, "tx": xs, "ty": ys,
                   "matrix": matrices[0]}


#: Diagnostics from the last `_shift_spacing` calls: one dict per (glyph,
#: master) the 1 u floor had to move. Read by test-output/e2ic_classify_export.
CAPPED_MOVES: list = []


def _ink_of(glyph) -> float:
    """Outline bbox width of one UFO glyph (no composites to resolve here)."""
    if not glyph.contours:
        return 0.0
    xs = [point.x for contour in glyph.contours for point in contour.points]
    return float(max(xs) - min(xs)) if xs else 0.0


def _lsb_of(glyph) -> float:
    if not glyph.contours:
        return 0.0
    xs = [point.x for contour in glyph.contours for point in contour.points]
    return float(min(xs)) if xs else 0.0


def _shift_spacing(twin, original, inks: Dict[str, float], mean_ink: float, *,
                   kind: str, value: float, bias: float, scale: float,
                   mean_default: Optional[float] = None) -> int:
    """Move one twin's metrics as the injector moves its phantom points.

    Simple glyph: ``advance + 2·dx`` with the drawing moved right by ``dx``, so
    both sidebearings open equally. A composite takes its BASE letter's amount
    on both sides (E2k, the user's rule): its ink moves by ``dx_base`` and its
    advance by ``2·dx_base``, exactly like the base — so each component's offset
    is corrected by the shift its base already carries, and the composite's own
    ink-derived amount is not used. A composition the studio's build decomposes
    (transformed, or mixed components+contours) is flattened first and then
    tracks like a simple glyph with its own amount.

    E2i-c will make ``amount`` depend on the master's own ink; the base rule is
    written against ``shifts`` so it follows that change.
    """
    mean_D = mean_default if mean_default else mean_ink
    capped = 0
    shifts: Dict[str, int] = {}
    pending: Dict[str, bool] = {}
    for name, ink in inks.items():
        if name not in twin:
            if name == ".notdef":
                # The sources carry no `.notdef`; the compiler synthesises the
                # box, and the zip's README says it is not spaced.
                continue
            raise SourcesError(
                f"the built font measures {name!r}, but the sources have no "
                "glyph by that name — the spacing would silently skip it "
                "(E2i-b). Check the production names (public.postscriptNames).")
        glyph = twin[name]
        _decompose_transformed(glyph, original, name)
        if glyph.components and glyph.contours:
            # A mixed glyph cannot compile as a composite: flatten it, as the
            # studio's build does, so it tracks like a simple one (E2j-b).
            _flatten(glyph, original, name)
        composite = bool(glyph.components)
        if composite:
            pending[name] = True
            continue
        # The rule's own integer, before the tight-end floor, so the capped-move
        # record can name both. The floor itself lives in spac_amounts, shared
        # with the injector so the two renderers cannot round apart.
        rule = (round(value) if kind == "spac"
                else round(value * ((ink / mean_ink) ** bias)
                           * (mean_ink / mean_D) * scale))
        dx = (spac_amounts.fit_dx(rule, glyph.width, composite)
              if kind == "spac"
              else spac_amounts.per_side(value, ink, mean_ink, mean_D, bias,
                                         scale, glyph.width, composite))
        if dx < 0:
            before = rule
            if dx != before:
                capped += 1
                ink_w = _ink_of(glyph)
                lsb = _lsb_of(glyph)
                CAPPED_MOVES.append({
                    "glyph": name,
                    "kind": kind,
                    "value": value,
                    "advance": glyph.width,
                    "ink": ink_w,
                    "lsb": lsb,
                    "rsb": glyph.width - lsb - ink_w,
                    "rule_dx": before,
                    "floored_dx": dx,
                    "rule_advance": glyph.width + 2 * before,
                    "floored_advance": glyph.width + 2 * dx,
                    "mean_L": mean_ink,
                    "mean_D": mean_D,
                    "ink_used": ink,
                })
        if dx:
            shifts[name] = dx

    # Composites: the base letter's amount, on both sides (E2k). ufoLib2 exposes
    # `useMyMetrics` per GLYPH, not per component, so the base is the first
    # component — which is the flagged one in every composite glyphsLib writes
    # (`Aacute` = `A` + `acute`). Resolved in dependency order so a composite of
    # a composite follows the same rule.
    remaining = dict(pending)
    while remaining:
        progressed = False
        for name in list(remaining):
            components = twin[name].components
            if not components:
                remaining.pop(name)
                progressed = True
                continue
            base = components[0].baseGlyph
            if base in remaining:
                continue
            base_dx = shifts.get(base, 0)
            if base_dx:
                shifts[name] = base_dx
            remaining.pop(name)
            progressed = True
        if not progressed:      # a component cycle; leave the rest unspaced
            break

    for name in set(shifts) | set(pending):
        dx = shifts.get(name, 0)
        glyph = twin[name]
        if glyph.components:
            # Every component is corrected by the difference between THIS
            # composite's amount and the shift its own base already carries —
            # including when the composite's amount is zero. `IJ` is the case:
            # its base `I` is a hairline stem whose amount rounds to 0, while
            # its `J` component moved by J's full amount, so the right side
            # collapsed by 63 u against the studio's 0 (E2k: a composite's ink
            # tracks its base).
            for component in glyph.components:
                base_shift = shifts.get(component.baseGlyph, 0)
                a, b, c, d, ox, oy = component.transformation
                component.transformation = (a, b, c, d, ox + dx - base_shift, oy)
            glyph.width += 2 * dx
        else:
            _shift_metrics(glyph, dx)
            glyph.width += 2 * dx
    return capped


def _injected_axis_tags(source_path: Path) -> set:
    """The fvar tags the enabled transforms add (``slnt``, ``ROND``, ``SPAC``…).

    E2i-c resolves a derived master's parent by LOCATION, which needs to know
    which axes the transforms created: those are the axes a derived master
    moves off its default, and they are excluded from the location match."""
    from .transforms import config as _tx_config
    from .transforms import registry as _tx_registry

    tags = set()
    for entry in _tx_config.entries(Path(source_path)):
        if not entry.get("enabled"):
            continue
        transform = _tx_registry.REGISTRY.get(str(entry.get("type", "")))
        tag = getattr(getattr(transform, "spec", None), "injected_axis_tag", None)
        if tag:
            tags.add(tag)
    return tags


def _parent_master(index: int, location: Dict[str, float],
                   measured: List[Tuple[object, Dict[str, float], Dict[str, float]]],
                   injected: set, defaults: Dict[str, float],
                   style_name: str) -> Optional[int]:
    """The master ``index`` descends from, or None when it is not derived.

    E2i-c (user decision): slanted and rounded styles take their parent
    master's spacing amounts — a sheared bounding box is not the glyph's width
    (on Crispy the slanted `colon` measures 248 u of ink against 14 upright,
    which drove its amount into the 1 u floor). The injector already overrides
    the masters it derives; the export must agree.

    The parent resolves by LOCATION — the master at the defaults of every
    transform-injected axis that agrees on all the others — so neither the
    style names nor the order of ``doc.sources`` can change the answer, and a
    designer's own master called "… Rounded" is left alone. A derived master
    with no parent raises: keeping its own sheared measurement is exactly the
    behaviour this rule replaced.
    """
    def at(where: Dict[str, float], tag: str) -> float:
        return float(where.get(tag, defaults.get(tag, 0.0)))

    derived_on = {t for t in injected if at(location, t) != defaults.get(t, 0.0)}
    if not derived_on:
        return None                       # the designer made this master
    matches = [
        other for other, (_, other_location, _) in enumerate(measured)
        if other != index
        # the parent sits at the injected axes' defaults ...
        and not [t for t in injected if at(other_location, t) != defaults.get(t, 0.0)]
        # ... and agrees on every axis the transforms did not inject.
        and all(at(other_location, t) == at(location, t)
                for t in (set(location) | set(other_location)) - injected)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SourcesError(
            f"the derived master {style_name!r} has no parent: no master sits "
            f"at the default of {', '.join(sorted(derived_on))} at the same "
            f"location on the other axes, so its spacing amounts are unknown "
            f"(the export does not guess from style names)")
    names = [repr(measured[other][0].styleName or measured[other][0].name)
             for other in matches]
    raise SourcesError(
        f"the derived master {style_name!r} matches {len(matches)} candidate "
        f"parents ({', '.join(names)}) — the source locations are ambiguous")


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
    default_source = next(
        (s for s in doc.sources if not s.layerName), doc.sources[0])
    renames: Dict[str, str] = {}
    if ink_font is not None and Path(ink_font).exists():
        inks = _ink_widths_from_build(Path(ink_font))
        # E2i-b: the built font speaks PRODUCTION names (`Euro`, `uni0162`), the
        # UFOs speak source names (`euro`, `Tcedilla`). Without this the spacing
        # skipped every renamed glyph and its composites, silently.
        renames = _production_to_source(default_source.font)
        remapped: Dict[str, float] = {}
        for name, ink in inks.items():
            source_name = renames.get(name, name)
            if source_name in remapped:
                raise SourcesError(
                    f"the built font's {name!r} and another glyph both map to "
                    f"the source glyph {source_name!r}")
            remapped[source_name] = ink
        basis = remapped
        build_font = Path(ink_font)

        def _inks_from_build(location):
            """This master's ink, read from the BUILD at that master's
            location — the injector's own measurement (E2i-c, option B)."""
            out: Dict[str, float] = {}
            for name, ink in _ink_widths_from_build(build_font,
                                                    location).items():
                out.setdefault(renames.get(name, name), ink)
            return out
    else:
        basis = _ink_widths(default_source.font)
        _inks_from_build = None

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
    # E2i-c: the amount scales with EACH MASTER's own ink, so both means are
    # taken over the same glyph set — the sources' names, minus `.notdef` (the
    # sources carry none; the compiler synthesises the box). `mean_D` is the
    # default design's, from the built font when there is one.
    names = [n for n in basis if n in default_source.font and n != ".notdef"]
    mean_D = (sum(basis[n] for n in names) / len(names)) if names else 0.0
    capped_total = 0
    twins = []
    # Only the masters get twins: a sparse brace source is an extra layer of its
    # master's UFO and rides along in the deep copy.
    tags_by_name = {axis.name: axis.tag for axis in doc.axes}

    # (source, location by tag, ink widths) for every master, measured before
    # any parent is resolved so the order of doc.sources cannot change the
    # answer (E2i-c).
    measured: List[Tuple[object, Dict[str, float], Dict[str, float]]] = []
    for source in [s for s in doc.sources if not s.layerName]:
        location = {tags_by_name[name]: float(value)
                    for name, value in (source.location or {}).items()
                    if tags_by_name.get(name)}
        if _inks_from_build is not None:
            # (B) measure the build at THIS master; (A) the master's UFO is the
            # fallback only when there is no build to instance.
            inks = _inks_from_build(location)
        else:
            inks = _ink_widths(source.font)
        measured.append((source, location, inks))

    injected = _injected_axis_tags(source_path)
    defaults = {a.tag: float(a.default or 0.0) for a in doc.axes}
    derived, parents = 0, set()
    for index, (source, location, inks) in enumerate(measured):
        parent = _parent_master(index, location, measured, injected, defaults,
                                source.styleName or source.name)
        if parent is not None:
            derived += 1
            parents.add(parent)
            inks = measured[parent][2]
        master_inks = {n: w for n, w in inks.items() if n in names}
        mean_L = ((sum(master_inks.values()) / len(master_inks))
                  if master_inks else mean_D)
        for value in ends:
            twin_font = _copy.deepcopy(source.font)
            capped_total += _shift_spacing(
                twin_font, source.font, master_inks, mean_L, kind=kind,
                value=value, bias=bias, scale=scale, mean_default=mean_D)
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
    if derived:
        log.info("sources_export: %d derived masters take their parents' "
                 "spacing amounts (%d parents, by location)", derived, len(parents))
    if capped_total:
        # E2i-c made the amount proportional to each master's own ink, so a
        # tightening that outruns a glyph's advance should no longer happen.
        # Say so loudly if it ever does: the cap distorts every location.
        log.warning("sources_export: %d per-glyph moves still hit the "
                    "negative-advance cap (E2i-c should have removed them)",
                    capped_total)
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
    # E1c: the composed list is the writer's, read from the same sidecar the
    # build reads — so the CLI's zip and the dialog's zip say the same thing
    # instead of each caller describing its own view.
    from .transforms import config as _tx_config

    composed = [e.get("type") for e in _tx_config.entries(sidecar_base)
                if e.get("enabled")]
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
        # E1c: the hash and the name in the README are the DESIGNER's file, not
        # the staged shadow the masters were read from.
        (tmp / "README.txt").write_text(
            _readme(family, sidecar_base, composed=composed,
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
