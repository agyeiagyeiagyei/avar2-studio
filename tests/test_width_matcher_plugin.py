"""Width Matcher's panel and Save as Master (``plugin.py``) with Glyphs,
AppKit and vanilla stood in for.

The fakes copy what Glyphs 3.5 does where the plugin depends on it:
appending a master gives every glyph an EMPTY layer for it; a layer no
glyph holds reports empty ``bounds``; ``bounds`` and the sidebearings of
a composite are read through the layers its components point at, as they
stand at that moment; an interpolated instance sits between grid points;
a popup cannot hold two rows with one title.

The test font is ordered the way the trouble starts: composites come
BEFORE the glyphs they are built from.
"""

from __future__ import annotations

import copy
import importlib.util
import itertools
import os
import sys
import types
from pathlib import Path

import pytest

RESOURCES = Path(os.environ.get("WIDTH_MATCHER_RESOURCES") or (
    Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
    / "WidthMatcher.glyphsReporter" / "Contents" / "Resources"))
_ids = itertools.count(1)
SCRATCH = "Width Matcher Preview"


# --- Glyphs ---------------------------------------------------------------------


class Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Placed:
    def __init__(self, x, y, type_="line", name=None):
        self.position = (x, y)
        self.type, self.name, self.smooth = type_, name, False

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value if isinstance(value, Pt) else Pt(float(value[0]), float(value[1]))


class Path_:
    def __init__(self, points, closed=True):
        self.nodes = [Placed(*p) for p in points]
        self.closed = closed


class Component:
    def __init__(self, name, transform=(1, 0, 0, 1, 0, 0)):
        self.componentName = self.name = name
        self.transform = tuple(float(v) for v in transform)

    @property
    def position(self):
        return Pt(self.transform[4], self.transform[5])


def _apply(t, x, y):
    return (t[0] * x + t[2] * y + t[4], t[1] * x + t[3] * y + t[5])


class Layer:
    def __init__(self, paths=(), components=(), anchors=(), width=600.0, layerId=None):
        self.paths, self.components, self.anchors = list(paths), list(components), list(anchors)
        self.width = float(width)
        self.layerId = self.associatedMasterId = layerId
        self.parent = None
        self.fails = None  # "copy": cannot be copied

    def _clone(self):
        return Layer(copy.deepcopy(self.paths), copy.deepcopy(self.components), copy.deepcopy(self.anchors),
                     self.width, self.layerId)

    def copy(self):
        if self.fails == "copy":
            raise RuntimeError("layer could not be copied")
        return self._clone()

    __copy__ = copy

    def applyTransform(self, m):
        for pt in [n for p in self.paths for n in p.nodes] + self.anchors:
            pt.position = _apply(m, pt.position.x, pt.position.y)
        for c in self.components:
            t = c.transform
            c.transform = t[:4] + (t[4] + m[4], t[5] + m[5])

    # What Glyphs answers when asked, as opposed to what the outline is.
    def _points(self, depth=0):
        pts = [(n.position.x, n.position.y) for p in self.paths for n in p.nodes]
        font = self.parent.font if self.parent is not None else None
        if font is None or depth > 6:
            return pts
        for c in self.components:
            base = font.glyphs[c.componentName]
            held = base.layers[self.layerId] if base is not None else None
            if held is not None:
                pts += [_apply(c.transform, x, y) for x, y in held._points(depth + 1)]
        return pts

    @property
    def bounds(self):
        pts = self._points() if self.parent is not None else []
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x, y = (min(xs), min(ys)) if pts else (0.0, 0.0)
        return types.SimpleNamespace(origin=Pt(x, y), size=types.SimpleNamespace(
            width=max(xs) - x if pts else 0.0, height=max(ys) - y if pts else 0.0))

    @property
    def LSB(self):
        pts = self._points()
        return float(round(min(p[0] for p in pts))) if pts else 0.0

    @LSB.setter
    def LSB(self, value):
        shift = round(value - self.LSB)
        self.applyTransform((1, 0, 0, 1, shift, 0))
        self.width += shift

    @property
    def RSB(self):
        pts = self._points()
        return float(round(self.width - max(p[0] for p in pts))) if pts else 0.0

    @RSB.setter
    def RSB(self, value):
        self.width = max(p[0] for p in self._points()) + round(value)


