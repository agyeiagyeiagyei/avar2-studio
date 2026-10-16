"""Untouched seed braces follow master edits; hand-drawn braces are kept.

regenerate_shadow decides seed-vs-edit by the seed signature stamped at
seeding time (``xyz.avar2studio.seed-sig``), not by comparing against the
default master: a seed interpolates at its corner, so it nearly always
differs from the default master, and the old rule froze every seed the
moment a master moved (the audit's probe, exec summary #2).
"""
import glyphsLib
from test_capture_outlines import _brace, _draw_into, _nodes

from avar2_studio import control_axes


def _shift_masters(source_path, glyph_name, dx=37.0):
    """A master edit: shift every master layer of the glyph."""
    font = glyphsLib.GSFont(str(source_path))
    master_ids = {m.id for m in font.masters}
    for layer in font.glyphs[glyph_name].layers:
        if layer.layerId not in master_ids:
            continue
        for p in layer.paths:
            for n in p.nodes:
                n.position = (n.position.x + dx, n.position.y)
    font.save(str(source_path))


def _axis_with_seeded_brace(crispy_source):
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "ymod",
        add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}])
    shadow = control_axes.regenerate_shadow(crispy_source)
    assert shadow is not None
    return shadow


def test_an_untouched_seed_follows_master_edits(crispy_source):
    shadow = _axis_with_seeded_brace(crispy_source)
    _, before = _brace(shadow, "e")
    assert before is not None
    before_nodes = _nodes(before)

    _shift_masters(crispy_source, "e", dx=37.0)
    shadow = control_axes.regenerate_shadow(crispy_source)
    _, after = _brace(shadow, "e")
    assert after is not None
    assert _nodes(after) == [(round(x + 37.0, 2), y) for x, y in before_nodes], \
        "an untouched seed must re-interpolate when the masters move"


def test_a_hand_drawn_brace_is_preserved_across_master_edits(crispy_source):
    shadow = _axis_with_seeded_brace(crispy_source)
    drawn = _draw_into(shadow, "e", dy=250.0)
    assert drawn is not None

    _shift_masters(crispy_source, "e", dx=37.0)
    shadow = control_axes.regenerate_shadow(crispy_source)
    _, after = _brace(shadow, "e")
    assert after is not None
    assert _nodes(after) == drawn, \
        "a hand-drawn brace (stamp mismatch) must be preserved verbatim"
