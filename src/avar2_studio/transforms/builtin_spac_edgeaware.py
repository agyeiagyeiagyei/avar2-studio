"""Edge-aware SPAC — spacing scaled by each glyph's edge shape, not its width.

The width-aware transform scales spacing by bbox width, which over-spaces
glyphs that are wide because they're built from repeated narrow units — ``m``,
``w`` — since a sidebearing cares about the *shape at the edge*, not the total
width. This transform measures how "open" each glyph's left and right edges are
and scales the added space per side by that.

Openness is measured by scanline: flatten the outline to segments, sample a set
of horizontal scanlines over the glyph's height, and find where the ink's left
and right edges cross each one. A side's openness is the fraction of scanlines
where the edge sits AWAY from the extreme (does not reach the margin). A flat
stem or straight diagonal (``m``, ``w``, ``H``, ``l``) reaches the margin over
its whole height → openness 0 → the nominal amount. A receding round edge
(``o``) or an open terminal (``r``'s right, ``c``, ``T``) → higher openness →
more space. Each side is measured independently, so ``r`` gets its own left and
right amounts.

So ``m`` finally spaces like a stem-edged glyph rather than like something twice
its width. Mechanically identical injection to the width-aware transform (gvar
phantom deltas + fvar axis + add_HVAR + region padding); only the per-glyph,
per-side scale differs.
"""

from __future__ import annotations

from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables
from fontTools.ttLib.tables._f_v_a_r import Axis
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib.hvar import add_HVAR

from .base import BuildContext, ParamSpec, Transform, TransformSpec
from .builtin_spac import _spac_output_name

_SCANLINES = 40
_NEAR_FRAC = 0.10   # an edge within 10% of ink width of the extreme "reaches the margin"


class _SegmentPen(BasePen):
    """Flatten the rendered outline (components decomposed) into line segments
    so we can intersect scanlines with the true edges — robust to sparse point
    sampling, unlike binning a point cloud."""

    def __init__(self, glyph_set, steps=12):
        super().__init__(glyph_set)
        self.segs = []
        self._steps = steps
        self._start = None
        self._cur = None

    def _moveTo(self, p):
        self._start = p
        self._cur = p

    def _lineTo(self, p):
        self.segs.append((self._cur, p))
        self._cur = p

    def _curveToOne(self, a, b, c):
        p = self._cur
        prev = p
        for i in range(1, self._steps + 1):
            t = i / self._steps
            m = 1 - t
            x = m * m * m * p[0] + 3 * m * m * t * a[0] + 3 * m * t * t * b[0] + t * t * t * c[0]
            y = m * m * m * p[1] + 3 * m * m * t * a[1] + 3 * m * t * t * b[1] + t * t * t * c[1]
            self.segs.append((prev, (x, y)))
            prev = (x, y)
        self._cur = c

    def _qCurveToOne(self, a, b):
        p = self._cur
        prev = p
        for i in range(1, self._steps + 1):
            t = i / self._steps
            m = 1 - t
            x = m * m * p[0] + 2 * m * t * a[0] + t * t * b[0]
            y = m * m * p[1] + 2 * m * t * a[1] + t * t * b[1]
            self.segs.append((prev, (x, y)))
            prev = (x, y)
        self._cur = b

    def _closePath(self):
        if self._cur and self._start and self._cur != self._start:
            self.segs.append((self._cur, self._start))

    def _endPath(self):
        self._closePath()


def _scanline_x(segs, y):
    """Leftmost and rightmost x where the outline crosses the scanline y."""
    xs = []
    for (x0, y0), (x1, y1) in segs:
        if y0 == y1:
            continue
        lo, hi = (y0, y1) if y0 < y1 else (y1, y0)
        if lo <= y < hi:
            xs.append(x0 + (x1 - x0) * (y - y0) / (y1 - y0))
    if not xs:
        return None, None
    return min(xs), max(xs)


