"""Shadow ``.glyphs`` regeneration: seeding, virtual masters and outline
capture (split from the ``control_axes`` module, audit §4).
"""

from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

from ..studio_log import get_logger
from . import naming, outlines, schema

log = get_logger(__name__)


# --------------------------------------------------------------------------
# Shadow source file: the studio never writes the original; with at least
# one control axis it derives ``<original-dir>/.avar2-studio/shadow/<basename>``
# from original + sidecar and builds from that. See
# docs/guides/secondary-parametric-axes.md#source-format-scope.
# --------------------------------------------------------------------------


def shadow_dir_for(original_path: Path) -> Path:
    """Per-source shadow directory: sibling to the original under the
    studio's existing ``.avar2-studio/`` workdir."""
    return original_path.parent / ".avar2-studio" / "shadow"


def shadow_path_for(original_path: Path) -> Path:
    """Path to the shadow source file. Same basename as the original
    so external tooling (Glyphs.app, fontmake) treats the two as
    structurally identical."""
    return shadow_dir_for(original_path) / original_path.name


def shadow_exists(original_path: Path) -> bool:
    return shadow_path_for(original_path).exists()


def regenerate_shadow(original_path: Path) -> Optional[Path]:
    """Derive the shadow from ``original + sidecar``:

      1. Wipe and re-copy the original to the shadow path.
      2. Mutate the shadow .glyphs file via glyphsLib to add every
         control axis from the sidecar that isn't already in the
         font's axis list. Each master's coordinate vector is
         extended with the new axis's default value, since masters
         sit at the axis default until brace layers exist.

    Returns the shadow path on success, ``None`` if regeneration
    isn't applicable (e.g., sidecar is empty — caller should treat
    that as "no shadow needed"). Raises on hard failures so the
    HTTP layer can 500.
    """
    axes_to_add = schema.list_axes(original_path)
    if not axes_to_add:
        # Sidecar empty — caller can remove any pre-existing shadow.
        return None

    suffix = original_path.suffix.lower()
    if suffix == ".designspace":
        from . import shadow_designspace
        return shadow_designspace._regenerate_shadow_designspace(original_path, axes_to_add)
    if suffix != ".glyphs":
        return None

    shadow_path = shadow_path_for(original_path)
    shadow_dir_for(original_path).mkdir(parents=True, exist_ok=True)

    # Preserve outlines the designer has already drawn in the
    # previous shadow (model β-with-best-effort during the v2.5a
    # window — model α with full sidecar back-capture is a later
    # slice). We read the existing shadow BEFORE the copy-from-
    # original wipes it, then merge any matching brace-layer
    # outlines back in after the axes get re-applied.
    preserved_layers = _extract_brace_outlines(shadow_path) if shadow_path.exists() else {}
    # (glyph, location) of every layer we SEED this run — stamped after the
    # save, from the persisted geometry. See _restamp_seeds.
    seeded_keys: set = set()

    # Always re-copy from original. The shadow is fully derived;
    # incremental updates would just multiply the bug surface.
    shutil.copy2(original_path, shadow_path)

    # Mutate the shadow's axis list. We import glyphsLib lazily so
    # this module stays importable in non-.glyphs contexts (and unit
    # tests don't need to pull glyphsLib for sidecar-only tests).
    from glyphsLib import GSFont
    from glyphsLib.classes import GSAxis, GSLayer

    font = GSFont(str(shadow_path))
    # Studio-side instance renames survive regeneration: the shadow is
    # re-derived from the original (which keeps its own names), so the
    # alias map re-applies the user's renames on every rebuild.
    from .. import instance_aliases
    instance_aliases.apply_to_font(font, instance_aliases.load(original_path))
    existing_tags = {str(getattr(ax, "axisTag", "")).lower() for ax in font.axes}

    # Each control axis's index in the eventual axis list (so we can
    # find the right slot when writing brace-layer coordinates).
    axis_index_by_tag: Dict[str, int] = {}
    # Newly-declared control axes needing a Virtual Master so glyphsLib
    # gives them a real range (see the Virtual Master block below).
    new_control_axes: List[Dict] = []

    for spec in axes_to_add:
        tag = (spec.get("tag") or "").strip()
        if not tag:
            continue
        if tag.lower() in existing_tags:
            # Pre-existing source axis — find its index for brace
            # layer indexing.
            for i, src_ax in enumerate(font.axes):
                if str(getattr(src_ax, "axisTag", "")).lower() == tag.lower():
                    axis_index_by_tag[tag] = i
                    break
            continue

        new_axis = GSAxis()
        new_axis.axisTag = tag
        new_axis.name = spec.get("display_name") or tag
        font.axes.append(new_axis)
        axis_index_by_tag[tag] = len(font.axes) - 1
        existing_tags.add(tag.lower())

        default_value = float(spec.get("default", 0))
        new_control_axes.append({
            "name": new_axis.name,
            "index": axis_index_by_tag[tag],
            "min": float(spec.get("min", default_value)),
            "max": float(spec.get("max", default_value)),
            "default": default_value,
        })
        for master in font.masters:
            coords = list(getattr(master, "axes", None) or [])
            coords.append(default_value)
            master.axes = coords

        # Pad EXISTING brace-layer coordinates with the new axis's
        # default. Without this, every old brace in the source
        # ships a shorter coords list than the (now-larger) axis
        # count, and Glyphs/Fontra projects them onto the lower-D
        # subspace — which collides with the brace layers we
        # author at the same N-D projection. User-visible symptom:
        # Fontra's Glyph Sources panel shows "locations must be
        # unique" with multiple duplicate rows at master corners.
        for glyph in font.glyphs:
            for layer in glyph.layers:
                attrs = getattr(layer, "attributes", None)
                if attrs is None:
                    continue
                coords = attrs.get("coordinates") if hasattr(attrs, "get") else None
                if not isinstance(coords, list):
                    continue
                while len(coords) < len(font.axes):
                    coords.append(default_value)
                attrs["coordinates"] = coords

    # Virtual Masters — the crux of making a control axis EDITABLE in
    # Fontra. A control axis only varies via brace layers; no master
    # spans it (every master sits at the axis default). glyphsLib
    # derives axis ranges from master coordinates, so such an axis
    # comes out as [default, default] — zero width — and fontra-glyphs
    # then DROPS it. With the axis gone, brace coordinates mis-map and
    # several layers collapse onto the same location, which Fontra
    # demotes to non-editable *background* layers and flags as
    # "locations must be unique". A Virtual Master declares the axis
    # extreme(s) without a full master, giving glyphsLib a real range
    # so the axis survives into Fontra and each brace becomes a
    # distinct, editable source. One VM per extreme that differs from
    # the axis default (edge-default axes need only the far extreme;
    # interior-default axes need both). fontc reads the range from the
    # axis declaration directly, so the preview build is unaffected.
    if font.masters and new_control_axes:
        from glyphsLib.classes import GSCustomParameter
        base_coords = list(getattr(font.masters[0], "axes", None) or [])
        axis_labels = [
            str(getattr(a, "name", "") or getattr(a, "axisTag", "")) for a in font.axes
        ]
        for cax in new_control_axes:
            idx = cax["index"]
            extremes = []
            if cax["min"] < cax["default"]:
                extremes.append(cax["min"])
            if cax["max"] > cax["default"]:
                extremes.append(cax["max"])
            for extreme in extremes:
                point = list(base_coords)
                if idx < len(point):
                    point[idx] = extreme
                value = [
                    {"Axis": axis_labels[i], "Location": point[i]}
                    for i in range(min(len(axis_labels), len(point)))
                ]
                font.customParameters.append(
                    GSCustomParameter("Virtual Master", value)
                )

    # Tag → axis-index map across the full final axis list. Used to
    # resolve sparse {axis_tag: value} dicts from the unified
    # ``layers`` list into full N-D coordinate vectors.
    full_axis_index_by_tag: Dict[str, int] = {}
    for i, ax in enumerate(font.axes):
        full_axis_index_by_tag[str(getattr(ax, "axisTag", "")).lower()] = i
    # Labels print the AUTHORED tag case ("YMOD 100", not "ymod 100");
    # the lowercased keys above stay for case-insensitive matching.
    tag_by_index = {i: str(getattr(ax, "axisTag", "")) for i, ax in enumerate(font.axes)}

    # A brace layer is a full point: a parametric location × the
    # control-axis value. Label it by the master corner or named instance
    # at that location ("XTRA3330-XOPQ2-YOPQ2", "Narrow 900 MaxOpsz"); a
    # location matching neither keeps the control-axis coordinates so the
    # row stays identifiable.
    control_axis_indices = set()
    for spec in axes_to_add:
        t = str(spec.get("tag") or "").strip().lower()
        if t in full_axis_index_by_tag:
            control_axis_indices.add(full_axis_index_by_tag[t])
    corner_name_by_parametric, instance_name_by_parametric = naming.corner_name_maps(
        font, control_axis_indices, original_path
    )

    def _corner_name(loc):
        param_key = tuple(
            round(float(loc[i]), 1)
            for i in range(len(loc))
            if i not in control_axis_indices
        )
        return (corner_name_by_parametric.get(param_key)
                or instance_name_by_parametric.get(param_key))

    def _brace_source_label(loc):
        corner = _corner_name(loc)
        if corner:
            return corner
        return ", ".join(
            f"{tag_by_index.get(i, i)} {naming._fmt_coord(loc[i])}"
            for i in sorted(control_axis_indices)
            if i < len(loc)
        )

    # Parametric interpolation model. A seeded brace should start as the
    # glyph's NATURAL shape at its parametric location — not a copy of
    # the default master. Without this, a brace at a non-default
    # parametric corner shows the (thin) default-master outline, which
    # reads as "same as default" in Fontra until edited. We build a
    # variation model over the real masters' parametric coordinates
    # (control axes excluded — no masters span them) and interpolate
    # node positions at the brace's location. Exact at a master corner;
    # interpolated in between.
    from fontTools.varLib.models import VariationModel, normalizeLocation

    param_axis_indices = [
        i for i in range(len(font.axes)) if i not in control_axis_indices
    ]
    _param_triples = {}
    for i in param_axis_indices:
        vals = [
            float(m.axes[i])
            for m in font.masters
            if i < len(getattr(m, "axes", None) or [])
        ]
        if vals:
            _param_triples[i] = (min(vals), float(font.masters[0].axes[i]), max(vals))

    def _param_norm(coords):
        loc = {}
        for i in param_axis_indices:
            lo, dflt, hi = _param_triples.get(i, (0.0, 0.0, 0.0))
            v = float(coords[i]) if i < len(coords) else dflt
            loc[str(i)] = (
                0.0 if hi == lo
                else normalizeLocation({str(i): v}, {str(i): (lo, dflt, hi)})[str(i)]
            )
        return loc

    try:
        _interp_model = VariationModel(
            [_param_norm(list(getattr(m, "axes", None) or [])) for m in font.masters]
        )
    except Exception:
        _interp_model = None

    def _interpolated_seed(glyph, location):
        """(paths, width) interpolated for a brace at ``location``, or
        None if the masters aren't outline-compatible for this glyph."""
        if _interp_model is None:
            return None
        mlayers = []
        for m in font.masters:
            ml = next(
                (
                    layer for layer in glyph.layers
                    if layer.associatedMasterId == m.id
                    and not (dict(getattr(layer, "attributes", None) or {}).get("coordinates"))
                ),
                None,
            )
            if ml is None:
                return None
            mlayers.append(ml)
        ref_paths = list(mlayers[0].paths or [])
        for ml in mlayers:
            mps = list(ml.paths or [])
            if len(mps) != len(ref_paths):
                return None
            for rp, mp in zip(ref_paths, mps):
                if len(rp.nodes) != len(mp.nodes):
                    return None
        loc = _param_norm(location)
        # Seeding the memo with the owning layer is load-bearing, not a micro-
        # optimisation. GSPath.parent -> GSLayer -> GSGlyph -> GSFont, so a
        # plain deepcopy walks that backref and clones the ENTIRE font for
        # every brace layer (~750ms each; 18s across 24 layers — 92% of a
        # rebuild). Mapping the layer to None stops the walk at the boundary:
        # identical paths/nodes/types, ~750x faster.
        new_paths = copy.deepcopy(ref_paths, {id(mlayers[0]): None})
        for pi, rp in enumerate(new_paths):
            for ni in range(len(rp.nodes)):
                xs = [float(mlayers[mi].paths[pi].nodes[ni].position.x) for mi in range(len(mlayers))]
                ys = [float(mlayers[mi].paths[pi].nodes[ni].position.y) for mi in range(len(mlayers))]
                rp.nodes[ni].position = (
                    _interp_model.interpolateFromMasters(loc, xs),
                    _interp_model.interpolateFromMasters(loc, ys),
                )
        widths = [float(ml.width) for ml in mlayers]
        width = _interp_model.interpolateFromMasters(loc, widths)
        return new_paths, width

    # Seed brace layers from the unified ``layers`` list (v2.7).
    # Every brace layer is explicit — auto-seeds at axis-min/max are
    # gone. Designer authored each location through the
    # AddBraceLayerModal. Each layer is a copy of the default
    # master's outline (or a preserved outline from the previous
    # shadow if Fontra had drawn one there) so the brace exists as
    # a structural target.
    if font.masters:
        default_master = font.masters[0]
        default_master_id = default_master.id
        default_loc = list(getattr(default_master, "axes", None) or [])

        seed_jobs: List[tuple] = []  # (glyph_name, location_list)
        for spec in axes_to_add:
            for entry in spec.get("layers") or []:
                glyph_name = entry.get("glyph")
                if not glyph_name:
                    continue
                pinned = entry.get("location") or {}
                location = list(default_loc)
                pinned_any = False
                for pin_tag, pin_value in pinned.items():
                    idx = full_axis_index_by_tag.get(str(pin_tag).lower())
                    if idx is None:
                        continue
                    try:
                        location[idx] = float(pin_value)
                        pinned_any = True
                    except (TypeError, ValueError):
                        continue
                if not pinned_any:
                    continue
                # Correction target: the outline is computed as if the
                # glyph sat at the layer's parametric location with these
                # overrides applied (parametric axes only — a target on
                # the control axis itself is meaningless).
                seed_location = None
                target = entry.get("target") or {}
                if isinstance(target, dict) and target:
                    seed_location = list(location)
                    applied = False
                    for t_tag, t_value in target.items():
                        idx = full_axis_index_by_tag.get(str(t_tag).lower())
                        if idx is None or idx in control_axis_indices:
                            continue
                        try:
                            seed_location[idx] = float(t_value)
                            applied = True
                        except (TypeError, ValueError):
                            continue
                    if not applied:
                        seed_location = None
                seed_jobs.append((glyph_name, location, dict(pinned), seed_location,
                                  entry.get("outline")))

        # Order each glyph's brace layers alphabetically by their Fontra
        # source label, named (master/instance) locations first — the
        # editor's sources list follows file order.
        seed_jobs.sort(key=lambda job: (
            job[0],
            _corner_name(job[1]) is None,
            (_brace_source_label(job[1]) or "").casefold(),
        ))

        # De-duplicate seed jobs — same (glyph, location) added by
        # multiple paths only needs writing once.
        seen_jobs: set = set()
        for glyph_name, location, _pinned, seed_location, stored_outline in seed_jobs:
            job_key = (glyph_name, tuple(location))
            if job_key in seen_jobs:
                continue
            seen_jobs.add(job_key)

            glyph = font.glyphs[glyph_name] if glyph_name in font.glyphs else None
            if glyph is None:
                print(
                    f"  [control-axes] glyph '{glyph_name}' not in source — skipping seed layer",
                )
                continue
            default_layer = next(
                (layer for layer in glyph.layers if layer.associatedMasterId == default_master_id),
                None,
            )
            if default_layer is None:
                continue

            # Skip if a brace layer at this exact location already
            # exists (idempotent regeneration; also catches collisions
            # between an auto-seed and an extra_location that happen
            # to land at the same point).
            if any(
                list(dict(getattr(layer, "attributes", None) or {}).get("coordinates") or [])
                == location
                for layer in glyph.layers
            ):
                continue

            # If the previous shadow had drawn outlines at this
            # (glyph, location) — preserve them. Otherwise seed with
            # the default master's outline so the brace layer is a
            # no-op until the designer edits.
            preserved = preserved_layers.get(
                (glyph_name, tuple(float(v) for v in location))
            )
            # Seed-vs-edit by the seed signature (_restamp_seeds): a preserved
            # layer still matching its stamp is re-interpolated, so seeds
            # follow master edits; pre-stamp layers fall back to the
            # default-master comparison. See tests/test_seed_follows_masters.py.
            if preserved is None:
                preserved_is_edit = False
            elif preserved.get("seed_sig"):
                preserved_is_edit = (
                    outlines._geometry_sig(preserved.get("paths"), preserved.get("width"))
                    != preserved["seed_sig"]
                )
            else:
                preserved_is_edit = (
                    outlines._paths_sig(preserved.get("paths")) != outlines._paths_sig(default_layer.paths)
                )
            # A correction layer (seed_location set) is COMPUTED, not
            # drawn: it is re-derived from its target on every regen so
            # changing the target in the studio takes effect. Fontra
            # edits on such a layer are therefore overwritten — drop the
            # target to hand-draw it.
            if seed_location is not None:
                preserved_is_edit = False
            # A drawing captured into the sidecar (model α) outranks both the
            # prior shadow and the seed: it is the portable copy, so restoring
            # a sidecar into an empty workspace reproduces the drawn outlines.
            restored = outlines._outline_to_layer_data(stored_outline) if seed_location is None else None
            was_seeded = False
            if restored is not None:
                layer_paths = restored["paths"]
                layer_components = restored["components"]
                layer_anchors = restored["anchors"]
                layer_width = restored["width"]
            elif preserved_is_edit:
                layer_paths = preserved["paths"]
                layer_components = preserved["components"]
                layer_anchors = preserved["anchors"]
                layer_width = preserved["width"]
            else:
                was_seeded = True
                interp = _interpolated_seed(glyph, seed_location or location)
                if interp is not None:
                    layer_paths, layer_width = interp
                else:
                    # Fallback: masters not outline-compatible for this
                    # glyph — copy the default master.
                    layer_paths = list(default_layer.paths) if default_layer.paths else []
                    layer_width = default_layer.width
                layer_components = (
                    list(default_layer.components) if default_layer.components else []
                )
                layer_anchors = (
                    list(default_layer.anchors) if default_layer.anchors else []
                )

            brace = GSLayer()
            brace.associatedMasterId = default_master_id
            brace.attributes = {"coordinates": location}
            brace.paths = layer_paths
            brace.components = layer_components
            brace.anchors = layer_anchors
            brace.width = layer_width
            brace.name = "{" + ", ".join(naming._fmt_coord(v) for v in location) + "}"

            # Fontra sidebar label: the master corner or named instance
            # this view sits at. fontra-glyphs prefers
            # userData["xyz.fontra.source-name"] over the positional
            # "{...}" name; the "{...}" name stays for glyphsLib brace
            # recognition.
            # Only a freshly SEEDED layer gets a stamp, written after the save
            # (_restamp_seeds): stamping restored or preserved layers, or
            # stamping before the save, makes capture skip or freeze them.
            # See tests/test_capture_outlines.py and tests/test_seed_follows_masters.py.
            if was_seeded:
                seeded_keys.add((glyph_name, tuple(float(v) for v in location)))

            source_label = _brace_source_label(location)
            if source_label and seed_location is not None:
                # Name the correction so the Fontra source list says what
                # this computed view is: "… → as if XOPQ 1100".
                as_if = ", ".join(
                    f"{font.axes[i].axisTag}{naming._fmt_coord(v)}"
                    for i, v in enumerate(seed_location)
                    if i < len(location) and float(v) != float(location[i])
                )
                if as_if:
                    source_label = f"{source_label} → as if {as_if}"
            if source_label:
                brace.userData["xyz.fontra.source-name"] = source_label

            glyph.layers.append(brace)

    from ..source_font import save_font_atomically
    save_font_atomically(font, shadow_path)
    # Stamp the seeds from the file as WRITTEN. glyphsLib's first save
    # normalises coordinates, so a signature taken in memory never matches the
    # layer that comes back — every untouched seed then looked hand-drawn.
    # Save/reload is idempotent after that first normalisation, so a stamp
    # taken here stays valid until someone actually edits the layer.
    if seeded_keys:
        _restamp_seeds(shadow_path, seeded_keys)
    return shadow_path


