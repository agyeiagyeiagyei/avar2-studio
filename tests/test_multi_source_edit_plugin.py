"""Multi-Source Edit's drag handling (``plugin.py``) with Glyphs, AppKit,
the panel and the Select tool itself stood in for.

The fakes copy what Glyphs 3.5 does where the plugin depends on it, each
measured on Glyphs' own engine: the Select tool moves the handles next to
a dragged on-curve node although they are not in the selection; a handle
dragged next to a smooth node turns the opposite handle by a delta of its
own; a node index past the end of a path wraps around."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
          / "MultiSourceEdit.glyphsTool" / "Contents" / "Resources" / "plugin.py")


class _Anything:
    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()


class GlyphsStub:
    font = None
    currentDocument = None

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

    def __init__(self, x, y, type_="line", smooth=False):
        self.position = (x, y)
        self.type, self.smooth = type_, smooth

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = Pt(float(value[0]), float(value[1]))


class Nodes(list):
    """An index past the end wraps around, as a GSPath's does."""

    def __getitem__(self, index):
        return list.__getitem__(self, index % len(self))


class Path_:
    def __init__(self, nodes, closed=True):
        self.nodes = Nodes(nodes)
        self.closed = closed


class Layer:
    def __init__(self, layerId, paths):
        self.layerId = self.associatedMasterId = layerId
        self.paths = list(paths)
        self.selection = []
        self.parent = None
        self.changes = []

    def beginChanges(self):
        self.changes.append("begin")

    def endChanges(self):
        self.changes.append("end")


def E(shift):
    """Twelve corners, no curves."""
    points = [(0, 0), (500, 0), (500, 100), (100, 100), (100, 350), (400, 350),
              (400, 450), (100, 450), (100, 700), (500, 700), (500, 800), (0, 800)]
    return [Path_([Node(x + shift, y) for x, y in points])]


def bowl(shift):
    """Two lines and two curves; node 4 is smooth, node 7 a corner."""
    points = [(100, 0, "line", False), (400, 0, "line", False),
              (460, 120, "offcurve", False), (460, 280, "offcurve", False), (400, 400, "curve", True),
              (340, 520, "offcurve", False), (160, 520, "offcurve", False), (100, 400, "curve", False)]
    return [Path_([Node(x + shift, y, t, s) for x, y, t, s in points])]


class Master:
    def __init__(self, name):
        self.name, self.id = name, "id-" + name


class Glyph:
    def __init__(self, name, masters, outline):
        self.name = name
        self.layers = {}
        for i, m in enumerate(masters):
            layer = Layer(m.id, outline(10.0 * i))  # every master its own coordinates
            layer.parent = self
            self.layers[m.id] = layer


