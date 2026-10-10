"""The slant transform: build-time italics on a slnt axis, mirroring the
Slant Master plugin's choices (shear about x-height/2, LSB and advance
preserved, grid-snapped)."""
import math

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib.tables._g_l_y_f import Glyph
from glyphsLib.classes import (
    GSAnchor, GSAxis, GSComponent, GSFont, GSFontMaster, GSGlyph, GSLayer,
    GSNode, GSPath,
)

from avar2_studio.transforms import BuildContext
from avar2_studio.transforms.builtin_slant import SlantTransform
from avar2_studio.transforms import slanting

PARAMS = {"narrow_angle": 11.0, "wide_angle": 6.0}


def box(x0, x1, y0, y1):
    path = GSPath()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        path.nodes.append(GSNode((x, y), "line"))
    path.closed = True
    return path


def layer_for(master_id, paths, width):
    layer = GSLayer()
    layer.layerId = layer.associatedMasterId = master_id
    for p in paths:
        layer.paths.append(p)
    layer.width = width
    return layer


def slant_fixture():
    font = GSFont()
    font.axes = []
    ax = GSAxis(); ax.name, ax.axisTag = "X-Transparency", "XTRA"
    font.axes.append(ax)
    narrow, wide = GSFontMaster(), GSFontMaster()
    narrow.name, narrow.axes, narrow.xHeight = "Narrow", [100], 500
    wide.name, wide.axes, wide.xHeight = "Wide", [1000], 500
    font.masters.append(narrow)
    font.masters.append(wide)
    glyph = GSGlyph("box")
    font.glyphs.append(glyph)
    glyph.layers.append(layer_for(narrow.id, [box(0, 300, 0, 700)], 400))
    glyph.layers.append(layer_for(wide.id, [box(0, 900, 0, 700)], 1000))
    return font, glyph, narrow, wide


def test_angles_follow_mean_advance():
    font, *_ = slant_fixture()
    angles = slanting.master_angles(font, PARAMS)
    by_name = {str(m.name): angles[str(m.id)] for m in font.masters}
    assert by_name == {"Narrow": 11.0, "Wide": 6.0}


def test_master_angle_overrides_and_unknown_names():
    font, *_ = slant_fixture()
    angles = slanting.master_angles(font, dict(PARAMS, master_angles={"Wide": 9.0}))
    by_name = {str(m.name): angles[str(m.id)] for m in font.masters}
    assert by_name["Wide"] == 9.0 and by_name["Narrow"] == 11.0
    with pytest.raises(ValueError, match="Wid "):
        slanting.master_angles(font, dict(PARAMS, master_angles={"Wid ": 9.0}))


def test_slant_adds_axis_twins_and_preserves_spacing():
    font, glyph, narrow, wide = slant_fixture()
    stats = slanting.slant_font(font, PARAMS)
    assert stats["axis"] and stats["max_angle"] == 11.0
    assert [str(a.axisTag) for a in font.axes] == ["XTRA", "slnt"]
    assert len(font.masters) == 4
    assert all(list(m.axes)[-1] == -11.0 for m in font.masters[2:])
    assert str(font.masters[2].name) == "Narrow Slanted"
    # original layers untouched
    orig = [(float(n.position.x), float(n.position.y))
            for n in glyph.layers[narrow.id].paths[0].nodes]
    assert orig == [(0.0, 0.0), (300.0, 0.0), (300.0, 700.0), (0.0, 700.0)]
    # the narrow twin: shear 11 deg about y0 = 250, LSB back to 0, snapped
    t = math.tan(math.radians(11.0))
    twin = glyph.layers[font.masters[2].id]
    pts = [(float(n.position.x), float(n.position.y)) for n in twin.paths[0].nodes]
    assert min(x for x, _ in pts) == 0.0, "LSB preserved"
    assert float(twin.width) == 400.0, "advance preserved"
    assert [y for _, y in pts] == [0.0, 0.0, 700.0, 700.0], "y untouched"
    # top edge sits tan(11)*700 to the right of the bottom edge
    assert pts[3][0] - pts[0][0] == pytest.approx(700 * t, abs=1.0)


