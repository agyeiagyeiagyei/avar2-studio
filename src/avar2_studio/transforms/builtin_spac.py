"""SPAC — the spacing transform (built-in, transform #1).

Injects a ``SPAC`` fvar axis into the compiled font via ``gftools-gen-spac``.
Spacing is realized purely as advance-width / sidebearing deltas — outlines
never move. Because the injected axis has no source master, it surfaces in
the studio as an ordinary *parametric* slider (see the built-font overlay in
``server.get_axes``).

Verified on the fontc build path: adds a ``SPAC`` axis, advances track
uniformly, outline coordinates are byte-identical across the axis.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from fontTools.ttLib import TTFont

from .base import BuildContext, ParamSpec, Transform, TransformSpec


def _spac_output_name(family: str, vf_path: Path) -> str:
    """Build the output filename from the input font's ACTUAL fvar axes plus
    SPAC, e.g. ``Crispy[SPAC,XOPQ,XTRA,YOPQ,crbr].ttf`` — so the served /
    downloaded name matches the bytes instead of hiding the other axes behind
    a bare ``[SPAC]``. Falls back to ``[SPAC]`` if the font can't be read."""
    tags = ["SPAC"]
    try:
        font = TTFont(str(vf_path))
        if "fvar" in font:
            tags = sorted({a.axisTag for a in font["fvar"].axes} | {"SPAC"})
        font.close()
    except Exception:
        pass
    return f"{family}[{','.join(tags)}].ttf"


class SpacTransform(Transform):
    spec = TransformSpec(
        id="spac",
        name="Spacing (SPAC axis)",
        description="Inject a SPAC parametric axis; advances track uniformly, outlines unchanged.",
        params=[
            # gftools-gen-spac's numbers are per-side, so ±N ≈ ±2N advance
            # units. -20…40 matches Crispy's proven build; editable per project.
            ParamSpec(key="min", label="Min", type="int", default=-20),
            ParamSpec(key="max", label="Max", type="int", default=40),
        ],
        default_enabled=False,
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

        # gftools-gen-spac's --out is quirky (treats the arg as a full path
        # despite the 'dir' help text); copy then --inplace is the reliable
        # form. Co-locate the output with the input build.
        out = vf_path.parent / _spac_output_name(ctx.family, vf_path)
        shutil.copy2(vf_path, out)
        try:
            proc = subprocess.run(
                ["gftools-gen-spac", "--inplace", str(out), str(lo), str(hi)],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
            out.unlink(missing_ok=True)
            raise RuntimeError(f"gftools-gen-spac could not run: {e}") from e
        if proc.returncode != 0:
            out.unlink(missing_ok=True)
            raise RuntimeError(
                f"gftools-gen-spac failed: {(proc.stderr or proc.stdout or '').strip()[:500]}"
            )
        ctx.log(f"SPAC axis injected ({lo}…{hi}) → {out.name}")
        return out
