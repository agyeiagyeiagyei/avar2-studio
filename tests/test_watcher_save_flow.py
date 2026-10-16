"""T0.7: the watcher's save handling cannot storm.

Helpers extracted from the (nested, `main()`-local) watchdog handler so the
behaviour is unit-testable: settling before acting on a save event, a
resolve that retries once, and the resolve lock that keeps overlapping
resolves from interleaving the shadow/transformed writes. The branch wiring
itself is exercised by the live probe (sprint LOG, round 2).
"""
import threading
import time

import pytest

import avar2_studio.server as server


def test_settle_returns_immediately_for_an_old_file(tmp_path):
    import os
    f = tmp_path / "old.glyphs"
    f.write_text("familyName = X;")
    old = time.time() - 10
    os.utime(f, (old, old))   # a settled file
    t0 = time.time()
    server._wait_for_save_to_settle(f, settle=0.5)
    assert time.time() - t0 < 0.3


def test_settle_waits_for_a_fresh_write(tmp_path):
    f = tmp_path / "fresh.glyphs"
    f.write_text("familyName = X;")   # mtime == now
    t0 = time.time()
    server._wait_for_save_to_settle(f, settle=0.5)
    assert time.time() - t0 >= 0.5


def test_resolve_after_save_retries_once_and_then_warns(monkeypatch, capsys, tmp_path):
    f = tmp_path / "x.glyphs"
    f.write_text("familyName = X;")
    monkeypatch.setattr(server, "_wait_for_save_to_settle", lambda *a, **k: None)

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise ValueError("Unexpected EOF while parsing plist")

    monkeypatch.setattr(server, "_resolve_active_source", flaky)
    server._resolve_source_after_save(f)
    assert len(calls) == 2, "one retry after a mid-write parse failure"

    def always_broken():
        raise ValueError("still broken")

    monkeypatch.setattr(server, "_resolve_active_source", always_broken)
    server._resolve_source_after_save(f)  # must not raise out of the watcher
    assert "shadow regeneration failed" in capsys.readouterr().err


def test_resolves_are_serialized(crispy_source, monkeypatch):
    """Two overlapping resolves interleave the shadow/transformed writes —
    the storm's second half. _RESOLVE_LOCK serializes them."""
    depth = 0
    max_depth = 0
    depth_lock = threading.Lock()

    def slow_regen(_path):
        nonlocal depth, max_depth
        with depth_lock:
            depth += 1
            max_depth = max(max_depth, depth)
        time.sleep(0.15)
        with depth_lock:
            depth -= 1
        return None

    monkeypatch.setattr(server._control_axes, "regenerate_shadow", slow_regen)
    monkeypatch.setattr(server._control_axes, "list_axes", lambda _p: [])
    monkeypatch.setattr(server._grade, "list_graded_instances", lambda _p: [])
    monkeypatch.setattr(server._transforms, "active", lambda *a, **k: [])
    monkeypatch.setattr(server, "ORIGINAL_PATH", crispy_source)
    monkeypatch.setattr(server, "GLYPHS_PATH", crispy_source)

    threads = [threading.Thread(target=server._resolve_active_source)
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert max_depth == 1, f"overlapping resolves: max depth {max_depth}"


def test_classify_watched_path(tmp_path, monkeypatch):
    """The watcher must see the shadow save even when the active source is
    the transformed/ copy (T0.9), and never confuse the three files."""
    original = tmp_path / "F.glyphs"
    shadow = tmp_path / ".avar2-studio" / "shadow" / "F.glyphs"
    transformed = tmp_path / ".avar2-studio" / "shadow" / "transformed" / "F.glyphs"
    monkeypatch.setattr(server, "ORIGINAL_PATH", original)

    # Transforms on: the build source is the transformed copy.
    monkeypatch.setattr(server, "GLYPHS_PATH", transformed)
    assert server._classify_watched_path(transformed) == "source"
    assert server._classify_watched_path(original) == "original"
    assert server._classify_watched_path(shadow) == "shadow"
    assert server._classify_watched_path(tmp_path / "notes.txt") is None

    # Transforms off: GLYPHS_PATH IS the shadow.
    monkeypatch.setattr(server, "GLYPHS_PATH", shadow)
    assert server._classify_watched_path(shadow) == "source"
    assert server._classify_watched_path(original) == "original"

    # No shadow at all: a save to the original is "source" (it IS the build
    # source), and a designspace build sees pooled-UFO edits as "glif".
    monkeypatch.setattr(server, "GLYPHS_PATH", original)
    assert server._classify_watched_path(original) == "source"
    monkeypatch.setattr(server, "GLYPHS_PATH", tmp_path / "F.designspace")
    assert server._classify_watched_path(tmp_path / "F.ufo/glyphs/e.glif") == "glif"

    # Blind launch: nothing loaded, nothing classified.
    monkeypatch.setattr(server, "GLYPHS_PATH", None)
    assert server._classify_watched_path(original) is None
