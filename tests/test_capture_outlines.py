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
