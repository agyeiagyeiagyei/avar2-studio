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

import hashlib
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

import glyphsLib
from fontTools.designspaceLib import DesignSpaceDocument, InstanceDescriptor

from .build import config_generator
from .studio_log import get_logger

log = get_logger(__name__)

#: Verbatim, from findings.md ("E1 approved"). The UI shows the same text; a
#: test pins both against this constant.
SPACING_WARNING = (
    "Spacing is not included. The SPAC axis and Kinetikern corrections are "
    "applied to the built font, not to the sources; a font built from these "
    "sources has the source's own spacing.")


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


def declared_user_axes(metadata: Dict[str, Dict]) -> List[Tuple[str, Dict]]:
    """``[(tag, entry)]`` for the user axes the metadata declares.

    Parametric axes are the masters' own business (glyphsLib emits them with
    their master ranges); these are the CREATED axes — wght/wdth/opsz and any
    other the designer declared — whose default the mapping is written against.
    """
    out = []
    for _col, entry in (metadata or {}).items():
        tag = (entry.get("registered_tag") or "").strip()
        if not tag or entry.get("is_parametric"):
            continue
        if entry.get("min") is None or entry.get("max") is None:
            continue
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


def _shift_metrics(glyph, dx: int) -> None:
    """Move the drawing right by ``dx`` (the LSB grows), origin unchanged."""
    for contour in glyph:
        for point in contour:
            point.x += dx
    for component in glyph.components:
        a, b, c, d, ox, oy = component.transformation
        component.transformation = (a, b, c, d, ox + dx, oy)


def _use_my_metrics(glyph) -> bool:
    """True when the compiler takes this composite's metrics from a component.

    ufoLib2 exposes no per-component lib in this version, and the flag is
    glyph-level in both conventions glyphsLib writes, so it is read there.
    """
    lib = glyph.lib or {}
    return bool(lib.get("public.useMyMetrics") or lib.get("com.glyphs.useMyMetrics"))


def _add_spacing_axis(doc: DesignSpaceDocument, source_path: Path) -> Optional[str]:
    """Add ``SPAC`` as metrics-only twin sources (E2).

    Per master and per end of the range, a twin source with the same outlines and
    every inked glyph's sidebearings moved by the enabled transform's per-glyph
    amount, so the axis changes metrics and nothing else. The binary-stage
    injector moves phantom points; this is the same arithmetic at source level —
    a simple glyph takes ``+d`` per side (advance ``+2d``), a composite only its
    left side (advance ``+d``), and a ``USE_MY_METRICS`` composite is left alone
    because it inherits its base's delta.

    The transform is linear in SPAC, so one twin per end defines the axis: at the
    maximum always, and at the minimum when the range is not symmetric.
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
    # its default location, so the renderer measures the default source.
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

    ends = [hi] + ([lo] if abs(lo + hi) > 1e-9 else [])
    twins = []
    # Only the masters get twins: a sparse brace source is an extra layer of its
    # master's UFO and rides along in the deep copy.
    for source in [s for s in doc.sources if not s.layerName]:
        for value in ends:
            twin_font = _copy.deepcopy(source.font)
            for name, ink in inks.items():
                if name not in twin_font:
                    # `.notdef` is in the ink basis but glyphsLib did not write
                    # it; the compiler will synthesise the same box.
                    continue
                glyph = twin_font[name]
                amount = (value if kind == "spac"
                          else value * ((ink / mean_ink) ** bias) * scale)
                dx = round(amount)
                if not dx or dx == 0:
                    continue
                composite = bool(glyph.components)
                if composite and _use_my_metrics(glyph):
                    continue
                _shift_metrics(glyph, dx)
                glyph.width += dx if composite else 2 * dx
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
            default_row_name: Optional[str], designspace_name: str) -> str:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    composed_text = ", ".join(composed) if composed else "nothing"
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
        f"{SPACING_WARNING}\n\n"
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
                      composed: Sequence[str] = ()) -> Path:
    """Write ``<family>-sources.zip`` next to nothing else: masters, a
    designspace 5 and a README. Returns the zip path.

    ``source_path`` must be the ACTIVE source (the staged shadow when control
    axes exist), so the masters carry the braces the studio composed.
    """
    source_path = Path(source_path)
    if csv_path is None or not Path(csv_path).exists():
        raise SourcesError("the mappings are not authored yet, so there is "
                           "nothing to map in the designspace")
    font, fmt = _load(source_path)
    if "glyphs" not in str(fmt).lower():
        raise SourcesError(
            "sources can only be written from a .glyphs file; this project's "
            "source is a .designspace")

    family = family_name or getattr(font, "familyName", None) or source_path.stem
    rows = config_generator.read_csv_mappings(Path(csv_path))
    user_axes = declared_user_axes(metadata or {})
    declared = {tag: (float(e["min"]), _default_of(e, tag), float(e["max"]))
                for tag, e in user_axes}

    doc = glyphsLib.to_designspace(font)
    # E2: spacing rides the sources as a metrics-only SPAC axis rendered from
    # the transforms sidecar, so a fontmake build of the zip is spaced.
    spac_tag = _add_spacing_axis(doc, source_path)
    # Created user axes: declared min/default/max, appended after the
    # parametric ones glyphsLib emitted from the masters.
    existing = {a.tag for a in doc.axes}
    for tag, entry in user_axes:
        if tag in existing:
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
                    designspace_name=ds_name), encoding="utf-8")
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