class Layers(dict):
    def __init__(self, glyph):
        dict.__init__(self)
        self.glyph = glyph

    def __missing__(self, key):
        return None

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)

    def __setitem__(self, key, layer):
        layer.parent = self.glyph
        dict.__setitem__(self, key, layer)


class Glyph:
    def __init__(self, name, font):
        self.name, self.font, self.lastChange = name, font, 0
        self.leftMetricsKey = self.rightMetricsKey = self.widthMetricsKey = None
        self.layers = Layers(self)

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
    ascender, descender, xHeight, capHeight, italicAngle = 800.0, -200.0, 500.0, 700.0, 0.0

    def __init__(self, name, axes):
        self.name, self.axes = name, list(axes)
        self.id = "M%d" % next(_ids)
        self.userData = {}

    def copy(self):
        return copy.copy(self)  # the id included, as in Glyphs


class Masters(list):
    def __init__(self, font):
        list.__init__(self)
        self.font = font

    def append(self, master):
        master.id = "M%d" % next(_ids)
        list.append(self, master)
        for g in self.font.glyphs:
            g.layers[master.id] = Layer(layerId=master.id)


class Instance:
    """Interpolates the font's first two masters along the first axis."""

    font = None

    def __init__(self):
        self.name, self.active, self.axes = "", True, []
        self.interpolations = 0

    @property
    def interpolatedFont(self):
        self.interpolations += 1
        font = self.font
        a, b = font.masters[0], font.masters[1]
        t = (self.axes[0] - a.axes[0]) / float(b.axes[0] - a.axes[0])
        out = Font.__new__(Font)
        out.gridLength = font.gridLength
        master = Master(self.name, list(self.axes))
        out.glyphs = Glyphs_()
        for g in font.glyphs:
            A, B = g.layers[a.id], g.layers[b.id]
            mix = lambda u, v: u + (v - u) * t
            if g.unavailable:
                continue
            L = A._clone()
            for pa, pb in zip(L.paths, B.paths):
                for na, nb in zip(pa.nodes, pb.nodes):
                    na.position = (mix(na.position.x, nb.position.x), mix(na.position.y, nb.position.y))
            for ca, cb in zip(L.components, B.components):
                ca.transform = tuple(mix(u, v) for u, v in zip(ca.transform, cb.transform))
            for xa, xb in zip(L.anchors, B.anchors):
                xa.position = (mix(xa.position.x, xb.position.x), mix(xa.position.y, xb.position.y))
            L.width = mix(A.width, B.width)
            L.layerId = L.associatedMasterId = master.id
            L.fails = A.fails
            ig = Glyph(g.name, out)
            ig.layers[master.id] = L
            out.glyphs.append(ig)
        out.masters = [master]
        return out


class Axis:
    def __init__(self, name, tag):
        self.name, self.axisTag = name, tag


