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
    GSAxis, GSFont as GSFontCls, GSFontMaster, GSGlyph, GSLayer, GSNode, GSPath,
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
    thin, bold = GSFontMaster(), GSFontMaster()
    thin.name, thin.axes = "Thin", [100, 10]
    bold.name, bold.axes = "Bold", [100, 1000]
    font.masters.append(thin)
    font.masters.append(bold)
    glyph = GSGlyph("O")
    font.glyphs.append(glyph)
    def drawing(stroke):
        return [box(0, 400 + stroke, 0, 400 + stroke),
                box(200, 200 + stroke * 0.2 + 40, 200, 200 + stroke * 0.2 + 40)]
    glyph.layers.append(layer_for(thin.id, drawing(10)))
    glyph.layers.append(layer_for(bold.id, drawing(1000)))
    brace = layer_for(bold.id, drawing(500))
    brace.layerId = "BRACE-1"
    brace.name = "{100, 500}"
    brace.attributes["coordinates"] = [100, 500]
    glyph.layers.append(brace)
    backup = layer_for(bold.id, drawing(1000))
    backup.layerId = "BACKUP-1"
    backup.name = "Sep 29, 26 backup"
    glyph.layers.append(backup)
    return font, glyph, thin, bold, brace, backup


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


PARAMS = {"outer_pct": 15.0, "inner_pct": 5.0, "outer_min": 2.0, "inner_min": 1.0}


def test_every_interpolating_layer_gains_the_same_corners():
    font, glyph, thin, bold, brace, backup = mini_font()
    stats = corner_rounding.round_font(font, PARAMS)
    layers = [glyph.layers[thin.id], glyph.layers[bold.id], brace]
    assert len({str(structure(L)) for L in layers}) == 1
    assert structure(layers[0])[0] == ("line", "offcurve", "offcurve", "curve") * 4
    assert structure(backup) == [("line",) * 4, ("line",) * 4], "a backup is not touched"
    assert stats["corners"] == 8 and stats["layers"] == 3


def test_the_radius_follows_each_layers_stroke():
    font, glyph, thin, bold, brace, backup = mini_font()
    corner_rounding.round_font(font, PARAMS)
    assert fitted_radius(glyph.layers[bold.id]) == pytest.approx(150.0, abs=1)   # 15% of 1000
    assert fitted_radius(brace) == pytest.approx(75.0, abs=1)                    # 15% of 500
    assert fitted_radius(glyph.layers[thin.id]) == pytest.approx(2.0, abs=1)     # the floor
    # counters take the inner share: 5% of 1000 = 50
    assert fitted_radius(glyph.layers[bold.id], 1) == pytest.approx(50.0, abs=1)


def test_a_correction_layers_radius_follows_its_target():
    font, glyph, thin, bold, brace, backup = mini_font()
    targets = corner_rounding.control_targets({"axes": [{"tag": "lcwd", "layers": [
        {"glyph": "O", "location": {"XTRA": 100, "XOPQ": 500}, "target": {"XOPQ": 200.0}},
    ]}]})
    corner_rounding.round_font(font, PARAMS, targets=targets)
    assert fitted_radius(brace) == pytest.approx(30.0, abs=1)  # 15% of the TARGET's 200


def test_advances_and_extremes_survive():
    font, glyph, thin, bold, brace, backup = mini_font()
    before = {L.layerId: (float(L.width),
                          max(float(n.position.x) for p in L.paths for n in p.nodes))
              for L in glyph.layers}
    corner_rounding.round_font(font, PARAMS)
    for L in glyph.layers:
        assert float(L.width) == before[L.layerId][0]
        if L is not backup:
            assert max(float(n.position.x) for p in L.paths for n in p.nodes) == before[L.layerId][1]


def test_a_second_pass_finds_nothing_left_to_round():
    font, glyph, thin, bold, brace, backup = mini_font()
    corner_rounding.round_font(font, PARAMS)
    once = {L.layerId: [[(float(n.position.x), float(n.position.y)) for n in p.nodes]
                        for p in L.paths] for L in glyph.layers}
    stats = corner_rounding.round_font(font, PARAMS)
    assert stats["corners"] == 0
    assert {L.layerId: [[(float(n.position.x), float(n.position.y)) for n in p.nodes]
                        for p in L.paths] for L in glyph.layers} == once


def test_a_brace_with_its_own_structure_is_left_alone_and_named():
    font, glyph, thin, bold, brace, backup = mini_font()
    brace.paths[0].nodes.append(GSNode((5, 5), "line"))
    logs = []
    stats = corner_rounding.round_font(font, PARAMS, log=logs.append)
    assert stats["skipped_layers"] == ["O/{100, 500}"]
    assert any("do not match" in m for m in logs)
    assert structure(glyph.layers[bold.id])[0][1] == "offcurve", "the masters still round"


def test_without_an_xopq_axis_nothing_happens():
    font, glyph, thin, bold, brace, backup = mini_font()
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
