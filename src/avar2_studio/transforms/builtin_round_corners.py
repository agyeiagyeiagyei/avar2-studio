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
            # {master name: {"outer": units, "inner": units}} — sparse,
            # absolute-unit overrides pinned at masters; everything not
            # named follows the formula exactly. No flyout editor yet.
            ParamSpec(key="master_overrides", label="Per-master overrides",
                      type="table", default={}),
            ParamSpec(key="rounding_axis", label="Expose as ROND axis", type="bool",
                      default=False),
            ParamSpec(key="axis_max", label="Axis maximum", type="float",
                      default=100.0, min=1.0, max=1000.0),
        ],
        default_enabled=False,
    )

    def validate(self, params: dict) -> None:
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
        # Source-stage: the compiled font is already rounded.
        return vf_path

    def apply_to_source(self, font, params: dict, ctx: BuildContext) -> dict:
        from .. import control_axes
        try:
            targets = corner_rounding.control_targets(control_axes.load(ctx.source_path))
        except Exception:  # noqa: BLE001 — a broken sidecar must not block the build
            targets = {}
        axis_max = float(params.get("axis_max", 100.0)) if params.get("rounding_axis") else None
        stats = corner_rounding.round_font(font, params, targets=targets, log=ctx.log,
                                           axis_max=axis_max)
        ctx.log("round_corners: %d corners over %d layers; %d clamped, %d collapsed, "
                "%d buried in overlaps, %d concentric at thin walls%s%s"
                % (stats["corners"], stats["layers"], stats["clamped"], stats["collapsed"],
                   stats["hidden"], stats["concentric"],
                   ", %d master override(s)" % stats["overrides"] if stats.get("overrides") else "",
                   "; ROND axis 0-%g (default 0 = sharp), %d twin layers"
                   % (axis_max, stats["twins"]) if axis_max is not None else ""))
        return stats
