"""C9 — Fontra's port is configurable (``--fontra-port`` / ``AVAR2_FONTRA_PORT``).

It used to be the hard-coded ``state.FONTRA_PORT = 8001``, so the second
studio to open the editor lost it to the first: the fresh spawn died on bind
while the port probe saw the squatter and reported success. Parallel studios —
the designer's 5071, the agent's 5072, the verifier's 5073 — each need their
own Fontra (findings.md, phase C item C9).
"""

from __future__ import annotations

import sys

import pytest

from avar2_studio import server, state


def test_the_env_sets_the_default(monkeypatch):
    monkeypatch.setenv("AVAR2_FONTRA_PORT", "8002")
    assert state._fontra_port_from_env() == 8002


def test_bad_env_values_fall_back_to_8001(monkeypatch):
    for raw in ("", "  ", "not-a-port", "0", "99999", "-1"):
        monkeypatch.setenv("AVAR2_FONTRA_PORT", raw)
        assert state._fontra_port_from_env() == 8001, raw
    monkeypatch.delenv("AVAR2_FONTRA_PORT")
    assert state._fontra_port_from_env() == 8001


def test_the_flag_overrides_the_env_at_startup(monkeypatch):
    """``--fontra-port`` is applied before the server runs, and it beats the
    env value state read at import time."""
    monkeypatch.setattr(state, "USE_FONTC", True)
    monkeypatch.setattr(state, "FONTRA_PORT", 8001)
    monkeypatch.setenv("AVAR2_FONTRA_PORT", "8003")
    ran = {}
    monkeypatch.setattr(server.app, "run", lambda **kw: ran.update(kw))
    monkeypatch.setattr(sys, "argv",
                        ["avar2-studio", "--port", "5072", "--fontra-port", "8002"])

    server.main()

    assert ran.get("port") == 5072, ran
    assert state.FONTRA_PORT == 8002


def test_an_out_of_range_flag_is_a_usage_error(monkeypatch):
    monkeypatch.setattr(state, "USE_FONTC", True)
    monkeypatch.setattr(server.app, "run", lambda **kw: pytest.fail("server ran"))
    monkeypatch.setattr(sys, "argv",
                        ["avar2-studio", "--fontra-port", "70000"])
    with pytest.raises(SystemExit) as excinfo:
        server.main()
    assert excinfo.value.code == 2


def test_open_editor_answers_with_the_configured_port(tmp_path, monkeypatch):
    """The endpoint reports the port Fontra is on, so each studio's editor is
    addressable: the proxy and the "open in a new tab" URL both use it."""
    from pathlib import Path

    from avar2_studio import routes  # noqa: F401  (import proves the app loads routes)

    sample = (Path(__file__).resolve().parents[1]
              / "examples/crispy-mini/sources/CrispyMini.glyphs")
    source = tmp_path / "CrispyMini.glyphs"
    source.write_bytes(sample.read_bytes())
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    monkeypatch.setattr(state, "GLYPHS_PATH", source)
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", None)
    monkeypatch.setattr(state, "FONTRA_PORT", 8002)
    monkeypatch.setattr(server, "_ensure_fontra_running",
                        lambda *a, **k: state.FONTRA_PORT)

    server.app.config["TESTING"] = True
    with server.app.test_client() as client:
        resp = client.post("/api/control-axes/crbr/open-editor", json={})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["port"] == 8002
    assert ":8002/" in body["direct_url"]
