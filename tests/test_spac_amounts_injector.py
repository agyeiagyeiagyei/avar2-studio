"""75e6c55 end to end: the injector writes the export's integer amounts.

The earlier fixture test checked the shared amount function and the model. This
one runs the ACTUAL transform on a small variable font and reads the tuples back
out of gvar: at the wide corner the SPAC phantom deltas must reconstruct
`spac_amounts.per_side(...)` exactly — the number the export writes into the
twin.

What each half proves: the end-to-end test pins the injector to that number, but
a two-corner font cannot reproduce the 2–3 u drift the old float path caused (its
deltas happen to be integral here); the second test is the mechanism guard — it
asserts the deltas are integers for inputs whose float deltas are not.
"""
from __future__ import annotations

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._g_l_y_f import Glyph
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib.models import VariationModel

from avar2_studio import spac_amounts
from avar2_studio.transforms.base import BuildContext
from avar2_studio.transforms.builtin_spac_widthaware import WidthAwareSpacTransform

CORNERS = [{}, {"XTRA": 1.0}]          # the base and the wide master
INKS = {"H": (100, 600), "n": (100, 400)}   # glyph -> (left, ink width)


def _vf(path):
    fb = FontBuilder(1000, isTTF=True)
    names = [".notdef", *INKS]
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(n): n for n in INKS})
    glyf = {".notdef": Glyph()}
    glyf[".notdef"].numberOfContours = 0
    for name, (left, ink) in INKS.items():
        glyph = Glyph()
        glyph.coordinates = None
        glyph.numberOfContours = 1
        # A 100 x 600 box: the ink width the transform measures.
        glyph.coordinates = _box(left, ink)
        from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates

        glyph.coordinates = GlyphCoordinates(_box(left, ink))
        glyph.flags = bytearray([1] * 4)
        glyph.endPtsOfContours = [3]
        glyph.program = _empty_program()
        glyf[name] = glyph
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics({name: (1000, 0) for name in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Amounts", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar([("XTRA", 0, 0, 100, "X-Transparency")], [])
    font = fb.font
    gvar = newTable("gvar")
    gvar.version = 1
    gvar.reserved = 0
    gvar.variations = {name: [TupleVariation({"XTRA": (0, 1, 1)},
                                             [None] * (len(_box(*INKS[name]))
                                                       + 4))]
                       for name in INKS}
    font["gvar"] = gvar
    font.save(str(path))
    return path


def _box(left, ink):
    return [(left, 0), (left + ink, 0), (left + ink, 600), (left, 600)]


def _empty_program():
    from fontTools.ttLib.tables.ttProgram import Program

    program = Program()
    program.fromAssembly(["SVTCA[0]"])
    return program


def _spac_amounts_at(path, glyph, corner, end=1):
    """The SPAC phantom deltas this font carries at ``corner`` for one end.

    Each corner contributes a lo and a hi tuple, so the model is rebuilt from
    ONE end's tuples or its locations repeat.
    """
    font = TTFont(str(path))
    variations = [v for v in font["gvar"].variations.get(glyph, [])
                  if "SPAC" in v.axes and (v.axes["SPAC"][1] > 0) == (end > 0)]
    assert variations, f"no SPAC tuple on {glyph} for end {end:+d}"
    model = VariationModel([{tag: peak
                             for tag, (_s, peak, _e) in v.axes.items()
                             if tag != "SPAC"} for v in variations],
                           axisOrder=sorted({t for v in variations
                                             for t in v.axes if t != "SPAC"}))
    left = model.interpolateFromDeltas(
        corner, [v.coordinates[-4][0] for v in variations])
    advance = model.interpolateFromDeltas(
        corner, [v.coordinates[-3][0] for v in variations])
    font.close()
    return left, advance


def test_the_injector_writes_the_exports_integer_amounts(tmp_path):
    vf = _vf(tmp_path / "amounts.ttf")
    ctx = BuildContext(build_dir=tmp_path, source_path=tmp_path / "x.glyphs",
                       glyphs_path=tmp_path / "x.glyphs", family="Amounts",
                       master_locations=[{}, {"XTRA": 100.0}])
    out = WidthAwareSpacTransform().apply(
        vf, {"min": -100, "max": 100, "bias": 1.0, "scale": 1.25}, ctx)

    widths = {name: ink for name, (_l, ink) in INKS.items()}
    mean_ink = sum(widths.values()) / len(widths)
    # H's ink is 600 against the font's mean 500, so its amount is the one
    # per_side() gives for that corner — the number the export writes.
    expected = spac_amounts.per_side(100.0, widths["H"], mean_ink, mean_ink,
                                     1.0, 1.25, 1000.0, False)
    left, advance = _spac_amounts_at(out, "H", {"XTRA": 1.0})
    assert advance == pytest.approx(expected, abs=0), (
        f"the wide corner must read exactly {expected} (it read {advance})")
    assert left == pytest.approx(-expected, abs=0)


def test_the_amounts_are_integers_and_read_back_exactly():
    """The shared primitive, as the injector and the export both call it."""
    corners = [{}, {"XTRA": 1.0}, {"XTRA": 0.6}]
    model = VariationModel(corners, axisOrder=["XTRA"])
    amounts = [spac_amounts.per_side(100.0, ink, mean, 600.0, 1.0, 1.25,
                                     800.0, False)
               for ink, mean in ((600.0, 600.0), (1500.0, 900.0),
                                 (240.0, 300.0))]
    deltas = model.getDeltas(amounts, round=round)
    assert all(float(d).is_integer() for d in deltas), deltas
    for corner, amount in zip(corners, amounts):
        assert model.interpolateFromDeltas(corner, deltas) == amount
