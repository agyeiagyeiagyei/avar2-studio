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


# --------------------------------------------------------------------------
# W3: a Fontra that fails says why (verifier, 2026-11-06 19:00)
# --------------------------------------------------------------------------

@pytest.fixture
def stand_in_fontra(tmp_path, monkeypatch):
    """`_ensure_fontra_running` with Fontra "installed" and a stand-in process.

    Only the command is swapped (for ``python -c <script>``); the launch's own
    stdout/stderr arguments are kept, so the capture under test is the real one.
    The port is bound but never listening: connects are refused, and no real
    studio or Fontra port is probed.
    """
    import socket
    import subprocess
    import sys
    from types import SimpleNamespace

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a, **k: object()
                        if name in ("fontra", "fontra_glyphs")
                        else real_find_spec(name, *a, **k))
    workdir = tmp_path / ".avar2-studio"
    workdir.mkdir()
    monkeypatch.setattr(state, "PREVIEW_DIR", workdir)
    monkeypatch.setattr(state, "FONTRA_PROCESS", None)
    monkeypatch.setattr(state, "FONTRA_CONTENT_ROOT", None)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    monkeypatch.setattr(state, "FONTRA_PORT", blocker.getsockname()[1])

    spawned = []
    real_popen = subprocess.Popen

    def run(script):
        def popen(cmd, **kwargs):
            proc = real_popen([sys.executable, "-c", script], **kwargs)
            spawned.append(proc)
            return proc
        monkeypatch.setattr(subprocess, "Popen", popen)

    yield SimpleNamespace(run=run, log=workdir / "fontra.log", spawned=spawned,
                          blocker=blocker)
    blocker.close()
    for proc in spawned:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_fontra_that_exits_immediately_names_its_stderr(stand_in_fontra):
    stand_in_fontra.run(
        "import sys; print('fontra: starting'); sys.stdout.flush(); "
        "sys.stderr.write('OSError: [Errno 48] Address already in use\\n'); "
        "sys.exit(3)")

    served = stand_in_fontra.log.parent / "shadow"     # what Fontra serves
    served.mkdir()
    with pytest.raises(RuntimeError) as excinfo:
        server._ensure_fontra_running(served)

    message = str(excinfo.value)
    assert "exit code 3" in message
    assert "OSError: [Errno 48] Address already in use" in message, message
    assert str(stand_in_fontra.log) in message, message
    # Both streams land in the log, in the workdir but outside the served folder.
    assert not list(served.iterdir())
    logged = stand_in_fontra.log.read_text()
    assert "fontra: starting" in logged and "Address already in use" in logged


def test_a_fontra_that_is_slow_to_start_still_opens(stand_in_fontra, monkeypatch):
    """A cold start on a busy machine takes more than 5 s: right after a build,
    with a compress job running, Fontra was still importing when the old wait
    gave up, and the designer got a 500 for a Fontra that was about to come up.
    """
    port = state.FONTRA_PORT
    stand_in_fontra.blocker.close()               # the stand-in binds it itself
    monkeypatch.setattr(state, "FONTRA_SHADOW_MTIME", None, raising=False)
    stand_in_fontra.run(
        "import socket, time; time.sleep(6); s = socket.socket(); "
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
        f"s.bind(('127.0.0.1', {port})); s.listen(); time.sleep(60)")
    served = stand_in_fontra.log.parent / "shadow"
    served.mkdir()

    assert server._ensure_fontra_running(served) == port


def test_a_fontra_that_never_binds_is_stopped_and_named(stand_in_fontra,
                                                         monkeypatch):
    """The timeout must stop the process it launched, or that orphan holds the
    port and the next open fails on it."""
    import time

    monkeypatch.setattr(time, "sleep", lambda _s: None)   # the bind wait, at once
    monkeypatch.setattr(server, "FONTRA_BIND_ATTEMPTS", 5)
    stand_in_fontra.run(
        "import sys, time; print('fontra: still importing'); "
        "sys.stdout.flush(); time.sleep(60)")
    served = stand_in_fontra.log.parent / "shadow"
    served.mkdir()

    with pytest.raises(RuntimeError) as excinfo:
        server._ensure_fontra_running(served)

    message = str(excinfo.value)
    assert "didn't bind" in message
    assert str(stand_in_fontra.log) in message, message
    [proc] = stand_in_fontra.spawned
    assert proc.poll() is not None, "the Fontra that never bound is still running"


def test_the_open_editor_500_carries_fontras_own_words(tmp_path, monkeypatch,
                                                       client, stand_in_fontra,
                                                       caplog):
    """What the probe prints on a 500 is this body: it must say why."""
    import logging

    src = tmp_path / "CrispyMini.glyphs"
    src.write_bytes(CRISPY_MINI.read_bytes())
    monkeypatch.setattr(state, "ORIGINAL_PATH", src)
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", None)
    stand_in_fontra.run(
        "import sys; sys.stderr.write('ModuleNotFoundError: No module named "
        "aiohttp\\n'); sys.exit(1)")

    with caplog.at_level(logging.ERROR):
        response = client.post("/api/control-axes/XTRA/open-editor", json={})

    assert response.status_code == 500, response.get_data(as_text=True)
    error = response.get_json()["error"]
    assert "No module named aiohttp" in error, error
    assert str(stand_in_fontra.log) in error, error
    assert "could not start Fontra" in caplog.text
