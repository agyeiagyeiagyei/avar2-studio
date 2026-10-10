"""Instance flows must never mint CSV mapping columns for injected or
control axes.

Regression: create_instance "evolves the CSV schema" from the axes of
the loaded build source — which is the TRANSFORMED SHADOW, whose axis
list includes the transform-injected axes (slnt/ROND/SPAC) and the
control axes (lcwd/ymod). Creating an instance appended LCWD/YMOD/SLNT/
ROND as uppercase mapping columns (back-filling every row with shadow
"defaults" like SLNT -11), which the avar2 build then compiled as NEW
user fvar axes alongside the real ones, and later failed with 'LCWD'."""

import csv as csv_mod
from pathlib import Path

import pytest

from avar2_studio import server

SHADOW_AXES = [
    {"tag": "XTRA", "default": 663.1},
    {"tag": "XOPQ", "default": 202.5},
    {"tag": "YOPQ", "default": 121.8},
    {"tag": "lcwd", "default": 0.0},   # control axis
    {"tag": "ymod", "default": 0.0},   # control axis
    {"tag": "slnt", "default": -11.0},  # transform twin extension
    {"tag": "ROND", "default": 0.0},   # transform twin extension
]


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def harness(monkeypatch, tmp_path):
    csv_path = tmp_path / "F-avar.csv"
    csv_path.write_text(
        "Instance Name,XTRA,XOPQ,YOPQ,WGHT\n"
        "Default 400,663.1,202.5,121.8,400.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "GLYPHS_PATH", tmp_path / "F.glyphs")
    monkeypatch.setattr(server, "ORIGINAL_PATH", tmp_path / "F.glyphs")
    monkeypatch.setattr(server, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(server._source_font, "load_source", lambda _p: (object(), "glyphs"))
    monkeypatch.setattr(server._source_font, "get_source_instances", lambda _f: [])
    monkeypatch.setattr(server._source_font, "get_axes", lambda _f: SHADOW_AXES)
    monkeypatch.setattr(server, "_get_preview_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_get_avar2_csv_path", lambda: csv_path)
    monkeypatch.setattr(server._control_axes, "list_axes",
                        lambda _p: [{"tag": "lcwd"}, {"tag": "ymod"}])
    monkeypatch.setattr(server, "trigger_build", lambda: True)
    monkeypatch.setattr(server, "_resolve_active_source", lambda: None)
    monkeypatch.setattr(server, "_update_csv_modification_time", lambda _p: None)
    return csv_path


def _read(csv_path):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv_mod.DictReader(f)
        return list(reader.fieldnames), list(reader)


def test_create_instance_does_not_mint_injected_axis_columns(client, harness):
    r = client.post("/api/instance", json={
        "name": "New One",
        # The duplicate flow sends the full coordinate dict, shadow
        # axes included — none of these may become columns.
        "coordinates": {"XTRA": 700, "XOPQ": 180, "YOPQ": 130,
                        "lcwd": 0, "ymod": 0, "slnt": -11, "ROND": 0},
    })
    assert r.status_code == 200, r.get_json()
    fieldnames, rows = _read(harness)
    assert fieldnames == ["Instance Name", "XTRA", "XOPQ", "YOPQ", "WGHT"], (
        "injected/control axes must not become CSV columns")
    new = next(row for row in rows if row["Instance Name"] == "New One")
    assert new["XTRA"] == "700.0"
    assert new["WGHT"] == "", "mapping columns start blank, never stamped"


def test_update_instance_drops_injected_axis_values(client, harness):
    r = client.put("/api/instance/Default%20400?csv_only=true", json={
        "coordinates": {"YOPQ": 157.0, "slnt": -11, "lcwd": 40, "ROND": 55},
    })
    assert r.status_code == 200, r.get_json()
    fieldnames, rows = _read(harness)
    assert fieldnames == ["Instance Name", "XTRA", "XOPQ", "YOPQ", "WGHT"]
    row = rows[0]
    assert row["YOPQ"] == "157.0", "the real coordinate edit persists"
    assert "SLNT" not in row and "LCWD" not in row
