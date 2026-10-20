"""The Fontra catch-all must not swallow avar2-studio's own /api/ paths.

Round-11 review, side finding 1: a wrong-method request to a studio route
(the S1 recipe POSTed to `/api/instances/<row>/grade`, which is a PUT route)
fell into `fontra_api_fallthrough` and came back as
``503 {"error": "Fontra subprocess is not running"}`` — meaningless to a
caller, and it hid the recipe's own bug. Studio namespaces now answer 405
(wrong method) or 404 (unknown path) in JSON; Fontra's own paths still proxy.
"""

from __future__ import annotations

import pytest

from avar2_studio import server


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def test_a_wrong_method_on_a_studio_route_is_405_json(client):
    r = client.post("/api/instances/Default%201%20MaxOpsz/grade", json={"pct": 0.2})
    assert r.status_code == 405, r.status_code
    body = r.get_json()
    assert body and "not allowed" in body["error"]


def test_an_unknown_studio_path_is_404_json(client):
    r = client.post("/api/kinetikern/instances/Def", json={"looseness": 0.4})
    assert r.status_code == 405, r.status_code   # PUT-only route

    r = client.get("/api/kinetikern/nope/nope")
    assert r.status_code == 404, r.status_code
    body = r.get_json()
    assert body and "No such API route" in body["error"]


def test_a_fontra_path_still_falls_through(client, monkeypatch):
    """Non-studio paths must keep proxying (the editor's own calls) — here we
    only assert we did not answer 404/405 locally."""
    from avar2_studio.routes import fontra

    seen = {}

    def _fake_proxy(path):
        seen["path"] = path
        return "proxied", 200

    monkeypatch.setattr(fontra, "_proxy_to_fontra", _fake_proxy)
    # A path in no studio namespace (e.g. a Fontra-only call) still proxies.
    r = client.get("/api/fontrainternal/something")
    assert seen.get("path") == "/api/fontrainternal/something"
    assert r.status_code == 200
