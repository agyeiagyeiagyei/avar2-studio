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

## Round 8 — local test run, head `f27ff5b` — 2026-10-19

Context: the user asked to run the sprint build locally before any push
to Pages (studio on 5072 against a COPY of Crispy, static demo on 5075).
First report from that run: "I was in the Preview tab, moved a weight
slider, and got a rebuild."

**Diagnosis (reproduced headless, Python Playwright against 5072).**
The Preview tab's sliders are isolated local state and never save:
load → Preview → drag wght produced only `GET /api/mapped-location`.
The rebuild came from an earlier step: clicking an instance row seeds
`editingCoordinates` from the row, the auto-save effect in
`useInstanceEditing.jsx` had no saved baseline for that row, so it
PUT the unchanged coordinates through the csv-only path 800 ms after the
click. That rewrite bumped the mappings' mtime, `/api/health` reported
`build_stale`, and the App shell's "rebuild once when Preview opens while
stale" effect kicked a full build. Pre-existing: the audit baseline
`4e4313b` has the same effect, so it is not a sprint regression.

**Fix `f27ff5b`.** Skip the auto-save when the persisted coordinate set
equals the selected row's own coordinates (same preview-only filtering as
the saved-key guard). Verified against the rebuilt bundle on 5072:

| scenario | before | after |
| --- | --- | --- |
| row click → Preview → drag wght | PUT at +0.8 s, `POST /api/build` on Preview open | no PUT, no build |
| row click → sidebar XOPQ drag → Preview | PUT, build | PUT (real edit), one build on Preview open (by design) |

Gates on the fix: frontend typecheck clean, `test:unit` all passed. Lint
reports 690 errors, all in `frontend/dist-pages/assets` (my local Pages
build output; `eslint.config.js` ignores only `build/` and
`node_modules/`). CI is unaffected because `pages.yml` lints before it
builds `dist-pages`, but a local `npm run lint` after a Pages build is
noisy — add `dist-pages/` to the eslint ignores if the user wants (not
done: outside the report).

**Not covered by an automated test.** The hooks have no React test
harness (`test:unit` runs plain-node specs), so the regression check is
the headless scenario above; its script lives in the session scratchpad.

**Still unconfirmable without a push:** GitHub Actions runs (cold
wasm-pack, Linux/x86_64 wheel legs, rust-cache), the Pages deploy and
the fontra-embed deploy.

### Round-8 addendum — the origin warning on 5072 is a staging artefact

The verifier's server on 5072 (staged at `/private/tmp/av2-local-test`
per the routine) shows "No instance row sits at the axis defaults (… opsz
72 …)". Cause: the routine's first version staged the source files but
not `.avar2-studio/axis-metadata.json`, so the studio regenerated the
metadata without the declared defaults and `opsz` fell back to the
registered convention (72). The real Crispy declares `opsz` default 144,
which makes `Default 400 MaxOpsz` (renamed from `Def` in
`instance-renames.json`) the ORIGIN row; the designer's studio is not
affected. Routine fixed: stage the authored `.avar2-studio` state and
check `origin_warning` null + `OPSZ.default` 144 before A1. Results
gathered against the incomplete copy must be re-run after re-staging
(the ORIGIN row changes default relocation, so A10–A13 and anything that
builds or exports depend on it).

Cosmetic, pre-existing, not fixed: the warning text lists `lcwd -1000`
and `ymod -1000` because control axes carry placeholder ranges with no
`default` in the metadata and `_declared_axis_ranges` prints min as the
default; their real defaults come from the control sidecar (0 and 100).
The row test is unaffected (rows never carry control inputs), only the
message is misleading. Candidate fix: exclude control axes from
`defaults_desc` or print their sidecar defaults.

## Round 9 — local test routine, Part A on a Crispy copy (2026-10-19)

Ran `LOCAL_TEST_ROUTINE.md` Part A end to end against
`/private/tmp/av2-routine` (port 5072) at `de4de1a`, after re-staging
with the authored `.avar2-studio` state. Full evidence in
`LOCAL_TEST_RESULTS.md`; this section is the verdict summary and the
root causes.

**Greens (13).** A1 staging gate + axes/rows, A2 first load, A3
select≠edit, A4 Preview-only traffic, A5 one csv-only save + one
build, A9 multi-select LCWD broadcast, A11 secondary axis + Fontra on
the shadow, A12 exports (TTF/WOFF2; 496 glyphs, 0 point-count
mismatches, 0.0 u drift at wght 900; set-default at 921.8/605.8
returns the documented gvar-limit error), A13 Kinetikern (KKRN
present on apply, absent after revert), A14 Tester, A15 Load Font
(example and back), A17 watcher (exactly one build, no unexpected
EOF), A21 clean install (pytest 536, ruff `src tests` clean, lint 0
errors, typecheck/unit/build green, kinetikern2 Rust 53 passed).

**FAILs (7).**

1. **A6 — a source row can never be green.** `_sameCoords` compares
   key counts; `csvCoords` (CSV `out`, 3 keys) vs `source_coordinates`
   (8 keys, control/transform axes included at their defaults) is
   always false. `Default 400 MaxOpsz` has `out == glyphs_coordinates`
   for XTRA/XOPQ/YOPQ yet its dot is orange.
2. **A7 — stale rename alias → 500 on “Save to source file”.**
   `instance-renames.json` says `Def → Default 400 MaxOpsz`, but the
   `.glyphs` instance is already named `Default 400 MaxOpsz`;
   `_source_instance_name` maps back to a `Def` that no longer exists
   and the route 500s. Repro: direct PUT, source unchanged.
3. **A8 — New Instance is blocked by HTML step validation.** The modal
   seeds `step=0.1` inputs with float32 defaults
   (`663.1000061035156`), so native validation refuses to submit (no
   POST). Rounding the fields makes create/duplicate/rename/delete
   pass.
