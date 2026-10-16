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
