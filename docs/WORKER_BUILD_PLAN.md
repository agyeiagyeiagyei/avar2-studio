# Worker-process build rework — takeover plan

Audience: the agent taking over this work. Repo: `avar2-studio`
(this repo). Test font lives in `~/Documents/Crispy` (design WIP —
never commit anything there). Use `.venv` in this repo for everything
(`source .venv/bin/activate`); the Crispy venv lacks flask_sock.

## Problem

The studio server (`src/avar2_studio/server.py`) is a single-process
Flask app. Font builds, source resolution, and exports run on Python
threads inside that same process. CPython's GIL means a build that
spends minutes in fontTools/compile code **starves every HTTP
handler** — the UI reads as "hung" even though the server is healthy.

Every stability incident of the 2026-10 sessions traces to this one
cause:

- **Frontend "static-mode latch":** during a build, `/api/health`
  couldn't answer within the frontend's probe timeout, so the UI
  silently fell back to static-bundle mode and stayed there (stale
  tabs, "No font loaded"). Mitigated with a retry ladder
  ([1500, 4000, 8000] ms) + blind-static auto-reload in
  `frontend/src/static-api.js` (`selectApiMode`) — a workaround, not
  a fix.
- **Download/export hangs:** clicking Download during (or right
  after triggering) a build queues the request behind the build;
  the user saw "nothing happens", then the download fired on hard
  refresh when the socket was re-made.
- **Multi-minute rebuild grinds:** adding slant pushed in-process
  build time past 15 min at one point (GC thrash on a grown heap —
  see `_gc_paused`, server.py:1184). GC pauses are mitigated, but the
  requests still serialize behind the build.
- **BUILDING wedge:** a build thread stuck 30+ min in a munmap storm
  (forced `gc.collect()` on a huge heap — since removed) left the
  global `BUILDING` flag true with no way to cancel or recover except
  killing the server.

A worker **process** fixes the class: the Flask process stays
responsive no matter what the build does, builds become cancellable
(kill the worker), and a wedged build can't poison server state.

## Current architecture (what you're replacing)

All in `server.py` unless noted. Line numbers are as of this commit.

- `trigger_build()` — server.py:1382. POST `/api/build`. Spawns
  `_run_build` on a `threading.Thread`.
- `_run_build()` — server.py:1424. Sets global `BUILDING = True`
  (1461), calls `_resolve_active_source()` then
  `_perform_avar2_build()`, clears `BUILDING` in a finally (1491).
- `_resolve_active_source()` — server.py:1208. Decides which source
  feeds the build (shadow regeneration via
  `control_axes.regenerate_shadow`, transform chain, etc.). CPU-heavy;
  wrapped in `_gc_paused`.
- `_gc_paused()` — server.py:1184. Context manager: `gc.disable()`
  for the duration, re-enable in finally. **Deliberately does NOT
  force a `gc.collect()`** — a forced monolithic collect on the grown
  heap caused the 30-min munmap wedge. Keep that property in the
  worker (or let the worker process die after N builds instead).
- `_perform_avar2_build()` — server.py:7031. Outer build driver; also
  sets/clears `BUILDING` (7060/7167).
- `_build_in_process(...)` — server.py:6771. The actual pipeline:
  fontc compile → step 3.5 default-origin relocation (gated by
  `AVAR2_RELOCATE_BUILD_DEFAULT`, default ON — read the comment there
  before touching it, there is real avar2 physics in it) → avar2
  table generation → `_apply_transform_chain` (server.py:1074: SPAC
  via native `spac-inject` or fontTools fallback, grades, rounding,
  slant) → `_stamp_control_instance_values`.
- `export_font()` — server.py:3667. POST export endpoint. Runs the
  same heavy machinery inline **on the request thread** (sets
  BUILDING at 3760); `_build_export_source` (server.py:3462) stages
  an interpolated-origin source for "set default" exports.
- Watchers: a watchdog thread on the `.glyphs` source + sidecars
  triggers rebuilds with suppression windows (search
  `_SUPPRESS`/`watch` in server.py). These must keep working — the
  studio's core loop is "save in Glyphs → auto rebuild".
- Locks: `SIDECAR_LOCK` (server.py:224) around CSV/sidecar writes;
  `_INSTANCE_VALUES_LOCK` in `control_axes.py`. These protect files
  the worker will also read — see "shared state" below.

## Target architecture