class Glyphs_(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next((g for g in self if g.name == key), None)
        return list.__getitem__(self, key)


class Font:
    def __init__(self, masters=("Light", "Bold", "Wide", "Black"), glyphs=(("E", E), ("O", bowl))):
        self.masters = [Master(n) for n in masters]
        self.glyphs = Glyphs_(Glyph(name, self.masters, outline) for name, outline in glyphs)
        self.selectedFontMaster = self.masters[0]

    def layer(self, glyphName, masterName):
        master = next(m for m in self.masters if m.name == masterName)
        return self.glyphs[glyphName].layers[master.id]


class View:
    """The Edit view and its controller in one: they know the layer
    being edited."""

    layer = None

    def graphicView(self):
        return self

    def activeLayer(self):
        return self.layer

    def getActiveLocation_(self, event):
        return Pt(0, 0)


class Drag:
    """A mouse event. ``delta`` is how far the pointer is from where it
    went down; ``moves`` instead says what the Select tool does to each
    node, for the cases where that is not the pointer's delta."""

    def __init__(self, delta=(0, 0), moves=None):
        self.delta, self.moves = delta, moves


class SelectTool:
    """Glyphs' Select tool, as far as a drag goes: every selected node
    is put at its mouse-down position plus the delta, and an on-curve
    node takes the handles next to it along. The selection stays what
    it was."""

    def editViewController(self):
        return self.view

    def _nodes(self):
        return [(p, n, node) for p, path in enumerate(self.view.layer.paths)
                for n, node in enumerate(path.nodes)]

    def mouseDown_(self, event):
        self._down = {(p, n): (node.position.x, node.position.y) for p, n, node in self._nodes()}

    def mouseDragged_(self, event):
        layer = self.view.layer
        moves = event.moves
        if moves is None:
            moves = {}
            for p, n, node in self._nodes():
                if node not in layer.selection:
                    continue
                moves[(p, n)] = event.delta
                if node.type != "offcurve":
                    count = len(layer.paths[p].nodes)
                    for near in ((n - 1) % count, (n + 1) % count):
                        if layer.paths[p].nodes[near].type == "offcurve":
                            moves[(p, near)] = event.delta
        for (p, n), (dx, dy) in moves.items():
            x, y = self._down[(p, n)]
            layer.paths[p].nodes[n].position = (x + dx, y + dy)

    def mouseUp_(self, event):
        pass


class Widget:
    def __init__(self, posSize, title="", value=None, callback=None, **kwargs):
        self.posSize, self.title, self.value, self.callback = posSize, title, value, callback

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class TextBox(Widget):
    def __init__(self, posSize, text="", **kwargs):
        Widget.__init__(self, posSize, value=text)


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
def tool(monkeypatch, tmp_path):
    """``plugin.py`` loaded against the stand-ins; returns a function
    that makes the tool, its panel built, Sync edits ticked, on a font."""
    glyphs = GlyphsStub()
    appkit = types.ModuleType("AppKit")
    appkit.NSImage = _Anything()
    glyphsapp = types.ModuleType("GlyphsApp")
    glyphsapp.Glyphs = glyphs
    plugins = types.ModuleType("GlyphsApp.plugins")
    plugins.SelectTool = SelectTool
    objc = types.ModuleType("objc")
    objc.python_method = lambda f: f
    objc.super = super
    vanilla = types.ModuleType("vanilla")
    vanilla.CheckBox, vanilla.FloatingWindow, vanilla.TextBox = Widget, FloatingWindow, TextBox
    for name, mod in (("objc", objc), ("AppKit", appkit), ("GlyphsApp", glyphsapp),
                      ("GlyphsApp.plugins", plugins), ("vanilla", vanilla)):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("multisourceedit_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._DEBUG_LOG = str(tmp_path / "multisourceedit-debug.log")

    def make(font, sync=True):
        glyphs.font = font
        p = module.MultiSourceEdit()
        p.view = View()
        p.settings()
        p.start()
        p.activate()
        p._panel.syncBox.set(sync)
        p._syncToggled(p._panel.syncBox)
        return p

    return make


def positions(font, glyphName):
    return {m.name: [[(n.position.x, n.position.y) for n in path.nodes]
                     for path in font.glyphs[glyphName].layers[m.id].paths]
            for m in font.masters}


def moves(font, glyphName, before):
    """{master: {(path, node): (dx, dy)}} of every node that is not
    where it was."""
    out = {}
    for name, paths in positions(font, glyphName).items():
        out[name] = {(p, n): (x - before[name][p][n][0], y - before[name][p][n][1])
                     for p, path in enumerate(paths) for n, (x, y) in enumerate(path)
                     if (x, y) != before[name][p][n]}
    return out


def press(p, layer, picked):
    p.view.layer = layer
    layer.selection = [layer.paths[path].nodes[node] for path, node in picked]
    p.mouseDown_(Drag())


def drag(p, layer, picked, delta=(0, 0), moves=None):
    """Pick nodes of the layer by (path, node), press, drag, release."""
    press(p, layer, picked)
    p.mouseDragged_(Drag(delta, moves))
    p.mouseUp_(Drag(delta, moves))


def status(p):
    return p._panel.statusLine.get()


# --- what the tool did before, and still does --------------------------------


def test_a_drag_moves_the_same_node_in_every_ticked_master(tool):
    font = Font()
    p = tool(font)
    before = positions(font, "E")
    drag(p, font.layer("E", "Light"), [(0, 2)], (30, 10))
    assert moves(font, "E", before) == {name: {(0, 2): (30, 10)}
                                        for name in ("Light", "Bold", "Wide", "Black")}


def test_an_unticked_master_is_left_alone(tool):
    font = Font()
    p = tool(font)
    box = p._masterRows[font.masters[2].id]
    box.set(False)
    p._masterToggled(box)
    before = positions(font, "E")
    drag(p, font.layer("E", "Bold"), [(0, 0), (0, 5)], (-12, 0))
    both = {(0, 0): (-12, 0), (0, 5): (-12, 0)}
    assert moves(font, "E", before) == {"Light": both, "Bold": both, "Wide": {}, "Black": both}


def test_without_sync_edits_only_the_active_layer_moves(tool):
    font = Font()
    p = tool(font, sync=False)
    before = positions(font, "E")
    drag(p, font.layer("E", "Light"), [(0, 2)], (30, 10))
    assert moves(font, "E", before) == {"Light": {(0, 2): (30, 10)}, "Bold": {}, "Wide": {}, "Black": {}}
    assert font.layer("E", "Bold").changes == []


def test_a_drag_is_one_undo_step_in_each_synced_layer(tool):
    font = Font()
    p = tool(font)
    press(p, font.layer("E", "Light"), [(0, 2)])
    for step in ((10, 0), (20, 5), (30, 10)):
        p.mouseDragged_(Drag(step))
    p.mouseUp_(Drag((30, 10)))
    assert [font.layer("E", name).changes for name in ("Bold", "Wide", "Black")] == [["begin", "end"]] * 3
    assert font.layer("E", "Light").changes == [], "the active layer is the Select tool's"


# --- M1: masters whose nodes differ ------------------------------------------


def test_a_master_with_other_nodes_is_skipped_and_named(tool):
    font = Font()
    more = font.layer("E", "Bold").paths[0].nodes
    more.insert(1, Node(250, 0))  # index 6 now holds what the others hold at 5
    fewer = font.layer("E", "Wide").paths[0].nodes
    del fewer[4:]                 # index 6 wraps around to 2
    p = tool(font)
    before = positions(font, "E")
    drag(p, font.layer("E", "Light"), [(0, 6)], (25, 0))
    assert moves(font, "E", before) == {
        "Light": {(0, 6): (25, 0)}, "Bold": {}, "Wide": {}, "Black": {(0, 6): (25, 0)}}
    assert status(p) == "Not synced — different nodes: Bold, Wide"
    assert font.layer("E", "Bold").changes == [] and font.layer("E", "Wide").changes == []
    assert font.layer("E", "Black").changes == ["begin", "end"]


def test_the_same_count_of_other_nodes_is_skipped_too(tool):
    font = Font()
    corners = font.layer("O", "Bold").paths[0].nodes
    for node in corners:
        node.type = "line"        # eight nodes as in the others, no curve among them
    font.layer("O", "Wide").paths[0].closed = False
    p = tool(font)
    before = positions(font, "O")
    drag(p, font.layer("O", "Light"), [(0, 0)], (5, 5))
    assert moves(font, "O", before) == {
        "Light": {(0, 0): (5, 5)}, "Bold": {}, "Wide": {}, "Black": {(0, 0): (5, 5)}}
    assert status(p) == "Not synced — different nodes: Bold, Wide"


def test_another_count_of_paths_is_skipped(tool):
    font = Font()
    font.layer("E", "Black").paths.append(E(0)[0])
    p = tool(font)
    before = positions(font, "E")
    drag(p, font.layer("E", "Light"), [(0, 2)], (30, 10))
    assert moves(font, "E", before)["Black"] == {}
    assert status(p) == "Not synced — different nodes: Black"


def test_the_odd_layer_out_syncs_into_nothing(tool):
    font = Font()
    font.layer("E", "Light").paths[0].nodes.insert(1, Node(250, 0))
    p = tool(font)
    before = positions(font, "E")
    drag(p, font.layer("E", "Light"), [(0, 6)], (25, 0))
    assert moves(font, "E", before) == {"Light": {(0, 6): (25, 0)}, "Bold": {}, "Wide": {}, "Black": {}}
    assert status(p) == "Not synced — different nodes: Bold, Wide, Black"


def test_masters_that_agree_are_not_reported(tool):
    font = Font()
    p = tool(font)
    drag(p, font.layer("E", "Light"), [(0, 2)], (30, 10))
    assert status(p) == ""


def test_an_unticked_master_with_other_nodes_is_not_reported(tool):
    font = Font()
    font.layer("E", "Bold").paths[0].nodes.insert(1, Node(250, 0))
    p = tool(font)
    box = p._masterRows[font.masters[1].id]
    box.set(False)
    p._masterToggled(box)
    drag(p, font.layer("E", "Light"), [(0, 6)], (25, 0))
    assert status(p) == ""


def test_the_report_goes_with_the_next_press(tool):
    font = Font()
    font.layer("E", "Bold").paths[0].nodes.insert(1, Node(250, 0))
    p = tool(font)
    drag(p, font.layer("E", "Light"), [(0, 6)], (25, 0))
    assert status(p) == "Not synced — different nodes: Bold"
    drag(p, font.layer("O", "Light"), [(0, 0)], (5, 5))  # a glyph whose masters agree
    assert status(p) == ""


def test_the_panel_has_room_for_the_report(tool):
    font = Font()
    p = tool(font)
    rows = len(font.masters)
    x, y, w, h = p._panel.statusLine.posSize
    assert y < 0 and -y >= h, "kept to the bottom edge, inside the window"
    assert p._panel.size[1] >= p._mastersY + rows * 22 + h, "under the master rows, not over them"


# --- M2: what moves without being selected -----------------------------------


def test_handles_next_to_a_dragged_node_follow_in_every_master(tool):
    font = Font()
    p = tool(font)
    before = positions(font, "O")
    layer = font.layer("O", "Light")
    drag(p, layer, [(0, 4)], (30, 10))
    assert [n.type for n in layer.selection] == ["curve"], "the handles were never selected"
    together = {(0, 3): (30, 10), (0, 4): (30, 10), (0, 5): (30, 10)}
    assert moves(font, "O", before) == {name: together for name in ("Light", "Bold", "Wide", "Black")}


def test_a_corner_takes_its_one_handle_along(tool):
    font = Font()
    p = tool(font)
    before = positions(font, "O")
    drag(p, font.layer("O", "Light"), [(0, 1)], (30, 10))  # line in, curve out
    assert moves(font, "O", before)["Black"] == {(0, 1): (30, 10), (0, 2): (30, 10)}


def test_every_node_takes_its_own_delta(tool):
    """A handle dragged next to a smooth node: Glyphs turns the opposite
    handle to keep the node smooth, by a delta of its own."""
    font = Font()
    p = tool(font)
    before = positions(font, "O")
    turned = {(0, 3): (30, 10), (0, 5): (-25, -16)}
    drag(p, font.layer("O", "Light"), [(0, 3)], moves=turned)
    assert moves(font, "O", before) == {name: turned for name in ("Light", "Bold", "Wide", "Black")}


def test_a_node_dragged_back_is_back_in_every_master(tool):
    font = Font()
    p = tool(font)
    before = positions(font, "O")
    press(p, font.layer("O", "Light"), [(0, 4)])
    p.mouseDragged_(Drag((30, 10)))
    assert moves(font, "O", before)["Bold"] != {}
    p.mouseDragged_(Drag((0, 0)))
    p.mouseUp_(Drag((0, 0)))
    assert moves(font, "O", before) == {name: {} for name in ("Light", "Bold", "Wide", "Black")}


def test_nodes_added_during_the_drag_stop_the_sync(tool):
    """The positions taken at the press are by index: once the active
    layer has other nodes they say nothing about what moved."""
    font = Font()
    p = tool(font)
    layer = font.layer("E", "Light")
    press(p, layer, [(0, 6)])
    layer.paths[0].nodes.insert(1, Node(250, 0))
    before = positions(font, "E")
    p.mouseDragged_(Drag(moves={(0, 7): (25, 0)}))
    p.mouseUp_(Drag())
    assert [moves(font, "E", before)[name] for name in ("Bold", "Wide", "Black")] == [{}, {}, {}]
