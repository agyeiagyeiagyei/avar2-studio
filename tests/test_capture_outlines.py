"""Model α: capturing hand-drawn brace outlines into the sidecar.

Without this the sidecar holds only layer LOCATIONS and every drawing lives
solely in ``.avar2-studio/shadow/``, which is derived — so deleting that
directory, or moving the project to another machine via a config bundle, loses
the work. ``capture_outlines`` copies the drawings into the sidecar, and
``regenerate_shadow`` restores them ahead of any seeding.

The test that matters is the round trip: draw, capture, WIPE the shadow, and
regenerate from the sidecar alone.
"""
import glyphsLib
import pytest

from avar2_studio import control_axes


def _brace(shadow_path, glyph_name, tag="ymod"):
    """OUR brace layer on ``glyph_name``. CrispyMini ships brace layers of its
    own, so select by a non-default value on the axis we declared rather than
    taking the first one found."""
    font = glyphsLib.GSFont(str(shadow_path))
    master_ids = {m.id for m in font.masters}
    tags = [str(a.axisTag) for a in font.axes]
    idx = tags.index(tag) if tag in tags else None
    for layer in font.glyphs[glyph_name].layers:
        if layer.layerId in master_ids:
            continue
        coords = dict(layer.attributes or {}).get("coordinates")
        if not coords:
            continue
        if idx is not None and idx < len(coords) and float(coords[idx]) != 0.0:
            return font, layer
    return font, None


def _nodes(layer):
    return [
        (round(float(n.position.x), 2), round(float(n.position.y), 2))
        for p in layer.paths for n in p.nodes
    ]


def _draw_into(shadow_path, glyph_name, dy=250.0, tag="ymod"):
    """Simulate a Fontra edit: shift every node of OUR brace layer up."""
    font = glyphsLib.GSFont(str(shadow_path))
    master_ids = {m.id for m in font.masters}
    tags = [str(a.axisTag) for a in font.axes]
    idx = tags.index(tag) if tag in tags else None
    edited = None
    for layer in font.glyphs[glyph_name].layers:
        if layer.layerId in master_ids:
            continue
        coords = dict(layer.attributes or {}).get("coordinates")
        if not coords:
            continue
        if idx is None or idx >= len(coords) or float(coords[idx]) == 0.0:
            continue
        for p in layer.paths:
            for n in p.nodes:
                n.position = (n.position.x, n.position.y + dy)
        edited = _nodes(layer)
    font.save(str(shadow_path))
    return edited


@pytest.fixture
def axis_with_layer(crispy_source):
    """A plain (drawable) brace layer on 'e', with a shadow generated."""
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "ymod",
        add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}],
    )
    shadow = control_axes.regenerate_shadow(crispy_source)
    assert shadow is not None
    return crispy_source, shadow


def test_nothing_captured_before_anything_is_drawn(axis_with_layer):
    """An untouched seed is not a drawing — capturing it would fill the
    sidecar with no-ops that defeat re-seeding."""
    source, _ = axis_with_layer
    assert control_axes.capture_outlines(source) == 0
    stored = control_axes.list_axes(source)[0]["layers"][0]
    assert "outline" not in stored


def test_drawing_is_captured_into_the_sidecar(axis_with_layer):
    source, shadow = axis_with_layer
    drawn = _draw_into(shadow, "e")
    assert control_axes.capture_outlines(source) == 1

    stored = control_axes.list_axes(source)[0]["layers"][0]
    assert "outline" in stored
    assert stored["outline"]["paths"], "captured outline has no paths"
    flat = [
        (round(n[0], 2), round(n[1], 2))
        for p in stored["outline"]["paths"] for n in p["nodes"]
    ]
    assert flat == drawn


def test_drawing_survives_the_shadow_being_wiped(axis_with_layer):
    """THE point of model α: the sidecar alone can rebuild the drawing."""
    source, shadow = axis_with_layer
    drawn = _draw_into(shadow, "e")
    control_axes.capture_outlines(source)

    # Blow away the derived workspace, exactly as a stale-cache clean would.
    import shutil
    shutil.rmtree(shadow.parent.parent)
    assert not shadow.exists()

    rebuilt = control_axes.regenerate_shadow(source)
    assert rebuilt is not None
    _, layer = _brace(rebuilt, "e")
    assert layer is not None, "brace layer missing after regeneration"
    assert _nodes(layer) == drawn, "the drawing was not restored from the sidecar"


