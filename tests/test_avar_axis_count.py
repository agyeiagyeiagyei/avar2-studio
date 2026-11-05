"""avar's RAW axis count must equal fvar's (verifier, 17:40).

fontTools writes ``AxisCount = len(fvar.axes)`` when it compiles avar and copies
the bytes verbatim when the table was never loaded, so any stage that adds or
drops fvar axes leaves the header naming the old count. Measured on Crispy's
headless build: **29 against 12**, which makes consumers drop the table outright
(FreeType loses avar and avar2 together, so wght/wdth/opsz are dead there).

The test reads bytes 6–7 of the table itself rather than ``f["avar"].segments``:
decompiling an avar that no longer matches fvar fabricates one segment map per
*fvar* axis, which hides exactly this defect.
"""

from __future__ import annotations

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable


def _raw_axis_count(path) -> int:
    font = TTFont(str(path))
    entry = font.reader.tables["avar"]
    font.reader.file.seek(entry.offset)
    return int.from_bytes(font.reader.file.read(8)[6:8], "big")


def _tiny_vf(path, tags, with_avar=True):
    """A valid 2-glyph VF with the given axes (FontBuilder does the plumbing)."""
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((100, 0))
    pen.lineTo((100, 100))
    pen.closePath()
    glyphs = {".notdef": TTGlyphPen(None).glyph(), "a": pen.glyph()}
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "a"])
    fb.setupCharacterMap({0x61: "a"})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({".notdef": (200, 0), "a": (300, 50)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Probe", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar([(tag, 0, 0, 1, tag) for tag in tags], [])
    fb.save(str(path))
    if with_avar:
        # setupAvar wants varLib's own objects; a plain segments dict is what
        # the table's compile() reads, so add it and let the save compile it.
        font = TTFont(str(path))
        avar = newTable("avar")
        avar.segments = {tag: {} for tag in tags}
        font["avar"] = avar
        font.save(str(path))
    return path


def test_the_repair_fixes_a_stale_raw_axis_count(tmp_path):
    """A header naming more axes than fvar is rewritten to the fvar count.

    Built the way the defect happens: 21 axes when avar was written, then fvar
    drops to 4 without the table being loaded, so its bytes pass through and the
    header keeps naming 21."""
    from avar2_studio import pipeline

    tags = [f"A{i:03d}" for i in range(21)]
    path = _tiny_vf(tmp_path / "stale.ttf", tags)
    assert _raw_axis_count(path) == 21

    font = TTFont(str(path))
    font["fvar"].axes = font["fvar"].axes[:4]
    font["fvar"].instances = []
    font.save(str(path))                       # avar was never loaded: bytes kept
    assert _raw_axis_count(path) == 21, "the stale header survives a rewrite"

    pipeline._repair_avar_axis_count(path)
    assert _raw_axis_count(path) == 4, "the header must follow fvar"

    after = path.read_bytes()
    pipeline._repair_avar_axis_count(path)
    assert path.read_bytes() == after, "a correct table must not be rewritten"


def test_a_correct_table_is_left_byte_identical(tmp_path):
    from avar2_studio import pipeline

    path = _tiny_vf(tmp_path / "clean.ttf", ["XTRA", "XOPQ"])
    before = path.read_bytes()
    pipeline._repair_avar_axis_count(path)
    assert path.read_bytes() == before


def test_a_font_without_avar_is_untouched(tmp_path):
    from avar2_studio import pipeline

    path = _tiny_vf(tmp_path / "no-avar.ttf", ["XTRA"], with_avar=False)
    before = path.read_bytes()
    pipeline._repair_avar_axis_count(path)
    assert path.read_bytes() == before
