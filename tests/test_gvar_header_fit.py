"""Relocating the default can push a twin-heavy glyph past gvar's u16
per-glyph header block; _fit_gvar_headers drops only sub-visible tuples,
only where needed, and explains itself when it cannot fit."""
import io
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.TupleVariation import TupleVariation

from avar2_studio import pipeline

FIXTURE = Path(__file__).resolve().parents[1] / "frontend" / "e2e" / "fixtures" / "WasmTest-VF.ttf"


def _font():
    font = TTFont(str(FIXTURE))
    assert font["gvar"].variations  # force decompile
    return font


def _stuff(font, glyph, n, delta):
    """Append n private-peak tuples moving every point by (delta, 0)."""
    axes = [a.axisTag for a in font["fvar"].axes]
    coords, _e, _f = font["glyf"][glyph].getCoordinates(font["glyf"])
    out = []
    for i in range(n):
        # distinct private peaks on EVERY axis with intermediates → the
        # largest possible per-tuple header (flags + peak + start + end)
        peak = round(0.05 + 0.9 * i / n, 4)
        support = {t: (0.0, peak, min(1.0, peak + 0.05)) for t in axes}
        out.append(TupleVariation(support, [(delta, 0)] * len(coords) + [None] * 4))
    font["gvar"].variations.setdefault(glyph, []).extend(out)
    return out


def _saves(font):
    buf = io.BytesIO()
    font.save(buf)
    return True


def test_overflowing_glyph_is_pruned_back_under_the_limit():
    font = _font()
    glyph = next(g for g in font.getGlyphOrder() if font["glyf"][g].numberOfContours > 0)
    big = _stuff(font, glyph, 5, 40)        # visible: must survive
    _stuff(font, glyph, 20000, 1)             # sub-visible splinters: prunable
    with pytest.raises(OverflowError):
        _saves(font)                         # the fixture now overflows
    removed = pipeline._fit_gvar_headers(font)
    assert glyph in removed and removed[glyph] > 0
    assert all(tv in font["gvar"].variations[glyph] for tv in big), "a visible tuple was pruned"
    assert _saves(font)
    assert pipeline._fit_gvar_headers(font) == {}, "a fitting font is left untouched"


def test_unfixable_glyph_raises_a_readable_error():
    font = _font()
    glyph = next(g for g in font.getGlyphOrder() if font["glyf"][g].numberOfContours > 0)
    _stuff(font, glyph, 20000, 40)            # all visible: nothing may be dropped
    with pytest.raises(RuntimeError) as exc:
        pipeline._fit_gvar_headers(font)
    assert "per-glyph gvar limit" in str(exc.value) and glyph in str(exc.value)
