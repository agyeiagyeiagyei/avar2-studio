"""75e6c55 end to end: the injector writes the export's integer amounts.

The earlier fixture test checked the shared amount function and the model. This
one runs the ACTUAL transform on a small variable font and reads the result back
the way a renderer does (HarfBuzz, at each master corner and SPAC end): every
corner must move each side by exactly `spac_amounts.per_side(...)` — the number
the export writes into the twin.

The wide master's INK really changes (its right-hand points move), and the inks
are picked so the float path cannot land on the export's integer: H's amount is
140.4 at the base and 180.8 at the wide corner, so the old injector (float
amounts → `getDeltas` without `round=` → `round()` per tuple) wrote
round(140.4) + round(40.4) = 180 where the export writes round(180.8) = 181; n
goes the other way (109.6 → 151.2: 110 + round(41.6) = 152, not 151). With
`builtin_spac_widthaware.py` from 80e96e8 this test fails (verifier, W2).

The second test is the shared primitive on its own: integer amounts read back
exactly through `getDeltas(..., round=round)`.
"""
from __future__ import annotations

import pytest
import uharfbuzz as hb
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphCoordinates
from fontTools.ttLib.tables.ttProgram import Program
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib import instancer
from fontTools.varLib.models import VariationModel

from avar2_studio import spac_amounts
from avar2_studio.transforms.base import BuildContext
from avar2_studio.transforms.builtin_spac_widthaware import WidthAwareSpacTransform

ADVANCE = 1000
HEIGHT = 600
# glyph -> (left, ink at the base, ink at the wide master). The masters average
# 363.75 u (312.5 and 415), so at SPAC ±100 with scale 1.455 and bias 1 a glyph's
# amount is ±0.4 × its ink.
INKS = {"H": (100, 351, 452), "n": (100, 274, 378)}
REFERENCE = 363.75
PARAMS = {"min": -100, "max": 100, "bias": 1.0, "scale": 1.455}


def _box(left, ink):
    return [(left, 0), (left + ink, 0), (left + ink, HEIGHT), (left, HEIGHT)]


def _vf(path):
    """A two-master XTRA font whose wide master widens each glyph's INK.

    Only the right-hand points (and the advance phantom) move; every point gets
    an explicit delta so IUP cannot drag the left side along.
    """
    fb = FontBuilder(1000, isTTF=True)
    names = [".notdef", *INKS]
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(n): n for n in INKS})
    glyf = {".notdef": Glyph()}
    glyf[".notdef"].numberOfContours = 0
    program = Program()
    program.fromAssembly(["SVTCA[0]"])
    for name, (left, ink, _wide) in INKS.items():
        glyph = Glyph()
        glyph.numberOfContours = 1
        glyph.coordinates = GlyphCoordinates(_box(left, ink))
        glyph.flags = bytearray([1] * 4)
        glyph.endPtsOfContours = [3]
        glyph.program = program
        glyf[name] = glyph
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics({".notdef": (ADVANCE, 0),
                               **{name: (ADVANCE, left)
                                  for name, (left, _i, _w) in INKS.items()}})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Amounts", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar([("XTRA", 0, 0, 100, "X-Transparency")], [])
    font = fb.font
    gvar = newTable("gvar")
    gvar.version = 1
    gvar.reserved = 0
    gvar.variations = {}
    for name, (_left, ink, wide) in INKS.items():
        grow = wide - ink
        # four contour points, then the phantoms: origin, advance, top, bottom
        deltas = [(0, 0), (grow, 0), (grow, 0), (0, 0),
                  (0, 0), (grow, 0), (0, 0), (0, 0)]
        gvar.variations[name] = [TupleVariation({"XTRA": (0, 1, 1)}, deltas)]
    font["gvar"] = gvar
    font.save(str(path))
    return path


def _hb_metrics(path, glyph, location):
    """(advance, left sidebearing) as HarfBuzz renders ``glyph`` at ``location``."""
    font = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))
    font.set_variations(location)
    gid = font.get_nominal_glyph(ord(glyph))
    return (font.get_glyph_h_advance(gid),
            font.get_glyph_extents(gid).x_bearing)


