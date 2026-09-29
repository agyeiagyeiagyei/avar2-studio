"""The hub plugin's launcher (``Avar2Studio.glyphsPlugin/…/studio_launcher.py``)
— the pure-Python half of *Open in avar2 Studio*.

Loaded from its bundle path (a directory with a dot in its name is not
importable). The studio itself is never started here: a stub interpreter
answers ``/api/health`` the way the server does, and the port scan is
pointed at a free range so the developer's real studio is never touched.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

LAUNCHER = (Path(__file__).resolve().parent.parent / "src" / "avar2_studio" / "glyphs"
            / "Avar2Studio.glyphsPlugin" / "Contents" / "Resources" / "studio_launcher.py")
SPAN = 3
WAIT = 20.0


def _is_free(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _free_port_span():
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            base = s.getsockname()[1]
        if base + SPAN < 65535 and all(_is_free(p) for p in range(base, base + SPAN)):
            return base
    pytest.skip("no free port span")


@pytest.fixture
def launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("studio_launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "FIRST_PORT", _free_port_span())
    monkeypatch.setattr(mod, "PORT_SPAN", SPAN)
    monkeypatch.setattr(mod, "READY_TIMEOUT", WAIT)
    return mod


class _Handler(BaseHTTPRequestHandler):
    payload = None  # None: not a studio (404 everywhere)

    def do_GET(self):
        if self.path == "/api/health" and self.payload is not None:
            body = json.dumps(self.payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def serve():
    servers = []

    def _serve(port, payload=None):
        handler = type("Handler", (_Handler,), {"payload": payload})
        srv = HTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv

    yield _serve
    for srv in servers:
        srv.shutdown()
        srv.server_close()


class _Outcome:
    """Collects the launcher's callbacks; ``wait()`` blocks until one fires."""

    def __init__(self):
        self.event = threading.Event()
        self.ready = None
        self.error = None

    def on_ready(self, url, note):
        self.ready = (url, note)
        self.event.set()

    def on_error(self, message):
        self.error = message
        self.event.set()

    def wait(self):
        assert self.event.wait(WAIT), "no callback within %ss" % WAIT
        return self


def _stub_interpreter(tmp_path, body):
    """A "python" whose ``-m avar2_studio SOURCE --port N`` runs ``body``
    (a script text) with SOURCE and N in ``sys.argv[1:]``."""
    script = tmp_path / "stub_server.py"
    script.write_text(body)
    stub = tmp_path / "bin" / "python"
    stub.parent.mkdir()
    stub.write_text(
        '#!/bin/sh\n'
        'if [ "$1" != "-m" ] || [ "$2" != "avar2_studio" ]; then echo "unexpected argv: $*"; exit 2; fi\n'
        'shift 2\n'
        'exec "%s" "%s" "$@"\n' % (sys.executable, script)
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "glyphs-plugin.json"
    config.write_text(json.dumps({"python": str(stub)}))
    return stub, config


STUB_STUDIO = '''
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
source, port = sys.argv[1], int(sys.argv[sys.argv.index("--port") + 1])
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "original_path": source, "path": os.environ["PATH"]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
print("stub studio on", port, flush=True)
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def test_probe_tells_free_other_and_studio_apart(launcher, serve):
    base = launcher.FIRST_PORT
    assert launcher.probe(base) == launcher.FREE
    serve(base)  # answers, but not with a health report
    assert launcher.probe(base) == launcher.OTHER
    serve(base + 1, {"status": "ok", "original_path": "/x/Font.glyphs"})
    assert launcher.probe(base + 1)["original_path"] == "/x/Font.glyphs"


def test_open_reuses_a_studio_already_serving_the_font(launcher, serve, tmp_path):
    source = tmp_path / "Font.glyphs"
    source.write_text("")
    serve(launcher.FIRST_PORT)  # something else on the first port
    serve(launcher.FIRST_PORT + 1, {"status": "ok", "original_path": str(source)})
    opened = []
    lch = launcher.StudioLauncher(config_path=tmp_path / "missing.json", opener=opened.append)

    outcome = _Outcome()
    lch.open(source, outcome.on_ready, outcome.on_error)

    url = launcher.url_for(launcher.FIRST_PORT + 1)
    assert outcome.wait().ready == (url, "already running")
    assert opened == [url]
    assert not lch.running  # adopted, not owned: Stop must leave it alone
    assert lch.stop() is False


def test_open_without_the_install_record_says_what_to_run(launcher, tmp_path):
    lch = launcher.StudioLauncher(config_path=tmp_path / "missing.json", opener=lambda url: None)
    outcome = _Outcome()

    lch.open(tmp_path / "Font.glyphs", outcome.on_ready, outcome.on_error)

    assert "install-glyphs-plugins" in outcome.wait().error
    assert outcome.ready is None


def test_open_starts_the_studio_on_the_first_free_port_and_stop_ends_it(launcher, serve, tmp_path):
    source = tmp_path / "Font.glyphs"
    source.write_text("")
    stub, config = _stub_interpreter(tmp_path, STUB_STUDIO)
    serve(launcher.FIRST_PORT)  # busy: the studio must go to the next port
    opened = []
    lch = launcher.StudioLauncher(config_path=config, log_path=tmp_path / "log", opener=opened.append)

    outcome = _Outcome()
    lch.open(source, outcome.on_ready, outcome.on_error)

    port = launcher.FIRST_PORT + 1
    assert outcome.wait().error is None
    assert outcome.ready == (launcher.url_for(port), "started")
    assert opened == [launcher.url_for(port)]
    assert lch.running and lch.port == port
    health = launcher.probe(port)
    assert health["original_path"] == str(source)
    assert health["path"].split(os.pathsep)[0] == str(stub.parent)  # fontc resolves from the interpreter's bin

    # A second Open for the same font reuses the running server.
    again = _Outcome()
    lch.open(source, again.on_ready, again.on_error)
    assert again.wait().ready == (launcher.url_for(port), "already running")

    assert lch.stop() is True
    assert not lch.running
    assert launcher.probe(port) == launcher.FREE
    assert "stub studio on %d" % port in (tmp_path / "log").read_text()


def test_open_reports_a_server_that_dies_with_its_log_tail(launcher, tmp_path):
    source = tmp_path / "Font.glyphs"
    source.write_text("")
    _, config = _stub_interpreter(tmp_path, 'import sys; print("boom: no such source"); sys.exit(3)\n')
    lch = launcher.StudioLauncher(config_path=config, log_path=tmp_path / "log", opener=lambda url: None)

    outcome = _Outcome()
    lch.open(source, outcome.on_ready, outcome.on_error)

    error = outcome.wait().error
    assert "status 3" in error and "boom: no such source" in error
    assert outcome.ready is None
    assert not lch.running
