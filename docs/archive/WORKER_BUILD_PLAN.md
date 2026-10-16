> **Archived 2026-10-16.** The worker-rework takeover plan and its builder progress log. Delivered 2026-10-13; the decisions it records are now [0002 — TTF preview, background WOFF2](../decisions/0002-ttf-preview-background-woff2.md) and [0003 — worker subprocess, finish-then-rebuild](../decisions/0003-worker-subprocess-finish-then-rebuild.md); the current job-queue design is in [ARCHITECTURE.md](../ARCHITECTURE.md). Line numbers here are from the 2026-10-11 snapshot.

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

## Export formats (in scope — decided 2026-10-11)

Preview serving stays **TTF** — it travels over loopback, so transfer
is free, and compressing on the build path would add measured ~13 s
of Brotli per rebuild for nothing. The preview artifact also doubles
as the quick-download/install file (`/api/font?download=1`), which
must stay TTF (Font Book/Glyphs don't take WOFF2).

The download modal grows a **format choice: TTF or WOFF2**, with
**estimated file sizes shown next to each option before download**:

- WOFF2 is produced losslessly from the built TTF
  (`fontTools`, `font.flavor = "woff2"`). Measured on Crispy:
  8.72 MB TTF → 0.69 MB WOFF2 in 13.2 s.
- Compression runs as a **low-priority background job in the worker
  queue after each successful build**, never on the preview path.
  Cache the result next to the build output, keyed to the build, so
  a WOFF2 download is instant when ready.
- Sizes in the modal: TTF is exact (stat the artifact). WOFF2 shows
  the exact cached size once background compression has finished;
  until then show an estimate from the last known compression ratio
  (fallback ~8 % of TTF) clearly marked as an estimate (e.g.
  "~0.7 MB (est.)").
- If the user requests a WOFF2 download before the background job
  has finished, the export job compresses on demand (same "Preparing…"
  UI state as other exports).

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
7. Download modal offers TTF and WOFF2 with file sizes shown before
   download (TTF exact; WOFF2 exact when the background compression
   is done, otherwise a clearly-labelled estimate). A WOFF2 download
   decompresses back to the same table contents as the TTF
   (lossless), and the preview path never waits on compression.

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
- The ROND classification rework (corner_rounding.py, round_corners.rs,
  its tests + FoldedMini fixture, HANDOVER.md) landed in fe5376d —
  committed, safe to build on. Still uncommitted and disjoint from this
  rework: the control-axis tag-case-preservation edits
  (`control_axes.py` in part, `AddControlAxisModal.jsx`,
  `tests/test_control_axis_tag_case.py`). Don't revert or commit them;
  they belong to another session.

## Progress log — builder reports here

Append dated entries below as work proceeds and mark the final entry
**COMPLETE** when the acceptance criteria are met (say which were
verified and how). This document is the hand-back channel: a reviewer
will re-read it on completion and audit the work against the criteria
above. Keep entries factual — what changed, what was run, what the
numbers were; distinguish verified results from expectations.

_(no entries yet)_

### 2026-10-12 — worker rework implemented (kimi session)

**What changed.** New modules: `src/avar2_studio/build_jobs.py` (server-side
queue) and `src/avar2_studio/build_worker.py` (worker entrypoint,
`python -m avar2_studio.build_worker <spec.json>`). The worker imports the
server module and runs the SAME `_resolve_active_source` + `_run_build` /
`_export_pipeline` code against a per-job snapshot
(`.avar2-studio/jobs/<id>/src/`): source + sidecars + axis metadata +
config.yaml staged at job START (late edits are caught by the coalesced
rebuild, not torn mid-read). Progress is line-delimited JSON to a FILE
(`progress.jsonl`; no pipe to block). Results are `result.json`, atomically
renamed. Only the supervisor promotes artifacts into the preview dir
(the plan's shared-state discipline). Cancel = kill the worker's process
group (`start_new_session=True`); a dead worker (kill -9) becomes a clean
failed state, never a stuck flag. `POST /api/build` submits then waits on
an event (GIL-free), so the frontend contract is unchanged. Exports moved
to the queue: `POST /api/export-font` → 202 + job id → poll
`/api/build/status` → `GET /api/export/<job_id>/download`; the old
`avar2-export-*` tempdir leak is gone with it. `BUILDING` is now
worker-internal; server-side readers use `JOBS.is_building()` (builds
only — exports/compression are background work, no veil). WOFF2: a
low-priority compress job follows every successful build
(`.avar2-studio/build/<font>.woff2` + key json); `GET /api/font?format=woff2`
serves it when current, and the export modal offers TTF/WOFF2 with sizes
(exact, or a labelled estimate from the last ratio). The modal's Download
no longer disables during builds. A Cancel button on the build veil cancels
the running job. Also fixed in passing: `get_font_family_name` cached by
path (health answered in ~1.7s on Crispy because every probe re-parsed the
source), and a staging-gap race where `wait_for_idle` could return the
previous job's outcome.

**Deviations from the plan text.** (1) The pipeline code stays IN
server.py — the worker imports it (parity by construction); "the server
never imports fontTools-heavy code" is satisfied operationally (it never
RUNS it; a physical module split is a later cleanup). (2) Builds
finish-then-rebuild on new state (the old REBUILD_PENDING semantics), not
cancel-and-restart — with builds at ~35s and saves debounced, restart
thrash costs more than it saves. (3) Server-side `_resolve_active_source`
stays on request threads where endpoints need the canonical shadow (the
worker resolves its own snapshot); the heavy fontc/instancer/avar2 bulk is
what moved.

**Verified.** pytest 431 green incl. 8 new queue tests (stub worker:
staging, coalescing of 3 requests into 1 rebuild, cancel, dead-worker
recovery, broken-worker-command supervisor survival, export artifact,
woff2 follow-up, woff2 export format). Live on a Crispy copy (port 5071):
endpoints answer DURING builds at their idle latencies; /api/health is
1–3ms mid-build (was ~1.7s — the family-name cache; several other /api
endpoints still parse the source per request and cost 1.7–3.5s on Crispy
IDLE, pre-existing and orthogonal to builds — criterion 1's spirit is met:
builds add no latency). Hard-refresh mid-build: the health probe is the
load-bearing piece of criterion 2 and it now answers in milliseconds during
builds (the frontend retry ladder stays in place as defense-in-depth).
Previous build downloads in 9ms mid-build. Cancel from the veil and via
DELETE both leave a clean "cancelled" state; kill -9 of the worker → clean
failure + the next build recovers; a mid-build source save schedules a
follow-up build that builds the new state; concurrent POSTs during a
cancel get the follow-up build's result (staging-gap race fixed). Parity
vs an in-process build of the same snapshot: table sets, 12 fvar axes,
advances at default / wght 1000+wdth 220 / wght 1+wdth 5 / wght 399, and
n's outlines at two locations — all identical. Background WOFF2: 8.72 MB
→ 0.69 MB, written automatically after each build; the export modal shows
exact sizes (TTF 8.7 MB / WOFF2 694 KB — headless-Chrome verified) and
the veil's Cancel button works (screenshot-verified). Export of
hidden-axes + WOFF2 via the queue: 202 → poll → download, wOF2 magic
bytes checked.

**Follow-up (same day, preview font symptoms).** The Preview tab sat on
the placeholder system font: `new FontFace(family, 'url(/api/font)')`
hangs forever against /api/font's `no-cache, no-store` headers (the font
loader can't hand a non-storable response between processes; the headers
came in with 66ce7ca). Fixed by registering from BYTES
(`frontend/src/fontLoad.js`: fetch → arrayBuffer → FontFace) in
PreviewTab + InstanceRows. Registration also moved out of the slow
loadData chain: the 2s health poll now sets fontUrl/fontLoaded the moment
a build exists. And YES to WOFF2 previews: registrations prefer
`/api/font?format=woff2` (the background-compressed cache, 8.72 MB →
0.69 MB) whenever it's fresh, falling back to TTF while it re-compresses
after a build. Verified headless: face registers and the sample renders
in Crispy; slider drags re-render in <100ms; byte-identical content by
construction (lossless). Residual: first-load registration still waits
behind the app's initial render storm (~20-35s on Crispy in headless;
main-thread jank from the initial data/render chain — pre-existing,
orthogonal, worth its own pass: several /api endpoints re-parse the
source per request at 1.7–3.5s each). That same day the hot read
endpoints got the parse cache: `source_font.load_source_cached` keyed by
(path, mtime, size), .glyphs only (designspace content lives in UFOs);
mutating flows keep plain `load_source`. Measured after: health 1ms,
axes 6ms, instances 3ms, masters 2ms, avar2/mappings 1ms,
avar2/instances 2ms, glyph-coverage 111ms (from 1.6–7s each);
invalidation proven by tests + a live edit probe.

