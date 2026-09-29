"""Slant Master's per-layer pipeline on duck-typed path objects: shear,
extrema insertion/removal and handle harmonisation, without Glyphs. The
fakes answer no private selector, so the node-list rebuild fallback is
what runs — the path Glyphs takes when a selector is missing."""

from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path

RESOURCES = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
             / "SlantMaster.glyphsReporter" / "Contents" / "Resources")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, RESOURCES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


slant_math = _load("slant_math")
_load("slant_extrema")
slant_paths = _load("slant_paths")


class Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Node:
    """GSNode stand-in: ``position`` accepts a tuple and reads back with
    ``.x``/``.y``, like the real one."""

    def __init__(self, x=0.0, y=0.0, type_="curve"):
        self._pos = Pt(x, y)
        self.type = type_

    @property
    def position(self):
        return self._pos

    @position.setter
    def position(self, value):
        self._pos = value if isinstance(value, Pt) else Pt(float(value[0]), float(value[1]))


# The rebuild fallback creates nodes through GlyphsApp.GSNode; outside
# Glyphs the stand-in above plays that part.
sys.modules["GlyphsApp"] = types.SimpleNamespace(GSNode=Node)


class FakePath:
    def __init__(self, nodes, closed=True):
        self.nodes = nodes
        self.closed = closed


class FakeLayer:
    def __init__(self, paths, width=600.0):
        self.paths = paths
        self.components = []
        self.width = width

    def applyTransform(self, m):
        for p in self.paths:
            for n in p.nodes:
                x, y = slant_math.transform_point(m, n.position.x, n.position.y)
                n.position = Pt(x, y)


def circle(r=200.0, cx=300.0, cy=300.0):
    """A 4-segment cubic circle in Glyphs node order (offcurve, offcurve,
    curve …), extrema at 3/12/9/6 o'clock."""
    k = 4.0 / 3.0 * (math.sqrt(2.0) - 1.0) * r
    pts = [
        (cx + r, cy), (cx + r, cy + k), (cx + k, cy + r), (cx, cy + r),
        (cx - k, cy + r), (cx - r, cy + k), (cx - r, cy),
        (cx - r, cy - k), (cx - k, cy - r), (cx, cy - r),
        (cx + k, cy - r), (cx + r, cy - k),
    ]
    nodes = []
    for i, (x, y) in enumerate(pts):
        nodes.append(Node(x, y, "curve" if i % 3 == 0 else "offcurve"))
    # Glyphs stores a closed contour with its first on-curve LAST: rotate
    # so the list ends on an on-curve node.
    nodes = nodes[1:] + nodes[:1]
    return FakePath(nodes, closed=True)


def _extrema_x_positions(path):
    xs = [n.position.x for n in path.nodes if n.type != "offcurve"]
    return min(xs), max(xs)


def test_plain_shear_keeps_node_count_and_moves_points():
    layer = FakeLayer([circle()])
    before = [(n.position.x, n.position.y) for n in layer.paths[0].nodes]
    stats = slant_paths.slant_layer(layer, slant_math.shear_transform(12.0), fix_extrema=False)
    after = [(n.position.x, n.position.y) for n in layer.paths[0].nodes]
    assert len(after) == len(before) == 12
    assert after != before
    assert stats["inserted"] == stats["removed"] == 0


def test_fix_extrema_restores_true_left_right_extrema():
    layer = FakeLayer([circle()])
    logs = []
    m = slant_math.shear_transform(12.0, origin_y=300.0)
    stats = slant_paths.slant_layer(layer, m, fix_extrema=True, log=logs.append)
    path = layer.paths[0]
    # Two new extrema (left and right of the sheared ellipse) …
    assert stats["inserted"] == 2, stats
    # … and the two stale 3/9 o'clock nodes merged away (cubic+cubic within gate).
    assert stats["removed"] == 2, (stats, logs)
    assert len(path.nodes) == 12, "node count returns to the original"
    # The new on-curve extrema carry (near-)vertical tangents: every
    # on-curve node's neighbours in x sit inside the node's x for the
    # leftmost / rightmost node.
    lo, hi = _extrema_x_positions(path)
    xs_all = [n.position.x for n in path.nodes]
    assert min(xs_all) >= lo - 0.5 and max(xs_all) <= hi + 0.5
    assert not logs, logs  # the rebuild fallback ran silently


def test_structure_change_is_seen_when_the_node_count_is_not():
    """+2 −2 leaves twelve nodes, but the list no longer starts where the
    uprights' does — counting nodes would call that compatible."""
    layer = FakeLayer([circle()])
    before = slant_paths.layer_structure(layer)
    m = slant_math.shear_transform(12.0, origin_y=300.0)
    stats = slant_paths.slant_layer(layer, m, fix_extrema=True)
    assert stats["inserted"] == stats["removed"] == 2
    assert len(layer.paths[0].nodes) == 12
    assert slant_paths.layer_structure(layer) != before
    assert stats["structure_changed"] is True


def test_plain_shear_keeps_the_structure():
    layer = FakeLayer([circle()])
    before = slant_paths.layer_structure(layer)
    stats = slant_paths.slant_layer(layer, slant_math.shear_transform(12.0), fix_extrema=False)
    assert slant_paths.layer_structure(layer) == before
    assert stats["structure_changed"] is False


def test_summary_and_positions_feed_the_overlay():
    layer = FakeLayer([circle()])
    stats = slant_paths.slant_layer(layer, slant_math.shear_transform(10.0), fix_extrema=True)
    assert len(stats["inserted_positions"]) == stats["inserted"]
    assert len(stats["removed_positions"]) == stats["removed"]
    assert "extrema +2" in slant_paths.extrema_summary(stats)