def test_components_and_anchors_follow():
    font, glyph, narrow, wide = slant_fixture()
    comp_glyph = GSGlyph("acute")
    font.glyphs.append(comp_glyph)
    for m, w in ((narrow, 400), (wide, 1000)):
        L = layer_for(m.id, [box(0, 100, 600, 700)], w)
        comp_glyph.layers.append(L)
    carrier = GSGlyph("abox")
    font.glyphs.append(carrier)
    for m, w in ((narrow, 400), (wide, 1000)):
        L = layer_for(m.id, [box(0, 300, 0, 500)], w)
        c = GSComponent("acute")
        c.position = (50, 100)
        L.components.append(c)
        a = GSAnchor()
        a.name, a.position = "top", (150, 500)
        L.anchors.append(a)
        carrier.layers.append(L)
    slanting.slant_font(font, PARAMS)
    t = math.tan(math.radians(11.0))
    twin = carrier.layers[font.masters[2].id]
    pts = [(float(n.position.x), float(n.position.y)) for n in twin.paths[0].nodes]
    shift = -min(0 + (0 - 250) * t, 0 + (500 - 250) * t)  # LSB restore
    assert min(x for x, _ in pts) == 0.0
    comp = twin.components[0]
    assert float(comp.position.x) == pytest.approx(50 + 100 * t + shift, abs=1.0)
    anchor = twin.anchors[0]
    ax = anchor.position[0] if isinstance(anchor.position, tuple) else anchor.position.x
    assert float(ax) == pytest.approx(150 + (500 - 250) * t + shift, abs=1.0)


def test_a_brace_interpolates_its_angle():
    font, glyph, narrow, wide = slant_fixture()
    brace = layer_for(narrow.id, [box(0, 600, 0, 700)], 700)
    brace.layerId = "BRACE-1"
    brace.name = "{550}"
    brace.attributes["coordinates"] = [550]
    glyph.layers.append(brace)
    slanting.slant_font(font, PARAMS)
    # XTRA 550 is halfway 100..1000 -> angle halfway 11..6 = 8.5
    t = math.tan(math.radians(8.5))
    twins = [L for L in glyph.layers
             if isinstance(L.attributes.get("coordinates"), (list, tuple))
             and list(L.attributes["coordinates"]) == [550, -11.0]]
    assert len(twins) == 1
    pts = [(float(n.position.x), float(n.position.y)) for n in twins[0].paths[0].nodes]
    assert pts[3][0] - pts[0][0] == pytest.approx(700 * t, abs=1.5)


def make_stamp_font(path):
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef"])
    fb.setupCharacterMap({})
    fb.setupGlyf({".notdef": Glyph()})
    fb.setupHorizontalMetrics({".notdef": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "T", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar(
        [("XTRA", 1, 1, 1000, "X-Transparency"), ("slnt", -11, 0, 0, "Slant")],
        [dict(location={"XTRA": 100, "slnt": 0}, stylename="Thin"),
         dict(location={"XTRA": 900, "slnt": 0}, stylename="Bold"),
         # Italic by name: gftools' doubled set sits at the axis
         # minimum and is never stamped (same rule for "… Slanted").
         dict(location={"XTRA": 100, "slnt": -11}, stylename="Thin Italic"),
         dict(location={"XTRA": 900, "slnt": -7}, stylename="Bold Slanted")])
    fb.save(str(path))


def test_apply_stamps_percent_of_the_axis_minimum(tmp_path):
    p = tmp_path / "f.ttf"
    make_stamp_font(p)
    t = SlantTransform()
    ctx = BuildContext(build_dir=tmp_path, source_path=tmp_path / "F.glyphs",
                       glyphs_path=tmp_path / "F.glyphs", family="F")
    t.apply(p, {"default_pct": 50.0, "style_pcts": {"Bold": 100.0}}, ctx)
    from fontTools.ttLib import TTFont
    f = TTFont(str(p))
    name = f["name"]
    coords = {name.getDebugName(i.subfamilyNameID): i.coordinates.get("slnt")
              for i in f["fvar"].instances}
    # Italic/Slanted-named styles keep their own slnt — never stamped.
    assert coords == {"Thin": -5.5, "Bold": -11.0,
                      "Thin Italic": -11.0, "Bold Slanted": -7.0}


def test_validation():
    t = SlantTransform()
    base = t.spec.coerce_params({})
    for bad in ({"Bold": "x"}, {"Bold": 150}, {"": 5}):
        with pytest.raises(ValueError):
            t.validate(dict(base, style_pcts=bad))
    with pytest.raises(ValueError):
        t.validate(dict(base, master_angles={"Narrow": 90}))
    # Italic/Slanted-named styles can't take a percent — they're exempt
    # from stamping, so a style_pcts entry for one would silently do
    # nothing; reject it loudly instead.
    for exempt in ("Thin Italic", "Display Slanted"):
        with pytest.raises(ValueError):
            t.validate(dict(base, style_pcts={exempt: 40}))
    t.validate(dict(base, style_pcts={"Bold": 40}, master_angles={"Narrow": 9}))
