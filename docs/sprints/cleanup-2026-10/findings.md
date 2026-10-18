# Verifier findings — cleanup sprint 2026-10

Written by the verifier only. Verdicts: **VERIFIED** (independently
reproduced by the verifier), **FAILED** (must be fixed before the next
hand-off), **BLOCKED** (cannot proceed; needs the user), **NOT-VERIFIED**
(claimed without evidence the verifier could run). NEW items are added by
the verifier and get IDs in the tier they belong to; add them to PLAN.md.

## Round 1 — hand-off `1e253cb` (HEAD `5947d7c`), Tier 0 — 2026-10-16

**Verification environment.** Fresh `git clone` at the hand-off SHA →
new venv → `pip install -e .[dev]` (no `mkdir`) → `pytest` with the
fontc-gated tests enabled (venv symlinked as `.venv`, `AVAR2_SPAC_INJECT`
and `AVAR2_KK2_DYLIB` pointed at the main checkout's binaries) → the new
Tier-0 tests run against the fixed source AND against the pre-fix source
(`4851cf6`) in a second clone → live smoke: studio on port 5071 from the
hand-off code (`PYTHONPATH=<clone>/src`) on a copy of Crispy's sources.

Suite in the fresh venv: **452 passed, 2 failed** (both
`tests/test_grade_with_italic_axis.py` — see T0.10; not caused by the
sprint, exposed by it). The Tier-0 set: 26 passed on the fixed source;
on the pre-fix source exactly one failure per fix:
`test_worker_shadow::test_a_shadow_drawing_survives_the_worker_build`,
`test_seed_follows_masters::test_an_untouched_seed_follows_master_edits`,
`test_layer_delta_carry::test_a_coordinate_edit_carries_the_drawing`,
`test_build_gate::test_a_cancelled_build_keeps_the_gate_open`.

Live smoke (Crispy copy, port 5071): boot build 33.8 s, one build only
(no double build on boot); font `Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,
YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`; mappings exact (rest
663.1/202.5/121.8, wght 399 → 662.9/202.0/121.5, wght 900 →
725.9/316.3/235.2, wght 100 → 619.2/48.7/41.0); `?format=woff2` serves
wOF2 903,592 B; export job `202 → poll → 200 font/woff2` 903,584 B wOF2;
`/api/instances` 30 rows, 9 with `YMOD`, no lowercase foundry keys.

| ID | Verdict | Evidence / what to change |
|---|---|---|
| T0.1 | **VERIFIED** | `origin/github-pages` == local `17360ba`; main checkout clean; sprint branch pushed; worktree at `~/Documents/avar2-studio-sprint`. |
| T0.2 | **VERIFIED** | Fresh clone installs with no `mkdir`; `.gitkeep` tracked; `.gitignore` root cause (dir vs contents) correctly diagnosed; `pages.yml` workaround removed. Opens T0.10 (below). |
| T0.3 | **VERIFIED** | `_stage` copies `shadow_path_for(orig)` into `jobs/<id>/src/.avar2-studio/shadow/`; stub staging test + real-worker e2e pass on the fix and fail on pre-fix source. Deviation (not staging `transformed/`, not staging the designspace shadow) is well-reasoned; accepted. |
| T0.4 | **VERIFIED** | `_apply_build_state` leaves status untouched on `cancelled`; previous-value semantics (a cancelled retry on a broken source keeps the gate closed) is the right call. Tests pass / fail-on-pre-fix as claimed. |
| T0.5 | **VERIFIED** | Seed-vs-edit by `seed_sig` with the pre-stamp fallback; probe test moves the seed by +37 on a master shift. Logged limitation (components/anchors-only edits read as untouched) accepted as pre-existing. |
| T0.6 | **VERIFIED** | Server-side carry in `apply_layer_delta`, one-to-one per glyph, never onto a `target` entry; endpoint test + regen assertion pass; fails on pre-fix source. |
| T0.7 | **FAILED** | The `_run_one` half is right (ordering test). The **watcher-branch half causes a resolve storm** on Crispy's real configuration (source transforms on): `_resolve_active_source()` writes `shadow/transformed/<name>.glyphs`, which IS the watched active source, so its own write re-fires the mid-build branch → resolve → write → … Measured: one glyphsLib load+save of the original 8 s into a build produced 21 full resolve passes (slant + round over 3,824 layers each), 18 × "Source changed mid-build; queued for after this build.", builds still spawning (build-8) 80 s later, and two *overlapping* resolves on different threads (the unlocked-resolve hazard, audit §3 F9). The first resolve also fired while the save was mid-write: `Warning: shadow regeneration failed: Unexpected EOF while parsing plist`. On `github-pages` the same save yields one follow-up build. **Fix:** (1) do not let the server's own writes re-trigger the watcher — set the suppression window AFTER the `transformed/` save (audit T1.2, pull it into this item) and/or ignore events whose path+mtime match a `_SELF_WRITES` record; (2) serialize `_resolve_active_source` behind an `RLock` (minimal slice of T2.1); (3) in the watcher, resolve only once the file is stable (mtime unchanged ≥0.5 s and the parse succeeds; retry once) instead of on the first event; (4) if after (1)–(3) the immediate watcher resolve still cannot be made safe, drop it and keep only the `_run_one` resolve (stale reads then last at most one build). **Acceptance (verifier will rerun):** Crispy copy on 5071, `POST /api/build`, 8 s later `glyphsLib.GSFont(src).save(src)` → exactly 2 builds total, ≤2 `[transform] slant` lines after the save, no `Unexpected EOF`, no overlapping resolves, idle within 2 builds + compress. Add this as a test where feasible (stub worker + fake watcher events). |
| T0.8 | **VERIFIED (unit) — see T0.9** | `_capture_shadow_drawings` + 3 tests verified. But on Crispy it never runs: with source transforms on, `GLYPHS_PATH` is `shadow/transformed/…`, the observer watches that dir, and a Fontra save writes `shadow/<name>` — **not watched**. Live probe: saving the shadow file directly → 0 builds, 0 captures. Pre-existing watcher gap (the agent logged it); it makes T0.8 inert for the user's setup, hence T0.9. |

### NEW items (add to PLAN.md; all Tier 0)

- **T0.9 — Fontra saves are watched when source transforms are on.**
  Schedule the observer on `shadow_dir_for(ORIGINAL_PATH)` as well (or
  watch the path the Fontra session actually serves), so a shadow save
  triggers `_capture_shadow_drawings` + resolve + rebuild exactly like a
  source save; exclude `shadow/transformed/` self-writes per T0.7. Check:
  live probe above (shadow save → 1 capture when a drawing exists, 1
  follow-up build) + a watcher test.
- **T0.10 — Fresh installs resolve incompatible dependencies.** A clean
  `pip install -e .[dev]` resolves glyphsLib **6.16.0** (dev venv: 6.13.1,
  Crispy venv: 6.12.4), fontTools 4.67.0 (4.63.0) and fontc **1.0.0**
  (0.6.0). glyphsLib ≥6.16 makes `GSComponent.transform` a plain tuple:
  `grade_shadow.py:187` and `control_axes.py:1710` (`c.transform.value`)
  raise `AttributeError` — the grade shadow and **outline capture of any
  layer with components** break on a fresh install (2 test failures:
  `test_grade_with_italic_axis` ×2; `capture_outlines` is only untested
  for components). fontc 1.0.0 passed the fontc-gated tests on
  crispy-mini; a full Crispy build on it is untested. **Fix:** make both
  sites version-agnostic (`tuple(tc.transform)` works on both), add a
  component-bearing layer to the capture tests, and either pin upper
  bounds (`glyphsLib<7`, `fontc<2`?) or add a `constraints.txt` + CI
  matrix (dev venv pins vs latest). Check: fresh-clone suite green with
  latest deps; capture test with a component passes on both glyphsLib
  versions.
- **T0.11 (hygiene, can ride with Tier 1) — `.venv/` is not in
  `.gitignore`**; the main checkout relies on a global ignore. Fresh
  clones show it untracked.

### Round-1 decision
Tier 0 is **not closed**: fix T0.7, implement T0.9 and T0.10, then hand
off Tier 0 again (round 2) **before** starting Tier 1 — T0.7 as pushed is
a regression against `github-pages` for the user's configuration, and
T0.10 means the install that T0.2 just enabled breaks the capture path.
Everything else in Tier 0 is accepted and need not be touched again.

## Round 2 — hand-off `38a4ccf` (HEAD `9e4b3fc`), Tier 0 re-check — 2026-10-16

**Verification environment.** Same recipe as round 1: (a) the round-1
clone fast-forwarded to `38a4ccf` with its latest-deps venv (glyphsLib
6.16.0 / fontTools 4.67.0 / fontc 1.0.0, fontc-gated tests enabled) →
**461 passed, 0 skipped**; (b) a brand-new clone + venv at `38a4ccf` →
`pip install -e .[dev]` resolves the same latest versions → **461
passed**; (c) the new tests against the pre-fix source (`8a06878`) on the
6.16 venv → 9 failures (watcher retry/serialization/classifier, component
round-trip, pre-axis-add capture, both grade tests), 14 passed on the
fixed source; (d) live smoke on a fresh Crispy copy at 5071.

Live smoke results: boot build 1 job only; mappings exact (rest
663.1/202.5/121.8, wght 399 → 662.9, 900 → 725.9/316.3/235.2, 100 →
619.2/48.7/41.0); all 12 axes; WOFF2 preview `wOF2`; 30 instances / 9
with `YMOD` / no lowercase keys. **T0.7 acceptance probe** (POST
`/api/build` in the background — note the endpoint blocks until the build
ends — then a glyphsLib re-save of the original 12 s in, health confirmed
`building: true`): **2 builds** from the POST, **2** `[transform] slant`
passes after the save (watcher resolve + `_run_one` resolve), **1** "Source
changed mid-build", **0** `Unexpected EOF`, **0** warnings, idle after the
compress. **T0.9 probe** (Fontra-style save of `shadow/Crispy.glyphs`
with all `a` brace layers shifted +250): "Captured 1 drawn outline(s)",
exactly **1** build, and the copy's sidecar holds the `a` outline at
dy = +250.0 (the computed `target` layer was skipped, as designed).

| ID | Verdict | Evidence / notes |
|---|---|---|
| T0.7 | **VERIFIED** | `_RESOLVE_LOCK`, mtime watermarks armed after every self-write (incl. after the `transformed/` save), settle-then-retry, first-event-per-final-mtime dedup. Probe numbers above match the acceptance exactly; round-1's storm (21 resolves) is gone. |
| T0.9 | **VERIFIED** | Shadow dir watched when transforms are on (`_classify_watched_path` + observer schedule); plus the real find: `_extract_brace_outlines` now pads short coordinate vectors, so braces created before an axis was appended match again — this is the audit's "28 layers with no captured outline" on Crispy. Regression test fails pre-fix. |
| T0.10 | **VERIFIED** | `getattr(t, "value", t)` at both sites; component round-trip test passes on glyphsLib 6.13 and 6.16; pins `glyphsLib<7`, `fontc<2`. Fresh install + suite green on the latest resolved set. |
| T0.11 | **VERIFIED** | `.venv/` ignored. |

**Tier 0 is closed.** Notes, no action required from the executor:
- The "duplicate (glyph, location) entries" observed in round 2 do not
  exist in the user's live `Crispy-control.json` (0 duplicate keys in
  LCWD and YMOD) — most likely an artefact of the probe copy. The live
  YMOD axis does carry six entries naming glyphs `2 3 4 5 6 8` (no such
  glyph names in the font); that is the user's data and is being raised
  with them, not with you.
