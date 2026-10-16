"""/api/axes serves the BUILT font's axis defaults for source axes.

Regression: the build relocates the parametric defaults to the ORIGIN
row's location, but /api/axes read defaults from the SOURCE — so the
Preview tab's sliders (and anything pinning axisDefaults) kept resting
at the raw compile origin (the hairline), hiding the relocation."""


import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib.tables._g_l_y_f import Glyph

from avar2_studio import server


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def _built_font(path):
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef"])
    fb.setupCharacterMap({})
    fb.setupGlyf({".notdef": Glyph()})
    fb.setupHorizontalMetrics({".notdef": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "T", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar([("XTRA", 47, 663.1, 1715, "X-Transparency")], [])
    fb.save(str(path))


def test_axes_default_comes_from_the_built_font(client, monkeypatch, tmp_path):
    built = tmp_path / "built.ttf"
    _built_font(built)
    monkeypatch.setattr(server, "GLYPHS_PATH", tmp_path / "F.glyphs")
    monkeypatch.setattr(server, "ORIGINAL_PATH", None)  # skip sidecar overlays
    monkeypatch.setattr(server, "VARIABLE_FONT_PATH", built)
    monkeypatch.setattr(server, "get_axes_from_glyphs", lambda _p: [
        {"tag": "XTRA", "name": "X-Transparency", "min": 47.0,
         "default": 47.0, "max": 1715.0, "has_master_coverage": True},
    ])
    monkeypatch.setattr(server, "_get_avar2_csv_path", lambda: None)

    r = client.get("/api/axes")
    assert r.status_code == 200
    (axis,) = [a for a in r.get_json()["axes"] if a["tag"] == "XTRA"]
    assert axis["default"] == pytest.approx(663.1), (
        "the relocated default must reach the UI")
    assert axis["min"] == 47.0 and axis["max"] == 1715.0, "ranges stay source-derived"
    assert axis["has_master_coverage"] is True
