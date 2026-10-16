"""Width-aware SPAC — our own spacing transform (built-in, transform #2).

Same idea as the uniform gftools SPAC, but each glyph's advance/sidebearing
delta scales with its own outline width, so the whole font loosens by a
consistent *proportion* — wider glyphs get proportionally more space, not a
flat amount. It operates at the fontTools level (not shelling to gftools,
since we need the per-glyph loop), reusing gen_spac's injection seam: two gvar
``TupleVariation``s per glyph on the phantom points ``[-4]`` (LSB) and ``[-3]``
(advance), an fvar SPAC axis, ``add_HVAR``, and VarStore region padding.

Two deliberate differences from a naive port of Crispy's original:

1. **Proportional around the font's own average, not log, and not
   UPM-normalized.** The width factor is ``(ink / mean_ink) ** bias``,
   where ``mean_ink`` is the font's average ink width. At ``bias = 1``
   (default) an average-width glyph tracks exactly like the uniform
   gftools transform (±N per side), wide glyphs get more, narrow less —
   the *distribution* is proportional but the *magnitude* matches the
   flat transform at any design width. (Normalizing by UPM instead —
   the original behaviour — made the effect ~10× too weak on condensed
   designs, whose glyphs are a small fraction of the em.) Crispy's
   ``log(width)`` curve actually *compressed* the wide end, handing
   narrow glyphs proportionally MORE space and wide glyphs less (the
   opposite of even rhythm). ``bias > 1`` pushes past proportional so
   wide glyphs get extra.

2. **Composites are spaced too.** gen_spac (and the uniform transform) skip
   glyphs with no drawn outline, so ``w``/``u`` and every accented letter get
   zero tracking and read as cramped. Here we measure their bounds through the
   glyph set and inject advance/sidebearing deltas anyway.

Because the factor uses each glyph's share of the font's own average
width, it is UPM- and design-invariant (same magnitude on condensed and
wide designs alike). Outlines never move — only phantom points.

Per-unit rate: at ``scale = 1``, ±N SPAC units ≈ ∓N units on each
sidebearing of an average-width glyph (±2N advance — exactly the uniform
gftools amount). ``scale`` multiplies that rate; the default 1.25 sits
just above parity so lowercase-heavy sample text (narrower than the
glyph mean) also matches the gftools amount.
"""

from __future__ import annotations

from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables
from fontTools.ttLib.tables._f_v_a_r import Axis
from fontTools.ttLib.tables._g_l_y_f import USE_MY_METRICS
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib.hvar import add_HVAR

from .base import BuildContext, ParamSpec, Transform, TransformSpec
from .builtin_spac import _instance_spac_overrides, _spac_output_name


def _native_injector():
    """The Rust SPAC engine (wasm/fontc-web spac.rs, built natively as
    the spac-inject CLI) — the same code the static demo runs, parity-
    tested by tests/spac_oracle.rs. Preferred because it splices tuples
    with write-fonts' compact gvar encoding: the fontTools path below
    re-encodes the whole table and overflows gvar's 64KB per-glyph
    header ceiling on twin-heavy fonts (slant x rounding x grade).
    Returns the binary's path, or None (callers fall back to fontTools).
    Override with $AVAR2_SPAC_INJECT."""
    import os
    env = os.environ.get("AVAR2_SPAC_INJECT")
    if env:
        path = Path(env)
        return path if path.exists() else None
    path = (Path(__file__).resolve().parents[3]
            / "wasm" / "fontc-web" / "target" / "release" / "spac-inject")
    return path if path.exists() else None


def _ink_width(glyph_set, name) -> float:
    """Outline bounding-box width at the default master, resolved through the
    glyph set so composites (which reference other glyphs) measure correctly."""
    pen = BoundsPen(glyph_set)
    try:
        glyph_set[name].draw(pen)
    except Exception:
        return 0.0
    if not pen.bounds:
        return 0.0
    xmin, _ymin, xmax, _ymax = pen.bounds
    return xmax - xmin


def _gvar_point_count(glyph, glyf) -> int:
    """Number of gvar coordinate slots for a glyph = its points + 4 phantoms.
    Composites store one slot per component; simple glyphs one per point."""
    if glyph.isComposite():
        return len(glyph.components) + 4
    coords, _end, _flags = glyph.getCoordinates(glyf)
    return len(coords) + 4


