"""Corner Radii's panel glue (``plugin.py``) with Glyphs, AppKit and
vanilla stood in for: which layers Apply and Sharpen reach, what they
refuse, what they tell the user, and that they leave masters compatible.

The fakes copy what Glyphs 3.5.1 does where the glue depends on it
(measured on the engine): a backup layer and a brace layer both carry
their master's id in ``associatedMasterId`` and an id of their own in
``layerId``; only ``isSpecialLayer`` tells them apart."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

RESOURCES = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
             / "CornerRadii.glyphsReporter" / "Contents" / "Resources")
K = 0.5522847498


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

    def deactivateReporter(self, reporter):
        pass


# --- the panel ----------------------------------------------------------------


class Widget:
    def __init__(self, posSize, title="", value=None, callback=None, **kwargs):
        self.posSize = posSize
        self.value = title if value is None else value
        self.callback = callback

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class PopUp(Widget):
    def __init__(self, posSize, items, callback=None, **kwargs):
        Widget.__init__(self, posSize, value=0, callback=callback)
        self.items = items


class Window:
    def __init__(self, size, title="", **kwargs):
        self.size = size

    def bind(self, event, callback):
        pass

    def open(self):
        pass

    def resize(self, width, height):
        self.size = (width, height)

    def getNSWindow(self):
        return _Anything()


# --- the font -----------------------------------------------------------------


class Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Node:
    """``position`` accepts a tuple and reads back with ``.x``/``.y``,
    like GSNode's."""

    def __init__(self, x, y, type_):
        self.position = (x, y)
        self.type = type_
        self.smooth = type_ == "curve"

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value if isinstance(value, Pt) else Pt(float(value[0]), float(value[1]))


class Path_:
    def __init__(self, nodes, closed=True):
        self.nodes = [Node(*n) for n in nodes]
        self.closed = closed

    def removeNode_(self, node):
        self.nodes.remove(node)


class Layer:
    def __init__(self, outlines, layerId, master=None, special=False, name=""):
        self.paths = [Path_(o) for o in outlines]
        self.layerId = layerId
        self.associatedMasterId = master or layerId
        self.isSpecialLayer = special
        self.name = name
        self.parent = None
        self.open_changes = 0

    def beginChanges(self):
        self.open_changes += 1

    def endChanges(self):
        self.open_changes -= 1


class Layers(list):
    """Glyphs' ``glyph.layers``: by master id, and one after the other."""

    def __getitem__(self, key):
        if isinstance(key, str):
            return next((layer for layer in self if layer.layerId == key), None)
        return list.__getitem__(self, key)


class Glyph:
    def __init__(self, name, font):
        self.name = name
        self.parent = font
        self.layers = Layers()

    def add(self, layer):
        layer.parent = self
        self.layers.append(layer)
        return layer


class Master:
    def __init__(self, number):
        self.id = "M%d" % number
        self.name = "Master %d" % number


