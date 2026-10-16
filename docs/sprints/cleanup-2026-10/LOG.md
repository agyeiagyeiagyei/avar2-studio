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