4. **A10 — Transforms menu and G badges never render.**
   `useTransforms()`'s provider value omits `transforms` and `grade`
   (they live in `useStudioData`), so Header's menu condition and the
   rows' `gradeEnabled` are always false even though the sidecars
   drive the build.
5. **A16 — config bundle cannot import into its own source.** Dry-run
   reports 163 `lcwd` layer errors (“pins axis 'LCWD', which this
   source does not have”); the validator checks layers against the
   source axes before the control-axis sidecar applies.
6. **A18 — a source that no longer parses does not trip the build
   gate.** The build falls back to a reduced 7-axis font and reports
   success, so `LAST_BUILD_STATUS` stays `ok`; the PUT then 500s with
   `ParseError: Junk after plist`, and the degraded font replaces the
   last-good full font. Restoring + rebuilding succeeds.
7. **A19 — one `/api/health` 404 on the static demo.** `selectApiMode`
   probes it by design; Chrome logs the 404, so “no `/api/*` / zero
   console errors” is not literally met. Everything else (example
   switch, sliders, wasm) passes.

**A20 note.** Console-clean passes at idle; running it while the
WOFF2 compress job is alive logs three 409s from
`/api/font?format=woff2`. Idle must be `building: false` AND no
`build_job`.

**A21 scope notes.** `ruff check .` (incl. `examples/`, `spike/`)
still reports 13 pre-existing errors; round 7's “ruff clean” holds for
`src tests`. `wasm/fontc-web cargo test` could not compile — the
machine is at 100% disk with 1.2 GB free — so that crate is BLOCKED
here, not failed.

**Routine/probe defects fixed.** The accelerator paths in the setup
snippet expanded `~` against the new `HOME` and silently disabled both
engines (SPAC fell back to fontTools, Kinetikern 503); they now use
`$WT/...`. The routine now defines idle with `build_job` included.
`ui_probe.py` gained `--sources`, dialog acceptance, response
capture, FAIL screenshots and one scenario per test ID.

