"""T3.10: the watcher is a lifecycle object — /api/load-source re-points it.

Before, the Observer was scheduled once in main() and never re-scheduled:
a source swap (or a blind launch) left the studio watching the old file.
"""
import pytest

import avar2_studio.server as server
from avar2_studio import state, watch
from avar2_studio.routes import load as load_routes


@pytest.fixture
def fake_observer(monkeypatch):
    scheduled = []

    class FakeObserver:
        def schedule(self, handler, path, recursive=False):
            scheduled.append((path, recursive))

        def start(self):
            pass

        def stop(self):
            pass

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(watch, "Observer", FakeObserver)
    monkeypatch.setattr(watch, "WATCHDOG_AVAILABLE", True)
    return scheduled


def test_reschedule_repoints_at_the_new_source(tmp_path, monkeypatch, fake_observer):
    monkeypatch.setattr(state, "GLYPHS_PATH", None)
    monkeypatch.setattr(state, "ORIGINAL_PATH", None)
    w = watch.SourceWatcher()
    assert w.start() is False, "blind launch: nothing to watch"
    assert fake_observer == []

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    src_a = a / "A.glyphs"
    src_b = b / "B.glyphs"
    src_a.write_text("familyName = A;")
    src_b.write_text("familyName = B;")
    monkeypatch.setattr(state, "GLYPHS_PATH", src_a)
    monkeypatch.setattr(state, "ORIGINAL_PATH", src_a)
    assert w.start() is True
    assert [p for p, _ in fake_observer] == [str(a)]

    fake_observer.clear()
    monkeypatch.setattr(state, "GLYPHS_PATH", src_b)
    monkeypatch.setattr(state, "ORIGINAL_PATH", src_b)
    assert w.reschedule() is True
    assert [p for p, _ in fake_observer] == [str(b)], "the watcher must follow the swap"


def test_load_source_reschedules_the_watcher(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(watch.WATCHER, "reschedule", lambda: calls.append(1) or True)
    monkeypatch.setattr(server, "_apply_source_path", lambda _p: None)
    staged = tmp_path / "Ex.glyphs"
    staged.write_text("familyName = Ex;")
    monkeypatch.setattr(load_routes, "_stage_builtin_example", lambda _id: staged)
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        r = c.post("/api/load-source", json={"example": "crispy-mini"})
    assert r.status_code == 200, r.get_json()
    assert calls == [1]
