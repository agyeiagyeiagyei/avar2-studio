"""Per-style rounding: named instances take their ROND coordinate from
style_pcts / default_pct in the transform's font-stage apply()."""
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib.tables._g_l_y_f import Glyph

from avar2_studio.transforms import BuildContext
from avar2_studio.transforms.builtin_round_corners import RoundCornersTransform


def make_font(path: Path, with_rond=True):
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef"])
    fb.setupCharacterMap({})
    fb.setupGlyf({".notdef": Glyph()})
    fb.setupHorizontalMetrics({".notdef": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "T", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    axes = [("XOPQ", 1, 1, 1000, "X-Opacity")]
    if with_rond:
        axes.append(("ROND", 0, 0, 100, "Rounding"))
    instances = [
        dict(location={"XOPQ": 100, **({"ROND": 0} if with_rond else {})}, stylename="Thin"),
        dict(location={"XOPQ": 900, **({"ROND": 0} if with_rond else {})}, stylename="Bold"),
    ]
    fb.setupFvar(axes, instances)
    fb.save(str(path))


def ctx(tmp_path):
    return BuildContext(build_dir=tmp_path, source_path=tmp_path / "F.glyphs",
                        glyphs_path=tmp_path / "F.glyphs", family="F")


def rond_coords(path):
    from fontTools.ttLib import TTFont
    f = TTFont(str(path))
    name = f["name"]
    return {name.getDebugName(i.subfamilyNameID): i.coordinates.get("ROND")
            for i in f["fvar"].instances}


def test_styles_take_their_percent_and_the_rest_the_default(tmp_path):
    p = tmp_path / "f.ttf"
    make_font(p)
    t = RoundCornersTransform()
    out = t.apply(p, {"default_pct": 30.0, "style_pcts": {"Bold": 80.0}}, ctx(tmp_path))
    assert rond_coords(out) == {"Thin": 30.0, "Bold": 80.0}


def test_percents_clamp_and_default_is_sharp(tmp_path):
    p = tmp_path / "f.ttf"
    make_font(p)
    t = RoundCornersTransform()
    out = t.apply(p, {"style_pcts": {"Bold": 250.0}}, ctx(tmp_path))
    assert rond_coords(out) == {"Thin": 0.0, "Bold": 100.0}


def test_without_a_rond_axis_apply_is_a_no_op(tmp_path):
    p = tmp_path / "f.ttf"
    make_font(p, with_rond=False)
    before = p.read_bytes()
    t = RoundCornersTransform()
    t.apply(p, {"default_pct": 50.0}, ctx(tmp_path))
    assert p.read_bytes() == before


def test_style_pcts_validation():
    t = RoundCornersTransform()
    base = t.spec.coerce_params({})
    for bad in ([1], {"Bold": "abc"}, {"Bold": -5}, {"Bold": 150}, {"": 10}):
        with pytest.raises(ValueError):
            t.validate(dict(base, style_pcts=bad))
    t.validate(dict(base, style_pcts={"Bold": 80}))