**Part B is untouched** (Glyphs/Fontra/design apps/push are the
designer's).

## Round 10 — the Round-9 FAILs fixed and re-run (2026-10-19/20)

Fixes (uncommitted until the user approves the commit), one root cause
each, each with a test:

| Round-9 FAIL | Root cause | Fix | Test |
| --- | --- | --- | --- |
| A6 orange source rows | `getInstanceSyncStatus` compares the CSV `out` map (parametric only) with `source_coordinates` (every axis), and `_sameCoords` bails on the key-count mismatch | compare against the subset of source coordinates the CSV actually carries | `test_source_writeback_targets.py` |
| A7 500 on save-to-source | `instance-renames.json` alias `Def → Default 400 MaxOpsz` is stale; the route maps back to a `Def` the source no longer has | fall back to the requested name when the alias target is absent; skip the CSV row only when the CSV branch wrote it; a studio-only delete no longer touches the source | `test_save_falls_back_to_the_display_name_when_the_alias_is_stale`, `test_full_source_save_resyncs_its_own_csv_row`, `test_delete_of_a_studio_only_row_does_not_touch_the_source` |
| A8 New Instance blocked | `DuplicateModal` seeds `step={0.1}` inputs with float32 defaults (`663.1000061035156`), so native validation refuses to submit | `step="any"` (the server clamps) | lifecycle scenario |
| A10 no Transforms menu / no G badges | `useTransforms()`'s provider value omits `transforms`/`grade` | re-export them (they stay owned by `useStudioData`) | grade scenario |
| A16 self-import rejected | layer pins and control tags are compared case-sensitively (`lcwd`/`LCWD`) | case-insensitive tag lookup in `validate_bundle`; a layer on a missing glyph is a warning; CSV read/write keeps its own line endings | `test_round_trip_accepts_authored_tag_case_on_layers`, `test_layer_for_a_missing_glyph_is_a_warning`, `test_apply_bundle_preserves_the_csv_bytes` |
| A18 broken source passes the gate | the pipeline falls back to a reduced font and reports success, so `LAST_BUILD_STATUS` stays `ok` | `_run_build()` pre-flights `load_source`; a loader failure records `last_build_status: failed` with the parse message and keeps the last-good font | `test_unreadable_source_fails_the_build` |
| A19 `/api/health` 404 on the static demo | `selectApiMode()` probes the backend before deciding | the served index carries `<meta name="avar2-backend">`; production without it goes straight to static mode | `test_ui_index_marker.py` |

**Two new findings from the re-run.**

1. **A modest ORIGIN-row edit takes the mapped build over gvar's
   per-glyph header limit.** Once A7 actually saves, the probe's 1 %
   XOPQ nudge on `Default 400 MaxOpsz` (202.5 → 191.2) makes the
   *preview* build fail: “glyph 'g' has too many variation tuples …
   after relocating the default (1767 tuples; dropping the 428
   sub-visible ones was not enough)”, the studio shows the amber
   fallback banner, the exported estimates drop to the plain build
   (642 KB) and the parametric sliders disappear — so A12 cannot even
   run. Restoring the row restores the mapped build. This is the
   documented relocation limit, but 1 % of a slider is a plausible
   design move, so it deserves a note in the studio UI (the message is
   readable and the fallback is safe; it is discovery cost, not data
   loss). Probe consequence: `export` now runs **before** the origin
   edit.
2. **A config import queues four sequential full rebuilds.** Applying a
   bundle rewrites four sidecars; each write schedules its own rebuild
   — observed `build-…-3/4/5` at 47–48 s each plus a ~16 s compress
   apiece, ~7 minutes on Crispy before the studio is idle again. Not a
   correctness bug (the payload round-trips), but it makes A16 slow and
   would feel broken to a designer. Candidate fix (not done): coalesce
   the import's writes into one rebuild.

**Round-10 fix found by re-running A16 with strict comparison.** Export
pruned layer pins with an exact-case keep-set (`WGHT` vs the source's
`wght`), and because the import then wrote the pruned locations back,
one export→import cycle moved every brace layer that pinned a
traditional axis. On Crispy that was 55 of 133 layers (WGHT 900, WDTH 5,
WDTH 220, …). Fixed in `_portable_control_axes` with a case-insensitive
keep-set and `test_export_keeps_pins_on_source_axes_that_differ_in_case`.
Pins on axes the source genuinely lacks (SPAC/GRAD/ROND) are still
pruned — `control_axes/shadow_designspace.py` ignores them at build time
(`if name is None: continue`), so those are inert either way.

## Verifier review of rounds 9–10 (2026-10-20, head `9c4ee68` + the uncommitted fixes)

Reviewed against the committed code, the pre-sprint baseline `4e4313b`
and the uncommitted diff in the worktree; ran the four new test files
(20 passed), `ruff check src tests` (clean) and the frontend typecheck
(clean) on the uncommitted state. Verdict per item, then the follow-ups
the agent owes before the next hand-off.

| Item | Root cause verified? | Regression or pre-existing | Fix as proposed |
| --- | --- | --- | --- |
| A6 orange SRC rows | yes — `_sameCoords` key-count compare | pre-existing (`4e4313b` has it) | accepted: compare the subset the CSV carries |
| A7 stale alias → 500 | yes — `_source_instance_name` has no fallback | pre-existing | accepted (save + delete fallbacks, `csv_row_written` sync fix) |
| A8 step validation | yes — `step={0.1}` seeded with float32 defaults | pre-existing | accepted: `step="any"` |
| A10 Transforms menu / G badges gone | yes — provider value omits `transforms`/`grade` | **SPRINT REGRESSION from T3.6 (`cdaebc8`)**: App passed them as props, the context does not carry them. Missed in round 7 (the smoke never opened the menu). | accepted: re-export them |
| A16 self-import rejected + export moved 55/133 layer pins | yes for the case mismatch; the pin-pruning data loss is plausible from the code and **must be shown in evidence** (see below) | pre-existing | accepted |
| A18 unparseable source | **partly**: the degraded build with `source_transform_error` set is BY DESIGN (ARCHITECTURE §5 banner); the real defects are the raw-HTML 500s on every mutating route and that fontc's leniency let a file glyphsLib cannot read build at all | pre-existing | accepted as a pre-flight that fires ONLY when `load_source` fails; the transform-failure fallback must stay |
| A19 `/api/health` 404 on static hosts | yes | pre-existing, by design | marker approach acceptable; it is a product change, so it ships only with the user's nod (alternative: document the 404) |
| A20 / A21 | routine notes | — | routine updated; wasm crate BLOCKED by disk space, not code |

**Follow-ups owed by the agent (each with evidence in
`LOCAL_TEST_RESULTS.md`):**

1. **Commit the fixes** one commit per FAIL with its test, push, and set
   `STATE: checkpoint`; I verify in a fresh clone + live smoke on my own
   copy. Nothing in the shared worktree counts until it is pushed.
2. **A10 guard.** Typecheck passed with the fields missing because
   `Header.tsx` destructures an untyped context with silent defaults
   (`transforms = []`). Type the TransformsContext value (an interface
   in the hook or a `.d.ts`) or drop the defaults so a missing field
   fails `npm run typecheck`; show the typecheck failing before the fix
   and passing after. Re-run the grade scenario and attach the
   Transforms-menu and G-badge screenshots.
3. **A6 evidence is frontend, the named test is Python.** Add a node
   spec for the subset comparison (the `test:unit` harness) or at
   minimum the probe screenshot of a pristine SRC row showing green.
4. **A16 data-loss proof.** Export → import on the Crispy copy with no
   edits in between; diff `Crispy-control.json` before/after (layer
   locations identical, 133 of 133 pins kept). Record the four
   sequential rebuilds as a backlog item, not a fix.
5. **A18.** (a) Name the test that keeps the transform-failure fallback
   (banner, degraded font) green. (b) Measure the pre-flight's cost on
   Crispy (one extra glyphsLib load per build); use the cached loader if
   its invalidation covers the watcher path. (c) Re-run with a
   corruption fontc also rejects (truncate inside a glyph), and record
   `last_build_status`, `last_build_error`, `source_transform_error`, the
   served font size and a mutating request's status before and after
   restore. (d) Every mutating route must answer JSON, never an HTML
   500, while the source is unreadable.
6. **A19.** Serving `index.html` through `Response` drops `send_file`'s
   conditional/ETag handling — confirm that is acceptable or set the
   headers. Evidence: the static scenario's request log shows no
   `/api/health` at all; the desktop path shows the marker and no probe;
   `prod-smoke.mjs` and the static e2e still pass.
7. **A21.** Re-run `wasm/fontc-web cargo test` with
   `CARGO_TARGET_DIR=~/Documents/avar2-studio/wasm/fontc-web/target` (the
   warm target; a cold build is what filled the disk). The 13 `ruff`
   errors outside `src tests` stay pre-existing and out of scope.
8. **Full Part A re-run** after the fixes, not only the failing
   scenarios, at idle (`building: false` and `build_job: null`).

**For the user, not the agent:** (1) new finding 1 — a 1 % parametric
nudge on the ORIGIN row pushes the preview relocation over the gvar
header limit on Crispy, so the ORIGIN row is in practice pinned to the
compiled master; options are a UI note with a revert affordance, a
twin-free relocation (drop slant/rounding twins during relocation) or
the inserted-master route ADR 0007 rejected; (2) new finding 2 — a
config import queues four full rebuilds (~7 min); coalescing is a
job-queue change; (3) whether the A19 marker ships; (4) the machine was
at 100 % disk during A21 (3.2 GB free at review time).

## Approved next scope — user decisions of 2026-10-20 (verifier-written; the agent picks up from here)

**Decisions recorded.** (1) Committing the round-10 fixes is approved:
one commit per FAIL with its test, pushed, `STATE: checkpoint`; the
eight follow-ups in the verifier review above are part of that
hand-off. (2) The A19 backend marker ships. (3) Import rebuilds are
fixed at the trigger, not the queue. (4) The ORIGIN-row relocation
stays as designed (ADR 0001); the failing case gets cheap and
non-destructive — see S1, which revises the verifier's first draft of
this recommendation after checking the Preview tab's wiring. (5) Disk:
test clones go, the warm Rust target stays.

Aim for every item: identical behaviour to what is planned, less time
or load. Order below is the order of work. Each item names the
smallest change and the evidence that closes it (into
`LOCAL_TEST_RESULTS.md`, with commands/logs/screenshots as before).

### S0 — land round 10
Commit per FAIL (A6, A7, A8, A10, A16, A18, A19) with tests, push,
checkpoint. Follow-ups 1–8 of the verifier review are due in the same
hand-off. Full Part A re-run at idle.

### S1 — ORIGIN-row relocation: keep it, bound its cost, never degrade to the 7-axis font
Why not "skip relocation on the preview": `PreviewTab.jsx` pins
non-overridden parametric axes at their defaults and drives the user
axes, so it consumes the font's own avar2 table — relocation is what
makes the neighbourhood of the default inputs show the ORIGIN row
(ADR 0001). Instance rows pin the parametric location explicitly and
never depend on it. So:
- (a) **Budget pre-check before the instancer.** Before
  `instantiateVariableFont`, estimate the post-relocation header bytes
  per glyph from the current tuple set and the moved axes (each tuple
  splits across every axis whose default moves). If the worst glyph
  cannot fit even after pruning the ≤2 u tuples, raise the existing
  readable error WITHOUT running the instancer. Evidence: the round-10
  nudge (`Default 400 MaxOpsz` XOPQ 202.5 → 191.2) — wall-clock of the
  failing build before/after (log timestamps), same message text.
- (b) **Failure keeps the avar2 font.** When relocation is impossible,
  serve the un-relocated avar2 build (the existing
  `relocate_at_build=False` path: all axes, avar2 table present) instead
  of the plain fallback, and report it in health as
  `origin_relocation_error` with the message. Evidence: after the nudge,
  `/api/axes` still lists 13 axes, the parametric sliders stay, health
  carries the error, the amber banner names it.
- (c) **Preview tab under (b):** when health carries
  `origin_relocation_error`, pin the parametric axes to
  `/api/mapped-location`'s values for the current inputs (the oracle the
  tab already fetches) with the user axes at their defaults — the same
  technique the rows use — and show a one-line notice ("resting state
  is approximated by the mapping until the ORIGIN row is back on a
  master; exports will say the same"). Evidence: with the nudge in
  place, the Preview canvas at three input locations matches a
  fontTools instancing of the export-at-that-location within 3 u
  (drift probe), screenshot of the notice.
- (d) **ORIGIN row affordance (small):** the ORIGIN badge tooltip says
  whether the row's out sits on a compiled master and, if not, by how
  much per axis. No snap action yet (user to decide after seeing it).
ADR 0001 gets an amendment paragraph for (b)/(c); no new ADR.

### S2 — one build per import (and per broadcast)
The job queue coalesces already; the extra builds come from
`watch.py` reacting to the shadow/transformed copies the studio itself
regenerates during an import (`_resolve_active_source`) and after
sidecar writes. Stamp studio-written shadow/transformed files as "ours"
exactly as `_update_csv_modification_time` does for the CSV, and give
the watcher a short settle window (≈1 s) so a burst of sidecar writes
yields one request. Evidence: config import → exactly one
`[jobs] build-… started` in the log; multi-select LCWD broadcast over 3
rows then Preview open → exactly one build; A17 (external Glyphs save →
exactly one build) still passes.

### S3 — A19 marker, shipped properly
Keep the uncommitted change; serve the marked `index.html` with
`Cache-Control: no-store` (better for bundle updates than the dropped
ETag); keep the Vite-dev probe. Evidence: follow-up 6 of the review
(static scenario shows no `/api/health`; desktop shows the marker and
no probe; `prod-smoke.mjs` + static e2e pass) plus a request-timing
line showing the desktop first paint no longer waits on the probe.

### S4 — stop re-parsing the source per request
Seven route call sites parse the `.glyphs` on every request with
`load_source` (`routes/instances.py` ×4 incl. the auto-save PUT,
`routes/control_axes.py` ×2, `routes/load.py`); the mtime-keyed
`load_source_cached` exists for read-only callers. Switch every
read-only site (and the A18 pre-flight) to the cached loader; any site
that mutates the returned font object keeps the uncached load (say
which). Evidence: `PUT /api/instance/<row>?csv_only=true` latency
before/after from the log timestamps (request start → `Saved … in the
studio`), on Crispy; the full pytest still green.

### S5 — idle means no build AND no compress
Frontend: the WOFF2 preview fetch and the "rebuild once when Preview
opens stale" effect wait for `build_job == null`, not only
`building == false`, so a page load or slider settle during the
compress job neither logs 409s nor queues behind it. Probe: same rule.
Evidence: `console-clean` run immediately after a build passes (no
409s); the A5 scenario timing unchanged.

### S6 — disk
The verifier deletes its own `verify-r5`/`verify-r6` clones (1.4 GB).
The agent deletes `/private/tmp/av2-wheel-test`, `av2-pw`,
`av2-local-test` (its own, ~0.5 GB) and keeps `av2-routine`. Nobody
touches `~/Documents/avar2-studio/wasm/fontc-web/target` (9.9 GB, the
warm target that makes `cargo test` cheap); run that crate's tests with
`CARGO_TARGET_DIR` pointed at it (follow-up 7).

### S7 — optional, cosmetic
The origin warning text prints `-1000` for control axes (placeholder
ranges, no `default`); print their sidecar defaults or omit control
axes from the list. Pre-existing; take it only if it falls out of S1(d).

Out of scope until the user says otherwise: a snap-to-master action,
twin-free preview relocation, the 13 `ruff` errors outside `src tests`,
and anything in Part B.

## Round 11 — S0 hand-off: round-10 fixes landed, review follow-ups closed (2026-10-20)

Round 10's fixes are committed one FAIL at a time (each with its test) and
pushed; this section is the hand-off note for the verifier's checkpoint
review. Evidence lives in `LOCAL_TEST_RESULTS.md`; the scope after this is
S1–S6 from "Approved next scope" above.

**What changed since the verifier review** (all on top of the review's own
commit `364ae67`):

| Review follow-up | Outcome |
| --- | --- |
| 1. commit per FAIL + push + checkpoint | done: see the commit list in `LOCAL_TEST_RESULTS.md § S0` |
| 2. A10 guard | **typecheck cannot do it**: `tsconfig.json` sets `allowJs` but not `checkJs` and there is no `.d.ts` for the hook, so `src/**/*.jsx` is parsed but never checked — a missing context field is invisible to `tsc`. Guard is a new `test:unit` spec, `e2e/transforms-context.spec.mjs`, which reads the provider's `value` keys and every consumer's `useTransforms()` destructure and fails when a consumer reads what the provider does not expose. Proved to bite: deleting `transforms,`/`grade,` from the provider makes it report 5 failures (2 named + the 3 consumer assertions). Grade scenario + screenshots re-run. |
| 3. A6 node spec | extracted the comparison to `src/instance-sync.js` (`sameCoords`, `csvDivergesFromSource`) and added `e2e/instance-sync.spec.mjs` (9 assertions incl. the float32-noise case and the "no comparable axis" case); both specs are wired into `npm run test:unit`. |
| 4. A16 data-loss proof | export no longer prunes layer pins at all (`_portable_control_axes` deleted); validation downgrades a pin on an axis the target lacks from error to warning, because the pin is the only record of where the layer was authored and `control_axes/shadow_designspace.py` ignores such axes at build time. Measured on the Crispy copy: **133 layers / 697 pins before and after** one export→import, `layer/axis payload changed: []`, and on an already-canonical sidecar the byte diff is `[]` too. |
| 5. A18 | (a) new `test_a_readable_source_that_misses_the_mapping_still_builds` pins the degraded-fallback (plain font published, `LAST_AVAR2_ERROR` set, gate open) so the pre-flight cannot swallow it; (b) the pre-flight now uses `load_source_cached` (mtime+size keyed, so a saved/edited source still re-reads); (c) truncation corruption evidence in `LOCAL_TEST_RESULTS.md § A18`; (d) every mutating route answers **JSON** (`409` + `build_blocked`) while unreadable — verified by a live PUT, not only by the unit test. |
| 6. A19 | marker ships, and the index is served with `Cache-Control: no-store` (S3) instead of the conditional/ETag handling `send_file` provided — the index names hashed assets, so revalidation is the wrong default. |
| 7. A21 | `wasm/fontc-web cargo test` with `CARGO_TARGET_DIR` pointed at the warm target: compiles and all test binaries pass (6 tests across the targets, 0 failures); the earlier BLOCKED was the cold-build disk, not code. Disk after S6 clean-up: 5.1 GiB free. |
| 8. full Part A re-run at idle | idle is now enforced before every scenario (`building: false` **and** no `build_job`), plus a header-interactive wait before the menu-driven ones. Results table in `LOCAL_TEST_RESULTS.md`. |

**Three harness defects found while re-running** (all fixed in `ui_probe.py`,
none a product defect): a scenario could start while a build from the
previous one was in flight — the `.build-veil` intercepts the Download
click and the header menus are `disabled={busy}`, which read as failures;
the first `export` failure in the round-10 run was the ORIGIN-row nudge
from A7 (now ordered before A7); and opening the Preview tab on a stale
build makes the app itself post `/api/build`, so `export` has to wait for
*that* build too (A6's source save leaves the build stale) — the veil then
eats the Download click even though the scenario started at idle.

**New observation for the backlog (not in S1–S6):** restoring a corrupt
source with an *older* mtime (e.g. `cp -p` from a backup) is not picked up
by the watcher — it compares mtimes and only reacts to newer ones, so the
gate stays closed until the file is touched. `git checkout` and every
editor save set mtime=now, so the practical exposure is low, but it is the
one way "restore the file" does not self-heal.

## Verifier review of round 11 — S0 checkpoint `329d181` VERIFIED (2026-10-20)

Independent of the agent's environment: fresh clone of `329d181`
(scratchpad `verify-r11/repo`, own venv, `pip install -e .[dev]`), live
smoke from that clone on port 5071 against a copy staged per the
corrected routine (`crispy-r11`, authored `.avar2-studio` state, isolated
`HOME`), static demo built from the clone with the wasm binary and the
snapshotted examples on 5075.

**Gates (fresh clone).** pytest 545 passed, 2 skipped (with the two
accelerator env vars; the agent's 547 counts the two skips as passes on
its machine — not material); `ruff check src tests` clean; frontend lint
0 errors, typecheck clean, `test:unit` all passed including
`transforms-context` and `instance-sync`; `npm run build` OK;
`native/kinetikern2` 53 passed; `wasm/fontc-web` 6 passed on the warm
target.

**Live smoke (my copy, the clone's server).** Staging gate: origin
warning null, OPSZ default 144. `ui_probe.py` scenarios first-load,
select-no-save, sync-dots, flyout-source (source PUT 200), lifecycle
(create no longer blocked), grade (Transforms menu + G badges present —
screenshot checked), multi-broadcast: all PASS. My own A16 script:
export → dry-run (0 errors, 0 warnings) → apply → all four sidecars
byte-identical; control sidecar 2 axes / 133 layers / 697 pins before and
after. My own A18 script: truncate the source inside a glyph → health
`last_build_status failed` with the readable parse message and
`source_transform_error` set; `PUT` → `409 application/json` with
`build_blocked`; `/api/font` still 11,034,988 B (last-good kept);
restore with mtime=now → one build → `PUT 200`. Static demo: 8 rows,
wght slider drives `font-variation-settings`, **zero `/api/*` requests,
zero console errors**.

**Verdict: every S0 item VERIFIED.** `VERIFIED_HEAD: 329d181`. A10's
regression is closed with a guard that provably bites.

**One claim not reproduced — re-scopes S1.** Round 10 reported that the
1 % ORIGIN-row nudge (`Default 400 MaxOpsz` XOPQ 202.5 → 191.2) makes
the *preview* build fail on glyph `g` (1,767 tuples). On my copy the
same nudge (made by the flyout-source scenario) relocated the preview
font — fvar XOPQ default is 191.2 in the built TTF — and four
subsequent builds succeeded; `g` has 1,471 tuples, `B` 1,386. The
agent's surviving logs contain only the set-default *export* failures
(`B`, 2,927 tuples at wght 921.8/605.8), which are the documented
limit. The likely difference is the extra regions the agent's copy had
accumulated by then (the A11 control axis `TST2` with brace layers, a
Kinetikern entry, row grades) — each adds tuples that the relocation
then splits. So the ORIGIN row is NOT pinned on a pristine Crispy; it
becomes fragile as studio-authored axes and braces accumulate.

S1 is re-ordered accordingly: **(a) budget pre-check and (b) keep the
avar2 font on failure stay approved** (they are what makes the
accumulating case safe and cheap); **(c) the oracle-pinned Preview and
(d) the badge tooltip wait** until the agent supplies a reproducible
recipe (which axes/braces/grades present, build log, tuple counts) on a
copy staged per the routine. Add that recipe to `LOCAL_TEST_RESULTS.md`
as the first S1 deliverable.

**S2 evidence from this run:** one config import produced builds
`…-7/-9/-11/-13/-15` (five, ~40–48 s each plus compress) on my copy —
the watcher self-trigger is confirmed and S2 stays next after S1(a)/(b).

**Backlog noted:** the watcher ignores an older mtime on restore
(`cp -p`); agreed low exposure, keep as backlog.

## Verifier review of the S1 recipe (2026-10-20) — reproduced; S1(c)(d) unparked

Ran `s1_recipe.py` (head `9b83fc0`) myself from the fresh clone on a
newly staged copy (`crispy-r11b`, port 5071). Same numbers as the agent:

| stage | status / avar2_error | axes | font | `g` | `B` |
| --- | --- | --- | --- | --- | --- |
| pristine | ok / None | 12 | 10,472,360 B | 1,476 | 1,328 |
| + control axis `TST2` + reseeded braces | ok / None | 13 | 10,790,892 B | 1,332 | 1,281 |
| + 1 % ORIGIN nudge (XOPQ 202.5 → 187.89) | **ok** / "glyph 'g' … 1767 tuples; dropping the 425 sub-visible ones was not enough" | **10** | **586,304 B** | 85 | 77 |
| + second nudge (→ 173.28) | same | 10 | 585,884 B | 85 | 77 |

**Recipe VERIFIED.** The round-11 review's "not reproduced" stands for a
pristine copy and is withdrawn for the studio-authored case: one control
axis with seeded brace layers is enough. S1(c) and S1(d) are unparked;
the full S1 (a)–(d) is the next checkpoint, in that order. S1(b)'s bar is
set by this table: after the fix the nudge must leave 13 axes and the
avar2 table in the served font, `last_build_status` must not read `ok`
while the mapped build failed (today only `avar2_error` tells), and the
budget pre-check must short-circuit before the instancer runs.

**Two side findings from the recipe run (agent to close in the S1
hand-off):**

1. **Unknown `/api/*` requests fall into the Fontra proxy.** The recipe
   POSTed to `/api/instances/<row>/grade` and
   `/api/kinetikern/instances/<row>` (both are `PUT` routes); instead of
   `405`/`404`, the catch-all `'/api/<path:rest>'` in `routes/fontra.py`
   answered `503 {"error": "Fontra subprocess is not running"}`. A recipe
   bug (use `PUT`; fix the script and re-run so grade + Kinetikern state
   is part of the recipe as intended), and a product wart: the catch-all
   should proxy only Fontra's own paths and let everything else 404/405
   with a JSON body. Smallest change: narrow the catch-all's prefix or
   return 404 when the path is not a Fontra route. Evidence: the two
   recipe calls answering `405`/`404`, and the Fontra editor still working
   (A11).
2. **The recipe's baseline font differs in size run to run**
   (10,533,616 B agent vs 10,472,360 B here, same source bytes) — most
   likely the SPAC injector's pruning or table ordering, not a defect, but
   note the cause in the results so size comparisons in A12 have a
   tolerance.

## Verifier call on S1(a) (2026-10-20) — measurement accepted; S1(a) becomes a relocation memo

The agent's measurement (`s1_estimate_probe.py`, both calibration fonts)
settles it: the instancer is ~27 s of a ~45 s build, the split count is
not predictable from a cheap rule, and the pre-prune count would refuse
builds that succeed after `_fit_gvar_headers` prunes (pristine `g`:
2,036 pre-prune → 1,467 served). A pre-instancer budget gate is
withdrawn. Order and shape of S1 from here:

1. **S1(b) first** — on relocation failure serve the un-relocated avar2
   build (13 axes, avar2 table, ~1,332-tuple gvar) and report it as a
   failed mapped build (`last_build_status` not `ok`; health carries the
   relocation error). Bar as set by the recipe table.
2. **S1(a′) relocation memo, success AND failure.** The relocation's
   inputs are the fontc-compiled font and the target; the compiled font
   changes only when the staged source/shadow changes, while most studio
   edits (every sidebar/CSV save, grade, Kinetikern) change only what
   comes after it. Key the memo on sha256 of the compiled TTF bytes +
   the target dict + `GVAR_PRUNE_TOL`; store the relocated TTF (or the
   error) under the build dir; on hit, skip the instancer and the
   header-fit. Evidence: (i) a CSV-only edit after a successful
   relocation → log shows the hit and the build's wall-clock drops by
   ~27 s on Crispy (record both numbers); (ii) the recipe's second
   nudge after a failure (same target) → the failure is answered from
   the memo in <1 s instead of 27 s; (iii) a source edit invalidates the
   memo (miss logged); (iv) exports use the same memo when their target
   matches (set-default at the ORIGIN row) and otherwise compute as
   today. Same output bytes as an uncached run (hash compare) is the
   acceptance.
3. **S1(c)** oracle-pinned Preview under the relocation error, then
   **S1(d)** the ORIGIN badge tooltip, as written.
4. The pruned-aware budget model is **not** scheduled; it only pays if
   the memo leaves a measurable failing case, which the recipe will show.

This keeps the designed behaviour (the preview rests at the ORIGIN row
whenever relocation fits) and removes the 27 s from every build that
did not change the compiled font — the common case.

## Verifier early note on S1(b) + the catch-all (2026-10-20, commits `18c5d82`, `5a59d1b`) — read-only review, not a checkpoint

Read the diffs; no live run yet (that comes with the S1 checkpoint).

- **`degraded` as the status value is accepted.** It meets the bar
  ("not `ok` while the mapped build failed") and keeps the build gate
  open so the ORIGIN row can be put back — better than `failed`. The
  frontend only special-cases `failed`, so nothing hangs on it (checked
  `useBuildStatus`, `StatusBanners`, `build-wait`).
- **But the degraded state is invisible today.** `StatusBanners.jsx`
  renders banners for `failed`, the origin warning, the transform error
  and the avar2 error; there is none for `origin_relocation_error`, so a
  designer sees sliders at the compiled origin with no explanation
  until S1(c) lands. Add the banner in the S1 hand-off (message + "revert
  the ORIGIN row's parametric values to clear it"), and list the new
  status in `routes/health.py`'s docstring and ARCHITECTURE §5.
- **Exports raising on a failed relocation is right** (a set-default
  export must not ship a different resting style silently).
- **Probe:** `sidebar-edit-saves` asserts `last_build_status == "ok"`;
  keep that strict there, and let only the recipe/S1 scenarios expect
  `degraded`.
- **Catch-all:** deriving the studio namespaces from the URL map is the
  right source of truth; it is recomputed per request — compute it once
  (the map is frozen after the first request) so a 404 path costs
  nothing. `test_fontra_catchall.py` covers the 404/405 cases; add the
  positive case (a Fontra-owned `/api/...` path still proxies) so the
  editor cannot regress silently.

## Verifier note on the S1(b) live evidence (2026-10-20, `a89f7a7`/`fcf4122`) — one number does not add up

The S1(b) table reports the served font after the nudge as **13 axes,
792,256 B, `g` 86 tuples**, against 10,894,284 B and 1,333 tuples one
build earlier. The S1(b) bar was "the un-relocated avar2 build: all
axes, avar2 table, **~1,332-tuple gvar**". A 792 KB font cannot carry
the slant/rounding twin deltas (7,597 twin layers), so what is being
served is not the un-relocated compile — it is a font with the axes and
the avar2 table but almost no variation data, i.e. slnt/ROND/LCWD/YMOD
present and inert and every parametric region flattened. For the
Preview that is as wrong as the 10-axis fallback, only quieter.

Before S1(a′)/(c) build on it, the agent must establish which bytes end
up at `out` when the instancer raises: the diff closes the mutated
in-memory font and saves nothing, so the on-disk `out` should still be
fontc's full compile unless something earlier in `_build_in_process`
(the fix_font save/re-read, or a prior pass) rewrote it. Evidence
required in the results: for the degraded build, `fontTools` tuple
counts for `g`/`B`/`e` and the file size of (i) fontc's output, (ii)
`out` after the failed relocation, (iii) the served font — all three
should match the pre-nudge 1,333-tuple build within the SPAC injector's
pruning. If they do not, S1(b) is not done. Also add that check to
`test_a_relocation_that_cannot_fit_keeps_the_avar2_font` (tuple count of
one heavy glyph equal to the un-relocated compile, not just "has avar").

The worker-boundary fix (relocation error crossing the subprocess) and
the catch-all cache/positive test are accepted as described.

**Executor answer (2026-10-20, `7d3d74e`) — relocation inflates gvar; the
792 KB font is the compile it could not fit.** Measured on the same staged
source: fontc alone 608,820 B / 7,701 tuples / `g` 83; the failed build's
`out` 792,296 B / 8,304 / 86 (the +603 tuples are the avar2 step's created
axes — `SPAC` alone participates in 408); `/api/font` is **sha256-equal** to
that `out`. The 10,533,616 B / 124,370-tuple font is the *relocated* one
(`exports/default.ttf` is another copy of it). Instancing re-expresses every
delta against the new default, so a feasible 1-unit XOPQ relocation already
takes `g` 86 → 213 and the total to 17,900 — at the 1 % target the instancer
saw 1,767 against the successful build's 1,476. The twins are live in the
served font, not inert: `slnt` 3,413 and `ROND` 3,316 tuple participations
(plus `XOPQ` 6,313, `YOPQ` 6,081, `XTRA` 5,629, `GRAD` 3,065, `LCWD` 223,
`SPAC` 408). And the table's pre-nudge row came from the *earlier* run: in
this run the pre-nudge build was 10,533,616 B / 124,370 / 12 axes, because
`TST2` arrived with the recipe's accumulate step — the cross-run comparison
is what made two different fonts look like one collapsed one.

The test now asserts what the review asked for and more: the failed build's
output must be **byte-identical** to the same config built with
`relocate_default=False`, with equal tuple dicts and a non-empty gvar.

## Verifier: gvar-collapse note withdrawn (2026-10-20, `7d3d74e`/`a8316a6`) — S1(b) evidence accepted

The agent's measurement is right and my premise was wrong: I compared
the degraded font against the previous run's *relocated* font. fontc's
own compile of this source is 608,820 B with `g` at 83 tuples; the
avar2 step adds the created axes (792,296 B, 86 tuples); relocation is
what inflates gvar ~15× to the 10.5 MB / 124,370-tuple font. The served
degraded font is sha256-equal to the un-relocated `out`, the twins are
live in it (slnt 3,413 and ROND 3,316 tuple participations), and the
test now pins byte-identity with a `relocate_default=False` build. S1(b)
is accepted on this evidence, pending the fresh-clone run at the
checkpoint.

Two consequences to carry forward:

- **S1(a′) memo stores the relocated font**, which is the expensive
  artifact (≈10 MB per entry, 27 s to make). Keep the latest two entries
  per build dir and evict the rest; the key stays (compiled-font sha256,
  target, `GVAR_PRUNE_TOL`). On a hit the build skips the instancer,
  the header-fit AND the inflation, so the win is the whole 27 s.
- **Docs:** ARCHITECTURE's "relocation densifies gvar ~7×" should
  quote the measured Crispy figure (~15× with slant/rounding twins) and
  say that the un-relocated compile is the small one — the next reader
  will make my mistake otherwise.

## Verifier early note on S1(a′) (2026-10-20, `f4a3e91`) — design accepted; one hazard, one proof to add

Read the diff and the three tests. The key (content digest without
`head`, exact per-axis window, prune tolerance, format version), the
atomic store of successes and failures, the keep-2 eviction, the memo
dir surviving the worker's job dir, and `origin_relocation_memo` in
health are all as scoped. Two items before the checkpoint:

1. **Hazard: the plain-fallback picker can now return a memo file.**
   `build_variable_font(state.GLYPHS_PATH, state.BUILD_DIR, …)` ends
   with `output_dir.rglob("*.ttf")` and returns `variable_fonts[0]`
   (unordered). The memo lives at `<build>/.relocation-memo/<key>.ttf`
   — a relocated avar2 font from an earlier compile — inside that tree.
   Whenever the fallback runs in the stable build dir (in-process builds,
   tests, any path where `BUILD_DIR` is not a throwaway job dir), the
   fallback can hand back a cached relocated font as "the plain build".
   Make every picker non-recursive or exclude `RELOCATION_MEMO_DIRNAME`
   (`pipeline.py` ~1269 is the recursive one; `server.py` 509/514,
   `routes/build.py` 190 and `build_jobs.py` 66 are flat and fine), and
   add a test: with a memo entry present, the fallback returns the
   fontmake/fontc output, never the memo file.
2. **Proof that the memo sits before gen-avar2.** The store happens
   right after the instancer, so a mapping-only edit must hit the memo
   AND still regenerate the avar2 table. Add that to
   `test_a_relocation_is_memoised_and_reused`: change one CSV `out`
   value between the two builds → `memo == "hit"` and the second font's
   avar2 mapping for that row differs from the first's. Live evidence
   at the checkpoint: a sidebar save on Crispy → health shows
   `origin_relocation_memo: "hit"` and the build's wall-clock versus the
   previous miss (expect ≈27 s less).

## Verifier corrections + early note on `f24fc1c` (2026-10-20)

**Two of my round-11 claims are withdrawn; the agent's measurements stand.**

1. **A16 was not verified by me.** My round-11 script posted the raw
   export bundle to `/api/config/import`; the route wants
   `{bundle, dry_run}` and answered **400** to both calls (my log, lines
   1266–1267). The script read the error body as "0 errors" and the
   "all four sidecars byte-identical" result was vacuous — nothing had
   been imported. The agent's A16 evidence (133 layers / 697 pins,
   payload diff `[]`) is therefore the only A16 evidence so far; I re-run
   A16 with the corrected script at the checkpoint (`dry_run` defaults
   to true, so the apply call must say `false`).
2. **"Five builds per import" is withdrawn.** Builds `-7…-15` on my log
   sit between the lifecycle rename, the grade scenario and the
   multi-select broadcast, not after the import (which never applied).
   The agent's measurement — one import, one build — stands; S2 is
   reduced to the multi-select-broadcast half, to be measured, and fixed
   only if it shows more than one build.

Lesson applied to my scripts: every API call now prints its HTTP status
and fails the check on anything but 200.

**Read-review of `f24fc1c` (S4, S5, S1(c), S1(d)):**

- **S4 accepted**: six read-only sites on the cached loader; PUT
  latency 2,951 → 6.5 ms median is the kind of number S4 was for. The
  stat-fallthrough keeping the unreadable-source error identical on
  both paths is right.
- **S5 accepted** (font registration and the stale-Preview rebuild wait
  for `build_job == null`).
- **S1(c): one gap.** `renderCoords` pins every parametric axis to the
  oracle under `origin_relocation_error`, including axes the designer
  has explicitly overridden (`overrides` set). Keep an overridden axis
  at its slider value: `pinned[a.tag] = overrides.has(a.tag) ?
  effectiveCoords[a.tag] : oracle`. With the user axes held at their
  defaults the avar2 delta is zero, so the override renders exactly —
  this is the one mode where a direct parametric input is exact.
- **S1(d) accepted**; `MASTER_TOLERANCE = 1.0` matches
  `_fit_gvar_headers`' notion of sub-visible.
- **Still open from the S1(a′) note:** the recursive fallback picker vs
  the memo dir, and the memo-before-gen-avar2 test.

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
