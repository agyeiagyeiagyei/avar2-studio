"""Instance Delta's panel glue (``plugin.py``) with Glyphs, AppKit and
the panel stood in for: the Compare list, what a pick compares against,
and what the kept interpolation is good for.

The fakes copy what Glyphs 3.5 and the panel do where the glue depends
on it: a popup keeps ONE row per title (a title added again takes the
place of the first); an instance interpolates to a one-master font, and
at the axis values it has at that moment."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
          / "InstanceDelta.glyphsReporter" / "Contents" / "Resources" / "plugin.py")
LOG = "instancedelta-debug.log"


class _Anything:
    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()


class GlyphsStub:
    font = None
    defaults = {}

    def localize(self, names):
        return names["en"]

    def redraw(self):
        pass

    def deactivateReporter(self, reporter):
        pass


class Layer:
    def __init__(self, width, master):
        self.width, self.master = width, master
        self.parent = None


class Layers(dict):
    def __missing__(self, key):
        return None

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


class Glyph:
    def __init__(self, name, layers):
        self.name = name
        self.layers = Layers(layers)
        for layer in self.layers.values():
            layer.parent = self


class Glyphs_(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next((g for g in self if g.name == key), None)
        return list.__getitem__(self, key)


class Master:
    ascender, descender = 2000.0, -400.0

    def __init__(self, name):
        self.name, self.id = name, "id-%s-%d" % (name, id(self))


class Instance:
    """Interpolates to a one-master font whose every advance is the
    instance's first axis value; counts how often it is asked."""

    def __init__(self, name, axes, glyphNames=("A", "B", "C")):
        self.name, self.axes = name, list(axes)
        self.interpolations = 0
        self._glyphNames = glyphNames

    @property
    def interpolatedFont(self):
        self.interpolations += 1
        master = Master(self.name)
        return types.SimpleNamespace(
            masters=[master],
            glyphs=Glyphs_(Glyph(n, {master.id: Layer(float(self.axes[0]), master)})
                           for n in self._glyphNames))


class Font:
    """Two masters (advances 500 and 900) and the instances asked for."""

    def __init__(self, instances=(("Regular", (400, 400, 100)),), masters=("Light", "Bold")):
        self.masters = [Master(n) for n in masters]
        self.glyphs = Glyphs_(
            Glyph(n, {m.id: Layer(500.0 + 400.0 * i, m) for i, m in enumerate(self.masters)})
            for n in ("A", "B", "C"))
        self.instances = [Instance(name, axes) for name, axes in instances]
        self.selectedLayers = [self.glyphs["A"].layers[self.masters[0].id]]


class Widget:
    def __init__(self, posSize, title="", value=None, callback=None, **kwargs):
        self.value, self.callback = value, callback

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def getNSView(self):
        return _Anything()


class TextBox(Widget):
    def __init__(self, posSize, text="", **kwargs):
        Widget.__init__(self, posSize, value=text)


class PopUpButton(Widget):
    """One row per title, as NSPopUpButton keeps them: a title added
    again is taken out where it was and put at the end."""

    def __init__(self, posSize, items, callback=None, **kwargs):
        Widget.__init__(self, posSize, value=0, callback=callback)
        self.items = []
        for title in items:
            if title in self.items:
                self.items.remove(title)
            self.items.append(title)

    def getItems(self):
        return list(self.items)


class FloatingWindow:
    def __init__(self, posSize, title="", **kwargs):
        self.size = posSize

    def bind(self, event, callback):
        pass

    def open(self):
        pass

    def resize(self, width, height):
        self.size = (width, height)

    def getNSWindow(self):
        return _Anything()


