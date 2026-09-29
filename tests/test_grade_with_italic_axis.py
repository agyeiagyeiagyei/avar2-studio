"""Grade generation on a source with an italic axis.

The interpolation model behind the grade braces spans the parametric
axes only, so an italic master — same XTRA/XOPQ/YOPQ as its upright,
ital 1 — used to land on its upright's location and fontTools refused
the model ("Locations must be unique."): any source with an italic axis
silently lost its GRAD axis, while the build reported success.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from glyphsLib import GSFont
from glyphsLib.classes import GSAxis, GSFontMaster, GSLayer

from avar2_studio import grade_shadow

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SRC = EXAMPLES / "crispy-mini" / "sources" / "CrispyMini.glyphs"


def graded_build(tmp_path, with_italics):
    """A copy of crispy-mini, a grade declaration, and (optionally) an
    italic master sheared from each upright."""
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(SRC, src)
    font = GSFont(str(src))
    if with_italics:
        axis = GSAxis()
        axis.name, axis.axisTag = "Italic", "ital"
        font.axes.append(axis)
        for m in list(font.masters):
            m.axes = list(m.axes) + [0]
        for m in list(font.masters):
            dup = GSFontMaster()
            dup.name = "%s Italic" % m.name
            dup.axes = list(m.axes)[:-1] + [1]
            font.masters.append(dup)
            for g in font.glyphs:
                base = g.layers[m.id]
                layer = base.copy() if hasattr(base, "copy") else GSLayer()
                layer.layerId = dup.id
                layer.associatedMasterId = dup.id
                g.layers.append(layer)
    font.save(str(src))
    (tmp_path / "CrispyMini-grade.json").write_text(
        '{"version": 1, "enabled": true, "default_pct": 0.25, "intensity": 1.0,'
        ' "clamp_to_headroom": true, "instances": [{"name": "Mid", "pct": 0.25}]}')
    return src, {"Mid": {"XTRA": 1712.0, "XOPQ": 509.0, "YOPQ": 232.0}}


def grad_layers(path):
    font = GSFont(str(grade_shadow._control_axes.shadow_path_for(path)))
    idx = next(i for i, a in enumerate(font.axes) if str(a.axisTag).upper() == "GRAD")
    out = []
    for g in font.glyphs:
        for layer in g.layers:
            coords = dict(layer.attributes or {}).get("coordinates")
            if coords and len(coords) > idx and float(coords[idx]):
                out.append((g.name, float(coords[idx])))
    return out


def test_an_upright_source_gets_its_grade_braces(tmp_path):
    src, coords = graded_build(tmp_path, with_italics=False)
    assert grade_shadow.apply_grades(src, coords) is not None
    assert grad_layers(src)


def test_a_source_with_an_italic_axis_gets_the_same(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    src0, coords0 = graded_build(plain, with_italics=False)
    assert grade_shadow.apply_grades(src0, coords0) is not None
    src, coords = graded_build(tmp_path, with_italics=True)
    assert grade_shadow.apply_grades(src, coords) is not None, \
        "an italic master must not collide with its upright in the grade model"
    assert grad_layers(src) == grad_layers(src0)
