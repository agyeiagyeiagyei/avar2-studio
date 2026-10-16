"""_measure_strokes returns the measured polygon alongside the numbers.

The Fontra HUD's overlay preview draws exactly what the scanlines cut —
the decomposed outline with off-curves as vertices — so the numeric
readouts and the picture can never disagree. The numbers themselves
must be unchanged by the addition."""

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from avar2_studio.server import _measure_strokes


def _font_with_H(tmp_path):
    # An 'H': two 100-wide stems x=[100,200] and [500,600], one 80-tall
    # bar y=[460,540] spanning between them. Advance 700.
    pen = TTGlyphPen(None)
    for x0, x1 in ((100, 200), (500, 600)):
        pen.moveTo((x0, 0))
        pen.lineTo((x1, 0))
        pen.lineTo((x1, 1000))
        pen.lineTo((x0, 1000))
        pen.closePath()
    pen.moveTo((200, 460))
    pen.lineTo((500, 460))
    pen.lineTo((500, 540))
    pen.lineTo((200, 540))
    pen.closePath()
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "H"])
    fb.setupCharacterMap({0x48: "H"})
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "H": pen.glyph()})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "H": (700, 100)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "T", "styleName": "R"})
    fb.setupOS2()
    fb.setupPost()
    path = tmp_path / "t.ttf"
    fb.save(str(path))
    return path


def test_measure_strokes_numbers_and_contours(tmp_path):
    from fontTools.ttLib import TTFont
    font = TTFont(str(_font_with_H(tmp_path)))
    m = _measure_strokes(font.getGlyphSet(), font["hmtx"], "H")
    assert m["stem"] == 100.0    # thinnest vertical run at the 25% scanline
    assert m["bar"] == 80.0      # thinnest horizontal run at mid-advance
    assert m["advance"] == 700.0
    assert m["contrast"] == 1.25
    # The overlay's geometry: three closed contours, plain point pairs.
    assert len(m["contours"]) == 3
    for c in m["contours"]:
        assert all(len(pt) == 2 for pt in c)
    # The bar contour's points are present verbatim (rounded).
    flat = {tuple(pt) for c in m["contours"] for pt in c}
    assert (200.0, 460.0) in flat and (500.0, 540.0) in flat