def box(x0, x1, y0=0.0, y1=700.0):
    return Path_([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


#: name -> (paths, components, anchors, advance), each a function of the
#: master's scale. Composites first, their bases after them.
DRAWN = [
    ("M", lambda s: ([], [Component("M.002")], [Placed(400 * s, 700, name="top")], 800 * s)),
    ("comma", lambda s: ([], [Component("quoteright", (1, 0, 0, 1, -10 * s, -600))], [], 140 * s)),
    ("colon", lambda s: ([], [Component("period"), Component("period", (1, 0, 0, 1, 0, 400))], [], 160 * s)),
    ("Q", lambda s: ([box(300 * s, 380 * s, -150, 80)], [Component("O")], [Placed(310 * s, 700, name="top")], 620 * s)),
    ("V", lambda s: ([], [Component("N", (-1, 0, 0, -1, 500 * s, 700))], [], 500 * s)),
    ("quotedblright", lambda s: ([], [Component("quotedblleft", (-1, 0, 0, -1, 200 * s, 900))], [], 200 * s)),
    ("quotedblleft", lambda s: ([], [Component("quoteleft"), Component("quoteleft", (1, 0, 0, 1, 80 * s, 0))], [], 200 * s)),
    ("H", lambda s: ([box(50 * s, 450 * s)], [], [Placed(250 * s, 700, name="top")], 500 * s)),
    ("N", lambda s: ([box(50 * s, 450 * s)], [], [], 500 * s)),
    ("O", lambda s: ([box(60 * s, 560 * s)], [], [], 620 * s)),
    ("period", lambda s: ([box(30 * s, 130 * s, 0, 100)], [], [], 160 * s)),
    ("quoteright", lambda s: ([box(40 * s, 110 * s, 600, 700)], [], [], 140 * s)),
    ("quoteleft", lambda s: ([box(10 * s, 60 * s, 600, 700)], [], [], 70 * s)),
    ("M.002", lambda s: ([box(60 * s, 700 * s)], [], [], 800 * s)),
    ("space", lambda s: ([], [], [], 200 * s)),
]


class Font:
    upm = 1000
    filepath = "/x/Test.glyphs"
    familyName = "Test"
    selectedLayers = ()
    gridLength = 1.0

    def __init__(self, names=("Narrow", "Wide", "Loose")):
        self.axes = [Axis("Width", "wdth")]
        self.instances = []
        self.glyphs = Glyphs_(Glyph(name, self) for name, _ in DRAWN)
        for g in self.glyphs:
            g.unavailable = False
        self.masters = Masters(self)
        # Narrow and Wide are what gets interpolated; Loose is a reference
        # with sidebearings of its own (everything sits 35 further right).
        for name, axis, scale, push in zip(names, (0.0, 100.0, 50.0), (1.0, 3.0, 2.0), (0.0, 0.0, 35.0)):
            master = Master(name, [axis])
            self.masters.append(master)
            for (gname, draw), g in zip(DRAWN, self.glyphs):
                paths, comps, anchors, width = draw(scale)
                L = Layer(paths, comps, anchors, width + (2 * push if paths or comps else 0), master.id)
                if push and paths:
                    for pt in [n for p in L.paths for n in p.nodes] + L.anchors:
                        pt.position = (pt.position.x + push, pt.position.y)
                g.layers[master.id] = L
        self.selectedFontMaster = self.masters[0]
        self.resumed = None  # what Glyphs does when the interface updates resume

    def disableUpdateInterface(self):
        pass

    def enableUpdateInterface(self):
        if self.resumed:
            self.resumed(self)


# --- vanilla and AppKit -----------------------------------------------------------


class _Anything:
    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()


class Control:
    def __init__(self, posSize, value="", callback=None, **kwargs):
        self.value, self.title, self.callback, self.enabled = value, value, callback, True
        self._nsObject = _Anything()

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def setTitle(self, title):
        self.title = title

    def enable(self, onOff):
        self.enabled = onOff

    def getNSView(self):
        return _Anything()


class PopUp(Control):
    """A popup keeps one row per title, as NSPopUpButton does."""

    def __init__(self, posSize, items, callback=None, **kwargs):
        Control.__init__(self, posSize, 0, callback)
        self.setItems(items)

    def setItems(self, items):
        self.items = []
        for title in items:
            if title in self.items:
                self.items.remove(title)
            self.items.append(title)
        self.value = 0

    def getItem(self):
        return self.items[self.value] if 0 <= self.value < len(self.items) else None

    def choose(self, title):
        self.value = self.items.index(title)
        self.callback(self)


class SliderControl(Control):
    def __init__(self, posSize, minValue=0, maxValue=100, value=0, callback=None, **kwargs):
        Control.__init__(self, posSize, value, callback)


class Window:
    def __init__(self, posSize, title="", **kwargs):
        self.__dict__["closed"] = False

    def bind(self, event, callback):
        pass

    def open(self):
        pass

    def resize(self, w, h):
        pass

    def getNSWindow(self):
        return _Anything()


@pytest.fixture
def widthmatcher(monkeypatch):
    """``plugin.py`` loaded against the stand-ins; returns a function
    that opens the panel on a font."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    glyphs = types.SimpleNamespace(font=None, fonts=[], defaults={}, localize=lambda names: names["en"],
                                   redraw=lambda: None, deactivateReporter=lambda reporter: None,
                                   currentDocument=None)
    appkit = types.ModuleType("AppKit")
    for name in ("NSAffineTransform", "NSBezierPath", "NSColor", "NSImage", "NSImageView"):
        setattr(appkit, name, _Anything())
    glyphsapp = types.ModuleType("GlyphsApp")
    glyphsapp.Glyphs, glyphsapp.GSInstance = glyphs, Instance
    glyphsapp.__all__ = ["Glyphs", "GSInstance"]
    plugins = types.ModuleType("GlyphsApp.plugins")
    plugins.ReporterPlugin, plugins.__all__ = object, ["ReporterPlugin"]
    objc = types.ModuleType("objc")
    objc.python_method = lambda f: f
    vanilla = types.ModuleType("vanilla")
    vanilla.FloatingWindow, vanilla.PopUpButton, vanilla.Slider = Window, PopUp, SliderControl
    for name in ("Button", "EditText", "Group", "TextBox"):
        setattr(vanilla, name, Control)
    for name, mod in (("objc", objc), ("AppKit", appkit), ("GlyphsApp", glyphsapp),
                      ("GlyphsApp.plugins", plugins), ("vanilla", vanilla)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "width_spacing", raising=False)
    spec = importlib.util.spec_from_file_location("widthmatcher_plugin", RESOURCES / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def open_on(font, reference="Loose", at=33.0, glyph="H"):
        glyphs.font, glyphs.fonts = font, [font]
        monkeypatch.setattr(Instance, "font", font)
        p = module.WidthMatcher()
        p.settings()
        p._lastLayer = font.glyphs[glyph].layers[font.masters[0].id]
        p._build_panel()
        p._panelFont = font
        p.module, p.panel = module, p._panel
        p.panel.refPop.choose(reference)
        field = p._axisRows[0][2]
        field.set("%g" % at)
        p._axisFieldChanged(field)
        return p

    return open_on


# --- measuring, by hand ------------------------------------------------------------


def drawn(font, name, masterId):
    """Every point of a glyph as it renders in a master, components
    resolved through that master's layers."""
    def points(layer, t=(1, 0, 0, 1, 0, 0), depth=0):
        pts = [_apply(t, n.position.x, n.position.y) for p in layer.paths for n in p.nodes]
        for c in layer.components:
            base = font.glyphs[c.componentName].layers[masterId]
            u, v = t, c.transform
            pts += points(base, (u[0] * v[0] + u[2] * v[1], u[1] * v[0] + u[3] * v[1],
                                 u[0] * v[2] + u[2] * v[3], u[1] * v[2] + u[3] * v[3],
                                 u[0] * v[4] + u[2] * v[5] + u[4], u[1] * v[4] + u[3] * v[5] + u[5]), depth + 1)
        return pts
    return points(font.glyphs[name].layers[masterId])


def measured(font, name, masterId):
    """(LSB, RSB, advance) from the outline; sidebearings None without ink."""
    pts, L = drawn(font, name, masterId), font.glyphs[name].layers[masterId]
    if not pts:
        return (None, None, L.width)
    return (min(p[0] for p in pts), L.width - max(p[0] for p in pts), L.width)


def save(p, name="Matched"):
    p.panel.nameField.set(name)
    p._saveAsMaster(None)
    return p._currentFont().masters[-1]


def status(p):
    return p.panel.statusLine.get()


INKED = [name for name, _ in DRAWN if name != "space"]


# --- Save as Master ----------------------------------------------------------------


def test_the_uprights_are_left_alone(widthmatcher):
    font = Font()
    before = {(g.name, m.id): (drawn(font, g.name, m.id), g.layers[m.id].width) for g in font.glyphs for m in font.masters}
    p = widthmatcher(font)
    masters = list(font.masters)
    save(p)
    assert {(g.name, m.id): (drawn(font, g.name, m.id), g.layers[m.id].width) for g in font.glyphs for m in masters} == before
    assert len(font.masters) == 4


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_every_glyph_lands_as_the_interpolated_outline_moved_as_a_whole(widthmatcher, mode):
    """A base that is re-spaced drags whatever is built from it, and a
    glyph with paths of its own next to a component comes apart."""
    font = Font()
    p = widthmatcher(font)
    p.panel.spacingPop.set(mode)
    p._spacingChanged(p.panel.spacingPop)
    interp = p._workingInstance(font).interpolatedFont
    want = {n: drawn(interp, n, interp.masters[0].id) for n in INKED}
    anchors = {n: [(a.position.x, a.position.y) for a in interp.glyphs[n].layers[0].anchors] for n in INKED}
    new = save(p)
    for name in INKED:
        got = drawn(font, name, new.id)
        assert len(got) == len(want[name]), name
        # "As a whole" on a grid: a node is rounded by up to half a unit,
        # and so is the offset of the component that draws it.
        slack = 1.0 if font.glyphs[name].layers[new.id].components else 0.5
        across = [a[0] - b[0] for a, b in zip(got, want[name])]
        by = (max(across) + min(across)) / 2.0
        assert max(across) - min(across) <= 2 * slack, (name, min(across), max(across))
        assert max(abs(a[1] - b[1]) for a, b in zip(got, want[name])) <= slack, name
        for a, (x, y) in zip(font.glyphs[name].layers[new.id].anchors, anchors[name]):
            assert abs(a.position.x - x - by) <= slack + 0.5 and abs(a.position.y - y) <= 0.5, name


def test_a_composite_ahead_of_its_base_takes_the_references_sidebearings(widthmatcher):
    """M comes first and is built from M.002, which comes last: measured
    while M.002's layer in the new master is still the empty one, M has
    no ink to speak of."""
    font = Font()
    p = widthmatcher(font)  # Spacing: reference sidebearings
    ref = font.masters[2]
    new = save(p)
    for name in INKED:
        rl, rr, _ = measured(font, name, ref.id)
        nl, nr, adv = measured(font, name, new.id)
        xs = [x for x, _ in drawn(font, name, new.id)]
        assert (nl, nr) == pytest.approx((rl, rr), abs=0.5), name
        assert adv == pytest.approx(rl + (max(xs) - min(xs)) + rr, abs=0.5), name


@pytest.mark.parametrize("mode,offset", [(1, 0), (2, 0), (3, 0), (2, 25), (1, -10)])
def test_in_the_advance_modes_the_advance_is_the_references(widthmatcher, mode, offset):
    font = Font()
    p = widthmatcher(font)
    p.panel.spacingPop.set(mode)
    p._spacingChanged(p.panel.spacingPop)
    p.panel.offsetField.set(str(offset))
    p._offsetChanged(p.panel.offsetField)
    ref = font.masters[2]
    new = save(p)
    for g in font.glyphs:
        assert g.layers[new.id].width == g.layers[ref.id].width + offset, g.name


def test_an_empty_glyph_takes_the_references_advance(widthmatcher):
    font = Font()
    p = widthmatcher(font)
    new = save(p)
    assert font.glyphs["space"].layers[new.id].width == font.glyphs["space"].layers[font.masters[2].id].width


def test_the_new_master_is_on_the_grid(widthmatcher):
    font = Font()
    p = widthmatcher(font, at=33.0)
    interp = p._workingInstance(font).interpolatedFont
    assert any(not float(n.position.x).is_integer() for g in interp.glyphs for pa in g.layers[0].paths for n in pa.nodes)
    new = save(p)
    for g in font.glyphs:
        L = g.layers[new.id]
        for pt in [n for pa in L.paths for n in pa.nodes] + L.anchors + L.components:
            assert float(pt.position.x).is_integer() and float(pt.position.y).is_integer(), g.name
        assert float(L.width).is_integer(), g.name


def test_a_font_without_a_grid_is_not_rounded(widthmatcher):
    font = Font()
    font.gridLength = 0.0
    p = widthmatcher(font, at=33.3)
    new = save(p)
    H = font.glyphs["H"].layers[new.id]
    assert H.paths[0].nodes[0].position.x == pytest.approx(135.0)  # the reference's sidebearing
    assert H.paths[0].nodes[1].position.x == pytest.approx(135.0 + 400 * 1.666)


def test_the_new_master_sits_where_the_sliders_are(widthmatcher):
    font = Font()
    p = widthmatcher(font, at=41.0)
    new = save(p, "Forty-one")
    assert (new.name, new.axes) == ("Forty-one", [41.0])


# --- what the panel says -------------------------------------------------------------


def numbers(text):
    """LSB, RSB and advance out of the panel's "Saved:" line."""
    words = text.split()
    return tuple(float(words[words.index(key) + 1]) for key in ("LSB", "RSB", "Adv"))


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_what_the_panel_predicts_is_what_save_writes(widthmatcher, mode):
    font = Font()
    p = widthmatcher(font)
    p.panel.spacingPop.set(mode)
    p._spacingChanged(p.panel.spacingPop)
    said = {}
    for name in INKED:
        p._lastLayer = font.glyphs[name].layers[font.masters[0].id]
        p._updatePreview()
        said[name] = numbers(p.panel.readoutPlan.get())
    new = save(p)
    for name in INKED:
        assert measured(font, name, new.id) == pytest.approx(said[name], abs=0.5), name


def test_spacing_and_offset_change_the_prediction_at_once(widthmatcher):
    font = Font()
    p = widthmatcher(font)
    first = numbers(p.panel.readoutPlan.get())
    p.panel.spacingPop.set(2)
    p._spacingChanged(p.panel.spacingPop)
    centred = numbers(p.panel.readoutPlan.get())
    assert centred != first and centred[2] == font.glyphs["H"].layers[font.masters[2].id].width
    p.panel.offsetField.set("40")
    p._offsetChanged(p.panel.offsetField)
    assert numbers(p.panel.readoutPlan.get())[2] == centred[2] + 40


def test_the_reference_shown_is_the_reference_used(widthmatcher):
    font = Font()
    p = widthmatcher(font, reference="Narrow")
    assert p.panel.refEcho.get() == "spacing from: Narrow"
    assert p.panel.nameField.get() == "Narrow matched"
    p.panel.refPop.choose("Loose")
    assert p.panel.refEcho.get() == "spacing from: Loose"
    assert p.panel.nameField.get() == "Loose matched"


def test_a_name_that_was_typed_stays(widthmatcher):
    font = Font()
    p = widthmatcher(font, reference="Narrow")
    p.panel.nameField.set("Medium")
    p.panel.refPop.choose("Loose")
    assert p.panel.nameField.get() == "Medium"


def test_refresh_picks_up_an_edit_to_the_masters(widthmatcher):
    font = Font()
    p = widthmatcher(font)
    before = numbers(p.panel.readoutPlan.get())
    for m in font.masters[:2]:
        L = font.glyphs["H"].layers[m.id]
        for n in L.paths[0].nodes[1:3]:
            n.position = (n.position.x + 300, n.position.y)
    p._updatePreview()
    assert numbers(p.panel.readoutPlan.get()) == before, "nothing re-interpolates on its own"
    p._refresh(None)
    after = numbers(p.panel.readoutPlan.get())
    assert after[2] == before[2] + 300  # reference sidebearings: the advance follows the ink


def test_the_status_is_still_there_after_the_save(widthmatcher):
    font = Font()
    p = widthmatcher(font)
    save(p)
    assert status(p).startswith("saved Matched: 15 glyphs")


def test_a_name_in_use_is_refused(widthmatcher):
    font = Font()
    p = widthmatcher(font)
    save(p, "Wide")
    assert len(font.masters) == 3
    assert "name in use" in status(p)


def test_a_glyph_that_fails_is_named_and_the_rest_land(widthmatcher, capsys):
    font = Font()
    for m in font.masters:
        font.glyphs["O"].layers[m.id].fails = "copy"
    p = widthmatcher(font)
    new = save(p)
    # Q is built from O: it cannot be measured without it, and fails with it.
    assert status(p).startswith("saved Matched: 13 glyphs")
    assert "2 glyph(s) failed: Q, O (RuntimeError: layer could not be copied)" in status(p)
    landed = [g.name for g in font.glyphs if g.layers[new.id].paths or g.layers[new.id].components]
    assert landed == [n for n in INKED if n not in ("Q", "O")]
    assert "RuntimeError: layer could not be copied" in capsys.readouterr().out


def test_a_layer_that_did_not_land_is_counted(widthmatcher):
    font = Font()
    p = widthmatcher(font)

    class Forgetful(Layers):  # takes the layer and keeps the empty one
        def __setitem__(self, key, layer):
            if key in self:
                return
            Layers.__setitem__(self, key, layer)

    kept = font.glyphs["H"].layers
    font.glyphs["H"].layers = Forgetful(font.glyphs["H"])
    for key, layer in kept.items():
        font.glyphs["H"].layers[key] = layer
    save(p)
    assert "only 14 of 15 layers landed" in status(p)


def test_nothing_is_called_drift_when_nothing_moved(widthmatcher):
    font = Font()
    p = widthmatcher(font)
    p.panel.spacingPop.set(1)
    p._spacingChanged(p.panel.spacingPop)
    save(p)
    assert "moved" not in status(p)


def test_a_glyph_moved_after_the_save_is_named(widthmatcher):
    """Metrics keys are re-applied when the interface updates resume."""
    font = Font()
    p = widthmatcher(font)

    def keys_come_back(font):
        L = font.glyphs["H"].layers[font.masters[-1].id]
        L.applyTransform((1, 0, 0, 1, 12, 0))
        L.width += 24

    font.resumed = keys_come_back
    save(p)
    assert "1 moved after the save: H" in status(p)


# --- the scratch instance and the reference list ----------------------------------------


def test_the_scratch_instance_does_not_export_and_leaves_with_the_panel(widthmatcher):
    font = Font()
    p = widthmatcher(font)
    assert [(i.name, i.active) for i in font.instances] == [(SCRATCH, False)]
    save(p)
    assert [i.name for i in font.instances] == [SCRATCH]
    p.willDeactivate()
    assert font.instances == []


def test_a_scratch_instance_left_in_the_file_is_taken_over(widthmatcher):
    font = Font()
    left = Instance()
    left.name, left.active, left.axes = SCRATCH, True, [50.0]
    kept = Instance()
    kept.name, kept.axes = "Regular", [50.0]
    font.instances = [kept, left]
    p = widthmatcher(font)
    assert [(i.name, i.active) for i in font.instances] == [("Regular", True), (SCRATCH, False)]
    p._panelClosed(None)
    assert font.instances == [kept]


def test_masters_with_one_name_are_told_apart(widthmatcher):
    font = Font(names=("Narrow", "Wide", "Narrow"))
    p = widthmatcher(font, reference="Narrow")
    assert len(p.panel.refPop.items) == 3
    assert p._referenceMaster(font) is font.masters[0]
    p.panel.refPop.set(2)
    p._referenceChanged(p.panel.refPop)
    assert p._referenceMaster(font) is font.masters[2]
    new = save(p, "Third")
    assert measured(font, "H", new.id)[0] == measured(font, "H", font.masters[2].id)[0]
    assert measured(font, "H", new.id)[0] != measured(font, "H", font.masters[0].id)[0]


def test_a_glyph_the_spacing_has_no_room_for_keeps_its_own(widthmatcher):
    """Reference sidebearings far below zero around a narrow glyph add up
    to an advance below zero."""
    font = Font()
    ref = font.masters[2]
    L = font.glyphs["period"].layers[ref.id]
    L.applyTransform((1, 0, 0, 1, -400, 0))   # its ink now starts at -305 …
    L.width = -120                             # … and ends 245 past the advance
    p = widthmatcher(font, glyph="period")
    assert "no room" in p.panel.readoutPlan.get()
    interp = p._workingInstance(font).interpolatedFont
    new = save(p)
    got = font.glyphs["period"].layers[new.id]
    assert got.width == round(interp.glyphs["period"].layers[0].width)
    assert "kept the spacing they were interpolated with" in status(p) and "period" in status(p)
    assert "moved after the save" not in status(p)
