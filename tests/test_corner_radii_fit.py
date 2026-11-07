"""Corner Radii's geometry (``cornerfit.py``) on duck-typed nodes, without
Glyphs: what counts as one rounded corner, scaling and sharpening it in
place, the rounds that outgrow their side, and the choice of rounds
across layers that have to stay compatible."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

RESOURCES = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
             / "CornerRadii.glyphsReporter" / "Contents" / "Resources")

_spec = importlib.util.spec_from_file_location("corner_radii_cornerfit", RESOURCES / "cornerfit.py")
cornerfit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cornerfit)


class Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Node:
    def __init__(self, x, y, type_):
        self.position = Pt(float(x), float(y))
        self.type = type_


class Path_:
    def __init__(self, nodes, closed=True):
        self.nodes = [Node(*n) for n in nodes]
        self.closed = closed


# --- outlines ---------------------------------------------------------------
# An outline is a list of (x, y, type), in the order Glyphs keeps them.


def arc(centre, r, a0, a1, pieces=1):
    """The nodes of a circular arc from angle ``a0`` to ``a1`` (degrees)
    AFTER its first point: two handles and a curve node per piece."""
    out = []
    step = (a1 - a0) / float(pieces)
    k = 4.0 / 3.0 * math.tan(math.radians(abs(step)) / 4.0) * r
    sign = 1.0 if step > 0 else -1.0

    def on(a):
        return (centre[0] + r * math.cos(math.radians(a)), centre[1] + r * math.sin(math.radians(a)))

    def along(a):  # direction of travel at angle a
        return (-sign * math.sin(math.radians(a)), sign * math.cos(math.radians(a)))

    for i in range(pieces):
        s, e = a0 + i * step, a0 + (i + 1) * step
        (sx, sy), (ex, ey), ts, te = on(s), on(e), along(s), along(e)
        out.append((sx + k * ts[0], sy + k * ts[1], "offcurve"))
        out.append((ex - k * te[0], ey - k * te[1], "offcurve"))
        out.append((ex, ey, "curve"))
    return out


def rounded(points, rounds):
    """The polygon ``points`` with the corners in ``rounds``
    (``{index: (radius, pieces)}``) rounded off by a circular arc."""
    out = []
    m = len(points)
    for i, c in enumerate(points):
        if i not in rounds:
            out.append((c[0], c[1], "line"))
            continue
        r, pieces = rounds[i]
        p, q = points[i - 1], points[(i + 1) % m]
        u = _unit((c[0] - p[0], c[1] - p[1]))   # arriving
        v = _unit((q[0] - c[0], q[1] - c[1]))   # leaving
        turn = math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])
        reach = r * math.tan(abs(turn) / 2.0)
        t1 = (c[0] - reach * u[0], c[1] - reach * u[1])
        side = 1.0 if turn > 0 else -1.0
        centre = (t1[0] - side * r * u[1], t1[1] + side * r * u[0])
        a0 = math.degrees(math.atan2(t1[1] - centre[1], t1[0] - centre[0]))
        out.append((t1[0], t1[1], "line"))
        out.extend(arc(centre, r, a0, a0 + math.degrees(turn), pieces))
    return out


def _unit(v):
    length = math.hypot(*v)
    return (v[0] / length, v[1] / length)


def rect(x0, y0, x1, y1, r=None, pieces=1, only=None):
    """Counter-clockwise from the bottom right; corners 0..3 are bottom
    right, top right, top left, bottom left."""
    points = [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    which = range(4) if only is None else only
    return rounded(points, {i: (r, pieces) for i in which} if r else {})


def pill(x0, y0, x1, y1, pieces=2):
    """A stadium: straight top and bottom, half-circle ends."""
    r = (y1 - y0) / 2.0
    return ([(x1 - r, y0, "line")] + arc((x1 - r, y0 + r), r, -90.0, 90.0, pieces)
            + [(x0 + r, y1, "line")] + arc((x0 + r, y0 + r), r, 90.0, 270.0, pieces))


def arch(lean, pieces):
    """Two stems 400 apart leaning ``lean`` degrees towards each other,
    joined by an arch — the top of an n."""
    dx = 600.0 * math.tan(math.radians(lean))
    t1, t2 = (400.0 - dx, 600.0), (dx, 600.0)
    up = _unit((-dx, 600.0))
    down = _unit((-dx, -600.0))
    top = (200.0, 800.0)
    h = 110.0
    nodes = [(0.0, 0.0, "line"), (400.0, 0.0, "line"), (t1[0], t1[1], "line")]
    if pieces == 1:
        nodes += [(t1[0] + 2.4 * h * up[0], t1[1] + 2.4 * h * up[1], "offcurve"),
                  (t2[0] - 2.4 * h * down[0], t2[1] - 2.4 * h * down[1], "offcurve"),
                  (t2[0], t2[1], "curve")]
    else:
        nodes += [(t1[0] + h * up[0], t1[1] + h * up[1], "offcurve"),
                  (top[0] + h, top[1], "offcurve"), (top[0], top[1], "curve"),
                  (top[0] - h, top[1], "offcurve"),
                  (t2[0] - h * down[0], t2[1] - h * down[1], "offcurve"),
                  (t2[0], t2[1], "curve")]
    return nodes


def find(*outlines):
    paths = [Path_(o) for o in outlines]
    return paths, cornerfit.find_corners(paths)


def kinds(path):
    return [n.type for n in path.nodes]


def write(paths, corner, factor):
    path = paths[corner["path_index"]]
    for i, (x, y) in cornerfit.transformed_positions(path, corner, factor).items():
        path.nodes[i].position = Pt(x, y)


# --- what is one round --------------------------------------------------------


def test_a_round_in_one_curve_piece():
    _, corners = find(rect(100, 0, 900, 1200, r=100))
    assert [c["node_indices"] for c in corners] == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11], [12, 13, 14, 15]]
    assert [c["radius"] for c in corners] == [pytest.approx(100.0, abs=0.05)] * 4
    assert [c["corner"] for c in corners] == [
        pytest.approx((900, 0)), pytest.approx((900, 1200)), pytest.approx((100, 1200)), pytest.approx((100, 0))]
    assert [c["baseline"] for c in corners] == [True, False, False, True]


def test_a_round_in_two_curve_pieces_is_one_round():
    _, corners = find(rect(100, 0, 900, 1200, r=100, pieces=2, only=[0]))
    assert len(corners) == 1
    c = corners[0]
    assert c["node_indices"] == [0, 1, 2, 3, 4, 5, 6]
    assert (c["t1_index"], c["t2_index"]) == (0, 6)
    assert c["corner"] == pytest.approx((900.0, 0.0))
    assert c["radius"] == pytest.approx(100.0, abs=0.05)
    assert c["residual"] < 0.05
    assert len(c["mids"]) == 1 and len(c["handles"]) == 4 and len(c["segments"]) == 2
    # the label sits on the arc, at its middle
    assert c["label_pos"] == pytest.approx((800 + 100 * math.sin(math.pi / 4), 100 - 100 * math.cos(math.pi / 4)))


def test_a_round_in_three_curve_pieces_is_one_round():
    _, corners = find(rect(100, 0, 900, 1200, r=120, pieces=3))
    assert [len(c["node_indices"]) for c in corners] == [10] * 4
    assert [c["radius"] for c in corners] == [pytest.approx(120.0, abs=0.05)] * 4


def test_a_round_is_found_wherever_the_node_list_starts():
    outline = rect(100, 0, 900, 1200, r=100, pieces=2, only=[0])
    for shift in range(len(outline)):
        turned = outline[shift:] + outline[:shift]
        _, corners = find(turned)
        assert len(corners) == 1, shift
        assert corners[0]["corner"] == pytest.approx((900.0, 0.0)), shift
        assert len(corners[0]["node_indices"]) == 7, shift
        assert corners[0]["t1_index"] == (0 - shift) % len(outline), shift


def test_a_two_piece_round_scales_in_place():
    paths, corners = find(rect(100, 0, 900, 1200, r=100, pieces=2, only=[0]))
    before = kinds(paths[0])
    write(paths, corners[0], 1.5)
    assert kinds(paths[0]) == before and len(paths[0].nodes) == 10
    again = cornerfit.find_corners(paths)
    assert len(again) == 1
    assert again[0]["radius"] == pytest.approx(150.0, abs=0.05)
    assert again[0]["corner"] == pytest.approx((900.0, 0.0))
    nodes = paths[0].nodes
    assert (nodes[0].position.x, nodes[0].position.y) == pytest.approx((750.0, 0.0))   # still on the bottom edge
    assert (nodes[6].position.x, nodes[6].position.y) == pytest.approx((900.0, 150.0))  # still on the right edge
    assert [(n.position.x, n.position.y) for n in nodes[7:]] == [(900, 1200), (100, 1200), (100, 0)]


def test_a_two_piece_round_sharpens_to_one_node():
    _, corners = find(rect(100, 0, 900, 1200, r=100, pieces=2, only=[0]))
    plan = cornerfit.sharpen_plan(corners[0])
    assert plan["t1"] == 0 and plan["delete"] == [1, 2, 3, 4, 5, 6]
    assert plan["corner"] == pytest.approx((900.0, 0.0))


def test_a_collapsed_round_is_still_a_round():
    """A master with no rounding keeps the round's nodes, all on the
    corner. Sharpen has to find it, or that master keeps four nodes where
    the others are left with one."""
    collapsed = [(900, 0, "line"), (900, 0, "offcurve"), (900, 0, "offcurve"), (900, 0, "curve"),
                 (900, 1200, "line"), (100, 1200, "line"), (100, 0, "line")]
    _, corners = find(collapsed)
    assert len(corners) == 1
    assert corners[0]["corner"] == pytest.approx((900.0, 0.0))
    assert cornerfit.sharpen_plan(corners[0])["delete"] == [1, 2, 3]


def test_a_narrow_apex_is_a_corner():
    """The two sides of a V, 20 degrees apart."""
    dx = 1200.0 * math.tan(math.radians(10.0))
    _, corners = find(rounded([(-dx, 1200.0), (0.0, 0.0), (dx, 1200.0)], {1: (30.0, 2)}))
    assert len(corners) == 1
    assert corners[0]["corner"] == pytest.approx((0.0, 0.0), abs=1e-6)
    assert corners[0]["radius"] == pytest.approx(30.0, abs=0.05)


# --- what is not ----------------------------------------------------------------


def test_a_half_circle_end_is_not_a_corner():
    """Its two straights are parallel: there is no corner to scale about,
    and its radius is half the distance between them whatever is asked."""
    for pieces in (1, 2, 3):
        paths, corners = find(pill(0, 0, 900, 300, pieces=pieces))
        assert corners == [], pieces


def test_an_arch_between_stems_that_are_not_quite_parallel_is_not_a_corner():
    """Extended, the stems of an n leaning 1 degree each meet 11 000
    units up. Sharpened to that point the glyph would be a spike."""
    for pieces in (1, 2):
        for lean in (0.0, 0.2, 1.0, 2.0):
            _, corners = find(arch(lean, pieces))
            assert corners == [], (pieces, lean)


def test_a_curve_between_straights_that_meet_behind_it_is_not_a_corner():
    """An S between two straights: extended, they meet past the far end
    of the curve, not where it could be rounding anything off."""
    s = [(0, 0, "line"), (400, 0, "line"), (500, 0, "offcurve"), (450, 300, "offcurve"), (600, 300, "curve"),
         (900, 200, "line"), (900, 900, "line"), (0, 900, "line")]
    _, corners = find(s)
    assert corners == []


def test_a_round_without_a_straight_on_one_side_is_not_a_corner():
    """A quarter round that runs on into another curve shares its end
    node with that curve; the node cannot serve both."""
    outline = rect(100, 0, 900, 1200, r=100)
    outline[4] = (900, 300, "offcurve")  # the straight up the right side becomes a curve
    outline[5:5] = [(900, 600, "offcurve"), (900, 1100, "curve")]
    _, corners = find(outline)
    assert [(round(c["corner"][0]), round(c["corner"][1])) for c in corners] == [(100, 1200), (100, 0)]


def test_no_round_is_scaled_about_anything_but_its_corner():
    paths, corners = find(pill(0, 0, 900, 300, pieces=1))
    before = [(n.position.x, n.position.y) for n in paths[0].nodes]
    for c in corners:
        write(paths, c, 1.5)
    assert [(n.position.x, n.position.y) for n in paths[0].nodes] == before
    # nor is a round handed over without one moved about its circle's centre
    paths, corners = find(rect(100, 0, 900, 1200, r=100))
    cornerless = dict(corners[0], corner=None)
    assert cornerfit.transformed_positions(paths[0], cornerless, 1.5) == {}


# --- rounds that outgrow their side -------------------------------------------


def overruns(outline, factor):
    paths, corners = find(outline)
    tight = cornerfit.overruns(paths, corners, [factor] * len(corners))
    return [corners.index(c) for c in tight]


def test_two_rounds_on_one_side_that_would_cross():
    square = rect(0, 0, 300, 300, r=100)
    assert overruns(square, 1.4) == []
    assert overruns(square, 2.0) == [0, 1, 2, 3]


def test_two_rounds_that_would_meet_leave_no_straight_between_them():
    """At x1.5 the rounds of a 300 square meet at 150: the straight is
    gone and with it what says where its rounds' corners are."""
    square = rect(0, 0, 300, 300, r=100)
    assert overruns(square, 1.5) == [0, 1, 2, 3]
    assert overruns(square, 1.48) == []


