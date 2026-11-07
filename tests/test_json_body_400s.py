"""T1.4: the six JSON endpoints that called request.get_json() without
silent=True answered a malformed/non-JSON body with werkzeug's HTML 415 —
the UI showed "Invalid JSON response". All six now answer a JSON 400 from
their own validation. Also pinned: a non-string name is a 400, not a 500.
"""
import pytest

import avar2_studio.server as server


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    server.app.config["TESTING"] = True
    return server.app.test_client()


SIX_ROUTES = [
    ("post", "/api/instance"),
    ("put", "/api/instance/Def"),
    ("put", "/api/instance/Def/rename"),
    ("post", "/api/avar2/axis"),
    ("put", "/api/avar2/axis/WGHT"),
    ("put", "/api/avar2/mapping/Def/WGHT"),
]


@pytest.mark.parametrize("method,path", SIX_ROUTES)
def test_malformed_json_is_a_json_400(client, method, path):
    r = getattr(client, method)(path, data="{not json",
                                content_type="application/json")
    assert r.status_code != 415, f"{method} {path} answered HTML 415"
    assert r.status_code != 500, f"{method} {path} crashed"
    # 400 from the body's own validation; 404 where a no-source guard runs
    # first — either way the answer is JSON the UI can show.
    assert r.status_code in (400, 404), (method, path, r.status_code)
    assert r.is_json, f"{method} {path} did not answer JSON"
    assert "error" in r.get_json()


def test_non_string_instance_name_is_a_400(client):
    r = client.post("/api/instance", json={"name": 123,
                                           "coordinates": {"WGHT": 400}})
    assert r.status_code == 400
    assert r.is_json


def test_non_string_rename_is_a_400(client):
    r = client.put("/api/instance/Def/rename", json={"new_name": 42})
    assert r.status_code == 400
    assert r.is_json
