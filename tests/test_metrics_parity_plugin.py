"""Metrics Parity's panel glue (the ParametricMasters bundle's
``plugin.py``) with Glyphs, AppKit and the panel stood in for: grouping,
the scan, and what a double-click on a row does — including the cases
that used to end in silence."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
          / "ParametricMasters.glyphsReporter" / "Contents" / "Resources" / "plugin.py")


class _Anything:
    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()


class GlyphsStub:
    font = None
    defaults = {}
    currentDocument = None

    def localize(self, names):
        return names["en"]

    def redraw(self):
        pass


class Pt:
    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)


class Node:
    def __init__(self, x, y, type_="line"):
        self.position, self.type = Pt(x, y), type_


class Path_:
    closed = True

    def __init__(self, points):
        self.nodes = [Node(*p) for p in points]


class Component:
    def __init__(self, name, transform=(1, 0, 0, 1, 0, 0)):
        self.componentName, self.transform = name, tuple(float(v) for v in transform)


class Layer:
    """Advance and sidebearings as given. ``LSB`` and ``RSB`` are what
    Glyphs shows, in whole units; the outline is drawn from ``ink``, the
    sidebearings as they really are (the same unless said otherwise)."""

    def __init__(self, width, lsb, rsb, ink=None, components=()):
        self.width, self.LSB, self.RSB = width, lsb, rsb
        self.ink, self.components = ink, list(components)

    @property
    def paths(self):
        if self.components:
            return []
        lsb, rsb = self.ink or (self.LSB, self.RSB)
        return [Path_([(lsb, 0), (self.width - rsb, 0), (self.width - rsb, 700), (lsb, 700)])]


class Layers(dict):
    def __missing__(self, key):
        return None


class Glyph:
    def __init__(self, name, layers):
        self.name = name
        self.layers = Layers(layers)


class Glyphs_(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next((g for g in self if g.name == key), None)
        return list.__getitem__(self, key)


class Master:
    italicAngle, xHeight = 0.0, 500.0

    def __init__(self, name, axes):
        self.name, self.axes, self.id = name, list(axes), "id-" + name


class Axis:
    def __init__(self, name, tag):
        self.name, self.axisTag = name, tag


class Tab:
    masterIndex = None


class Font:
    """Three masters; 47-1-1 and 47-1-275 share XTRA + XOPQ. ``A`` and
    ``B`` disagree between them, ``C`` agrees."""

    def __init__(self, newTab=None):
        # Named by tag, as in Crispy: the plugin's default pair is looked
        # up by axis NAME ("XTRA", "XOPQ").
        self.axes = [Axis("XTRA", "XTRA"), Axis("XOPQ", "XOPQ"), Axis("YOPQ", "YOPQ")]
        self.masters = [Master("1715-1-1", [1715, 1, 1]), Master("47-1-1", [47, 1, 1]),
                        Master("47-1-275", [47, 1, 275])]
        a, b, c = (m.id for m in self.masters)
        self.glyphs = Glyphs_([
            Glyph("A", {a: Layer(900, 50, 50), b: Layer(175, 37, 40), c: Layer(180, 37, 45)}),
            Glyph("B", {a: Layer(900, 50, 50), b: Layer(165, 40, 27), c: Layer(165, 40, 40)}),
            Glyph("C", {a: Layer(900, 50, 50), b: Layer(162, 37, 30), c: Layer(162, 37, 30)}),
        ])
        self.masterIndex = 0
        self.tabs = []
        self._newTab = newTab

    def newTab(self, text):
        if self._newTab is not None:
            return self._newTab(text)
        tab = Tab()
        tab.text = text
        self.tabs.append(tab)
        return tab


class Field:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class TableView:
    def __init__(self, clicked=-1):
        self.clicked = clicked

    def clickedRow(self):
        return self.clicked


class ListField:
    """A vanilla List as far as the glue uses it; counts rewrites."""

    def __init__(self):
        self.items, self.selection, self.sets, self.table = [], [], 0, TableView()

    def set(self, items):
        self.items, self.selection = list(items), []
        self.sets += 1

    def get(self):
        return self.items

    def getSelection(self):
        return self.selection

    def setSelection(self, selection):
        self.selection = list(selection)

    def getNSTableView(self):
        return self.table


class PopUp(Field):
    def setItems(self, items):
        self.items = list(items)


def panel():
    return types.SimpleNamespace(list=ListField(), summary=Field(), note=Field())


@pytest.fixture
def parity(monkeypatch):
    glyphs = GlyphsStub()
    appkit = types.ModuleType("AppKit")
    appkit.__getattr__ = lambda name: _Anything()
    glyphsapp = types.ModuleType("GlyphsApp")
    glyphsapp.Glyphs, glyphsapp.UPDATEINTERFACE = glyphs, "UPDATEINTERFACE"
    glyphsapp.__all__ = ["Glyphs", "UPDATEINTERFACE"]
    plugins = types.ModuleType("GlyphsApp.plugins")
    plugins.ReporterPlugin, plugins.__all__ = object, ["ReporterPlugin"]
    objc = types.ModuleType("objc")
    objc.python_method = lambda f: f
    for name, mod in (("objc", objc), ("AppKit", appkit), ("GlyphsApp", glyphsapp),
                      ("GlyphsApp.plugins", plugins), ("vanilla", None)):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("parity_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def make(font):
        glyphs.font = font
        p = module.ParametricMasters()
        p.settings()
        p._panel = panel()
        p._scan()
        return p

    return make


def glyph_names(p):
    return [r["glyph"] for r in p._panel.list.get()]


def test_the_title_says_what_the_tool_checks(parity):
    assert parity(Font()).menuName == "Metrics Parity"


def test_scan_flags_the_glyphs_that_disagree_and_names_the_group(parity):
    p = parity(Font())
    assert glyph_names(p) == ["A", "B"]
    row = p._panel.list.get()[0]
    assert row["group"] == "XTRA 47 · XOPQ 1"
    assert row["masters"] == "47-1-1 (YOPQ 1) · 47-1-275 (YOPQ 275)"
    assert (row["dadv"], row["dlsb"], row["drsb"]) == ("5", "0", "5")
    assert row["_masterIndex"] == 1, "the group's first master, by its index in the font"


def test_double_click_opens_the_clicked_row_at_the_groups_first_master(parity):
    font = Font()
    p = parity(font)
    lst = p._panel.list
    lst.table.clicked = 1          # the row under the pointer …
    lst.selection = []             # … even with nothing selected
    p._openGlyph(lst)
    assert [t.text for t in font.tabs] == ["/B"]
    assert font.tabs[0].masterIndex == 1
    assert p._panel.note.get() == "opened B at 47-1-1"


def test_double_click_falls_back_to_the_selection(parity):
    font = Font()
    p = parity(font)
    lst = p._panel.list
    lst.table.clicked = -1
    lst.selection = [0]
    p._openGlyph(lst)
    assert [t.text for t in font.tabs] == ["/A"]


def test_every_double_click_opens_a_tab_not_only_the_first(parity):
    font = Font()
    p = parity(font)
    lst = p._panel.list
    for clicked in (0, 1, 0):
        lst.table.clicked = clicked
        p._openGlyph(lst)
        p._scan()                  # the live rescan an open tab provokes
    assert [t.text for t in font.tabs] == ["/A", "/B", "/A"]
    assert lst.sets == 1, "an unchanged scan must not rewrite the list"


def test_a_failure_to_open_is_reported_in_the_panel(parity):
    def boom(text):
        raise RuntimeError("no document window")

    p = parity(Font(newTab=boom))
    lst = p._panel.list
    lst.table.clicked = 0
    p._openGlyph(lst)
    assert p._panel.note.get() == "could not open A: no document window"


def test_a_tab_that_cannot_switch_master_is_reported_too(parity):
    class Stubborn:
        text = None

        @property
        def masterIndex(self):
            return 0

        @masterIndex.setter
        def masterIndex(self, value):
            raise AttributeError("read-only")

    p = parity(Font(newTab=lambda text: Stubborn()))
    lst = p._panel.list
    lst.table.clicked = 0
    p._openGlyph(lst)
    assert p._panel.note.get() == "opened A, but could not switch master: read-only"


def test_a_click_on_nothing_says_so(parity):
    font = Font()
    p = parity(font)
    p._openGlyph(p._panel.list)    # clickedRow -1, no selection
    assert font.tabs == []
    assert p._panel.note.get() == "double-click a row to open its glyph"


def test_a_rebuilt_panel_is_filled_even_when_the_rows_did_not_change(parity):
    p = parity(Font())
    assert glyph_names(p) == ["A", "B"]
    p._panel = panel()             # the red X drops the window; this is the new one
    p._scan()
    assert glyph_names(p) == ["A", "B"]


def test_switching_the_tool_on_fills_the_list_at_once(parity):
    p = parity(Font())
    p._panel = panel()
    p._showPanel = lambda: None    # the panel is already there
    p.willActivate()
    assert p._active is True
    assert glyph_names(p) == ["A", "B"]


def test_a_changed_scan_keeps_the_selected_row_selected(parity):
    font = Font()
    p = parity(font)
    lst = p._panel.list
    lst.selection = [1]            # B
    font.glyphs["A"].layers[font.masters[2].id].width = 175   # A now agrees
    font.glyphs["A"].layers[font.masters[2].id].RSB = 40
    p._scan()
    assert glyph_names(p) == ["B"]
    assert lst.getSelection() == [0]


# --- what is measured ---------------------------------------------------------------


def two_masters(layers, tags=("XTRA", "XOPQ", "YOPQ"), names=None):
    font = Font()
    font.axes = [Axis(n, t) for n, t in zip(names or tags, tags)]
    font.masters = [Master("47-1-1", [47, 1, 1]), Master("47-1-275", [47, 1, 275])]
    a, b = (m.id for m in font.masters)
    font.glyphs = Glyphs_(Glyph(name, {a: la, b: lb}) for name, (la, lb) in layers.items())
    return font


def test_a_difference_just_over_a_unit_is_flagged(parity):
    """Glyphs shows sidebearings in whole units: 26 and 24.95 read 26 and
    25, a unit apart and so within tolerance."""
    p = parity(two_masters({"k": (Layer(152, 35, 26), Layer(152, 35, 25, ink=(35, 24.95)))}))
    assert glyph_names(p) == ["k"]
    row = p._panel.list.get()[0]
    assert (row["dadv"], row["dlsb"], row["drsb"]) == ("0", "0", "1.05")


def test_a_difference_of_a_unit_is_within_tolerance(parity):
    p = parity(two_masters({"k": (Layer(152, 35, 26), Layer(152, 35, 25))}))
    assert glyph_names(p) == []


def test_a_composite_is_measured_through_what_it_is_built_from(parity):
    colon = lambda: Layer(160, 0, 0, components=[Component("period"), Component("period", (1, 0, 0, 1, 0, 400))])
    p = parity(two_masters({"colon": (colon(), colon()),
                            "period": (Layer(160, 30, 30), Layer(160, 42, 30))}))
    rows = dict((r["glyph"], (r["dadv"], r["dlsb"], r["drsb"])) for r in p._panel.list.get())
    assert rows == {"colon": ("0", "12", "0"), "period": ("0", "12", "0")}


def test_sidebearings_are_taken_along_the_slant(parity):
    """An italic master measures its sidebearings along its angle, around
    half its x-height, as Glyphs does."""
    import math

    class Leaning(Layer):
        """A stem 80 wide, 60 from either side, leaning by ``angle``."""

        angle = 10.0

        @property
        def paths(self):
            lean = math.tan(math.radians(self.angle))
            return [Path_([(x + lean * (y - 250.0), y) for x, y in ((60, 0), (140, 0), (140, 700), (60, 700))])]

    font = two_masters({"l": (Leaning(200, 60, 60), Leaning(200, 60, 60))})
    font.masters[0].italicAngle = font.masters[1].italicAngle = 10.0
    assert glyph_names(parity(font)) == []
    # The same two outlines measured upright are 60 and 60 again, so it
    # takes masters that lean differently to tell the measures apart.
    steeper = Leaning(200, 60, 60)
    steeper.angle = 14.0
    font = two_masters({"l": (Leaning(200, 60, 60), steeper)})
    font.masters[0].italicAngle, font.masters[1].italicAngle = 10.0, 14.0
    assert glyph_names(parity(font)) == []


def test_an_empty_glyph_is_compared_by_its_advance(parity):
    empty = lambda w: Layer(w, 0, 0, components=[Component("nothing")])
    p = parity(two_masters({"space": (empty(200), empty(240))}))
    row = p._panel.list.get()[0]
    assert (row["glyph"], row["dadv"], row["dlsb"], row["drsb"]) == ("space", "40", "0", "0")


# --- the default grouping and the summary -------------------------------------------------


def test_the_default_grouping_is_found_by_tag(parity):
    """Crispy's axes are NAMED X-Transparency and X-Opacity; XTRA and
    XOPQ are their tags."""
    font = two_masters({"A": (Layer(175, 37, 40), Layer(180, 37, 45))},
                       tags=("wght", "XTRA", "XOPQ", "YOPQ"),
                       names=("Weight", "X-Transparency", "X-Opacity", "Y-Opacity"))
    font.masters = [Master("47-1-1", [400, 47, 1, 1]), Master("47-1-275", [400, 47, 1, 275])]
    a, b = (m.id for m in font.masters)
    font.glyphs = Glyphs_([Glyph("A", {a: Layer(175, 37, 40), b: Layer(180, 37, 45)})])
    p = parity(font)
    assert glyph_names(p) == ["A"]
    assert p._panel.list.get()[0]["group"] == "XTRA 47 · XOPQ 1"
    assert p._panel.summary.get().startswith("X-Transparency + X-Opacity")
    p._panel.pairPop = PopUp()
    p._syncPairsToFont(font)
    assert p._panel.pairPop.items[p._panel.pairPop.get()] == "X-Transparency + X-Opacity"


def test_the_summary_counts_glyphs(parity):
    font = Font()
    font.masters.append(Master("1715-1-275", [1715, 1, 275]))
    for g in font.glyphs:
        g.layers[font.masters[3].id] = Layer(950, 60, 50)
    p = parity(font)
    assert [r["glyph"] for r in p._panel.list.get()] == ["A", "B", "A", "B", "C"]
    assert p._panel.summary.get().endswith("3 glyphs flagged, in 5 rows")


def test_the_summary_when_every_glyph_has_one_row(parity):
    assert parity(Font())._panel.summary.get().endswith("2 glyphs flagged")
