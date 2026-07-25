"""Grade → shadow brace layers.

Turns the ``-grade.json`` declaration (see :mod:`grade`) into GRAD-axis brace
layers on the shadow ``.glyphs``, reusing the shadow/virtual-master machinery
that :mod:`control_axes` established. A grade is an *auto-generated* control
axis: the designer never draws these braces — each is computed.

For every graded instance, for every glyph:

  * interpolate the glyph's outline at the instance's LIGHT and DARK grade
    coords (a VariationModel over the parametric masters + any parametric curve
    braces — so round glyphs get fontmake-accurate geometry);
  * **equalise** each brace's advance to the glyph's true base advance at the
    instance (interpolated width), shifting the outline symmetrically — this is
    what holds advance across GRAD with zero phantom-point delta;
  * inject the two braces at ``(instance parametric coords × GRAD ∓10)`` and a
    Virtual Master pair so the GRAD axis has a real range.

Composition: this runs AFTER ``control_axes.regenerate_shadow`` on the same
shadow (the server orchestrates the order), so it must skip control-axis braces
when building the parametric interpolation model. If no control axes exist it
makes the shadow itself (fresh copy of the original).

Scope: ``.glyphs`` sources only for now (matches the control-axes v2 slice);
``.designspace`` grade is future work.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fontTools.varLib.models import VariationModel, normalizeLocation, supportScalar

from . import grade as _grade
from . import control_axes as _control_axes


def apply_grades(
    original_path: Path,
    instance_coords: Dict[str, Dict[str, float]],
    fresh_shadow: bool = True,
) -> Optional[Path]:
    """Add GRAD braces + virtual masters to the shadow for every graded
    instance. ``instance_coords`` maps instance name → its parametric base
    ``{XTRA, XOPQ, YOPQ}`` (the caller resolves the instance's TRUE avar2-mapped
    location). Returns the shadow path, or ``None`` when no grade applies (toggle
    off / no graded instances / unsupported format).

    ``fresh_shadow`` controls idempotency: pass ``True`` (default) to start from
    a clean copy of the original so re-runs don't stack braces; pass ``False``
    when ``control_axes.regenerate_shadow`` has just rebuilt the shadow this cycle
    (grade then composes on top of the control-axis shadow)."""
    graded = _grade.list_graded_instances(original_path)
    if not graded:
        return None
    if original_path.suffix.lower() != ".glyphs":
        return None  # .designspace grade deferred

    from glyphsLib import GSFont
    from glyphsLib.classes import GSAxis, GSCustomParameter, GSLayer, GSPath, GSNode, GSComponent
    from glyphsLib.types import Point, Transform

    shadow_path = _control_axes.shadow_path_for(original_path)
    if fresh_shadow or not shadow_path.exists():
        shadow_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original_path, shadow_path)

    font = GSFont(str(shadow_path))
    masters = list(font.masters)
    if not masters:
        return None

    tag_to_idx = {str(a.axisTag).upper(): i for i, a in enumerate(font.axes)}
    param_idx = [tag_to_idx[t] for t in _grade.PARAM_TAGS if t in tag_to_idx]
    if len(param_idx) != len(_grade.PARAM_TAGS):
        return None  # source isn't the parametric family we understand

    origin = list(getattr(masters[0], "axes", None) or [])
    # parametric ranges (min, default=origin, max) for the model + clamping
    triples: Dict[int, Tuple[float, float, float]] = {}
    for i in param_idx:
        vals = [float(m.axes[i]) for m in masters if i < len(getattr(m, "axes", None) or [])]
        triples[i] = (min(vals), float(origin[i]), max(vals))
    param_ranges = {
        _grade.PARAM_TAGS[k]: (triples[param_idx[k]][0], triples[param_idx[k]][2])
        for k in range(len(param_idx))
    }
    nonparam_idx = [i for i in range(len(font.axes)) if i not in param_idx]

    def pnorm(coords) -> Dict[str, float]:
        loc = {}
        for i in param_idx:
            lo, dflt, hi = triples[i]
            v = float(coords[i]) if i < len(coords) else dflt
            loc[str(i)] = 0.0 if hi == lo else normalizeLocation({str(i): v}, {str(i): (lo, dflt, hi)})[str(i)]
        return loc

    def coords_to_pnorm(tag_vals: Dict[str, float]) -> Dict[str, float]:
        loc = {}
        for k, i in enumerate(param_idx):
            lo, dflt, hi = triples[i]
            v = float(tag_vals.get(_grade.PARAM_TAGS[k], dflt))
            loc[str(i)] = 0.0 if hi == lo else normalizeLocation({str(i): v}, {str(i): (lo, dflt, hi)})[str(i)]
        return loc

    def is_parametric_plane(coords) -> bool:
        """A layer sits on the parametric plane iff every non-parametric axis
        (control axes, GRAD) is at the origin's value — i.e. it's a master or a
        parametric curve brace, not a control/grade brace."""
        for i in nonparam_idx:
            if i < len(coords) and i < len(origin) and float(coords[i]) != float(origin[i]):
                return False
        return True

    # --- per-glyph interpolator over masters + parametric curve braces -----
    def build_interpolator(glyph):
        entries = []  # (pnorm_loc, layer)
        mids = {m.id for m in masters}
        for m in masters:
            L = next((l for l in glyph.layers if l.associatedMasterId == m.id
                      and not (dict(getattr(l, "attributes", None) or {}).get("coordinates"))), None)
            if L is None:
                return None
            entries.append((pnorm(list(getattr(m, "axes", None) or [])), L))
        for L in glyph.layers:
            co = dict(getattr(L, "attributes", None) or {}).get("coordinates")
            if co and is_parametric_plane(co):
                entries.append((pnorm(list(co)), L))
        # outline compatibility
        ref = entries[0][1]
        sig = (len(ref.paths), tuple(len(p.nodes) for p in ref.paths), len(ref.components))
        for _, L in entries:
            if (len(L.paths), tuple(len(p.nodes) for p in L.paths), len(L.components)) != sig:
                return None
        Ls = [e[1] for e in entries]
        model = VariationModel([e[0] for e in entries])
        n = len(Ls)
        wdel = model.getDeltas([float(L.width) for L in Ls])
        meta = []
        pdel = []
        for pi, tp in enumerate(Ls[0].paths):
            pm, nd = [], []
            for ni, tn in enumerate(tp.nodes):
                pm.append((tn.type, tn.smooth))
                nd.append((model.getDeltas([float(Ls[k].paths[pi].nodes[ni].position.x) for k in range(n)]),
                           model.getDeltas([float(Ls[k].paths[pi].nodes[ni].position.y) for k in range(n)])))
            meta.append((tp.closed, pm))
            pdel.append(nd)
        comp_ref = list(Ls[0].components)
        cdel = [(model.getDeltas([float(Ls[k].components[ci].position.x) for k in range(n)]),
                 model.getDeltas([float(Ls[k].components[ci].position.y) for k in range(n)]))
                for ci in range(len(comp_ref))]

        def eval_at(loc):
            sc = [supportScalar(loc, sup) for sup in model.supports]
            dot = lambda d: sum(a * b for a, b in zip(d, sc))
            lay = GSLayer()
            for pi, (closed, pm) in enumerate(meta):
                p = GSPath()
                p.closed = closed
                for ni, (ntype, smooth) in enumerate(pm):
                    nd = GSNode()
                    nd.position = Point(dot(pdel[pi][ni][0]), dot(pdel[pi][ni][1]))
                    nd.type = ntype
                    nd.smooth = smooth
                    p.nodes.append(nd)
                lay.paths.append(p)
            for ci, tc in enumerate(comp_ref):
                c = GSComponent(tc.componentName)
                c.transform = Transform(*tc.transform.value)
                c.position = Point(dot(cdel[ci][0]), dot(cdel[ci][1]))
                lay.components.append(c)
            lay.width = dot(wdel)
            return lay
        return eval_at

    # --- add GRAD axis + extend master/instance locations ------------------
    if _grade.GRAD_TAG.upper() not in tag_to_idx:
        gax = GSAxis()
        gax.name, gax.axisTag = _grade.GRAD_NAME, _grade.GRAD_TAG
        font.axes.append(gax)
        for m in font.masters:
            if len(m.axes) < len(font.axes):
                m.axes = list(m.axes) + [_grade.GRAD_DEFAULT]
        for inst in font.instances:
            if len(inst.axes) < len(font.axes):
                inst.axes = list(inst.axes) + [_grade.GRAD_DEFAULT]
    grad_idx = next(i for i, a in enumerate(font.axes)
                    if str(a.axisTag).upper() == _grade.GRAD_TAG.upper())
    axis_labels = [str(getattr(a, "name", "") or getattr(a, "axisTag", "")) for a in font.axes]

    def brace_location(base_tag_vals: Dict[str, float], gradval: float) -> List[float]:
        n = len(font.axes)
        loc = (list(origin) + [_grade.GRAD_DEFAULT] * n)[:n]  # pad origin to full axis count
        for k, i in enumerate(param_idx):
            loc[i] = float(base_tag_vals.get(_grade.PARAM_TAGS[k], loc[i]))
        loc[grad_idx] = float(gradval)
        return loc

    def add_virtual_master(loc: List[float]):
        value = [{"Axis": axis_labels[i], "Location": loc[i]} for i in range(min(len(axis_labels), len(loc)))]
        font.customParameters.append(GSCustomParameter("Virtual Master", value))

    # --- inject braces per graded instance ---------------------------------
    interp_cache: Dict[str, Optional[object]] = {}
    applied = 0
    for entry in graded:
        name = entry.get("name")
        pct = float(entry.get("pct", 0.0))
        base = instance_coords.get(name)
        if base is None or pct <= 0:
            continue
        light_c, dark_c = _grade.grade_coords(base, pct, param_ranges)
        base_loc = coords_to_pnorm(base)
        light_loc = coords_to_pnorm(light_c)
        dark_loc = coords_to_pnorm(dark_c)
        # virtual masters at this instance's parametric location × GRAD extremes
        add_virtual_master(brace_location(base, _grade.GRAD_MIN))
        add_virtual_master(brace_location(base, _grade.GRAD_MAX))

        for glyph in font.glyphs:
            if glyph.name not in interp_cache:
                interp_cache[glyph.name] = build_interpolator(glyph)
            eval_at = interp_cache[glyph.name]
            if eval_at is None:
                continue
            A0 = round(eval_at(base_loc).width)
            for gradval, gloc in ((_grade.GRAD_MIN, light_loc), (_grade.GRAD_MAX, dark_loc)):
                lay = eval_at(gloc)
                shift = round((A0 - lay.width) / 2)
                for path in lay.paths:
                    for node in path.nodes:
                        node.position = Point(node.position.x + shift, node.position.y)
                for comp in lay.components:
                    comp.position = Point(comp.position.x + shift, comp.position.y)
                lay.width = A0
                loc = brace_location(base, gradval)
                lay.layerId = str(uuid.uuid4()).upper()
                lay.associatedMasterId = masters[0].id
                lay.attributes["coordinates"] = loc
                lay.name = "{" + ", ".join(_control_axes._fmt_coord(v) for v in loc) + "}"
                glyph.layers.append(lay)
        applied += 1

    if applied == 0:
        return None
    font.save(str(shadow_path))
    return shadow_path
