"""Width Matcher's measuring and spacing (``width_spacing.py``): pure
Python on duck-typed layers, no Glyphs."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

RESOURCES = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
             / "WidthMatcher.glyphsReporter" / "Contents" / "Resources")

spec = importlib.util.spec_from_file_location("width_spacing", RESOURCES / "width_spacing.py")
ws = importlib.util.module_from_spec(spec)
sys.modules["width_spacing"] = ws
spec.loader.exec_module(ws)


class Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Placed:
    """Anything with a ``position`` that takes a tuple and reads back
    with ``.x``/``.y``, as Glyphs' nodes and anchors do."""

    def __init__(self, x, y, type_="line", name=None):
        self.position = (x, y)
        self.type = type_
        self.name = name

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value if isinstance(value, Pt) else Pt(float(value[0]), float(value[1]))


class Path_:
    def __init__(self, nodes, closed=True):
        self.nodes = [n if isinstance(n, Placed) else Placed(*n) for n in nodes]
        self.closed = closed


class Component:
    def __init__(self, name, transform=(1, 0, 0, 1, 0, 0)):
        self.componentName = name
        self.transform = tuple(float(v) for v in transform)


class Layer:
    def __init__(self, paths=(), components=(), anchors=(), width=600.0):
        self.paths, self.components, self.anchors = list(paths), list(components), list(anchors)
        self.width = width

    def applyTransform(self, m):
        """Moves nodes, anchors and components, as GSLayer's does."""
        for pt in [n for p in self.paths for n in p.nodes] + self.anchors:
            x, y = pt.position.x, pt.position.y
            pt.position = (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])
        for c in self.components:
            t = c.transform
            c.transform = t[:4] + (t[4] + m[4], t[5] + m[5])


