"""Per-instance SPAC persists through the update path.

The SPAC column is the one injected-axis value with a per-instance CSV
contract (builtin_spac._instance_spac_overrides reads it; /api/instances
hydrates it back). These tests pin the write side: the update endpoint
mints the column on first use and round-trips the value, without
touching other rows or columns.
"""

import csv
import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import avar2_studio.server as server
    importlib.reload(server)

    glyphs = FIXTURES / "WasmTest.glyphs"
    if not glyphs.exists():
        glyphs = Path(__file__).resolve().parents[1] / "frontend/e2e/fixtures/WasmTest.glyphs"
    source = tmp_path / "WasmTest.glyphs"
    source.write_text(glyphs.read_text())

    csv_path = tmp_path / "WasmTest-avar.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Instance Name", "WGHT"])
        w.writerow(["Alpha", "100.0"])
        w.writerow(["Beta", "400.0"])

    monkeypatch.setattr(server, "GLYPHS_PATH", source)
    monkeypatch.setattr(server, "ORIGINAL_PATH", source)
    monkeypatch.setattr(server, "_get_preview_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_get_avar2_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_check_csv_external_edit", lambda p: False)
    server.app.config["TESTING"] = True
    return server.app.test_client(), csv_path


def _read(csv_path):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames), {r["Instance Name"]: r for r in reader}


def test_spac_column_minted_and_round_tripped(client):
    c, csv_path = client
    resp = c.put("/api/instance/Alpha?csv_only=true",
                 json={"coordinates": {"wght": 120.0, "SPAC": 20.0}})
    assert resp.status_code == 200, resp.get_json()

    fieldnames, rows = _read(csv_path)
    assert "SPAC" in fieldnames
    assert rows["Alpha"]["SPAC"] == "20.0"
    assert rows["Alpha"]["WGHT"] == "120.0"
    # The untouched row gets a blank cell, never a stamped zero.
    assert rows["Beta"]["SPAC"] == ""
    assert rows["Beta"]["WGHT"] == "400.0"

    # Hydration: the instances listing carries SPAC back.
    data = c.get("/api/instances").get_json()
    alpha = next(i for i in data["instances"] if i["name"] == "Alpha")
    assert alpha["coordinates"]["SPAC"] == 20.0
    beta = next(i for i in data["instances"] if i["name"] == "Beta")
    assert "SPAC" not in beta["coordinates"]

    # Second save updates in place — no duplicate column.
    resp = c.put("/api/instance/Alpha?csv_only=true",
                 json={"coordinates": {"SPAC": -10.0}})
    assert resp.status_code == 200
    fieldnames, rows = _read(csv_path)
    assert fieldnames.count("SPAC") == 1
    assert rows["Alpha"]["SPAC"] == "-10.0"


def test_control_axis_columns_still_refused(client):
    c, csv_path = client
    resp = c.put("/api/instance/Alpha?csv_only=true",
                 json={"coordinates": {"wght": 150.0, "lcwd": 100.0}})
    assert resp.status_code == 200
    fieldnames, rows = _read(csv_path)
    assert "LCWD" not in fieldnames and "lcwd" not in fieldnames
    assert rows["Alpha"]["WGHT"] == "150.0"