def _edge_openness(glyph_set, name):
    """Return (left_openness, right_openness, ink_width). Openness ∈ [0,1] is
    the fraction of scanlines where that edge does NOT reach the margin — 0 for
    a flat stem/diagonal, higher for a receding/open edge."""
    pen = _SegmentPen(glyph_set)
    try:
        glyph_set[name].draw(pen)
    except Exception:
        return 0.0, 0.0, 0.0
    segs = pen.segs
    if not segs:
        return 0.0, 0.0, 0.0
    ys = [p[1] for s in segs for p in s]
    ymin, ymax = min(ys), max(ys)
    if ymax - ymin <= 0:
        return 0.0, 0.0, 0.0
    rows = []
    for i in range(_SCANLINES):
        y = ymin + (ymax - ymin) * (i + 0.5) / _SCANLINES
        lx, rx = _scanline_x(segs, y)
        if lx is not None:
            rows.append((lx, rx))
    if not rows:
        return 0.0, 0.0, 0.0
    xmin = min(r[0] for r in rows)
    xmax = max(r[1] for r in rows)
    ink_w = xmax - xmin
    if ink_w <= 0:
        return 0.0, 0.0, ink_w
    thr = _NEAR_FRAC * ink_w
    near_l = sum(1 for lx, _ in rows if lx - xmin <= thr) / len(rows)
    near_r = sum(1 for _, rx in rows if xmax - rx <= thr) / len(rows)
    return 1.0 - near_l, 1.0 - near_r, ink_w


def _gvar_point_count(glyph, glyf):
    if glyph.isComposite():
        return len(glyph.components) + 4
    coords, _end, _flags = glyph.getCoordinates(glyf)
    return len(coords) + 4


class EdgeAwareSpacTransform(Transform):
    spec = TransformSpec(
        id="spac_edgeaware",
        name="Spacing — edge-aware",
        description="Inject a SPAC axis that scales each side by how open that edge is — stems/diagonals get the base amount, receding round or open edges get more. Fixes m/w over-spacing.",
        params=[
            ParamSpec(key="min", label="Min", type="int", default=-20),
            ParamSpec(key="max", label="Max", type="int", default=40),
            # Added space per side = amount * (1 + openness * edge_openness).
            # 0 = uniform (edge ignored); higher lets open edges get more over
            # the flat-edge baseline, which always gets the nominal min/max.
            ParamSpec(key="openness", label="Openness", type="float", default=1.0, min=0.0, max=3.0),
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
        try:
            gain = float(params.get("openness", 1.0))
        except (TypeError, ValueError):
            gain = 1.0
        gain = max(0.0, gain)

        font = TTFont(str(vf_path))
        try:
            if not all(t in font for t in ("fvar", "gvar", "glyf")):
                raise RuntimeError("edge-aware SPAC needs a glyf-based VF with fvar+gvar")
            glyf = font["glyf"]
            gvar = font["gvar"]
            glyph_set = font.getGlyphSet()

            for name in font.getGlyphOrder():
                o_l, o_r, ink = _edge_openness(glyph_set, name)
                if ink <= 0:
                    continue                     # space, .notdef — leave untouched

                glyph = glyf[name]
                # Composites are skipped. Accented letters carry USE_MY_METRICS,
                # so they inherit their base glyph's SPAC delta automatically
                # (é tracks like e). Other composites are rare, and injecting
                # into them reliably (the left phantom drags the components) is
                # not worth the fragility — better no delta than a broken one.
                if glyph.isComposite():
                    continue

                # Flat edge (openness 0) → nominal amount; open edges scale up.
                w_l = 1.0 + gain * o_l
                w_r = 1.0 + gain * o_r

                variations = gvar.variations.get(name)
                if variations:
                    n = len(variations[0].coordinates)
                else:
                    n = _gvar_point_count(glyph, glyf)
                    variations = gvar.variations.setdefault(name, [])

                for support, amount in (((-1.0, -1.0, 0.0), lo), ((0.0, 1.0, 1.0), hi)):
                    coords = [None] * n
                    coords[-4] = (round(-amount * w_l), 0)
                    coords[-3] = (round(amount * w_r), 0)
                    variations.append(TupleVariation({"SPAC": support}, coords))

            # fvar axis + instance pinning + HVAR + region padding
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
        ctx.log(f"edge-aware SPAC injected ({lo}…{hi}, openness {gain:g}) → {out.name}")
        return out
