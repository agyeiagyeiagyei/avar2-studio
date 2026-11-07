"""Slant Master's panel glue (``plugin.py``) with Glyphs, AppKit and the
panel stood in for: the source list, the angles kept in the source file,
the shared axis value and the batch Apply.

The fakes copy what Glyphs 3.5 does where the glue depends on it: a
copied master keeps its id until ``masters.append`` gives it a fresh one
and every glyph an empty layer for it; ``userData`` answers None for a
missing key; filling the list fires its edit callback just as typing
does."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import math
import sys
import types
from pathlib import Path

import pytest

RESOURCES = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
             / "SlantMaster.glyphsReporter" / "Contents" / "Resources")
ANGLE_KEY = "xyz.avar2studio.slant-angle"
_ids = itertools.count(1)


class _Anything:
    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()


class GlyphsStub:
    font = None
    fonts = ()
    defaults = {}

    def localize(self, names):
        return names["en"]

    def redraw(self):
        pass


class Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Node:
    """``position`` accepts a tuple and reads back with ``.x``/``.y``,
    like GSNode's."""

    def __init__(self, x=0.0, y=0.0, type_="line"):
        self.position = (x, y)
        self.type = type_

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value if isinstance(value, Pt) else Pt(float(value[0]), float(value[1]))


class Path_:
    def __init__(self, points, closed=True):
        self.nodes = [Node(x, y) for x, y in points]
        self.closed = closed


class UserData(dict):
    def __missing__(self, key):
        return None


class Layer:
    """``bounds`` is empty for a layer no glyph holds, as in Glyphs."""

    def __init__(self, paths=(), layerId=None, boom=False):
        self.paths = list(paths)
        self.components = []
        self.width = 600.0
        self.layerId = self.associatedMasterId = layerId
        self.boom = boom
        self.parent = None

    def copyDecomposedLayer(self):
        dup = Layer(copy.deepcopy(self.paths), self.layerId, self.boom)
        dup.width = self.width
        return dup

    copy = copyDecomposedLayer

    def applyTransform(self, m):
        if self.boom:
            raise RuntimeError("layer could not be transformed")
        for path in self.paths:
            for n in path.nodes:
                x, y = n.position.x, n.position.y
                n.position = Pt(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])

    def _xs(self):
        return [n.position.x for path in self.paths for n in path.nodes]

    @property
    def bounds(self):
        xs = self._xs() if self.parent is not None else []
        ys = [n.position.y for path in self.paths for n in path.nodes] if xs else []
        x, y = (min(xs), min(ys)) if xs else (0.0, 0.0)
        return types.SimpleNamespace(
            origin=Pt(x, y),
            size=types.SimpleNamespace(width=max(xs) - x if xs else 0.0, height=max(ys) - y if xs else 0.0))

    @property
    def LSB(self):
        return min(self._xs()) if self.paths else 0.0  # an empty layer: 0 and 0, as in Glyphs

    @LSB.setter
    def LSB(self, value):  # moves the outline, and the advance with it
        shift = value - self.LSB
        for path in self.paths:
            for n in path.nodes:
                n.position = Pt(n.position.x + shift, n.position.y)
        self.width += shift

    @property
    def RSB(self):
        return self.width - max(self._xs()) if self.paths else 0.0

    @RSB.setter
    def RSB(self, value):
        self.width = max(self._xs()) + value


def stem(layerId, boom=False):
    """A 100 x 1200 upright stem."""
    return Layer([Path_([(0, 0), (100, 0), (100, 1200), (0, 1200)])], layerId, boom)


class Layers(dict):
    def __init__(self, glyph, layers=()):
        dict.__init__(self)
        self.glyph = glyph
        for key, layer in layers:
            self[key] = layer

    def __missing__(self, key):
        return None

    def __setitem__(self, key, layer):
        layer.parent = self.glyph
        dict.__setitem__(self, key, layer)


