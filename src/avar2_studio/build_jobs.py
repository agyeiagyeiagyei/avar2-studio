"""Build/export job queue — the server-side half of the worker rework
(docs/decisions/0003-worker-subprocess-finish-then-rebuild.md).

One worker subprocess at a time runs builds, exports and WOFF2
compression; the Flask process only stages snapshots, supervises, and
promotes artifacts. Every job works on a COPY of the source set
(``.avar2-studio/jobs/<id>/src/``) staged at job START, so a save in
Glyphs mid-build can't tear the worker's inputs, and the worker never
writes shared state — the supervisor alone promotes results into the
preview dir.

Builds coalesce: a build requested while one runs sets a rebuild-wanted
bit and the supervisor re-stages and rebuilds once, carrying whatever
landed meanwhile — the semantics ``trigger_build`` had. Exports queue
FIFO; compression runs only when nothing else is pending. Cancelling
kills the worker's process group; a dead worker (kill -9, crash) becomes
a clean failed state, never a stuck flag.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

from . import state
from .studio_log import get_logger

#: Source-set sidecars staged into every job snapshot (siblings of the
#: original source), plus the small preview-dir files builds/exports read.

log = get_logger(__name__)
SIDECAR_SUFFIXES = ("-avar.csv", "-control.json", "-transforms.json", "-grade.json", "-kinetikern.json")
PREVIEW_FILES = ("axis-metadata.json", "instance-renames.json", "config.yaml")
LEGACY_CSV = "avar2-mappings.csv"

#: Worker env passthrough — the build consults these (see pipeline.py).
ENV_KEYS = ("AVAR2_RELOCATE_BUILD_DEFAULT", "AVAR2_STUDIO_GFTOOLS_BUILDER",
            "AVAR2_SPAC_INJECT")

#: Export artifacts kept on disk for download; older ones are reaped.
KEEP_EXPORTS = 5
# Fonts kept in .avar2-studio/build/ besides the one just promoted: an
# axis-set change renames the output, so stale names otherwise pile up
# (33 files / 78 MB observed) behind the newest-by-mtime picker.
KEEP_BUILDS = 2

#: Finished-job results kept for ``job_status`` lookups by id. ``_last_result``
#: alone lost any job that finished inside one client poll window.
KEEP_RESULTS = 50



def reap_build_dir(current: Path, keep: int = KEEP_BUILDS) -> int:
    """Delete fonts in ``current``'s directory beyond the newest ``keep``
    (plus the one just promoted), together with their WOFF2 cache twins.
    Returns the number of files removed."""
    build_dir = Path(current).parent
    fonts = sorted(build_dir.glob("*.ttf"), key=lambda p: p.stat().st_mtime, reverse=True)
    protected = {Path(current).resolve()} | {p.resolve() for p in fonts[:keep]}
    removed = 0
    for font in fonts:
        if font.resolve() in protected:
            continue
        for twin in (font, font.with_suffix(".woff2"), font.with_suffix(".woff2.json")):
            try:
                twin.unlink()
                removed += 1
            except FileNotFoundError:
                pass
    return removed

class JobQueue:
    def __init__(self, server, worker_cmd=None):
        self.server = server  # the server module itself (methods; value state lives in state.py)
        self._worker_cmd = worker_cmd  # tests stub the worker subprocess
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._rebuild_wanted = False
        self._source_refresh = None  # "original" | "source" — original outranks
        self._pending_exports = []   # job dicts, FIFO
        self._pending_compress = None
        self._running = None         # job dict with proc attached
        self._last_result = None
        self._results = OrderedDict()  # job id → result, newest last, bounded
        self._last_build_ok = None   # last BUILD job's success, for waiters
        self._supervisor = threading.Thread(target=self._supervise, daemon=True,
                                            name="build-jobs")
        self._supervisor.start()

    # ------------------------------------------------------------------
    # submission
    # ------------------------------------------------------------------

    def request_build(self, source_refresh=None):
        """Coalescing build request — the trigger_build replacement."""
        with self._lock:
            self._rebuild_wanted = True
            if source_refresh == "original" or (
                    source_refresh and self._source_refresh != "original"):
                self._source_refresh = source_refresh
            self._wake.set()
        return True

    def submit_export(self, hidden_axes, default_location, format="ttf"):
        if state.ORIGINAL_PATH is None:
            return None
        job = {"kind": "export", "hidden_axes": list(hidden_axes),
               "default_location": default_location, "format": format}
        with self._lock:
            self._pending_exports.append(job)
            self._wake.set()
            return self._job_id(job)

    def submit_compress(self, font_path):
        """Lowest priority: refresh the WOFF2 cache for the current build."""
        with self._lock:
            self._pending_compress = {"kind": "compress",
                                      "font_path": str(font_path)}
            self._wake.set()

    def cancel_all(self):
        """A new source was loaded: drop everything, kill the worker."""
        with self._lock:
            self._rebuild_wanted = False
            self._source_refresh = None
            self._pending_exports = []
            self._pending_compress = None
            running = self._running
        if running:
            self._kill(running)

    def cancel(self, job_id):
        with self._lock:
            for q in (self._pending_exports,):
                for job in list(q):
                    if self._job_id(job) == job_id:
                        q.remove(job)
                        # Never ran, so _finish never records it: a client
                        # polling by id must still see a terminal state.
                        self._record_result({"job_id": job_id, "kind": job["kind"],
                                             "success": False, "cancelled": True,
                                             "error": "cancelled"})
                        return True
            running = self._running
            if running is None or self._job_id(running) != job_id:
                return False
        self._kill(running)
        return True

    def is_building(self):
        """For the UI's building state: a build running or wanted. Exports
        and WOFF2 compression are background work — they never touch the
        served font, so the preview doesn't veil for them."""
        with self._lock:
            return ((self._running is not None and self._running["kind"] == "build")
                    or self._rebuild_wanted)

    def wait_for_idle(self, timeout=3600.0):
        """POST /api/build + the CLI wait on this; the GIL is free while
        waiting. Returns the last build's success."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                idle = (self._running is None and not self._rebuild_wanted
                        and not self._pending_exports)
            if idle:
                return bool(self._last_build_ok)
            time.sleep(0.25)
        return False

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def _job_id(self, job):
        if "id" not in job:
            JobQueue._seq = getattr(JobQueue, "_seq", 0) + 1
            job["id"] = "%s-%d-%d" % (job["kind"], int(time.time() * 1000), JobQueue._seq)
        return job["id"]

    def status(self):
        with self._lock:
            running = None
            if self._running is not None:
                running = {"job_id": self._job_id(self._running),
                           "kind": self._running["kind"],
                           "elapsed": round(time.time() - self._running["started"], 1),
                           "phase": self._read_phase(self._running)}
            queued = ([{"job_id": self._job_id(j), "kind": j["kind"]}
                       for j in self._pending_exports]
                      + ([{"kind": "build"}] if self._rebuild_wanted else [])
                      + ([{"kind": "compress"}] if self._pending_compress else []))
            last = self._last_result
        return {"running": running, "queued": queued, "last": last,
                "sizes": self.export_sizes()}

    def job_status(self, job_id):
        """One job's state by id: ``queued`` / ``running`` / ``finished``
        (+ ``success``, ``cancelled``, ``error``, ``artifact_ready``), or
        None when the id is unknown (never submitted, or evicted after
        KEEP_RESULTS newer results). Finished is checked first: _finish
        records the result before _running clears, and a recorded result
        means the artifact is already promoted."""
        with self._lock:
            result = self._results.get(job_id)
            if result is not None:
                return {"job_id": job_id, "kind": result.get("kind"),
                        "state": "finished",
                        "success": bool(result.get("success")),
                        "cancelled": bool(result.get("cancelled")),
                        "error": result.get("error"),
                        "artifact_ready": (result.get("kind") == "export"
                                           and bool(result.get("success"))
                                           and self.export_artifact(job_id) is not None)}
            running = self._running
            if running is not None and self._job_id(running) == job_id:
                return {"job_id": job_id, "kind": running["kind"], "state": "running",
                        "elapsed": round(time.time() - running.get("started", time.time()), 1),
                        "phase": self._read_phase(running)}
            for job in self._pending_exports:
                if self._job_id(job) == job_id:
                    return {"job_id": job_id, "kind": job["kind"], "state": "queued"}
        return None

    def _record_result(self, result):
        """Caller holds ``_lock``. Bounded: the oldest result goes first."""
        jid = result.get("job_id")
        if jid is None:
            return
        self._results[jid] = result
        self._results.move_to_end(jid)
        while len(self._results) > KEEP_RESULTS:
            self._results.popitem(last=False)

    def export_sizes(self):
        """TTF exact; WOFF2 exact when the cache matches the current build,
        else an estimate from the last known ratio (fallback 8%)."""
        font = state.VARIABLE_FONT_PATH
        if not font or not Path(font).exists():
            return {"ttf_bytes": None, "woff2_bytes": None, "woff2_exact": False}
        font = Path(font)
        ttf_size = font.stat().st_size
        cache = font.with_suffix(".woff2")
        key = font.with_suffix(".woff2.json")
        if cache.exists() and key.exists():
            try:
                k = json.loads(key.read_text())
                if k.get("ttf_size") == ttf_size and k.get("ttf_mtime") == font.stat().st_mtime:
                    return {"ttf_bytes": ttf_size, "woff2_bytes": cache.stat().st_size,
                            "woff2_exact": True}
            except Exception:
                pass
        ratio = None
        if cache.exists() and key.exists():
            try:
                k = json.loads(key.read_text())
                if k.get("source_bytes"):
                    ratio = k["woff2_bytes"] / k["source_bytes"]
            except Exception:
                ratio = None
        est = round(ttf_size * (ratio if ratio else 0.08))
        return {"ttf_bytes": ttf_size, "woff2_bytes": est, "woff2_exact": False}

    def export_artifact(self, job_id):
        exports = self._exports_dir()
        for p in sorted(exports.glob(f"{job_id}-*")):
            if p.suffix in (".ttf", ".woff2"):
                return p
        return None

    # ------------------------------------------------------------------
    # supervisor
    # ------------------------------------------------------------------

    def _supervise(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            while True:
                job = self._next_job()
                if job is None:
                    break
                try:
                    self._run_one(job)
                except Exception as exc:
                    # kill the supervisor: that's the wedge this rework exists
                    # to remove. Clear running, record the failure, move on.
                    import traceback
                    traceback.print_exc()
                    with self._lock:
                        self._running = None
                    self._finish(job, {"job_id": self._job_id(job),
                                       "kind": job["kind"], "success": False,
                                       "error": f"supervisor error: {exc}"})

    def _next_job(self):
        with self._lock:
            if self._running is not None:
                return None
            if self._pending_exports:
                job = self._pending_exports.pop(0)
            elif self._rebuild_wanted:
                self._rebuild_wanted = False
                refresh, self._source_refresh = self._source_refresh, None
                job = {"kind": "build", "source_refresh": refresh}
            elif self._pending_compress is not None:
                job, self._pending_compress = self._pending_compress, None
            else:
                return None
            # Running from THIS moment: between pick-up and the worker's
            # launch (staging) waiters must not see a false idle gap.
            self._running = job
            return job

    def _run_one(self, job):
        jid = self._job_id(job)
        job["started"] = time.time()
        try:
            if job["kind"] == "build" and job.get("source_refresh") in ("original", "source"):
                # A source change landed mid-build: re-resolve the active
                # source FIRST — the shadow still reflects the pre-save
                # state, and both the CSV hook and the staging below read
                # from it (a stale one syncs old instances and builds old
                # outlines while looking like success).
                resolve = getattr(self.server, "_resolve_active_source", None)
                if resolve is not None:
                    resolve()
                # The CSV sync stays server-side (the server owns sidecar
                # writes) and runs BEFORE the snapshot, so the job builds
                # the new state.
                hook = state._SYNC_CSV_HOOK
                if hook is not None:
                    hook()
            self._stage(job)
        except Exception as exc:
            with self._lock:
                self._running = None
            self._finish(job, {"job_id": jid, "kind": job["kind"], "success": False,
                               "error": f"could not stage the job: {exc}"})
            return
        if job.get("cancelled"):  # cancelled while staging: don't launch
            with self._lock:
                self._running = None
            self._finish(job, {"job_id": jid, "kind": job["kind"], "success": False,
                               "cancelled": True, "error": "cancelled"})
            return
        spec_path = job["dir"] / "spec.json"
        spec_path.write_text(json.dumps(job["spec"]))
        cmd = (list(self._worker_cmd) if self._worker_cmd
               else [sys.executable, "-m", "avar2_studio.build_worker"])
        proc = subprocess.Popen(
            cmd + [str(spec_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)  # own process group: cancel = killpg
        with self._lock:
            job["proc"] = proc
        log.info(f"[jobs] {jid} started ({job['kind']})")
        proc.wait()
        result = self._read_result(job)
        self._finish(job, result)
        # Running until the result is READ and promoted: clearing this right
        # after proc.wait() let wait_for_idle answer with the PREVIOUS
        # build's outcome in the gap.
        with self._lock:
            self._running = None

    def _kill(self, job):
        job["cancelled"] = True
        proc = job.get("proc")
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def _read_result(self, job):
        result_path = job["dir"] / "result.json"
        try:
            return json.loads(result_path.read_text())
        except Exception:
            if job.get("cancelled"):
                return {"job_id": self._job_id(job), "kind": job["kind"],
                        "success": False, "cancelled": True, "error": "cancelled"}
            return {"job_id": self._job_id(job), "kind": job["kind"],
                    "success": False,
                    "error": "the build worker died without a result (killed or crashed)"}

    def _finish(self, job, result):
        jid = self._job_id(job)
        result.setdefault("job_id", jid)
        result.setdefault("kind", job["kind"])
        log.info(f"[jobs] {jid} finished: success={result.get('success')} "
              f"in {result.get('duration', '?')}s")
        if result.get("success"):
            try:
                self._promote(job, result)
            except Exception as exc:
                result = dict(result, success=False,
                              error=f"could not promote the artifact: {exc}")
        if job["kind"] == "build":
            self._apply_build_state(result)
            self._last_build_ok = bool(result.get("success"))
            if result.get("success"):
                self._maybe_compress()
        with self._lock:
            self._last_result = result
            self._record_result(result)
        if job.get("dir"):
            shutil.rmtree(job["dir"], ignore_errors=True)

    # ------------------------------------------------------------------
    # staging + promotion
    # ------------------------------------------------------------------

    def _job_dir(self, job):
        base = self.server._get_preview_dir()
        if base is None:
            raise RuntimeError("no source loaded")
        d = Path(base) / "jobs" / self._job_id(job)
        d.mkdir(parents=True, exist_ok=False)
        return d

    def _stage(self, job):
        """Copy the source set into the job dir and fill in the spec."""
        job["dir"] = self._job_dir(job)
        src_dir = job["dir"] / "src"
        src_dir.mkdir()
        orig = Path(state.ORIGINAL_PATH)
        stem = orig.stem
        copies = [orig]
        for suffix in SIDECAR_SUFFIXES:
            p = orig.with_name(stem + suffix)
            if p.exists():
                copies.append(p)
        legacy = orig.parent / LEGACY_CSV
        if legacy.exists():
            copies.append(legacy)
        preview = Path(self.server._get_preview_dir())
        small = preview  # read before the job dir exists under it
        for name in PREVIEW_FILES:
            p = small / name
            if p.exists():
                copies.append(p)
        for p in copies:
            target = src_dir / p.name
            if p.parent == preview:
                target = src_dir / ".avar2-studio" / p.name
                target.parent.mkdir(exist_ok=True)
            shutil.copy2(str(p), str(target))
        # The control-axis shadow is derived state EXCEPT for the drawings
        # in it: until capture_outlines writes them into the sidecar, a
        # Fontra-drawn brace outline exists only here. The worker's regen
        # preserves a staged shadow and silently rebuilds from the sidecar
        # alone when it is missing — so a build would drop every uncaptured
        # drawing. .glyphs only: the designspace shadow regen ignores
        # outlines, and its shadow references pooled UFOs we do not stage.
        # shadow/transformed/ stays out: the worker re-derives it from the
        # staged shadow.
        if orig.suffix == ".glyphs":
            from . import control_axes
            shadow = control_axes.shadow_path_for(orig)
            if shadow.exists():
                target = src_dir / ".avar2-studio" / "shadow" / orig.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(shadow), str(target))
        if orig.suffix == ".designspace":
            for ufo in orig.parent.glob("*.ufo"):
                if ufo.is_dir():
                    shutil.copytree(str(ufo), str(src_dir / ufo.name))
        # The relocation memo outlives the job dir: it lives next to the built
        # fonts, which the supervisor promotes into.
        from .pipeline import RELOCATION_MEMO_DIRNAME
        spec = {"job_id": self._job_id(job), "kind": job["kind"],
                "job_dir": str(job["dir"]),
                "memo_dir": str(Path(self.server._get_avar2_font_dir())
                                / RELOCATION_MEMO_DIRNAME),
                "original_path": str(src_dir / orig.name),
                "use_fontc": bool(state.USE_FONTC),
                "env": {k: os.environ.get(k) for k in ENV_KEYS},
                "progress_path": str(job["dir"] / "progress.jsonl"),
                "result_path": str(job["dir"] / "result.json"),
                "out_dir": str(job["dir"] / "out")}
        if job["kind"] == "export":
            current = state.VARIABLE_FONT_PATH
            if not current or not Path(current).exists():
                raise RuntimeError("no built font to export from")
            staged_font = src_dir / Path(current).name
            shutil.copy2(str(current), str(staged_font))
            # The active source (possibly the transformed build copy) is
            # staged too — a server-side shadow regen could otherwise
            # rewrite it under the worker's feet.
            active = Path(state.GLYPHS_PATH)
            if active.resolve() == Path(state.ORIGINAL_PATH).resolve():
                staged_active = spec["original_path"]
            else:
                staged_active = src_dir / ("active-" + active.name)
                shutil.copy2(str(active), str(staged_active))
            spec.update({"current_font": str(staged_font),
                         "glyphs_path": str(staged_active),
                         "hidden_axes": job["hidden_axes"],
                         "default_location": job["default_location"],
                         "format": job.get("format", "ttf")})
        elif job["kind"] == "compress":
            spec["font_path"] = job["font_path"]
        job["spec"] = spec

    def _promote(self, job, result):
        """Supervisor-only writes into shared state (the plan's discipline).
        Everything promotes ATOMICALLY (same-filesystem rename): the browser
        can fetch the served font at any moment, and a truncate-and-copy
        window hands it a torn font that fails to parse — the preview falls
        back to the system font until the next build."""
        if job["kind"] == "build":
            built = result.get("built_font")
            if not built or not Path(built).exists():
                raise RuntimeError("worker reported success but produced no font")
            dst = Path(self.server._get_avar2_font_dir()) / Path(built).name
            shutil.move(built, str(dst))  # jobs/ and build/ share a filesystem
            state.VARIABLE_FONT_PATH = dst
            reap_build_dir(dst)
            # The regenerated gftools config is the current one — the
            # export row-lookup reads it (from snapshots).
            cfg = Path(state.ORIGINAL_PATH).parent / ".avar2-studio" / "config.yaml"
            job_cfg = job["dir"] / "src" / ".avar2-studio" / "config.yaml"
            if job_cfg.exists():
                shutil.move(str(job_cfg), str(cfg))
        elif job["kind"] == "export":
            artifact = result.get("artifact")
            if not artifact or not Path(artifact).exists():
                raise RuntimeError("export produced no artifact")
            exports = self._exports_dir()
            dst = exports / f"{self._job_id(job)}-{result['artifact_name']}"
            shutil.move(artifact, str(dst))
            self._reap_exports()
        elif job["kind"] == "compress":
            artifact = result.get("artifact")
            if not artifact or not Path(artifact).exists():
                return
            font = Path(job["font_path"])
            dst = font.with_suffix(".woff2")
            shutil.move(artifact, str(dst))
            key = {"ttf_size": font.stat().st_size, "ttf_mtime": font.stat().st_mtime,
                   "woff2_bytes": result.get("woff2_bytes"),
                   "source_bytes": result.get("source_bytes")}
            font.with_suffix(".woff2.json").write_text(json.dumps(key))

    def _apply_build_state(self, result):
        for key in ("last_build_status", "last_build_error", "last_avar2_error",
                    "last_origin_relocation_error", "last_origin_relocation_memo",
                    "last_grade_error", "last_source_transform_error",
                    "last_transform_error", "last_origin_warning", "last_build_time"):
            if key in result:
                setattr(state, key.upper(), result[key])
        if not result.get("success") and "last_build_status" not in result:
            if result.get("cancelled"):
                # A cancel is not a broken source: leave the previous
                # status alone — "ok" keeps the gate open, a prior
                # "failed" (cancelled retry) keeps it closed.
                pass
            else:
                # The worker died before it could report state (kill -9,
                # crash) — fail loudly, keep serving the last-good font.
                state.LAST_BUILD_STATUS = "failed"
                state.LAST_BUILD_ERROR = result.get("error") or "build worker failed"

    def _maybe_compress(self):
        font = state.VARIABLE_FONT_PATH
        if not font:
            return
        sizes = self.export_sizes()
        if sizes["ttf_bytes"] and not sizes["woff2_exact"]:
            self.submit_compress(font)

    def _exports_dir(self):
        d = Path(self.server._get_preview_dir()) / "exports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _reap_exports(self):
        exports = sorted(self._exports_dir().iterdir(),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for old in exports[KEEP_EXPORTS:]:
            old.unlink(missing_ok=True)

    def _read_phase(self, job):
        progress = job.get("dir") and job["dir"] / "progress.jsonl"
        if not progress or not progress.exists():
            return None
        try:
            lines = progress.read_text().strip().splitlines()
            for line in reversed(lines[-50:]):
                entry = json.loads(line)
                if "phase" in entry:
                    return entry["phase"]
        except Exception:
            return None
        return None


# Created by server.py at import time.
JOBS = None