def test_the_injector_writes_the_exports_integer_amounts(tmp_path):
    vf = _vf(tmp_path / "amounts.ttf")
    ctx = BuildContext(build_dir=tmp_path, source_path=tmp_path / "x.glyphs",
                       glyphs_path=tmp_path / "x.glyphs", family="Amounts",
                       master_locations=[{}, {"XTRA": 0.0}, {"XTRA": 100.0}])
    out = WidthAwareSpacTransform().apply(vf, dict(PARAMS), ctx)

    base_mean = sum(ink for _l, ink, _w in INKS.values()) / len(INKS)
    wide_mean = sum(wide for _l, _i, wide in INKS.values()) / len(INKS)
    corners = {"base": ({}, 0, base_mean), "wide": ({"XTRA": 100}, 1, wide_mean)}
    checked = []
    for name, inks in INKS.items():
        for end in (PARAMS["min"], PARAMS["max"]):
            floats = [end * inks[1 + i] / REFERENCE * PARAMS["scale"]
                      for i in (0, 1)]
            # The fixture must be one the float path gets wrong: summing the
            # per-tuple roundings misses the export's integer at the wide corner.
            assert (round(floats[0]) + round(floats[1] - floats[0])
                    != round(floats[1])), (name, end, floats)
            for label, (where, index, mean) in corners.items():
                expected = spac_amounts.per_side(
                    float(end), inks[1 + index], mean, REFERENCE,
                    PARAMS["bias"], PARAMS["scale"], ADVANCE, False)
                advance0, lsb0 = _hb_metrics(out, name, where)
                advance1, lsb1 = _hb_metrics(out, name, {**where, "SPAC": end})
                read = (advance1 - advance0, lsb1 - lsb0)
                checked.append((name, end, label, expected, read))
    wrong = [f"{name} at the {label} corner, SPAC {end:+d}: the export moves "
             f"each side by {expected} (advance {2 * expected}); the font "
             f"moves the advance by {read[0]} and the left side by {read[1]}"
             for name, end, label, expected, read in checked
             if read != (2 * expected, expected)]
    assert not wrong, "\n".join(wrong)
    # The numbers in the docstring: H 140 → 181, n 110 → 151 per side at +100.
    assert [(n, lbl, e) for n, end, lbl, e, _r in checked if end > 0] == [
        ("H", "base", 140), ("H", "wide", 181),
        ("n", "base", 110), ("n", "wide", 151)]


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


def _spaced_at_rest(tmp_path, default):
    """The fixture resting at XTRA ``default``, spaced with the pipeline's masters."""
    work = tmp_path / f"rest-{default}"
    work.mkdir()
    path = _vf(work / "vf.ttf")
    if default:
        moved = instancer.instantiateVariableFont(
            TTFont(str(path)), {"XTRA": (0, default, 100)})
        moved.save(str(path))
    ctx = BuildContext(build_dir=work, source_path=work / "x.glyphs",
                       glyphs_path=work / "x.glyphs", family="Amounts",
                       master_locations=[{}, {"XTRA": 0.0}, {"XTRA": 100.0}])
    return WidthAwareSpacTransform().apply(path, dict(PARAMS), ctx)


@pytest.mark.parametrize("default, tolerance", [
    # On a master the relocation is exact.
    (100, 0),
    # Off the masters it rounds the moved outlines, which can move an amount by
    # 1 u per side. Averaging this default in with the masters would move H by
    # more than 3 u per side, so that mistake fails here too.
    (25, 2),
])
def test_moving_the_default_does_not_change_the_amounts(tmp_path, default, tolerance):
    """A SPAC unit means the same wherever the font rests (user report, 2026-11-07).

    Moving Crispy's default from the 400 row to the hairline made every stored
    SPAC value about 19 times stronger, because the amount was measured against
    the mean ink at the default (`Wide 900 MinOpsz`: 69 u per side became 1,436 u).
    """
    home = _spaced_at_rest(tmp_path, 0)
    moved = _spaced_at_rest(tmp_path, default)
    wrong = []
    for name in INKS:
        for where in ({"XTRA": 0}, {"XTRA": 100}):
            for end in (PARAMS["min"], PARAMS["max"]):
                reads = []
                for font in (home, moved):
                    resting, _ = _hb_metrics(font, name, where)
                    spaced, _ = _hb_metrics(font, name, {**where, "SPAC": end})
                    reads.append(spaced - resting)
                if abs(reads[0] - reads[1]) > tolerance:
                    wrong.append(f"{name} at {where}, SPAC {end:+d}: the advance moves "
                                 f"by {reads[0]} resting at XTRA 0, by {reads[1]} "
                                 f"resting at XTRA {default}")
    assert not wrong, "\n".join(wrong)

