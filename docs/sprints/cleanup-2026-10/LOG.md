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

## T2.1 — one lock + one atomic writer for every sidecar

- New `src/avar2_studio/sidecar_io.py`: `LOCK` (process-wide RLock) and
  `atomic_writer` (mkstemp beside the target + fsync + `os.replace`,
  mode preserved, temp removed on failure; holds the lock while writing),
  plus `write_text`/`write_json`.
- Converted every writer [verified by the new code-shape guard]: the 11
  CSV sites in server.py, `config.yaml`, `axis-metadata.json` (×2),
  `csv_io.update_csv_from_glyphs`, `grade._save`, `transforms/config.save`,
  `kinetikern/sidecar._save`, `control_axes._save` (which keeps its
  backup call). `server.SIDECAR_LOCK` and `control_axes._INSTANCE_VALUES_LOCK`
  are now aliases of `sidecar_io.LOCK` — one lock.
- Locked the read-modify-write cycles: `@_sidecar_locked` on the CSV
  routes (`reorder_instances`, `create_instance`, `update_instance`,
  `rename_instance`, `delete_instance`, `add/update/delete_avar2_axis`,
  `update_avar2_mapping`, `update_transforms`) and helpers
  (`_add_missing_instance_to_csv`, `_save_axis_metadata`), on the
  supervisor's CSV hook `sync_csv_with_glyphs`, and on every load→mutate→
  save function in control_axes (8), grade (8) and kinetikern/sidecar (4)
  via a module-local `@_locked`. Lock order stays SIDECAR → RESOLVE
  (`_resolve_active_source` never takes the sidecar lock).
- Tests [verified]: `tests/test_sidecar_io.py` — failed write leaves the
  target intact and no temp file; JSON round trip keeps mode + trailing
  newline; 8 threads × 40 locked RMW increments lose nothing (320);
  code-shape guard finds no raw `.open("w")` on a sidecar path under
  src/ (Glyphs plugins excluded). Full suite **484 passed** + 4 new;
  `ruff check` clean (9 import-order fixes applied).

## T2.14 — `.avar2-studio/build/` reaper

- `build_jobs.reap_build_dir(current, keep=KEEP_BUILDS=2)` runs after every
  build promotion: fonts beyond the newest two (plus the promoted one) are
  deleted with their `.woff2`/`.woff2.json` twins. The newest-by-mtime
  picker in `_get_avar2_built_font_filename` is unchanged.
- Test [verified]: `test_promotion_reaps_stale_fonts_but_keeps_the_newest`
  (five fonts, two with twins → the newest two survive, 7 files removed).
  `tests/test_build_jobs.py` green; ruff clean.

## T2.13 — Kinetikern tuples carry an explicit marker

