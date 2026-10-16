# Cleanup sprint 2026-10 — log

Append-only. Per item: what changed (files/symbols), commands run with
results, and what is inferred vs verified.

## 2026-10-16 — Step 0 / T0.1 (safety push), main checkout

- Baseline [verified]: `.venv/bin/python -m pytest -q` in the main
  checkout → **441 passed in 30.83 s** (includes the untracked
  kinetikern/tag-case tests, which pytest already collected).
- Committed the multi-agent WIP on `github-pages` in three groups:
  - `3693c19` Kinetikern: vendored engine (`native/kinetikern2`, MIT),
    `src/avar2_studio/kinetikern/` (loader/propose, sidecar, bake),
    server routes, `-kinetikern.json` added to `SIDECAR_SUFFIXES`,
    Kinetikern tab replaces the Space tab (SpaceTab kept but orphaned —
    intent decision is T1.17), README/HANDOVER Kinetikern docs.
    Staging checked: no `__pycache__`, no `target/`, no binaries.
  - `6996bbd` tag-case preservation + `tag_by_index` authored-case label
    fix (`control_axes.py`, `AddControlAxisModal.jsx`,
    `tests/test_control_axis_tag_case.py`).
  - `17360ba` `docs/CODEBASE_AUDIT.md` (this sprint's source of truth).
- Push [verified]: `origin/github-pages` `b45084c..17360ba` (7 prior +
  3 new commits). Working tree clean.
- Branch [verified]: `cleanup-2026-10` at `17360ba`, pushed with `-u`.
- Worktree [verified]: `~/Documents/avar2-studio-sprint` on
  `cleanup-2026-10`; with `PYTHONPATH=~/Documents/avar2-studio-sprint/src`,
  `avar2_studio.__file__` resolves inside the worktree. Main checkout is
  back on `github-pages` at the same SHA (zero file changes), so the
  live studio on port 5070 is untouched. All sprint work happens in the
  worktree.
- Deferred [rule conflict]: the audit Tier-0 clause "run
  `capture_outlines` once on Crispy" writes the designer's live sidecar
  under `/Users/agyei/Documents/Crispy` — the sprint rules forbid that.
  Left for the user to run (one config-bundle export from the studio
  does it).

## T0.2 — fresh clone installs (6a9c99e)

- Reproduced [verified]: scratch clone of `cleanup-2026-10` (pre-fix) →
  `pip install -e . --no-deps` → `FileNotFoundError: Forced include not
  found: .../src/avar2_studio/static` (metadata-generation-failed).
- Root cause [verified]: `.gitignore` excluded the static **dir**
  (`src/avar2_studio/static/`), which made the later
  `!src/avar2_studio/static/.gitkeep` exception dead — git cannot
  re-include a file under an excluded directory. That is how the tracked
  `.gitkeep` could be deleted in `a550f7c` and why restoring the file
  alone would not stick.
- Fix: ignore only the dir's contents (`src/avar2_studio/static/*`),
  restored the (empty) `.gitkeep`, removed the `mkdir -p` workaround
  from `pages.yml`.
- Acceptance [verified]: fresh scratch clone of `6a9c99e` → venv →
  `pip install -e '.[dev]'` succeeded with no mkdir; clone contains
  `src/avar2_studio/static/.gitkeep`; `python -c "import avar2_studio"`
  OK from the clone's venv.

## T0.3 — worker snapshot keeps the shadow

- Mechanism [verified by reading]: the worker's `_resolve_active_source`
  calls `regenerate_shadow(staged_original)`; preservation
  (`_extract_brace_outlines`) reads the shadow only if it exists in the
  snapshot (`control_axes.py:844`). `_stage` copied original + sidecars +
  preview files but never `.avar2-studio/shadow/` → `preserved_layers={}`.
- Fix: `_stage` copies `control_axes.shadow_path_for(orig)` into
  `jobs/<id>/src/.avar2-studio/shadow/<name>` for `.glyphs` sources.
  Deviations from the brief's parenthetical, with reasons: `transformed/`
  NOT staged — `_resolve_active_source` step 3 re-derives it from the
  staged shadow every build; designspace shadow NOT staged — its regen
  ignores `outline` (audit §4) and references pooled UFOs that are not in
  the snapshot, so staging it could only break, never preserve.
- Tests [verified]: `test_stage_copies_the_shadow` (stub queue) and new
  `tests/test_worker_shadow.py` — first real-worker test: crispy-mini
  copy, `ymod` axis + brace on 'e', +250 hand edit left UNCARTURED in the
  shadow, real `JobQueue` (worker_cmd=None → `python -m
  avar2_studio.build_worker`), assert built font at {ymod:100, XOPQ:500}
  carries the +250 vs {ymod:0}. Fails without the fix (stash-checked:
  1 failed), passes with it (1 passed; full build ≈0.4 s + 0.3 s WOFF2).
  fontc resolved as `Path(sys.executable).with_name("fontc")` — robust in
  both checkouts (the REPO/.venv hard-code in older tests skips silently
  in the worktree).
- `pytest tests/test_build_jobs.py -q` → 10 passed in 6.39 s [verified].

## T0.4 — cancel does not trip the build gate

- Mechanism [verified by reading]: `DELETE /api/build/<id>` →
  `JOBS.cancel` → worker killpg → `_read_result` synthesizes
  `{success: False, cancelled: True}` → `_finish` → `_apply_build_state`,
  whose fallback branch stamped `LAST_BUILD_STATUS="failed"` for any
  result lacking a worker-reported status → `before_request` gate 409s
  all mutating requests until a build succeeds. `POST /api/build` does
  NOT re-stamp (it only reads `wait_for_idle`), so the queue was the
  only writer to fix.
- Fix: `build_jobs._apply_build_state` leaves `LAST_BUILD_STATUS`/
  `LAST_BUILD_ERROR` untouched when `result["cancelled"]` — previous
  value semantics (chose this over a distinct "cancelled": a cancelled
  retry on a broken source must keep the gate closed).
- Tests [verified]: `test_cancel_a_running_build` updated per the brief;
  `test_build_gate.py` +2 tests (cancel after ok → PUT passes; cancel
  after failed → PUT still 409). `pytest tests/test_build_jobs.py
  tests/test_build_gate.py -q` → 16 passed in 5.58 s.

## T0.5 — seeds follow master edits

- Root cause [verified by probe]: `regenerate_shadow` decided
  seed-vs-edit by `_paths_sig(preserved) != _paths_sig(default_master)`.
  A seed interpolates at its corner, so it differs from the default
  master by construction → `preserved_is_edit` was always True → seeds
  froze on any master edit. The `xyz.avar2studio.seed-sig` stamp was
  written (`_restamp_seeds`) and read (`_extract_brace_outlines`) but
  never consulted in regen.
- Fix (`control_axes.py`, seeding block): with a stamp present, compare
  `_geometry_sig(preserved.paths, preserved.width)` against it — match
  means untouched seed → re-interpolate; mismatch means hand-drawn →
  preserve. No stamp (pre-stamping shadow) → old default-master
  comparison, unchanged behaviour until a reseed stamps the layer.
- Probe [verified]: on crispy-mini, 'e' brace stamp matches before a
  master edit (`a1112a98…` == recomputed), regen after +37 master shift
  moves the seed exactly (37.0, 0.0).
- Tests [verified]: tests/test_seed_follows_masters.py (imports
  `_brace`/`_draw_into`/`_nodes` from test_capture_outlines) — both pass
  with the fix; the seed-follow test FAILS without it (stash-checked).
  One float-noise fix in the assertion (round after adding 37.0).
  Control-axis cluster (`-k "control or capture or seed or brace or
  editor or shadow"`) → 36 passed.
- Known limitation (pre-existing, logged not fixed): the sig covers
  paths+width only — a components/anchors-only edit to a seed still
  reads as untouched (same blind spot as `capture_outlines`).

## T0.6 — modal edit keeps drawings

- Mechanism [verified by reading both sides]: `list_control_axes`
  (server.py:4502) strips `outline`/`source_sig`, serving
  `has_outline`/`source_changed` instead; `LayersEditor.replaceLayer`'s
  carry-over (`oldEntry.outline`) therefore always reads undefined and
  the modal's remove+add delta arrives drawing-less.
- Fix (root cause server-side, per the brief): `apply_layer_delta`
  carries `outline`/`source_sig` from each removed stored entry to a
  re-added entry of the same glyph that lacks them — one-to-one in
  request order, never onto a `target` (computed) entry. Pure deletes
  keep deleting (no add → no carry).
- Tests [verified]: tests/test_layer_delta_carry.py — (1) endpoint: POST
  /api/control-axes/ymod/layers/delta with a coordinate move; outline +
  source_sig still in the sidecar at the new location AND in the
  regenerated shadow. Fails pre-fix (stash-checked: KeyError on the
  dropped outline). (2) no-donor delta is a clean no-op. (3) carry never
  lands on a computed entry. 3 passed.

## T0.7 — mid-build saves re-resolve

- Mechanism [verified by reading]: watcher mid-build branch
  (`GlyphsFileHandler.on_modified`, `JOBS.is_building()` true) only did
  `request_build(source_refresh=kind)`; `_run_one` then ran the CSV hook
  — which reads `GLYPHS_PATH`, still the pre-save shadow — and staged
  that same stale shadow. The not-mid-build path already resolved
  (server.py:8091).
- Fix, both branches per the brief:
  1. `build_jobs._run_one` source_refresh branch calls
     `server._resolve_active_source()` (getattr-guarded like the hook)
     BEFORE `_SYNC_CSV_HOOK` and `_stage`.
  2. The watcher's mid-build branch resolves immediately too, so read
     endpoints refresh during the running build (the observed "needed a
     PATCH to refresh" window), not only at follow-up staging time.
- Concurrency note [reasoned]: the running worker only touches its
  snapshot; server-side regen writes the shadow via
  `save_font_atomically` (tmp+rename), so a concurrent `_stage` copy2
  reads a whole file. The pre-existing unlocked-resolve hazard (audit
  §3) is T2.1 scope, not enlarged materially by one more caller.
- Test [verified]: `test_a_midbuild_save_resolves_before_syncing`
  records `resolve`/`csv` calls through the real queue (stub worker);
  asserts `["resolve", "csv"]`. Old code contains no resolve call, so
  the test fails by construction pre-fix (not stash-verified).
  `pytest tests/test_build_jobs.py -q` → 11 passed. server.py compiles
  and imports [verified]; the watcher branch itself is nested in
  `main()` — reading-verified only until T3.10 makes it testable.

## T0.8 — capture on Fontra save

- Mechanism [verified by reading]: studio-axis Fontra sessions serve
  `shadow_path_for(ORIGINAL_PATH)` (server.py:5559) — saves write the
  shadow. `capture_outlines`' sole caller was config-bundle export
  (`config_port.py`), so drawings sat in derived state until an export.
- Fix: module-level `server._capture_shadow_drawings()` (guard: no-op
  when no source or when GLYPHS_PATH == ORIGINAL_PATH; capture under
  SIDECAR_LOCK per the layer-mutation invariant), called from both
  Fontra-save watcher branches: the direct `is_source` branch (before
  the CSV sync) and the mid-build branch (before the re-resolve, so regen
  can't precede capture).
- Deliberately not in scope: the `.glif`/designspace branch
  (`capture_outlines` on .designspace is a no-op per audit §10 —
  unverified beyond that; parity question is T2.15); "drawer close"
  (watcher covers saves continuously; no close endpoint exists);
  the shadow dir not being watched when source transforms are active
  (watcher watches `transformed/` then, not `shadow/` — pre-existing
  watcher gap, belongs to T2.11 watcher work; logged here so it isn't
  lost).
- Tests [verified]: tests/test_capture_on_fontra_save.py — capture
  moves paths + source_sig into the sidecar; editing-original guard
  no-op; no-source no-op. 3 passed; server.py py_compile + import OK.

## Hand-off — Tier 0 (round 1)

- Full suite in the worktree [verified]:
  `PYTHONPATH=$PWD/src PATH=<repo>/.venv/bin:$PATH <repo>/.venv/bin/python -m pytest tests/ -q`
  → **446 passed, 8 skipped in 31.16–31.38 s**. The 8 skips are the
  pre-existing `REPO/.venv/bin/fontc` hard-codes (audit §6: relocation ×3,
  kinetikern ×3 +engine, kinetikern_inject ×2) — REPO resolves to the
  worktree, which has no `.venv`; they ran in the main-checkout baseline.
  All new Tier-0 tests run and pass (staging, real-worker e2e, gate ×2,
  seeds ×2, carry ×3, ordering, capture ×3).
- ruff / frontend gates: not yet applicable (T1.20/T1.29 add them).
- Tier-0 acceptance probes: each item's check is in its section above;
  T0.2's fresh-clone install and T0.3's fail-without-fix e2e included.
- Not done by rule: the audit's "run capture_outlines once on Crispy"
  (would write the designer's live sidecar) — left for the user.

## Round 2 — verifier findings (8a06878)

findings.md: T0.1–T0.6 VERIFIED, T0.8 verified-but-inert on transform
configs, T0.7 FAILED (watcher-side resolve storms: 21 resolves / 18
queued builds from one glyphsLib save on a Crispy copy; mid-write EOF
parse; two overlapping resolves). NEW: T0.9 (shadow un watched when
transforms on), T0.10 (glyphsLib 6.16 tuple-transform breaks
grade_shadow + component capture), T0.11 (.venv not gitignored).
T0.11 was folded into the T0.10 commit series (hygiene, one line).

## T0.7 fix + T0.9 — one commit (ad014cd)

- T0.7 root causes addressed per the verifier's (1)–(3):
  1. `_SUPPRESS_WATCHDOG_UNTIL` is now re-armed AFTER the
     `shadow/transformed/` save — the post-regen window (2.5 s) expired
     during the multi-second transform pass, so the server's own write
     re-fired the mid-build branch (the storm).
  2. `_resolve_active_source` serialized on a new module-level
     `_RESOLVE_LOCK` (RLock). Lock order: the config import nests
     SIDECAR→RESOLVE; resolve never takes SIDECAR, so no cycle.
  3. Watcher acts only after the file's mtime settles
     (`_wait_for_save_to_settle`, 0.5 s) and the resolve retries once
     (`_resolve_source_after_save`) — kills the mid-write EOF crash.
     Applied to the mid-build branch AND the non-mid-build
     is_original/is_source branches.
- T0.9: `_classify_watched_path` (module-level, testable) classifies
  source/original/shadow/glif; "shadow" maps onto the source flow
  (settle → capture → sync → build). Observer now also watches
  `shadow_dir_for(ORIGINAL_PATH)` (non-recursive) when a shadow is
  active and the dir isn't already covered. Not covered (pre-existing,
  logged): the watcher is scheduled once in `main()` — a shadow created
  AFTER launch still unwatched until restart (the reschedule gap, audit
  §1/T3.10).
- Option (4) (drop the watcher-side resolve) not needed: (1)–(3) hold in
  the probe below.
- Tests [verified]: tests/test_watcher_save_flow.py — settle timing (old
  file returns <0.3 s; fresh file waits ≥0.5 s), retry-once + final
  warning, serialization (4 threads → max depth 1; FAILS without the
  lock, stash-checked), classifier incl. transforms-on shadow case.
  Full suite after the edits: **452 passed, 8 skipped in 34.5 s**.

## T0.10 + T0.11 — fresh-install compatibility

- T0.10 root cause [verified]: glyphsLib 6.16 makes `GSComponent.transform`
  a plain tuple. `grade_shadow.py:187` (`Transform(*tc.transform.value)`)
  raised AttributeError (the verifier's 2 fresh-clone failures);
  `control_axes._parts_to_outline` caught the AttributeError and stored
  the component WITHOUT its transform — silent data loss on capture.
  Fix: `getattr(t, "value", t)` at both read sites (tuple on 6.16,
  .value wrapper before). Write sites (`Transform(*...)`) are fine on
  both. Other `.transform` uses are in Glyphs plugins, which run on
  Glyphs' own runtime — untouched.
- Pins: `glyphsLib>=6.4,<7`, `fontc>=0.6,<2` (majors-only; fontTools
  deliberately left unbounded — stable API, no observed break).
- Test: `test_component_outlines_round_trip_with_transform` — component
  with a transform through set_layers → shadow restore → capture;
  faithful round trip rewrites nothing (n == 0) and the stored transform
  survives. Passes on BOTH versions.
- Verification [verified]: fresh venv (`pip install -e worktree[dev]`,
  glyphsLib 6.16.0 / fontTools 4.67.0 / fontc 1.0.0): **452 passed,
  8 skipped** — incl. the two previously-failing grade tests, the new
  component test, and the real-worker e2e built by fontc 1.0.0. Same 8
  pre-existing skips as the worktree run.
- T0.11 [verified]: `.venv/` added to `.gitignore`;
  `git check-ignore -v .venv/bin/python` → `.gitignore:29:.venv/`.

## T0.7 fix v2 (685c297) — watermark suppression; probe evidence

- The round-2 probe on a Crispy copy (5071) showed the first fix damped
  but did not kill the storm: exactly one follow-up per build, forever.
  Mechanism [verified from the log]: the suppression TIME window is
  checked at event-PROCESSING time, but the handler thread is busy in a
  multi-second resolve when the self-write's event arrives → window
  expired → self-event re-fires the mid-build branch → resolve →
  re-queue → repeat.
- Fix: `_suppress_watcher()` arms window + mtime watermark
  (`_SELF_WRITE_WATERMARK` / `_SELF_WRITE_ORIGINAL_WATERMARK`); an event
  whose file mtime is not newer than the last server write is dropped
  however late it is processed (a designer save always re-writes the
  file). All six arm sites converted; the Glyphs.app round-trip also
  re-arms in `finally`. The handler settles the save and processes only
  the first event per final mtime.
- Probe v3 [verified, `/tmp/av2-sprint/probe`]: POST /api/build,
  glyphsLib re-save of the original 8 s in → **exactly 1 follow-up
  build** (2 total from the POST), **2** `[transform] slant` lines
  (watcher resolve + `_run_one` resolve), **0** Unexpected EOF, **1**
  "Source changed mid-build", idle at end (incl. compress). v2 of the
  same probe showed the storm (7+ builds) before this fix.

## T0.9 fix (9e4b3fc) — capture key drift, found via the probe

- Probe v2 phase B: a shadow save produced a build but NO capture.
  Root cause [verified on the live copy]: braces created before the
  GRAD append carry 5-value coordinates in a 6-axis shadow;
  `_extract_brace_outlines` keyed by the raw tuple while capture/regen
  look up full-length vectors → capture silently wrote 0 (this is the
  audit's "28 layers, no captured outline" on Crispy) and regen
  preservation missed them too. Fix: extraction pads to the full axis
  vector with default-master values.
- Verified: capture of a hand-drawn 'a' brace on the live probe copy →
  `captured: 1`, sidecar carries the +250 outline + source_sig;
  regression test fails pre-fix (stash-checked). Note: my first probe
  drew into a *computed* (target) layer, which capture deliberately
  skips — probe corrected to match the sidecar entry by full location.
- Side observation for the verifier (pre-existing, not touched): the
  live Crispy `-control.json` holds DUPLICATE (glyph, location) layer
  entries (e.g. two `LCWD a` rows, one with outline) — the next
  normalise+save dedups first-wins and would drop the outlined copy.
  Also bogus entries naming glyph `3` (nice-name lookup resolves to
  `three`, so regen seeds land on the wrong glyph).
- Probe v3 phase B [verified]: shadow save → "Captured 1 drawn
  outline(s) into the sidecar.", exactly 1 build, idle at end, sidecar
  carries the +250 drawing.

## Hand-off — Tier 0 (round 2)

- Full suite after all round-2 changes [verified]: **453 passed,
  8 skipped in 36.55 s** (same 8 pre-existing skips).
- Latest-deps venv (glyphsLib 6.16.0 / fontTools 4.67.0 / fontc 1.0.0):
  **452 passed, 8 skipped** [verified] — grade tests and the new
  component-capture test pass on both glyphsLib versions.
- Live probe: see above; both phases meet the verifier's acceptance.

## Tier 1 (server group)

## T1.1 — global OBSERVER
- Bug [verified]: `main()` assigned `OBSERVER` without `global`; the
  `finally` then read a never-assigned local on blind-launch shutdown
  (UnboundLocalError), and the module global stayed None either way.
- Fix: added `OBSERVER` to main()'s global declaration.
- Test [verified]: tests/test_blind_launch.py boots the server with no
  source on 5199 and SIGINTs it; fails pre-fix (UnboundLocalError in
  stderr, stash-checked), passes post-fix.

## T1.2 — M2 double build: verified dead, no code change

- §10 item. Probe [verified]: fresh Crispy copy on 5071, boot → idle →
  POST /api/control-axes/LCWD/layers/delta (one brace) → studio log:
  exactly **1** build (build-3 after the boot build), **0** "Source
  changed mid-build" lines, idle after. Pre-watermark this edit class
  produced a second build (the transformed/ write landing outside the
  2.5 s window); the T0.7-v2 watermark suppression drops those
  self-events regardless of processing delay. The probe's own counter
  under-reported (its idle check can't see the pre-build resolve
  window); the counts above are read directly from the server log.

## T1.3 — secure_filename on uploads

- Mechanism [verified]: the multipart branch of `/api/load-source` saved
  `f.filename` verbatim under the per-font workspace — a
  `../../../tmp/x.glyphs` name escaped it (path traversal).
- Fix: `secure_filename()` (werkzeug) on the upload name; directories
  flatten into a single safe segment. The .zip branch already guards
  member paths ("Unsafe path in archive").
- Test [verified]: tests/test_upload_security.py — traversal name lands
  inside the workspace as one safe segment, normal upload unaffected.
  Pre-fix the destination escapes the workspace (stash-checked; the
  parent-location assertion fails).

## T1.4 — get_json(silent=True) + 400s

- The six sites (routes `/api/instance` POST, `/api/instance/<n>` PUT,
  `/api/instance/<n>/rename` PUT, `/api/avar2/axis` POST,
  `/api/avar2/axis/<n>` PUT, `/api/avar2/mapping/<i>/<a>` PUT) called
  `request.get_json()` bare: a malformed body raised werkzeug's HTML 415
  before the handlers' own `not data` 400s. All six now
  `get_json(silent=True)`; their existing 400s answer. Also guarded the
  two `.strip()` 500s (`name`/`new_name` must be strings → 400).
- Test [verified]: tests/test_json_body_400s.py — invalid JSON to all six
  → JSON 400 (or JSON 404 where a no-source guard runs first), never
  415/500; non-string names → JSON 400. 8 passed; pre-fix 5 failed
  (stash-checked: three 415s + two 500s).

## T1.5 — dead code deletion

- Verified unused first (grep across src/tests/frontend/scripts):
  `create_instance_in_glyphs` (the add-to-source route calls
  `_source_font.add_instance_to_source` directly — the dead function's
  docstring lied), `schedule_debounced_build` + `_CSV_BUILD_TIMER` +
  `_CSV_BUILD_TIMER_LOCK` + `_CSV_BUILD_DEBOUNCE_SECONDS`, routes
  `/api/avar2/mappings` and `/api/avar2/sync-csv` (the latter duplicated
  the CSV hook), and four `rebuild_in_background` thread wrappers whose
  entire body was a call to the now-non-blocking `trigger_build()`
  (inlined as direct calls). The audit's empty `routes/` dir exists only
  untracked in the main checkout — nothing tracked to delete; noted.
- Verification [verified]: zero references (grep), py_compile OK,
  `pytest -k "instance or rename or gate or build"` → 69 passed.

## T1.6 — get_font_family_name without parsing

- The function loaded the whole source via `_source_font.load_source`
  just to call `get_family_name`, which returns `path.stem`
  unconditionally (verified at source_font.py:137). Now returns the stem
  directly; `_FAMILY_NAME_CACHE` deleted (pointless without the parse).
  The one caller already falls back to `GLYPHS_PATH.stem`, and the old
  not-found path returned None → same fallback — behavior identical.
- Test [verified]: tests/test_family_name.py — stem returned with the
  loader rigged to raise; None for no path.

## T1.7 — load_source_cached on the read paths

- Swapped the audit-listed read sites to the mtime-keyed cache:
  `create_instance`, `_load_axis_metadata`, `_get_glyphs_axis_tags`,
  `_grade_instance_coords`, `_grade_param_ranges`, `get_avar2_axes`
  (its second parse rides `_load_axis_metadata`). Mutating callers
  (rename/delete/writeback) keep the uncached loader — the cached font
  object is shared.
- Found while swapping: `create_instance` reused `font` after a
  swallowed load failure (UnboundLocalError → 500). Hoisted the axis
  derivation into the load's try; a failed load now means empty
  source_axes, not a crash. Pre-existing bug, fixed in place because
  the swap made a test hit it.
- Test harnesses that stub `load_source` now also stub
  `load_source_cached` (test_instance_csv_schema.py).
- Verification [verified]: full suite 465 passed, 8 skipped.

## T1.8 — _declared_axis_ranges once per input version

- The three per-build call sites (`_builder_env` shim env,
  `_build_in_process`'s default-row detection + fvar fix) span two build
  paths and an export path, so a parameter thread-through would have
  touched three signatures. Instead: single-entry memo keyed on
  (metadata path, mtime, size; source path, mtime, size; sorted
  overrides) — one computation per build per state, no staleness
  (any edit re-keys), no signature changes. Callers get a fresh copy.
- Test [verified]: tests/test_declared_ranges_cache.py — one computation
  across repeat calls, recompute on metadata touch, overrides key
  separately, callers get copies.

## T1.9 — dead --csv / --build-dir fail loudly

- `_apply_source_path` resets `CSV_PATH = None` (server.py:7521) and the
  worker builds a staged snapshot with sibling sidecars — both flags set
  globals nothing reads (dead since the worker rework; the CLI `build
  --csv` set it pre-reset, equally dead). Both now exit(2) with a clear
  message; the unreachable `server.CSV_PATH = ...` in cli.py removed.
- Test [verified]: tests/test_dead_cli_flags.py — both flags exit 2 with
  the message (server + build CLI), `--help` unaffected. 4 passed.

## T1.10 — _running clears after the result is read and promoted

- `_run_one` cleared `_running` right after `proc.wait()`, before
  `_read_result`/`_finish` — `wait_for_idle` saw idle in the gap and
  returned the PREVIOUS build's outcome. Now cleared after `_finish`.
  Cancel safety: a cancel during the gap finds `proc` already exited
  (`_kill` no-ops on a dead process).
- Test [verified]:
  `test_wait_for_idle_returns_the_current_builds_outcome` — hold
  `_read_result` open, assert the waiter blocks, then reports the current
  build's failure (not the previous ok). Fails pre-fix (stash-checked).

## Tier 1 — control axes group

## T1.12 — case-insensitive lookup in update_axis
- Bug [verified]: `update_axis`'s range validation did
  `loc.get(tag_norm)` with the lowercased tag against authored-case
  location keys — for uppercase tags (SUBS) the lookup missed and the
  narrowing check was silently skipped. Now matches case-insensitively.
- Test [verified]: `test_update_axis_range_check_sees_authored_case`
  (the audit's SUBS probe) — narrowing [0,100]→50 with a layer at 100
  raises; fails pre-fix (stash-checked). 6 passed in the file.

## T1.13 — Fontra session/responses keep authored tag case

- The open-editor session and four responses forced `tag.lower()`,
  which breaks the shim's authored-case label matching for uppercase
  axes (the tag-case change's whole point). Now: the session and the
  open-editor response carry the matched sidecar axis's tag (fallback:
  the URL tag as sent); the layers/delete responses echo the URL tag
  as sent. Updated the test pin (`xouc` → `XOUC`).
- Tests [verified]: test_open_editor.py + tag-case + delta-carry +
  capture suites → 15 passed.

## T1.14 — shim labelRe case-insensitive; 'lcwd' hardcode dropped

- `_FONTRA_STUDIO_SHIM_JS`: `labelRe` now regex-escapes the tag and
  compiles with `i` (labels print authored case; a case drift on either
  side must not silently unmatch); the reference-metrics location line
  filters the SESSION axis (`cfg.tag`, case-insensitive) instead of the
  hard-coded 'lcwd'. Verified by reading the rendered string (not an
  f-string; `\\s`/`\\d` escapes intact) + py_compile; the shim has no JS
  harness — T3.4 (single shim implementation) is where it becomes
  testable.

## T1.15 — _normalise_layers docstring
- Docstring said "last write wins"; the code keeps the FIRST occurrence
  (`if key in seen: continue`). Fixed the docstring (first-wins is the
  intended authority order: the sidecar's stored order wins).

## T1.16 — BLOCKED (wrong repo)
- The contract bug is real [verified by reading]:
  `~/Documents/fontra-embed/src/metrics-hud.js:364` calls
  `getReferenceMetrics(g, loc)` but `embed-font.js:123-124` forwards
  `[refs]` only — the location never crosses the RPC boundary, so the
  static HUD always measures at the opening layer. The fix belongs to
  the fontra-embed repo, which this sprint's branch/verifier cannot
  cover. Needs the user's decision (fix there, or vendor the file).

## Tier 1 — frontend group

## T1.17 — SpaceTab removed (user decision)
- The Kinetikern WIP had both improved SpaceTab (axis picker) AND
  orphaned it (tab slot → Kinetikern). Asked the user: **remove it**.
  Deleted SpaceTab.jsx/.css (927 lines). `coverage.js`/`pinCorner`/
  `clampOutOfRange` stay — static-api.js (the Pages demo) uses them.
  Now-dead but NOT removed (noted, not in scope): the live-mode api.js
  methods pinCorner/clampOutOfRange/addMappingRow and their server
  routes.

## T1.18 — HeaderProps
- `transformsDirty`/`onApplyTransform` were destructured but not in the
  interface → tsc errors. Declared. `tsc --noEmit` exits 0.

## T1.19 — eslint error-free
- 4 no-irregular-whitespace: intentional BOM literals → `\uFEFF` escapes
  (static-api.js ×4, mappings-csv.js ×1; semantics identical).
- 427 no-undef: `js.configs.recommended` applied to `e2e/*.mjs` with no
  node/browser globals — added an e2e block (`globals.node` +
  `globals.browser`: Playwright specs evaluate page code).
- 1 no-unused-vars: a dead `removeGrade` helper in grade-live.spec.mjs —
  deleted (genuinely unused; no indirect callers).
- eslint now exits 0 (52 warnings = the config's accepted baseline).

## T1.20 — lint/typecheck/test:unit scripts + CI
- package.json scripts: `lint` (eslint .), `typecheck` (tsc --noEmit),
  `test:unit` (the three node-only specs: avar2-eval [self-skips without
  its fixture], grade-model, avar2-lint). Wired into pages.yml (before
  the vite build) and release.yml (before the frontend build).
- Found while enabling: grade-model.spec's two crispy-demo oracle values
  were stale (1.99/0.84) — the CURRENT Python oracle answers 0.0 for
  both (headroom clamp: no room to grade at the ceiling/floor).
  Regenerated the expectations with a comment. [verified: ran
  `grade.max_pct_for` against the current Python]
- release.yml still carries a stale CRA comment about CI=true — noted,
  not touched (cosmetic).

## T1.21 — orphans + 2 deps
- Deleted Avar2Preview.jsx/.css, UpdateButton.jsx/.css, ui/button.tsx
  (no importers — grep-verified); uninstalled @radix-ui/react-slot and
  class-variance-authority (used only by ui/button.tsx). ui/dialog.tsx
  and ui/dropdown-menu.tsx stay (live users).
- Gates after all five items [verified]: lint 0, typecheck 0,
  test:unit 0, vite build 0; python suite 472 passed, 8 skipped.

## T1.22 — UI copy no longer leaks file-format internals
- Reworded 12 spots: "Save to avar2-studio (CSV)" → "Save in
  avar2-studio"; "-control.json"/"the sidecar" titles → "saved with your
  studio data"; "avar2 mapping CSV" → "in the studio"; "Not valid JSON"
  → "Couldn't read that file"; "as one JSON file" → "as one file";
  "sidecars" → "studio data"; the Fontra "not a studio sidecar" → "not
  just in the studio". Verified: the only remaining mentions are code
  comments (grep). lint/typecheck/build all exit 0.

## T1.23 — Kinetikern fenced out of static mode
- The Kinetikern tab needs the live engine routes; on Pages (static
  provider) it shipped half-implemented. The tab button + content now
  render only when `!staticMode`. Verified: build/lint/typecheck exit 0.

## T1.24 — sliders get accessible names
- All 10 `type="range"` inputs gained `aria-label`: axis sliders
  (`{name} ({tag})`), font-size sliders ×3, the Kinetikern looseness
  slider, the Sidebar mapping slider (`axisLabel`), and the four
  AddBraceLocationModal pin/correction sliders. Build + lint exit 0.

## T1.25 — dismissable error banner
- The generic error banner had no close control (the only way out was a
  reload). It now has a × button (`aria-label="Dismiss error"`,
  `setError(null)`) with a small style rule. Build/lint/typecheck 0.

## Tier 1 — rust/tests/process group

## T1.26–T1.28 — cargo oracle paths + vacuous oracle
- T1.26: the 14 hard-coded `/Users/agyei/.../.venv/bin/python` defaults in
  12 test files now derive the repo venv from CARGO_MANIFEST_DIR, falling
  back to `python3` when the venv doesn't exist (fresh clones, CI).
- T1.27: oracle OUTPUTS moved from /tmp to `<crate>/target/tmp/` (created
  per test fn). The /tmp INPUT fixture defaults (spike/oracle fonts) stay
  — deterministic generation is T2.9.
- T1.28: correction_oracle is `#[ignore]`d with a reason (needs a
  fixture font that isn't in the repo; CORRECTION_FONT env still runs it
  explicitly); the dead Claude-scratchpad default path removed.

## T1.29 — ruff baseline + triage
- `pip install -e .[dev]` re-synced the venv (ruff 0.17.1). New
  `[tool.ruff]` baseline in pyproject: select E4/E7/E9/F/B/I001/RUF100/W;
  ignore E402 (guarded optional imports) + E501 (existing long-line
  style) + B905 (zip strict — 40 parallel-list sites, behavior already
  correct; comment in config); per-file-ignores F403/F405 for
  src/avar2_studio/glyphs/** (star imports are the Glyphs plugin idiom).
- 342 autofixes (RUF100 stale noqs, I001 import sorts, F401 dead imports,
  F541) + manual triage by a coder subagent, verified by me after:
  F841 dead assignments ×10, E702 semicolons ×20, E741 ambiguous `l`
  renames ×18, B007 ×7, B904 (from None / from e by whether the chain
  aids debugging) ×13, E722 bare except ×1, E731 lambda→def ×6; B023
  closure left with noqa + justification comment (immediate-call, not
  deferred). All 20 remaining W293/W291 were whitespace inside docstring/
  AppleScript literals — content-free, stripped.
- Deviation the subagent flagged honestly: the baseline I quoted (453)
  was stale; it proved no-change via a stash round-trip (472 both ways).
- Verified by me independently: `ruff check src tests` exit 0;
  `pytest tests/ -q` 472 passed, 8 skipped.

### T1.26–T1.28 verification (after the avar2_oracle move fix)
- `cargo test --no-run` compiles all 14 test binaries [verified].
- `AVAR2_PYTHON=<repo>/.venv/bin/python cargo test --no-fail-fast`
  [verified]: 13 of 15 targets ok (incl. fix/measure/pin/round_corners/
  slant oracles that failed on the python default before); the 4
  failures (avar2, braces, spac, stat) are all the pre-existing missing
  /tmp INPUT fixtures (panic: read spike font NotFound) — T2.9 generates
  them deterministically; correction_oracle shows `1 ignored` with the
  reason. Note for the verifier: in a worktree there's no `.venv`, so
  the oracle python falls back to `python3` — set AVAR2_PYTHON to a venv
  with fontTools+gftools to run them.

## T1.30 — doctor checks the native accelerators
- Two new optional checks mirror the production discovery exactly (via
  import, so they can't drift): `spac-inject` (`_native_injector`) and
  `kinetikern2` (`engine_path`), reporting `$AVAR2_SPAC_INJECT` /
  `$AVAR2_KK2_DYLIB` when set (and flagging a set-but-missing override
  by name). Optional = never fails the gate (ok=True with detail + fix),
  same contract as the fontra check. Tests + live run [verified]:
  2 passed; `doctor` lists both.

## T1.31 — log when the native SPAC injector is absent
- `_native_injector()` returned None silently; builds fell back to the
  fontTools SPAC path invisibly (the path whose gvar re-encode overflow
  the native injector exists to avoid). The transform now logs the
  absence and the consequence via `ctx.log`. Test [verified]:
  test_spac_fallback_log.py — the absence message fires before the
  fallback runs.

## T1.32 — wasm/fontc-web/pkg/
- Verified: untracked and gitignored; absent from the worktree — nothing
  to commit. The stale copy lives only in the main checkout (untouched
  per the sprint rules); noted for the user to delete.

## T1.33 — real_jobs marker registered
- The marker was documented and honored in conftest but unregistered.
  Registered under [tool.pytest.ini_options]; `pytest --markers` lists it.

## T1.34 — merged branches pruned
- `git branch -d` ×6 (collapse-helper-scripts, designspace-support,
  example-roboto-delta-demo, glyph-scoped-axes, grade-transform,
  vite-ts-shadcn) — all merged into github-pages. Kept:
  `grade-comparison` (unmerged, parked WIP — user's call), main,
  github-pages. Remotes untouched.

## T1.35 — tag v0.1.0: BLOCKED
- §10 verified first: https://pypi.org/pypi/avar2-studio/json → 404 —
  the 0.1.0 release never published to PyPI (release.yml may have run,
  but nothing landed). No tag without a real release; needs the user's
  decision (publish first, or drop the release).

## T1.36 — git gc
- [verified] before: 4,727 loose objects, 0 packs, 124.5 MB loose;
  after `git gc`: 92 loose, 4,562 in-pack, 31.2 MB pack. The 7.45 MB
  wasm blob history persists in the pack — that's T3.7 (history rewrite
  needs explicit user approval).

## Tier 1 — docs group

## T1.37 — README images/links
- All 13 image refs were `raw.githubusercontent.com/.../main/docs/images/`
  and 10 of those files never existed. Repointed 8 to the images that DO
  exist (verified by reading each image and matching its content to the
  caption: app-overview→instances-overview, sidebar-axes→instances-sidebar,
  correction-layers→secondary-axes-layers, wght-heavy-lcwd→preview-lcwd,
  fontra-reference, preview-sliders→preview-user-axes, export-modal,
  transforms-menu), all as RELATIVE paths; dropped the 4 refs whose
  screenshots were never taken (load-font-menu, preview-wght-sweep,
  grade-badge-popover, config-import-modal — already tracked in
  docs/readme-screenshots.md); removed the Space-tab section (the tab was
  removed in T1.17). All `blob/main` links → relative. Verified: every
  referenced file exists; every relative doc/image link resolves.

## T1.38 — env-var table
- README now documents every env var the code reads, in a table:
  AVAR2_STUDIO_DEBUG / _DEMO / _GFTOOLS_BUILDER /
  AVAR2_RELOCATE_BUILD_DEFAULT / AVAR2_SPAC_INJECT / AVAR2_KK2_DYLIB /
  AVAR2_STUDIO_AXIS_RANGES (set BY the build, never set by hand);
  frontend VITE_API_URL / VITE_FONTRA_EMBED_URL / window.AVAR2_EMBED_URL;
  cargo oracles AVAR2_PYTHON / AVAR2_SPIKE_FONT / AVAR2_ORACLE /
  AVAR2_CSV / CORRECTION_FONT. Verified against `grep -rn os.environ
  src/` + the ENV_KEYS passthrough in build_jobs (which the plain grep
  misses) + frontend VITE_*/window.* + the cargo tests.

## T1.39 — HANDOVER one-line contradictions
- `server.py ~832` → symbol `_resolve_active_source` (docs reference
  symbols, not line numbers); debounce "1.2s" → 0.4s (the code's value);
  "41 server tests" → 483 collected (measured now); deleted the orphaned
  paragraph fragment (its lead was long gone; the Coverage button it
  described was Space-tab UI). The structural contradictions (§3 vs §4.6
  caching, §4.8 designspace roadmap) are T2.16's docs restructure, not
  one-line edits.

## Hand-off — Tier 1 (round 3)

- All Tier-1 items in audit order: server (T1.1–T1.10; T1.11 aliased to
  T0.7), control axes (T1.12–T1.15; T1.16 BLOCKED — the contract bug is
  real but lives in the fontra-embed repo, outside this sprint's reach),
  frontend (T1.17 SpaceTab removed per the user's decision; T1.18–T1.25),
  rust/tests/process (T1.26–T1.29 cargo/ruff; T1.30 doctor; T1.31 SPAC
  absence log; T1.32 pkg/ verified absent from the worktree; T1.33 marker
  registered; T1.34 six merged branches pruned; T1.35 BLOCKED — 0.1.0 is
  not on PyPI (404), nothing to tag; T1.36 git gc), docs (T1.37–T1.39).

## Round 4 — executor: the verifier (the other agent is tied up; user instruction 2026-10-16)

From here the verifier executes and self-verifies with the same evidence
bar; findings rounds still get written so a second reviewer can re-check.

## T1.8 — fix: never memoize under an unversioned key

- Root cause [verified, round 3]: `_declared_ranges_cache_key` appended
  `None` for a missing/unset path, so every call with no source bound
  shared the key `((None, None), ())` and got the first computation.
- Fix: a missing on-disk version returns no key → compute without
  caching; versioned parts use `st_mtime_ns`. Memo semantics unchanged
  otherwise (one load per input version, callers get copies).
- Tests [verified]: new `test_no_caching_without_a_versioned_source`
  (two different metadata dicts under an unbound source → two different
  results, nothing cached). Ordering repro
  `pytest tests/test_declared_ranges_cache.py tests/test_avar2_default_relocation.py`
  (fontc-gated tests enabled via a `.venv` link in the worktree):
  **5 passed** in both orders; with the pre-fix `server.py` stashed back:
  **2 failed** (the new test + `test_two_default_rows_fail_loudly`).

## T1.22 — fix: the last copy leak

- `App.jsx` reseed confirm: "This rewrites the sidecar…" → "This updates
  the studio's saved layers…". Grep for user-facing "sidecar" in
  JSX/TSX: none. `npm run lint` 0 errors, `typecheck` clean.

## T0.11 — follow-up: one `.venv` ignore line

- Correction: `.gitignore` already ignored `.venv/` (line 13); the
  round-1 "untracked `.venv`" observation came from the verifier's own
  `.venv` SYMLINK in a scratch clone (a `/`-suffixed pattern matches
  directories only). The duplicate line added for T0.11 is removed and
  the one entry is now `.venv` (covers a symlink too — useful for
  worktrees that link a venv in so the `REPO/.venv/bin/fontc` test
  hard-codes resolve). `git check-ignore .venv` passes for both forms.

- Full suite after the three changes [verified]: **484 passed, 0 skipped
  in 54.9 s** (fontc-gated tests included); `ruff check src tests` clean.
