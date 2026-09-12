"""The build gate: a source that does not compile pauses every mutating
endpoint, keeps reads open, and leaves the recovery routes reachable."""

import pytest

from avar2_studio import server


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def broken_build(monkeypatch):
    monkeypatch.setattr(server, "LAST_BUILD_STATUS", "failed")
    monkeypatch.setattr(server, "LAST_BUILD_ERROR", "fontc: 'z' has interpolation-incompatible paths")


def test_mutations_are_refused_while_broken(client, broken_build):
    for method, path in [
        ("put", "/api/instances/Def/grade"),
        ("post", "/api/control-axes/lcwd/reseed"),
        ("put", "/api/transforms"),
        ("post", "/api/export-font"),
        ("delete", "/api/instance/Def"),
        ("post", "/api/config/import"),
    ]:
        r = getattr(client, method)(path, json={})
        assert r.status_code == 409, (method, path, r.status_code)
        body = r.get_json()
        assert body["build_blocked"] is True
        assert "interpolation-incompatible" in body["detail"]


def test_reads_stay_open_while_broken(client, broken_build):
    r = client.get("/api/health")
    assert r.status_code != 409
    assert r.get_json()["last_build_status"] == "failed"


def test_recovery_routes_stay_open_while_broken(client, broken_build):
    # These may fail for OTHER reasons with no source loaded in the test
    # process; the assertion is only that the GATE does not refuse them.
    for method, path in [
        ("post", "/api/build"),
        ("post", "/api/load-source"),
        ("post", "/api/instance/Def/editing"),
        ("delete", "/api/instance/Def/editing"),
    ]:
        r = getattr(client, method)(path, json={})
        assert r.status_code != 409, (method, path)


def test_gate_is_inert_when_build_is_ok(client, monkeypatch):
    monkeypatch.setattr(server, "LAST_BUILD_STATUS", "ok")
    r = client.put("/api/transforms", json={})
    assert r.status_code != 409