class WidthAwareSpacTransform(Transform):
    spec = TransformSpec(
        id="spac_widthaware",
        name="Spacing — width-aware",
        description="Inject a SPAC axis that loosens every glyph by a consistent proportion of its width (wider glyphs get more), including composites.",
        params=[
            ParamSpec(key="min", label="Min", type="int", default=-40),
            ParamSpec(key="max", label="Max", type="int", default=40),
            # 1.0 = proportional (added space ÷ width is constant). >1 gives
            # wide glyphs progressively more than proportional; normalized so
            # an average-width glyph has factor 1 at any bias.
            ParamSpec(key="bias", label="Wide bias", type="float", default=1.0, min=1.0, max=4.0),
            # Per-unit rate multiplier. 1.0 = ±N SPAC ≈ ∓N per sidebearing
            # for an average-width glyph (±2N advance — i.e. exactly the
            # uniform gftools amount). 1.25 lands lowercase-heavy sample
            # text (narrower than the glyph mean) at the gftools amount too.
            ParamSpec(key="scale", label="Scale", type="float", default=1.25, min=0.1, max=10.0),
        ],
        default_enabled=False,
        injected_axis_tag="SPAC",
    )

    def validate(self, params: dict) -> None:
        lo = int(params.get("min", -40))
        hi = int(params.get("max", 40))
        if lo >= hi:
            raise ValueError(f"SPAC min ({lo}) must be less than max ({hi}).")

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        vf_path = Path(vf_path)
        lo = int(params.get("min", -40))
        hi = int(params.get("max", 40))
        if lo >= hi:
            raise ValueError(f"SPAC min ({lo}) must be less than max ({hi}).")
        try:
            bias = float(params.get("bias", 1.0))
        except (TypeError, ValueError):
            bias = 1.0
        bias = max(1.0, bias)
        try:
            scale = float(params.get("scale", 1.25))
        except (TypeError, ValueError):
            scale = 1.25
        scale = max(0.1, scale)

        native = _native_injector()
        if native is not None:
            import json as _json
            import os
            import subprocess
            import tempfile
            out = vf_path.parent / _spac_output_name(ctx.family, vf_path)
            payload = _json.dumps([{"type": "spac_widthaware", "enabled": True,
                                    "params": {"min": lo, "max": hi,
                                               "bias": bias, "scale": scale}}])
            tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            try:
                tf.write(payload)
                tf.close()
                csv_path = ctx.source_path.parent / f"{ctx.source_path.stem}-avar.csv"
                cmd = [str(native), str(vf_path), str(out), tf.name]
                if csv_path.exists():
                    cmd.append(str(csv_path))
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            finally:
                os.unlink(tf.name)
            if res.returncode == 0 and out.exists():
                ctx.log(f"spac_widthaware: native injector ({lo}\u2026{hi}, "
                        f"bias {bias:g}, scale {scale:g})")
                return out
            ctx.log("spac_widthaware: native injector failed; falling back to "
                    "fontTools (%s)" % ((res.stderr or "").strip()[-300:] or "no stderr"))
        else:
            # Absence used to be silent: the fallback is slower and its gvar
            # re-encode can overflow the 64KB per-glyph tuple-header ceiling
            # on twin-heavy fonts — say which path is running and why.
            ctx.log("spac_widthaware: native injector not found — the fontTools "
                    "path runs (slower; can overflow gvar's 64KB tuple-header "
                    "ceiling on twin-heavy fonts). `avar2-studio doctor` lists it.")

        font = TTFont(str(vf_path))
        try:
            if not all(t in font for t in ("fvar", "gvar", "glyf")):
                raise RuntimeError("width-aware SPAC needs a glyf-based VF with fvar+gvar")
            glyf = font["glyf"]
            gvar = font["gvar"]
            glyph_set = font.getGlyphSet()

            # Two passes: measure every glyph first so deltas can be
            # normalized against the font's OWN average ink width — an
            # average-width glyph then tracks exactly like the uniform
            # gftools transform (see module docstring), whatever the
            # design's absolute width. space/.notdef (no ink) are skipped.
            inks = {
                name: ink
                for name in font.getGlyphOrder()
                if (ink := _ink_width(glyph_set, name)) > 0
            }
            if not inks:
                raise RuntimeError("width-aware SPAC: no measurable glyph outlines")
            mean_ink = sum(inks.values()) / len(inks)

            for name, ink in inks.items():
                glyph = glyf[name]
                composite = glyph.isComposite()
                # A composite with USE_MY_METRICS takes its advance/lsb from a
                # component, so it ALREADY inherits that base glyph's SPAC
                # delta (an accented é tracks like its e). Injecting here would
                # double it — skip.
                if composite and any(c.flags & USE_MY_METRICS for c in glyph.components):
                    continue

                factor = (ink / mean_ink) ** bias  # average-width glyph → factor 1.0 at any bias

                variations = gvar.variations.get(name)
                if variations:
                    n = len(variations[0].coordinates)
                else:
                    n = _gvar_point_count(glyph, glyf)
                    variations = gvar.variations.setdefault(name, [])

                # Simple glyph: move both phantoms (±) for a symmetric grow.
                # Composite: moving the LEFT phantom shifts the whole component,
                # which opens BOTH sidebearings equally — so a right delta too
                # would double the right side. Left phantom only.
                for support, amount in (((-1.0, -1.0, 0.0), lo), ((0.0, 1.0, 1.0), hi)):
                    coords = [None] * n
                    coords[-4] = (round(-amount * factor * scale), 0)
                    if not composite:
                        coords[-3] = (round(amount * factor * scale), 0)
                    variations.append(TupleVariation({"SPAC": support}, coords))

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
            # Instance pinning: default 0 — EXCEPT instances the CSV gives a
            # per-instance SPAC coordinate (authored via the studio). Those
            # must survive injection, not be flattened to the default.
            spac_overrides = _instance_spac_overrides(ctx.source_path)
            for inst in fvar.instances:
                iname = name_table.getDebugName(inst.subfamilyNameID) or ""
                inst.coordinates["SPAC"] = spac_overrides.get(iname, 0.0)
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
            try:
                font.save(str(out))
            except OverflowError as exc:
                # gvar's per-glyph variation data has a hard 64KB header
                # ceiling. A font whose tuple count is already enormous
                # (source twins from slant/rounding x grade x the default
                # relocation) sits just under it, and adding the SPAC
                # axis inflates every embedded tuple header past it.
                raise RuntimeError(
                    "this font's per-glyph variation data is at gvar's "
                    "64KB ceiling (slant/rounding twins multiply it), so "
                    "the SPAC axis cannot be injected. Disable the slant "
                    "transform to get spacing back, or keep slant and go "
                    "without SPAC.") from exc
        finally:
            font.close()
        ctx.log(f"width-aware SPAC injected ({lo}…{hi}, bias {bias:g}, scale {scale:g}) → {out.name}")
        return out