- `inject()` records every tuple it adds — (glyph, axes support) — in a
  private sfnt table `KKRN`; `strip()` removes exactly those and deletes
  the table; a font without the record has nothing of ours to remove.
  The old rule ("phantom-only on the parametric axes") also matched
  genuine compile output (22 tuples in Crispy's `three`). Survival of the
  table through later stages: fontTools saves keep unknown tables; the
  native SPAC injector repacks every table raw (`FontBuilder::add_raw`
  in `wasm/fontc-web/src/lib.rs`).
- Tests [verified]: `test_strip_removes_only_the_recorded_tuples` plants a
  phantom-only tuple at a master-corner peak before injecting — strip
  removes only the injected ones, the planted one survives, a second
  strip removes nothing; `test_marker_survives_a_save_and_reload`.
  `tests/test_kinetikern_inject.py` + `test_kinetikern.py`: 7 passed
  (fontc + engine); ruff clean.
- Known residual: a font injected by the PREVIOUS code (no marker) keeps
  its old tuples until the next fresh build; preview fonts are rebuilt
  from scratch every build, so this only affects one export in between.

## T2.10 — control sidecar: passthrough, version gate, corrupt-file surfacing

- Unknown keys at the top level, per axis and per layer now survive
  `load → _normalise → _save` (`_KNOWN_*_KEYS` sets; known keys win).
- Version gate: `_save` raises `SidecarVersionError` (a `ValueError`, so
  the existing route handlers answer 400) when the file's `version` is
  newer than `_SCHEMA_VERSION`; reads still work.
- Corrupt file: `load` records the parse error in `_LOAD_ERRORS`
  (`load_errors()`), returns the empty shape so readers keep working,
  and `_save` raises `SidecarCorruptError` instead of overwriting; the
  error clears on the next successful load. `/api/health` carries
  `sidecar_errors` ({path: message}) for the UI.
- Tests [verified]: `tests/test_control_sidecar_robustness.py` — unknown
  keys round-trip through `update_axis`; version 99 is readable but
  `add_axis` refuses and the file is byte-identical; a truncated file is
  reported, `add_axis` refuses, the bytes are preserved, and repairing
  the file clears the error; `/api/health` lists the error and drops it
  once the file is gone. Control-axis cluster: 85 passed; ruff clean.

## T2.11 + T2.12 — the watcher sees sidecars, moved/created events, and ignores job snapshots

- Hoisted `sync_csv_with_glyphs` and `GlyphsFileHandler` out of `main()`
  to module level (no behaviour change; the class now degrades to
  `object` when watchdog is missing instead of failing at import), so the
  handler is testable with watchdog's event classes.
- T2.11: `_classify_watched_path` returns `"sidecar"` for
  `<stem>{-avar.csv,-control.json,-transforms.json,-grade.json,-kinetikern.json}`
  beside the original (the same list the job snapshot stages). The
  handler reacts to `on_created` and `on_moved` (destination path) as
  well as `on_modified` — atomic saves and git checkouts surface that
  way. The studio's own sidecar writes are filtered by a per-path stamp
  that `sidecar_io.atomic_writer` records (`LAST_WRITES`): only a later
  mtime is an external edit, and it schedules the debounced regen +
  rebuild.
- T2.12: any path under `.avar2-studio/jobs/` is ignored. §10-M3
  reproduced first on `examples/roboto-delta-mini` (designspace →
  recursive watch): with NO edits the pre-fix server ran **46 builds in
  150 s** and logged 51,215 self-triggered lines ("UFO glyph edited" /
  "Source changed mid-build"). Post-fix probe: see below.
- Tests [verified]: `tests/test_watcher_events.py` — external sidecar edit
  → regen; the studio's own write → ignored, a later rewrite → regen;
  created + moved events count; original → resolve/csv/build and the
  shadow → csv/build keep their paths; snapshot paths (`.glyphs`,
  `.glif`) under `jobs/` classify as None and trigger nothing; the
  handler imports without watchdog. Full suite green; ruff clean.

## T2.9 — merged from branch `t2-ci` (executed by a subagent; verified by the executor)

- `wasm/fontc-web/tests/common/mod.rs` generates the spike font in-test
  from `examples/crispy-mini` (SOURCE_DATE_EPOCH pinned; deterministic
  sha256 across runs) and the avar2 oracle through `spike/build_oracle.py`
  under `target/tmp`; the four oracles that needed hand-made `/tmp` fonts
  use it; `AVAR2_SPIKE_FONT`/`AVAR2_ORACLE` still override. Found on the
  way: T1.27 had left `compare_braces.py`/`compare_spac.py` reading
  `/tmp` — fixed (paths passed as argv). Still writing to `/tmp` and
  carrying their own python derivation: measure, pin, pin_synth,
  round_corners, slant oracles → follow-up item T2.17.
- `.github/workflows/test.yml`: python (ubuntu+macos; venv at `.venv` so
  the fontc-gated tests run; ruff + pytest), frontend (lint/typecheck/
  test:unit/build), rust (cargo test with AVAR2_PYTHON, wasm32 compile
  check on Linux, kinetikern2 dylib on macOS). Every step run locally by
  the subagent in workflow order; GitHub Actions itself not run from here.
- Executor verification [verified]: after the merge, with no
  `/tmp/fontc-wasm-spike.ttf` / `/tmp/av2-oracle.ttf` present,
  `cargo test --no-fail-fast` on the warm target → all 14 binaries ok,
  `correction_oracle` ignored; `test.yml` loads (jobs python/frontend/rust).

## T2.15 — designspace authoring: 501, by decision

- Decision: secondary-axis AUTHORING stays .glyphs-only. Parity for the
  designspace shadow (corrections via `target`, captured drawings, seed
  stamps, atomic writes, aliases) is a large port with no current user;
  the audit's alternative — refuse clearly — is implemented:
  `_designspace_authoring_refusal()` answers **501** with a plain message
  on `POST /api/control-axes`, `PUT …/layers`, `POST …/layers/delta`,
  `POST …/reseed`, `PATCH …/<tag>`, `DELETE …/<tag>` when the source is a
  .designspace. `GET /api/control-axes`, `open-editor` and
  `reference-metrics` keep working, and existing sidecars still build.
  Decision record: `docs/decisions/0013-designspace-authoring.md` (added
  once the docs restructure branch lands, to avoid a docs/ conflict).
- Tests [verified]: `tests/test_designspace_authoring_501.py` — all six
  mutations → 501 mentioning "designspace", reads 200, no shadow and no
  sidecar written; a .glyphs source is unaffected. Control-axis cluster
  71 passed; ruff clean.
- T2.12 post-fix probe [verified]: same `examples/roboto-delta-mini` copy,
  worktree code, port 5074, no edits for 150 s after the boot build →
  **1 build, 0 self-trigger lines, 0 warnings** (pre-fix: 46 builds,
  51,215 lines).
- T2.15 lint follow-up: the `studio_client` fixture moved from
  `test_open_editor.py` to `conftest.py` so other route tests can use it
  without an F811 import; ruff clean.

## T2.4 — assets out of Python strings

- `src/avar2_studio/fontra/shim.js` (the 420-line Fontra shim, written
  from the evaluated string so escapes are preserved), `fontra/focus.css`
  (the focus stylesheet as a `string.Template` with `$panel_rules` /
  `$chrome_rules`), `glyphsapp/document_modified.applescript` and
  `glyphsapp/reload_document.applescript` (`$abs_path`). Loaded through
  `_read_asset()` (`importlib.resources`, cached); `_FONTRA_STUDIO_SHIM_JS`
  keeps its name. server.py: 8,180 → 7,843 lines.
- Guard [verified]: goldens captured from the PRE-move code
  (`tests/fixtures/fontra_rewrite_{input,studio,plain}.html`) and
  `tests/test_fontra_assets.py` asserts `_rewrite_html_paths` output is
  byte-identical for a studio session and for none; assets exist in the
  package; the AppleScript templates substitute cleanly. Wheel built from
  the worktree contains all four files. Full suite **505 passed**; ruff
  clean; cargo oracles after the T2.9 merge: 17 binaries, 21 passed,
  0 failed, 1 ignored.

## T2.17 — the five remaining oracles: `target/tmp` outputs, shared python derivation

- `measure`, `pin`, `pin_synth`, `round_corners`, `slant` oracles now use
  `common::python()` and write every intermediate under
  `common::tmp_dir()` (CARGO_TARGET_TMPDIR) instead of fixed `/tmp`
  names; the comparators already took their paths as arguments.
- [verified] `cargo test` for the five binaries on the warm target: all
  ok; no `"/tmp/` literal left in `wasm/fontc-web/tests/*.rs`.

## T2.2 (part 1) — logging for the library modules

- New `src/avar2_studio/studio_log.py`: `get_logger(name)` returns a child
  of the `avar2_studio` logger, which has one handler that writes the
  plain message to whatever `sys.stderr` is at emit time (test capture and
  redirected launches both see it); werkzeug's request log is untouched.
- Converted with an AST-based rewriter (`print(x, file=sys.stderr)` →
  `log.info/warning/error(x)` by message prefix, leading "\n" spacing
  dropped): `control_axes.py` 4, `csv_io.py` 15, `build/config_generator.py`
  38, `transforms/registry.py` 4 — 61 calls; no stderr prints remain in
  those modules. `server.py`, `build_jobs.py` and `cli.py`/`doctor.py`
  follow in part 2 (after the frontend branch merges; the CLI's own
  user-facing output stays `print`).
- [verified] compiles; ruff clean; full suite **505 passed** (including
  the capsys assertion in `test_watcher_save_flow`).

## T2.2 (part 2) — logging for server.py and build_jobs.py

- Same AST rewriter: `server.py` 152 and `build_jobs.py` 2 stderr prints →
  `log.info/warning/error`. No `print(..., file=sys.stderr)` remains
  under `src/avar2_studio/` outside `cli.py`/`doctor.py` (user-facing CLI
  output, kept as print) and the Glyphs plugins (their own runtime).
- [verified] compiles; ruff clean; full suite **505 passed**; the
  `[jobs] …` lines and every warning still reach stderr through the live
  handler (the capsys assertion in `test_watcher_save_flow` passes).

## T2.16 — merged from branch `t2-docs` (executed by a subagent; pointers applied by the executor)

- Docs restructure per audit §7: `docs/ARCHITECTURE.md`, `DEVELOPMENT.md`,
  `SIDECARS.md`, `docs/guides/` (authoring, secondary axes rewritten,
  grade expanded, kinetikern, transforms), `docs/decisions/0001–0012` +
  index, `docs/archive/` (HANDOVER as HANDOVER-2026-10, WORKER_BUILD_PLAN,
  debugging log, Pages migration, design-notes, screenshots brief),
  README user-facing only, AGENTS.md 80 lines, `docs/README.md` map,
  three unreferenced images pruned. Subagent checks: 139 links/images
  resolve (2 pre-existing anchors in `examples/` READMEs remain), every
  `os.environ` name documented, all 70 route decorators in ARCHITECTURE.
- Code comments → pointers [verified: compiles, full suite 505 passed,
  ruff clean]: the step-0.5 / METHOD NOTE / step-3.5 / no-reloc blocks
  in `_build_in_process`, the export authored-row parentheticals, the
  baked-correction narratives, `_gc_paused`, the strip and step-3 asides,
  `update_control_axis`, `update_instance`, `_non_mapping_axis_tags`,
  the stale "slice" comments and banners, control_axes' module/
  instance_values/shadow-banner/stamp/seed comments, the shim's
  isStudioItem story, inject.py's docstring, corner_rounding's
  provenance, is_slant_exempt, `_native_injector`, grade.max_pct_for,
  csv_io.backup_sidecar; stale doc paths in build_jobs/build_worker/cli/
  lib.rs/pages.yml re-pointed. `observed:` markers in server.py: 0.
  The two HTML goldens were regenerated (the shim text changed on
  purpose). Left for the frontend merge: `api.js`, `static-api.js`,
  `fontc-compile.js`, `ControlAxes.jsx` still cite the old doc paths.
- Follow-ups: `fontra/shim.js` still carries its `<script>` wrapper
  (so it is not standalone-lintable — move the tags into the injector);
  `docs/decisions/0013-designspace-authoring.md` for T2.15; the two
  `examples/` README anchors; the three chat-only decisions listed as
  "Not yet recorded" in `docs/decisions/README.md`.

## Resumed Tier 2 (post-round-3)

- Assessment on resume: the previous session had already landed T1.8/T1.22
  fixes + T2.1/T2.2/T2.4/T2.9–T2.16 (+T2.17) and parked T2.5/T2.6/T2.7 on
  the unmerged side branch `t2-frontend`. Reviewed that branch (per-job
  result registry is bounded, finished-before-running ordering correct,
  cancel-from-queue records a terminal state) and merged it (3f73bda);
  one test-file conflict (both sides appended) resolved keeping both.
  Gates on the landed tree before merging: 502 passed, ruff/lint/tsc/
  build clean (run mid-merge — see the conflict-marker errors it caught;
  the marker fix was verified by `pytest tests/test_build_jobs.py` → 17
  passed and re-run below).

## T2.8 — Radix Dialog for the hand-rolled modals (subagent agent-5)
- Six modals converted to the vendored ui/dialog.tsx: AddAxisModal,
  AddBraceLocationModal, AddControlAxisModal, DeleteInstanceModal,
  DuplicateModal, EditAxisModal. Inner markup/classes byte-identical
  (e2e selectors preserved); outside-click close suppressed where the
  original had none; DeleteInstanceModal's Escape/X routes through
  handleCancel so the checkbox reset invariant holds.
- Verified by the subagent: lint/typecheck/build/test:unit all exit 0;
  a real-browser DOM smoke (playwright-core + Chrome, throwaway harness
  in /tmp) — 44/44 checks: role="dialog" present, focus lands inside,
  overlay click does not close, Escape closes. I re-ran the four gates
  myself (all 0). Not run: the full e2e browser specs (need the demo
  harness) — flagged for the verifier.

## T2.3 — state.py extraction (subagent agent-6)
- New `src/avar2_studio/state.py` holds the 37 mutable runtime-state
  globals (pure values, no package imports); server.py reads/writes
  `state.X` everywhere (tokenize-based rewrite, comments/strings
  untouched), all `global` declarations for moved names deleted.
  build_jobs reads values off `state` (methods still off the server
  module); build_worker binds/reports via `state`; tests' ~60 setattr
  sites migrated to `monkeypatch.setattr(state, …)`; StubServer became
  method-only + a `stub_state` fixture. `_SYNC_CSV_HOOK` moved to state
  (runtime-registered callable). Locks/constants/JOBS stayed in
  server.py. New tests/test_state_module.py probes the seam.
- Verified by me independently: 511 passed / 3 skipped (509 baseline +
  2 new probes), ruff clean, and the no-shadow-attribute probe passes.

## Tier-2 follow-ups (from the previous session's notes)

- Frontend doc-path citations: api.js's worker comment now points at
  `docs/archive/WORKER_BUILD_PLAN.md` (the file moved). The other cited
  files (static-api.js, fontc-compile.js, ControlAxes.jsx) had no stale
  doc refs (grep-verified).
- `fontra/shim.js` is now pure JavaScript: the `<script>` wrapper moved
  into the injector (`_FONTRA_STUDIO_SHIM_JS` wraps at read time). The
  T2.4 golden test passes AND the wrapped content is byte-identical in
  the golden (verified by direct string check), so the injected HTML is
  unchanged.
- `docs/decisions/0013-designspace-authoring-501.md` written for T2.15
  and indexed (the previous session's deferral).
- examples/ README anchors: both example READMEs + examples/README.md
  now link `docs/guides/authoring-instances.md` directly (the redirect
  stub stays for any other inbound links).
- The three chat-only decisions (ROND white-room cap, collision badge,
  Def-as-real-master shelf) stay "Not yet recorded" in
  docs/decisions/README.md — only the user knows their terms; BLOCKED on
  them (write down or consciously drop).

## Checkpoint — Tier 2 complete (round 4)

- On resume: the previous session had landed T2.1/T2.2/T2.4/T2.9–T2.16
  (+T2.17) and the T1.8/T1.22 fixes; I assessed, merged the parked
  `t2-frontend` branch (T2.5/T2.6/T2.7), committed T2.8 (six modals →
  Radix Dialog) and T2.3 (state.py — the audit's 37 globals, exactly),
  and closed the noted follow-ups (shim.js pure JS + golden-verified
  byte-identity, ADR 0013, doc-path fixes, examples anchors). The three
  chat-only decisions remain BLOCKED on the user (their terms exist only
  in conversation).
- Gates on the final tree [verified]: pytest 511 passed / 3 skipped;
  ruff clean; frontend lint/tsc/build/test:unit all exit 0.

## Tier 3

## T3.1 — pipeline.py + Flask-free worker (subagent agent-7)
- New `src/avar2_studio/pipeline.py` (1,955 lines): the audit's (c) list
  (`_resolve_active_source`, `_run_build`, `_perform_avar2_build`,
  `_build_in_process`, `_apply_transform_chain`, `_apply_source_path`)
  plus pipeline-only helpers (builder env, declared ranges + memo,
  metadata I/O, export pipeline, transform chain helpers). Shared
  helpers both sides need moved there with server.py back-importing
  (bare-name resolution keeps the ~8 test monkeypatch sites working
  unedited; `server.X is pipeline.X` verified). `_apply_source_path`
  uses `state.JOBS` (new in state.py); conftest patches both.
- `build_worker.py` imports pipeline + state only — no Flask: verified
  by me: `import avar2_studio.build_worker` with
  `'flask' not in sys.modules` assertion → OK.
- server.py 7,663 → 5,773 lines. Verified by me: 511 passed / 3 skipped,
  ruff clean, the real-worker e2e (test_worker_shadow.py) passes —
  the worker path builds a real font end to end.

## T3.2 — routes/ blueprints (subagent agent-7, timed out; finished by me)
- The subagent's tree was ~95% done at timeout: 13 blueprint modules
  (routes/{ui,source,health,build,text_width,coverage,config,
  transforms_grade,kinetikern,avar2,instances,control_axes,load,fontra})
  with handlers reading `state.X`, calling pipeline.py, and reaching
  server helpers via late-bound `server.X` (they load mid-import and
  tests patch server.X). It had not registered `fontra` in
  `routes/__init__.py` — the url_map snapshot caught exactly that (12
  missing paths: the Fontra proxy set, the UI catch-all, shim config,
  the websocket). Registered it; snapshot green.
- Test call sites the subagent missed, migrated by me:
  test_fontra_assets.py → `routes.fontra` (rewrite + shim);
  test_watcher_save_flow.py → `pipeline._grade/_transforms/
  _resolve_active_source`. Both files' import order re-sorted (ruff).
- server.py is 1,321 lines (from the audit's 8,180). Verified by me:
  full suite **512 passed / 3 skipped**, ruff clean, snapshot test
  green, no-flask import probe still OK.

## T3.3 — control_axes/ package (subagent agent-8)
- control_axes.py (1,983 lines) → `control_axes/` package: schema.py /
  naming.py / outlines.py / shadow_glyphs.py / shadow_designspace.py,
  `__init__.py` re-exporting all 65 module-level names — dir-count
  parity 74=74 verified name-by-name. Bodies byte-identical except 23
  sanctioned import/cross-module qualifications (machine-compared via
  replay-diff). Internal calls are module-qualified; the designspace
  dispatch stays lazy (no cycle). Docs rows updated (ARCHITECTURE,
  SIDECARS).
- Verified by me independently: 512 passed / 3 skipped, ruff clean,
  no-flask import probe OK, name parity probe OK.

## T3.10 — watcher lifecycle object (watch.py)
- New `src/avar2_studio/watch.py`: `GlyphsFileHandler` (verbatim) +
  `_classify_watched_path`/`_wait_for_save_to_settle`/
  `_resolve_source_after_save`/`_capture_shadow_drawings` moved from
  server.py, and a `SourceWatcher` singleton (`WATCHER`) with
  `start`/`stop`/`reschedule`. Call-ins go through late-bound `server.X`
  (the T3.2 idiom); reads are `state.X`; resolve is pipeline.py.
- server.py: main() shrinks to `_watch.WATCHER.start()` + the periodic
  fallback + `finally: WATCHER.stop()`; the dead watchdog import block
  removed. routes/load.py: `WATCHER.reschedule()` after each successful
  `_apply_source_path` — the audit's long-standing TODO (a source swap
  or blind launch now re-points the watcher).
- Tests: the three watcher test files' patches moved to their new homes
  (watch./pipeline./state.JOBS); new tests/test_watch_lifecycle.py —
  reschedule re-points (fake Observer records schedule paths), blind
  launch is a no-op, and POST /api/load-source triggers reschedule.
- Verified by me: full suite **514 passed / 3 skipped**, ruff clean,
  no-flask probe OK. server.py now **1,024 lines** (audit: 8,180).

## T3.8 — parameterised default relocation for export
- Per decisions/0007 (the decided direction, verbatim): `_build_in_process`
  gains `relocate_default` (None → the env, default on); the export build
  passes **False** — it stages its own origin master via
  `_build_export_source`, and the step-3.5 instancer rebase on top was
  the ~7× densification (audit §2 H3). The `round()`-vs-decimal mismatch
  is inert under it (the rebase no longer runs there); noted as a
  fidelity follow-up if exact resting-location matters at 0.2-unit level.
- Tests [verified]: tests/test_export_relocation.py — relocate True/False
  move/keep the fvar default (XTRA 800.0 vs compiled origin) and the
  natural build's gvar tuple count ≤ the relocated one's; env default +
  `0` both honored when no parameter is passed. 2 passed. fontc via the
  interpreter-sibling idiom (worktree-safe).

## T3.4 — SKIPPED by user decision (2026-10-17)
- The single-shim-shared-with-fontra-embed unification is deferred; the
  desktop shim stays as the lintable package file (T2.4) until a
  fontra-embed-coordinated pass is worth doing.

## T3.6 — App.jsx shrinks to a shell (subagent agent-9)
- App.jsx 2,398 → **207 lines**: providers + AppShell only. New modules:
  hooks/useStudioData.jsx (data + loaders + health poll; the full-screen
  "Loading…" gate now fires only on FIRST load — the audit's
  unmount-on-every-refresh bug fixed), useTransforms.jsx + context
  (Header 19→7 props), useControlAxes.jsx + context (Sidebar 43→31),
  useInstanceEditing.jsx + InstancesTab container, StatusBanners.jsx +
  BuildVeil.jsx. Invariant comments moved with their code; the audit's
  adaptations (provider order, source-swap clearing signal) recorded in
  its report. eslint warnings 41 → 34 (all moves accounted).
- Verified by the subagent: gates exit 0; DOM smoke 13/13 (tab bar with
  the Kinetikern fence, slider edit round-trips the context chain, no
  re-gate on poll). Verified by me: lint/tsc/build/test:unit exit 0;
  python suite 516 passed / 3 skipped.

## T3.5 — one CSV/axis model (subagent agent-10)
- csv_io now owns mappings-CSV reading; config_generator's five sites and
  kinetikern/inject's site delegate (identical semantics, incl. the
  bad-cell prefix rule; ragged rows now tolerated everywhere instead of
  crashing some readers). New `read_csv_spac_overrides()` is the one
  SPAC-column parse.
- Rust↔Python: per-instance SPAC values ride the transforms JSON as
  `params.instance_spac`; `spac.rs`'s hand CSV parser is DELETED (the M7
  divergences — quoted-comma names lost, column voided on one bad cell —
  gone by construction). The CLI's 4th arg is retained-but-ignored;
  the wasm export signature kept (arg ignored) so the checked-in JS glue
  stays valid; static-api.js fills `params.instance_spac` browser-side.
- Parity tests on both sides (tests/test_spac_csv_parity.py ×6 +
  wasm spac_csv_parity.rs, with a non-vacuity guard); the native binary
  exercised via the cargo oracle (log line verified).
- Verified by me: 523 passed / 3 skipped, ruff clean (incl. fixing the
  I001 in MY T3.8 test file that the subagent caught), frontend gates 0,
  cargo spac_csv_parity + spac_oracle pass. Pathological-input behavior
  deltas (padded headers, ragged rows, empty-CSV error type) are
  documented in its report — all toward-consistency.

## T3.8 — RE-DONE per ADR 0007 (verifier FAILED round 1 of it)
- Round-1 of T3.8 was wrong per the verifier's measurement: it kept the
  inserted-master staging (`_build_export_source`) and only disabled the
  rebase — the rejected method, with measured interior drift up to
  543–761 u on Crispy (wght 900/100 stops).
- The re-do per the verifier's direction: `_build_export_source` is GONE
  (186 lines of the rejected method removed). The set-default export now
  builds the staged ORIGINAL source exactly like the preview, with
  step-3.5 relocation ON and its target = P: `_build_in_process` gains
  `relocate_target` (parametric tags → exact location; wins over the
  default row's out:) alongside `relocate_default` (env stays the preview
  default). The authored-row lookup returns the row's out: verbatim —
  the round() is gone.
- Found while building it: the export's blank-cell fill read defaults
  from the metadata file while the declared defaults were the requested
  override — blank rows aliased onto the requested row ("Locations must
  be unique"). The metadata copy that travels with the export now carries
  the REQUESTED defaults.
- Fidelity [verified by me]: preview vs plain-compile ground truth at the
  authored stop "Narrow Heavy 12" = **0.0 u**; the export vs truth ≤
  **0.49 u** (sub-unit F2Dot14 rounding; the bar is 3 u). The export's
  fvar defaults are exactly the authored out: (222.8/279.6/250.2); the
  mapping probe evaluates exactly. tests/test_export_relocation.py has
  the three tests; the earlier false "701 u" reading was my test pinning
  user AND parametric axes at once (double-applied the avar2 mapping) —
  the correct methodology (avar-stripped copies pinned on the parametric
  axes) is what's in the test. Pre-fix drift is the verifier's
  measurement (543–761 u on Crispy).
- Gates [verified]: 524 passed / 3 skipped; ruff clean; frontend 0.

## Verifier NEXT items — docs module map + stale doc paths + anchors
- ARCHITECTURE.md's module map now carries `state.py`, `watch.py`,
  `routes/` rows, an accurate post-T3.2 `server.py` row, and pipeline.py
  no longer lists the removed `_build_export_source`.
- Stale code citations to `docs/WORKER_BUILD_PLAN.md` repointed to
  `docs/archive/WORKER_BUILD_PLAN.md` (server.py, state.py).
- examples/ README anchors: all relative links verified resolving (the
  direct `docs/guides/authoring-instances.md` links went in earlier).

## T3.4 — one Fontra shim shared with fontra-embed (subagent agent-12)
- fontra-embed branch `shared-studio-shim` @ 3de37a6 (NOT pushed):
  `build-studio-shim.mjs` concatenates focused-ui.js + metrics-hud.js +
  a new host bootstrap into one classic script (provenance header;
  import/export strip asserted at build). Session flags: the desktop sets
  `allow_computed_edits: true`, the embed sets `auto_enable_edits: true`
  — the subagent found the brief's premise wrong (the desktop has
  auto-enabled NOTHING since a23d78c; the embed's auto-enable is pinned
  by our own e2e) and parameterised rather than forced one behavior.
  Reconciled: labelRe escaped+case-insensitive both hosts; the embed now
  sorts by source name (desktop's behavior); the HUD's hardcoded 'lcwd'
  filter → the session tag (the T1.16-class bug's sibling).
- avar2-studio: `_studio_shim_script()` prefers the built
  `fontra/embed-shim.js`, falls back to `fontra/shim.js` byte-untouched;
  `/api/fontra-shim-config` adds `allow_computed_edits` for studio
  sessions. Goldens regenerated once (delta = header + bundle swap,
  CSS prefix byte-identical).
- Verified by me: the three targeted test files → 10 passed; ruff clean;
  fontra-embed harness 8/8 PASS; the subagent's desktop smoke on a
  Crispy copy :5071 (12/12: sorted rows, no auto-enabled edits, HUD
  present, no double CSS) is recorded with evidence in its report.
- Follow-up for the user: fontra-embed's branch is unpushed (per the
  rule); deploying it updates the Pages demo — the e2e expectations hold
  because the flags preserve behavior.

### Note — a mis-staging I caught and fixed
- My first T3.4 commit (7a39d1c, unpushed) accidentally swept the T3.9
  subagent's in-progress files via `git add -A`. Recovered with
  `git reset --soft HEAD~1` (unpushed; tree untouched) and recommitted as
  c573d91 with only T3.4's files. The T3.9 work continues uncommitted as
  its agent expects. One known-red test mid-flight:
  test_doctor_native.py's absence case sees agent-11's just-built
  `src/avar2_studio/bin/` binaries — T3.9's own acceptance (green suite)
  covers fixing it.

## T3.9 — platform wheels carry both accelerators (per decision 0015)
- Discovery order everywhere: env override → package-carried
  `avar2_studio/bin/<name>` → checkout-relative. `DYLIB_NAME` is now
  platform-aware (`.dylib` darwin / `.so` linux).
- New `hatch_build.py` build hook: when `src/avar2_studio/bin/` holds the
  cargo-built binaries, the wheel is tagged `py3-none-<plat>` non-pure so
  pip installs them into platlib; a tree without binaries builds the pure
  wheel as before. `release.yml` gained a per-platform matrix (macOS
  arm64/x86_64 + ubuntu) that cargo-builds both binaries before the wheel
  builds. `src/avar2_studio/bin/` is gitignored (build output).
- Acceptance, run by me: built
  `dist/avar2_studio-0.1.0-py3-none-macosx_26_0_arm64.whl` (unzip -l
  shows both binaries under avar2_studio/bin/); fresh scratch venv →
  `avar2-studio doctor` → **spac-inject AND kinetikern2 found** at the
  installed site-packages path, no env overrides; `pytest
  tests/test_kinetikern*.py` in that venv → **8 passed** (runs against
  the wheel's dylib, not skipped).
- The subagent (agent-11) timed out twice on the slow tail (cargo
  rebuilds); the wheel build + acceptance above are verified by me
  directly. The hermetic doctor test (absence simulated via monkeypatch
  on both discovery functions) is fixed — it no longer depends on
  whether binaries exist on disk.
- Gates: 532 passed / **0 skipped** (the previously-skipped fontc-gated
  tests now run — the venv-fontc discovery resolved); ruff clean.

## T3.8 — third pass (executor: the verifier, 2026-10-19): the export builds the ACTIVE source

- Round 6 found the set-default export compiled `state.ORIGINAL_PATH`,
  dropping every studio axis and transform. `_export_pipeline` now hands
  `update_config` `state.GLYPHS_PATH` — the resolved active source the
  job queue stages as `active-<name>` and binds in the worker — exactly
  what the preview build compiles. Relocation to P (round 6's correct
  half) is unchanged.
- Test [verified]: `tests/test_export_active_source.py` — real worker on
  crispy-mini with a `ymod` brace AND `round_corners` enabled; preview
  initialised the way the server does (`_initialize_preview_*`,
  `_resolve_active_source`); export at the authored row "Narrow Thin
  144": the export's fvar axis set equals the preview's (incl. `ymod`,
  `ROND`), defaults land on 94/6/6, every glyph's point count matches,
  drift ≤ 3 u at the authored stop. Fails on the pre-fix source (axis
  set differs). Full suite green; ruff clean; frontend build OK.
- Note for future fixtures: a real-worker test needs the preview CSV +
  config initialised and a server-side resolve before exporting, or the
  avar2 step fails and the plain-VF fallback (no user axes) is served.

## T3.7 — going-forward half (user decision 2026-10-19: history is kept)

- `frontend/src/wasm/fontc-web/fontc_web_bg.wasm` untracked (`git rm
  --cached`) and ignored; the small wasm-bindgen glue stays tracked so the
  frontend builds and tests without a Rust toolchain — Vite only warns
  when the binary is absent [verified: build exit 0 with the file hidden].
- `pages.yml` and both frontend-bundle jobs in `release.yml` build it
  before the frontend build (rust toolchain + wasm32 target + rust-cache +
  `wasm-pack build --release --target web`). The command was run locally
  on the warm target [verified]; the glue it regenerates differs from the
  tracked copy (newer wasm-bindgen) — CI regenerates both, the tracked
  glue is updated when it is next rebuilt locally. YAML of all three
  workflows loads. No history rewrite; the 25 historical blobs stay.
- `docs/decisions/0014-wasm-blob-built-in-ci-history-kept.md` + index
  row; `docs/DEVELOPMENT.md` note.

## T3.8 — gvar header-fit pruning for relocated exports (executor: the verifier, 2026-10-19)

- Measured on the Crispy copy: the set-default export at the ORIGIN style
  is now the preview font re-origined — same axes, same point counts,
  same 124,432 tuples, mappings exact, **0.0 u** drift at every probed
  location. An export resting at a FAR interior style (wght 900) failed
  with fontTools' bare `OverflowError: int too big to convert`: relocating
  the default there splits every region again and the heaviest glyphs
  (`B`: 2,707 tuples) exceed gvar's u16 per-glyph header block.
- `pipeline._fit_gvar_headers` runs after the relocation step (preview and
  export alike): per glyph it budgets the header bytes (4 + peak +
  intermediate region per tuple, every peak counted as private — the
  shared set is recomputed by fontTools after the prune, so this is the
  bound that cannot be beaten) and drops only sub-visible tuples
  (max |delta| ≤ 2 u, smallest first) and only where the block overflows;
  one no-sharing compile verifies the result. When that is not enough it
  raises a readable error naming the glyph and the two ways out (export at
  the resting style; disable slant/rounding twins for the export) — which
  the export modal shows as the job's error.
- Tests [verified]: `tests/test_gvar_header_fit.py` on the in-repo
  `WasmTest-VF.ttf` — 20,000 planted ±1 u tuples overflow the fixture and
  are pruned back under the limit with every visible tuple intact; a glyph
  of only visible tuples raises the readable error. Full suite green;
  ruff clean. Crispy wght-900 export: the readable error, no traceback.
- Residual (proposed follow-up, not in this sprint's backlog): an export
  option that compiles without the twin masters (slant/ROND) so far
  interior styles fit the header limit — the message already points at it.

## Part B flyout fixes F1 + F2 (findings.md, Designer Part B, 2026-10-21)

Executor: agent. Two pre-existing behaviours exposed by the designer's B2
sequence, fixed one commit each with a test.

**F1 — promote flushes the pending auto-save draft** (`f01f856`).
`handleAddInstanceToSource` called `POST /add-to-source` without flushing
the 800 ms debounced auto-save; the promotion flips the row to
`origin:"source"`, so the auto-save effect then returns early and the draft
was dropped. The ordering now lives in `frontend/src/promote-flush.js` —
strip preview-only axes → flush with the same csv-only PUT the timer would
have sent (queued behind `autoSaveChain`, since each commit rewrites the
whole CSV) → promote → return the refreshed rows; a failed flush throws
before the promote. The hook cancels the pending timer, refreshes both row
caches (instances + the avar2 mappings the sync dot judges) and reseeds the
editing baseline from the refreshed row.

Evidence:
- `node e2e/promote-flush.spec.mjs` — 18 checks green: call order
  `updateInstance → addInstanceToSource → getInstances`, the `csvOnly` flag,
  preview-axis stripping (SPAC kept), flush queued behind a 25 ms pending
  chain, empty draft → no PUT, failed flush → promote never called.
  Added to `npm run test:unit` (chain exits 0).
- `npm run build` green; `eslint` clean on the touched files.

**F2 — a source save carries the mapping-only values to the studio**
(`PUT /api/instance/<name>` in `routes/instances.py`). SPAC has no axis in
the source, so `update_instance_in_glyphs` cannot carry it and the
source→CSV sync only refreshes axis columns; the full source save left it
behind and the row stayed red. The endpoint now derives
`mapping_only = csv_coordinates − glyphs_coordinates` and writes those cells
in the CSV row even when the row is a source instance, while the axis
columns stay the sync's job (the row is NOT added to `skip_instances`).
The flyout says so in copy: a muted note under "Save to source file" —
"SPAC is saved in the studio's mappings file, not the source" — and the
button tooltip carries the same sentence.

Evidence:
- `tests/test_source_save_spac.py` — **fails on the pre-fix route**
  (`SPAC == "63.5"` assertion, value stayed `51.2`) and passes after it;
  the writeback payload pinned to `{"XTRA": 700.0}` (SPAC never sent to the
  .glyphs), the row not skipped by the sync, and an untouched SPAC
  surviving a later source save.
- `pytest tests/test_source_save_spac.py tests/test_instance_spac_persistence.py
  tests/test_source_writeback_targets.py -q` → 8 passed.
- Full gates: `ruff check src tests` clean; `pytest tests/ -q` → **564
  passed**; `npm run test:unit` green; `npm run build` green.

## Round 15 — F3 (SPAC key case), the preview-coords lint error, flyout copy

**F3 — the sync dot compared SPAC by exact key case.** `getInstanceSyncStatus`
now delegates dirtiness to `instance-sync.js`, which owns one rule:
`persistedValue(persisted, key)` looks a draft axis up in the persisted union
case-insensitively (exact key first), and `editsAreDirty(edits, persisted,
previewOnlyTags)` is the per-key dirty check. The CSV lowercases columns it
does not recognise (`csv_io.normalize_in_axis_name` → `spac`) while the row,
the sliders and /api/instances use the fvar tag (`SPAC`); `!(k in persisted)`
therefore called a persisted SPAC "a value with no persisted home" and the dot
stayed red for good whenever the SPAC transform was on.

Evidence:
- `e2e/instance-sync.spec.mjs` (in `test:unit`): `{SPAC: 70.6}` against
  `in {spac: 70.6}` → clean; the reverse case → clean; a real SPAC difference
  → dirty; a key with no persisted home → dirty; preview-only axes → clean;
  float32 noise tolerated. 16 checks green.
- Live, port 5075, fresh copy of `examples/crispy-mini` (which ships
  `spac_widthaware` enabled, the state the designer had): selected
  `Ultra Wide Thin 144`, dragged SPAC 25 → dot `sync-status-red`; "Save to
  source file (promote)" → row gains the SRC badge and the dot reads
  `sync-status-green`. Server truth after the save: `/api/avat2/instances` →
  `in {"opsz":144,"spac":25,"wdth":200}` (lowercase `spac`), `/api/instances`
  → `coordinates {"SPAC":25.0,…}` with `source_coordinates` carrying no SPAC.
  That is exactly the key-case pair that used to force red.

**Lint error since `5527ecc`** — `frontend/src/preview-coords.spec.mjs:59`
`'console' is not defined` (no-undef; under `src/` the eslint config treats
the file as browser code). Moved to `frontend/e2e/preview-coords.spec.mjs`
with the other node specs, import path updated, `test:unit` chain repointed.
Evidence: `node_modules/.bin/eslint .` → **0 errors, 34 warnings** (totals
line; warnings all pre-existing). The verifier's note that earlier rounds
read the "0 errors … fixable" line instead of the totals is correct — this
round quotes the totals line.

**Flyout copy (F2 note)** — the interface rule forbids file-format internals:
the note under "Save to source file" and the button tooltip now both read
"SPAC is saved in the studio, not the source file." Evidence: live flyout on
the same staging — note text matched exactly; tooltip matched.

Gates at this state: `ruff check src tests` clean; `pytest tests/ -q` → 564
passed; `eslint .` 0 errors; `tsc --noEmit` clean; `npm run test:unit` green
(10 chains); `npm run build` green.

## Phase C — C9: `--fontra-port` and `AVAR2_FONTRA_PORT`

`state.FONTRA_PORT` was the hard-coded 8001, so the second studio to open the
editor lost it to the first (the fresh spawn died on bind while the port probe
saw the squatter and reported success). It now comes from
`state._fontra_port_from_env()` (`AVAR2_FONTRA_PORT`, invalid values fall back
to 8001) and the server's `--fontra-port` overrides it at startup (out of range
→ exit 2). `doctor`'s stale-process check reports/compares the configured port
instead of the literal 8001.

Commands + results:
- `pytest tests/test_fontra_port.py -q` → 5 passed.
- `ruff check src tests` → All checks passed; `pytest tests/ -q` → 569 passed.
- Live: two studios (`--port 5072 --fontra-port 8002`, `--port 5074
  --fontra-port 8004`) on copies of `examples/crispy-mini` under
  `~/avar2-agent/c9/`; `open-editor` returned `port 8002` / `port 8004` with
  matching direct URLs; `http://127.0.0.1:8002/editor.html` and `:8004` both
  200 simultaneously; `/fontra/editor.html` proxies on both studios 200;
  logs: `Started Fontra on http://127.0.0.1:8002 (root=…/src1/CrispyMini.glyphs)`
  and `…:8004 (root=…/src2/CrispyMini.glyphs)`. Reservations respected: no
  access to 5070/5071/8001 or `~/Documents/Crispy`.

## Phase C — C1: the declared axis defaults ship with the source

The traditional axes' declared defaults lived in `.avar2-studio/axis-metadata.json`,
which SIDECARS.md told designers not to commit — so a clean checkout built with each
column's minimum as the default and named styles landed off their mapped values. The
canonical home is now the committed sibling `<stem>-axis-metadata.json` beside the
source (`pipeline._sibling_axis_metadata_path`); the old location is a read fallback
that is migrated to the sibling (then removed) on first access, keeping the legacy file
if the migration fails. `config_generator` takes an explicit `--axis-metadata` /
`metadata_path` so the collision guard and the blank-cell fill read the same file; both
callers (preview build, export) pass it. The job stager carries `-axis-metadata.json`
among `SIDECAR_SUFFIXES`, so the worker sees the declared defaults. `doctor`'s metadata
advice unchanged. SIDECARS.md: the row is now `<stem>-axis-metadata.json` / commit? yes;
the `.avar2-studio/` listings no longer claim the file. The bundled example moves to
`examples/crispy-mini/sources/CrispyMini-axis-metadata.json` (tracked), the legacy
`.avar2-studio/` copy is removed and its `.gitignore` exception dropped. The CLI now
exits 2 when neither home exists.

Commands + results:
- `pytest tests/test_axis_metadata_sibling.py -q` → 8 passed.
- `ruff check src tests` → All checks passed; `pytest tests/ -q` → 577 passed.
- Live, `~/avar2-agent/c1` (routine staging, metadata moved to the sibling, `git init`
  + commit): `git status --porcelain` → 0 lines before the build;
  `python -m avar2_studio build src/Crispy.glyphs --out out` → **exit 0**, 83 s,
  stderr has no metadata warning; built font
  `out/Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`
  with fvar defaults wght 400 / wdth 100 / opsz 144 and `avar.majorVersion == 2`;
  `pipeline._evaluate_with_font(font, {wght 900, wdth 100, opsz 144})` →
  XTRA 725.89 (row 726.0), XOPQ 316.27 (316.3), YOPQ 235.20 (235.2) — all within 1 u.
- Same source with neither metadata file → **exit 2**,
  `error: axis metadata not found: neither Crispy-axis-metadata.json nor
  .avar2-studio/axis-metadata.json exists beside Crispy.glyphs …`.
- Studio on the same checkout (5072, fontra 8002): `origin_warning` null,
  `last_build_status` ok, `/api/avar2/axes` → metadata OPSZ default **144**;
  `git status --porcelain -- src` → 0 lines.
- Post-build the checkout's own `.avar2-studio/` workdir is written beside the source
  (C2's `--workdir` removes that); the committed inputs are unchanged.

## Phase C — C2: `--workdir DIR`

The build wrote `.avar2-studio/` beside the source. `avar2-studio build --workdir DIR`
now puts the whole tool workdir under DIR (default: beside the source, unchanged):
`state.WORKDIR_OVERRIDE` is consulted by `pipeline._get_preview_dir()`, and the three
places that resolved the workdir directly were routed through it —
`control_axes.shadow_glyphs.shadow_dir_for`, `instance_aliases._path`, and the
`config.yaml` promotion in `build_jobs` (the shadow and the renamed-instance map were
the two files still landing in the checkout). `_apply_source_path` derives BUILD_DIR
from `_get_preview_dir()` instead of the source's parent.

Commands + results:
- `pytest tests/test_build_workdir.py -q` → 4 passed (the test stages the committed
  source + CSV, so a bootstrap write cannot mask a leak).
- `ruff check src tests` → clean; `pytest tests/ -q` → **581 passed**. (First full run
  failed `test_designspace_authoring_501` because the new test's
  `_apply_source_path` left `SOURCE_FORMAT`/`BUILD_DIR` set for later tests — the
  fixture now restores every global the call mutates.)
- Live, `~/avar2-agent/c2/clean` (fresh `git clone` of the C1 checkout):
  - without the flag: exit 0, `git status --porcelain --ignored` → `!! src/.avar2-studio/`.
  - with `--workdir $R/work`: exit 0, `git status --porcelain --ignored` → **0 lines**;
    the workdir is `$R/work/.avar2-studio/{config.yaml,build,jobs}`.
  - font equality: A vs B differ in `head` only (`head.modified` 3876025681 vs
    3876025604); all other tables sha256-identical. Two builds WITHOUT the flag also
    differ (`c401d36b…` vs `bd36a347…`), so raw byte-identity is a property the build
    does not have run-to-run; the flag changes nothing but the workdir.

## Phase C — C3: every failure reason reaches the exit message

A degraded build (origin relocation failed, S1(b)) exited 1 as "build failed: unknown":
`LAST_BUILD_ERROR` is empty for it and the relocation message lives in
`LAST_ORIGIN_RELOCATION_ERROR`. `cli._failure_reasons(state)` now collects every
recorded reason — build status/error, avar2, origin relocation, grade, source
transform — one per line, printed by `_build` as `error: …`. A degraded status with
nothing recorded still reports ("build degraded: no reason recorded") rather than
exiting silently. Exit codes: 0 ok, 1 degraded/failed, 2 usage/load error.

Commands + results:
- `pytest tests/test_cli_failures.py -q` → 7 passed; `ruff check src tests` clean;
  `pytest tests/ -q` → **588 passed**.
- Live, `~/avar2-agent/c3` (routine staging + `s1_recipe.py --url
  http://localhost:5072 --sources $R/sources`): after the recipe the studio reported
  `last_build_status degraded` and `origin_relocation_error` = "glyph 'g' has too many
  variation tuples for OpenType's per-glyph gvar limit after relocating the default
  (1767 tuples; …)". Studio stopped, then
  `python -m avar2_studio build $R/sources/Crispy.glyphs --out $R/out` → **exit 1**,
  stderr `error: origin relocation failed (degraded build — the avar2 mapping is not
  the authored one): glyph 'g' has too many variation tuples…`.
- Exit 2: C1's missing-metadata case (live) and the unloadable-source case (unit).
  Exit 0: C1's clean build (live).

## Phase C — C4: the export options on the CLI

`avar2-studio build` gains `--hide-axes TAGS`, `--default tag=value[,tag=value]` and
repeatable `--format ttf|woff2`. With no flags it behaves exactly as before (the served
font is the artifact); with any flag it calls `pipeline._export_pipeline` on the font it
just built — the same function the studio's export dialog submits. A malformed
`--default` is exit 2 (it used to be able to reach the exporter empty and rebuild at
the wrong origin); an export that cannot fit the requested resting style exits 1 with
the pipeline's readable message.

Commands + results:
- `pytest tests/test_cli_export_options.py -q` → 12 passed; `ruff check src tests`
  clean; `pytest tests/ -q` → **600 passed**.
- Live, `~/avar2-agent/c4/mini` (copy of `examples/crispy-mini/sources`):
  CLI `build CrispyMini.glyphs --out cli-out --hide-axes XOPQ --default
  wght=400,opsz=14 --format ttf --format woff2` → exit 0;
  `CrispyMini[SPAC,XOPQ,XTRA,YOPQ,opsz,wdth,wght].ttf` 277,532 B and `.woff2`
  79,560 B.
  Studio on the same copy (5072), `POST /api/export-font {"hidden_axes":["XOPQ"],
  "default_location":{"wght":400,"opsz":14},"format":"ttf"}` → download 277,532 B.
  Table hashes: identical for every table except `head` (raw sha256 7c40ccdd… vs
  ee243e07…; `head.modified` only). fvar hidden flags `{"XOPQ": true, others false}`;
  fvar defaults `wght 400`, `opsz 14` (re-origined), parametric axes rebased. WOFF2
  table set == TTF table set (18 tables).
- Note: the CLI's transform chain logged "native injector not found" and fell back to
  fontTools for SPAC; the studio's export did the same (outputs identical), so the
  comparison is like-for-like. Tracked for C7 where the transform runs from the config.

## Phase C — C5: CI proof and the recipe for other repos

(a) `.github/workflows/test.yml` gains `cli-build`: non-editable `pip install .` into
`$RUNNER_TEMP/cli-venv`, an assertion that `avar2_studio.__file__` contains
`site-packages` (not the checkout), then `avar2-studio build
examples/crispy-mini/sources/CrispyMini.glyphs --out … --workdir …` run from
`$RUNNER_TEMP`, then a `git status --porcelain --ignored` cleanliness check. The
check excludes `__pycache__/` because `pip install .` leaves
`__pycache__/hatch_build.cpython-311.pyc` at the repo root (observed in the local
simulation); every other path there would be the build writing into the checkout.

(b) README "Building in CI": `pip install avar2-studio` (fontc + gftools are regular
dependencies with majors-only upper bounds), `avar2-studio doctor --strict`,
`avar2-studio build … --config sources/MyFont-build.json --workdir … --out fonts/`,
the exit codes, a paste-ready Actions snippet, and the
`AVAR2_STUDIO_GFTOOLS_BUILDER=1` note.

Commands + results:
- YAML parsed with `yaml.safe_load` → jobs `['python', 'cli-build', 'frontend', 'rust']`.
- Local simulation of the job on a fresh clone of `0a080d2` (`~/avar2-agent/c5`):
  - `pip install .` → `avar2_studio` at `~/avar2-agent/c5/venv/lib/python3.11/site-packages/avar2_studio/__init__.py`;
  - `avar2-studio build … --out out --workdir work` → exit 0; jobs
    `build-…-1 finished: success=True in 2.83s`, `compress-…-2 … 0.64s`; artifact
    `out/CrispyMini[SPAC,XOPQ,XTRA,YOPQ,opsz,wdth,wght].ttf` 83,716 B;
  - cleanliness check → 0 lines.
- The real workflow run is on the checkpoint push (it runs on `cleanup-2026-10`).

## Phase C — C6: `doctor --strict`

`doctor` still reports and exits 1 on any failed check. `--strict` narrows that to the
headless-build prerequisites: `CheckResult` gains `build_prereq` (fontc,
gftools-builder, glyphsLib set it) and `configured_missing` (the SPAC injector and the
Kinetikern engine set it when `$AVAR2_SPAC_INJECT` / `$AVAR2_KK2_DYLIB` names a path
that is not there — a silent fallback to the slow fontTools path is exactly what CI
should not ship). UI-only checks (frontend bundle, Fontra) are printed but do not fail
strict mode. `doctor.main(argv)` takes its arguments; `cli.main` passes `argv[1:]`.

Commands + results:
- `pytest tests/test_doctor_strict.py -q` → 7 passed; `ruff check src tests` clean;
  `pytest tests/ -q` → **607 passed**.
- `python -m avar2_studio doctor --strict` → **exit 0**,
  "All build prerequisites present. `avar2-studio build` can run on this machine."
- `env PATH=/tmp/fakebin:/usr/bin:/bin python -m avar2_studio doctor --strict` →
  **exit 1**: `✗ fontc not on PATH`, `✗ gftools-builder not on PATH`,
  "2 build prerequisite(s) missing." with `brew install fontc` /
  `pipx install gftools` fixes.

## Phase C — C7: one committed build config drives the whole export

`src/avar2_studio/build_config.py` reads/validates `<stem>-build.json`
(`format`/`format_version`, `axes.hide`, `default`, `transforms`, `instances.fix|stat`,
`formats`). `avar2-studio build` uses it automatically when it sits beside the source
(so CI needs no flags), `--config` points elsewhere, `--write-config` emits it from the
current sidecars and exits. C4 flags override the file. `instances.fix/stat` fold into
the existing `fix_instances` / `gen_stat` transforms, so there is one mechanism.
Transforms come from the committed `<stem>-transforms.json` unless the config carries
`transforms`, which replaces them for that run via `transforms.config.set_override` —
the sidecar is never rewritten. Any config key naming design-authored state
(`control`, `grade`, `kinetikern`, `coordinates`, …) is exit 2 with a message pointing
at the studio.

Commands + results:
- `pytest tests/test_build_config.py -q` → 16 passed; `tests/test_cli_export_options.py`
  → 17 passed (5 of them the config wiring); `ruff check src tests` clean;
  `pytest tests/ -q` → **628 passed**.
- Live, `~/avar2-agent/c7` (git-clean clone of the committed Crispy copy with
  `Crispy-build.json` committed; `git status --porcelain --ignored` = 0 lines before
  and after): `python -m avar2_studio build src/Crispy.glyphs --out out --workdir
  work` → exit 0. stderr shows the configured transforms ran
  (`width-aware SPAC injected (-20…40, bias 1, scale 1.25)`, `fvar named instances
  regenerated`, `STAT table rebuilt`). Artifacts:
  `out/Crispy[GRAD,LCWD,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,wdth,wght].ttf` + `.woff2`.
  Font inspection: `XOPQ` hidden; defaults `wght 400`, `opsz 14`, `wdth 100`;
  9 named instances; `STAT` present; WOFF2 tables == TTF tables.
- Studio export on the same copy with the same choices
  (`POST /api/export-font {"hidden_axes":["XOPQ"],"default_location":{"wght":400,
  "opsz":14},"format":"ttf"}`; the copy's transforms sidecar was set to the config's
  run: spac_widthaware -20…40 + fix_instances + gen_stat): table-for-table identical
  except `head` (raw sha256 3e562b66… vs 5d379cc5…; hidden flags and defaults equal,
  9 instances both).
- `Crispy-build.json` = `{"control": {"axes": []}}` → **exit 2**,
  `error: Crispy-build.json: 'control' is studio-authored state — open the source in
  avar2-studio and edit it there. …`.

## Phase C — checkpoint

C1–C9 are all in. The agent's loop (`~/avar2-agent/loop.sh`, run after each push)
staged a fresh Crispy copy per the routine, ran `avar2-studio build` from the
INSTALLED package (`~/avar2-agent/venv`) on a git-clean checkout, started the studio on
5072 (Fontra 8002) and ran the full `ui_probe.py` at idle, then appended its results and
pushed. Final run at `2fcda7e`:

- staged copy `~/avar2-agent/runs/20261028-181713/checkout/src`, git-clean **0 lines
  before and after** the CLI build;
- `avar2-studio build … --out … --workdir …` → **exit 0**, artifact
  `out/Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`;
- `ui_probe.py all` → **exit 0, PASS 16 / FAIL 0** (log and screenshots in the run dir);
- reserved paths/ports untouched (`~/Documents/Crispy`, 5070, 5071, 8001,
  `~/avar2-studio-test`).

Gates at this checkpoint: `ruff check src tests` clean; `pytest tests/ -q` → 628 passed;
frontend eslint 0 errors, typecheck clean, `test:unit` green, build green.

## Phase C review — P3: the build config's transform override reaches the worker

`transforms.config.set_override` is a process global; the build runs in a worker
subprocess, so a config at the resting style (`default: null`) changed only the CLI's own
shadow step — the worker read the committed `<stem>-transforms.json` and the font kept
`slnt` and one instance (the verifier's finding). `build_jobs._stage` now carries
`transform_override` in the job spec and `build_worker._bind_server_globals` installs it
before the build. The committed sidecar is still never rewritten.

Commands + results:
- `pytest tests/test_build_config_worker.py -q` → 3 passed (spec carries the override;
  the worker bootstrap applies it with the on-disk file unchanged; a real worker build
  with `spac_widthaware` disabled by the config yields a font without SPAC).
- `ruff check src tests` clean; `pytest tests/ -q` → **631 passed**.
- Live, `~/avar2-agent/p3` (git-clean clone of the committed Crispy copy + committed
  `Crispy-build.json`: `default: null`, transforms = `fix_instances` + `gen_stat`,
  `instances.fix/stat` true; the sidecar enables round_corners, slant and
  spac_widthaware): `python -m avar2_studio build clean/src/Crispy.glyphs --out out
  --workdir work` → exit 0;
  `Crispy[GRAD,LCWD,XOPQ,XTRA,YMOD,YOPQ].ttf` — axes `XTRA XOPQ YOPQ LCWD YMOD GRAD
  opsz wdth wght` (no ROND / slnt / SPAC), 9 named instances, STAT present; checkout
  `git status --porcelain --ignored` → 0 lines.

## Phase C review — P1 + P2

**P1** — `tests/test_fontra_port.py` asserted `importlib.util.find_spec("fontra") is
not None` on top of a stubbed launcher, which only coupled the test to this venv: it
failed on every plain `pip install -e .[dev]` clone and in both CI python jobs since
`65d2039`. Assert (and its comment + unused import) removed; the test still pins the
port in the response. `pytest tests/test_fontra_port.py -q` → 5 passed.

**P2** — an existing but unparseable source used to reach the build and exit 1 with
`error: build failed: The source file cannot be read: 'str' object has no attribute
'keys'`. The CLI now probes the source with the cached loader right after applying it and
exits **2** with `error: <name> could not be read: <parse message>`, cancelling the
auto-build `_apply_source_path` queued.

Commands + results:
- `pytest tests/test_cli_failures.py tests/test_fontra_port.py -q` → 13 passed;
  `ruff check src tests` clean; `pytest tests/ -q` → **632 passed**.
- Live `~/avar2-agent/p2` (garbage `.glyphs` + valid metadata sibling): exit **2**,
  `error: Crispy.glyphs could not be read: Unexpected character after key at line 1:
  'a'`; no `out/`; no build worker left running.

## Designer B7 — K2, K3, K4 (Kinetikern tab copy + size)

K2: "Apply to all…" overwrites every style's looseness, so the control now says so — the button
reads `Apply -0.60 to all…` (the slider's value) and the confirm names the tuned styles and the
value they become; the guide says it replaces each style's looseness. K3: the dropdown's bare "●"
became `· spaced -0.60`, Revert names its style, and the slider sync effect also keys on
`entry?.looseness` (the payload arrives async). K4: the Font Size control moved into `.kk-main` and
picking a style sets the size from its `opsz` (clamped to 9–144; no opsz → unchanged).

All copy lives in `frontend/src/kinetikern-copy.js` so it is testable in node.

Commands + results:
- `node e2e/kinetikern-copy.spec.mjs` → all passed (labels for -0.60/0.00/+0.25; confirm text
  singular/plural/plain; option and Revert text; `fontSizeForStyle` 72/144/clamped/null).
- `pytest tests/ -q` → **635 passed**; `ruff check src tests` clean; frontend `eslint .` 0 errors;
  `tsc --noEmit` clean; `npm run build` green.
- `ui_probe.py`'s kinetikern scenario now asserts the K3 option/Revert text after an apply and that
  `.kk-main .font-size-control` exists.

## K1 (UI) — the probe's first run failed, and why

Run `20261028-223121`: `probe exit=1 (PASS 15 / FAIL 1)`,
`FAIL kinetikern: no K badge on the spaced row (Default 1 MaxOpsz)`. The
apply and the K3/K4 assertions passed; the badge was simply not there yet —
it renders from `useStudioData.kinetikern`, which only refreshed on the 2 s
health poll, and the assert ran right after the build landed.

Fixed the cause, not the symptom: `KinetikernTab.reloadStored` already
fetches the payload after a build, so it now pushes that same payload into
the shared state — an apply in the tab badges the row at once. The probe
also waits for the badge to attach/detach (60 s) instead of snapshotting in
the same tick.

Commands + results:
- `pytest tests/ -q` → **637 passed** (with CI0's new
  `tests/test_fontc_lookup.py`); `ruff check src tests` clean.
- `node e2e/kinetikern-copy.spec.mjs` → all passed; `npm run build` green.

## CI0 — the python and rust jobs on a plain runner

- CI0a: `tests/test_glyphs_launcher.py` waited exactly `WAIT = 20.0` for a
  callback *and* pinned the launcher's `READY_TIMEOUT` to the same 20 s, so a
  loaded runner reported "no callback within 20.0s". They are separate now
  (`READY_TIMEOUT = 20.0`, `WAIT = 3 * READY_TIMEOUT`).
- CI0b: the worker-shadow failure was a real bug, not a fixture problem.
  `build_variable_font` found fontc with `shutil.which` only; a build started
  without the venv on PATH (CI's `.venv/bin/pytest`, a plugin launcher) fell
  through to fontmake, which is not a dependency, and raised
  `FileNotFoundError: 'fontmake'`. It now also looks beside
  `sys.executable` — the same place the tests look. New
  `tests/test_fontc_lookup.py`.
- CI0c: the clamp oracle shells out to fontmake for its reference build; the
  rust job installed only the package. `compare_clamp.py` now exits 3 with
  "SKIP: fontmake is not installed" instead of a traceback, `clamp_oracle.rs`
  accepts that code, and the rust job installs fontmake so the bound runs.

Commands + results:
- `pytest tests/test_fontc_lookup.py -q` → 2 passed;
  `pytest tests/test_glyphs_launcher.py -q` → 5 passed;
  `pytest tests/ -q` → 637 passed; `ruff check src tests` clean.
- Not run locally: `cargo test` (the fontations stack is a 10+ minute cold
  build) — CI is the acceptance for CI0c and for the whole item.

## The fourth CI run: two failures, both now understood

`python (ubuntu)`, `rust (ubuntu)`, `rust (macos)` (started later) and
`cli-build` were green; two jobs were red.

- `frontend`: `FAIL setKinetikernEnabled — live-only and not in
  DESKTOP_ONLY`. K1's `api.js` method needed an entry in
  `e2e/api-parity.spec.mjs` (the spec enforces that every live-only method is
  declared). Registered against the `kinetikern` capability.
- `python (macos)`: the launcher test again, but now with the launcher's own
  message — and the durations line named the cause:
  `test_probe_tells_free_other_and_studio_apart` **35.05 s** for three local
  servers. `HTTPServer.server_bind` calls `socket.getfqdn`, and that reverse
  lookup has no resolver on the runner, so the stub reached `serve_forever`
  after the launcher's deadline. The fixture and the stub now bind through a
  `_QuietServer` that skips the lookup.

Commands + results:
- `pytest tests/test_glyphs_launcher.py -q` → 5 passed in 4.0 s (was 8.6 s).
- `node e2e/api-parity.spec.mjs` → all passed.
- Next: a fresh workflow run is the acceptance for CI0.

## Checkpoint ROUND 18

All of the approved queue is in at `4d96107` (one commit each, K2–K4 sharing
one because they are one file's copy and layout): P3 `14a0d73`, P1 `6689b6`,
P2 `393e093`, K1 route `619da0f`, K2–K4 `056a551`, K1 UI `0ee5c90` (+
`944fd13` for the badge timing the probe found), CI0 `0169274` (+
`2237df9` for the parity entry and the reverse DNS).

Verification at this state:
- Test workflow run `40721221145` (commit `2237df9`): **all six jobs green**
  — python (ubuntu, macos), rust (ubuntu, macos), frontend, cli-build. That is
  CI0's acceptance.
- Loop run `20261028-225007`: staged copy git-clean (0 lines before and after
  the build), `avar2-studio build` from the installed package exit 0, studio
  on 5072 / Fontra 8002 idle at probe time, `ui_probe.py all` **PASS 16 /
  FAIL 0** (the kinetikern scenario now covers K1's badge, the Transforms
  toggle and the off/on round trip).
- `pytest tests/ -q` → 637 passed; `ruff check src tests` clean; frontend
  `eslint .` 0 errors, `tsc --noEmit` clean, `npm run test:unit` all pass,
  `npm run build` green.

Not verified locally, stated plainly: `cargo test` (the fontations stack is a
10+ minute cold build) — CI ran it, and both rust legs are green, including
`clamp_drops_stranded_sources` with fontmake installed.

## K1b — the badge popover named a file

The verifier's copy note: the popover read "deltas live in the Kinetikern
sidecar, not in this row", and the studio's UI copy never names sidecars or
their formats. The string is now `badgeHint(looseness)` in
`frontend/src/kinetikern-copy.js` — "Spaced by Kinetikern at -0.60. The deltas
live in the Kinetikern tab, not in this row." — and the node spec pins it and
asserts it never says sidecar/JSON/CSV.

Commands + results:
- `node e2e/kinetikern-copy.spec.mjs` → all passed (9 new cases).
- `eslint` on the two touched files → 0 problems; `tsc --noEmit` clean;
  `npm run test:unit` → 0 failing cases; `npm run build` green.
- `grep -rn sidecar frontend/src` → every remaining hit is a comment, not copy.
