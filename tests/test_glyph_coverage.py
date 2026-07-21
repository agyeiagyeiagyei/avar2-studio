"""Glyph-coverage reads: master coverage vs. per-glyph scoped variation.

``compute_coverage`` is the read API the frontend classifies axes with:
100% coverage → ``universal`` (stays a parametric slider), anything
less → ``scoped`` (surfaces under CONTROL AXES). These tests pin that
classification against the two shipped example sources, plus the
design-space vs. user-space split in layer locations and the
``source``/``studio`` tagging the coverage endpoint overlays from the
control-axes sidecar.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from avar2_studio import control_axes, glyph_coverage, source_font


def _coverage(source_path: Path):
    """Load a source file and run compute_coverage on it."""
    font, _fmt = source_font.load_source(source_path)
    return glyph_coverage.compute_coverage(font)


# ----------------------------------------------------------------------
# crispy-mini (.glyphs) — every axis is spanned by the master grid
# ----------------------------------------------------------------------


def test_crispy_axes_are_universal_with_full_master_coverage(crispy_source):
    """All three Crispy Mini axes sit on the master grid → universal."""
    cov = _coverage(crispy_source)

    assert set(cov) == {"XTRA", "XOPQ", "YOPQ"}
    for tag, (lo, dflt, hi) in {
        # min/default/max in design space; .glyphs has no explicit
        # default, so the first master's coordinates stand in for it.
        "XTRA": (94.0, 94.0, 3330.0),
        "XOPQ": (2.0, 2.0, 1016.0),
        "YOPQ": (2.0, 2.0, 462.0),
    }.items():
        info = cov[tag]
        assert info["kind"] == "universal"
        assert info["covers_count"] == info["total_glyphs"] == 63
        assert info["covers"] == sorted(info["covers"])  # stable ordering
        assert {"A", "e", "eight", "space"} <= set(info["covers"])
        assert (info["min"], info["default"], info["max"]) == (lo, dflt, hi)


def test_crispy_brace_layers_record_full_sparse_locations(crispy_source):
    """Crispy's existing brace layers deviate on all three axes at once,
    so each universal axis also carries the same 42 layer entries —
    layers record intermediate variation on TOP of master coverage."""
    cov = _coverage(crispy_source)

    layers = cov["XOPQ"]["layers"]
    assert len(layers) == 42
    sample = layers[0]
    assert set(sample) == {"glyph", "location", "location_user"}
    assert set(sample["location"]) == {"XTRA", "XOPQ", "YOPQ"}
    # .glyphs has no user/design split in this pipeline — the two
    # locations are identical by construction.
    assert sample["location_user"] == sample["location"]
    # Same brace layer seen from the other two axes.
    assert cov["XTRA"]["layers"] == layers
    assert cov["YOPQ"]["layers"] == layers


def test_crispy_glyph_chars_map_names_to_characters(crispy_source):
    """Layer thumbnails render as text, so names need their codepoint."""
    font, _fmt = source_font.load_source(crispy_source)
    chars = glyph_coverage.compute_glyph_chars(font)

    assert len(chars) == 63  # every Crispy Mini glyph has a unicode
    assert chars["A"] == "A"
    assert chars["eight"] == "8"  # multi-char name → its codepoint
    assert chars["space"] == " "


# ----------------------------------------------------------------------
# roboto-delta-mini (.designspace) — case-split axes are glyph-scoped
# ----------------------------------------------------------------------


def test_roboto_axes_are_all_scoped(roboto_source):
    """Roboto Delta's case-split UFOs vary only their own glyph subset,
    so none of the nine axes reaches universal coverage."""
    cov = _coverage(roboto_source)

    assert set(cov) == {
        "XOUC", "YOUC", "XTUC", "XOLC", "YOLC", "XTLC", "XOFI", "YOFI", "XTFI",
    }
    for info in cov.values():
        assert info["kind"] == "scoped"
        assert 0 < info["covers_count"] < info["total_glyphs"]
        assert info["total_glyphs"] == 67
    # Spot-pin the two ends of the coverage spread.
    assert cov["XOUC"]["covers_count"] == 26
    assert cov["YOFI"]["covers_count"] == 8


def test_roboto_layers_are_sparse_and_single_axis(roboto_source):
    """Each alternate master deviates on exactly one axis, so every
    layer location is a single-tag design-space pin."""
    cov = _coverage(roboto_source)

    xouc = cov["XOUC"]
    assert len(xouc["layers"]) == 52  # 26 glyphs × the XOUC2/XOUC310 masters
    for entry in xouc["layers"]:
        assert set(entry) == {"glyph", "location", "location_user"}
        assert set(entry["location"]) == {"XOUC"}
        assert entry["location"]["XOUC"] in (2.0, 310.0)
    # Axis extremes come through in design space alongside the layers.
    assert (xouc["min"], xouc["default"], xouc["max"]) == (2.0, 96.0, 310.0)


def test_roboto_location_user_mirrors_location(roboto_source):
    """This fixture ships NO <map> elements on any axis (identity maps),
    so user space IS design space here: every layer's location_user
    equals its location, and the documented non-invertible case
    (location_user=None) does not occur in this fixture — see the
    synthetic-map test below for that path."""
    cov = _coverage(roboto_source)

    layers = [e for info in cov.values() for e in info["layers"]]
    assert layers  # sanity: the fixture does have layers
    for entry in layers:
        assert entry["location_user"] == entry["location"]


# ----------------------------------------------------------------------
# design vs. user space + the non-invertible map guard — the shipped
# fixtures contain no <map> elements at all, so a minimal synthetic
# designspace pins both behaviours the read API documents.
# ----------------------------------------------------------------------


def _write_stub_ufo(ufo_dir: Path, glyph_widths: dict) -> None:
    """Write just enough UFO for the coverage reader: contents.plist +
    one glif per glyph (it only ever slurps glif bytes)."""
    glyphs = ufo_dir / "glyphs"
    glyphs.mkdir(parents=True)
    contents = {name: f"{name}.glif" for name in glyph_widths}
    with (glyphs / "contents.plist").open("wb") as f:
        plistlib.dump(contents, f)
    for name, width in glyph_widths.items():
        (glyphs / f"{name}.glif").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<glyph name="{name}" format="2">\n'
            f'  <advance width="{width}"/>\n'
            "  <outline/>\n"
            "</glyph>\n",
            encoding="utf-8",
        )


_DESIGNSPACE_XML = """<?xml version="1.0" encoding="utf-8"?>
<designspace format="5.0">
  <axes>
    <!-- Non-monotonic map: output rises 0→100 then falls back to 50. -->
    <axis tag="TEST" name="TEST" minimum="0" maximum="200" default="0">
      <map input="0" output="0"/>
      <map input="100" output="100"/>
      <map input="200" output="50"/>
    </axis>
    <!-- Ordinary invertible map: user 0..100 ↔ design 0..50. -->
    <axis tag="MAPD" name="MAPD" minimum="0" maximum="100" default="0">
      <map input="0" output="0"/>
      <map input="100" output="50"/>
    </axis>
  </axes>
  <sources>
    <source filename="Def.ufo" familyname="Mini" stylename="Default">
      <location>
        <dimension name="TEST" xvalue="0"/>
        <dimension name="MAPD" xvalue="0"/>
      </location>
    </source>
    <source filename="Alt.ufo" familyname="Mini" stylename="Alt">
      <location>
        <dimension name="TEST" xvalue="25"/>
        <dimension name="MAPD" xvalue="50"/>
      </location>
    </source>
    <source filename="Alt2.ufo" familyname="Mini" stylename="Alt2">
      <location>
        <dimension name="TEST" xvalue="0"/>
        <dimension name="MAPD" xvalue="50"/>
      </location>
    </source>
  </sources>
