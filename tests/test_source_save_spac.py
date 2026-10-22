"""A source save carries the mapping-only values to the studio (F2).

SPAC is a mapping-only value: a column in the mappings file with no axis in
the `.glyphs`. ``update_instance_in_glyphs`` therefore cannot carry it, and
the source→CSV sync only refreshes axis columns — so the full source save
used to drop a SPAC draft, leaving the row red after "Save to source file"
even though the row was in the source (findings.md, "Designer Part B
finding", F2).

The update endpoint now writes the mapping-only remainder of the row itself,
while the axis columns stay the sync's business (it must not skip the row).
"""

import csv

import pytest

from avar2_studio import server, state


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def harness(monkeypatch, tmp_path):
    calls = []
    source = tmp_path / "orig.glyphs"
    csv_path = tmp_path / "F-avar.csv"
    csv_path.write_text(
        "Instance Name,XTRA,SPAC\nDefault,663.1,51.2\n", encoding="utf-8")

    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    monkeypatch.setattr(state, "GLYPHS_PATH", source)
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(server._source_font, "load_source_cached",
                        lambda _p: (object(), "glyphs"))
    monkeypatch.setattr(server._source_font, "get_source_instances",
                        lambda _f: [{"name": "Default"}])
    monkeypatch.setattr(server._source_font, "get_axes",
                        lambda _f: [{"tag": "XTRA"}])
    monkeypatch.setattr(server, "update_instance_in_glyphs",
                        lambda p, n, c: calls.append(("update", dict(c))) or True)
    monkeypatch.setattr(server, "_resolve_active_source",
                        lambda: calls.append(("resolve",)))

    def _sync(*args, **kwargs):
        calls.append(("csv-sync", kwargs))
        return False

    monkeypatch.setattr(server._csv_io, "update_csv_from_glyphs", _sync)
    monkeypatch.setattr(server, "_get_preview_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_get_avar2_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_update_csv_modification_time", lambda _p: None)
    monkeypatch.setattr(server, "trigger_build", lambda: True)
    return calls, csv_path


def _read(csv_path):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return {r["Instance Name"]: r for r in csv.DictReader(f)}


def test_source_save_writes_spac_to_the_studio(client, harness):
    calls, csv_path = harness
    resp = client.put("/api/instance/Default", json={
        "coordinates": {"XTRA": 700.0, "SPAC": 63.5},
    })
    assert resp.status_code == 200, resp.get_json()

    writeback = next(c for c in calls if c[0] == "update")[1]
    assert writeback == {"XTRA": 700.0}, (
        "the source file can only take source axes; SPAC is mapping-only")

    rows = _read(csv_path)
    assert rows["Default"]["SPAC"] == "63.5"
    # The axis columns stay the source->CSV sync's job, so this row must
    # NOT be skipped: skipping it would leave the stale XTRA behind.
    sync = next(c for c in calls if c[0] == "csv-sync")[1]
    assert "Default" not in (sync.get("skip_instances") or set())


def test_a_source_save_without_mapping_only_values_keeps_the_spac(client, harness):
    calls, csv_path = harness
    resp = client.put("/api/instance/Default", json={
        "coordinates": {"XTRA": 720.0},
    })
    assert resp.status_code == 200, resp.get_json()
    rows = _read(csv_path)
    assert rows["Default"]["SPAC"] == "51.2", "untouched SPAC must survive"
