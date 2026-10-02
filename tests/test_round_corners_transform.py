"""The round_corners transform: the geometry (corner_rounding) on
glyphsLib objects, the stage split in the registry, and the pipeline
over a real example source."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest
from glyphsLib import GSFont
from glyphsLib.classes import (
    GSAxis, GSFont as GSFontCls, GSFontMaster, GSGlyph, GSInstance, GSLayer,
    GSNode, GSPath,
)

from avar2_studio.transforms import BuildContext, registry
from avar2_studio.transforms import config as transforms_config
from avar2_studio.transforms import corner_rounding

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CRISPY_MINI = EXAMPLES / "crispy-mini" / "sources" / "CrispyMini.glyphs"


def box(x0, x1, y0, y1):
    path = GSPath()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        path.nodes.append(GSNode((x, y), "line"))
    path.closed = True
    return path


def rbox(x0, x1, y0, y1):
    """A box wound the other way — how a real source draws a counter."""
    path = box(x0, x1, y0, y1)
    path.reverse()
    return path


def layer_for(master_id, paths, width=600.0):
    layer = GSLayer()
    layer.layerId = layer.associatedMasterId = master_id
    for p in paths:
        layer.paths.append(p)
    layer.width = width
    return layer


def mini_font():
    """Two masters (XOPQ 10 and 1000), one glyph: a rectangle with a
    counter, plus a drawn brace layer between them and a backup layer."""
    font = GSFontCls()
    font.familyName = "Mini"
    font.axes = []   # a fresh GSFont ships with default axes; ours only
    for name, tag in (("X-Transparency", "XTRA"), ("X-Opacity", "XOPQ")):
        ax = GSAxis()
        ax.name, ax.axisTag = name, tag
        font.axes.append(ax)
    thin, bold, wide = GSFontMaster(), GSFontMaster(), GSFontMaster()
    thin.name, thin.axes = "Thin", [100, 10]
    bold.name, bold.axes = "Bold", [100, 1000]
    wide.name, wide.axes = "Wide", [2000, 10]
    font.masters.append(thin)
    font.masters.append(bold)
    font.masters.append(wide)
    glyph = GSGlyph("O")
    font.glyphs.append(glyph)
    def drawing(stroke):
        return [box(0, 400 + stroke, 0, 400 + stroke),
                rbox(200, 200 + stroke * 0.2 + 40, 200, 200 + stroke * 0.2 + 40)]
    glyph.layers.append(layer_for(thin.id, drawing(10)))
    glyph.layers.append(layer_for(bold.id, drawing(1000)))
    glyph.layers.append(layer_for(wide.id, drawing(10)))
    brace = layer_for(bold.id, drawing(500))
    brace.layerId = "BRACE-1"
    brace.name = "{100, 500}"
    brace.attributes["coordinates"] = [100, 500]
    glyph.layers.append(brace)
    backup = layer_for(bold.id, drawing(1000))
    backup.layerId = "BACKUP-1"
    backup.name = "Sep 29, 26 backup"
    glyph.layers.append(backup)
    return font, glyph, thin, bold, wide, brace, backup


def structure(layer):
    return [tuple(str(n.type) for n in p.nodes) for p in layer.paths]


def fitted_radius(layer, path_index=0):
    """Radius of the first round on a path: distance from T2 to the
    corner the tangents meet at (equal to t for a 90° corner)."""
    nodes = layer.paths[path_index].nodes
    for i, n in enumerate(nodes):
        if str(n.type) == "curve":
            t2 = nodes[i].position
            t1 = nodes[(i - 3) % len(nodes)].position
            return max(abs(float(t2.x) - float(t1.x)), abs(float(t2.y) - float(t1.y)))
    return 0.0


PARAMS = {"outer_pct": 15.0, "inner_pct": 5.0, "outer_min": 2.0, "inner_min": 1.0,
          "outer_xtra_pct": 3.0, "inner_xtra_pct": 1.0}


def test_every_interpolating_layer_gains_the_same_corners():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    stats = corner_rounding.round_font(font, PARAMS)
    layers = [glyph.layers[thin.id], glyph.layers[bold.id], glyph.layers[wide.id], brace]
    assert len({str(structure(L)) for L in layers}) == 1
    assert structure(layers[0])[0] == ("line", "offcurve", "offcurve", "curve") * 4
    assert structure(backup) == [("line",) * 4, ("line",) * 4], "a backup is not touched"
    assert stats["corners"] == 8 and stats["layers"] == 4


def test_the_radius_blends_stroke_and_width():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, PARAMS)
    # 15% of XOPQ + 3% of XTRA on outer corners, 5% + 1% on counters.
    assert fitted_radius(glyph.layers[bold.id]) == pytest.approx(153.0, abs=1)   # 150 + 3
    assert fitted_radius(brace) == pytest.approx(78.0, abs=1)                    # 75 + 3
    assert fitted_radius(glyph.layers[thin.id]) == pytest.approx(4.5, abs=1)     # 1.5 + 3
    assert fitted_radius(glyph.layers[bold.id], 1) == pytest.approx(51.0, abs=1) # 50 + 1
    # THE case the blend exists for: hairline stroke, huge width.
    assert fitted_radius(glyph.layers[wide.id]) == pytest.approx(61.5, abs=1)    # 1.5 + 60
    assert fitted_radius(glyph.layers[wide.id], 1) == pytest.approx(20.5, abs=1) # 0.5 + 20


def test_without_the_width_share_the_wide_thin_master_stays_sharp_looking():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, dict(PARAMS, outer_xtra_pct=0.0, inner_xtra_pct=0.0))
    assert fitted_radius(glyph.layers[wide.id]) == pytest.approx(2.0, abs=1)     # the old rule


def test_a_correction_layers_radius_follows_its_target():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    targets = corner_rounding.control_targets({"axes": [{"tag": "lcwd", "layers": [
        {"glyph": "O", "location": {"XTRA": 100, "XOPQ": 500}, "target": {"XOPQ": 200.0}},
    ]}]})
    corner_rounding.round_font(font, PARAMS, targets=targets)
    # 15% of the TARGET's XOPQ 200 + 3% of the LOCATION's XTRA 100 (no target XTRA)
    assert fitted_radius(brace) == pytest.approx(33.0, abs=1)


def test_a_targets_xtra_counts_too():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    targets = corner_rounding.control_targets({"axes": [{"tag": "lcwd", "layers": [
        {"glyph": "O", "location": {"XTRA": 100, "XOPQ": 500},
         "target": {"XOPQ": 200.0, "XTRA": 1500.0}},
    ]}]})
    corner_rounding.round_font(font, PARAMS, targets=targets)
    assert fitted_radius(brace) == pytest.approx(75.0, abs=1)  # 30 + 3% of 1500


def test_advances_and_extremes_survive():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    before = {L.layerId: (float(L.width),
                          max(float(n.position.x) for p in L.paths for n in p.nodes))
              for L in glyph.layers}
    corner_rounding.round_font(font, PARAMS)
    for L in glyph.layers:
        assert float(L.width) == before[L.layerId][0]
        if L is not backup:
            assert max(float(n.position.x) for p in L.paths for n in p.nodes) == before[L.layerId][1]


def test_a_second_pass_finds_nothing_left_to_round():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, PARAMS)
    once = {L.layerId: [[(float(n.position.x), float(n.position.y)) for n in p.nodes]
                        for p in L.paths] for L in glyph.layers}
    stats = corner_rounding.round_font(font, PARAMS)
    assert stats["corners"] == 0
    assert {L.layerId: [[(float(n.position.x), float(n.position.y)) for n in p.nodes]
                        for p in L.paths] for L in glyph.layers} == once


def test_a_brace_with_its_own_structure_is_left_alone_and_named():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    brace.paths[0].nodes.append(GSNode((5, 5), "line"))
    logs = []
    stats = corner_rounding.round_font(font, PARAMS, log=logs.append)
    assert stats["skipped_layers"] == ["O/{100, 500}"]
    assert any("do not match" in m for m in logs)
    assert structure(glyph.layers[bold.id])[0][1] == "offcurve", "the masters still round"


def test_without_an_xopq_axis_nothing_happens():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    font.axes[1].axisTag = "wght"
    logs = []
    stats = corner_rounding.round_font(font, PARAMS, log=logs.append)
    assert stats["corners"] == 0
    assert structure(glyph.layers[bold.id]) == [("line",) * 4, ("line",) * 4]
    assert any("no XOPQ axis" in m for m in logs)


# --- the registry stage split -------------------------------------------------


def test_the_post_build_chain_never_sees_a_source_transform(tmp_path):
    registry.discover(force=True)
    src = tmp_path / "Mini.glyphs"
    src.write_text("stub")
    transforms_config.sidecar_path_for(src).write_text(json.dumps({"version": 1, "transforms": [
        {"type": "round_corners", "enabled": True, "params": {}},
        {"type": "spac", "enabled": True, "params": {"min": -20, "max": 40}},
    ]}))
    assert [t.spec.id for t, _ in registry.active(src)] == ["spac"]
    source = registry.active(src, stage="source")
    assert [t.spec.id for t, _ in source] == ["round_corners"]
    assert source[0][1]["outer_pct"] == 15.0, "params come back filled from the schema"


# --- the pipeline, on the real example ----------------------------------------


def test_the_transform_rounds_crispy_mini_end_to_end(tmp_path):
    registry.discover(force=True)
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(CRISPY_MINI, src)
    font = GSFont(str(src))
    before = sum(1 for g in font.glyphs for p in g.layers[font.masters[0].id].paths
                 for n in p.nodes if str(n.type) == "offcurve")
    transform = registry.REGISTRY["round_corners"]
    logs = []
    ctx = BuildContext(build_dir=tmp_path, source_path=src, glyphs_path=src,
                       family="CrispyMini", log=logs.append)
    stats = transform.apply_to_source(font, transform.spec.coerce_params({}), ctx)
    assert stats["corners"] > 100
    mids = [m.id for m in font.masters]
    def sig(L): return [(bool(p.closed), tuple(str(n.type) for n in p.nodes)) for p in L.paths]
    for g in font.glyphs:
        assert len({str(sig(g.layers[mid])) for mid in mids}) == 1, g.name
    after = sum(1 for g in font.glyphs for p in g.layers[mids[0]].paths
                for n in p.nodes if str(n.type) == "offcurve")
    # crispy-mini is drawn WITH curves — only its line-line corners round,
    # each adding two handles per layer.
    assert after == before + 2 * stats["corners"]
    assert any(m.startswith("round_corners:") for m in logs)


# --- the CSV sync beside it ---------------------------------------------------


def test_a_new_source_axis_column_is_backfilled_with_its_default(tmp_path):
    """The sync adds a column when the source gains an axis (ital). Blank
    cells in a parametric column abort the whole avar2 build, so every
    row gets the axis default — a zero avar2 delta, changing nothing."""
    import shutil as _shutil
    from avar2_studio import csv_io
    src = tmp_path / "CrispyMini.glyphs"
    _shutil.copy2(CRISPY_MINI, src)
    font = GSFont(str(src))
    axis = GSAxis()
    axis.name, axis.axisTag = "Italic", "ital"
    font.axes.append(axis)
    for m in font.masters:
        m.axes = list(m.axes) + [0]
    inst_names = []
    from glyphsLib.classes import GSInstance
    inst = GSInstance()
    inst.name = "Mid"
    inst.axes = [1712, 509, 232, 1]
    font.instances.append(inst)
    font.save(str(src))
    csv_path = tmp_path / "CrispyMini-avar.csv"
    csv_path.write_text(
        "Instance Name,XTRA,XOPQ,YOPQ,WGHT\n"
        "Old-Style,94.0,2.0,2.0,400.0\n")
    assert csv_io.update_csv_from_glyphs(src, csv_path)
    rows = list(csv_path.read_text().splitlines())
    header = rows[0].split(",")
    assert "ital" in header
    ital = header.index("ital")
    old = dict(zip(header, rows[1].split(",")))
    assert old["Instance Name"] == "Old-Style"
    assert old["ital"] and float(old["ital"]) == 0.0, "backfilled with the axis default, not blank"
    mid = next(r.split(",") for r in rows[1:] if r.startswith("Mid"))
    assert mid[ital] == "1.0", "a synced instance keeps its own value"
    assert not any(r.endswith(",") for r in rows[1:]), "no blank cells anywhere"


def test_a_shallow_bend_takes_a_shallower_round():
    """At a near-straight corner t = r*tan(turn/2) explodes — 3.7*r at
    150 degrees — and with long segments the room clamp never bites, so
    the arc used to swing across the outline. t is capped at 2*r."""
    font = GSFontCls()
    font.axes = []
    ax = GSAxis(); ax.name, ax.axisTag = "X-Opacity", "XOPQ"
    font.axes.append(ax)
    m = GSFontMaster(); m.name, m.axes = "Bold", [1000]
    font.masters.append(m)
    glyph = GSGlyph("bend")
    font.glyphs.append(glyph)
    path = GSPath()
    # a 150-degree bend at (2000, 0) between two 4000-unit segments
    for x, y in ((-2000, 0), (2000, 0), (2000 + 4000 * math.cos(math.radians(150)),
                                         4000 * math.sin(math.radians(150))), (-2000, 3000)):
        path.nodes.append(GSNode((x, y), "line"))
    path.closed = True
    glyph.layers.append(layer_for(m.id, [path], width=4000))
    corner_rounding.round_font(font, PARAMS)
    nodes = glyph.layers[m.id].paths[0].nodes
    # every corner rounds; the bend's T1 is the tangent on the incoming
    # horizontal — the rightmost line node at y = 0 followed by a handle
    t1x = max(float(n.position.x) for i, n in enumerate(nodes)
              if str(n.type) == "line" and float(n.position.y) == 0.0
              and str(nodes[(i + 1) % len(nodes)].type) == "offcurve")
    # r = 150 outer; uncapped t would be r*tan(75) = 560; capped: 2r = 300
    assert 2000 - t1x == pytest.approx(300.0, abs=2)


def test_a_corner_buried_in_an_overlap_stays_put():
    """Crispy overlaps construction pieces (R's leg over its stem). A
    buried corner is not a visible corner: rounding it pulls the piece
    back from the junction and the counter shows through."""
    font = GSFontCls()
    font.axes = []
    ax = GSAxis(); ax.name, ax.axisTag = "X-Opacity", "XOPQ"
    font.axes.append(ax)
    m = GSFontMaster(); m.name, m.axes = "Bold", [1000]
    font.masters.append(m)
    glyph = GSGlyph("leg")
    font.glyphs.append(glyph)
    # a leg piece overlapping a stem, as R draws it: the leg's top
    # corners sit inside the stem's ink
    stem = box(0, 400, 0, 1400)
    leg = box(100, 300, -600, 200)
    glyph.layers.append(layer_for(m.id, [stem, leg], width=600))
    stats = corner_rounding.round_font(font, PARAMS)
    assert stats["hidden"] == 2, "exactly the leg's two corners inside the stem"
    L = glyph.layers[m.id]
    # the buried corners collapse in place, so the junction stays sealed
    leg_pts = [(float(n.position.x), float(n.position.y)) for n in L.paths[1].nodes]
    assert (100.0, 200.0) in leg_pts and (300.0, 200.0) in leg_pts
    # the leg's visible tips and the stem's own corners still round
    assert (100.0, -600.0) not in leg_pts and (300.0, -600.0) not in leg_pts
    stem_pts = [(float(n.position.x), float(n.position.y)) for n in L.paths[0].nodes]
    assert (0.0, 0.0) not in stem_pts and (400.0, 1400.0) not in stem_pts


def path_of(*pts):
    path = GSPath()
    for x, y in pts:
        path.nodes.append(GSNode((x, y), "line"))
    path.closed = True
    return path


def xopq_font(*paths, width=600.0):
    """One master at XOPQ 1000 (no XTRA), one glyph from ``paths``."""
    font = GSFontCls()
    font.axes = []
    ax = GSAxis(); ax.name, ax.axisTag = "X-Opacity", "XOPQ"
    font.axes.append(ax)
    m = GSFontMaster(); m.name, m.axes = "Bold", [1000]
    font.masters.append(m)
    glyph = GSGlyph("test")
    font.glyphs.append(glyph)
    glyph.layers.append(layer_for(m.id, list(paths), width=width))
    return font, glyph.layers[m.id]


def nodes_of(layer, path_index=0):
    return [(float(n.position.x), float(n.position.y))
            for n in layer.paths[path_index].nodes]


def test_a_thin_wall_corner_rounds_concentrically():
    """An ink-concave corner one wall away from an outer corner tracks
    that corner's radius minus the wall, instead of the counter share —
    otherwise the outer arc cuts through the hairline and everts."""
    # an L of wall 10: outer corner (0,0), its concave partner (10,10)
    font, L = xopq_font(path_of((0, 0), (400, 0), (400, 10), (10, 10), (10, 400), (0, 400)))
    corner_rounding.round_font(font, PARAMS)
    pts = nodes_of(L)
    # outer: r = 15% of 1000 = 150, tangents at 150 along each edge
    assert (150.0, 0.0) in pts and (0.0, 150.0) in pts
    # concave partner: concentric r = 150 - wall 10 = 140, not the 5% share
    assert (150.0, 10.0) in pts and (10.0, 150.0) in pts
    assert (60.0, 10.0) not in pts, "the 50-unit counter share would evert the wall"


def test_concentric_pairs_through_ink_only():
    """The wall partner is found through the ink, not by raw distance: a
    nearer convex corner across white space is not a wall."""
    ell = path_of((0, 0), (400, 0), (400, 30), (30, 30), (30, 400), (0, 400))
    square = box(45, 445, 45, 445)   # in the L's notch, corner (45,45) nearest
    font, L = xopq_font(ell, square)
    corner_rounding.round_font(font, PARAMS)
    pts = nodes_of(L)
    # paired with (0,0) through the 30-unit wall: r = 150 - 30 = 120
    assert (150.0, 30.0) in pts and (30.0, 150.0) in pts
    assert (165.0, 30.0) not in pts, "pairing with the square (across white) would give 135"


def test_concentric_reverts_where_the_wall_is_thick():
    """A concave corner farther than the outer radius keeps the counter
    share: bold masters keep their small inner rounds."""
    font, L = xopq_font(path_of((0, 0), (400, 0), (400, 200), (200, 200), (200, 400), (0, 400)))
    corner_rounding.round_font(font, PARAMS)
    pts = nodes_of(L)
    assert (250.0, 200.0) in pts and (200.0, 250.0) in pts, "5% share of 1000 = 50"


OVERRIDE_PARAMS = dict(PARAMS, master_overrides={"Bold": {"outer": 333.0, "inner": 11.0}})


def test_an_override_hits_its_master_exactly_and_only_it():
    """A master override lands exactly at that master; every other
    master is pinned to the formula, so nothing else moves."""
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, OVERRIDE_PARAMS)
    assert fitted_radius(glyph.layers[bold.id]) == pytest.approx(333.0, abs=1)
    assert fitted_radius(glyph.layers[bold.id], 1) == pytest.approx(11.0, abs=1)
    assert fitted_radius(glyph.layers[thin.id]) == pytest.approx(4.5, abs=1)
    assert fitted_radius(glyph.layers[wide.id]) == pytest.approx(61.5, abs=1)
    assert fitted_radius(glyph.layers[wide.id], 1) == pytest.approx(20.5, abs=1)


def test_a_brace_blends_the_override_toward_its_location():
    """A brace between the formula master and the overridden one gets an
    in-between radius: delta 180 scaled by its position on the axis."""
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, OVERRIDE_PARAMS)
    # brace at XOPQ 500 between thin (10) and bold (1000): 490/990 of the way
    assert fitted_radius(brace) == pytest.approx(78 + 180 * 490 / 990.0, abs=2)
    assert fitted_radius(brace, 1) == pytest.approx(26 - 40 * 490 / 990.0, abs=2)


def test_no_overrides_changes_nothing():
    """An empty override table is byte-identical to no table at all —
    the regression pin for every build made before overrides existed."""
    font_a, glyph_a, *_ = mini_font()
    font_b, glyph_b, *_ = mini_font()
    corner_rounding.round_font(font_a, PARAMS)
    corner_rounding.round_font(font_b, dict(PARAMS, master_overrides={}))
    for La, Lb in zip(glyph_a.layers, glyph_b.layers):
        for pa, pb in zip(La.paths, Lb.paths):
            assert ([(float(n.position.x), float(n.position.y)) for n in pa.nodes]
                    == [(float(n.position.x), float(n.position.y)) for n in pb.nodes])


def test_an_override_naming_no_master_fails_the_build_loudly():
    font, *_ = mini_font()
    with pytest.raises(ValueError, match="Bol"):
        corner_rounding.round_font(font, dict(PARAMS, master_overrides={"Bol": {"outer": 10}}))


def test_an_override_of_zero_collapses_to_sharp_never_inverts():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, dict(PARAMS, master_overrides={"Bold": {"outer": 0}}))
    pts = [(float(n.position.x), float(n.position.y)) for n in glyph.layers[bold.id].paths[0].nodes]
    assert (0.0, 0.0) in pts, "the corner collapses in place"
    assert fitted_radius(glyph.layers[bold.id], 1) == pytest.approx(51.0, abs=1), "counters keep the formula"


def italic_twin_font():
    font = GSFontCls()
    font.axes = []
    for name, tag in (("X-Transparency", "XTRA"), ("X-Opacity", "XOPQ"), ("Italic", "ital")):
        ax = GSAxis(); ax.name, ax.axisTag = name, tag
        font.axes.append(ax)
    up, it = GSFontMaster(), GSFontMaster()
    up.name, up.axes = "Bold", [100, 1000, 0]
    it.name, it.axes = "Bold Italic", [100, 1000, 1]
    font.masters.append(up)
    font.masters.append(it)
    glyph = GSGlyph("O")
    font.glyphs.append(glyph)
    for m in (up, it):
        glyph.layers.append(layer_for(m.id, [box(0, 1400, 0, 1400)]))
    return font, glyph, up, it


def test_an_italic_twin_shares_its_upright_override():
    """Radii live on the parametric plane: a slanted master at the same
    XOPQ/XTRA point rounds like its upright, so slant cannot fork radii."""
    font, glyph, up, it = italic_twin_font()
    corner_rounding.round_font(font, dict(PARAMS, master_overrides={"Bold": {"outer": 120}}))
    assert fitted_radius(glyph.layers[up.id]) == pytest.approx(120.0, abs=1)
    assert fitted_radius(glyph.layers[it.id]) == pytest.approx(120.0, abs=1)


def test_twins_with_conflicting_overrides_are_an_error():
    font, glyph, up, it = italic_twin_font()
    with pytest.raises(ValueError, match="parametric"):
        corner_rounding.round_font(font, dict(PARAMS, master_overrides={
            "Bold": {"outer": 120}, "Bold Italic": {"outer": 200}}))


def test_the_override_table_is_validated_at_the_api():
    from avar2_studio.transforms.builtin_round_corners import RoundCornersTransform
    t = RoundCornersTransform()
    base = t.spec.coerce_params({})
    for bad in ([1, 2],
                {"Bold": [1]},
                {"Bold": {"outre": 3}},
                {"Bold": {"outer": -5}},
                {"Bold": {"outer": "abc"}}):
        with pytest.raises(ValueError):
            t.validate(dict(base, master_overrides=bad))
    t.validate(dict(base, master_overrides={"Bold": {"outer": 120, "inner": 8}}))
    # the schema round-trips the table instead of dropping or mangling it
    kept = t.spec.coerce_params({"master_overrides": {"Bold": {"outer": 3}}})
    assert kept["master_overrides"] == {"Bold": {"outer": 3}}


def test_axis_mode_adds_the_axis_and_twin_masters():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, PARAMS, axis_max=100.0)
    assert [str(a.axisTag) for a in font.axes] == ["XTRA", "XOPQ", "ROND"]
    assert len(font.masters) == 6
    assert all(list(m.axes)[-1] == 0 for m in font.masters[:3])
    assert all(list(m.axes)[-1] == 100 for m in font.masters[3:])
    assert str(font.masters[3].name) == "Thin Rounded"
    assert font.masters[3].id != thin.id


def test_axis_mode_is_sharp_at_zero_and_todays_rounding_at_max():
    baked_font, baked_glyph, _t, baked_bold, *_ = mini_font()
    corner_rounding.round_font(baked_font, PARAMS)
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, PARAMS, axis_max=100.0)

    def shape(layer):
        return [[(float(n.position.x), float(n.position.y), str(n.type)) for n in p.nodes]
                for p in layer.paths]

    bold_twin = font.masters[4]
    assert str(bold_twin.name) == "Bold Rounded"
    # the twin carries exactly what bake mode produces
    assert shape(glyph.layers[bold_twin.id]) == shape(baked_glyph.layers[baked_bold.id])
    # the original keeps the same node structure but sharp: every quad
    # collapses onto its corner, so the first round measures zero
    assert structure(glyph.layers[bold.id]) == structure(baked_glyph.layers[baked_bold.id])
    assert fitted_radius(glyph.layers[bold.id]) == 0.0
    assert float(glyph.layers[bold.id].width) == float(glyph.layers[bold_twin.id].width)
    # a backup stays a plain pair of boxes
    assert structure(backup) == [("line",) * 4, ("line",) * 4]


def test_axis_mode_twins_braces_and_extends_coordinates():
    baked_font, baked_glyph, *_ = mini_font()
    corner_rounding.round_font(baked_font, PARAMS)
    baked_brace = [L for L in baked_glyph.layers if str(L.layerId) == "BRACE-1"][0]
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, PARAMS, axis_max=100.0)
    assert list(brace.attributes["coordinates"]) == [100, 500, 0.0]
    twins = [L for L in glyph.layers
             if isinstance(L.attributes.get("coordinates"), (list, tuple))
             and list(L.attributes["coordinates"])[-1] == 100.0]
    assert len(twins) == 1
    def shape(layer):
        return [[(float(n.position.x), float(n.position.y), str(n.type)) for n in p.nodes]
                for p in layer.paths]
    assert shape(twins[0]) == shape(baked_brace)
    assert fitted_radius(brace) == 0.0


def test_axis_max_is_the_users_number():
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    corner_rounding.round_font(font, PARAMS, axis_max=40.0)
    assert all(list(m.axes)[-1] == 40 for m in font.masters[3:])
    assert list(brace.attributes["coordinates"]) == [100, 500, 0.0]


def test_axis_mode_defaults_instances_to_sharp():
    font, glyph, *_ = mini_font()
    inst = GSInstance()
    inst.name = "Regular"
    inst.axes = [100, 500]
    font.instances.append(inst)
    corner_rounding.round_font(font, PARAMS, axis_max=100.0)
    assert list(inst.axes) == [100, 500, 0.0]


def test_axis_mode_twins_every_glyph_even_cornerless_ones():
    """Twin masters need a layer for every glyph; a glyph with no corners
    gets identical copies, which cost nothing in the compiled font."""
    font, glyph, thin, bold, wide, brace, backup = mini_font()
    curvy = GSGlyph("curvy")
    font.glyphs.append(curvy)
    for m in (thin, bold, wide):
        path = GSPath()
        for x, y in ((0, 0), (300, 0), (300, 300), (0, 300)):
            path.nodes.append(GSNode((x, y), "curve"))
        path.closed = True
        curvy.layers.append(layer_for(m.id, [path]))
    corner_rounding.round_font(font, PARAMS, axis_max=100.0)
    for tm in font.masters[3:]:
        L = curvy.layers[tm.id]
        assert L is not None
        assert [(float(n.position.x), float(n.position.y)) for n in L.paths[0].nodes] ==                [(0.0, 0.0), (300.0, 0.0), (300.0, 300.0), (0.0, 300.0)]