</designspace>
"""


@pytest.fixture
def mapped_designspace(tmp_path):
    """A two-axis designspace with real (and one non-invertible) maps."""
    doc_path = tmp_path / "Mini.designspace"
    doc_path.write_text(_DESIGNSPACE_XML, encoding="utf-8")
    _write_stub_ufo(tmp_path / "Def.ufo", {"A": 500, "B": 500})
    _write_stub_ufo(tmp_path / "Alt.ufo", {"A": 700})
    _write_stub_ufo(tmp_path / "Alt2.ufo", {"A": 900})
    return doc_path


def test_non_invertible_map_yields_location_user_none(mapped_designspace):
    """Design 25 on the non-monotonic TEST map inverts to user 100,
    which forward-maps to 100 ≠ 25 — the round-trip guard gives up and
    reports location_user=None for that layer (navigation must skip it).
    Note the None is per LAYER: one bad axis poisons the whole
    location, since a partial user-space location would land Fontra
    somewhere wrong."""
    cov = _coverage(mapped_designspace)

    test_layers = cov["TEST"]["layers"]
    assert len(test_layers) == 1
    assert test_layers[0]["glyph"] == "A"
    assert test_layers[0]["location"] == {"MAPD": 50.0, "TEST": 25.0}
    assert test_layers[0]["location_user"] is None


def test_invertible_map_separates_design_and_user_locations(mapped_designspace):
    """The MAPD axis maps user 100 ↔ design 50, so the Alt2 master
    reports design-space location and user-space location_user as
    genuinely different numbers."""
    cov = _coverage(mapped_designspace)

    mapd_layers = cov["MAPD"]["layers"]
    assert len(mapd_layers) == 2  # Alt (with TEST) + Alt2 (MAPD only)
    pure = next(e for e in mapd_layers if set(e["location"]) == {"MAPD"})
    assert pure["location"] == {"MAPD": 50.0}
    assert pure["location_user"] == {"MAPD": 100.0}
    # Design extremes come from the map OUTPUTS, so the non-monotonic
    # bump (not forward(max)) sets TEST's design-space max.
    assert cov["TEST"]["max"] == 100.0
    assert cov["MAPD"]["max"] == 50.0


# ----------------------------------------------------------------------
# /api/glyph-coverage — the endpoint overlays the control-axes sidecar
# and tags each axis ``source`` (from the file) or ``studio`` (declared
# in the sidecar). That's the flag gating the UI's edit affordances.
# ----------------------------------------------------------------------


@pytest.fixture
def coverage_client(monkeypatch):
    """Flask test client with the server pointed at a tmp source."""
    import avar2_studio.server as server

    def set_paths(original, active):
        monkeypatch.setattr(server, "ORIGINAL_PATH", original)
        monkeypatch.setattr(server, "GLYPHS_PATH", active)

    return server.app.test_client(), set_paths


def test_endpoint_tags_source_derived_axes_as_source(coverage_client, crispy_source):
    """With no sidecar, every axis comes straight from the file."""
    client, set_paths = coverage_client
    set_paths(crispy_source, crispy_source)

    body = client.get("/api/glyph-coverage").get_json()
    assert body["glyph_chars"]["A"] == "A"
    assert {ax["tag"] for ax in body["axes"]} == {"XTRA", "XOPQ", "YOPQ"}
    for ax in body["axes"]:
        assert ax["source"] == "source"
        assert ax["kind"] == "universal"


def test_endpoint_appends_sidecar_axis_as_studio(coverage_client, crispy_source):
    """A sidecar-declared axis the source doesn't have is appended with
    source: "studio"; the source-derived axes keep their tagging."""
    client, set_paths = coverage_client
    control_axes.add_axis(crispy_source, "crbr", "Crossbar", 0, -100, 100)
    set_paths(crispy_source, crispy_source)

    body = client.get("/api/glyph-coverage").get_json()
    by_tag = {ax["tag"]: ax for ax in body["axes"]}
    studio = by_tag["crbr"]
    assert studio["source"] == "studio"
    assert studio["name"] == "Crossbar"
    assert (studio["min"], studio["default"], studio["max"]) == (-100.0, 0.0, 100.0)
    assert studio["covers"] == []  # no layers declared yet
    assert studio["kind"] == "scoped"
    assert by_tag["XOPQ"]["source"] == "source"


def test_endpoint_retags_shadow_derived_axis_as_studio(coverage_client, crispy_source):
    """Once the shadow exists, the axis IS in the (shadow) file — the
    sidecar still wins the tagging, flipping source → studio and
    overlaying the sidecar's metadata + declared layers."""
    client, set_paths = coverage_client
    control_axes.add_axis(crispy_source, "crbr", "Crossbar", 0, -100, 100)
    control_axes.apply_layer_delta(
        crispy_source, "crbr", add=[{"glyph": "e", "location": {"crbr": 100}}]
    )
    shadow = control_axes.regenerate_shadow(crispy_source)
    set_paths(crispy_source, shadow)

    body = client.get("/api/glyph-coverage").get_json()
    by_tag = {ax["tag"]: ax for ax in body["axes"]}
    flipped = by_tag["crbr"]
    assert flipped["source"] == "studio"
    assert flipped["covers"] == ["e"]
    assert flipped["kind"] == "scoped"  # 1 of 63 glyphs
    # Sidecar range overlays the shadow's master-derived [0, 100].
    assert (flipped["min"], flipped["default"], flipped["max"]) == (-100.0, 0.0, 100.0)
    assert flipped["layers"] == [{"glyph": "e", "location": {"crbr": 100.0}}]
    # The real parametric axes are untouched by the overlay.
    assert by_tag["XOPQ"]["source"] == "source"
    assert by_tag["XOPQ"]["kind"] == "universal"
