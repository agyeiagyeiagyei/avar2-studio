"""K1 (backend half) — `PUT /api/kinetikern` flips the family's enable flag.

The sidecar already honoured ``enabled`` at build time, but nothing could reach
it: the only off-switch was hand-editing ``<stem>-kinetikern.json``. The route
sets it, schedules one shadow rebuild when it changes, and keeps the entries so
turning it back on restores the spacing (findings.md, "Designer Part B finding —
B7", K1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from avar2_studio import kinetikern, server, state

MINI = Path(__file__).resolve().parents[1] / "examples/crispy-mini/sources/CrispyMini.glyphs"


@pytest.fixture
def client(tmp_path, monkeypatch):
    src = tmp_path / "CrispyMini.glyphs"
    src.write_bytes(MINI.read_bytes())
    monkeypatch.setattr(state, "ORIGINAL_PATH", src)
    monkeypatch.setattr(state, "GLYPHS_PATH", src)
    rebuilds = []
    monkeypatch.setattr(server, "schedule_shadow_rebuild",
                        lambda *a, **k: rebuilds.append(1))
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c, src, rebuilds


def test_the_route_turns_spacing_off_and_back_on(client):
    c, src, rebuilds = client
    kinetikern.sidecar.set_entry(src, "Default 400 MaxOpsz", -0.60,
                                 {"A": [558, 564]})

    resp = c.put("/api/kinetikern", json={"enabled": False})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["enabled"] is False
    assert kinetikern.sidecar.entries(src) == [], "off means the build ignores it"
    assert len(kinetikern.sidecar.load(src)["instances"]) == 1, "entries are kept"
    assert len(rebuilds) == 1, "one rebuild when the flag changes"

    resp = c.put("/api/kinetikern", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.get_json()["enabled"] is True
    assert [e["name"] for e in kinetikern.sidecar.entries(src)] == ["Default 400 MaxOpsz"]
    assert len(rebuilds) == 2

    # A no-op toggle does not rebuild again.
    c.put("/api/kinetikern", json={"enabled": True})
    assert len(rebuilds) == 2


def test_the_route_validates_its_body(client):
    c, _src, _rebuilds = client
    assert c.put("/api/kinetikern", json={}).status_code == 400
    assert c.put("/api/kinetikern", data="not json",
                 content_type="application/json").status_code == 400


def test_the_route_needs_a_source(monkeypatch, client):
    c, _src, _rebuilds = client
    monkeypatch.setattr(state, "ORIGINAL_PATH", None)
    assert c.put("/api/kinetikern", json={"enabled": False}).status_code == 400
