"""/api/instances merges the CSV (studio layer) over source rows.

Regression: an instance coordinate saved with the flyout's "Save to
studio" (csv_only) landed in the CSV but the listing kept serving the
SOURCE file's coordinates — after a reload the saved edit was invisible.
Now CSV cells matching a source axis tag (plus SPAC) override the
displayed coordinates, mapping columns (WGHT/OPSZ…) stay display-only,
blank cells leave the source value standing, and the pristine source
set rides along as ``source_coordinates`` for the sync dot."""

from pathlib import Path

import pytest

from avar2_studio import server


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def test_csv_out_columns_override_source_coordinates(client, monkeypatch, tmp_path):
    csv_path = tmp_path / "F-avar.csv"
    csv_path.write_text(
        "Instance Name,XTRA,XOPQ,YOPQ,WGHT,SPAC\n"
        "Default 400,,210.0,157.0,400.0,12\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "GLYPHS_PATH", tmp_path / "F.glyphs")
    monkeypatch.setattr(server, "get_instances_from_glyphs", lambda _p: [
        {"name": "Default 400",
         "coordinates": {"XTRA": 663.1, "XOPQ": 202.5, "YOPQ": 121.8},
         "origin": "source"},
    ])
    monkeypatch.setattr(server, "get_axes_from_glyphs", lambda _p: [
        {"tag": "XTRA"}, {"tag": "XOPQ"}, {"tag": "YOPQ"},
    ])
    monkeypatch.setattr(server, "_get_preview_csv_path", lambda: csv_path)

    r = client.get("/api/instances")
    assert r.status_code == 200
    (inst,) = r.get_json()["instances"]
    coords = inst["coordinates"]
    assert coords["YOPQ"] == 157.0, "the studio-saved value must come back"
    assert coords["XOPQ"] == 210.0
    assert coords["XTRA"] == 663.1, "a blank CSV cell leaves the source value"
    assert coords["SPAC"] == 12.0, "SPAC merges as before"
    assert "WGHT" not in coords, "mapping columns are not coordinates"
    assert inst["source_coordinates"] == {
        "XTRA": 663.1, "XOPQ": 202.5, "YOPQ": 121.8,
    }, "the pristine source set rides along for the sync dot"
