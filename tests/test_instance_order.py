"""Instance row order persists in the CSV and the listing honors it.

Regression: drag-reorder (and the ⇅ move) only touched frontend state —
any refetch after a rebuild snapped the list back to source-then-CSV
order. Now PUT /api/instances/order rewrites the CSV row order, and
/api/instances sorts by it (rows without a CSV entry keep their source
order, first)."""

import csv as csv_mod

import pytest

from avar2_studio import server, state


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def harness(monkeypatch, tmp_path):
    csv_path = tmp_path / "F-avar.csv"
    csv_path.write_text(
        "Instance Name,XTRA,XOPQ\n"
        "Alpha,663.1,202.5\n"
        "Beta,500.0,300.0\n"
        "Gamma,900.0,10.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(state, "GLYPHS_PATH", tmp_path / "F.glyphs")
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    # Alpha is source-defined; Beta/Gamma are studio-only CSV rows.
    monkeypatch.setattr(server, "get_instances_from_glyphs", lambda _p: [
        {"name": "Alpha", "coordinates": {"XTRA": 663.1, "XOPQ": 202.5},
         "origin": "source"},
    ])
    monkeypatch.setattr(server, "get_axes_from_glyphs", lambda _p: [
        {"tag": "XTRA"}, {"tag": "XOPQ"},
    ])
    monkeypatch.setattr(server, "_get_preview_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_check_csv_external_edit", lambda _p: False)
    monkeypatch.setattr(server._csv_io, "backup_sidecar", lambda _p: None)
    monkeypatch.setattr(server, "_update_csv_modification_time", lambda _p: None)
    return csv_path


def _names(client):
    return [i["name"] for i in client.get("/api/instances").get_json()["instances"]]


def test_listing_follows_csv_order_and_reorder_persists(client, harness):
    assert _names(client) == ["Alpha", "Beta", "Gamma"]

    r = client.put("/api/instances/order",
                   json={"names": ["Gamma", "Alpha", "Beta"]})
    assert r.status_code == 200, r.get_json()

    with harness.open("r", encoding="utf-8-sig", newline="") as f:
        rows = [row["Instance Name"] for row in csv_mod.DictReader(f)]
    assert rows == ["Gamma", "Alpha", "Beta"], "CSV rows carry the order"
    assert _names(client) == ["Gamma", "Alpha", "Beta"], (
        "the listing — source and studio rows alike — follows the CSV order")


def test_reorder_rejects_bad_body(client, harness):
    assert client.put("/api/instances/order", json={"names": "nope"}).status_code == 400
    assert client.put("/api/instances/order", json={}).status_code == 400
