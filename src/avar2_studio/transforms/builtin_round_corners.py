"""Round corners — a SOURCE-stage transform.

Unlike the font-stage transforms (SPAC etc.), this one runs before the
compile, on the composed shadow ``.glyphs``: every line-line corner of
every interpolating layer gains a round whose radius follows the stroke
weight that layer represents. The source the designer edits stays
sharp; the rounding is a build-time finishing pass, tunable from the
sidecar and switchable off. The geometry lives in
:mod:`avar2_studio.transforms.corner_rounding`.
"""

from __future__ import annotations

from pathlib import Path

from .base import BuildContext, ParamSpec, Transform, TransformSpec
from . import corner_rounding


class RoundCornersTransform(Transform):
    spec = TransformSpec(
        id="round_corners",
        name="Round corners",
        description=("Round every corner before the compile; the radius blends each "
                     "layer's stroke weight (XOPQ) with its width (XTRA), outer "
                     "corners and counters separately."),
        stage="source",
        params=[
            # The broad rounding: every named style's ROND coordinate,
            # unless the style sets its own percent (style_pcts).
            ParamSpec(key="default_pct", label="Default rounding %", type="float",
                      default=0.0, min=0.0, max=100.0),
            ParamSpec(key="outer_pct", label="Outer, % of stroke", type="float",
                      default=15.0, min=0.0, max=60.0),
            ParamSpec(key="inner_pct", label="Counters, % of stroke", type="float",
                      default=5.0, min=0.0, max=60.0),
            ParamSpec(key="outer_xtra_pct", label="Outer, % of width", type="float",
                      default=3.0, min=0.0, max=60.0),
            ParamSpec(key="inner_xtra_pct", label="Counters, % of width", type="float",
                      default=1.0, min=0.0, max=60.0),
            ParamSpec(key="outer_min", label="Outer floor (units)", type="float",
                      default=2.0, min=0.0, max=100.0),
            ParamSpec(key="inner_min", label="Counter floor (units)", type="float",
                      default=1.0, min=0.0, max=100.0),
            # {instance name: percent} — per-style rounding, grade-style.
            # Edited from the instance rows' R badge, not the flyout.
            ParamSpec(key="style_pcts", label="Per-style rounding", type="table",
                      default={}),
            # {master name: {"outer": units, "inner": units}} — sparse,
            # absolute-unit overrides pinned at masters; everything not
            # named follows the formula exactly. Sidecar-only.
            ParamSpec(key="master_overrides", label="Per-master overrides",
                      type="table", default={}),
        ],
        default_enabled=False,
        # Enabling rounding ALWAYS adds the ROND axis (0-100, default
        # 0 = sharp) with twin masters; styles take their rounding as a
        # ROND coordinate (default_pct / style_pcts), grade-style.
        # Declaring the tag keeps the one-injector-per-tag rule and lets
        # the axes endpoint label it transform_injected — live-preview
        # state like SPAC and GRAD, never per-instance data.
        injected_axis_tag="ROND",
        # Percent edits only re-stamp instance coordinates (apply, the
        # font stage): the server can skip the full shadow re-round.
        font_stage_param_keys=("default_pct", "style_pcts"),
    )

    def validate(self, params: dict) -> None:
        pcts = params.get("style_pcts") or {}
        if not isinstance(pcts, dict):
            raise ValueError("per-style rounding must map style names to percents")
        for name, pct in pcts.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("per-style rounding: a style name is empty")
            try:
                num = float(pct)
            except (TypeError, ValueError):
                raise ValueError(f"per-style rounding for '{name}' must be a number")
            if not 0 <= num <= 100:
                raise ValueError(f"per-style rounding for '{name}' must be 0-100")
        over = params.get("master_overrides") or {}
        if not isinstance(over, dict):
            raise ValueError("master overrides must map master names to values")
        for name, entry in over.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("master overrides: a master name is empty")
            if not isinstance(entry, dict):
                raise ValueError("override for '%s' must be an object with outer/inner" % name)
            bad = sorted(set(entry) - {"outer", "inner"})
            if bad:
                raise ValueError("override for '%s' has unknown key(s): %s"
                                 % (name, ", ".join(bad)))
            for key, val in entry.items():
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    raise ValueError("override for '%s': %s must be a number" % (name, key))
                if not 0 <= num <= 10000:
                    raise ValueError("override for '%s': %s must be between 0 and 10000 units"
                                     % (name, key))

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        # The outlines were rounded at the source stage; here each named
        # instance takes its rounding as a ROND coordinate — style_pcts
        # for styles that set their own, default_pct for the rest.
        from fontTools.ttLib import TTFont

        default_pct = float(params.get("default_pct", 0.0) or 0.0)
        style_pcts = params.get("style_pcts") or {}
        font = TTFont(str(vf_path))
        if "fvar" not in font:
            font.close()
            return vf_path
        fvar = font["fvar"]
        if not any(a.axisTag == "ROND" for a in fvar.axes):
            font.close()
            return vf_path
        name_table = font["name"]
        stamped = 0
        for inst in fvar.instances:
            sub = name_table.getDebugName(inst.subfamilyNameID) or ""
            pct = style_pcts.get(sub, default_pct)
            try:
                pct = min(100.0, max(0.0, float(pct)))
            except (TypeError, ValueError):
                pct = default_pct
            inst.coordinates["ROND"] = pct
            stamped += 1
        font.save(str(vf_path))
        font.close()
        ctx.log("round_corners: ROND stamped on %d named instance(s) "
                "(default %g%%, %d style override(s))"
                % (stamped, default_pct, len(style_pcts)))
        return vf_path

    def apply_to_source(self, font, params: dict, ctx: BuildContext) -> dict:
        from .. import control_axes
        try:
            targets = corner_rounding.control_targets(control_axes.load(ctx.source_path))
        except Exception:  # noqa: BLE001 — a broken sidecar must not block the build
            targets = {}
        # Rounding IS the axis: sharp/rounded twins on ROND 0-100,
        # default 0. Styles dial their own position via apply().
        stats = corner_rounding.round_font(font, params, targets=targets, log=ctx.log,
                                           axis_max=100.0)
        ctx.log("round_corners: %d corners over %d layers; %d clamped, %d collapsed, "
                "%d buried in overlaps, %d concentric at thin walls%s%s"
                % (stats["corners"], stats["layers"], stats["clamped"], stats["collapsed"],
                   stats["hidden"], stats["concentric"],
                   ", %d master override(s)" % stats["overrides"] if stats.get("overrides") else "",
                   "; ROND axis 0-100 (default 0 = sharp), %d twin layers" % stats["twins"]))
        return stats
