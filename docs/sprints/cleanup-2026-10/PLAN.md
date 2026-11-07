# Cleanup sprint 2026-10 — plan

Source of truth: `docs/CODEBASE_AUDIT.md` §9 (same repo). IDs are stable,
in audit order, never renumbered. Format: ID — item — acceptance check.
A §10 reference means the audit could not verify the underlying claim:
verify first, record the result in LOG.md, only then change (or mark
BLOCKED). Line numbers in the audit are a 2026-10-16 snapshot — re-locate
by symbol.

## Tier 0 — safety

- T0.1 — Commit the Kinetikern feature + `.gitignore`; push `github-pages`. Check: `git status` clean; `origin/github-pages` == local HEAD.
- T0.2 — Fresh clone installs: restore `src/avar2_studio/static/.gitkeep` (or non-fatal hatch include); drop the `mkdir -p` workaround from `pages.yml` if unneeded. Check: scratch `git clone` → `python -m venv v` → `v/bin/pip install -e .[dev]` succeeds with no `mkdir`.
- T0.3 — Worker snapshot keeps the shadow: stage `.avar2-studio/shadow/<name>` (+ `shadow/transformed/`) into the job snapshot, or `capture_outlines` before staging. Check: staging test asserts shadow files land in `jobs/<id>/src/.avar2-studio/`; a real-worker build on `examples/crispy-mini` with a hand-drawn brace outline finds that outline in the built font. (Audit's "run `capture_outlines` once on Crispy" clause conflicts with the no-writes-to-Crispy rule → deferred to the user, BLOCKED here.)
- T0.4 — Cancel does not trip the build gate: after `DELETE /api/build/<id>`, `LAST_BUILD_STATUS` is not `"failed"` (distinct `"cancelled"` or previous value). Check: updated `test_cancel_a_running_build` asserts the status and that a subsequent mutating request is not 409.
- T0.5 — Seeds follow master edits: `regenerate_shadow` decides seed-vs-edit via the seed signature (`_restamp_seeds`/`_extract_brace_outlines`), not node-vs-default comparison. Check: probe test — add seeded brace, shift every master of that glyph +37, regenerate → untouched seed moves; hand-edited brace (stamp mismatch) preserved.
- T0.6 — Modal edit keeps drawings: `apply_layer_delta` carries `outline`/`source_sig` from the removed entry to the re-added one when the new entry lacks them. Check: endpoint test — store outline, apply a coordinate delta, outline still in sidecar and shadow.
- T0.7 — Mid-build saves re-resolve: `_run_one`'s `source_refresh` branch and the watcher's mid-build branch call `server._resolve_active_source()` before the CSV hook. Check: test or live probe (Crispy copy on port 5071) — edit the source mid-build; `/api/instances` reflects it after the follow-up build with no PATCH.
- T0.8 — Capture on Fontra save: `capture_outlines` runs on the watcher's Fontra-save branch (or drawer close) under `SIDECAR_LOCK`. Check: test exercising the watcher save branch finds the drawing in the sidecar.
- T0.9 — *(verifier, round 1)* Fontra saves are watched when source transforms are on: the observer watches `transformed/` then, and shadow saves go unseen, so T0.8 never fires for the user's setup. Schedule the observer on `shadow_dir_for(ORIGINAL_PATH)` too (or watch the path the Fontra session serves); exclude `transformed/` self-writes per T0.7. Check: live probe — shadow save → 1 capture when a drawing exists + 1 follow-up build; plus a watcher-classification test.
- T0.10 — *(verifier, round 1)* Fresh installs resolve incompatible deps (glyphsLib 6.16 makes `GSComponent.transform` a plain tuple → `grade_shadow.py:187` and `control_axes.py` `_parts_to_outline` raise AttributeError; breaks grade shadow + outline capture of component-bearing layers). Fix both sites version-agnostically; add a component-bearing capture test; add majors-only upper bounds. Check: suite green on the worktree venv (6.13.1) AND on latest (6.16.x, throwaway venv); component capture test passes on both.
- T0.11 — *(verifier, round 1, hygiene)* `.venv/` is not in `.gitignore` (main checkout relies on a global ignore). Check: fresh clone shows no untracked `.venv/`.

## Tier 1 — quick wins (audit grouping: server, control axes, frontend, rust/tests/process, docs)

### Server
- T1.1 — `global OBSERVER` so shutdown cannot raise `UnboundLocalError`. Check: probe — launch without the watcher branch, shut down cleanly.
- T1.2 — Suppression windows set *before* the writes they suppress, and again after the `transformed/` save. §10-M2: grep the build log for "Source changed mid-build" after a control-axis edit first. Check: test/probe shows no double build after a resolve.
- T1.3 — `secure_filename` on uploads. Check: POST with `../` filename is sanitised/rejected (test).
- T1.4 — `get_json(silent=True)` + 400 at the six sites. Check: test POSTing invalid JSON to each gets a JSON 400, not 415/500.
- T1.5 — Delete dead code: routes `/api/avar2/mappings` + `/api/avar2/sync-csv`, `schedule_debounced_build` + timer/lock, `create_instance_in_glyphs`, `rebuild_in_background` threads, empty `routes/` dir. Check: grep shows no references; pytest green.
- T1.6 — `get_font_family_name` without parsing the whole source. Check: unit test; same return on the fixture.
- T1.7 — `load_source_cached` on the listed read paths (`_load_axis_metadata`, `get_avar2_axes` ×2, `_grade_instance_coords`, `_grade_param_ranges`, `_get_glyphs_axis_tags`, `create_instance`). Check: grep; pytest green.
- T1.8 — Hoist `_declared_axis_ranges` to one call per build. Check: probe counts source parses per build before/after.
- T1.9 — Fail loudly when `--csv` is passed. Check: CLI exits non-zero with a clear message.
- T1.10 — `_running = None` only after promotion, so `wait_for_idle` returns the current outcome. Check: `test_build_jobs.py` test.
- T1.11 — Re-resolve in `_run_one`'s `source_refresh` branch + watcher mid-build branch — **aliased to T0.7** (done there; no separate commit).

### Control axes
- T1.12 — Case-insensitive lookup in `update_axis` (+ `SUBS` test). Check: new test — uppercase tag gets range validation.
- T1.13 — Stop lowercasing the Fontra session/responses (update the `"xouc"` pin in `test_open_editor.py`). Check: pytest green with authored-case assertions.
- T1.14 — Case-insensitive `labelRe` in the Fontra shim; drop the `'lcwd'` hardcode. Check: grep finds neither; shim behaviour unchanged for lowercase tags.
- T1.15 — Fix the `_normalise_layers` docstring (code is first-wins). Check: docstring matches code.
- T1.16 — Forward `location` in `embed-font.js` (`getReferenceMetrics(g, loc)`). Check: static HUD measures at the selected layer (code check + static build).

### Frontend
- T1.17 — Resolve the SpaceTab WIP intent (modified *and* orphaned by the Kinetikern WIP). Check: decision recorded in LOG.md; re-wired or removed; build green.
- T1.18 — Fix `HeaderProps` → tsc green. Check: `tsc --noEmit` exits 0.
- T1.19 — Replace the 4 irregular-whitespace chars → eslint error-free. Check: eslint exits with 0 errors.
- T1.20 — Add `lint`/`typecheck`/`test:unit` npm scripts and run them in both workflows. Check: scripts exist; `pages.yml` + `release.yml` run them.
- T1.21 — Delete orphans (`Avar2Preview`, `UpdateButton`, `ui/button.tsx` + 2 deps) — after T1.17 decides SpaceTab. Check: no importers (grep); build green.
- T1.22 — Reword the 12 UI copy leaks of file-format internals. Check: grep for the audit-listed strings returns none.
- T1.23 — Fence Kinetikern in static mode. Check: static build makes no `/api/kinetikern*` calls.
- T1.24 — `aria-label` on sliders. Check: DOM snapshot shows accessible names.
- T1.25 — Dismissable error banner. Check: banner has a working close control.

### Rust / tests / process
- T1.26 — Derive oracle python from `CARGO_MANIFEST_DIR`. Check: grep finds no `/Users/agyei` default; `cargo test` compiles.
- T1.27 — Oracle outputs under `target/tmp`. Check: no `/tmp` fixture dependency in code.
- T1.28 — `#[ignore]` the vacuous `correction_oracle` with a reason. Check: `cargo test` reports it ignored.
- T1.29 — `pip install -e .[dev]` + `[tool.ruff]` + triage. Check: `ruff check src tests` exit 0 (fixes or justified `noqa`s), baseline in LOG.md.
- T1.30 — `doctor` checks `spac-inject`, the dylib, and their env overrides. Check: doctor output lists both binaries + `AVAR2_SPAC_INJECT`/`AVAR2_KK2_DYLIB`.
- T1.31 — Log when the native injector is absent (not only on failure). Check: probe with binary hidden → message observed.
- T1.32 — Delete `wasm/fontc-web/pkg/`. Check: dir gone; no references (grep).
- T1.33 — Register or delete the `real_jobs` marker. Check: `pytest --markers` lists it, or no references remain.
- T1.34 — Prune merged local branches (7). Check: `git branch` before/after in LOG.md.
- T1.35 — Tag `v0.1.0` if 0.1.0 is actually published. §10: verify PyPI first; if not published, BLOCKED with reason.
- T1.36 — `git gc`. Check: `git count-objects -v` before/after in LOG.md (repo-level: affects the shared object store from any worktree).

### Docs
- T1.37 — Fix README images/links (relative, not `blob/main`). Check: every referenced image exists in the repo; links resolve on this branch.
- T1.38 — Write the env-var table. Check: table matches `grep -rn os.environ src/` plus frontend `VITE_*`.
- T1.39 — Fix HANDOVER's one-line contradictions (0.4 s debounce, test counts, orphaned L253–254). Check: values match the code.

## Tier 2 — S/M

- T2.1 — One lock + one atomic-write helper for every sidecar (incl. the supervisor's CSV hook). Check: all writers use them (grep); pytest green.
- T2.2 — `logging` migration. Check: `print(file=sys.stderr)` count collapses; errors surface via logging.
- T2.3 — `state.py` extraction + test `setattr` migration. Check: pytest green; globals moved.
- T2.4 — Assets out of Python strings (shim JS, focus CSS, AppleScript → package files), guarded by a byte-identical `_rewrite_html_paths` output test. Check: that test.
- T2.5 — Export polling on a per-job endpoint + `AbortSignal` + working Cancel. Check: test — two exports finishing inside one poll window resolve correctly.
- T2.6 — `useBuildWait()`/`useBuildStatus()` (one building/stale signal). Check: the four 60 s loops gone; build green.
- T2.7 — `api.capabilities` per provider + parity test. Check: parity test passes live vs static.
- T2.8 — Radix Dialog for the hand-rolled modals. Check: `role="dialog"`, Escape closes.
- T2.9 — CI `test.yml` + deterministic oracle fixtures (spike font generated in-test from `examples/crispy-mini`, cached under `target/tmp`). Check: workflow green; `cargo test` with no hand-made `/tmp` fonts.
- T2.10 — Sidecar version gate + unknown-key passthrough + corrupt-file surfacing in `_normalise`/`load`. Check: one test per behaviour.
- T2.11 — Watch sidecars; handle moved/created events (watchdog event-class test). Check: atomic-replace of `-control.json` triggers regen (test).
- T2.12 — Exclude `.avar2-studio/jobs/` from the recursive watch. §10-M3: reproduce the designspace perpetual-rebuild loop first. Check: designspace build does not self-retrigger (probe).
- T2.13 — Kinetikern explicit tuple marker (not a shape heuristic). Check: `strip()` spares genuine phantom-only compile tuples (the 22 in `three`) — test.
- T2.14 — `.avar2-studio/build/` reaper. Check: keeps newest N; test.
- T2.15 — Designspace authoring: 501 the routes or reach parity. Check: decision in LOG.md; routes 501 with a clear message or parity tests.
- T2.16 — Docs restructure per §7 (ARCHITECTURE, DEVELOPMENT, SIDECARS, guides/, decisions/ with the 12 ADRs, archive/). Check: links resolve; images exist; env table matches grep; each moved narrative comment left a one-line pointer.

## Tier 3 — L / structural

- T3.1 — `pipeline.py` + Flask-free worker. Check: `python -c "import avar2_studio.build_worker"` pulls no Flask.
- T3.2 — Blueprints per route group + `sorted(app.url_map.iter_rules())` snapshot test. Check: snapshot test.
- T3.3 — `control_axes/` package with `__init__` re-exporting today's names. Check: pytest green; imports unchanged.
- T3.4 — Single shim implementation shared with fontra-embed (built copy injected by the desktop proxy; static fallback). Check: harness test.
- T3.5 — One axis/CSV model across Python, Rust and JS. Check: three-way parity test on one CSV.
- T3.6 — App.jsx shrink to a shell with per-tab containers. Check: App ≈250 lines; build + smoke green.
- T3.7 — Stop committing the wasm blob (CI build; LFS/release asset; optional history rewrite). Check: blob out of HEAD; CI builds it; Pages green. (History rewrite = force-push → needs explicit user approval.)
- T3.8 — Parameterised default relocation for export (audit §2 "decided direction"). §10-H3: measure the export size effect first. Check: export passes `relocate_default=False`; first export test asserts natural tuple counts.
- T3.9 — Native-binary distribution (platform wheels carrying `spac-inject` + dylib, or Rust-only SPAC). Check: fresh install + doctor green.
- T3.10 — Watcher lifecycle object (`start/stop/reschedule`). Check: `/api/load-source` re-points watching — test.