class Glyph:
    def __init__(self, name, masters, boom=False):
        self.name = name
        self.lastChange = 0
        self.layers = Layers(self, ((m.id, stem(m.id, boom)) for m in masters))

    def beginUndo(self):
        pass

    def endUndo(self):
        pass


class Glyphs_(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next((g for g in self if g.name == key), None)
        return list.__getitem__(self, key)


class Master:
    def __init__(self, name, axes, italicAngle=0.0):
        self.name, self.axes, self.italicAngle = name, list(axes), italicAngle
        self.id = "M%d" % next(_ids)
        self.xHeight, self.capHeight, self.ascender, self.descender = 1200.0, 1600.0, 2000.0, -400.0
        self.userData = UserData()

    def copy(self):
        return copy.deepcopy(self)  # the id included, as in Glyphs


class Masters(list):
    def __init__(self, font, masters):
        list.__init__(self, masters)
        self.font = font

    def append(self, master):
        master.id = "M%d" % next(_ids)
        list.append(self, master)
        for g in self.font.glyphs:
            g.layers[master.id] = Layer(layerId=master.id)


class Instance:
    """Interpolates to a one-master font; counts how often it is asked."""

    def __init__(self, name, axes, glyphNames):
        self.name, self.axes = name, list(axes)
        self.userData = UserData()
        self.interpolations = 0
        self._glyphNames = glyphNames

    @property
    def interpolatedFont(self):
        self.interpolations += 1
        master = Master(self.name, self.axes)
        return types.SimpleNamespace(
            masters=[master], glyphs=Glyphs_(Glyph(n, [master]) for n in self._glyphNames))


class Axis:
    def __init__(self, tag):
        self.axisTag = tag


class Font:
    upm = 2000
    gridLength = 0.0  # no rounding, so the geometry below is exact; the grid has its own tests
    filepath = "/x/Crispy.glyphs"
    familyName = "Crispy"
    selectedLayers = ()

    def __init__(self, tags=("XTRA", "XOPQ", "YOPQ", "slnt"), glyphs=("A", "B", "C"), boom=()):
        self.axes = [Axis(t) for t in tags]
        zero = [0] * (len(tags) - 3)
        uprights = [Master("47-1-1", [47, 1, 1] + zero), Master("1715-1-1", [1715, 1, 1] + zero),
                    Master("47-1462-1", [47, 1462, 1] + zero)]
        self.glyphs = Glyphs_(Glyph(n, uprights, boom=n in boom) for n in glyphs)
        self.masters = Masters(self, uprights)
        self.instances = [Instance("Regular", [400, 400, 100] + zero, glyphs)]

    def disableUpdateInterface(self):
        pass

    def enableUpdateInterface(self):
        pass


class Field:
    def __init__(self, value=""):
        self.value = self.title = value
        self.enabled = True

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def setItems(self, items):
        self.items = items

    def setTitle(self, title):
        self.title = title

    def enable(self, onOff):
        self.enabled = onOff


class Item(dict):
    """A list row. Writing to it notifies the list, as the real one's
    key-value observing does."""

    def __init__(self, values, owner):
        dict.__init__(self, values)
        self.owner = owner

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        self.owner.callback(self.owner)


class ListField:
    def __init__(self, callback):
        self.callback = callback
        self.items = []

    def set(self, items):
        self.items = [Item(i, self) for i in items]
        self.callback(self)

    def get(self):
        return self.items


@pytest.fixture
def slantmaster(monkeypatch):
    """``plugin.py`` loaded against the stand-ins; returns a function
    that makes a SlantMaster with a panel on a font."""
    monkeypatch.setattr(sys, "path", list(sys.path))  # plugin.py prepends its folder
    glyphs = GlyphsStub()
    appkit = types.ModuleType("AppKit")
    for name in ("NSAffineTransform", "NSBezierPath", "NSColor", "NSNumberFormatter",
                 "NSNumberFormatterDecimalStyle"):
        setattr(appkit, name, _Anything())
    glyphsapp = types.ModuleType("GlyphsApp")
    glyphsapp.Glyphs, glyphsapp.GSNode, glyphsapp.__all__ = glyphs, Node, ["Glyphs"]
    plugins = types.ModuleType("GlyphsApp.plugins")
    plugins.ReporterPlugin, plugins.__all__ = object, ["ReporterPlugin"]
    objc = types.ModuleType("objc")
    objc.python_method = lambda f: f
    for name, mod in (("objc", objc), ("AppKit", appkit), ("GlyphsApp", glyphsapp),
                      ("GlyphsApp.plugins", plugins), ("vanilla", None)):
        monkeypatch.setitem(sys.modules, name, mod)
    for name in ("slant_math", "slant_extrema", "slant_paths"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location("slantmaster_plugin", RESOURCES / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def make(font):
        glyphs.font, glyphs.fonts = font, [font]
        p = module.SlantMaster()
        p.settings()
        w = types.SimpleNamespace(list=ListField(p._rowsEdited))
        for name, value in dict(
                allField="10", widthField="100", heightField="100", offsetField="0",
                axisField="", axisLabel="", suffixField="Italic", applyButton="Apply",
                readout="", status1="", status2="", originPop=1, spacingPop=3, refPop=0,
                decomposeBox=True, extremaBox=False, italicBox=True, matchBox=False).items():
            setattr(w, name, Field(value))
        p._panel = p.panel = w
        p.module = module
        p._syncPanelToFont(font)
        return p

    return make


def rows(p):
    return [(i["use"], i["label"], i["angle"]) for i in p.panel.list.get()]


def edit(p, index, key, value):
    p.panel.list.get()[index][key] = value


def type_in(p, field, text):
    getattr(p.panel, field).set(text)
    p._paramsChanged(getattr(p.panel, field))


def status(p):
    return p.panel.status1.get() + " | " + p.panel.status2.get()


# --- the list ---------------------------------------------------------------


def test_list_has_masters_then_instances_and_ticks_the_uprights(slantmaster):
    font = Font()
    font.masters[2].italicAngle = 12.0  # slanted already
    p = slantmaster(font)
    assert rows(p) == [
        (True, "Master: 47-1-1", 10.0),
        (True, "Master: 1715-1-1", 10.0),
        (False, "Master: 47-1462-1", 10.0),
        (False, "Instance: Regular", 10.0),
    ]
    assert p.panel.applyButton.title == "Apply (2)"


def test_an_angle_is_saved_in_the_source_file_and_comes_back(slantmaster):
    font = Font()
    p = slantmaster(font)
    edit(p, 1, "angle", 11.5)
    edit(p, 3, "angle", 9.0)
    assert font.masters[1].userData[ANGLE_KEY] == 11.5
    assert font.instances[0].userData[ANGLE_KEY] == 9.0
    assert font.masters[0].userData[ANGLE_KEY] is None, "an untouched row writes nothing"
    # A new session: nothing but the font carries over.
    assert [r[2] for r in rows(slantmaster(font))] == [10.0, 11.5, 10.0, 9.0]


def test_an_emptied_angle_cell_keeps_its_angle(slantmaster):
    font = Font()
    p = slantmaster(font)
    edit(p, 0, "angle", 12.0)
    edit(p, 0, "angle", None)
    assert rows(p)[0] == (True, "Master: 47-1-1", 12.0)
    assert font.masters[0].userData[ANGLE_KEY] == 12.0
    assert "12° for 47-1-1" in status(p)


def test_set_all_angles(slantmaster):
    font = Font()
    p = slantmaster(font)
    p.panel.allField.set("8.5")
    p._setAllAngles(None)
    assert [r[2] for r in rows(p)] == [8.5] * 4
    assert [m.userData[ANGLE_KEY] for m in font.masters] == [8.5] * 3
    p.panel.allField.set("steep")
    p._setAllAngles(None)
    assert [r[2] for r in rows(p)] == [8.5] * 4
    assert "has to be a number" in status(p)


def test_unticking_is_remembered_across_a_resync(slantmaster):
    font = Font()
    p = slantmaster(font)
    edit(p, 0, "use", False)
    p._syncPanelToFont(font)
    assert [r[0] for r in rows(p)] == [False, True, True, False]


# --- the axis value ---------------------------------------------------------


def test_slnt_default_keeps_following_the_angle(slantmaster):
    p = slantmaster(Font())
    assert p.panel.axisField.get() == "-10"
    type_in(p, "widthField", "95")  # any other field, read back with the rest
    edit(p, 0, "angle", 12.0)
    assert p.panel.axisField.get() == "-12"
    assert p.axisValue is None


def test_slnt_default_is_the_first_ticked_rows_angle(slantmaster):
    p = slantmaster(Font())
    edit(p, 0, "angle", 12.0)
    edit(p, 1, "angle", 14.0)
    assert p.panel.axisField.get() == "-12"
    edit(p, 0, "use", False)
    assert p.panel.axisField.get() == "-14"


def test_a_typed_axis_value_stays(slantmaster):
    p = slantmaster(Font())
    type_in(p, "axisField", "-9")
    edit(p, 0, "angle", 12.0)
    assert p.panel.axisField.get() == "-9"
    type_in(p, "axisField", "")  # emptied: the default again, from the next change on
    edit(p, 0, "angle", 13.0)
    assert p.panel.axisField.get() == "-13"


def test_ital_axis_default_is_one(slantmaster):
    p = slantmaster(Font(tags=("XTRA", "XOPQ", "YOPQ", "ital")))
    edit(p, 0, "angle", 12.0)
    assert p.panel.axisField.get() == "1"


def test_without_an_italic_axis_the_field_is_off(slantmaster):
    p = slantmaster(Font(tags=("XTRA", "XOPQ", "YOPQ")))
    assert not p.panel.axisField.enabled
    assert "no ital/slnt axis" in p.panel.axisLabel.get()


# --- Apply ------------------------------------------------------------------


def test_apply_makes_a_master_per_ticked_row_at_its_own_angle(slantmaster):
    font = Font()
    p = slantmaster(font)
    edit(p, 1, "angle", 12.0)
    edit(p, 2, "use", False)
    uprights = list(font.masters)
    before = {(g.name, m.id): [(n.position.x, n.position.y) for n in g.layers[m.id].paths[0].nodes]
              for g in font.glyphs for m in uprights}
    p._apply(None)

    made = font.masters[3:]
    assert [m.name for m in made] == ["47-1-1 Italic", "1715-1-1 Italic"]
    assert [m.italicAngle for m in made] == [10.0, 12.0], "each its own angle"
    assert [m.axes for m in made] == [[47, 1, 1, -10.0], [1715, 1, 1, -10.0]], "one slnt value for all"
    assert len({m.id for m in font.masters}) == 5
    for m, angle in zip(made, (10.0, 12.0)):
        for g in font.glyphs:
            top_left = g.layers[m.id].paths[0].nodes[3].position
            assert top_left.x == pytest.approx(math.tan(math.radians(angle)) * 600.0)  # pivot: x-height / 2
            assert top_left.y == 1200.0
    after = {(g.name, m.id): [(n.position.x, n.position.y) for n in g.layers[m.id].paths[0].nodes]
             for g in font.glyphs for m in uprights}
    assert after == before, "the uprights are never touched"
    assert p.panel.status1.get() == "2 master(s) created, 3 glyphs each"


def test_the_new_masters_are_listed_unticked(slantmaster):
    font = Font()
    p = slantmaster(font)
    p.panel.italicBox.set(False)  # nothing on the master says it is slanted
    p._apply(None)
    p._syncPanelToFont(font)
    assert [(use, label) for use, label, _ in rows(p)][3:6] == [
        (False, "Master: 47-1-1 Italic"), (False, "Master: 1715-1-1 Italic"),
        (False, "Master: 47-1462-1 Italic")]


def test_name_suffix(slantmaster):
    font = Font()
    p = slantmaster(font)
    type_in(p, "suffixField", "Slanted")
    p._apply(None)
    assert [m.name for m in font.masters[3:]] == ["47-1-1 Slanted", "1715-1-1 Slanted", "47-1462-1 Slanted"]


def test_apply_refuses_a_name_in_use_before_creating_anything(slantmaster):
    font = Font()
    font.masters.append(Master("1715-1-1 Italic", [1715, 1, 1, -10], italicAngle=10.0))
    p = slantmaster(font)
    p._apply(None)
    assert len(font.masters) == 4
    assert "name in use: 1715-1-1 Italic" in status(p)
    assert "nothing was created" in status(p)


def test_apply_refuses_an_axis_value_that_is_not_a_number(slantmaster):
    font = Font()
    p = slantmaster(font)
    type_in(p, "axisField", "-")
    p._apply(None)
    assert len(font.masters) == 3
    assert "slnt axis value has to be a number" in status(p)


def test_apply_with_nothing_ticked(slantmaster):
    font = Font()
    p = slantmaster(font)
    for i in range(3):
        edit(p, i, "use", False)
    p._apply(None)
    assert len(font.masters) == 3
    assert "nothing is ticked" in status(p)


def test_a_glyph_that_fails_is_named_and_the_rest_land(slantmaster, capsys):
    font = Font(glyphs=("A", "B", "C", "D", "E"), boom=("C",))
    p = slantmaster(font)
    edit(p, 1, "use", False)
    edit(p, 2, "use", False)
    p._apply(None)
    new = font.masters[-1]
    assert [g.name for g in font.glyphs if g.layers[new.id].paths] == ["A", "B", "D", "E"]
    assert p.panel.status1.get() == "1 master(s) created, 4 glyphs each — 1 with problems"
    assert ("47-1-1 Italic — 1 glyph(s) failed: C (RuntimeError: layer could not be transformed)"
            in p.panel.status2.get())
    assert "landed" not in status(p), "the four that were put there are there"
    assert "RuntimeError: layer could not be transformed" in capsys.readouterr().out


def test_a_layer_that_did_not_land_is_counted(slantmaster):
    font = Font()
    p = slantmaster(font)
    edit(p, 1, "use", False)
    edit(p, 2, "use", False)

    class Forgetful(Layers):  # takes the layer and keeps the empty one
        def __setitem__(self, key, layer):
            if key in self:
                return
            Layers.__setitem__(self, key, layer)

    font.glyphs["B"].layers = Forgetful(font.glyphs["B"], font.glyphs["B"].layers.items())
    p._apply(None)
    assert "47-1-1 Italic — only 2 of 3 layers landed" in p.panel.status2.get()


def test_a_changed_node_structure_is_reported_when_the_counts_match(slantmaster):
    font = Font()
    p = slantmaster(font)
    edit(p, 1, "use", False)
    edit(p, 2, "use", False)
    real = p.module.slant_paths.slant_layer

    def moved_start_node(layer, matrix, **kwargs):
        stats = real(layer, matrix, **kwargs)
        stats.update(inserted=2, removed=2, structure_changed=True)
        return stats

    p.module.slant_paths.slant_layer = moved_start_node
    p._apply(None)
    assert "3 glyph(s) changed node structure" in p.panel.status2.get()
    assert "A, B, C" in p.panel.status2.get()


def test_apply_interpolates_an_instance_afresh(slantmaster):
    font = Font()
    p = slantmaster(font)
    for i in range(3):
        edit(p, i, "use", False)
    edit(p, 3, "use", True)
    instance = font.instances[0]
    entry = p._rows[3]["entry"]
    p._entryLayer(entry, "A")  # what the overlay does: interpolate once, keep it
    p._entryLayer(entry, "B")
    assert instance.interpolations == 1
    p._apply(None)
    assert instance.interpolations == 2, "Apply must not write from the kept interpolation"
    assert [m.name for m in font.masters[3:]] == ["Regular Italic"]
    assert font.masters[3].axes == [400, 400, 100, -10.0]


def test_remove_last_takes_every_master_of_the_last_apply(slantmaster):
    font = Font()
    p = slantmaster(font)
    p._apply(None)
    assert len(font.masters) == 6
    p._removeLast(None)
    assert [m.name for m in font.masters] == ["47-1-1", "1715-1-1", "47-1462-1"]
    assert "removed 3 master(s)" in status(p)
    p._removeLast(None)
    assert "nothing to remove" in status(p)


# --- the overlay ------------------------------------------------------------


def test_the_overlay_follows_the_master_on_screen(slantmaster):
    font = Font()
    p = slantmaster(font)
    edit(p, 1, "angle", 12.0)
    layer = font.glyphs["A"].layers[font.masters[1].id]
    row = p._rowForLayer(layer)
    assert (row["entry"]["name"], row["angle"]) == ("1715-1-1", 12.0)
    pv = p._previewFor(font, "A", row)
    assert pv["stats"]["inserted"] == 0 and pv["adv"] == 600.0
    edit(p, 1, "use", False)
    assert p._rowForLayer(layer) is None, "an unticked master is not previewed"


def test_the_overlay_plans_the_spacing_of_a_glyph_with_ink(slantmaster):
    """The sheared copy belongs to no glyph, and Glyphs reports no bounds
    for such a layer: asked that way, every glyph looks empty."""
    font = Font()
    p = slantmaster(font)
    type_in(p, "matchBox", True)
    layer = font.glyphs["A"].layers[font.masters[1].id]
    pv = p._previewFor(font, "A", p._rowForLayer(layer))
    assert pv["plan"] == pytest.approx((0.0, 500.0, 600.0))  # keep LSB: the stem is 100 wide
    p._updateReadout(font, "A", pv, p._rowForLayer(layer))
    assert "LSB 0 / RSB 500 / adv 600" in p.panel.readout.get()
    assert "empty" not in p.panel.readout.get()


def test_the_overlay_measures_along_the_slant_as_apply_will(slantmaster):
    font = Font()
    p = slantmaster(font)
    type_in(p, "matchBox", True)
    type_in(p, "originPop", 0)  # sheared around the baseline: the stem leans off to the right
    layer = font.glyphs["A"].layers[font.masters[0].id]
    lean = math.tan(math.radians(10.0)) * 1200.0

    pv = p._previewFor(font, "A", p._rowForLayer(layer))
    # The new master carries the angle, so its ink is the stem's 100
    # units, found half an x-height's lean to the right.
    assert pv["plan"] == pytest.approx((0.0, 500.0, 600.0))
    assert pv["dx"] == pytest.approx(-lean / 2.0)

    type_in(p, "italicBox", False)  # no angle on the master: Glyphs measures the box
    pv = p._previewFor(font, "A", p._rowForLayer(layer))
    assert pv["plan"] == pytest.approx((0.0, 500.0 - lean, 600.0))
    assert pv["dx"] == pytest.approx(0.0)


def test_the_overlay_still_knows_an_empty_glyph(slantmaster):
    font = Font()
    for m in font.masters:
        font.glyphs["B"].layers[m.id].paths = []
    p = slantmaster(font)
    type_in(p, "matchBox", True)
    type_in(p, "offsetField", "20")
    layer = font.glyphs["B"].layers[font.masters[0].id]
    pv = p._previewFor(font, "B", p._rowForLayer(layer))
    assert pv["plan"] == (None, None, 620.0)


# --- the reference ----------------------------------------------------------


def widths_differ(font):
    """Three masters as unlike as parametric ones are: 600, 800, 1000."""
    for i, m in enumerate(font.masters):
        for g in font.glyphs:
            g.layers[m.id].width = 600.0 + 200.0 * i


def test_each_new_master_takes_the_widths_of_its_own_source(slantmaster):
    font = Font()
    widths_differ(font)
    p = slantmaster(font)
    type_in(p, "matchBox", True)
    assert p.panel.refPop.items[:2] == ["Each source itself", "Master: 47-1-1"]
    assert p.panel.refPop.get() == 0, "the default"
    p._apply(None)
    assert [font.glyphs["A"].layers[m.id].width for m in font.masters[3:]] == [600.0, 800.0, 1000.0]
    assert "widths vs each source itself" in p.panel.status2.get()


def test_a_picked_reference_is_the_reference_of_every_row(slantmaster):
    font = Font()
    widths_differ(font)
    p = slantmaster(font)
    type_in(p, "matchBox", True)
    p.panel.refPop.set(2)
    p._refChanged(p.panel.refPop)
    p._apply(None)
    assert [font.glyphs["A"].layers[m.id].width for m in font.masters[3:]] == [800.0, 800.0, 800.0]
    assert "widths vs Master: 1715-1-1" in p.panel.status2.get()


def test_the_overlay_reads_out_the_reference_of_its_row(slantmaster):
    font = Font()
    widths_differ(font)
    p = slantmaster(font)
    type_in(p, "matchBox", True)
    layer = font.glyphs["A"].layers[font.masters[2].id]
    row = p._rowForLayer(layer)
    pv = p._previewFor(font, "A", row)
    assert pv["plan"] == pytest.approx((0.0, 900.0, 1000.0))
    p._updateReadout(font, "A", pv, row)
    assert p.panel.readout.get().endswith("adv 1000 vs Master: 47-1462-1")


# --- the grid ---------------------------------------------------------------


def test_apply_puts_the_new_layers_on_the_fonts_grid(slantmaster):
    font = Font()
    font.gridLength = 1.0
    p = slantmaster(font)
    edit(p, 0, "angle", 11.5)
    p._apply(None)
    tan = math.tan(math.radians(11.5))
    for m in font.masters[3:]:
        for g in font.glyphs:
            for n in g.layers[m.id].paths[0].nodes:
                assert float(n.position.x).is_integer() and float(n.position.y).is_integer()
    top_left = font.glyphs["A"].layers[font.masters[3].id].paths[0].nodes[3].position
    assert top_left.x == math.floor(tan * 600.0 + 0.5)


def test_a_font_without_a_grid_is_not_rounded(slantmaster):
    font = Font()
    font.gridLength = 0.0
    p = slantmaster(font)
    p._apply(None)
    top_left = font.glyphs["A"].layers[font.masters[3].id].paths[0].nodes[3].position
    assert top_left.x == pytest.approx(math.tan(math.radians(10.0)) * 600.0)
    assert not float(top_left.x).is_integer()


def test_the_overlay_shows_the_rounded_outline(slantmaster):
    """What is drawn and measured before Apply is what Apply will write."""
    font = Font()
    font.gridLength = 1.0
    p = slantmaster(font)
    type_in(p, "matchBox", True)
    type_in(p, "originPop", 0)  # around the baseline: the top of the stem moves by tan × 1200
    layer = font.glyphs["A"].layers[font.masters[0].id]
    pv = p._previewFor(font, "A", p._rowForLayer(layer))
    tan = math.tan(math.radians(10.0))
    top = math.floor(tan * 1200.0 + 0.5)           # where the top-left node lands, rounded
    right = (100.0 + top) - tan * 600.0            # the top-right node, seen along the slant
    assert pv["plan"] == pytest.approx((0.0, 600.0 - (right - tan * 600.0), 600.0))
