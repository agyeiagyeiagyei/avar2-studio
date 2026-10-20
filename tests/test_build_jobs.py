"""The build job queue (build_jobs.py) against a stub worker — no fontc,
no fontTools: staging, coalescing, cancel, dead-worker recovery, export
artifacts, and the WOFF2 follow-up job.

These run the REAL queue with real subprocesses; the stub worker
(tests/stub_build_worker.py) fakes the pipeline in milliseconds.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from avar2_studio import build_jobs, state

STUB = Path(__file__).parent / "stub_build_worker.py"


class StubServer:
    """The queue's method surface; the value state it used to carry is
    monkeypatched onto ``state`` by the fixture."""

    def __init__(self, tmp_path):
        self._preview = tmp_path / ".avar2-studio"

    def _get_preview_dir(self):
        self._preview.mkdir(exist_ok=True)
        return self._preview

    def _get_avar2_font_dir(self):
        d = self._preview / "build"
        d.mkdir(parents=True, exist_ok=True)
        return d


@pytest.fixture
def stub_state(tmp_path, monkeypatch):
    """Filesystem + ``state`` values the queue's staging/promotion reads."""
    original = tmp_path / "Mini.glyphs"
    original.write_text("familyName = Mini;")
    (tmp_path / "Mini-avar.csv").write_text("Instance Name,wght\nRegular,400\n")
    for name, value in (
            ("ORIGINAL_PATH", original), ("GLYPHS_PATH", original),
            ("USE_FONTC", True), ("VARIABLE_FONT_PATH", None),
            ("LAST_BUILD_STATUS", None), ("LAST_BUILD_ERROR", None),
            ("LAST_BUILD_TIME", None), ("_SYNC_CSV_HOOK", None)):
        monkeypatch.setattr(state, name, value)


@pytest.fixture
def queue(tmp_path, stub_state):
    q = build_jobs.JobQueue(StubServer(tmp_path),
                            worker_cmd=[sys.executable, str(STUB)])
    yield q


