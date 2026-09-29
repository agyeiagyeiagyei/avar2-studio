# -*- coding: utf-8 -*-
"""Start, adopt and stop a local avar2-studio server from Glyphs.

Pure Python — no Glyphs or AppKit imports — so the studio's test-suite can
exercise it; ``plugin.py`` wraps the callbacks so they land on the main
thread.

``avar2-studio install-glyphs-plugins`` records which interpreter runs the
studio in ``~/.avar2-studio/glyphs-plugin.json``. ``StudioLauncher.open``
first looks for a studio already serving the font on the ports the studio
uses, and only otherwise starts ``python -m avar2_studio SOURCE --port N``
from that interpreter, waits for ``/api/health`` and opens the browser.
Runs under Glyphs' bundled Python (3.8+): no newer syntax here.
"""

import errno
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

HOME_DIR = Path.home() / ".avar2-studio"
CONFIG_PATH = HOME_DIR / "glyphs-plugin.json"
LOG_PATH = HOME_DIR / "glyphs-plugin.log"

FIRST_PORT = 5001       # the server's default
PORT_SPAN = 10          # 5001–5010 are scanned for a running studio / a free port
READY_TIMEOUT = 600.0   # seconds; the server listens only after the first build
POLL_INTERVAL = 0.5

FREE = "free"           # probe(): nothing listens on the port
OTHER = "other"         # probe(): something that is not a studio does


class LauncherError(Exception):
    pass


def read_config(path=CONFIG_PATH):
    """The installer's record of the interpreter that runs the studio."""
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise LauncherError(
            "avar2-studio is not set up for Glyphs yet: run "
            "`avar2-studio install-glyphs-plugins` in a terminal."
        )
    except (OSError, ValueError) as exc:
        raise LauncherError("Could not read %s: %s" % (path, exc))
    python = data.get("python") if isinstance(data, dict) else None
    if not python or not Path(python).exists():
        raise LauncherError(
            "The Python recorded in %s is gone (%s): run "
            "`avar2-studio install-glyphs-plugins` again." % (path, python)
        )
    return data


def studio_command(python, source, port):
    return [str(python), "-m", "avar2_studio", str(source), "--port", str(port)]


def studio_env(python):
    """The child's environment: the interpreter's bin dir first on PATH.
    Finder-launched apps do not get the shell's PATH, and the server finds
    ``fontc`` (and the gftools scripts) through it."""
    env = dict(os.environ)
    bindir = str(Path(python).resolve().parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    return env


def url_for(port):
    return "http://127.0.0.1:%d" % port


def probe(port, timeout=1.0):
    """What listens on ``port``: FREE, OTHER, or the studio's health dict."""
    try:
        with urllib.request.urlopen(url_for(port) + "/api/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ConnectionRefusedError) or getattr(reason, "errno", None) == errno.ECONNREFUSED:
            return FREE
        return OTHER
    except (OSError, ValueError):
        return OTHER
    if not isinstance(data, dict) or data.get("status") != "ok":
        return OTHER
    return data


def _same_file(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def serves_source(health, source):
    """True when a studio's health report says it has ``source`` open."""
    for key in ("original_path", "glyphs_path"):
        path = health.get(key)
        if path and _same_file(path, source):
            return True
    return False


class StudioLauncher(object):

    def __init__(self, config_path=CONFIG_PATH, log_path=LOG_PATH, opener=webbrowser.open):
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
        self._open_url = opener
        self._proc = None
        self._source = None
        self._port = None

    @property
    def running(self):
        return self._proc is not None and self._proc.poll() is None

    @property
    def port(self):
        return self._port if self.running else None

    def open(self, source, on_ready, on_error):
        """Show ``source`` in the studio: a server already serving it is
        reused (ours or anyone's), otherwise one is started on the first
        free port. ``on_ready(url, note)`` or ``on_error(message)`` is
        called once the outcome is known — synchronously when nothing had
        to start, from a worker thread otherwise."""
        source = Path(source)
        if self.running and _same_file(self._source, source):
            self._show(self._port, on_ready, "already running")
            return
        taken = set()
        for port in range(FIRST_PORT, FIRST_PORT + PORT_SPAN):
            found = probe(port)
            if found == FREE:
                continue
            taken.add(port)
            if found != OTHER and serves_source(found, source):
                self._show(port, on_ready, "already running")
                return
        try:
            config = read_config(self.config_path)
        except LauncherError as exc:
            on_error(str(exc))
            return
        if self.running:
            self.stop()
        free = [p for p in range(FIRST_PORT, FIRST_PORT + PORT_SPAN) if p not in taken]
        if not free:
            on_error("Ports %d–%d are all in use." % (FIRST_PORT, FIRST_PORT + PORT_SPAN - 1))
            return
        port = free[0]
        python = config["python"]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(str(self.log_path), "w") as log:
                proc = subprocess.Popen(
                    studio_command(python, source, port),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=str(source.parent),
                    env=studio_env(python),
                )
        except OSError as exc:
            on_error("Could not start avar2-studio: %s" % exc)
            return
        self._proc, self._source, self._port = proc, source, port
        threading.Thread(
            target=self._await_ready, args=(proc, port, on_ready, on_error),
            name="avar2-studio-launch", daemon=True,
        ).start()

    def _show(self, port, on_ready, note):
        url = url_for(port)
        self._open_url(url)
        on_ready(url, note)

    def _await_ready(self, proc, port, on_ready, on_error):
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                on_error("avar2-studio exited (status %s).\n%s" % (proc.returncode, self.log_tail()))
                return
            if probe(port) not in (FREE, OTHER):
                self._show(port, on_ready, "started")
                return
            time.sleep(POLL_INTERVAL)
        on_error("avar2-studio did not answer within %d s.\n%s" % (READY_TIMEOUT, self.log_tail()))

    def stop(self):
        """Terminate the server this launcher started. True if one was running."""
        proc, self._proc, self._source, self._port = self._proc, None, None, None
        if proc is None or proc.poll() is not None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        return True

    def log_tail(self, lines=15):
        try:
            text = self.log_path.read_text(errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])