def remove_shadow(original_path: Path) -> bool:
    """Delete the shadow directory entirely. Called when the sidecar
    becomes empty — original is the sole source again. Returns True
    if anything was removed."""
    shadow_dir = shadow_dir_for(original_path)
    if not shadow_dir.exists():
        return False
    shutil.rmtree(shadow_dir)
    return True


def _restamp_seeds(shadow_path: Path, seeded_keys) -> None:
    """Write the seed signature for freshly-seeded braces, reading the geometry
    back off the SAVED shadow.

    The signature exists so ``capture_outlines`` can tell a hand-drawn edit from
    an untouched seed. Taking it in memory before the save does not work:
    glyphsLib's first write normalises coordinates, so the reloaded layer
    hashes differently and every seed reads as drawn. Reloading once here costs
    a parse but makes the stamp mean what it claims.
    """
    from glyphsLib import GSFont

    from ..source_font import save_font_atomically

    try:
        font = GSFont(str(shadow_path))
    except Exception as exc:
        print(f"Warning: could not re-stamp seeds: {exc}", file=sys.stderr)
        return
    master_ids = {m.id for m in font.masters}
    stamped = 0
    for glyph in font.glyphs:
        for layer in glyph.layers:
            if layer.layerId in master_ids:
                continue
            coords = (getattr(layer, "attributes", None) or {}).get("coordinates")
            if not coords:
                continue
            key = (glyph.name, tuple(float(v) for v in coords))
            if key not in seeded_keys:
                continue
            layer.userData["xyz.avar2studio.seed-sig"] = outlines._geometry_sig(
                layer.paths, layer.width
            )
            stamped += 1
    if stamped:
        save_font_atomically(font, shadow_path)


