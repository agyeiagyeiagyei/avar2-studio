"""A fresh install without Fontra must SAY so (round-12 review, finding 2).

Fontra is optional (not a dependency; `doctor` checks `find_spec("fontra")`),
so `POST /api/control-axes/<tag>/open-editor` used to spawn it anyway and answer
500 with "fontra exited immediately (exit code 1)". It now answers 503 with the
pip line before anything is spawned.
"""

import importlib.util
from pathlib import Path

import pytest

from avar2_studio import server, state

REPO = Path(__file__).resolve().parents[1]
CRISPY_MINI = REPO / "examples/crispy-mini/sources/CrispyMini.glyphs"


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def no_fontra(monkeypatch):
    """`importlib.util.find_spec` as it answers on a clean venv."""
    real = importlib.util.find_spec

    def missing(name, *args, **kwargs):
        if name in ("fontra", "fontra_glyphs"):
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", missing)


def test_open_editor_says_fontra_is_missing(tmp_path, monkeypatch, client, no_fontra):
    src = tmp_path / "CrispyMini.glyphs"
    src.write_bytes(CRISPY_MINI.read_bytes())
    monkeypatch.setattr(state, "ORIGINAL_PATH", src)
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", None)

    # Nothing may be spawned when the dependency is absent.
    spawned = []
    monkeypatch.setattr(server.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a) or pytest.fail(
                            "spawned Fontra although it is not installed"))

    response = client.post("/api/control-axes/XTRA/open-editor", json={})
    assert response.status_code == 503, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["optional_dependency"] == "fontra"
    assert "Fontra is not installed" in payload["error"]
    assert "pip install" in payload["error"]
    assert spawned == []


def test_the_guard_lives_before_the_spawn(monkeypatch, no_fontra):
    """`_ensure_fontra_running` raises `FontraUnavailable` on its own, so any
    future caller inherits the same diagnosis."""
    with pytest.raises(server.FontraUnavailable) as excinfo:
        server._ensure_fontra_running(Path("/tmp/whatever.glyphs"))
    assert "Fontra is not installed" in str(excinfo.value)
