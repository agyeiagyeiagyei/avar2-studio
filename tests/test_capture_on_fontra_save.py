"""T0.8: a Fontra save (which writes the shadow) is captured into the sidecar.

The watcher's Fontra-save branches call ``server._capture_shadow_drawings``
— the unit under test here; the branch wiring itself is nested in
``main()`` and verified by reading (watcher extraction is T3.10).
"""
import pytest
from test_capture_outlines import _draw_into

from avar2_studio import control_axes, state, watch


@pytest.fixture
def drawn_shadow(crispy_source, monkeypatch):
    """A control axis with a brace on 'e', a regenerated shadow, and a
    +250 hand edit left in it — the state a Fontra save leaves behind."""
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "ymod",
        add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}])
    shadow = control_axes.regenerate_shadow(crispy_source)
    drawn = _draw_into(shadow, "e")
    assert drawn is not None
    monkeypatch.setattr(state, "ORIGINAL_PATH", crispy_source)
    monkeypatch.setattr(state, "GLYPHS_PATH", shadow)
    return crispy_source


def test_a_fontra_save_is_captured_into_the_sidecar(drawn_shadow):
    source = drawn_shadow
    stored = control_axes.list_axes(source)[0]["layers"][0]
    assert "outline" not in stored, "drawing reached the sidecar before capture"

    captured = watch._capture_shadow_drawings()
    assert captured == 1
    stored = control_axes.list_axes(source)[0]["layers"][0]
    assert stored["outline"]["paths"], "the drawing is not in the sidecar"
    assert stored.get("source_sig"), "capture did not record the source signature"


def test_no_capture_when_editing_the_original(crispy_source, monkeypatch):
    """No shadow in play (Fontra on the user's own file): the guard keeps
    capture a no-op even if a stale shadow file exists."""
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "ymod",
        add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}])
    shadow = control_axes.regenerate_shadow(crispy_source)
    _draw_into(shadow, "e")
    monkeypatch.setattr(state, "ORIGINAL_PATH", crispy_source)
    monkeypatch.setattr(state, "GLYPHS_PATH", crispy_source)  # editing_original
    assert watch._capture_shadow_drawings() == 0
    assert "outline" not in control_axes.list_axes(crispy_source)[0]["layers"][0]


def test_no_source_loaded_is_a_noop(monkeypatch):
    monkeypatch.setattr(state, "ORIGINAL_PATH", None)
    monkeypatch.setattr(state, "GLYPHS_PATH", None)
    assert watch._capture_shadow_drawings() == 0
