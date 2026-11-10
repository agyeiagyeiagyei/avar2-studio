"""Every fvar axis gets a STAT design-axis record, named as fvar names it.

OpenType STAT: "there must be an axis record for every axis defined in the
'fvar' table, and it must use the same name ID used in the 'fvar' table".
The build's STAT step runs before avar2 and SPAC add their axes, so the real
Crispy font shipped 3 records for 12 axes (Fontbakery
``opentype/varfont/STAT_axis_record_for_each_axis``: FAIL) and named wght
"Weight" in STAT but "wght" in fvar. The chain's last step completes the
table; these tests pin its rule on a 4-axis font built the way Crispy's
comes out of step 4.
"""

from __future__ import annotations

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

# fvar order, with the raw-tag name avar2's step gives a user axis.
AXES = [
    ("wght", 100, 400, 900, "wght"),
    ("XTRA", 0, 0, 1, "X-Transparency"),
    ("slnt", -11, 0, 0, "Slant"),
    ("SPAC", -100, 0, 100, "Spacing"),
]
STAT_WGHT = {"tag": "wght", "name": "Weight", "values": [
    {"name": "Regular", "value": 400, "flags": 2, "linkedValue": 700},
    {"name": "Black", "value": 900},
]}
UNTOUCHED = ("glyf", "hmtx", "cmap", "OS/2", "post")


def _vf(path, stat_axes):
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((100, 0))
    pen.lineTo((100, 100))
    pen.closePath()
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "a"])
    fb.setupCharacterMap({0x61: "a"})
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "a": pen.glyph()})
    fb.setupHorizontalMetrics({".notdef": (200, 0), "a": (300, 50)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Probe", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar(AXES, [])
    if stat_axes is not None:
        fb.setupStat(stat_axes)
    fb.save(str(path))
    return path


def _stat(font):
    table = font["STAT"].table
    records = table.DesignAxisRecord.Axis
    values = table.AxisValueArray.AxisValue if table.AxisValueArray else []
    return records, values


def test_every_fvar_axis_gets_a_record_sharing_fvar_name(tmp_path):
    from avar2_studio import pipeline

    path = _vf(tmp_path / "three-missing.ttf", [STAT_WGHT])
    before = TTFont(str(path))
    old_wght_name = next(a.axisNameID for a in before["fvar"].axes if a.axisTag == "wght")
    assert before["name"].getDebugName(old_wght_name) == "wght"
    untouched = {t: before.getTableData(t) for t in UNTOUCHED}

    pipeline._complete_stat_axis_records(path)

    font = TTFont(str(path))
    records, values = _stat(font)
    name = font["name"]
    fvar_axes = {a.axisTag: a for a in font["fvar"].axes}
    assert [r.AxisTag for r in records] == ["wght", "slnt", "XTRA", "SPAC"], \
        "existing first, then slnt, then fvar order"
    assert [r.AxisOrdering for r in records] == [0, 1, 2, 3]
    for record in records:
        assert record.AxisNameID == fvar_axes[record.AxisTag].axisNameID, record.AxisTag
    assert name.getDebugName(fvar_axes["wght"].axisNameID) == "Weight", \
        "fvar takes STAT's name for an axis STAT already names"
    assert name.getDebugName(fvar_axes["XTRA"].axisNameID) == "X-Transparency"
    assert name.getDebugName(old_wght_name) is None, "the raw-tag name is retired"
    # The existing axis values survive untouched; the new records get none.
    assert [(v.AxisIndex, v.Format, v.Value, v.Flags, name.getDebugName(v.ValueNameID))
            for v in values] == [(0, 3, 400, 2, "Regular"), (0, 1, 900, 0, "Black")]
    assert values[0].LinkedValue == 700
    assert font["STAT"].table.Version == 0x10001, "no format 4 value, so 1.1 stays"
    for tag, data in untouched.items():
        assert font.getTableData(tag) == data, tag


def test_a_complete_table_is_left_byte_identical(tmp_path):
    from avar2_studio import pipeline

    path = _vf(tmp_path / "complete.ttf", [STAT_WGHT])
    pipeline._complete_stat_axis_records(path)
    once = path.read_bytes()
    pipeline._complete_stat_axis_records(path)
    assert path.read_bytes() == once


def test_a_font_without_stat_is_untouched(tmp_path):
    from avar2_studio import pipeline

    path = _vf(tmp_path / "no-stat.ttf", None)
    before = path.read_bytes()
    pipeline._complete_stat_axis_records(path)
    assert path.read_bytes() == before