def box(x0, x1, y0=0.0, y1=700.0):
    return Path_([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def circle(r=200.0, cx=300.0, cy=300.0):
    k = 4.0 / 3.0 * (math.sqrt(2.0) - 1.0) * r
    pts = [(cx + r, cy + k), (cx + k, cy + r), (cx, cy + r), (cx - k, cy + r), (cx - r, cy + k), (cx - r, cy),
           (cx - r, cy - k), (cx - k, cy - r), (cx, cy - r), (cx + k, cy - r), (cx + r, cy - k), (cx + r, cy)]
    return Path_([Placed(x, y, "curve" if i % 3 == 2 else "offcurve") for i, (x, y) in enumerate(pts)])


def drawn(layer, layers):
    """Every point of the outline as it renders, components resolved."""
    return sorted(p for seg in ws.outline(layer, layers.get) for p in (seg[0], seg[-1]))


# --- measuring ----------------------------------------------------------------


def test_ink_span_of_plain_paths():
    assert ws.ink_span(Layer([box(40, 140), box(200, 330)]), {}.get) == (40.0, 330.0)


def test_ink_span_of_a_layer_that_draws_nothing():
    assert ws.ink_span(Layer(), {}.get) is None
    assert ws.ink_span(Layer(components=[Component("missing")]), {}.get) is None


def test_ink_span_follows_the_curve_not_the_nodes():
    layer = Layer([circle(r=200.0, cx=300.0, cy=300.0)])
    layer.applyTransform((1.0, 0.0, math.tan(math.radians(12.0)), 1.0, 0.0, 0.0))
    nodes = [n.position.x for n in layer.paths[0].nodes if n.type != "offcurve"]
    left, right = ws.ink_span(layer, {}.get)
    assert right > max(nodes) + 1.0 and left < min(nodes) - 1.0


def test_ink_span_resolves_components():
    layers = {"period": Layer([box(30, 130, 0, 100)], width=160)}
    colon = Layer(components=[Component("period"), Component("period", (1, 0, 0, 1, 20, 400))])
    assert ws.ink_span(colon, layers.get) == (30.0, 150.0)


def test_ink_span_of_a_half_turned_component():
    """V drawn as N turned half way round: the transform is part of the
    measure."""
    layers = {"N": Layer([box(50, 450)], width=500)}
    V = Layer(components=[Component("N", (-1, 0, 0, -1, 500, 700))])
    assert ws.ink_span(V, layers.get) == (50.0, 450.0)
    layers["N"] = Layer([box(80, 450)], width=500)
    assert ws.ink_span(V, layers.get) == (50.0, 420.0)


def test_ink_span_of_components_of_components():
    layers = {"quoteleft": Layer([box(10, 60)], width=70),
              "quotedblleft": Layer(components=[Component("quoteleft"), Component("quoteleft", (1, 0, 0, 1, 80, 0))])}
    right = Layer(components=[Component("quotedblleft", (-1, 0, 0, -1, 200, 900))])
    assert ws.ink_span(right, layers.get) == (60.0, 190.0)


def test_a_glyph_that_contains_itself_ends():
    layers = {}
    layers["loop"] = Layer([box(0, 10)], components=[Component("loop", (1, 0, 0, 1, 100, 0))])
    left, right = ws.ink_span(layers["loop"], layers.get)
    assert left == 0.0 and right == 10.0 + 100.0 * ws.MAX_DEPTH


def test_ink_span_along_a_slant():
    layer = Layer([box(100, 200, 0, 1200)])
    tan = math.tan(math.radians(10.0))
    layer.applyTransform((1.0, 0.0, tan, 1.0, -tan * 600.0, 0.0))
    assert ws.ink_span(layer, {}.get, 10.0, 600.0) == pytest.approx((100.0, 200.0))
    upright = ws.ink_span(layer, {}.get)
    assert upright[1] - upright[0] == pytest.approx(100.0 + tan * 1200.0)


# --- planning -----------------------------------------------------------------


def test_reference_sidebearings():
    p = ws.plan(ws.SPACING_REF_SB, 0.0, (50.0, 450.0), 520.0, (80.0, 400.0), 480.0)
    assert p == {"shift": -30.0, "width": 50.0 + 320.0 + 70.0, "lsb": 50.0, "rsb": 70.0, "fits": True}


def test_reference_sidebearings_ignore_the_offset():
    assert ws.plan(ws.SPACING_REF_SB, 25.0, (50.0, 450.0), 520.0, (80.0, 400.0), 480.0)["width"] == 440.0


@pytest.mark.parametrize("mode,lsb", [(ws.SPACING_ADV_PROPORTIONAL, 200.0 * 50 / 120),
                                      (ws.SPACING_ADV_CENTRED, 100.0),
                                      (ws.SPACING_ADV_KEEP_LSB, 50.0)])
def test_the_advance_modes_pin_the_advance(mode, lsb):
    p = ws.plan(mode, 0.0, (50.0, 450.0), 520.0, (80.0, 400.0), 480.0)
    assert p["width"] == 520.0
    assert p["lsb"] == pytest.approx(lsb)
    assert p["lsb"] + 320.0 + p["rsb"] == pytest.approx(520.0)


def test_the_offset_widens_the_advance_modes():
    assert ws.plan(ws.SPACING_ADV_CENTRED, 25.0, (50.0, 450.0), 520.0, (80.0, 400.0), 480.0)["width"] == 545.0


@pytest.mark.parametrize("mode", [ws.SPACING_ADV_PROPORTIONAL, ws.SPACING_ADV_CENTRED, ws.SPACING_ADV_KEEP_LSB])
def test_on_a_grid_the_advance_is_still_the_references(mode):
    """Two sidebearings rounded one by one add up a unit off: E's ink of
    5455 centred in 5658 is 101.5 a side, and 102 + 5455 + 102 is 5659."""
    p = ws.plan(mode, 0.0, (102.0, 5557.0), 5658.0, (311.0, 5766.0), 6077.0, grid=1.0)
    assert p["width"] == 5658.0
    assert float(p["shift"]).is_integer()
    assert p["lsb"] + 5455.0 + p["rsb"] == 5658.0


def test_the_shift_keeps_a_layer_on_the_grid():
    p = ws.plan(ws.SPACING_ADV_CENTRED, 0.0, (50.0, 450.0), 521.0, (80.0, 400.0), 480.0, grid=1.0)
    assert p["shift"] == 21.0 and p["lsb"] == 101.0 and p["rsb"] == 100.0  # 100.5 a side: a half goes up
    assert ws.plan(ws.SPACING_ADV_CENTRED, 0.0, (50.0, 450.0), 521.0, (80.0, 400.0), 480.0, grid=0.0)["shift"] == 20.5


def test_an_empty_glyph_takes_the_advance():
    assert ws.plan(ws.SPACING_REF_SB, 40.0, None, 250.0, None, 310.0) == {
        "shift": 0.0, "width": 250.0, "lsb": None, "rsb": None, "fits": True}
    assert ws.plan(ws.SPACING_ADV_KEEP_LSB, 40.0, None, 250.0, None, 310.0)["width"] == 290.0


def test_a_reference_without_ink_lends_no_sidebearings():
    p = ws.plan(ws.SPACING_REF_SB, 0.0, None, 250.0, (80.0, 400.0), 480.0)
    assert (p["lsb"], p["rsb"], p["width"]) == (0.0, 0.0, 320.0)


def test_sidebearings_that_leave_no_room_are_not_forced():
    """An ultra-wide backslash overlaps its neighbours by 413 a side. Put
    around a hairline one 58 wide that is an advance of −768, which
    Glyphs stores as 0."""
    p = ws.plan(ws.SPACING_REF_SB, 0.0, (-413.0, 3149.0), 2736.0, (7.0, 65.0), 72.0, grid=1.0)
    assert p == {"shift": 0.0, "width": 72.0, "lsb": 7.0, "rsb": 7.0, "fits": False}
    assert ws.plan(ws.SPACING_ADV_KEEP_LSB, 0.0, (-413.0, 3149.0), 2736.0, (7.0, 65.0), 72.0, grid=1.0)["fits"]


def test_an_offset_cannot_take_an_empty_glyph_below_zero():
    assert ws.plan(ws.SPACING_ADV_CENTRED, -300.0, None, 250.0, None, 250.0)["width"] == 0.0


# --- moving -------------------------------------------------------------------


def test_round_layer_rounds_nodes_anchors_and_components():
    layer = Layer([Path_([(10.4, 0.5), (20.5, -0.5), (30.49, 700.2)])], [Component("a", (1, 0, 0, 1, 12.5, -3.4))],
                  [Placed(55.5, 699.6, name="top")])
    ws.round_layer(layer, 1.0)
    assert [(n.position.x, n.position.y) for n in layer.paths[0].nodes] == [(10.0, 1.0), (21.0, 0.0), (30.0, 700.0)]
    assert (layer.anchors[0].position.x, layer.anchors[0].position.y) == (56.0, 700.0)
    assert layer.components[0].transform == (1.0, 0.0, 0.0, 1.0, 13.0, -3.0)


class Stubborn(Placed):
    """Glyphs' node: a move too small to notice is no move."""

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        new = value if isinstance(value, Pt) else Pt(float(value[0]), float(value[1]))
        old = getattr(self, "_position", None)
        if old is None or abs(new.x - old.x) > 1e-4 or abs(new.y - old.y) > 1e-4:
            self._position = new


def test_round_layer_moves_a_node_that_is_a_hair_off_the_grid():
    layer = Layer([Path_([Stubborn(798.9999897078045, 1200.0), Stubborn(60.4, 648.0)])])
    ws.round_layer(layer, 1.0)
    assert [(n.position.x, n.position.y) for n in layer.paths[0].nodes] == [(799.0, 1200.0), (60.0, 648.0)]


def test_round_layer_without_a_grid_leaves_it():
    layer = Layer([Path_([(10.4, 0.5)])])
    ws.round_layer(layer, 0.0)
    assert layer.paths[0].nodes[0].position.x == 10.4


def moved_as_a_whole(before, after, by):
    return [(x + by, y) for x, y in before] == after


def test_a_composite_moves_by_its_own_shift_not_its_bases():
    layers = {"period": Layer([box(30, 130, 0, 100)], width=160),
              "colon": Layer(components=[Component("period"), Component("period", (1, 0, 0, 1, 20, 400))])}
    shifts = {"period": 40.0, "colon": -15.0}
    before = {n: drawn(layers[n], layers) for n in layers}
    for name, layer in layers.items():
        ws.shift_layer(layer, shifts[name], lambda base: shifts.get(base, 0.0))
    assert moved_as_a_whole(before["period"], drawn(layers["period"], layers), 40.0)
    assert moved_as_a_whole(before["colon"], drawn(layers["colon"], layers), -15.0)


def test_a_half_turned_component_moves_the_right_way():
    layers = {"N": Layer([box(50, 450)], width=500), "V": Layer(components=[Component("N", (-1, 0, 0, -1, 500, 700))])}
    shifts = {"N": 30.0, "V": 12.0}
    before = drawn(layers["V"], layers)
    for name, layer in layers.items():
        ws.shift_layer(layer, shifts[name], lambda base: shifts.get(base, 0.0))
    assert moved_as_a_whole(before, drawn(layers["V"], layers), 12.0)
    assert layers["V"].components[0].transform == (-1.0, 0.0, 0.0, -1.0, 500.0 + 12.0 + 30.0, 700.0)


def test_own_paths_and_components_stay_together():
    """Q is O plus a tail of its own: moved by different amounts the
    tail comes off."""
    layers = {"O": Layer([box(60, 560)], width=620),
              "Q": Layer([box(300, 380, -150, 80)], [Component("O")], [Placed(310, 700, name="top")])}
    shifts = {"O": -22.0, "Q": 9.0}
    before = drawn(layers["Q"], layers)
    for name, layer in layers.items():
        ws.shift_layer(layer, shifts[name], lambda base: shifts.get(base, 0.0))
    assert moved_as_a_whole(before, drawn(layers["Q"], layers), 9.0)
    assert layers["Q"].anchors[0].position.x == 319.0


def test_a_layer_that_stays_put_still_makes_up_for_its_base():
    layers = {"period": Layer([box(30, 130, 0, 100)]), "colon": Layer(components=[Component("period", (1, 0, 0, 1, 0, 400))])}
    before = drawn(layers["colon"], layers)
    ws.shift_layer(layers["period"], 25.0, lambda base: 0.0)
    ws.shift_layer(layers["colon"], 0.0, {"period": 25.0}.get)
    assert drawn(layers["colon"], layers) == before


def test_components_of_components_move_as_a_whole():
    layers = {"quoteleft": Layer([box(10, 60)], width=70),
              "quotedblleft": Layer(components=[Component("quoteleft"), Component("quoteleft", (1, 0, 0, 1, 80, 0))]),
              "quotedblright": Layer(components=[Component("quotedblleft", (-1, 0, 0, -1, 200, 900))])}
    shifts = {"quoteleft": 7.0, "quotedblleft": -4.0, "quotedblright": 11.0}
    before = {n: drawn(layers[n], layers) for n in layers}
    for name, layer in layers.items():
        ws.shift_layer(layer, shifts[name], lambda base: shifts.get(base, 0.0))
    for name in layers:
        assert moved_as_a_whole(before[name], drawn(layers[name], layers), shifts[name]), name


def test_a_move_leaves_the_layer_on_the_grid():
    """Glyphs carries the transform out in floating point."""
    layer = Layer([box(60, 560)], anchors=[Placed(310, 700, name="top")])
    plain = layer.applyTransform
    layer.applyTransform = lambda m: plain(m[:4] + (m[4] - 1.03e-5, m[5]))
    ws.shift_layer(layer, 229.0, lambda base: 0.0, grid=1.0)
    assert [n.position.x for n in layer.paths[0].nodes] == [289.0, 789.0, 789.0, 289.0]
    assert layer.anchors[0].position.x == 539.0


def test_shapes_tell_a_landed_layer_from_an_empty_one():
    assert ws.shapes(Layer()) == ((), ())
    assert ws.shapes(Layer([box(0, 10)], [Component("O")])) == ((4,), ("O",))
