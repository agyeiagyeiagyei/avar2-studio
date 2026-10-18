"""T3.4: /api/fontra-shim-config serves the shared shim's session config.

One session shape drives both shim hosts (fontra-embed's
src/focused-ui.js reads it there from the bridge init message). The
desktop proxy marks studio sessions ``allow_computed_edits`` — writes
into "→ computed" correction targets self-heal on the next shadow
regen — while the browser embed leaves the flag unset (its bridge
guards computed layers read-only).
"""

import avar2_studio.server as server
from avar2_studio import state


def _get(client):
    r = client.get("/api/fontra-shim-config")
    assert r.status_code == 200
    return r.get_json()


def test_studio_session_marks_allow_computed_edits(monkeypatch):
    session = {"studio": True, "tag": "YMOD", "axis_name": "Horizontal correction",
               "axis_default": 100, "glyph": "e"}
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", session)

    body = _get(server.app.test_client())

    assert body["allow_computed_edits"] is True
    assert body["tag"] == "YMOD"
    # The stored session dict is not mutated by the endpoint.
    assert "allow_computed_edits" not in session


def test_non_studio_sessions_have_no_flag(monkeypatch):
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", {"studio": False})
    assert "allow_computed_edits" not in _get(server.app.test_client())

    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", None)
    assert _get(server.app.test_client()) == {"studio": False}
