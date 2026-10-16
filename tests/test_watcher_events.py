"""T2.11/T2.12: the file watcher reacts to external sidecar edits (modified,
created, moved), ignores the studio's own writes, and ignores build-job
snapshots under the watched tree."""
import os
import time

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

import avar2_studio.server as server
from avar2_studio import sidecar_io, state, watch


def _settled(path):
    """Make a file look like a finished save (mtime well in the past)."""
    t = time.time() - 5
    os.utime(path, (t, t))


@pytest.fixture
def watched(tmp_path, monkeypatch):
    original = tmp_path / "F.glyphs"
    original.write_text("familyName = F;")
    # A control-axis shadow is the active build source, so the original
    # and the source are distinct files (as on any font with layers).
    shadow = tmp_path / ".avar2-studio" / "shadow" / "F.glyphs"
    shadow.parent.mkdir(parents=True)
    shadow.write_text("familyName = F;")
    monkeypatch.setattr(state, "ORIGINAL_PATH", original)
    monkeypatch.setattr(state, "GLYPHS_PATH", shadow)
    for name in ("_SUPPRESS_WATCHDOG_UNTIL", "_SUPPRESS_ORIGINAL_UNTIL",
                 "_SELF_WRITE_WATERMARK", "_SELF_WRITE_ORIGINAL_WATERMARK"):
        monkeypatch.setattr(state, name, 0.0)
    calls = []
    monkeypatch.setattr(server, "schedule_shadow_rebuild", lambda: calls.append("regen"))
    monkeypatch.setattr(server, "trigger_build", lambda: calls.append("build"))
    monkeypatch.setattr(server, "sync_csv_with_glyphs", lambda: calls.append("csv"))
    # These two moved to watch.py with the handler (T3.10).
    monkeypatch.setattr(watch, "_resolve_source_after_save", lambda p: calls.append("resolve"))
    monkeypatch.setattr(watch, "_capture_shadow_drawings", lambda: 0)

    class Jobs:
        def is_building(self):
            return False

        def request_build(self, **kw):
            calls.append("request")

    monkeypatch.setattr(state, "JOBS", Jobs())
    return original, watch.GlyphsFileHandler(), calls


def test_an_external_sidecar_edit_regenerates(watched):
    original, handler, calls = watched
    sidecar = original.with_name("F-control.json")
    sidecar.write_text("{}")
    _settled(sidecar)
    assert watch._classify_watched_path(sidecar) == "sidecar"
    handler.on_modified(FileModifiedEvent(str(sidecar)))
    assert calls == ["regen"]


def test_the_studios_own_sidecar_write_is_ignored(watched):
    original, handler, calls = watched
    sidecar = original.with_name("F-grade.json")
    sidecar_io.write_json(sidecar, {"version": 1})      # stamps LAST_WRITES
    handler.on_modified(FileModifiedEvent(str(sidecar)))
    assert calls == []
    # ...but a later external rewrite is seen
    time.sleep(0.05)
    sidecar.write_text("{}")
    t = time.time() + 1  # strictly after the studio's stamp
    os.utime(sidecar, (t, t))
    time.sleep(0.6)  # past the per-path debounce
    handler.on_modified(FileModifiedEvent(str(sidecar)))
    assert calls == ["regen"]


def test_created_and_moved_events_count_as_changes(watched):
    original, handler, calls = watched
    created = original.with_name("F-transforms.json")
    created.write_text("{}")
    _settled(created)
    handler.on_created(FileCreatedEvent(str(created)))
    moved = original.with_name("F-kinetikern.json")
    moved.write_text("{}")
    _settled(moved)
    handler.on_moved(FileMovedEvent(str(moved.with_name(".F-kinetikern.json.tmp")), str(moved)))
    assert calls == ["regen", "regen"]


def test_the_original_and_the_shadow_keep_their_paths(watched):
    original, handler, calls = watched
    _settled(original)
    handler.on_modified(FileModifiedEvent(str(original)))
    assert calls == ["resolve", "csv", "build"]
    calls.clear()
    shadow = state.GLYPHS_PATH
    _settled(shadow)
    handler.on_modified(FileModifiedEvent(str(shadow)))
    assert calls == ["csv", "build"]


def test_build_job_snapshots_are_ignored(watched):
    original, handler, calls = watched
    job = original.parent / ".avar2-studio" / "jobs" / "build-1" / "src"
    job.mkdir(parents=True)
    staged = job / "F.glyphs"
    staged.write_text("familyName = F;")
    _settled(staged)
    glif = job / "F.ufo" / "glyphs" / "a.glif"
    glif.parent.mkdir(parents=True)
    glif.write_text("<glyph/>")
    _settled(glif)
    assert watch._classify_watched_path(staged) is None
    assert watch._classify_watched_path(glif) is None
    handler.on_modified(FileModifiedEvent(str(staged)))
    handler.on_modified(FileModifiedEvent(str(glif)))
    assert calls == []


def test_the_handler_is_importable_without_watchdog():
    assert watch.GlyphsFileHandler.__name__ == "GlyphsFileHandler"
