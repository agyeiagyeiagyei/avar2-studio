"""Source writebacks target the ORIGINAL file, alias-aware.

Regression: the flyout's "Save to source file", the add-to-source
promote and the source delete all wrote GLYPHS_PATH — the TRANSFORMED
SHADOW, a build artifact regenerated from the original. The "source
save" evaporated on the next regen and the user's file never changed.
They now write ORIGINAL_PATH, translate renamed instances back to
their in-file name through the alias sidecar, and re-resolve the
shadow BEFORE any CSV sync reads it (or the sync would copy the stale
values straight back over the save)."""

from pathlib import Path

import pytest

from avar2_studio import server


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def harness(monkeypatch, tmp_path):
    calls = []
    original = tmp_path / "orig.glyphs"
    shadow = tmp_path / "shadow.glyphs"
    csv_path = tmp_path / "F-avar.csv"
    csv_path.write_text(
        "Instance Name,XTRA,XOPQ\nDefault,663.1,188.5\n", encoding="utf-8")
    monkeypatch.setattr(server, "ORIGINAL_PATH", original)
    monkeypatch.setattr(server, "GLYPHS_PATH", shadow)
    monkeypatch.setattr(server, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(server._source_font, "load_source", lambda _p: (object(), "glyphs"))
    # The shadow's listing carries the RENAMED (display) name — the
    # alias sidecar applied it on regen; the original still says "Def".
    monkeypatch.setattr(server._source_font, "get_source_instances",
                        lambda _f: [{"name": "Default"}])
    monkeypatch.setattr(server._source_font, "get_axes",
                        lambda _f: [{"tag": "XTRA"}, {"tag": "XOPQ"}])
    monkeypatch.setattr(server._instance_aliases, "load",
                        lambda _p: {"Def": "Default"})
    monkeypatch.setattr(server, "update_instance_in_glyphs",
                        lambda p, n, c: calls.append(("update", Path(p), n)) or True)
    monkeypatch.setattr(server, "_resolve_active_source",
                        lambda: calls.append(("resolve",)))
    monkeypatch.setattr(server._csv_io, "update_csv_from_glyphs",
                        lambda *a, **k: calls.append(("csv-sync",)) or False)
    monkeypatch.setattr(server, "_get_preview_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_get_avar2_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_update_csv_modification_time", lambda _p: None)
    monkeypatch.setattr(server, "trigger_build", lambda: True)
    return calls, original


def test_save_to_source_writes_the_original_under_its_in_file_name(client, harness):
    calls, original = harness
    r = client.put("/api/instance/Default", json={
        "coordinates": {"XTRA": 700.0, "XOPQ": 200.0},
    })
    assert r.status_code == 200, r.get_json()
    update = next(c for c in calls if c[0] == "update")
    assert update[1] == original, "must write the ORIGINAL, not the shadow"
    assert update[2] == "Def", "must address the instance by its in-file (pre-rename) name"
    # The shadow regen must land before the CSV sync reads it.
    order = [c[0] for c in calls]
    assert order.index("resolve") < order.index("csv-sync"), (
        "sync before regen copies stale shadow values back over the save")