class Glyphs_(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next((g for g in self if g.name == key), None)
        return list.__getitem__(self, key)


class Font:
    """``glyphs`` maps a name to a function that draws the glyph for the
    master of a given number (1, 2, ...)."""

    def __init__(self, masters=3, **glyphs):
        self.masters = [Master(i + 1) for i in range(masters)]
        self.selectedFontMaster = self.masters[0]
        self.glyphs = Glyphs_()
        for name, draw in glyphs.items():
            g = Glyph(name, self)
            for i, m in enumerate(self.masters):
                g.add(Layer(draw(i + 1), m.id))
            self.glyphs.append(g)


# --- outlines -------------------------------------------------------------------


def rrect(x0, y0, x1, y1, r):
    """Counter-clockwise from the bottom right, every corner rounded:
    rounds at nodes 0-3 (bottom right), 4-7, 8-11, 12-15 (bottom left)."""
    k = K * r
    return [
        (x1 - r, y0, "line"), (x1 - r + k, y0, "offcurve"), (x1, y0 + r - k, "offcurve"), (x1, y0 + r, "curve"),
        (x1, y1 - r, "line"), (x1, y1 - r + k, "offcurve"), (x1 - r + k, y1, "offcurve"), (x1 - r, y1, "curve"),
        (x0 + r, y1, "line"), (x0 + r - k, y1, "offcurve"), (x0, y1 - r + k, "offcurve"), (x0, y1 - r, "curve"),
        (x0, y0 + r, "line"), (x0, y0 + r - k, "offcurve"), (x0 + r - k, y0, "offcurve"), (x0 + r, y0, "curve"),
    ]


def two_piece(x0, y0, x1, y1, r):
    """A rectangle whose bottom right corner is rounded by two curve
    pieces of 45 degrees; the other corners are plain."""
    s = 0.7071067811865476 * r
    k = 0.2652164898395441 * r  # 4/3 tan(45 deg / 4)
    cx, cy = x1 - r, y0 + r
    return [
        (cx, y0, "line"), (cx + k, y0, "offcurve"),
        (cx + s - k * 0.7071067811865476, cy - s - k * 0.7071067811865476, "offcurve"), (cx + s, cy - s, "curve"),
        (cx + s + k * 0.7071067811865476, cy - s + k * 0.7071067811865476, "offcurve"),
        (x1, cy - k, "offcurve"), (x1, cy, "curve"),
        (x1, y1, "line"), (x0, y1, "line"), (x0, y0, "line"),
    ]


def pill(x0, y0, x1, y1):
    r = (y1 - y0) / 2.0
    k = K * r
    return [
        (x1 - r, y0, "line"), (x1 - r + k, y0, "offcurve"), (x1, y0 + r - k, "offcurve"), (x1, y0 + r, "curve"),
        (x1, y0 + r + k, "offcurve"), (x1 - r + k, y1, "offcurve"), (x1 - r, y1, "curve"),
        (x0 + r, y1, "line"), (x0 + r - k, y1, "offcurve"), (x0, y1 - r + k, "offcurve"), (x0, y0 + r, "curve"),
        (x0, y0 + r - k, "offcurve"), (x0 + r - k, y0, "offcurve"), (x0 + r, y0, "curve"),
    ]


def big(number):
    return [rrect(100, 0, 900, 1200, 100)]


def small(number):
    return [rrect(0, 0, 300, 300, 100)]


# --- the plugin -----------------------------------------------------------------


@pytest.fixture
def cornerradii(monkeypatch):
    """``plugin.py`` loaded against the stand-ins; returns a function
    that makes a CornerRadii with its panel on a font, looking at the
    first master of a glyph."""
    monkeypatch.setattr(sys, "path", list(sys.path))  # plugin.py prepends its folder
    glyphs = GlyphsStub()
    appkit = types.ModuleType("AppKit")
    for name in ("NSBezierPath", "NSColor"):
        setattr(appkit, name, _Anything())
    glyphsapp = types.ModuleType("GlyphsApp")
    glyphsapp.Glyphs, glyphsapp.__all__ = glyphs, ["Glyphs"]
    plugins = types.ModuleType("GlyphsApp.plugins")
    plugins.ReporterPlugin, plugins.__all__ = object, ["ReporterPlugin"]
    objc = types.ModuleType("objc")
    objc.python_method = lambda f: f
    vanilla = types.ModuleType("vanilla")
    for name in ("Button", "CheckBox", "EditText", "TextBox"):
        setattr(vanilla, name, Widget)
    vanilla.PopUpButton, vanilla.FloatingWindow = PopUp, Window
    for name, mod in (("objc", objc), ("AppKit", appkit), ("GlyphsApp", glyphsapp),
                      ("GlyphsApp.plugins", plugins), ("vanilla", vanilla)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "cornerfit", raising=False)
    spec = importlib.util.spec_from_file_location("cornerradii_plugin", RESOURCES / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def make(font, glyph=None, scope=0, outer=None, inner=None, baseline=False):
        glyphs.font = font
        p = module.CornerRadii()
        p.settings()
        p._build_panel()
        p.module = module
        p._lastLayer = (font.glyphs[glyph] if glyph else font.glyphs[0]).layers[font.masters[0].id]
        p._panel.scopePop.set(scope)
        p._scopeChanged(p._panel.scopePop)
        for field, callback, value in ((p._panel.outerField, p._outerFieldChanged, outer),
                                       (p._panel.innerField, p._innerFieldChanged, inner)):
            if value is not None:
                field.set(str(value))
                callback(field)
        p._panel.baselineBox.set(baseline)
        p._baselineChanged(p._panel.baselineBox)
        return p

    return make


CURRENT, ALL, THIS_MASTER, ENTIRE = 0, 1, 2, 3


def coords(layer):
    return [[(n.position.x, n.position.y) for n in path.nodes] for path in layer.paths]


def kinds(layer):
    return [[n.type for n in path.nodes] for path in layer.paths]


def radii(p, layer):
    return [round(c["radius"]) for c in p.module.cornerfit.find_corners(layer.paths)]


def status(p):
    return p._panel.status1.get() + " | " + p._panel.status2.get()


def master_layers(font, name):
    return [font.glyphs[name].layers[m.id] for m in font.masters]


# --- the panel ------------------------------------------------------------------


def test_the_panel_says_what_apply_did(cornerradii):
    font = Font(big=big)
    p = cornerradii(font, outer=1.5)
    assert status(p) == " | "
    p._apply(None)
    assert [radii(p, layer) for layer in master_layers(font, "big")] == [[150] * 4] * 3
    assert p._panel.status1.get() == "Scaled 12 rounds in 1 glyph"
    assert p._panel.status2.get() == ""
    assert all(layer.open_changes == 0 for layer in master_layers(font, "big"))


def test_the_status_sits_above_the_master_list(cornerradii):
    p = cornerradii(Font(big=big))
    w = p._panel
    buttons, masters = w.applyButton.posSize[1], w.mastersLabel.posSize[1]
    assert buttons < w.status1.posSize[1] < w.status2.posSize[1] < masters
    assert w.status2.posSize[1] + w.status2.posSize[3] <= masters
    assert w.size[1] > p._masterRows["M3"].posSize[1] + 20


def test_the_panel_says_when_there_was_nothing_to_do(cornerradii):
    font = Font(plain=lambda number: [[(300, 0, "line"), (300, 300, "line"), (0, 300, "line"), (0, 0, "line")]])
    p = cornerradii(font, outer=1.5)
    p._apply(None)
    assert p._panel.status1.get() == "No rounds found"
    p._sharpen(None)
    assert p._panel.status1.get() == "No rounds found"


# --- C1: rounds in more than one piece -----------------------------------------


def test_apply_scales_a_two_piece_round_and_keeps_the_nodes(cornerradii):
    font = Font(two=lambda number: [two_piece(100, 0, 900, 1200, 100)])
    before = [kinds(layer) for layer in master_layers(font, "two")]
    p = cornerradii(font, outer=1.5)
    p._apply(None)
    assert [kinds(layer) for layer in master_layers(font, "two")] == before
    assert [radii(p, layer) for layer in master_layers(font, "two")] == [[150]] * 3
    first = master_layers(font, "two")[0].paths[0].nodes
    assert (first[0].position.x, first[0].position.y) == pytest.approx((750.0, 0.0))
    assert (first[6].position.x, first[6].position.y) == pytest.approx((900.0, 150.0))
    assert p._panel.status1.get() == "Scaled 3 rounds in 1 glyph"


def test_sharpen_takes_a_two_piece_round_in_one(cornerradii):
    font = Font(two=lambda number: [two_piece(100, 0, 900, 1200, 100)])
    p = cornerradii(font)
    p._sharpen(None)
    for layer in master_layers(font, "two"):
        assert kinds(layer) == [["line"] * 4]
        assert coords(layer) == [[(pytest.approx(900.0), pytest.approx(0.0)), (900, 1200), (100, 1200), (100, 0)]]
        assert layer.paths[0].nodes[0].smooth is False
    assert p._panel.status1.get() == "Sharpened 3 rounds in 1 glyph"


def test_a_half_circle_end_is_left_alone(cornerradii):
    font = Font(pill=lambda number: [pill(0, 0, 900, 300)])
    before = [coords(layer) for layer in master_layers(font, "pill")]
    p = cornerradii(font, outer=1.5)
    p._apply(None)
    p._sharpen(None)
    assert [coords(layer) for layer in master_layers(font, "pill")] == before
    assert p._panel.status1.get() == "No rounds found"


# --- C2: backup layers ------------------------------------------------------------


def with_extra_layers(font, name):
    g = font.glyphs[name]
    first = font.masters[0].id
    backup = g.add(Layer(big(1), "BACKUP-1", master=first, special=False, name="Sep 29, 26 at 12:00"))
    brace = g.add(Layer(big(1), "BRACE-1", master=first, special=True, name="{47, 700, 1}"))
    return backup, brace


@pytest.mark.parametrize("scope", [ALL, THIS_MASTER, ENTIRE])
def test_font_wide_apply_never_rewrites_a_backup_layer(cornerradii, scope):
    font = Font(big=big)
    backup, brace = with_extra_layers(font, "big")
    kept = coords(backup)
    p = cornerradii(font, scope=scope, outer=1.5)
    p._apply(None)
    assert coords(backup) == kept
    assert backup.open_changes == 0
    assert radii(p, brace) == [150] * 4, "a brace layer goes with its master"
    assert radii(p, font.glyphs["big"].layers["M1"]) == [150] * 4


@pytest.mark.parametrize("scope", [ALL, THIS_MASTER, ENTIRE])
def test_font_wide_sharpen_never_rewrites_a_backup_layer(cornerradii, scope):
    font = Font(big=big)
    backup, brace = with_extra_layers(font, "big")
    kept = coords(backup)
    p = cornerradii(font, scope=scope)
    p._sharpen(None)
    assert coords(backup) == kept and kinds(backup) == [["line", "offcurve", "offcurve", "curve"] * 4]
    assert kinds(brace) == [["line"] * 4]
    assert kinds(font.glyphs["big"].layers["M1"]) == [["line"] * 4]


def test_current_glyph_stays_with_the_master_layers(cornerradii):
    font = Font(big=big)
    backup, brace = with_extra_layers(font, "big")
    kept = coords(backup), coords(brace)
    p = cornerradii(font, scope=CURRENT, outer=1.5)
    p._apply(None)
    assert (coords(backup), coords(brace)) == kept
    assert [radii(p, layer) for layer in master_layers(font, "big")] == [[150] * 4] * 3


def test_a_layer_glyphs_answers_with_a_method_is_read_too(cornerradii):
    """Some bridges hand back the selector instead of its value."""
    font = Font(big=big)
    backup, brace = with_extra_layers(font, "big")
    backup.isSpecialLayer = lambda: False
    brace.isSpecialLayer = lambda: True
    kept = coords(backup)
    p = cornerradii(font, scope=ALL, outer=1.5)
    p._apply(None)
    assert coords(backup) == kept
    assert radii(p, brace) == [150] * 4


# --- C3: rounds that outgrow their side ---------------------------------------------


def runs_forward(layer):
    """Every straight of a rounded rectangle still runs the way it was
    drawn: counter-clockwise from the bottom right."""
    n = layer.paths[0].nodes
    return (n[15].position.x < n[0].position.x and n[3].position.y < n[4].position.y
            and n[7].position.x > n[8].position.x and n[11].position.y > n[12].position.y)


def test_apply_refuses_a_glyph_whose_rounds_would_cross(cornerradii):
    font = Font(big=big, small=small)
    before = [coords(layer) for layer in master_layers(font, "small")]
    p = cornerradii(font, scope=ALL, outer=2.0)
    p._apply(None)
    assert [coords(layer) for layer in master_layers(font, "small")] == before
    assert all(runs_forward(layer) for layer in master_layers(font, "small"))
    assert [radii(p, layer) for layer in master_layers(font, "big")] == [[200] * 4] * 3
    assert p._panel.status1.get() == "Scaled 12 rounds in 1 glyph"
    assert p._panel.status2.get() == "Not scaled, a round would outgrow the straight it sits on: small"


def test_a_glyph_short_of_room_in_one_master_is_refused_in_all(cornerradii):
    """Scaled in two masters out of three it would have one radius here
    and another there, with nothing to say so."""
    font = Font(tight=lambda number: [rrect(0, 0, 300 if number == 2 else 900, 1200, 100)])
    before = [coords(layer) for layer in master_layers(font, "tight")]
    p = cornerradii(font, outer=2.0)
    p._apply(None)
    assert [coords(layer) for layer in master_layers(font, "tight")] == before
    assert p._panel.status1.get() == "No rounds scaled"
    assert "outgrow the straight it sits on: tight" in p._panel.status2.get()


def test_the_master_short_of_room_does_not_count_when_it_is_unticked(cornerradii):
    font = Font(tight=lambda number: [rrect(0, 0, 300 if number == 2 else 900, 1200, 100)])
    p = cornerradii(font, outer=2.0)
    box = p._masterRows["M2"]
    box.set(False)
    p._masterToggled(box)
    p._apply(None)
    assert [radii(p, layer) for layer in master_layers(font, "tight")] == [[200] * 4, [100] * 4, [200] * 4]
    assert status(p) == "Scaled 8 rounds in 1 glyph | "


def test_outer_and_inner_rounds_are_measured_with_their_own_factor(cornerradii):
    font = Font(o=lambda number: [rrect(0, 0, 1000, 1200, 150), list(reversed_outline(rrect(250, 300, 750, 900, 60)))])
    p = cornerradii(font, outer=1.5, inner=4.5)   # the counter is 500 wide: 60 x 4.5 = 270, twice over
    before = [coords(layer) for layer in master_layers(font, "o")]
    p._apply(None)
    assert [coords(layer) for layer in master_layers(font, "o")] == before
    assert "outgrow the straight it sits on: o" in p._panel.status2.get()
    p = cornerradii(font, outer=1.5, inner=4.0)
    p._apply(None)
    assert [radii(p, layer) for layer in master_layers(font, "o")] == [[225] * 4 + [240] * 4] * 3


def reversed_outline(nodes):
    """The same closed outline drawn the other way round — a counter."""
    m = len(nodes)
    for j in range(m - 1, -1, -1):
        x, y, kind = nodes[j]
        if kind == "offcurve":
            yield (x, y, "offcurve")
        else:
            yield (x, y, "curve" if nodes[(j + 1) % m][2] == "offcurve" else "line")


def test_many_refused_glyphs_are_named_up_to_a_point(cornerradii, capsys):
    font = Font(**{"g%02d" % i: small for i in range(12)})
    p = cornerradii(font, scope=ALL, outer=2.0)
    p._apply(None)
    assert p._panel.status2.get().endswith("sits on: g00, g01, g02, g03, g04, g05, g06, g07…")
    assert "g11" in capsys.readouterr().out, "the Macro window has them all"


# --- C4: the same rounds in every master --------------------------------------------


def base(number):
    """Bottom corners on the baseline, but 12 below it in master 2."""
    return [rrect(100, -12 if number == 2 else 0, 900, 1200, 100)]


def test_sharpen_baseline_only_leaves_the_masters_compatible(cornerradii):
    font = Font(base=base)
    p = cornerradii(font, baseline=True)
    p._sharpen(None)
    layers = master_layers(font, "base")
    assert kinds(layers[0]) == kinds(layers[1]) == kinds(layers[2])
    assert [len(layer.paths[0].nodes) for layer in layers] == [16, 16, 16]
    assert p._panel.status1.get() == "No rounds sharpened"
    assert p._panel.status2.get() == "Rounds left alone, on the baseline in some masters only: base"


def test_sharpen_baseline_only_takes_what_every_master_has_on_the_baseline(cornerradii):
    font = Font(base=lambda number: [rrect(100, 0, 900, 1200, 60 + 20 * number)])
    p = cornerradii(font, baseline=True)
    p._sharpen(None)
    for layer in master_layers(font, "base"):
        assert kinds(layer) == [["line"] + ["line", "offcurve", "offcurve", "curve"] * 2 + ["line"]]
        assert coords(layer)[0][0] == (900, 0) and coords(layer)[0][-1] == (100, 0)
    assert status(p) == "Sharpened 6 rounds in 1 glyph | "


def test_sharpen_without_the_filter_takes_every_round_in_every_master(cornerradii):
    font = Font(base=base)
    p = cornerradii(font)
    p._sharpen(None)
    assert [kinds(layer) for layer in master_layers(font, "base")] == [[["line"] * 4]] * 3
    assert status(p) == "Sharpened 12 rounds in 1 glyph | "


def test_the_master_that_disagrees_does_not_count_when_it_is_unticked(cornerradii):
    font = Font(base=base)
    p = cornerradii(font, baseline=True)
    box = p._masterRows["M2"]
    box.set(False)
    p._masterToggled(box)
    p._sharpen(None)
    assert [len(layer.paths[0].nodes) for layer in master_layers(font, "base")] == [10, 16, 10]
    assert status(p) == "Sharpened 4 rounds in 1 glyph | "


def test_apply_baseline_only_scales_the_same_rounds_in_every_master(cornerradii):
    font = Font(base=base)
    before = [coords(layer) for layer in master_layers(font, "base")]
    p = cornerradii(font, baseline=True, outer=1.5)
    p._apply(None)
    assert [coords(layer) for layer in master_layers(font, "base")] == before
    assert status(p) == "No rounds scaled | Rounds left alone, on the baseline in some masters only: base"


def test_sharpen_leaves_alone_a_round_one_master_does_not_have(cornerradii):
    """In master 2 the straight up the right side has no length: nothing
    says where the corners of the two rounds on it are. Sharpened in the
    other masters only, they would leave master 2 with six nodes more."""
    def draw(number):
        outline = rrect(100, 0, 900, 1200, 100)
        if number == 2:
            outline[4] = (900, 100, "line")
        return [outline]

    font = Font(met=draw)
    p = cornerradii(font)
    p._sharpen(None)
    layers = master_layers(font, "met")
    assert kinds(layers[0]) == kinds(layers[1]) == kinds(layers[2])
    assert [len(layer.paths[0].nodes) for layer in layers] == [10, 10, 10]
    assert p._panel.status1.get() == "Sharpened 6 rounds in 1 glyph"
    assert p._panel.status2.get() == "Rounds left alone, not found in every master: met"


def test_a_layer_with_a_structure_of_its_own_is_decided_on_its_own(cornerradii):
    """A bracket layer holds another drawing of the glyph; its rounds are
    not the masters' rounds, and it has nothing to agree with."""
    font = Font(big=big)
    bracket = font.glyphs["big"].add(
        Layer([two_piece(100, 0, 900, 1200, 100)], "BRACKET-1", master="M1", special=True, name="[700]"))
    p = cornerradii(font, scope=ALL)
    p._sharpen(None)
    assert [kinds(layer) for layer in master_layers(font, "big")] == [[["line"] * 4]] * 3
    assert kinds(bracket) == [["line"] * 4]
    assert status(p) == "Sharpened 13 rounds in 1 glyph | "
