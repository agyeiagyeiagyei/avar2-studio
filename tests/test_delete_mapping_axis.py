"""DELETE /api/avar2/axis/<name>: drop a mapping axis's CSV column and
metadata entry, rebuild; refuse parametric columns and unknown axes.

Regression: the static demo has had axis deletion since the
mapping-axis arc, but the desktop server never grew the endpoint — the
UI's delete affordance was dead ("can't delete … and resave")."""

import csv as csv_mod

import pytest

from avar2_studio import server


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def harness(monkeypatch, tmp_path):
    csv_path = tmp_path / "F-avar.csv"
    csv_path.write_text(
        "Instance Name,XTRA,XOPQ,WGHT,WDTH\n"
        "A,663.1,202.5,400.0,100.0\n"
        "B,500.2,316.3,900.0,100.0\n",
        encoding="utf-8",
    )
    meta = {"WDTH": {"display_name": "Width", "registered_tag": "wdth",
                     "min": 5, "default": 5, "max": 220},
            "WGHT": {"display_name": "Weight", "registered_tag": "wght",
                     "min": 1, "default": 1, "max": 1000}}
    monkeypatch.setattr(server, "GLYPHS_PATH", tmp_path / "F.glyphs")
    monkeypatch.setattr(server, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(server, "_get_avar2_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_check_csv_external_edit", lambda _p: False)
    monkeypatch.setattr(server._csv_io, "read_csv_mappings_with_axes",
                        lambda _c, _g: ([], [], ["WGHT", "WDTH"], ["XTRA", "XOPQ"], []))
    monkeypatch.setattr(server._csv_io, "backup_sidecar", lambda _p: None)
    monkeypatch.setattr(server, "_update_csv_modification_time", lambda _p: None)
    monkeypatch.setattr(server, "_load_axis_metadata", lambda: dict(meta))
    saved = {}
    monkeypatch.setattr(server, "_save_axis_metadata",
                        lambda m: saved.update(m) or saved.setdefault("__saved__", True))
    monkeypatch.setattr(server, "trigger_build", lambda: True)
    return csv_path, saved


def _columns(csv_path):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv_mod.DictReader(f)
        return list(reader.fieldnames), list(reader)


def test_delete_drops_column_and_metadata(client, harness):
    csv_path, saved = harness
    r = client.delete("/api/avar2/axis/WDTH")
    assert r.status_code == 200, r.get_json()
    fieldnames, rows = _columns(csv_path)
    assert fieldnames == ["Instance Name", "XTRA", "XOPQ", "WGHT"]
    assert rows[0]["WGHT"] == "400.0", "other columns keep their values"
    assert "WDTH" not in saved and saved.get("__saved__"), "metadata entry removed"


def test_delete_refuses_parametric_columns(client, harness):
    csv_path, _ = harness
    r = client.delete("/api/avar2/axis/XTRA")
    assert r.status_code == 403
    assert "parametric" in r.get_json()["error"]
    fieldnames, _rows = _columns(csv_path)
    assert "XTRA" in fieldnames, "nothing was touched"


def test_delete_unknown_axis_404s(client, harness):
    r = client.delete("/api/avar2/axis/NOPE")
    assert r.status_code == 404