- For a shadow save that arrives when no build is running, the server
  does not re-resolve `transformed/` before queuing (the worker regenerates
  from the staged shadow); read endpoints can lag for drawings only, which
  is harmless. Logged as an observation for T2's watcher/state work.
- `POST /api/build` blocking until completion is fine and documented
  behaviour, but any future mid-build test must start the build out of
  band (the executor's probe did; the verifier's first attempt did not).

**Next:** proceed to Tier 1 in audit order (server → control axes →
frontend → rust/tests/process → docs). Hand off at the end of Tier 1 or
every ~15 commits, whichever comes first.

## Round 3 — hand-off `9e4ca1d` (HEAD `ac69887`), Tier 1 — 2026-10-16

**Verification environment.** Brand-new clone + venv at `9e4ca1d`
(resolves glyphsLib 6.16.0 / fontc 1.0.0 / ruff 0.17.1): install OK;
`pytest` **482 passed, 1 failed** (see T1.8); `ruff check src tests` →
"All checks passed"; `doctor` lists the two new optional checks (absent →
hint; with `$AVAR2_SPAC_INJECT`/`$AVAR2_KK2_DYLIB` → paths); CLI
`build --csv` and server `--build-dir` exit **2** with the message;
frontend `npm ci` → lint **0 errors** (43 warnings baseline), typecheck
clean, `test:unit` all passed, vite build OK. Rust: with a warm `target/`
all 14 oracle test binaries compile; `round_corners_oracle` (in-repo input)
passes; `correction_oracle` is `#[ignore]`d with a reason; python derived
from `CARGO_MANIFEST_DIR`. New/updated tests against the pre-fix source
(`197d7a4`): **16 failures across T1.1/3/4/6/8/9/10/12/13/31**, 39 passed
on the fixed source. The ruff sweep was reviewed for removed side effects:
every removed call-bearing line is a deleted dead function, a renamed
variable, a split statement, or a dead computation (the `opsz_values`
block never applied its result; the dropped `preview` variable was
unused) — none removes behaviour. Live smoke (Crispy copy, 5071): boot
build 1 job; mappings exact; WOFF2 `wOF2`; 30 instances / 9 YMOD / no
lowercase keys; `open-editor` on `YMOD` answers `"tag": "YMOD"`; **one
layer delta → exactly 1 build, 0 mid-build lines, 1 resolve pass** (the
audit's M2 double build is indeed dead).

| ID | Verdict | Evidence / what to change |
|---|---|---|
| T1.1 | **VERIFIED** | `global OBSERVER`; `test_blind_launch` fails pre-fix, passes now. |
| T1.2 | **VERIFIED** | Probe above: 1 build per control-axis edit. |
| T1.3 | **VERIFIED** | `secure_filename`; traversal test fails pre-fix (escaped the workspace), passes now. |
| T1.4 | **VERIFIED** | Six sites silent; live: form-encoded body and non-string name both → JSON 400. |
| T1.5 | **VERIFIED** | Zero references to the deleted names in `src/`, `frontend/src/`, `tests/`, and fontra-embed. |
| T1.6 | **VERIFIED** | Stem without parsing; test. |
| T1.7 | **VERIFIED** | All six listed functions use `load_source_cached`; the `create_instance` unbound-`font` fix is a genuine extra. |
| T1.8 | **FAILED** | The memo is correct when both paths exist, but `_declared_ranges_cache_key` appends `None` for a missing/unset path, so with `GLYPHS_PATH` unset every call shares the key `((None, None), ())` and the first computation is returned forever. Repro: `pytest tests/test_declared_ranges_cache.py tests/test_avar2_default_relocation.py` (either order) → `test_two_default_rows_fail_loudly` raises `VariationModelError: Locations must be unique` instead of the loud failure, because it receives the ranges cached by the previous test; alone it passes. Production exposure: any call before a source is bound (boot, worker before `_bind_server_globals`, tests that patch `_load_axis_metadata`). **Fix:** never cache when any key part is `None`, and key on what the function actually consumes — the metadata dict (e.g. `json.dumps(_meta, sort_keys=True)`) plus overrides — rather than file stats; given T1.7 already caches the source parse, dropping the memo entirely is an acceptable alternative. Add the two-file ordering repro to the test. |
| T1.9 | **VERIFIED** | Both flags exit 2. |
| T1.10 | **VERIFIED** | `_running` cleared after promotion; test fails pre-fix. |
| T1.11 | aliased to T0.7 | — |
| T1.12 | **VERIFIED** | `SUBS` range test fails pre-fix. |
| T1.13 | **VERIFIED** | Live `open-editor` echoes authored case. |
| T1.14 | **VERIFIED (by reading)** | `labelRe` escaped + `i`; `'lcwd'` gone. Becomes testable in T3.4. |
| T1.15 | **VERIFIED** | Docstring matches code. |
| T1.16 | **BLOCKED (user)** | Correct call — the bug is in `~/Documents/fontra-embed`. The verifier can fix it there on the user's approval (one-line forward of `location` in `embed-font.js`). |
| T1.17 | **VERIFIED** | SpaceTab removed; the executor reports the user chose removal directly — user to confirm. `coverage.js`/`pinCorner`/`clampOutOfRange` correctly kept (static provider uses them); the now-dead live-mode `pinCorner`/`clampOutOfRange`/`addMappingRow` api methods + routes are a Tier-2 cleanup note. |
| T1.18 | **VERIFIED** | `tsc --noEmit` clean. |
| T1.19 | **VERIFIED** | eslint 0 errors. |
| T1.20 | **VERIFIED** | Scripts present; wired in `pages.yml` and `release.yml`; stale grade-model oracle values regenerated against the current Python. |
| T1.21 | **VERIFIED** | Orphans + 2 deps gone. |
| T1.22 | **FAILED (one string)** | 11 of 12 reworded. Leftover: `frontend/src/App.jsx:634` — ``This rewrites the sidecar and can't be undone from here.`` (a `window.confirm`). Reword (e.g. "This updates the studio's saved layers and can't be undone from here."). |
| T1.23 | **VERIFIED** | Tab + content gated on `!staticMode`. |
| T1.24 | **VERIFIED** | 10 of 10 range inputs carry `aria-label` (multi-line-aware check). |
| T1.25 | **VERIFIED** | Dismiss control present. |
| T1.26–T1.28 | **VERIFIED** | Compile + oracle run above. `/tmp` INPUT fixtures remain until T2's deterministic generation, as logged. |
| T1.29 | **VERIFIED** | ruff clean in a fresh venv; sweep reviewed (above); suite green. |
| T1.30 | **VERIFIED** | `doctor` output above. Nit: `test_doctor_native` assumes no repo-relative binary exists — it fails in a checkout with a built `target/`; monkeypatch `_native_injector`/`engine_path` to make it hermetic (fold into Tier 2's test work). |
| T1.31 | **VERIFIED** | Test fails pre-fix. |
| T1.32 | **VERIFIED** | Absent in the clone; the stale copy in the main checkout is the user's to delete. |
| T1.33 | **VERIFIED** | Marker registered. |
| T1.34 | **VERIFIED** | Six merged branches gone; `grade-comparison` kept (user's call). |
| T1.35 | **BLOCKED (user)** | avar2-studio is not on PyPI, so there is no 0.1.0 to tag. User decides: publish 0.1.0 (release workflow exists) or correct README/`pyproject` to say so. |
| T1.36 | **VERIFIED** | One pack, 128 loose objects. |
| T1.37 | **VERIFIED** | Every README link/image target resolves (0 missing). |
| T1.38 | **VERIFIED** | All six `os.environ` names in `src/` documented. |
| T1.39 | **VERIFIED** | Debounce/test-count/`~832` claims gone. Nit: the HANDOVER title still says "July 2026, updated August 2026" — goes away with the Tier-2 split. |

### Round-3 decision
Tier 1 closes once **T1.8** and **T1.22** are fixed. **APPROVED: fix T1.8
and T1.22 first, then Tier 2 in audit order** (one lock + one atomic-write
helper for every sidecar incl. the supervisor's CSV hook; `logging`
migration; `state.py` + test migration; assets out of Python strings;
export polling on a per-job endpoint + AbortSignal + Cancel; `useBuildWait`
/ `useBuildStatus`; `api.capabilities` + parity test; Radix Dialog for the
hand-rolled modals; CI `test.yml` + deterministic oracle fixtures; version
gate / unknown-key passthrough / corrupt-file surfacing in the sidecar
layer; watch sidecars + handle moved/created events; exclude
`.avar2-studio/jobs/` from the recursive watch; Kinetikern explicit tuple
marker; `build/` reaper; designspace parity-or-501; docs restructure +
the 12 ADRs). Hand off every ~15 commits — Tier 2 will take several
rounds. Carry the two nits (doctor test hermeticity; dead live-mode
pin/clamp/addMappingRow api methods + routes) into Tier 2's test and
dead-code work.

## Rounds 4 + 5 — hand-off `725d605` (HEAD `19cb51c`), Tier 2 remainder + Tier 3 — 2026-10-17

(Round 4's hand-off was not verified at the time — the verifier was
paused — so both are covered here.)

**Verification environment.** Brand-new clone + venv at `725d605`:
install OK; `import avar2_studio.build_worker` loads **no Flask** (T3.1);
`pytest` **526 passed, 0 skipped** (fontc-gated and engine tests on);
`ruff` clean; frontend `npm ci` → lint exit 0 (0 errors, 2 warnings),
typecheck 0, `test:unit` all passed (incl. the api-parity, export-poll and
build-wait specs), vite build OK, `App.jsx` 207 lines; a wheel built from
the clone carries `routes/`, `control_axes/`, `state.py`, `pipeline.py`
and the Fontra assets. Rust (warm target, no `/tmp` fixtures): **18
binaries, 22 passed, 0 failed, 1 ignored** (incl. `spac_csv_parity`).
Live smoke on a fresh Crispy copy (port 5071, hand-off code): boot
build 1 job, 0 warnings; mappings exact (rest 663.1/202.5/121.8, wght 399
→ 662.9, 900 → 725.9/316.3/235.2, 100 → 619.2/48.7/41.0); all 12 axes;
`?format=woff2` serves `wOF2`; 30 instances / 9 YMOD / no lowercase keys;
**T2.5 live:** `POST /api/export-font` → `GET /api/build/<id>` reports
`state: finished, artifact_ready: true` → download `font/woff2` 917,064 B
`wOF2`.

| ID | Verdict | Evidence / notes |
|---|---|---|
| T2.3 | **VERIFIED** | `state.py` holds 38 names; no `server.X =` writes left in src/tests; suite green in the fresh clone. |
| T2.5 | **VERIFIED** | Per-job endpoint live (above); `useBuildWait`/export-poll specs in `test:unit`. |
| T2.6 | **VERIFIED (gates + reading)** | `hooks/useBuildWait.js`, `useBuildStatus.jsx`; the four 60 s loops are gone from App.jsx (207 lines). |
| T2.7 | **VERIFIED** | `api-parity.spec.mjs` wired into `test:unit` and passing. |
| T2.8 | **VERIFIED (gates + reading)** | 7 components import the vendored Dialog / carry `role="dialog"`; Escape/focus behaviour taken from the gates and the executor's DOM smoke, not re-driven here. |
| T3.1 | **VERIFIED** | Flask absent from the worker's import graph; real-worker e2e in the suite. |
| T3.2 | **VERIFIED** | 13 blueprints; `tests/test_url_map_snapshot.py` green; server.py 1,012 lines. |
| T3.3 | **VERIFIED** | `control_axes/` package (schema, naming, outlines, shadow_glyphs, shadow_designspace); suite green; wheel ships it. |
| T3.4 | SKIPPED (user decision, per the executor's log) | — |
| T3.5 | **VERIFIED** | `csv_io.read_csv_spac_overrides` is the one reader; `spac.rs`'s CSV parser gone; parity tests on both sides pass (python suite + cargo). |
| T3.6 | **VERIFIED** | App.jsx 207 lines; `useStudioData` gates "Loading…" on first load only (`firstLoadDoneRef`) — the audit's unmount-on-refresh bug is closed by construction. |
| T3.7 | BLOCKED (user) | History rewrite / LFS strategy for the wasm blob needs the user's call. |
| T3.8 | **FAILED** | The implementation keeps the inserted-origin-master staging (`_build_export_source`) and only turns the step-3.5 rebase off — the method ADR 0007 rejects. Measured on the Crispy copy, the set-default export (wght 400/wdth 100/opsz 144) vs the lossless preview font, instantiated at the same absolute parametric locations: ORIGIN ≤ 3 u and the 47/1/1 master corner ≤ 2 u, **but the wght 900 stop deviates up to 543 u (e 4, n 476, o 543, a 468, H 516, g 451) and the wght 100 stop up to 761 u** — the interior drift that made the user reject this export on 2026-10-11. Its mappings are also off by the rounding (rest 663.0/202.0/122.0; wght 399 → 662.8/201.5/121.7). Size 1.4 MB vs 10.5 MB is the symptom, not a win. **Fix (the ADR's direction):** drop `_build_export_source` from the export path; build from the staged ORIGINAL source exactly like the preview with relocation ON and the relocation target P = the requested instance's authored parametric location (the authored-row lookup already finds it; no `round()`), regenerate the avar2 table anchored at P, then the transform chain and hidden axes; WOFF2 (ADR 0002) absorbs the size. **Acceptance:** the drift probe (`scratchpad/drift_probe.py`, or an equivalent test on crispy-mini) reads ≤ 3 u at every authored stop and both master corners; the mapping probe is exact; a test asserts outline parity between a preview build and a set-default export at an interior authored location. The existing `test_export_relocation.py` stays valid for the parameter itself. |
| T3.9 | BLOCKED (user) | Platform wheels vs Rust-only SPAC needs the user's call. |
| T3.10 | **VERIFIED (test + reading)** | `watch.SourceWatcher.start/stop/reschedule`; `routes/load.py` reschedules after `_apply_source_path`; `tests/test_watch_lifecycle.py` green. Not driven live: `/api/load-source` only takes an upload or `{example: id}`, and staging an example writes to the user's `~/.avar2-studio/workspace`, which the verifier does not touch. |

### Also required before Tier 3 closes
- **Docs follow-through for the Tier-3 moves:** `docs/ARCHITECTURE.md` /
  `DEVELOPMENT.md` do not mention `state.py`, `watch.py` or `routes/`
  (`pipeline.py` ×2, `control_axes/` ×1 only). Update the module map and
  the process description.
- **Stale doc paths cited from code** (the "grep-verified" claim in the
  log does not hold): `docs/WORKER_BUILD_PLAN.md` in `server.py` and
  `state.py`; `docs/control-axes.md` (never existed) in
  `frontend/src/components/ControlAxes.jsx`; `docs/migration-github-pages.md`
  in `frontend/src/fontc-compile.js` and `static-api.js` (now under
  `docs/archive/`). The two `examples/` README anchors to
  `#axis-surface…` still point at a heading that does not exist.

### Round-4/5 decision
Tier 2 is **closed**. Tier 3 closes once T3.8 is re-done per ADR 0007
(with the fidelity test), the docs module map is updated and the stale
paths fixed. T3.7 and T3.9 wait on the user; T3.4 stands skipped. The
three chat-only decisions remain the user's to record or drop.

### User decisions recorded 2026-10-17 (unblock T3.7 and T3.9)
- **T3.7 — history is important: NO history rewrite.** Stop committing
  the wasm blob from here on (CI builds it in `pages.yml`/`release.yml`
  via `wasm-pack`; keep the JS glue; the binary leaves HEAD via `git rm`
  + `.gitignore`), but the 25 historical revisions stay — no
  `filter-repo`, no force-push to `github-pages`. Record as
  `docs/decisions/0014-wasm-blob-built-in-ci-history-kept.md`.
- **T3.9 — users must be able to use the SPAC axis OR Kinetikern.** Both
  accelerators must reach end users: platform wheels (macOS arm64/x86_64,
  Linux) carrying `spac-inject` and `libkinetikern2` (maturin or
  cibuildwheel), `doctor` reporting both, and the fontTools SPAC fallback
  kept for pure-Python installs (Kinetikern has no fallback, so the
  wheel must carry its engine). The `AVAR2_SPAC_INJECT` /
  `AVAR2_KK2_DYLIB` overrides stay. Record as
  `docs/decisions/0015-native-accelerators-ship-in-platform-wheels.md`.
  Acceptance: a fresh install from the built wheel on this Mac → `doctor`
  shows both accelerators found with no env overrides; `pytest
  tests/test_kinetikern.py` runs (not skipped) against the wheel's dylib.
- **T3.4 is back in scope (user instruction 2026-10-17).** One shim
  implementation shared with fontra-embed: fontra-embed's `focused-ui.js`
  / `metrics-hud.js` are the port of `fontra/shim.js`; the desktop proxy
  injects a built copy of that implementation parameterised by the same
  session config (`/api/fontra-shim-config`), with `fontra/shim.js`
  retained only as the fallback when the fontra-embed build is absent.
  The two implementations' known divergences (editing auto-enabled on
  studio rows, the "→ computed" rule, the source-name sort) are
  reconciled, not duplicated. The fontra-embed change goes on a branch in
  `~/Documents/fontra-embed` (its HEAD already carries the T1.16 fix,
  `f063a02`); no push there without the user. Acceptance: the
  `_rewrite_html_paths` golden test updated once and passing; the
  fontra-embed harness (`harness/verify.mjs`) green on the shared file;
  the desktop open-editor smoke on a Crispy copy shows the layer labels,
  sort and HUD unchanged.

## Round 6 — hand-off `2b99233` (HEAD `7c74265`), Tier 3 completion — 2026-10-18

**Verification environment.** Fresh clone + venv at `2b99233`: install OK;
**532 passed, 0 skipped**; ruff clean; worker imports no Flask; frontend
lint 0 errors (34 warnings), typecheck 0, `test:unit` all passed, build OK.
Rust warm target: 18 binaries, 22 passed, 0 failed, 1 ignored.
fontra-embed branch `shared-studio-shim` (3 unpushed commits): harness
`npm run verify` **8/8 PASS**. Live smoke on a fresh Crispy copy: boot
build 1 job, 0 warnings; mappings exact; 30 instances / 9 YMOD; the
desktop `open-editor` page references `embed-shim.js` (fetched 200) with
the inline fallback id present once.

| ID | Verdict | Evidence / what to change |
|---|---|---|
| T3.4 | **VERIFIED** | One implementation: `fontra/embed-shim.js` built from fontra-embed @ `3de37a6` (provenance header), injected by the desktop proxy; `fontra/shim.js` kept as fallback; divergences reconciled (escaped case-insensitive labelRe both sides, source-name sort in the embed, HUD filters the session tag); harness 8/8; goldens regenerated once. fontra-embed branch stays unpushed until the user says so. |
| T3.9 | **VERIFIED** | Reproduced the release recipe in the fresh clone: cargo-built `spac-inject` + `libkinetikern2.dylib` into `src/avar2_studio/bin/` → `pip wheel` produced `avar2_studio-0.1.0-py3-none-macosx_26_0_arm64.whl` carrying both → clean venv install → `doctor` finds both at site-packages with NO env overrides → `tests/test_kinetikern*.py` **8 passed** against the wheel's dylib. (A tree without the binaries still builds the pure wheel — by design; the release matrix stages them.) |
| T3.8 | **FAILED (second time, new cause)** | The inserted-master staging is gone and relocation now targets P with the authored out: verbatim (export fvar defaults 663.1/202.5/121.8; mappings exact) — that part is right. But `_export_pipeline` now builds **`state.ORIGINAL_PATH`**, the raw source, instead of the resolved ACTIVE source the preview builds (control-axis shadow + grade braces + source-stage transforms). Measured on the Crispy copy: the set-default export has axes `XTRA/XOPQ/YOPQ/opsz/wdth/wght/SPAC` only — **no LCWD, YMOD, GRAD, slnt or ROND** — sharp corners (`e` 14 points vs 60 in the preview), 13,774 gvar tuples vs 124,432; 600 KB because most of the design is missing. The crispy-mini fidelity test passed only because that fixture has no control axes or transforms. **Fix:** build the export from the resolved active source — `build_jobs._stage` already stages the server-side active source as `active-<name>` for exports; compile THAT (with its `config.yaml`/metadata) with `relocate_target=P`, then `_apply_transform_chain` and hidden axes, exactly as the preview path does. **Acceptance:** on a fixture WITH a control axis and a source-stage transform enabled (crispy-mini + a ymod brace + round_corners on, or the Crispy copy at 5071), the export's fvar axis set equals the preview's, every glyph's point count matches, and the drift probe reads ≤ 3 u at the authored stops; the test must assert the axis-set and point-count parity, not only the drift. |
| T3.7 | **needs the user** | STATUS says "SKIPPED (user decision, 2026-10-18)" and the log has no entry; the blob is still tracked (`frontend/src/wasm/fontc-web/fontc_web_bg.wasm`). The recorded decision was "no history rewrite" — the going-forward half (CI builds the blob; `git rm` + ignore; history untouched) is still open unless the user confirms the whole item is dropped. |

### Nits still open
- `frontend/src/components/ControlAxes.jsx` cites `docs/control-axes.md`
  (never existed) and `frontend/src/fontc-compile.js` /
  `static-api.js` cite `docs/migration-github-pages.md` (now
  `docs/archive/…`) — listed in round 4+5, still unfixed.
- The verifier's drift probe must check point-count parity before
  comparing coordinates (it reported "0.0 u" with differing point counts;
  fixed in the scratchpad copy — any in-repo test should do the same).

### Round-6 decision
T3.4 and T3.9 close. **T3.8 is re-opened** (wrong source). Tier 3 closes
once T3.8 builds the active source with the parity test above and the
two stale citations are fixed; T3.7 awaits the user's word.

## Round 7 — executor + verifier: the verifier (user instruction 2026-10-19) — head 

Executed and self-verified by the verifier (the executor agent was not
re-run for these): T3.8 third pass, T3.7's going-forward half, the three
stale doc citations.

**Verification.** Fresh clone at  (before the final pruning
commit): install OK, **533 passed**, ruff clean, lint 0 errors, typecheck
0,  all passed, Vite build OK **with no wasm binary on disk**,
all three workflows load. Worktree at : **535 passed**, ruff
clean. Live on the Crispy copy (5071, worktree code): preview build 1
job, mappings exact.

| ID | Verdict | Evidence |
|---|---|---|
| T3.8 | **VERIFIED, with a documented limit** |  compiles  (the staged active source) with the lossless relocation to P. Crispy copy, export at the ORIGIN style (wght 400/wdth 100/opsz 144): axes identical to the preview, 0 glyphs with differing point counts, 124,432 tuples both, mappings exact (rest 663.1/202.5/121.8, wght 399 → 662.9), **0.0 u drift** at the wght 900 and 100 stops, the ORIGIN and the 47/1/1 corner. Real-worker parity test on crispy-mini (ymod brace + round_corners) in the suite. Limit: an export resting at a FAR interior style (wght 900) exceeds gvar's u16 per-glyph header block on this twin-heavy font (: 2,707 tuples);  prunes sub-visible tuples where that suffices and otherwise fails with a readable error naming the glyph and the ways out (was: bare ). Unit-tested on . Follow-up proposed, not in this backlog: an export option that compiles without the slant/ROND twin masters. |
| T3.7 | **VERIFIED (going-forward half; history kept)** |  untracked + ignored; glue tracked;  and both  bundle jobs run  before the frontend build;  command validated locally on the warm target; fresh clone without the binary builds (Vite warns only); ADR 0014 + index + DEVELOPMENT note. GitHub Actions not run from here. |
| nits | **VERIFIED** | The three stale doc citations repointed; every  path cited from code resolves. |

### Backlog status
Every item in PLAN.md is now VERIFIED (Tier 0: rounds 1–2; Tier 1: round
3 + fixes; Tier 2: rounds 4–5; Tier 3: rounds 5–7). Open outside the
backlog, for the user: the three chat-only decisions (record or drop);
the proposed twin-free export option; pushing fontra-embed's
 branch (deploys the Pages demo); and merging
 into  (Crispy's CI pins that branch).

---

## Standing instructions (apply to every round) — revised 2026-10-16

**Protocol change (user instruction, 2026-10-16): checkpoints do not
stop the sprint.** The approved scope is the whole remaining backlog in
PLAN.md order. Push a checkpoint every ~15 commits or at a tier boundary
(`STATE: checkpoint`), keep working, and at the next checkpoint pull and
fix any newly FAILED items before new work. `STATE:
ready-for-verification` is reserved for the end of the backlog. The
verifier's approval is expressed as the absence of FAILED items, plus the
`APPROVED:` line in STATUS.md, which now reads "everything remaining".

**User decisions recorded 2026-10-16:** T1.16 — fixed by the verifier in
`~/Documents/fontra-embed` (`embed-font.js` forwards `location`; local
commit, not yet deployed) — close it as VERIFIED-elsewhere. T1.35 — the
user will publish 0.1.0 separately; no sprint action, leave README and
`pyproject` as they are. T1.17 — the user confirms SpaceTab was meant to
go entirely.


1. **Scope is the whole audit.** The sprint runs until every item in
   `PLAN.md` — all of §9: Tier 0, Tier 1, Tier 2 (including the docs
   restructure and the 12 ADRs), Tier 3 — is **VERIFIED** here, or is
   **BLOCKED** with a reason that needs the user's decision. Do not stop
   after Tier 0 or Tier 1; do not skip Tier 3 as "later". The verifier
   sets `STATE: verified-complete` only when the last item closes.
2. **Cadence.** Hand off after each tier, or every ~15 commits inside a
   big tier (Tier 1 and Tier 2 will need several rounds). Each hand-off
   is verified in a fresh clone + live smoke, and this file gains a new
   "Round N" section. FAILED items from a round are fixed **before** new
   items in the next round; VERIFIED items are closed.
3. **Order.** Audit order within each tier (server → control axes →
   frontend → rust/tests/process → docs for Tier 1). Items the verifier
   adds (T0.9…, T1.x…) slot into their tier at the end and are due in
   the next hand-off of that tier.
4. **Evidence bar is unchanged.** A command and its result in `LOG.md`
   for every claim; a test or probe for every behavioural change; §10
   claims verified before changed. Anything else is NOT-VERIFIED and
   comes back.
5. **Keep the user's environment out of it.** Never touch
   `~/Documents/Crispy`; never start/stop port 5070; live probes on a
   copy at 5071 only.