def test_correction_layers_are_not_captured(crispy_source):
    """A layer with a target is computed on every regen, so capturing a
    drawing for it would be misleading — it would be silently discarded."""
    control_axes.add_axis(crispy_source, "lcwd", "Lowercase width", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "lcwd",
        add=[{"glyph": "e", "location": {"lcwd": 100, "XOPQ": 1016},
              "target": {"XOPQ": 500}}],
    )
    shadow = control_axes.regenerate_shadow(crispy_source)
    _draw_into(shadow, "e", tag="lcwd")
    assert control_axes.capture_outlines(crispy_source) == 0
    assert "outline" not in control_axes.list_axes(crispy_source)[0]["layers"][0]


def test_restored_anchors_write_unquoted(crispy_source):
    """Regression: a stored outline's anchors must restore as glyphsLib
    Points. Plain tuple/float positions serialize as quoted strings
    ('pos = "(70.0, 0.0)"'), which glyphs-reader refuses to parse — the
    fontc shadow build then fails on any stored outline with anchors."""
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    outline = {
        "width": 421.0,
        "paths": [{"closed": True,
                   "nodes": [[0.0, 0.0, "line"], [100.0, 0.0, "line"],
                             [100.0, 100.0, "line"], [0.0, 100.0, "line"]]}],
        "components": [],
        "anchors": [{"name": "bottom", "x": 70.0, "y": 0.0},
                    {"name": "top", "x": 70.0, "y": 1200.0}],
    }
    control_axes.set_layers(crispy_source, "ymod", [
        {"glyph": "e", "location": {"ymod": 100, "XOPQ": 500},
         "outline": outline},
    ])
    shadow = control_axes.regenerate_shadow(crispy_source)
    assert shadow is not None
    assert 'pos = "' not in shadow.read_text()
    _, layer = _brace(shadow, "e")
    assert layer is not None
    assert [(a.name, a.position.x, a.position.y) for a in layer.anchors] == [
        ("bottom", 70, 0), ("top", 70, 1200)]


def test_component_outlines_round_trip_with_transform(crispy_source):
    """A brace layer holding a COMPONENT must keep its transform through
    restore and capture. glyphsLib ≥6.16 made GSComponent.transform a plain
    tuple; reading .value (pre-T0.10 code) silently dropped it there."""
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    transform = [1.0, 0.0, 0.0, 1.0, 30.0, 40.0]
    outline = {
        "width": 500.0,
        "paths": [],
        "components": [{"name": "e", "transform": transform}],
        "anchors": [],
    }
    control_axes.set_layers(crispy_source, "ymod", [
        {"glyph": "A", "location": {"ymod": 100, "XOPQ": 500},
         "outline": outline},
    ])
    shadow = control_axes.regenerate_shadow(crispy_source)
    assert shadow is not None

    # The shadow carries a real component with the transform intact.
    _, layer = _brace(shadow, "A")
    assert layer is not None
    comp = layer.components[0]
    assert comp.name == "e"
    assert [float(v) for v in getattr(comp.transform, "value", comp.transform)] == transform

    # Capture reads it back faithfully — a faithful round trip rewrites
    # nothing (n == 0); the pre-fix tuple-transform read dropped the
    # transform, so capture would REWRITE the stored outline without it.
    assert control_axes.capture_outlines(crispy_source) == 0
    stored = control_axes.list_axes(crispy_source)[0]["layers"][0]
    assert stored["outline"]["components"][0]["transform"] == transform


def test_capture_matches_layers_written_before_an_axis_was_added(crispy_source):
    """A brace created before GRAD (or a new control axis) was appended
    carries a shorter coordinates vector than the shadow's axis count. The
    raw-tuple keys then never match the full-length lookups and capture
    silently writes 0 layers — on Crispy, every control layer was in this
    state. Extraction pads short vectors with the default master's values."""
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "ymod",
        add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}])
    shadow = control_axes.regenerate_shadow(crispy_source)
    drawn = _draw_into(shadow, "e")
    assert drawn is not None

    # Grade composition appends GRAD to the shadow without extending the
    # existing brace layers' coordinates — the production drift.
    (crispy_source.parent / "CrispyMini-grade.json").write_text(
        '{"version": 1, "enabled": true, "default_pct": 0.25, "intensity": 1.0,'
        ' "clamp_to_headroom": true, "instances": [{"name": "Mid", "pct": 0.25}]}')
    from avar2_studio import grade_shadow
    assert grade_shadow.apply_grades(
        crispy_source, {"Mid": {"XTRA": 1712.0, "XOPQ": 509.0, "YOPQ": 232.0}},
        fresh_shadow=False) is not None

    assert control_axes.capture_outlines(crispy_source) == 1
    stored = control_axes.list_axes(crispy_source)[0]["layers"][0]
    flat = [(round(n[0], 2), round(n[1], 2))
            for p in stored["outline"]["paths"] for n in p["nodes"]]
    assert flat == drawn
