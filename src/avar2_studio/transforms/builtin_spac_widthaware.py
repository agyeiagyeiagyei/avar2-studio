"""Width-aware SPAC — our own spacing transform (built-in, transform #2).

Same idea as the uniform gftools SPAC, but the per-glyph advance/sidebearing
delta scales with each glyph's own outline width — wider glyphs get more
spacing, narrower less — the way Crispy's original build did it, without the
Crispy-hardcoded XTRA range or UPM-bound constants.

It operates at the fontTools level (not shelling to gftools, since we need the
per-glyph loop), reusing gen_spac's exact injection seam: two gvar
``TupleVariation``s per glyph on the phantom points ``[-4]`` (LSB) and ``[-3]``
(advance), an fvar SPAC axis, ``add_HVAR``, and VarStore region padding.

The width factor is normalized so a glyph exactly one em wide gets factor 1.0 —
so ``min``/``max`` keep the same meaning as the uniform transform (nominal
per-side amount), and the factor just modulates around that per glyph. Because
the factor uses ``width / unitsPerEm``, it is UPM-invariant (Crispy is 2000).
"""

from __future__ import annotations

import math
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables
from fontTools.ttLib.tables._f_v_a_r import Axis
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib.hvar import add_HVAR

from .base import BuildContext, ParamSpec, Transform, TransformSpec
from .builtin_spac import _spac_output_name

_LOG2 = math.log(2.0)


def _outline_bbox_width(glyph, glyf):
    """Outline bbox width (advance-agnostic — Crispy's 'ink stays put, only
    sidebearings grow' invariant). getCoordinates returns outline points only
    (no phantoms), so no stripping is needed."""
    try:
        coords, _end, _flags = glyph.getCoordinates(glyf)
    except Exception:
        return None
    pts = list(coords)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    return max(xs) - min(xs)


class WidthAwareSpacTransform(Transform):
    spec = TransformSpec(
        id="spac_widthaware",
        name="Spacing — width-aware",
        description="Inject a SPAC axis whose per-glyph tracking scales with each glyph's width (wider glyphs get more).",
        params=[
            ParamSpec(key="min", label="Min", type="int", default=-20),
            ParamSpec(key="max", label="Max", type="int", default=40),
            ParamSpec(
                key="mode", label="Curve", type="select", default="log",
                options=[
                    {"value": "log", "label": "Log (gentle)"},
                    {"value": "linear", "label": "Linear"},
                ],
            ),
        ],
        default_enabled=False,
        injected_axis_tag="SPAC",
    )

    def validate(self, params: dict) -> None:
        lo = int(params.get("min", -20))
        hi = int(params.get("max", 40))
        if lo >= hi:
            raise ValueError(f"SPAC min ({lo}) must be less than max ({hi}).")

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        vf_path = Path(vf_path)
        lo = int(params.get("min", -20))
        hi = int(params.get("max", 40))
        if lo >= hi:
            raise ValueError(f"SPAC min ({lo}) must be less than max ({hi}).")
        mode = params.get("mode", "log")
        if mode not in ("log", "linear"):
            mode = "log"

        font = TTFont(str(vf_path))
        try:
            if not all(t in font for t in ("fvar", "gvar", "glyf")):
                raise RuntimeError("width-aware SPAC needs a glyf-based VF with fvar+gvar")
            upm = font["head"].unitsPerEm or 1000
            glyf = font["glyf"]
            gvar = font["gvar"]

            for name in font.getGlyphOrder():
                glyph = glyf[name]
                if not hasattr(glyph, "coordinates"):     # composites / space
                    continue
                variations = gvar.variations.get(name)
                if not variations:                        # static glyph (no gvar)
                    continue
                bw = _outline_bbox_width(glyph, glyf)
                if not bw or bw <= 0:
                    continue
                w_norm = bw / upm
                # normalized so a 1-em-wide glyph → factor 1.0 (min/max keep
                # their nominal per-side meaning; the factor modulates per glyph)
                factor = (math.log(w_norm + 1.0) / _LOG2) if mode == "log" else w_norm

                n = len(variations[0].coordinates)   # outline points + 4 phantoms
                mn = [None] * n
                mn[-4] = (round(-lo * factor), 0)
                mn[-3] = (round(lo * factor), 0)
                gvar.variations[name].append(TupleVariation({"SPAC": (-1.0, -1.0, 0.0)}, mn))
                mx = [None] * n
                mx[-4] = (round(-hi * factor), 0)
                mx[-3] = (round(hi * factor), 0)
                gvar.variations[name].append(TupleVariation({"SPAC": (0.0, 1.0, 1.0)}, mx))

            # fvar axis + instance pinning (vendored from gen_spac.add_spacing_axis)
            name_table = font["name"]
            axis = Axis()
            axis.axisTag = "SPAC"
            axis.axisNameID = name_table.addMultilingualName({"en": "Spacing"})
            axis.minValue = float(lo)
            axis.defaultValue = 0.0
            axis.maxValue = float(hi)
            fvar = font["fvar"]
            fvar.axes.append(axis)
            for inst in fvar.instances:
                inst.coordinates["SPAC"] = 0.0
            add_HVAR(font)

            # pad every VarStore's regions for the new axis
            spac_region = otTables.VarRegionAxis()
            spac_region.StartCoord = -1
            spac_region.PeakCoord = 0
            spac_region.EndCoord = 1
            for table in ("MVAR", "HVAR", "BASE", "VVAR", "COLR", "GDEF"):
                if table in font and hasattr(font[table].table, "VarStore"):
                    store = font[table].table.VarStore
                    store.VarRegionList.RegionAxisCount = len(fvar.axes)
                    for region in store.VarRegionList.Region:
                        while len(region.VarRegionAxis) < len(fvar.axes):
                            region.VarRegionAxis.append(spac_region)

            out = vf_path.parent / _spac_output_name(ctx.family, vf_path)
            font.save(str(out))
        finally:
            font.close()
        ctx.log(f"width-aware SPAC injected ({lo}…{hi}, {mode}) → {out.name}")
        return out
