# 0003 — Builds run in a worker subprocess; a mid-build change finishes, then rebuilds

- **Status:** Accepted
- **Date:** plan 2026-10-11/12; shipped 2026-10-13 (`5196154`); cancel and idle semantics refined 2026-10-16 (sprint T0.4, T1.10)
- **Area:** `build_jobs.py`, `build_worker.py`, `server.trigger_build`

## Context

The studio was a single-process Flask app running builds, source
resolution and exports on Python threads. Under the GIL a build that
spends minutes in fontTools starved every HTTP handler. Every stability
incident of the 2026-10 sessions traced to that one cause: the frontend
"static-mode latch" (`/api/health` missed its probe timeout and the UI
fell back to the no-backend provider for the life of the tab), downloads
queued behind builds, 15-minute rebuilds from GC thrash on a grown heap
(the identical source-transform chain measured 42 s in a fresh process
vs 8+ minutes after the heap had grown), and a `BUILDING` flag wedged
true for 30+ minutes by a forced `gc.collect()` munmap storm, with no
way to cancel.

## Decision

- **One worker subprocess per job**: `python -m avar2_studio.build_worker
  <spec.json>`. A fresh interpreter per build sidesteps the grown-heap GC
  problem entirely; `_gc_paused` deliberately never forces a collect.
- **Job queue in the Flask process** (`build_jobs.JobQueue`, supervisor
  thread started at import): builds **coalesce** through a rebuild-wanted
  bit (the semantics `REBUILD_PENDING` had), exports queue FIFO,
  compression runs when nothing else is pending.
- **Snapshot staging**: the original, its sidecars (`SIDECAR_SUFFIXES`),
  the small preview-dir files (`PREVIEW_FILES`), the control-axis shadow
  and any UFOs are copied to `.avar2-studio/jobs/<id>/src/` at job start,
  so a save in Glyphs mid-build cannot tear the worker's inputs.
- **Progress and result** are files: line-delimited JSON `progress.jsonl`
  (phases plus a stderr tee) and `result.json`, atomically renamed.
- **Supervisor-only promotion**: the worker writes only its job dir; the
  supervisor moves artifacts into the preview dir with same-filesystem
  renames and applies the build-state globals.
- **Cancel = kill the process group** (`start_new_session=True`); a dead
  worker (kill -9, crash) is a clean failed state, never a stuck flag.
- **Finish-then-rebuild, not cancel-and-restart**: with builds at ≈35 s
  and saves debounced, restart thrash costs more than it saves; a change
  mid-build sets the rebuild-wanted bit and the next pass re-resolves and
  re-stages (`_run_one`'s `source_refresh` branch).
- **Parity by construction**: the pipeline code stays in `server.py`; the
  worker imports the module and runs the same `_resolve_active_source` +
  `_run_build` / `_export_pipeline` against its snapshot
  (`_bind_server_globals`). A Flask-free physical split is sprint T3.1.
- Server-side `_resolve_active_source` stays on request threads where
  endpoints need the canonical shadow (serialized on `_RESOLVE_LOCK`
  since 2026-10-16).

Refinements since: a **cancelled** job leaves `LAST_BUILD_STATUS`
untouched (previous-value semantics — a cancelled retry on a broken
source must keep the edit gate closed; T0.4), and `_running` clears only
after the result is read and promoted so `wait_for_idle` reports the
current build's outcome (T1.10).

## Consequences

- Endpoints answer at idle latencies during builds; `/api/health` went
  from ≈1.7 s to 1–3 ms mid-build; the previous build downloads in 9 ms
  mid-build. Parity vs an in-process build of the same snapshot: table
  sets, 12 fvar axes, advances and outlines identical.
- Two sources of truth exist for the shadow (server-side canonical vs
  job snapshot) — the cause of the drawing-loss and stale-read bugs fixed
  in Tier 0 (T0.3, T0.7) and still the main state-ownership question
  (audit §8.4).
- `POST /api/build` blocks until the queue is idle (GIL-free wait);
  mid-build tests must start the build out of band.

## Where it lives

- `build_jobs.py` (module docstring is the contract), `build_worker.py`.
- `server.trigger_build`, `server._run_build`, `server._gc_paused`.
- `tests/test_build_jobs.py` (stub worker: staging, coalescing, cancel,
  dead worker, exports, WOFF2), `tests/test_build_gate.py`,
  `tests/test_worker_shadow.py` (first real-worker end-to-end build).
- History: archived `WORKER_BUILD_PLAN.md`.