@schema._locked
def capture_outlines(source_path: Path) -> int:
    """Copy hand-drawn brace outlines out of the shadow and INTO the sidecar.

    This is what makes ``-control.json`` (and any config bundle built from it) a
    real backup: without it the sidecar holds only layer locations and every
    drawing lives solely in ``.avar2-studio/shadow/``, which is derived and gets
    wiped. Layers that still match their computed seed are left alone so the
    sidecar doesn't fill with no-ops, and correction layers (those carrying a
    ``target``) are skipped because they are recomputed on every regeneration.

    Returns the number of layers captured.
    """
    shadow = shadow_path_for(source_path)
    if not shadow.exists():
        return 0
    drawn = _extract_brace_outlines(shadow)
    if not drawn:
        return 0

    from glyphsLib import GSFont

    try:
        shadow_font = GSFont(str(shadow))
    except Exception as exc:
        log.warning(f"Warning: could not read the shadow to capture outlines: {exc}")
        return 0
    axis_tags = [str(a.axisTag) for a in shadow_font.axes]
    # A sidecar location is SPARSE. regenerate_shadow fills the gaps with the
    # default master's coordinates, so the brace layer's stored coordinates —
    # and therefore the extractor's key — use those, not zeros.
    default_axes = list(getattr(shadow_font.masters[0], "axes", None) or []) if shadow_font.masters else []
    defaults = {
        tag: float(default_axes[i]) if i < len(default_axes) else 0.0
        for i, tag in enumerate(axis_tags)
    }

    data = schema.load(source_path)
    captured = 0
    for ax in data.get("axes") or []:
        for entry in ax.get("layers") or []:
            if entry.get("target"):
                continue  # computed, not drawn
            loc = entry.get("location") or {}
            # Match case-insensitively: sidecar tags are stored as the designer
            # typed them, the shadow's axis tags are canonical.
            lower = {str(k).lower(): v for k, v in loc.items()}
            key_loc = tuple(
                float(lower.get(tag.lower(), defaults[tag])) for tag in axis_tags
            )
            found = drawn.get((entry.get("glyph"), key_loc))
            if not found:
                continue
            # Untouched seeds still carry the signature they were stamped with;
            # skip those so the sidecar only ever holds real drawing.
            seed_sig = found.get("seed_sig")
            if seed_sig and seed_sig == outlines._geometry_sig(found.get("paths"), found.get("width")):
                continue
            serialised = outlines._parts_to_outline(
                found.get("paths"), found.get("components"),
                found.get("anchors"), found.get("width"),
            )
            if entry.get("outline") == serialised:
                continue
            entry["outline"] = serialised
            # Remember what the masters looked like when this was captured, so
            # a later master edit can be reported as "source changed since".
            entry["source_sig"] = outlines.glyph_masters_sig(shadow_font, entry.get("glyph"))
            captured += 1
    if captured:
        schema._save(source_path, data)
    return captured


