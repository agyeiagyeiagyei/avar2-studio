"""Thin wrappers around gftools fixers, exposed as post-build transforms.

Each loads the compiled VF, calls a gftools function in-process, and writes
the result back. None of these change the fvar axis set, so they save in
place (atomic tmp + replace) and keep the font's filename — no
``injected_axis_tag``.

Dropped from the original catalog on closer inspection:
- ``gen-avar2`` — the studio's core build already generates the avar2 table
  from the CSV; a transform would double-apply it.
- ``to-avar1`` — needs an explicit mapping and does a full designspace
  round-trip / VF rebuild; too heavy for a toggle. Revisit as an explicit
  "export avar1 fallback" action later.
"""

from __future__ import annotations

import os
from pathlib import Path

from fontTools.ttLib import TTFont

from .base import BuildContext, Transform, TransformSpec


def _save_in_place(font: TTFont, vf_path: Path) -> None:
    """Atomic-ish overwrite: save to a temp sibling then os.replace, so a
    reader mid-request never sees a half-written file."""
    tmp = vf_path.with_name(vf_path.stem + ".tmp.ttf")
    font.save(str(tmp))
    font.close()
    os.replace(tmp, vf_path)


class FixInstancesTransform(Transform):
    spec = TransformSpec(
        id="fix_instances",
        name="Clean fvar instances",
        description="Regenerate the font's named instances (fix-instances) so they match the current axes.",
    )

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        from gftools.fix import fix_fvar_instances
        vf_path = Path(vf_path)
        font = TTFont(str(vf_path))
        fix_fvar_instances(font)
        _save_in_place(font, vf_path)
        ctx.log("fvar named instances regenerated")
        return vf_path


class GenStatTransform(Transform):
    spec = TransformSpec(
        id="gen_stat",
        name="Rebuild STAT table",
        description="Generate the STAT table from the Google Fonts axis registry. Registered axes (wght/wdth/opsz) only — custom axes need a STAT config.",
    )

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        from gftools.stat import gen_stat_tables
        vf_path = Path(vf_path)
        font = TTFont(str(vf_path))
        try:
            gen_stat_tables([font])
        except Exception as e:  # noqa: BLE001 — custom axes aren't in the GF registry
            font.close()
            raise RuntimeError(
                f"gen-stat failed (likely an axis not in the GF registry): {e}"
            ) from e
        _save_in_place(font, vf_path)
        ctx.log("STAT table rebuilt")
        return vf_path


class FixUnhintedTransform(Transform):
    spec = TransformSpec(
        id="fix_unhinted",
        name="Smooth unhinted rendering",
        description="Add gasp + prep tables so an unhinted variable font rasterizes with grayscale anti-aliasing at all sizes.",
    )

    def apply(self, vf_path: Path, params: dict, ctx: BuildContext) -> Path:
        from gftools.fix import fix_unhinted_font
        vf_path = Path(vf_path)
        font = TTFont(str(vf_path))
        fix_unhinted_font(font)
        _save_in_place(font, vf_path)
        ctx.log("gasp/prep added for smooth unhinted rendering")
        return vf_path