@pytest.fixture
def instancedelta(monkeypatch, tmp_path):
    """``plugin.py`` loaded against the stand-ins; returns a function
    that makes an InstanceDelta with its panel built on a font. Whatever
    the plugin logs goes to ``tmp_path``."""
    glyphs = GlyphsStub()
    appkit = types.ModuleType("AppKit")
    appkit.NSBezierPath, appkit.NSColor = _Anything(), _Anything()
    glyphsapp = types.ModuleType("GlyphsApp")
    glyphsapp.Glyphs, glyphsapp.__all__ = glyphs, ["Glyphs"]
    plugins = types.ModuleType("GlyphsApp.plugins")
    plugins.ReporterPlugin, plugins.__all__ = object, ["ReporterPlugin"]
    objc = types.ModuleType("objc")
    objc.python_method = lambda f: f
    vanilla = types.ModuleType("vanilla")
    vanilla.Button = vanilla.CheckBox = Widget
    vanilla.FloatingWindow, vanilla.PopUpButton, vanilla.TextBox = FloatingWindow, PopUpButton, TextBox
    for name, mod in (("objc", objc), ("AppKit", appkit), ("GlyphsApp", glyphsapp),
                      ("GlyphsApp.plugins", plugins), ("vanilla", vanilla)):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("instancedelta_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._DEBUG_LOG = str(tmp_path / LOG)

    def make(font):
        glyphs.font = font
        p = module.InstanceDelta()
        p.settings()
        p.start()
        p.module = module
        p.willActivate()
        p._pulse()
        return p

    return make


def rows(p):
    return p._panel.instPop.getItems()


def pick(p, title, by_title=False):
    """Choose a row of the Compare popup, as a click does. The popup
    hands back the row's index in some vanilla builds and its title in
    others."""
    index = rows(p).index(title)
    p._panel.instPop.set(index)
    p._pickChanged(types.SimpleNamespace(get=lambda: title) if by_title else p._panel.instPop)


def compared(p, font):
    """What the overlay is drawn from: the chosen master or instance,
    and the advance the overlay shows for A."""
    return p._selectedEntry(font)["obj"], p._overlayLayer(font, "A").width


def draw(p, font, names=("A", "B", "C")):
    for name in names:
        p.background(font.glyphs[name].layers[font.masters[0].id])


SHARED = (("Def", (400, 400, 100)), ("Bold", (47, 1462, 1)), ("Def", (1715, 1, 1)))


# --- what the panel did before, and still does -------------------------------


def test_the_list_has_masters_then_instances(instancedelta):
    font = Font(instances=(("Regular", (400, 400, 100)), ("Width Matcher Preview", (1, 1, 1)),
                           ("Black", (47, 1462, 1))))
    p = instancedelta(font)
    assert rows(p) == ["Master: Light", "Master: Bold", "Instance: Regular", "Instance: Black"]
    assert compared(p, font) == (font.masters[0], 500.0), "the first row until something is picked"


def test_a_master_is_read_off_the_glyph(instancedelta):
    font = Font()
    p = instancedelta(font)
    pick(p, "Master: Bold")
    assert p._overlayLayer(font, "A") is font.glyphs["A"].layers[font.masters[1].id]
    assert font.instances[0].interpolations == 0


def test_an_instance_is_interpolated_once_for_all_the_draws(instancedelta):
    font = Font()
    p = instancedelta(font)
    pick(p, "Instance: Regular")
    for _ in range(10):
        draw(p, font)
    assert font.instances[0].interpolations == 1
    assert compared(p, font) == (font.instances[0], 400.0)
    assert p._panel.readoutAdv.get() == "Adv edit 500 - inst 400 (delta -100)"


def test_refresh_interpolates_again(instancedelta):
    font = Font()
    p = instancedelta(font)
    pick(p, "Instance: Regular")
    draw(p, font)
    p._refreshClicked(None)
    draw(p, font)
    assert font.instances[0].interpolations == 2


# --- I1: entries that share a name -------------------------------------------


def test_instances_that_share_a_name_each_have_a_row(instancedelta):
    font = Font(instances=SHARED)
    p = instancedelta(font)
    assert rows(p)[2:] == ["Instance: Def", "Instance: Bold", "Instance: Def (2)"]
    assert [e["obj"] for e in p._entries[2:]] == font.instances, "a row for every entry, in its order"


@pytest.mark.parametrize("by_title", [False, True])
def test_a_pick_compares_against_the_row_that_was_picked(instancedelta, by_title):
    font = Font(instances=SHARED)
    p = instancedelta(font)
    for title, instance in zip(("Instance: Def", "Instance: Bold", "Instance: Def (2)"), font.instances):
        pick(p, title, by_title)
        assert compared(p, font) == (instance, float(instance.axes[0])), title
        draw(p, font, ["A"])
        assert p._panel.readout.get() == "A — %s" % title


def test_masters_that_share_a_name_each_have_a_row(instancedelta):
    font = Font(masters=("Light", "Light"))
    p = instancedelta(font)
    assert rows(p)[:2] == ["Master: Light", "Master: Light (2)"]
    pick(p, "Master: Light (2)")
    assert compared(p, font) == (font.masters[1], 900.0)


def test_a_name_that_looks_like_a_numbered_row_gets_a_row_too(instancedelta):
    font = Font(instances=(("Def", (1, 1, 1)), ("Def", (2, 1, 1)), ("Def (2)", (3, 1, 1))))
    p = instancedelta(font)
    assert len(set(rows(p))) == len(p._entries) == 5
    for title, instance in zip(rows(p)[2:], font.instances):
        pick(p, title)
        assert compared(p, font) == (instance, float(instance.axes[0])), title


def test_the_pick_stays_with_its_instance_when_the_list_changes(instancedelta):
    font = Font(instances=SHARED)
    p = instancedelta(font)
    pick(p, "Instance: Def (2)")
    second_def = font.instances[2]
    del font.instances[0]            # the other Def goes: the list is rebuilt at the next draw
    draw(p, font)
    assert rows(p)[2:] == ["Instance: Bold", "Instance: Def"]
    assert compared(p, font) == (second_def, 1715.0)
    assert p._panel.instPop.get() == 3, "the popup shows the row of the pick"


def test_a_pick_that_left_the_font_falls_back_to_the_first_row(instancedelta):
    font = Font(instances=SHARED)
    p = instancedelta(font)
    pick(p, "Instance: Bold")
    del font.instances[1]
    draw(p, font)
    assert compared(p, font) == (font.masters[0], 500.0)


# --- I2: what the kept interpolation is good for -----------------------------


def test_a_moved_instance_is_interpolated_afresh(instancedelta):
    font = Font()
    instance = font.instances[0]
    p = instancedelta(font)
    pick(p, "Instance: Regular")
    draw(p, font)
    assert compared(p, font) == (instance, 400.0)
    instance.axes = [1500.0, 800.0, 200.0]   # moved in Font Info: same name, same count
    draw(p, font)
    assert compared(p, font) == (instance, 1500.0)
    assert instance.interpolations == 2
    for _ in range(10):
        draw(p, font)
    assert instance.interpolations == 2, "once for the new place, not once a draw"
    assert p._panel.readoutAdv.get() == "Adv edit 500 - inst 1500 (delta +1000)"


def test_an_instance_moved_back_is_interpolated_again(instancedelta):
    font = Font()
    instance = font.instances[0]
    p = instancedelta(font)
    pick(p, "Instance: Regular")
    draw(p, font)
    instance.axes = [1500, 800, 200]
    draw(p, font)
    instance.axes = [400, 400, 100]
    draw(p, font)
    assert compared(p, font) == (instance, 400.0)
    assert instance.interpolations == 3


def test_same_named_instances_do_not_share_an_interpolation(instancedelta):
    font = Font(instances=SHARED)
    p = instancedelta(font)
    first, _, second = font.instances
    p._interpolated(p._entries[2])
    p._interpolated(p._entries[4])   # without a pick in between, which would drop what is kept
    assert (first.interpolations, second.interpolations) == (1, 1)
    assert p._interpFont.masters[0].name == "Def" and p._interpFor is second


# --- I3: the debug log --------------------------------------------------------


def test_it_ships_with_the_debug_log_off(instancedelta, tmp_path):
    font = Font()
    p = instancedelta(font)
    pick(p, "Instance: Regular")
    draw(p, font)
    assert p.module.DEBUG is False
    assert not (tmp_path / LOG).exists(), (tmp_path / LOG).read_text()
