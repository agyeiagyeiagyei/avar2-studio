"""Slant — a SOURCE-stage transform: build-time italics on a slnt axis.

The engine (:mod:`avar2_studio.transforms.slanting`) mirrors the Slant
Master plugin's settled choices: shear about y = x-height/2, LSB and
advance preserved, no extrema correction. Enabling the transform adds a
``slnt`` axis (default 0 = upright); each master slants at its own angle
— linear in mean advance between the Narrowest/Widest dials, overridable
per master in degrees via the sidecar — and every named style takes its
slant as a percent of the axis, grade-style.
"""

from __future__ import annotations

from pathlib import Path

from . import slanting
from .base import BuildContext, ParamSpec, Transform, TransformSpec


def is_slant_exempt(style_name: str) -> bool:
    """A style named "… Italic"/"… Slanted" is italic BY NAME: the
    percent model never stamps it — it keeps its own slnt coordinate
    (gftools' instance doubling pins the Italic set at the axis
    minimum, i.e. fully slanted). Mirrored by the wasm port
    (fix.rs is_slant_exempt). See
    docs/decisions/0006-slant-name-exemption-italic-doubling.md."""
    lower = style_name.lower()
    return "italic" in lower or "slanted" in lower


class SlantTransform(Transform):
    spec = TransformSpec(
        id="slant",
        name="Slant",
        description=("Slant every master at build time on a slnt axis (0 = upright); "
                     "angles follow each master's width between the two dials, and "
                     "each style takes its slant as a percent."),
        stage="source",
        params=[
            ParamSpec(key="default_pct", label="Default slant %", type="float",
                      default=0.0, min=0.0, max=100.0),
            ParamSpec(key="narrow_angle", label="Narrowest angle (deg)", type="float",
                      default=11.0, min=0.0, max=45.0),
            ParamSpec(key="wide_angle", label="Widest angle (deg)", type="float",
                      default=6.0, min=0.0, max=45.0),
            # {instance name: percent} — per-style slant, grade-style.
            # Edited from the instance rows' S badge, not the flyout.
            ParamSpec(key="style_pcts", label="Per-style slant", type="table",
                      default={}),
            # {master name: degrees} — sidecar-only angle overrides.
            ParamSpec(key="master_angles", label="Per-master angles", type="table",
                      default={}),
        ],
        default_enabled=False,
        injected_axis_tag="slnt",
        font_stage_param_keys=("default_pct", "style_pcts"),
        source_priority=10,
    )

    def validate(self, params: dict) -> None:
        pcts = params.get("style_pcts") or {}
        if not isinstance(pcts, dict):
            raise ValueError("per-style slant must map style names to percents")
        for name, pct in pcts.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("per-style slant: a style name is empty")
            if is_slant_exempt(name):
                raise ValueError(
                    f"'{name}' is italic by name — Italic/Slanted styles keep "
                    "their own slnt coordinate and are never stamped")
            try:
                num = float(pct)
            except (TypeError, ValueError):
                raise ValueError(f"per-style slant for '{name}' must be a number") from None
            if not 0 <= num <= 100:
                raise ValueError(f"per-style slant for '{name}' must be 0-100")
        angles = params.get("master_angles") or {}
        if not isinstance(angles, dict):
            raise ValueError("per-master angles must map master names to degrees")
        for name, deg in angles.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("per-master angles: a master name is empty")
            try:
                num = float(deg)
            except (TypeError, ValueError):
                raise ValueError(f"angle for '{name}' must be a number") from None
            if not 0 <= num <= 45:
                raise ValueError(f"angle for '{name}' must be 0-45 degrees")

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        # Each named instance takes its slant as a slnt coordinate:
        # percent of the axis's (negative) minimum.
        from fontTools.ttLib import TTFont

        default_pct = float(params.get("default_pct", 0.0) or 0.0)
        style_pcts = params.get("style_pcts") or {}
        font = TTFont(str(vf_path))
        if "fvar" not in font:
            font.close()
            return vf_path
        fvar = font["fvar"]
        slnt = next((a for a in fvar.axes if a.axisTag == "slnt"), None)
        if slnt is None:
            font.close()
            return vf_path
        name_table = font["name"]
        stamped = 0
        exempt = 0
        for inst in fvar.instances:
            sub = name_table.getDebugName(inst.subfamilyNameID) or ""
            if is_slant_exempt(sub):
                # Italic by name (e.g. gftools' doubled "… Italic" set,
                # pinned at the axis minimum): keeps its own slnt.
                exempt += 1
                continue
            pct = style_pcts.get(sub, default_pct)
            try:
                pct = min(100.0, max(0.0, float(pct)))
            except (TypeError, ValueError):
                pct = default_pct
            inst.coordinates["slnt"] = slnt.minValue * pct / 100.0
            stamped += 1
        font.save(str(vf_path))
        font.close()
        ctx.log("slant: slnt stamped on %d named instance(s) (default %g%%, %d style "
                "override(s))%s"
                % (stamped, default_pct, len(style_pcts),
                   "; %d Italic/Slanted style(s) keep their own slnt" % exempt
                   if exempt else ""))
        return vf_path

    def apply_to_source(self, font, params: dict, ctx: BuildContext) -> dict:
        stats = slanting.slant_font(font, params, log=ctx.log)
        if stats.get("axis"):
            ctx.log("slant: slnt axis 0 to -%g over %d master(s), %d twin layers"
                    % (stats["max_angle"], stats["masters"], stats["twins"]))
        return stats
