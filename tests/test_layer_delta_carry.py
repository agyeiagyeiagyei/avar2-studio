"""A coordinate edit through the layers/delta endpoint keeps the drawing.

The desktop layers list is served WITHOUT outline/source_sig (they are
large path dumps and the endpoint is polled), so the edit modal re-submits
only ``{glyph, location, target}`` and its own carry-over guard reads
``undefined``. The carry-over that keeps a drawn outline across a replace
lives server-side in ``apply_layer_delta`` (T0.6).
"""
import glyphsLib
import pytest

import avar2_studio.server as server
from avar2_studio import control_axes, state

OUTLINE = {
    "width": 421.0,
    "paths": [{"closed": True,
               "nodes": [[0.0, 0.0, "line"], [100.0, 0.0, "line"],
                         [100.0, 100.0, "line"], [0.0, 100.0, "line"]]}],
    "components": [],
    "anchors": [],
}


@pytest.fixture
def client(crispy_source, monkeypatch):
    monkeypatch.setattr(state, "ORIGINAL_PATH", crispy_source)
    monkeypatch.setattr(state, "GLYPHS_PATH", crispy_source)
    # The endpoint's rebuild debounce is timing-dependent under tests; the
    # shadow assertion below regenerates explicitly instead.
    monkeypatch.setattr(server, "schedule_shadow_rebuild", lambda: None)
    server.app.config["TESTING"] = True
    return server.app.test_client(), crispy_source


def _stored(source):
    return control_axes.list_axes(source)[0]["layers"]


def _brace_at(shadow_path, glyph_name, value, tag="ymod"):
    font = glyphsLib.GSFont(str(shadow_path))
    master_ids = {m.id for m in font.masters}
    tags = [str(a.axisTag) for a in font.axes]
    idx = tags.index(tag)
    for layer in font.glyphs[glyph_name].layers:
        if layer.layerId in master_ids:
            continue
        coords = dict(layer.attributes or {}).get("coordinates")
        if coords and float(coords[idx]) == float(value):
            return layer
    return None


def test_a_coordinate_edit_carries_the_drawing(client):
    c, source = client
    control_axes.add_axis(source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.set_layers(source, "ymod", [
        {"glyph": "e", "location": {"ymod": 100, "XOPQ": 500},
         "outline": OUTLINE, "source_sig": "sig-at-capture"},
    ])

    # The desktop edit modal: re-submits only {glyph, location} — the list
    # endpoint strips outline/source_sig, so the client cannot carry them.
    resp = c.post("/api/control-axes/ymod/layers/delta", json={
        "remove": [{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}],
        "add": [{"glyph": "e", "location": {"ymod": 50, "XOPQ": 500}}],
    })
    assert resp.status_code == 200, resp.get_json()

    stored = _stored(source)
    assert len(stored) == 1
    entry = stored[0]
    assert entry["location"] == {"ymod": 50.0, "XOPQ": 500.0}
    assert entry["outline"]["paths"] == OUTLINE["paths"], \
        "the drawing was dropped from the sidecar by a coordinate edit"
    assert entry["source_sig"] == "sig-at-capture"

    # ...and the shadow regenerates with the drawing at the NEW location.
    shadow = control_axes.regenerate_shadow(source)
    layer = _brace_at(shadow, "e", 50)
    assert layer is not None, "no brace layer at the edited location"
    nodes = [(float(n.position.x), float(n.position.y), str(n.type))
             for p in layer.paths for n in p.nodes]
    assert nodes == [(0.0, 0.0, "line"), (100.0, 0.0, "line"),
                     (100.0, 100.0, "line"), (0.0, 100.0, "line")]


def test_a_plain_delta_carries_nothing(crispy_source):
    """No stored drawing → nothing to carry, and no crash."""
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "ymod",
        add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}])
    stored = control_axes.apply_layer_delta(
        crispy_source, "ymod",
        remove=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}],
        add=[{"glyph": "e", "location": {"ymod": 50, "XOPQ": 500}}])
    assert len(stored) == 1
    assert "outline" not in stored[0]


def test_carry_never_lands_on_a_computed_entry(crispy_source):
    """A correction layer (target set) is recomputed every regen; attaching
    a drawing to it would be silently discarded AND mis-filed."""
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "ymod",
        add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500},
              "outline": OUTLINE}])
    stored = control_axes.apply_layer_delta(
        crispy_source, "ymod",
        remove=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}],
        add=[{"glyph": "e", "location": {"ymod": 50, "XOPQ": 500},
              "target": {"XOPQ": 500}}])
    assert len(stored) == 1
    assert "outline" not in stored[0]