def test_a_round_that_would_pass_a_plain_corner():
    one = rect(0, 0, 300, 300, r=100, only=[0])
    assert overruns(one, 2.9) == []
    assert overruns(one, 3.0) == [0]
    assert overruns(one, 4.0) == [0]


def test_only_the_rounds_short_of_room_are_named():
    outline = rounded([(300, 0), (300, 1200), (0, 1200), (0, 0)], {0: (100, 1), 1: (100, 1), 3: (100, 1)})
    # bottom side 300: two rounds; right side 1200: two rounds; top left plain
    assert overruns(outline, 2.0) == [0, 2]
    assert overruns(outline, 6.5) == [0, 1, 2]


def test_each_round_is_measured_with_its_own_factor():
    paths, corners = find(rect(0, 0, 300, 300, r=100))
    tight = cornerfit.overruns(paths, corners, [1.0, 1.0, 1.0, 2.5])
    # bottom left grows along the bottom and the left side, into its neighbours' room
    assert [corners.index(c) for c in tight] == [0, 2, 3]
    assert cornerfit.overruns(paths, corners, [1.0, 1.0, 1.0, 1.9]) == []


def test_a_round_that_does_not_grow_never_overruns():
    """Two rounds already meeting: left as they are or made smaller they
    are no worse off."""
    tight = rect(0, 0, 201, 600, r=100)
    assert overruns(tight, 1.0) == []
    assert overruns(tight, 0.5) == []
    assert overruns(tight, 1.01) == [0, 1, 2, 3]