def test_ink_span_finds_the_extremes_between_the_nodes():
    """A sheared circle is widest between its nodes; the span has to
    come from the curve, not from the node positions."""
    layer = FakeLayer([circle(r=200.0, cx=300.0, cy=300.0)])
    assert slant_paths.ink_span(layer) == (100.0, 500.0)
    tan = math.tan(math.radians(12.0))
    slant_paths.slant_layer(layer, slant_math.shear_transform(12.0, origin_y=300.0))
    nodes = [n.position.x for n in layer.paths[0].nodes if n.type != "offcurve"]
    left, right = slant_paths.ink_span(layer)
    reach = 200.0 * math.sqrt(1.0 + tan * tan)
    assert (left, right) == (approx(300.0 - reach), approx(300.0 + reach))
    assert right > max(nodes) + 1.0 and left < min(nodes) - 1.0


def test_ink_span_along_the_slant_is_the_upright_span():
    """Un-slanted by the same angle around the same height, the sheared
    outline measures what the upright did."""
    layer = FakeLayer([circle(r=200.0, cx=300.0, cy=300.0)])
    slant_paths.slant_layer(layer, slant_math.shear_transform(12.0, origin_y=300.0))
    left, right = slant_paths.ink_span(layer, 12.0, 300.0)
    assert (left, right) == (approx(100.0), approx(500.0))
    # Measured around a lower height the whole span moves left; its
    # width stays.
    lower = slant_paths.ink_span(layer, 12.0, 0.0)
    shift = math.tan(math.radians(12.0)) * 300.0
    assert lower == (approx(100.0 - shift), approx(500.0 - shift))


def test_ink_span_of_a_layer_without_outlines():
    assert slant_paths.ink_span(FakeLayer([])) is None


def approx(value):
    import pytest
    return pytest.approx(value, abs=0.1)  # a four-arc circle is round to about 0.03%


# --- rounding to the grid ---------------------------------------------------


def _coords(layer):
    return [(n.position.x, n.position.y) for p in layer.paths for n in p.nodes]


def _on_grid(values, grid):
    return all(abs(v / grid - round(v / grid)) < 1e-9 for pair in values for v in pair)


def test_a_sheared_layer_is_off_the_grid_until_it_is_rounded():
    layer = FakeLayer([circle()])
    slant_paths.slant_layer(layer, slant_math.shear_transform(12.0, origin_y=300.0))
    assert not _on_grid(_coords(layer), 1.0)


def test_slant_layer_rounds_nodes_to_the_grid():
    exact = FakeLayer([circle()])
    layer = FakeLayer([circle()])
    m = slant_math.shear_transform(12.0, origin_y=300.0)
    slant_paths.slant_layer(exact, m)
    slant_paths.slant_layer(layer, m, grid=1.0)
    assert _on_grid(_coords(layer), 1.0)
    moved = max(max(abs(a - c), abs(b - d)) for (a, b), (c, d) in zip(_coords(layer), _coords(exact)))
    assert 0.0 < moved <= 0.5, "to the nearest grid point, no further"


def test_a_finer_grid():
    layer = FakeLayer([circle()])
    slant_paths.slant_layer(layer, slant_math.shear_transform(12.0, origin_y=300.0), grid=0.5)
    assert _on_grid(_coords(layer), 0.5)
    assert not _on_grid(_coords(layer), 1.0)


def test_halves_go_up_as_in_glyphs():
    """Measured on Glyphs 3.5.1's roundCoordinates: 0.5 → 1, 2.5 → 3,
    −0.5 → 0, −1.5 → −1, −2.5 → −2."""
    nodes = [Node(0.5, 1.5, "line"), Node(2.5, -0.5, "line"), Node(-1.5, -2.5, "line")]
    layer = FakeLayer([FakePath(nodes)])
    slant_paths.round_to_grid(layer, 1.0)
    assert _coords(layer) == [(1.0, 2.0), (3.0, 0.0), (-1.0, -2.0)]


def test_anchors_are_rounded_with_the_nodes():
    layer = FakeLayer([circle()])
    layer.anchors = [Node(300.0, 500.0, "top")]

    def shear_anchors_too(m, plain=layer.applyTransform):  # the layer's transform moves its anchors
        plain(m)
        for a in layer.anchors:
            a.position = Pt(*slant_math.transform_point(m, a.position.x, a.position.y))

    layer.applyTransform = shear_anchors_too
    slant_paths.slant_layer(layer, slant_math.shear_transform(12.0, origin_y=300.0), grid=1.0)
    x = 300.0 + math.tan(math.radians(12.0)) * 200.0
    assert (layer.anchors[0].position.x, layer.anchors[0].position.y) == (math.floor(x + 0.5), 500.0)


def test_a_kept_component_lands_on_the_grid():
    layer = FakeLayer([])
    layer.components = [types.SimpleNamespace(transform=(1.0, 0.0, 0.0, 1.0, 120.0, 700.0))]
    slant_paths.slant_layer(layer, slant_math.shear_transform(12.0, origin_y=300.0),
                            keep_components=True, grid=1.0)
    x = 120.0 + math.tan(math.radians(12.0)) * 700.0  # the translation is sheared, the pivot is not in it
    assert layer.components[0].transform == (1.0, 0.0, 0.0, 1.0, math.floor(x + 0.5), 700.0)