def _extract_brace_outlines(shadow_path: Path) -> Dict[tuple, Dict[str, object]]:
    """Read every brace layer from a shadow .glyphs and return a
    map keyed by ``(glyph_name, location_tuple)`` to the layer's
    outline data (paths / components / anchors / width). Used by
    regenerate_shadow to preserve drawn outlines across regenerations.

    The location tuple is the layer's ``attributes.coordinates`` as a
    tuple of floats. We treat any layer carrying a coordinates entry
    as a brace layer — including the ones we authored ourselves at
    axis-min / axis-max. Layers at master positions and default-master
    layers (no coordinates attribute) are skipped.
    """
    if not shadow_path.exists():
        return {}
    try:
        from glyphsLib import GSFont
        font = GSFont(str(shadow_path))
    except Exception as exc:
        log.warning(f"Warning: failed to read previous shadow for outline preservation: {exc}")
        return {}

    # Keys are full axis vectors. A brace created before an axis was
    # appended (e.g. GRAD composed onto the control shadow) carries a
    # shorter coordinates list — pad with the default master's values,
    # the same convention regenerate_shadow uses when extending masters.
    # Without this the keys never match full-length lookups and every
    # drawing on such a layer is silently lost to capture AND regen.
    n_axes = len(font.axes)
    master_axes = list(getattr(font.masters[0], "axes", None) or []) if font.masters else []

    def _full(loc: tuple) -> tuple:
        if len(loc) >= n_axes:
            return loc[:n_axes]
        return loc + tuple(
            float(master_axes[i]) if i < len(master_axes) else 0.0
            for i in range(len(loc), n_axes)
        )

    out: Dict[tuple, Dict[str, object]] = {}
    for glyph in font.glyphs:
        for layer in glyph.layers:
            attrs = getattr(layer, "attributes", None) or {}
            coords = attrs.get("coordinates") if hasattr(attrs, "get") else None
            if not coords:
                continue
            # Normalise coords to a tuple of floats. glyphsLib gives us
            # a list; tuple makes it hashable for dict keys.
            try:
                if isinstance(coords, (list, tuple)):
                    loc = tuple(float(v) for v in coords)
                elif hasattr(coords, "items"):
                    # Dict-shaped {axis_index: value}. Preserve key order
                    # by sorting on int key.
                    loc = tuple(
                        float(v) for _, v in sorted(coords.items(), key=lambda kv: int(kv[0]))
                    )
                else:
                    continue
            except (TypeError, ValueError):
                continue
            out[(glyph.name, _full(loc))] = {
                "paths": list(layer.paths) if layer.paths else [],
                "components": list(layer.components) if layer.components else [],
                "anchors": list(layer.anchors) if layer.anchors else [],
                "width": layer.width,
                # Stamped when the layer was seeded; lets capture_outlines tell
                # an untouched seed from a hand-drawn edit.
                "seed_sig": (getattr(layer, "userData", None) or {}).get(
                    "xyz.avar2studio.seed-sig"
                ),
            }
    return out