def _wait_for(q, pred, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_a_build_job_stages_runs_and_promotes(queue):
    queue.request_build()
    assert queue.wait_for_idle(timeout=30)
    server = queue.server
    assert state.LAST_BUILD_STATUS == "ok"
    assert state.VARIABLE_FONT_PATH is not None
    assert state.VARIABLE_FONT_PATH.exists()
    assert state.VARIABLE_FONT_PATH.parent == server._get_avar2_font_dir()
    assert state.VARIABLE_FONT_PATH.read_bytes() == b"faux-font"
    # the build's own outcome is recorded as the last BUILD result
    assert queue._last_build_ok is True


def test_build_requests_coalesce(queue, monkeypatch):
    monkeypatch.setenv("STUB_WORKER_MODE", "sleep")
    monkeypatch.setenv("STUB_WORKER_SLEEP", "2")
    runs = os.path.join(tempfile_dir(), "runs.log")
    monkeypatch.setenv("STUB_WORKER_RUNS", runs)
    queue.request_build()
    assert _wait_for(queue, lambda: queue.status()["running"], 10)
    queue.request_build()
    queue.request_build()
    queue.request_build()
    # the second pass must not sleep, or the test takes forever
    monkeypatch.setenv("STUB_WORKER_SLEEP", "0.01")
    assert queue.wait_for_idle(timeout=30)
    time.sleep(0.5)
    lines = [line for line in Path(runs).read_text().split() if line == "build"]
    assert lines == ["build", "build"], f"three requests during a build coalesce to one rebuild, got {lines}"


def tempfile_dir():
    import tempfile
    return tempfile.mkdtemp(prefix="build-jobs-test-")


def test_a_midbuild_save_resolves_before_syncing(queue, monkeypatch):
    """A source change landing mid-build: the follow-up job must re-resolve
    the active source BEFORE the CSV hook and staging — the shadow still
    reflects the pre-save state, and both read from it."""
    calls = []
    monkeypatch.setenv("STUB_WORKER_MODE", "sleep")
    monkeypatch.setenv("STUB_WORKER_SLEEP", "2")
    server = queue.server
    server._resolve_active_source = lambda: calls.append("resolve")
    monkeypatch.setattr(state, "_SYNC_CSV_HOOK", lambda: calls.append("csv"))
    queue.request_build()
    assert _wait_for(queue, lambda: queue.status()["running"], 10)
    queue.request_build(source_refresh="original")   # the watcher's mid-build save
    monkeypatch.setenv("STUB_WORKER_SLEEP", "0.01")
    assert queue.wait_for_idle(timeout=30)
    assert calls == ["resolve", "csv"], \
        f"resolve must precede the CSV hook (and both precede staging), got {calls}"


def test_cancel_a_running_build(queue, monkeypatch):
    monkeypatch.setenv("STUB_WORKER_MODE", "sleep")
    queue.request_build()
    assert _wait_for(queue, lambda: queue.status()["running"], 10)
    job_id = queue.status()["running"]["job_id"]
    assert queue.cancel(job_id)
    assert not queue.wait_for_idle(timeout=30), "a cancelled build reports failure"
    # A cancel is not a broken source: the previous build status is
    # untouched, so the build gate (LAST_BUILD_STATUS == "failed") stays
    # in whatever state it was — here, never-set.
    assert state.LAST_BUILD_STATUS != "failed"
    assert state.LAST_BUILD_STATUS is None
    assert state.LAST_BUILD_ERROR is None
    # and the queue recovers
    monkeypatch.setenv("STUB_WORKER_MODE", "ok")
    queue.request_build()
    assert queue.wait_for_idle(timeout=30)
    assert state.LAST_BUILD_STATUS == "ok"


def test_a_dead_worker_is_a_clean_failure(queue, monkeypatch):
    monkeypatch.setenv("STUB_WORKER_MODE", "die")
    queue.request_build()
    assert not queue.wait_for_idle(timeout=30)
    assert state.LAST_BUILD_STATUS == "failed"
    assert "worker died" in (state.LAST_BUILD_ERROR or "")
    assert state.VARIABLE_FONT_PATH is None, "a dead worker must not touch the served font"


def test_a_broken_worker_command_does_not_wedge_the_queue(tmp_path, stub_state):
    """If spawning the worker itself raises, the supervisor must survive and
    serve the next job — a dead supervisor is the wedge this rework removes."""
    q = build_jobs.JobQueue(StubServer(tmp_path),
                            worker_cmd=["/nonexistent/binary"])
    q.request_build()
    assert _wait_for(q, lambda: q._last_result is not None, 30)
    assert q._last_result["success"] is False
    assert not q.is_building()
    # recovered: swap in the stub worker and build for real
    q._worker_cmd = [sys.executable, str(STUB)]
    q.request_build()
    assert q.wait_for_idle(timeout=30)
    assert state.LAST_BUILD_STATUS == "ok"


def test_an_export_job_produces_a_downloadable_artifact(queue):
    queue.request_build()   # exports need a built font staged
    assert queue.wait_for_idle(timeout=30)
    job_id = queue.submit_export(hidden_axes=["XOPQ"], default_location=None)
    assert job_id
    assert _wait_for(queue, lambda: queue.export_artifact(job_id), 30)
    artifact = queue.export_artifact(job_id)
    assert artifact.read_bytes() == b"faux-export-ttf"


def test_a_woff2_export_carries_the_format(queue):
    queue.request_build()
    assert queue.wait_for_idle(timeout=30)
    job_id = queue.submit_export(hidden_axes=[], default_location=None,
                                 format="woff2")
    assert _wait_for(queue, lambda: queue.export_artifact(job_id), 30)
    artifact = queue.export_artifact(job_id)
    assert artifact.name.endswith(".woff2")
    assert artifact.read_bytes() == b"faux-export-woff2"


def test_a_successful_build_queues_woff2_compression(queue, monkeypatch):
    runs = os.path.join(tempfile_dir(), "runs.log")
    monkeypatch.setenv("STUB_WORKER_RUNS", runs)
    queue.request_build()
    assert queue.wait_for_idle(timeout=30)
    woff2 = state.VARIABLE_FONT_PATH.with_suffix(".woff2")
    assert _wait_for(queue, lambda: woff2.exists(), 30)
    assert woff2.read_bytes() == b"faux-woff2"
    key = json.loads(state.VARIABLE_FONT_PATH.with_suffix(".woff2.json").read_text())
    assert key["woff2_bytes"] == 10
    # and the sizes endpoint now reports it as exact
    sizes = queue.export_sizes()
    assert sizes["woff2_exact"] is True
    assert sizes["woff2_bytes"] == 10


def test_stage_copies_every_sidecar(queue, tmp_path):
    """The worker re-derives the build copy from the staged snapshot, so a
    sidecar left behind is a feature silently missing from the build."""
    for suffix in build_jobs.SIDECAR_SUFFIXES:
        p = tmp_path / ("Mini" + suffix)
        if not p.exists():
            p.write_text("{}")
    job = {"kind": "build"}
    queue._stage(job)
    staged = {p.name for p in (job["dir"] / "src").iterdir()}
    assert "Mini-kinetikern.json" in staged
    assert all(("Mini" + suffix) in staged for suffix in build_jobs.SIDECAR_SUFFIXES)


def test_stage_copies_the_shadow(queue, tmp_path):
    """Uncaptured Fontra drawings exist ONLY in .avar2-studio/shadow/ until
    capture_outlines runs; the worker's regen preserves a staged shadow and
    silently rebuilds from the sidecar alone when it is missing."""
    shadow = tmp_path / ".avar2-studio" / "shadow" / "Mini.glyphs"
    shadow.parent.mkdir(parents=True)
    shadow.write_text("familyName = Mini; # shadow carrying a drawing")
    job = {"kind": "build"}
    queue._stage(job)
    staged = job["dir"] / "src" / ".avar2-studio" / "shadow" / "Mini.glyphs"
    assert staged.exists()
    assert staged.read_bytes() == shadow.read_bytes()
    # shadow/transformed/ is derived — the worker re-creates it from the
    # staged shadow, so it must NOT need staging
    assert not (job["dir"] / "src" / ".avar2-studio" / "shadow" / "transformed").exists()


def test_wait_for_idle_returns_the_current_builds_outcome(queue, monkeypatch):
    """_running used to clear right after proc.wait(): in the gap before
    _finish, wait_for_idle answered with the PREVIOUS build's outcome."""
    import threading
    release = threading.Event()
    orig = build_jobs.JobQueue._read_result

    def held(self, job):
        release.wait(10)
        return orig(self, job)

    queue.request_build()   # build 1: ok
    assert queue.wait_for_idle(timeout=30)

    monkeypatch.setattr(build_jobs.JobQueue, "_read_result", held)
    monkeypatch.setenv("STUB_WORKER_MODE", "die")   # build 2: fails
    queue.request_build()
    outcome = []
    t = threading.Thread(
        target=lambda: outcome.append(queue.wait_for_idle(timeout=30)))
    t.start()
    time.sleep(1.0)   # the build reaches the held _read_result
    assert t.is_alive(), "wait_for_idle answered during the result gap"
    release.set()
    t.join(timeout=30)
    assert outcome == [False], \
        f"wait_for_idle must report the CURRENT build's failure, got {outcome}"


def test_promotion_reaps_stale_fonts_but_keeps_the_newest(tmp_path):
    """T2.14: build/ keeps the promoted font plus the newest KEEP_BUILDS
    others; older fonts go together with their WOFF2 twins."""
    import os
    import time

    from avar2_studio.build_jobs import KEEP_BUILDS, reap_build_dir
    build = tmp_path / "build"
    build.mkdir()
    names = [f"F[{i}].ttf" for i in range(5)]
    now = time.time()
    for i, n in enumerate(names):
        p = build / n
        p.write_bytes(b"ttf")
        os.utime(p, (now - 100 + i, now - 100 + i))  # names[4] newest
        if i < 2:
            p.with_suffix(".woff2").write_bytes(b"wOF2")
            p.with_suffix(".woff2.json").write_text("{}")
    current = build / names[4]
    removed = reap_build_dir(current)
    survivors = sorted(q.name for q in build.iterdir())
    kept = names[5 - KEEP_BUILDS:]  # the newest KEEP_BUILDS, which include current
    assert survivors == sorted(kept)
    assert removed == 3 + 2 * 2  # three old fonts, two of them with twins


def test_two_exports_finishing_inside_one_poll_window_resolve_by_id(queue):
    """The export poll used to key on status()["last"], which names only the
    NEWEST finished job: when two exports finished between two client polls
    the first one vanished and its client spun until the 20-minute timeout.
    job_status(id) answers for every recent job."""
    queue.request_build()   # exports need a built font staged
    assert queue.wait_for_idle(timeout=30)
    first = queue.submit_export(hidden_axes=[], default_location=None)
    second = queue.submit_export(hidden_axes=["XOPQ"], default_location=None)
    assert first != second
    assert queue.wait_for_idle(timeout=30)   # both done before we look once
    assert _wait_for(queue, lambda: queue.export_artifact(second), 30)
    # the old signal only knows the second job
    assert queue.status()["last"]["job_id"] == second
    # the per-job signal knows both, with their artifacts promoted
    for job_id in (first, second):
        st = queue.job_status(job_id)
        assert st["state"] == "finished", st
        assert st["kind"] == "export"
        assert st["success"] is True
        assert st["cancelled"] is False
        assert st["artifact_ready"] is True
        assert queue.export_artifact(job_id) is not None
    assert queue.job_status("export-never-submitted") is None


def test_job_status_walks_queued_running_finished(queue, monkeypatch):
    monkeypatch.setenv("STUB_WORKER_MODE", "sleep")
    monkeypatch.setenv("STUB_WORKER_SLEEP", "1.5")
    queue.request_build()   # the build runs first (sleeping) and stages a font
    assert _wait_for(queue, lambda: queue.status()["running"], 10)
    build_id = queue.status()["running"]["job_id"]
    assert queue.job_status(build_id)["state"] == "running"
    job_id = queue.submit_export(hidden_axes=[], default_location=None)
    assert queue.job_status(job_id) == {"job_id": job_id, "kind": "export",
                                        "state": "queued"}
    assert _wait_for(queue, lambda: queue.job_status(job_id)["state"] == "running", 30)
    running = queue.job_status(job_id)
    assert running["kind"] == "export"
    assert "elapsed" in running
    assert _wait_for(queue, lambda: queue.job_status(job_id)["state"] == "finished", 30)
    done = queue.job_status(job_id)
    assert done["success"] is True and done["artifact_ready"] is True
    finished_build = queue.job_status(build_id)
    assert finished_build["state"] == "finished" and finished_build["kind"] == "build"
    assert finished_build["artifact_ready"] is False   # builds promote to the preview, not an export


def test_a_cancelled_queued_export_reports_a_terminal_state(queue, monkeypatch):
    """A queued export that is cancelled never runs, so _finish never sees
    it — the client polling its id must still get finished+cancelled, not
    an unknown-job 404."""
    monkeypatch.setenv("STUB_WORKER_MODE", "sleep")
    monkeypatch.setenv("STUB_WORKER_SLEEP", "1.5")
    queue.request_build()
    assert _wait_for(queue, lambda: queue.status()["running"], 10)
    job_id = queue.submit_export(hidden_axes=[], default_location=None)
    assert queue.job_status(job_id)["state"] == "queued"
    assert queue.cancel(job_id)
    st = queue.job_status(job_id)
    assert st["state"] == "finished"
    assert st["success"] is False
    assert st["cancelled"] is True
    assert st["artifact_ready"] is False
    assert queue.wait_for_idle(timeout=30)
    assert queue.export_artifact(job_id) is None


def test_recent_results_are_bounded(queue):
    for i in range(build_jobs.KEEP_RESULTS + 10):
        with queue._lock:
            queue._record_result({"job_id": f"export-{i}", "kind": "export", "success": True})
    assert len(queue._results) == build_jobs.KEEP_RESULTS
    assert queue.job_status("export-0") is None            # evicted, oldest first
    assert queue.job_status("export-9") is None
    assert queue.job_status("export-10")["state"] == "finished"
    assert queue.job_status(f"export-{build_jobs.KEEP_RESULTS + 9}")["state"] == "finished"


def test_the_relocation_error_survives_the_worker_boundary(tmp_path, monkeypatch):
    """S1(b) reports the failed relocation through health, which is served by
    the PARENT process — so the worker has to hand the field over in both
    directions. It lives in two lists (`build_worker._apply_result` writes it
    into the result, `JobQueue._apply_build_state` copies it back); a field
    missing from either silently reads None in /api/health."""
    from avar2_studio import build_worker

    result = {}
    build_worker._apply_result(result)
    assert "last_origin_relocation_error" in result, sorted(result)

    monkeypatch.setattr(state, "LAST_ORIGIN_RELOCATION_ERROR", None)
    queue = build_jobs.JobQueue(StubServer(tmp_path))
    queue._apply_build_state({"success": True, "last_build_status": "degraded",
                              "last_origin_relocation_error": "glyph 'g' overflow"})
    assert state.LAST_ORIGIN_RELOCATION_ERROR == "glyph 'g' overflow"