One **worker subprocess** (multiprocessing or `subprocess` running a
`python -m avar2_studio.build_worker` entrypoint — prefer the latter:
a fresh interpreter per build sidesteps the grown-heap GC problem
entirely and makes `_gc_paused` unnecessary in the worker) plus a
small job layer in the server:

1. **Job queue in the Flask process.** `trigger_build` enqueues a job
   {source snapshot paths, env flags, job id} and returns immediately.
   One job runs at a time; a new request while one runs marks a
   "rebuild wanted" bit instead of queueing a pile (the watcher
   already debounces — keep its semantics).
2. **Worker executes the pipeline.** Move `_resolve_active_source` +
   `_build_in_process`/`_perform_avar2_build` + the transform chain
   into the worker entrypoint. Inputs: file paths + a JSON job spec.
   Output: the built font path + structured result JSON (warnings
   like `LAST_ORIGIN_WARNING`, timings, sizes) on stdout or a result
   file. The server never imports fontTools-heavy build code in its
   own process again.
3. **Progress + status.** Worker writes progress events (phase name,
   percent if cheap) to a line-delimited JSON pipe/file; server tails
   it and exposes `/api/build/status` (job id, phase, elapsed,
   last-result). `BUILDING` global becomes derived state ("a job is
   running"), never set by request threads.
4. **Cancellation.** DELETE `/api/build/<job id>` (or re-trigger)
   kills the worker process group. This is the capability that was
   impossible in-thread.
5. **Exports go through the same queue.** `export_font` becomes:
   enqueue export job → 202 + job id → frontend polls status →
   GET the artifact when done. Keep the current synchronous path for
   the cheap no-rebuild case (serving the already-built font —
   `/api/font?download=1` — stays as-is).
6. **Shared state discipline.** The worker only *reads* sources and
   *writes* its own output dir; the server owns sidecar writes. Take
   `SIDECAR_LOCK` only in the server. Snapshot the source set (copy
   to a job workdir, as `_build_export_source` already does for
   exports) so a mid-build save in Glyphs can't half-update a build —
   today this is a real race.

### Mitigations that become removable vs keep

- Removable after the rework: the static-api retry ladder's longer
  rungs (health answers instantly once builds are out-of-process),
  the blind-static auto-reload, `_gc_paused` (if worker-per-build),
  "download hangs during build" workarounds.
- Keep: building-aware UI states (disabled Download with reason,
  "Preparing…" on export) — they become accurate instead of
  defensive; the boot-time early `setFamilyName` in App.jsx; the
  watcher suppression windows.

## Acceptance criteria

1. During a full rebuild of Crispy (all transforms: SPAC native
   inject, grades, ROND, slant), every `/api/*` endpoint answers in
   <200 ms, and the Preview tab download of the *previous* build
   works mid-build.
2. Hard refresh mid-build never latches static mode.
3. A build can be cancelled from the UI and a new one started; the
   server survives `kill -9` of the worker with a clean error state.
4. A save in Glyphs mid-build produces a correct follow-up build
   (no torn source reads).
5. `pytest` green; add tests for the job layer with a stub worker
   (fast fake build) — don't shell out to fontc in unit tests.
6. Build result parity: byte-identical (or shape-identical: same
   axes, same mapped-stop spot checks — rest 663.1/202.5/121.8,
   wght 399 → 662.9 on current Crispy) with the in-process build.

## Gotchas learned the hard way

- Don't force `gc.collect()` on a grown heap (munmap storm, 30-min
  wedge). Worker-per-build makes this moot.
- The relocation gate (step 3.5) and the no-reloc row-drop in
  `_build_in_process` encode avar2 physics (zero-anchored tents →
  valley across the default cross when the ORIGIN row is dropped).
  Don't "simplify" them; read the inline comments.
- The native SPAC injector (`wasm/fontc-web/target/release/spac-inject`,
  source `wasm/fontc-web/src/bin/spac-inject.rs`) is found via
  `AVAR2_SPAC_INJECT` or repo-relative path in
  `transforms/builtin_spac_widthaware.py::_native_injector` — the
  worker must inherit/compute the same resolution.
- Port 5070 is the user's current arrangement (log:
  `/tmp/avar2-5070.log`). Don't hardcode a different one.
- No `git commit`/`push` or other repo mutations without the user's
  explicit per-action approval. Crispy design sources (CSV, sidecars,
  .glyphs) are user WIP — never commit or "fix" them.
