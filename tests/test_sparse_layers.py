"""The sparse-layer pass, and the integer amount model behind E2i-c.

Three fixes had no fixture test (verifier, 2026-11-06 12:24):

- 75e6c55 — the injector's per-corner amounts are INTEGERS, decomposed by the
  same `round()` the export writes, so a corner reads back exactly;
- ca90f44 — a rotated component glyph gets its base's sparse layer, under its
  OWN interpolated advance and transform (`nine` = 180°-rotated `six`);
- c9849fa — a mixed glyph (`cent` = `c` + a bar) gets the layer too, carrying
  its own interpolated contours in the masters' order.

Plus the pass's promise not to skip in silence: a glyph whose base has layers
but whose masters cannot be rebuilt is named in the log.
"""

from __future__ import annotations

import logging

import pytest
from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, SourceDescriptor
from fontTools.varLib.models import VariationModel

from avar2_studio import sources_export, spac_amounts

LAYER = "XTRA 100"


def _rect(font, name, width, height, advance=None, x=0):
    glyph = font.newGlyph(name)
    glyph.width = advance if advance is not None else width + 2 * x
    pen = glyph.getPen()
    pen.moveTo((x, 0))
    pen.lineTo((x + width, 0))
    pen.lineTo((x + width, height))
    pen.lineTo((x, height))
    pen.closePath()
    return glyph


def _two_axis_doc():
    doc = DesignSpaceDocument()
    axis = AxisDescriptor()
    axis.name, axis.tag = "XTRA", "XTRA"
    axis.minimum, axis.default, axis.maximum = 0, 0, 100
    doc.addAxis(axis)
    return doc


def _master(doc, style, xtra, build):
    import ufoLib2

    font = ufoLib2.Font()
    font.info.unitsPerEm = 1000
    build(font)
    source = SourceDescriptor()
    source.font = font
    source.styleName = style
    source.name = style
    source.location = {"XTRA": float(xtra)}
    doc.addSource(source)
    return source


def _sparse(doc, base_source, layer_name, xtra, build):
    """A sparse layer at ``xtra``: the base master's font plus a named layer."""
    layer = base_source.font.layers.newLayer(layer_name)
    glyphs = {}
    build(base_source.font, layer, glyphs)
    source = SourceDescriptor()
    source.font = base_source.font
    source.styleName = f"{base_source.styleName} {layer_name}"
    source.name = source.styleName
    source.location = {"XTRA": float(xtra)}
    source.layerName = layer_name
    doc.addSource(source)
    return source


# --------------------------------------------------------------------------
# 75e6c55 — integer amounts, exact at every corner
# --------------------------------------------------------------------------

def test_the_amounts_reconstruct_exactly_at_every_corner():
    """E2i-c: the model's deltas must read back the corner amount to the unit.

    The injector hands `VariationModel.getDeltas` the same INTEGERS the export
    writes for each corner, with `round=round`. Float amounts let each tuple
    delta round on its own, so a master's font gave Σ round(Δᵢ) while the export
    wrote round(amount) — measured 2–3 u at the SPAC ends (verifier,
    2026-11-06). The second half of this test is the reason the fix exists: the
    float path misses the corners.
    """
    corners = [{}, {"XTRA": 1.0}, {"XTRA": 0.6}, {"XOPQ": 1.0},
               {"XTRA": 1.0, "XOPQ": 1.0}]
    model = VariationModel(corners, axisOrder=["XOPQ", "XTRA"])
    amounts = [spac_amounts.per_side(100.0, ink, mean, 600.0, 1.0, 1.25,
                                     800.0, False)
               for ink, mean in ((600.0, 600.0), (1500.0, 900.0),
                                 (240.0, 300.0), (600.0, 420.0),
                                 (1900.0, 1000.0))]
    assert all(isinstance(amount, int) for amount in amounts)

    deltas = model.getDeltas(amounts, round=round)
    # Integral deltas ARE the fix: the old path handed `getDeltas` the raw
    # floats, so a corner read Σ round(Δᵢ) while the export wrote
    # round(amount) — 2–3 u apart at the SPAC ends on the real 36-corner grid
    # (verifier, 2026-11-06). A five-corner fixture cannot reproduce that gap,
    # but it does catch the fractional deltas that caused it.
    assert all(float(d).is_integer() for d in deltas), deltas
    for corner, amount in zip(corners, amounts):
        read = model.interpolateFromDeltas(corner, deltas)
        assert read == amount, (
            f"corner {corner} reads back {read}, not the {amount} the export "
            "writes")


# --------------------------------------------------------------------------
# ca90f44 — a rotated component carries its own advance and transform
# --------------------------------------------------------------------------

