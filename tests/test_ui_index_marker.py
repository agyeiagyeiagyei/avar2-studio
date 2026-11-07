"""The served index.html carries a backend marker.

`selectApiMode()` used to probe /api/health to tell a Flask install from
a static host. On GitHub Pages that probe 404s, and Chrome logs the
failed resource as a console error on every demo visit. The server now
injects a meta tag into the index it serves; the static build has no
marker, so the client can choose static mode without a network probe.
"""

from pathlib import Path

import pytest

from avar2_studio import server
from avar2_studio.routes import ui


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.mark.skipif(not (Path(ui._BUNDLE_DIR) / "index.html").exists(),
                    reason="frontend bundle not built in this checkout")
def test_index_marks_the_backend_once(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    assert r.data.count(b'name="avar2-backend"') == 1


@pytest.mark.skipif(not (Path(ui._BUNDLE_DIR) / "index.html").exists(),
                    reason="frontend bundle not built in this checkout")
def test_index_is_never_cached(client):
    """The index names hashed bundle assets, so it must not be served from a
    stale cache across a studio upgrade — no-store replaces the
    conditional/ETag handling send_file used to provide."""
    r = client.get("/")
    assert r.headers.get("Cache-Control") == "no-store", dict(r.headers)
