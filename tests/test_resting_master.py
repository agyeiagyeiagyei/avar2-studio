"""E2l: the export dialog asks the backend which master the font rests on.

The dialog must not guess from a master list, and the two notes it shows hinge
on the same fact: the size warning appears whenever the resting location is not
the source's DEFAULT master (the studio rebuilds the variations around the
resting style), and the sources note is false when it is.
"""

import pytest
from glyphsLib.classes import GSAxis, GSFont, GSFontMaster

from avar2_studio import csv_io, server, state


def _font(tmp_path, origin=None, origin_name=None):
    """Weight has master coverage (the CSV's parametric ``out`` side), Width
    does not (its ``in`` side). Thin is first; ``origin`` sets the Variable Font
    Origin by master id, ``origin_name`` the old name-keyed parameter."""
    font = GSFont()
    font.familyName = "Resting"
    for name in ("Weight", "Width"):
        axis = GSAxis()
        axis.name = name
        axis.axisTag = name[:4].lower()
        font.axes.append(axis)
    for name, master_id, coords in (("Thin", "M1", (47, 100)),
                                    ("Max", "M2", (1715, 100))):
        master = GSFontMaster()
        master.name = name
        master.id = master_id
        master.axes = list(coords)
        font.masters.append(master)
    if origin is not None:
        font.customParameters["Variable Font Origin"] = origin
    if origin_name is not None:
        font.customParameters["Variation Font Origin"] = origin_name
    path = tmp_path / "Resting.glyphs"
    font.save(str(path))
    return path


def _csv(tmp_path, origin_wght):
    path = tmp_path / "Resting-avar.csv"
    path.write_text("Instance Name,WDTH,WGHT\n"
                    f"Origin,100,{origin_wght}\n"
                    "Max,100,1715\n")
    return path


def test_the_default_master_is_the_one_at_the_axis_defaults(tmp_path):
    assert csv_io.default_master_name(_font(tmp_path)) == "Thin"


def test_the_variable_font_origin_names_the_default_master(tmp_path):
    """The origin is the compiler's answer, not the axis minima: a copy whose
    origin sits on the second master must report THAT master, or the dialog
    warns about a larger file on the one build that is exact."""
    assert csv_io.default_master_name(_font(tmp_path, origin="M2")) == "Max"


def test_the_old_name_keyed_origin_also_works(tmp_path):
    assert csv_io.default_master_name(
        _font(tmp_path, origin_name="Max")) == "Max"


def test_a_dangling_origin_falls_back_to_the_first_master(tmp_path):
    """Crispy's origin names a master id that is not in the saved file."""
    assert csv_io.default_master_name(
        _font(tmp_path, origin="NOT-A-MASTER")) == "Thin"


def test_no_origin_at_all_falls_back_to_the_first_master(tmp_path):
    assert csv_io.default_master_name(_font(tmp_path)) == "Thin"


def test_the_coincide_copy_reports_the_default_master(tmp_path):
    """The regression the verifier measured: a build whose ORIGIN row lands on
    the origin master is exact and must stay silent."""
    source = _font(tmp_path, origin="M2")
    report = csv_io.resting_master_report(source, None, _csv(tmp_path, 1715))
    assert report["default_master"] == "Max"
    assert report["status"] == "default_master", report


def test_a_location_on_the_default_master_needs_no_warning(tmp_path):
    report = csv_io.resting_master_report(_font(tmp_path), {"wght": 47})
    assert report["status"] == "default_master"
    assert report["name"] == "Thin"
    assert report["default_master"] == "Thin"


def test_a_location_on_another_master_names_it(tmp_path):
    """Another master still relocates — the file grows — so the warning shows,
    and the copy can tell the designer which master to make the default."""
    report = csv_io.resting_master_report(_font(tmp_path), {"wght": 1715})
    assert report["status"] == "other_master"
    assert report["name"] == "Max"
    assert report["default_master"] == "Thin"


def test_a_location_off_every_master_says_so(tmp_path):
    report = csv_io.resting_master_report(_font(tmp_path), {"wght": 663.1})
    assert report["status"] == "no_master"
    assert report["default_master"] == "Thin"
    assert report["max_delta"] == 616.1


def test_the_origin_row_is_resolved_from_the_csv(tmp_path):
    """With no location the report reads the ORIGIN row — the row whose in
    axes are all at their defaults — so the dialog need not re-derive it."""
    source = _font(tmp_path)
    assert csv_io.origin_row_out(source, _csv(tmp_path, 47)) == {"wght": 47.0}
    report = csv_io.resting_master_report(source, None, _csv(tmp_path, 47))
    assert report["status"] == "default_master"
    off = csv_io.resting_master_report(source, None, _csv(tmp_path, 663.1))
    assert off["status"] == "no_master"


def test_without_a_csv_and_without_a_location_the_status_is_unknown(tmp_path):
    report = csv_io.resting_master_report(_font(tmp_path))
    assert report["status"] == "unknown"


def test_a_source_with_no_master_at_the_default_falls_back_to_the_first(
        monkeypatch, tmp_path):
    """glyphsLib's own fallback: when the origin names nothing that exists,
    the first master is the default — no axis-default hunt, no id guessing."""
    source = _font(tmp_path, origin="A32E53B3-DANGLING")
    assert csv_io.default_master_name(source) == "Thin"


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def test_the_route_classifies_the_location_it_is_given(client, monkeypatch,
                                                       tmp_path):
    source = _font(tmp_path)
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    # No built font: the route takes the location as it stands instead of
    # mapping it through avar2 (another test may leave one behind).
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", None)
    res = client.post("/api/resting-master", json={"location": {"wght": 1715}})
    assert res.status_code == 200
    assert res.get_json()["status"] == "other_master"
    assert res.get_json()["name"] == "Max"


def test_the_route_falls_back_to_the_origin_row(client, monkeypatch, tmp_path):
    source = _font(tmp_path)
    _csv(tmp_path, 47)
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", None)
    res = client.post("/api/resting-master", json={})
    assert res.status_code == 200
    assert res.get_json()["status"] == "default_master"


def test_the_route_rejects_a_non_numeric_location(client, monkeypatch,
                                                  tmp_path):
    monkeypatch.setattr(state, "ORIGINAL_PATH", _font(tmp_path))
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", None)
    res = client.post("/api/resting-master", json={"location": {"wght": "wide"}})
    assert res.status_code == 400


def test_the_route_maps_the_preview_location_through_avar2_first(
        client, monkeypatch, tmp_path):
    """The preview speaks wght/wdth/opsz; the master grid is parametric.

    A preview location that the built font's avar2 maps onto a non-default
    master must come back as THAT master. wght 400 maps to 1715 here, so a route
    that classified the raw preview location would answer `no_master`.
    """
    source = _font(tmp_path)
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", tmp_path / "built.ttf")
    asked = {}

    def fake_mapped(coords):
        asked.update(coords)
        return {"wght": 1715.0, "wdth": 100.0}

    monkeypatch.setattr(server, "_evaluate_mapped_location", fake_mapped)
    res = client.post("/api/resting-master",
                      json={"location": {"wght": 400, "wdth": 100}})
    assert asked == {"wght": 400, "wdth": 100}, (
        "the mapping is asked with the preview's own axes")
    body = res.get_json()
    assert body["status"] == "other_master"
    assert body["name"] == "Max", body