# --- the same rounds in every layer ---------------------------------------------


def test_structure_tells_layers_apart():
    a = [Path_(rect(100, 0, 900, 1200, r=100))]
    b = [Path_(rect(50, -12, 700, 1000, r=30))]
    c = [Path_(rect(100, 0, 900, 1200, r=100, pieces=2))]
    d = [Path_(rect(100, 0, 900, 1200, r=100), closed=False)]
    assert cornerfit.structure(a) == cornerfit.structure(b)
    assert cornerfit.structure(a) != cornerfit.structure(c)
    assert cornerfit.structure(a) != cornerfit.structure(d)
    assert cornerfit.structure(a) != cornerfit.structure(a + b)


def shared(outlines, **kwargs):
    lists = [cornerfit.find_corners([Path_(o)]) for o in outlines]
    chosen, disputed = cornerfit.shared_corners(lists, **kwargs)
    return [[c["t1_index"] for c in layer] for layer in chosen], disputed


def test_rounds_found_in_every_layer_are_shared():
    assert shared([rect(100, 0, 900, 1200, r=100), rect(100, -12, 900, 1200, r=60)]) == (
        [[0, 4, 8, 12], [0, 4, 8, 12]], 0)


def test_a_round_missing_from_one_layer_is_left_out_of_all():
    """In one layer the straight up the right side has no length, so
    nothing says where the corners of the two rounds on it are."""
    met = rect(100, 0, 900, 1200, r=100)
    met[4] = (900, 100, "line")
    assert [c["t1_index"] for c in cornerfit.find_corners([Path_(met)])] == [8, 12]
    assert shared([rect(100, 0, 900, 1200, r=100), met]) == ([[8, 12], [8, 12]], 2)


def test_baseline_rounds_are_those_on_the_baseline_in_every_layer():
    on, below = rect(100, 0, 900, 1200, r=100), rect(100, -12, 900, 1200, r=100)
    assert shared([on, on], baseline_only=True) == ([[0, 12], [0, 12]], 0)
    assert shared([on, below, on], baseline_only=True) == ([[], [], []], 2)


def test_a_hairline_masters_bar_does_not_make_its_top_a_baseline_corner():
    """A bottom bar 1 unit thick has all four corners within reach of the
    baseline; at 275 thick only two of them are."""
    hair, bold = rect(100, 0, 900, 1, r=0.4), rect(100, 0, 900, 275, r=40)
    assert shared([hair], baseline_only=True) == ([[0, 4, 8, 12]], 0)
    assert shared([hair, bold], baseline_only=True) == ([[0, 12], [0, 12]], 2)


def test_one_layer_decides_for_itself():
    assert shared([rect(100, -12, 900, 1200, r=100)], baseline_only=True) == ([[]], 0)
    assert shared([], baseline_only=True) == ([], 0)
