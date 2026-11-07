"""The avar backstop must not crash on an avar2 font (verifier, 2026-11-06 18:12).

`_repair_avar_axis_count` exists for the case where a stage adds an fvar axis
without recompiling avar. On an avar 2 font the table also carries a VarStore
whose regions were built for the OLD axis list, and fontTools' VarRegionList
writes `RegionAxisCount` from fvar: adding a segment map alone made the repair
raise `(('RegionAxisCount', 7, 6), … 'VarRegionList', 'VarStore')` — cli-build
was red for a day because of it. This test fails on 932de1b.
"""
from __future__ import annotations

from fontTools.designspaceLib import AxisDescriptor
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph
from fontTools.varLib import _add_avar

from avar2_studio import pipeline


def raw_avar_axis_count(path) -> int:
    font = TTFont(str(path))
    entry = font.reader.tables["avar"]
    font.reader.file.seek(entry.offset)
    header = font.reader.file.read(8)
    font.close()
    return int.from_bytes(header[6:8], "big")


def _avar2_font(path, axes=("wght", "wdth", "opsz", "GRAD", "XTRA", "XOPQ")):
    """A font whose avar is 2.0 with a VarStore over ``axes``."""
    fb = FontBuilder(1000, isTTF=True)
    names = [".notdef", "a"]
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({97: "a"})
    glyf = {".notdef": Glyph(), "a": Glyph()}
    for glyph in glyf.values():
        glyph.numberOfContours = 0
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics({name: (500, 0) for name in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Avar2", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar([(tag, 0, 0, 100, tag) for tag in axes], [])
    font = fb.font
    # No gvar on purpose: this fixture is about avar's VarStore, and gvar's own
    # axisCount assertion would hide the defect under test.

    descriptors = {}
    for axis in font["fvar"].axes:
        descriptor = AxisDescriptor()
        descriptor.tag = axis.axisTag
        descriptor.minimum = axis.minValue
        descriptor.default = axis.defaultValue
        descriptor.maximum = axis.maxValue
        descriptors[axis.axisTag] = descriptor
    from collections import OrderedDict

    ordered = OrderedDict((tag, descriptors[tag]) for tag in axes)
    mapping = [type("M", (), {
        "inputLocation": {axes[0]: 100.0},
        "outputLocation": {axes[0]: 50.0},
    })()]
    _add_avar(font, ordered, mapping, list(axes))
    font.save(str(path))
    return path


def test_the_repair_survives_an_avar2_varstore(tmp_path):
    path = _avar2_font(tmp_path / "avar2.ttf")
    assert raw_avar_axis_count(path) == 6
    font = TTFont(str(path))
    assert getattr(font["avar"], "majorVersion", 1) == 2
    font.close()

    # A stage adds an axis WITHOUT touching avar — the case the repair exists for.
    font = TTFont(str(path))
    from fontTools.ttLib.tables._f_v_a_r import Axis

    axis = Axis()
    axis.axisTag = "SPAC"
    axis.minValue = -100.0
    axis.defaultValue = 0.0
    axis.maxValue = 100.0
    axis.axisNameID = font["name"].addMultilingualName(
        {"en": "Spacing"}, font, minNameID=256, mac=False)
    font["fvar"].axes.append(axis)
    font.save(str(path))
    font.close()
    assert raw_avar_axis_count(path) == 6, "avar still names the old axis count"

    pipeline._repair_avar_axis_count(path)

    assert raw_avar_axis_count(path) == 7, "the repair brings avar up to fvar"
    font = TTFont(str(path))          # compiles: no RegionAxisCount mismatch
    assert len(font["fvar"].axes) == 7
    store = font["avar"].table.VarStore
    assert store.VarRegionList.RegionAxisCount == 7
    for region in store.VarRegionList.Region:
        assert len(region.VarRegionAxis) == 7
    font.close()
