"""Glyph-scoped axis coverage (v1 read-only).

A control axis is one whose effect is constrained to a named subset of
glyphs rather than the whole font. ``compute_coverage`` walks the
source's brace layers (``.glyphs``) or alternate-master UFOs
(``.designspace``) and returns, per axis, the names of glyphs that
contribute variation along that axis.

The frontend uses ``covers_count / total_glyphs`` to classify each
axis:

  - ``"universal"`` (100% coverage)      — stays under AVAR2 MAPPINGS /
                                            parametric.
  - ``"scoped"``    (anything less)       — surfaces under CONTROL AXES.

Anything under 100% is "some glyphs, not all" — whether that's a
deliberate case-split (Roboto Delta's XOUC) or an accidental
authoring gap is a designer judgment call, not something a
threshold on this side can decide reliably.

v2 builds authoring on top of this read; the read API itself stays
stable.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from fontTools.designspaceLib import DesignSpaceDocument
from glyphsLib.classes import GSFont


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def compute_coverage(font: object) -> Dict[str, Dict[str, object]]:
    """Return per-axis coverage info, keyed by axis tag.

    Shape::

        {
          "XOPQ": {
            "covers": ["A", "B", ..., "z"],     # sorted glyph names
            "covers_count": 245,
            "total_glyphs": 245,
            "kind": "universal" | "scoped",
            "layers": [                          # per-glyph scoped variation
              {"glyph": "A", "location": {"XOUC": 26.0}},
              ...
            ],
          },
          ...
        }

    ``layers`` mirrors the sidecar's brace-layer shape so the frontend
    can render source-derived scoped axes in the same per-glyph layers
    panel as studio-declared ones (read-only). It records only the
    intermediate contributions — brace layers (``.glyphs``) or
    alternate masters (``.designspace``) — with a sparse location of
    the axes the layer actually deviates on. Master-grid coverage on
    universal axes produces no ``layers`` entries.

    Glyphs without coverage just stay static along the axis at
    render time; they don't appear in any axis's ``covers``.
    """
    if isinstance(font, GSFont):
        return _coverage_from_glyphs(font)
    if isinstance(font, DesignSpaceDocument):
        return _coverage_from_designspace(font)
    raise TypeError(f"Unexpected font type: {type(font).__name__}")


# --------------------------------------------------------------------------
# .glyphs implementation — walk each glyph's layers, look for brace
# layers (layers at intermediate axis positions). glyphsLib normalises
# the older ``{300, 100}`` name convention into
# ``layer.attributes['coordinates']`` as a dict from axis index to value.
# --------------------------------------------------------------------------


def _coverage_from_glyphs(font: GSFont) -> Dict[str, Dict[str, object]]:
    axes = list(font.axes or [])
    if not axes:
        return {}

    # Per-axis master values — used to detect master coverage. If two
    # masters sit at different values on axis X, then every exported
    # glyph is varied along X by the master grid (universal coverage).
    axis_master_values: Dict[int, set] = {i: set() for i in range(len(axes))}
    master_locations = set()
    for master in font.masters:
        master_axes = getattr(master, "axes", None) or []
        master_locations.add(tuple(float(v) for v in master_axes))
        for i in range(len(axes)):
            if i < len(master_axes):
                axis_master_values[i].add(float(master_axes[i]))

    # Axes with master-driven variation. For these the baseline
    # coverage is universal — every exported glyph contributes via
    # the gvar deltas the masters produce.
    master_covered_axes = {
        i for i, vals in axis_master_values.items() if len(vals) > 1
    }

    # Axis defaults — derived from the first master's coordinates,
    # since .glyphs doesn't carry an explicit per-axis default.
    axis_defaults: Dict[int, float] = {}
    if font.masters:
        first = font.masters[0]
        master_axes = getattr(first, "axes", None) or []
        for i in range(len(axes)):
            if i < len(master_axes):
                axis_defaults[i] = float(master_axes[i])

    # First pass: collect total exported glyphs + the exported-glyph
    # names. Master-covered axes get this whole set as their baseline.
    exported_glyph_names = [
        glyph.name for glyph in font.glyphs
        if getattr(glyph, "export", True)
    ]
    total_glyphs = len(exported_glyph_names)

    coverage: Dict[str, set] = defaultdict(set)
    for i, axis in enumerate(axes):
        if i in master_covered_axes:
            coverage[axis.axisTag] = set(exported_glyph_names)

    # Second pass: brace layers can add per-glyph scoped variation on
    # top of master coverage. Only matters when a glyph has a layer at
    # an intermediate (non-master) position — that's a brace layer.
    layers: Dict[str, list] = defaultdict(list)
    seen_layers: set = set()
    # Per-axis value sets in DESIGN space (master values + brace
    # values) — emitted as min/default/max so the frontend can
    # classify layer coverage in the same units the layer locations
    # use, without joining against the built font's fvar.
    axis_values: Dict[str, set] = {
        axes[i].axisTag: set(axis_master_values[i]) for i in range(len(axes))
    }
    for glyph in font.glyphs:
        if not getattr(glyph, "export", True):
            continue
        for layer in glyph.layers:
            loc = _layer_location(layer, len(axes))
            if loc is None or loc in master_locations:
                continue
            # Sparse location: only the axes this brace layer deviates
            # on — same shape the sidecar uses for studio layers.
            sparse = {
                axes[i].axisTag: loc[i]
                for i in range(len(axes))
                if axis_defaults.get(i) is not None and loc[i] != axis_defaults[i]
            }
            loc_key = tuple(sorted(sparse.items()))
            for tag, value in sparse.items():
                axis_values[tag].add(value)
                # .glyphs axes have no user/design split in this
                # pipeline — user space IS design space.
                _record_layer(coverage, layers, seen_layers, tag, glyph.name, sparse, loc_key, sparse)

    tag_defaults = {
        axes[i].axisTag: axis_defaults.get(i) for i in range(len(axes))
    }
    ranges = {
        tag: (
            min(vals) if vals else None,
            tag_defaults.get(tag),
            max(vals) if vals else None,
        )
        for tag, vals in axis_values.items()
    }
    return _shape_result(
        coverage, total_glyphs, {ax.axisTag for ax in axes}, layers, ranges
    )


def _layer_location(layer, num_axes: int) -> Optional[tuple]:
    """Return the layer's axis location as a tuple of floats (one per
    declared axis), or ``None`` if the layer has no explicit axis
    location (i.e. it's a default-master layer).

    glyphsLib normalises brace-layer coordinates in two flavours
    depending on the source version:

      - list:  ``[94, 2, 2, -100]`` — values in axis declaration order
      - dict:  ``{0: 94, 1: 2, 2: 2, 3: -100}`` — keyed by axis index

    Crispy Mini's existing brace layers serialise as lists; the
    layers we author from regenerate_shadow also use list form (the
    Glyphs plist writer rejects dicts with int keys). Handle both
    here so detection works regardless of which form ends up on disk.
    """
    attrs = getattr(layer, "attributes", None) or {}
    coords = attrs.get("coordinates") if hasattr(attrs, "get") else None
    if not coords:
        return None

    out: List[float] = []
    if isinstance(coords, (list, tuple)):
        if len(coords) < num_axes:
            return None
        for i in range(num_axes):
            try:
                out.append(float(coords[i]))
            except (TypeError, ValueError):
                return None
    else:
        # Dict-shaped — keyed by axis index.
        getter = getattr(coords, "get", None)
        if getter is None:
            return None
        for i in range(num_axes):
            v = getter(i)
            if v is None:
                return None
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                return None
    return tuple(out)


# --------------------------------------------------------------------------
# .designspace implementation — for each master at a non-default
# location, the glyphs IT CONTAINS cover the axes whose location values
# differ from the default. We read each UFO's ``glyphs/contents.plist``
# directly to avoid loading the full UFO — it's a single small plist
# file per master.
# --------------------------------------------------------------------------


def _coverage_from_designspace(doc: DesignSpaceDocument) -> Dict[str, Dict[str, object]]:
    if not doc.axes:
        return {}

    axis_defaults = {ax.name: float(ax.default) for ax in doc.axes}
    axis_tag_by_name = {ax.name: ax.tag for ax in doc.axes}
    axis_by_tag = {ax.tag: ax for ax in doc.axes}

    # Find the default master — its glif bytes are the reference we
    # diff against. A non-default master that simply copies the
    # default's glif for a given glyph contributes NO variation on
    # that glyph; we mustn't count it as coverage. (Roboto Delta's
    # case-split UFOs ship a full glyph set with most glyphs being
    # byte-identical to the default master.)
    default_src = _find_default_source(doc, axis_defaults)
    default_glifs = _load_ufo_glif_bytes(Path(default_src.path)) if default_src and default_src.path else {}

    all_glyphs: set = set(default_glifs.keys())
    coverage: Dict[str, set] = defaultdict(set)
    layers: Dict[str, list] = defaultdict(list)
    seen_layers: set = set()

    for src in doc.sources:
        if src is default_src:
            continue
        ufo_path = Path(src.path) if src.path else None
        if not ufo_path or not ufo_path.exists():
            continue

        loc = src.location or {}
        sparse = {
            axis_tag_by_name[axis_name]: float(value)
            for axis_name, value in loc.items()
            if axis_name in axis_defaults
            and float(value) != axis_defaults[axis_name]
        }
        if not sparse:
            continue  # supports / additional masters at default — skip
        loc_key = tuple(sorted(sparse.items()))

        # User-space twin of ``sparse``, for consumers that talk to
        # the COMPILED font or to Fontra — fvar coordinates and
        # Fontra's location bar are user space, while source
        # locations are design space; the two differ whenever the
        # axis carries a <map>. The round-trip check guards against
        # maps that can't be confidently inverted (non-monotonic /
        # flat segments): ``None`` tells consumers to skip
        # location-based navigation rather than land somewhere wrong.
        sparse_user: Optional[Dict[str, float]] = {}
        for tag, value in sparse.items():
            ax_desc = axis_by_tag.get(tag)
            try:
                user = float(ax_desc.map_backward(value))
                # Round-trip alone isn't enough: fontTools' piecewise
                # map extrapolates symmetrically past its end nodes,
                # so a design value outside the mapped range round-
                # trips through an out-of-bounds user value that fvar
                # would clamp. Reject those too.
                if abs(float(ax_desc.map_forward(user)) - value) > 0.01:
                    raise ValueError("map not invertible at this value")
                lo_u = getattr(ax_desc, "minimum", None)
                hi_u = getattr(ax_desc, "maximum", None)
                if lo_u is not None and user < float(lo_u) - 1e-6:
                    raise ValueError("inverts outside the axis range")
                if hi_u is not None and user > float(hi_u) + 1e-6:
                    raise ValueError("inverts outside the axis range")
            except Exception:
                sparse_user = None
                break
            sparse_user[tag] = user

        master_glifs = _load_ufo_glif_bytes(ufo_path)
        all_glyphs.update(master_glifs.keys())

        # A glyph from this master contributes variation iff its glif
        # bytes differ from the default master's glif (or the default
        # master doesn't ship that glyph at all — alternate is the
        # sole source).
        for glyph_name, glif_bytes in master_glifs.items():
            default_bytes = default_glifs.get(glyph_name)
            if default_bytes is not None and glif_bytes == default_bytes:
                continue  # copy of the default — no real variation
            for tag in sparse:
                _record_layer(coverage, layers, seen_layers, tag, glyph_name, sparse, loc_key, sparse_user)

    declared_tags = {ax.tag for ax in doc.axes}
    # Axis extremes in DESIGN space: source locations are design-space,
    # so map the user-space axis min/default/max through the axis map
    # before the frontend compares them against layer locations.
    ranges: Dict[str, tuple] = {}
    for ax in doc.axes:
        minimum = getattr(ax, "minimum", None)
        default = getattr(ax, "default", None)
        maximum = getattr(ax, "maximum", None)
        try:
            fwd = ax.map_forward
            # Design extremes are the min/max over the map's OUTPUT
            # values plus the mapped user extremes — forward(min)/
            # forward(max) alone understate the range whenever the
            # map is non-monotonic.
            candidates = []
            if minimum is not None:
                candidates.append(float(fwd(minimum)))
            if maximum is not None:
                candidates.append(float(fwd(maximum)))
            for _inp, outp in (getattr(ax, "map", None) or []):
                candidates.append(float(outp))
            lo = min(candidates) if candidates else None
            hi = max(candidates) if candidates else None
            ranges[ax.tag] = (
                lo,
                float(fwd(default)) if default is not None else None,
                hi,
            )
        except Exception:
            ranges[ax.tag] = (minimum, default, maximum)
    return _shape_result(coverage, len(all_glyphs), declared_tags, layers, ranges)


def _find_default_source(doc: DesignSpaceDocument, axis_defaults: Dict[str, float]):
    """Return the source whose ``location`` matches the axis defaults
    for every axis. Falls back to ``None`` (the caller treats the
    absence as "no default master found")."""
    for src in doc.sources:
        loc = src.location or {}
        if all(
            axis_name not in loc or float(loc[axis_name]) == default
            for axis_name, default in axis_defaults.items()
        ) and all(
            axis_name in loc for axis_name in axis_defaults
        ):
            return src
    return None


def _load_ufo_glif_bytes(ufo_path: Path) -> Dict[str, bytes]:
    """Return ``{glyph_name: glif_file_bytes}`` for a UFO without
    loading the full UFO. Reads ``glyphs/contents.plist`` to find the
    name→filename map, then slurps each glif as raw bytes for cheap
    equality comparison. Empty dict on failure."""
    contents = ufo_path / "glyphs" / "contents.plist"
    if not contents.exists():
        return {}
    try:
        import plistlib
        with contents.open("rb") as f:
            name_to_filename = plistlib.load(f)
    except Exception:
        return {}
    if not isinstance(name_to_filename, dict):
        return {}

    glifs: Dict[str, bytes] = {}
    glyphs_dir = ufo_path / "glyphs"
    for glyph_name, filename in name_to_filename.items():
        glif_path = glyphs_dir / filename
        try:
            raw = glif_path.read_bytes()
        except OSError:
            continue
        # Canonicalise: strip the <lib> element (editor metadata —
        # mark colour, custom data — that doesn't affect rendering)
        # so two glifs that draw identically but differ only in
        # editor metadata compare equal. Roboto Delta's case-split
        # UFOs ship a public.markColor lib on every glyph that the
        # default UFO lacks — without this strip, every glyph would
        # appear to "differ" and every axis would falsely report
        # universal coverage.
        glifs[glyph_name] = _canonical_glif(raw)
    return glifs


def _canonical_glif(raw: bytes) -> bytes:
    """Return a canonical byte representation of a glif. The
    canonicalisation strips:

      - the ``<lib>`` element (editor-only metadata: mark colour,
        custom data, etc. — doesn't affect rendering)
      - inter-element whitespace in ``text`` / ``tail`` (different
        UFOs serialise with different indentation; Roboto Delta's
        XOUC2 UFO uses tabs inside the closing tag that the default
        UFO doesn't — without normalising whitespace, every glyph
        appears to differ and the axis falsely reports universal
        coverage)

    Falls back to raw bytes on parse failure — the comparison is
    then strict, which is safer than treating unparseable glifs as
    equal.
    """
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
        for lib in list(root.findall("lib")):
            root.remove(lib)
        # Walk every element and normalise whitespace-only text / tail
        # to None. Non-whitespace text (e.g. inside <string> elements
        # if any survived the lib strip — there shouldn't be any in
        # outline/components/anchors/advance/unicode) is preserved.
        for elem in root.iter():
            if elem.text is not None and not elem.text.strip():
                elem.text = None
            if elem.tail is not None and not elem.tail.strip():
                elem.tail = None
        return ET.tostring(root, encoding="utf-8")
    except ET.ParseError:
        return raw


# --------------------------------------------------------------------------
# Shared shaping
# --------------------------------------------------------------------------


def _record_layer(
    coverage: Dict[str, set],
    layers: Dict[str, list],
    seen: set,
    tag: str,
    glyph_name: str,
    sparse: Dict[str, float],
    loc_key: tuple,
    sparse_user: Optional[Dict[str, float]],
) -> None:
    """Register one glyph-layer contribution on ``tag``: coverage
    membership plus a deduped ``layers`` entry. ``loc_key`` is the
    canonical identity of ``sparse`` (its sorted items) — hoisted by
    callers because it's constant per brace layer / alternate master.
    ``sparse_user`` is the user-space twin of ``sparse`` (equal for
    identity maps), or ``None`` when the axis map couldn't be
    inverted — consumers must then skip location-based navigation."""
    coverage[tag].add(glyph_name)
    key = (tag, glyph_name, loc_key)
    if key not in seen:
        seen.add(key)
        layers[tag].append({
            "glyph": glyph_name,
            "location": sparse,
            "location_user": sparse_user,
        })


def _shape_result(
    coverage: Dict[str, set],
    total_glyphs: int,
    declared_tags: set,
    layers: Optional[Dict[str, list]] = None,
    ranges: Optional[Dict[str, tuple]] = None,
) -> Dict[str, Dict[str, object]]:
    """Turn the raw ``axis_tag -> set(glyph_names)`` map into the
    public response, with kind classification and stable ordering.
    Includes axes that have zero coverage (declared but unused) so
    the frontend can list them too. ``ranges`` carries per-tag
    ``(min, default, max)`` in design space when the backend could
    derive them."""
    out: Dict[str, Dict[str, object]] = {}
    for tag in declared_tags:
        names = sorted(coverage.get(tag, set()))
        count = len(names)
        kind = _classify(count, total_glyphs)
        axis_layers = sorted(
            (layers or {}).get(tag, []),
            key=lambda e: (e["glyph"], sorted(e["location"].items())),
        )
        entry: Dict[str, object] = {
            "covers": names,
            "covers_count": count,
            "total_glyphs": total_glyphs,
            "kind": kind,
            "layers": axis_layers,
        }
        rng = (ranges or {}).get(tag)
        if rng is not None:
            entry["min"], entry["default"], entry["max"] = rng
        out[tag] = entry
    return out


def _classify(count: int, total: int) -> str:
    """``universal`` = 100% coverage. ``scoped`` = anything less
    (case-split axis, figure-only axis, crossbar-bearing letters,
    declared-but-unused, or an accidental authoring gap — the
    classifier can't reliably distinguish those and doesn't try).
    """
    if total == 0 or count == total:
        return "universal"
    return "scoped"