def test_a_rotated_component_takes_its_own_advance_not_its_bases():
    """`nine` is a 180°-rotated `six`; `six` carries the sparse layers.

    The synthesized layer must hold nine's OWN advance and offset at that
    location (interpolated from nine's masters), with six's layer outline under
    that transform. Copying the base's advance left nine 13 u out at the 900
    rows; the studio's own numbers are 2779 / 5173 / 1149 / 2196.
    """
    from ufoLib2.objects import Component

    doc = _two_axis_doc()
    transform = (-1, 0, 0, -1, 127, 1600)

    def base(font):
        _rect(font, "six", 300, 500, advance=500)
        nine = font.newGlyph("nine")
        nine.width = 700
        nine.components.append(Component("six", transform))

    thin = _master(doc, "Thin", 0, base)

    def wider(font, layer, _cache):
        # six's layer advance is 760, NINE's own is 700: a pass that copies the
        # base's advance lands on 760 and fails, which is the defect that left
        # nine 13 u out at the 900 rows.
        _rect(layer, "six", 420, 500, advance=760)

    _sparse(doc, thin, LAYER, 100, wider)
    # A second master so the models have something to interpolate.
    _master(doc, "Max", 100, base)

    sources_export._decompose_transformed_sources(doc)

    layer = thin.font.layers[LAYER]
    assert "nine" in layer, "the rotated component needs a layer of its own"
    nine = layer["nine"]
    base_advance = thin.font.layers[LAYER]["six"].width
    assert base_advance == 760, "the base's layer has its own advance"
    assert nine.width == pytest.approx(700, abs=1), (
        f"nine's own advance at XTRA 100 is 700, not its base's {base_advance} "
        f"({nine.width})")
    assert nine.contours and not nine.components, (
        "the layer holds six's layer outline, flattened")


# --------------------------------------------------------------------------
# c9849fa — a mixed glyph keeps its own contours, in the masters' order
# --------------------------------------------------------------------------

def test_a_mixed_glyph_gets_the_layer_with_its_own_contours():
    """`cent` is `c` plus a bar of its own, and `c` carries the layers.

    The layer is cent's OWN contours interpolated from cent's masters, its
    component under the transform at that location, and its own advance —
    otherwise the layer pins the glyph and cent missed c's Lowercase-width
    layers by up to 1,128 u.
    """
    from ufoLib2.objects import Component

    doc = _two_axis_doc()

    def base(font):
        _rect(font, "c", 300, 500, advance=500)
        cent = font.newGlyph("cent")
        cent.width = 640
        pen = cent.getPen()
        pen.moveTo((100, 600))
        pen.lineTo((200, 600))
        pen.lineTo((200, 700))
        pen.lineTo((100, 700))
        pen.closePath()
        cent.components.append(Component("c", (1, 0, 0, 1, 0, 0)))

    thin = _master(doc, "Thin", 0, base)

    def wider(font, layer, _cache):
        _rect(layer, "c", 420, 500, advance=700)

    _sparse(doc, thin, LAYER, 100, wider)
    _master(doc, "Max", 100, base)

    sources_export._decompose_transformed_sources(doc)

    layer = thin.font.layers[LAYER]
    assert "cent" in layer, "a mixed glyph needs its base's layer too"
    cent = layer["cent"]
    assert not cent.components, "the layer is flattened"
    assert len(cent.contours) == 2, (
        f"its own bar first, then c: {len(cent.contours)} contour(s)")
    own = cent.contours[0]
    assert [(p.x, p.y) for p in own.points][:2] == [(100, 600), (200, 600)], (
        "the glyph's OWN contours come first, in the masters' order")


# --------------------------------------------------------------------------
# no silent skips
# --------------------------------------------------------------------------

def test_a_glyph_the_models_cannot_rebuild_is_named_in_the_log(caplog):
    """A glyph with a layered base that gets no layer drifts exactly like cent.

    Its masters disagree on the contour count, so the model declines; the pass
    must say so where the designer reads it (next to the sparse-layer count)
    instead of skipping in silence.
    """
    from ufoLib2.objects import Component

    doc = _two_axis_doc()

    def thin(font):
        _rect(font, "c", 300, 500, advance=500)
        odd = font.newGlyph("codd")
        odd.width = 640
        odd.components.append(Component("c", (1, 0, 0, 1, 0, 0)))

    def maxed(font):
        _rect(font, "c", 340, 500, advance=560)
        odd = font.newGlyph("codd")
        odd.width = 700
        odd.components.append(Component("c", (1, 0, 0, 1, 0, 0)))
        pen = odd.getPen()                        # an extra contour here only
        pen.moveTo((10, 10))
        pen.lineTo((50, 10))
        pen.lineTo((50, 50))
        pen.closePath()

    thin_source = _master(doc, "Thin", 0, thin)

    def layer_c(font, layer, _cache):
        _rect(layer, "c", 420, 500, advance=700)

    _sparse(doc, thin_source, LAYER, 100, layer_c)
    _master(doc, "Max", 100, maxed)

    with caplog.at_level(logging.WARNING):
        sources_export._decompose_transformed_sources(doc)

    assert "codd" in caplog.text, (
        "the glyph that got no layer must be named in the build log")
    assert "got no layer of their own" in caplog.text
