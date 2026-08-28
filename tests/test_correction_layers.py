"""Correction layers: a brace layer whose outline is COMPUTED as the glyph
interpolated at a *target* parametric point instead of the layer's own.

This is how a glyph-scoped correction axis reduces stem weight at a corner
the global axes drive to max: the layer sits at the corner (so it engages
there), its target says "as if XOPQ were lower", and ``regenerate_shadow``
interpolates the outline at that lower point.
"""
import glyphsLib

from avar2_studio import control_axes


def _brace_layers(shadow_path, glyph_name):
    font = glyphsLib.GSFont(str(shadow_path))
    master_ids = {m.id for m in font.masters}
    out = {}
    for layer in font.glyphs[glyph_name].layers:
        if layer.layerId in master_ids:
            continue
        coords = dict(layer.attributes or {}).get("coordinates")
        if coords:
            out[tuple(float(v) for v in coords)] = layer
    return font, out


def _nodes(layer):
    return [
        (round(float(n.position.x), 3), round(float(n.position.y), 3))
        for p in layer.paths for n in p.nodes
    ]


def test_normalise_layers_keeps_a_numeric_target():
    raw = [
        {"glyph": "e", "location": {"crbr": 100}, "target": {"XOPQ": "700", "junk": "x"}},
        {"glyph": "f", "location": {"crbr": 100}, "target": {}},
        {"glyph": "g", "location": {"crbr": 100}},
    ]
    out = control_axes._normalise_layers(raw)
    assert out[0]["target"] == {"XOPQ": 700.0}
    assert "target" not in out[1]  # empty target is dropped
    assert "target" not in out[2]


def test_layer_delta_round_trips_the_target(crispy_source):
    control_axes.add_axis(crispy_source, "crbr", "Crossbar", 0, -100, 100)
    stored = control_axes.apply_layer_delta(
        crispy_source, "crbr",
        add=[{"glyph": "e", "location": {"crbr": 100, "XOPQ": 1016}, "target": {"XOPQ": 500}}],
    )
    assert stored[0]["target"] == {"XOPQ": 500.0}
    assert control_axes.list_axes(crispy_source)[0]["layers"][0]["target"] == {"XOPQ": 500.0}


def test_target_layer_is_seeded_at_the_target_location(crispy_source):
    """A layer at (crbr -100, XOPQ 2) with target XOPQ 1016 must carry the
    same outline as a plain layer at (crbr 100, XOPQ 1016): both are the
    glyph interpolated at XOPQ 1016. And it must differ from the glyph at
    its own location (XOPQ 2 == the default master)."""
    control_axes.add_axis(crispy_source, "crbr", "Crossbar", 0, -100, 100)
    control_axes.apply_layer_delta(
        crispy_source, "crbr",
        add=[
            {"glyph": "e", "location": {"crbr": 100, "XOPQ": 1016}},
            {"glyph": "e", "location": {"crbr": -100, "XOPQ": 2}, "target": {"XOPQ": 1016}},
        ],
    )
    shadow = control_axes.regenerate_shadow(crispy_source)
    assert shadow is not None
    font, braces = _brace_layers(shadow, "e")
    tag_index = [a.axisTag for a in font.axes].index("crbr")
    plain = next(l for c, l in braces.items() if c[tag_index] == 100.0)
    corrected = next(l for c, l in braces.items() if c[tag_index] == -100.0)
    assert _nodes(corrected) == _nodes(plain)
    assert abs(float(corrected.width) - float(plain.width)) < 1e-6
    default_layer = next(l for l in font.glyphs["e"].layers if l.layerId == font.masters[0].id)
    assert _nodes(corrected) != _nodes(default_layer)
    # The Fontra source label says what the computed view is.
    assert "as if XOPQ1016" in (corrected.userData.get("xyz.fontra.source-name") or "")


def test_target_on_the_control_axis_itself_is_ignored(crispy_source):
    """A target can only override PARAMETRIC axes; a control-axis key is
    meaningless and must not turn the layer into a computed one."""
    control_axes.add_axis(crispy_source, "crbr", "Crossbar", 0, -100, 100)
    control_axes.apply_layer_delta(
        crispy_source, "crbr",
        add=[{"glyph": "e", "location": {"crbr": 100}, "target": {"crbr": 50}}],
    )
    shadow = control_axes.regenerate_shadow(crispy_source)
    font, braces = _brace_layers(shadow, "e")
    # CrispyMini has source brace layers of its own on 'e' — pick ours.
    tag_index = [a.axisTag for a in font.axes].index("crbr")
    layer = next(l for c, l in braces.items() if c[tag_index] == 100.0)
    default_layer = next(l for l in font.glyphs["e"].layers if l.layerId == font.masters[0].id)
    # At the default parametric location the seed IS the default master.
    assert _nodes(layer) == _nodes(default_layer)
    assert "as if" not in (layer.userData.get("xyz.fontra.source-name") or "")
