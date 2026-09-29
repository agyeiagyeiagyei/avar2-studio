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
        description=("Round every corner before the compile; the radius follows each "
                     "layer's stroke weight (XOPQ), outer corners and counters separately."),
        stage="source",
        params=[
            ParamSpec(key="outer_pct", label="Outer, % of stroke", type="float",
                      default=15.0, min=0.0, max=60.0),
            ParamSpec(key="inner_pct", label="Counters, % of stroke", type="float",
                      default=5.0, min=0.0, max=60.0),
            ParamSpec(key="outer_min", label="Outer floor (units)", type="float",
                      default=2.0, min=0.0, max=100.0),
            ParamSpec(key="inner_min", label="Counter floor (units)", type="float",
                      default=1.0, min=0.0, max=100.0),
        ],
        default_enabled=False,
    )

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        # Source-stage: the compiled font is already rounded.
        return vf_path

    def apply_to_source(self, font, params: dict, ctx: BuildContext) -> dict:
        from .. import control_axes
        try:
            targets = corner_rounding.control_targets(control_axes.load(ctx.source_path))
        except Exception:  # noqa: BLE001 — a broken sidecar must not block the build
            targets = {}
        stats = corner_rounding.round_font(font, params, targets=targets, log=ctx.log)
        ctx.log("round_corners: %d corners over %d layers; %d clamped, %d collapsed"
                % (stats["corners"], stats["layers"], stats["clamped"], stats["collapsed"]))
        return stats
