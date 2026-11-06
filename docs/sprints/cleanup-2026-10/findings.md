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

## Verifier review of round 12 — S1 checkpoint `1555212` (2026-10-21): all VERIFIED except S1(c)

Fresh clone of `1555212` (`verify-r12/repo`, own venv), live smoke from
it on port 5071 against copies staged per the routine (`crispy-r12`,
`-r12b`, `-r12c`), static demo built from the clone on 5075.

**Gates.** pytest 554 passed, 2 skipped; `ruff check src tests` clean;
lint 0 errors; typecheck; `test:unit` all passed (incl.
`transforms-context`, `preview-coords`); build OK; kinetikern2 53
passed; fontc-web all 18 test binaries ok.

| Item | Verdict | Evidence (verifier's own run) |
| --- | --- | --- |
| A19 static demo | VERIFIED | 8 rows, wght 100 → 753.1 drives `font-variation-settings`, **0** `/api/*` requests, **0** console errors |
| A16 config round-trip | VERIFIED | corrected script: export 200, dry-run 200 and apply 200 (`ok: true`, 0 errors, 196 warnings), all four sidecars **byte-identical**, 133 layers / 697 pins |
| S2 one build per import | VERIFIED | the import added exactly one build to the log |
| S1(a′) relocation memo | VERIFIED | ORIGIN row nudged: build `miss` 42.9 s, then compile-unchanged builds `hit` 19.3 / 19.3 / 19.4 s (≈23 s saved each); after A18's restore the rebuild hit the memo too (18.8 s) — the content key survives a restore |
| A18 gate | VERIFIED | truncation → builds fail in 0.13 s, restore → one build `ok` |
| S1(b) degraded keeps the avar2 font | VERIFIED | recipe: `degraded`, 14 axes kept through two nudges, `origin_relocation_error` set |
| S1(d), S4, S5, catch-all | VERIFIED | probe scenarios + console-clean PASS after the env fix below |
| A11 open-editor | NOT VERIFIABLE in a fresh venv | see finding 2; the agent's PASS (Fontra installed) stands |
| **S1(c) degraded Preview** | **FAILED at the resting state** | finding 1 |

**Finding 1 — S1(c) shows the compiled origin exactly where it should
show the ORIGIN row.** `degraded_preview.py` (scratchpad): degraded
build, Preview at the ORIGIN row's own inputs (opsz 144, wdth 100,
wght 400). Rendered `font-variation-settings`: `XOPQ 1, XTRA 47,
YOPQ 1` (the fvar defaults) against the row's out `XOPQ 161.98, XTRA
663.1, YOPQ 121.8` — off by 161/616/121 units; the notice renders.
Root cause: `/api/mapped-location` evaluates the **built font's** avar
table (`routes/avar2.py`, docstring: "Evaluate the BUILT font's avar
table"). In a degraded build that table was generated around the
compiled origin, and avar2 cannot remap the default location — the
reason relocation exists (ADR 0001). So the oracle returns the compiled
origin at the default inputs and a mapping anchored on the wrong point
around it. The agent's drift probe compared the canvas with the oracle
the tab fetched (self-consistent by construction) at wght 1/400/1000
with auto-opsz on, so it never sat on the default location and never
compared against the authored row.

Required fix (smallest that keeps the planned behaviour): under
`origin_relocation_error`, the oracle must evaluate the **authored**
mapping as if the default had been relocated. The data is all there and
none of it needs gvar: take the built font's `fvar`, move the parametric
defaults to the ORIGIN row's out, run the same gen-avar2 step on that
`fvar`-only font, and evaluate `mapped-location` against the resulting
avar table (cache it per build; it costs milliseconds). Rendering the
pinned absolute parametric values on the un-relocated gvar is then
exact, because gvar interpolates absolute locations regardless of where
the default sits. Acceptance: (i) the verifier's `degraded_preview.py`
— at the ORIGIN inputs the applied parametric values equal the row's
out within 1 u; (ii) the drift probe redone against the **authored**
rows, not the oracle: at the default location and at two mapped stops,
canvas vs fontTools instancing of the served font at the authored out
≤ 3 u; (iii) a unit test on the oracle in degraded mode.

**Finding 2 — a fresh install's open-editor says "fontra exited
immediately (exit code 1)".** Fontra is optional (not a dependency;
`doctor` checks `find_spec("fontra")`), so in a clean venv
`POST /api/control-axes/<tag>/open-editor` answers 500 with a message
that hides the cause. Pre-existing, minor: answer with the doctor's
diagnosis instead (`Fontra is not installed: pip install fontra
fontra-glyphs`), status 503, before spawning. Test: with `fontra`
unimportable, the route returns that message.

**Harness note (mine).** My first servers this round ran without the
SPAC injector and the Kinetikern engine: zsh expands `$HOME` in a
prefix assignment against the new `HOME`, the same trap the agent
recorded. That produced the kinetikern/console-clean FAILs and the
smaller fonts in the recipe run; re-run with absolute paths, both PASS
(server env checked). The memo timings are relative and stand.

## Verifier note on `4936c8e` (2026-10-21) — the authored oracle drops the avar2 part; S1(c) still FAILED

Read-review plus one measurement. The open-editor fix (503 with the pip
line, nothing spawned) is **accepted**.

The S1(c) oracle is not. `_authored_mapping_table` runs gen-avar2 on the
`fvar`-only font correctly, then serialises only `avar.segments` — the
avar **1** per-axis segment maps — and `_evaluate_authored_mapped_location`
evaluates only those. The mapping that moves the parametric axes with
wght/wdth/opsz lives in the avar **2** `VarStore`, which is dropped.
Measured on a built Crispy font from this checkpoint:

```
avar majorVersion: 2
parametric axes with a non-identity segment map: []      (XOPQ/XTRA/YOPQ all identity)
avar2 VarStore present: True | regions: 28
full avar2 at wght 900: XOPQ normalized 0.0903 | segments-only: 0.0
```

So the degraded oracle returns the ORIGIN row's out at the ORIGIN inputs
(which is why the new test passes) and **the same out at every other
input**: the Preview would render the ORIGIN style at wght 1 and wght
1000 alike. The new unit test encodes this — at wght 700 it asserts
`XTRA == 663.1`, i.e. that the parametric axis does not move — and it
uses a hand-written segment table rather than a gen-avar2 one, so it
cannot see the VarStore.

Required: keep the whole avar table. Smallest change: write the
`fvar`+`avar` of the authored build as a tiny TTF (or the compiled table
bytes) next to the memo entry, and evaluate it exactly as
`_evaluate_mapped_location` already evaluates the built font
(`avar.renormalizeLocation`), so both paths share one evaluator.
Acceptance, all on Crispy in a degraded build: (i) at the ORIGIN inputs
the oracle returns the row's out within 1 u; (ii) at two other authored
rows' inputs (e.g. `Default 900 MaxOpsz`, `Narrow 1 MinOpsz`) it returns
those rows' out within 1 u; (iii) the unit test built from a real
gen-avar2 table, asserting a parametric axis moves with wght; (iv) the
verifier's `degraded_preview.py` at the ORIGIN inputs and the drift
probe against authored rows ≤ 3 u.

## Verifier live check of `2f51aad` (2026-10-21) — S1(c) still FAILS end to end; cause is the inputs, not the table

Fresh clone of `2f51aad` (`verify-r13`): pytest 559 passed / 2 skipped,
ruff clean, lint 0 errors, typecheck, `test:unit`, build all green. Own
copy staged per the routine, engines confirmed in the server's env, the
recipe reaches `degraded` after the nudge (XOPQ 173.28; error on glyph
`g`). The memo dir holds the `.authored.ttf` sidecars as described.

Acceptance (`s1c_check.py`, verifier scratchpad) — every check FAILS by
the same pattern:

| inputs (row) | oracle XOPQ / XTRA / YOPQ | row out | returned value |
| --- | --- | --- | --- |
| Default 400 MaxOpsz (ORIGIN) | gap −172.28 / −616.1 / −120.8 | 173.28 / 663.1 / 121.8 | **1 / 47 / 1** |
| Default 900 MaxOpsz | gap −296.18 / −642.22 / −144.78 | 297.18 / 689.22 / 145.78 | **1 / 47 / 1** |
| Narrow 1 MinOpsz | gap −11 / −101.9 / −11 | 12 / 148.9 / 12 | **1 / 47 / 1** |
| Preview tab at the ORIGIN inputs (rendered fvs) | same as row 1 | | **1 / 47 / 1**, notice shown |

The oracle returns exactly the parametric axes' **minimums**, at every
input. Root cause: the Preview (and my check, which copies it — the
agent recorded this in round 11) sends **every** axis, with the
non-overridden parametric axes at the **served** font's fvar defaults.
On Crispy those defaults are the axis minimums (XOPQ 1, XTRA 47, YOPQ
1). In the authored model the parametric defaults have moved to the
ORIGIN out, so an explicit `XOPQ=1` there normalises to −1; the avar2
deltas add on top and the result clamps at −1 → the minimum. The
unit tests pass because they send only the user axes, which leaves the
parametric axes at the authored defaults.

Required (server-side, so every caller is right): when evaluating the
authored model, translate each incoming coordinate that equals the
**served** font's default for an axis whose default moved into the
**authored** default before evaluating; any other value (an explicit
override) stays absolute. Add a unit test that sends the full
coordinate dict exactly as `PreviewTab` builds it (every axis, served
defaults) and expects the three rows' outs within 1 u; then the
verifier re-runs `s1c_check.py` live.

## Verifier: S1(c) VERIFIED live on `f08b002` (2026-10-21) — S1 closed

`verify-r13` clone at `f08b002`: pytest 562 passed, ruff clean. Fresh
Crispy copy staged per the routine, engines in the server env, recipe →
`degraded` (glyph `g` over the header limit). `s1c_check.py`:

| check | XOPQ / XTRA / YOPQ gap to the authored row |
| --- | --- |
| oracle at `Default 400 MaxOpsz` (ORIGIN) inputs | 0.0 / 0.0 / 0.0 |
| oracle at `Default 900 MaxOpsz` inputs | −0.02 / −0.11 / 0.0 |
| oracle at `Narrow 1 MinOpsz` inputs | 0.0 / 0.01 / 0.0 |
| Preview tab at the ORIGIN inputs (rendered fvs) | 0.0 / 0.0 / 0.0, notice shown |

All within the 1 u bar. With round 12's results this closes S1 (a′,
b, c, d) and the sprint scope S0–S6: **VERIFIED_HEAD `f08b002`**. Open
for the user only: Part B, pushing fontra-embed `shared-studio-shim`,
merging `cleanup-2026-10` into `github-pages`, and the CI/Pages runs a
push confirms.

## Designer Part B finding (2026-10-21) — two flyout bugs on promote-to-source (agent to fix)

Found by the designer during B2 on the verifier's sanitised studio (port
5071, copy of the current `Crispy.glyphs` with no transforms, grade or
Kinetikern). Row `Default 1 MinOpsz` (studio-only). Server log
evidence: three `PUT /api/instance/Default%201%20MinOpsz?csv_only=true`
auto-saves, the last at 23:17:28 (XOPQ 15 → 16), then
`POST /api/instance/Default%201%20MinOpsz/add-to-source` at 23:17:34.
Afterwards the mappings file, the promoted source instance and the
studio row all agree (XTRA 630.9, XOPQ 16, YOPQ 16, SPAC 51.2), yet the
row's dot stays **red** and the flyout now offers "Save to avar2
studio": the designer's last XTRA and SPAC drags were never written.

**F1 — "Save to source file" on a studio row promotes the last
auto-saved values, not the values on screen.**
`handleAddInstanceToSource` (`hooks/useInstanceEditing.jsx`) calls
`api.addInstanceToSource(name)` without flushing the pending debounced
auto-save (800 ms). The promotion flips the row to `origin: source`,
and the auto-save effect returns early for source rows by design, so
the pending draft is dropped from every save path and can only be
recovered through the flyout's studio save. It also refreshes only
`instances`, not `avar2Instances` or the row's original coordinates.
Fix: before promoting, cancel the auto-save timer and save the current
draft (the same csv-only PUT the timer would have sent), await it, then
promote, then refresh both row caches and reseed the editing baseline
from the refreshed row. Test: a node spec or probe scenario that edits
a studio row and clicks "Save to source file" within the debounce
window → the source instance and the mappings file carry the on-screen
values and the dot is green, not red.

**F2 — SPAC can never reach the source, and the flyout does not say
so.** SPAC is a mapping-only value (a column in the mappings file; no
axis in the `.glyphs`). A source save therefore cannot carry it, so any
SPAC draft on a source row stays red after "Save to source file". Fix:
the source save also writes the row's SPAC (and any other
mapping-only values) to the studio in the same action, and the flyout
copy says that SPAC is saved in the studio, not the source file. Test:
edit SPAC + XTRA on a source row, "Save to source file" → XTRA in the
`.glyphs`, SPAC in the mappings file, dot green.

Both are pre-existing behaviours exposed by Part B, not sprint
regressions; one commit each with its test, then the verifier re-runs
the designer's sequence on a fresh copy.

## Verifier re-run of the designer's B2 sequence at `72271f4` (2026-10-22) — data fixed, dot still red (F3); lint error

Gates on `72271f4` (`verify-r13` clone): pytest 564 passed, ruff clean,
typecheck, `test:unit`, build green. **Lint: 1 error** (see below).

Live, port 5073, fresh copy staged per the routine **plus the
designer's 5071 transforms state** (only `spac_widthaware` on — the
designer had switched it on at 23:16 before the original report, which
is why the sidebar showed a SPAC slider). Script `partb_f1f2.py`
(verifier scratchpad):

| case | on screen | persisted | dot |
| --- | --- | --- | --- |
| F1: studio row `Default 1 MinOpsz`, drag XTRA + SPAC, "Save to source file (promote)" inside the debounce | XTRA 828.5, SPAC 70.6 | source XTRA 828.5; mappings XTRA 828.5, SPAC 70.6 | **red** |
| F2: source row `Default 400 MaxOpsz`, drag SPAC, "Save to source file" (confirm accepted) | SPAC 16.8 | mappings SPAC 16.8 | **red** |

So **F1 and F2 are fixed for the data** — every value on screen reached
its home — but the dot still reads red, which is the symptom the
designer reported.

**F3 — the sync dot compares SPAC by exact key case.**
`getInstanceSyncStatus` builds `persisted` from `source_coordinates`
(no SPAC), the mappings `in` map and `out` map. The mappings file's
`in` keys are normalised to lowercase (`csv_io.normalize_in_axis_name`:
unknown columns → `.lower()`, so `spac`), while the row's coordinates
and the slider use the fvar tag `SPAC` (`/api/instances` returns
`{'SPAC': 51.2, …}`). The dirty check is `!(k in persisted)` with exact
case, so any draft carrying `SPAC` — including the baseline F1 now
reseeds from the refreshed row — is "a value with no persisted home"
and the dot is red for good whenever the SPAC transform is on. Fix:
compare keys case-insensitively when building/reading `persisted` (the
same rule `instance-sync.js` should own), and add the case to
`e2e/instance-sync.spec.mjs`: edits `{SPAC: 70.6}` against in
`{spac: 70.6}` → clean. Then the verifier re-runs `partb_f1f2.py`;
acceptance is green on both rows.

**Lint error since `5527ecc`:** `frontend/src/preview-coords.spec.mjs:59`
`'console' is not defined` (no-undef) — the spec lives under `src/`, so
the e2e node globals do not apply. Move it to `e2e/` with the other
specs or give it the node globals. **Correction of my own reports:** my
rounds 12 and 13 recorded "lint 0 errors"; my grep read the
"0 errors … fixable with --fix" line, not the totals line. The error has
been there since `5527ecc`; the CI `pages.yml` lint step would fail on
it.

**UI copy (F2 note):** "SPAC is saved in the studio’s mappings file, not
the source." The interface rule is no file-format internals: write "SPAC
is saved in the studio, not the source file." (also in the button
tooltip).

## Verifier: F1, F2 and F3 VERIFIED at `e325040` (2026-10-26) — Part B's B2 flow passes

Fresh clone of `e325040` (`verify-r15`; the previous clone in `/tmp` was
broken by macOS's periodic temp cleanup, so its first run here was
discarded). Gates read by exit code this time: pytest 562 passed /
2 skipped; `ruff check src tests` clean; **eslint 0 errors** (34
warnings, exit 0); typecheck ok; `test:unit` exit 0; build OK.

Designer's B2 sequence live on port 5073, fresh copy staged per the
routine with only the SPAC transform on (the designer's 5071 state),
`partb_f1f2.py`:

| case | on screen | persisted | dot |
| --- | --- | --- | --- |
| F1: studio row `Default 1 MinOpsz`, XTRA + SPAC dragged, "Save to source file (promote)" inside the debounce | XTRA 828.5, SPAC 70.6 | source XTRA 828.5; mappings XTRA 828.5, SPAC 70.6 | **green** |
| F2: source row `Default 400 MaxOpsz`, SPAC dragged, "Save to source file" (confirm accepted) | SPAC 16.8 | mappings SPAC 16.8 | **green** |

Flyout note reads "SPAC is saved in the studio, not the source file."
**VERIFIED_HEAD `e325040`.** Remaining items are the designer's: the
rest of Part B, the fontra-embed push, the merge into `github-pages`,
and the CI/Pages runs a push confirms.

## Approved next scope — a CI-grade `avar2-studio build` (user decision 2026-10-28; verifier-written, the agent picks up from here)

Why: the Google Fonts team needs one command they can run from any
build (their CI, a gftools recipe, a Makefile) that produces the
shipped avar2 font from a clean checkout, with no studio, no browser
and no files they did not commit. `avar2-studio build SOURCE --out DIR`
exists (`cli.py`) and runs the studio's pipeline; the verifier ran it
on the bundled example from the sprint code: 3.5 s, exit 0, font
printed. The items below make it fit for that use. Same rules as S0–S6:
smallest change, one commit per item with its test, evidence in
`LOCAL_TEST_RESULTS.md`, `STATE: checkpoint` when C1–C6 are in; the
verifier verifies in a fresh clone and on a fresh checkout of a copy
of Crispy (git-clean, committed files only). Nothing about the
server's behaviour changes; every item is a CLI-only concern unless
stated.

### C1 — the declared axis defaults ship with the source
Today the traditional axes' `min/default/max` (wght 400, wdth 100,
opsz 144 on Crispy) live in `.avar2-studio/axis-metadata.json`, which
SIDECARS.md tells designers not to commit. A clean checkout therefore
builds with each column's minimum as the default: the CLI only warns,
and named styles land off their mapped values (62 u measured on
Crispy). Fix: make the committed sibling `<stem>-axis-metadata.json`
(SIDECARS.md already names it as an alternative read location) the
canonical home — the writer writes there, `.avar2-studio/` is read as
a fallback and migrated to the sibling on first load, SIDECARS.md's
row flips to "Commit? yes", the CLI warning becomes an error only when
*neither* exists. Evidence: a git-clean checkout of a Crispy copy with
only committed files → `avar2-studio build` exits 0 with no warning,
and fontTools instancing of the built font at `Default 900 MaxOpsz`'s
inputs lands on that row's out within 1 u (the probe the verifier
uses). The studio on the same checkout shows the same defaults.

### C2 — `--workdir DIR`
The build writes `.avar2-studio/` (config, shadow, build output, memo)
beside the source. CI wants the checkout untouched. Add
`--workdir DIR` to `build` (default: beside the source, as today); the
preview dir, build dir and memo all resolve under it. Evidence: build
with `--workdir /tmp/x` → `git status --porcelain` in the checkout is
empty afterwards; the font is byte-identical to a build without the
flag.

### C3 — every failure reason reaches the exit message
`degraded` (relocation failed, S1(b)) exits 1 today as "build failed:
unknown" because `LAST_BUILD_ERROR` is empty. Print
`origin_relocation_error` for that case; print every `LAST_*` that is
set, one per line, prefixed `error:`; exit codes: 0 ok, 1 build
degraded/failed, 2 usage/load error. Evidence: the S1 recipe state
(control axis + braces + ORIGIN nudge) run through the CLI → exit 1
and the relocation message on stderr; the test pins the three codes.

### C4 — the export options on the CLI
The studio's export dialog (`routes/build.py` export job →
`pipeline._export_pipeline`) can hide axes, re-origin at a resting
style and emit WOFF2. CI needs the same font. Add to `build`:
`--hide-axes XOPQ,XTRA,YOPQ`, `--default wght=400,opsz=14` (the
"set default" relocation; refused with the existing readable message
when it cannot fit), `--format ttf|woff2` (repeatable). Implement by
calling `_export_pipeline` on the built font — no second pipeline.
Evidence: for the same options the CLI output is byte-identical
(sha256) to the studio's export download on the same copy; hidden
flags set in `fvar`; WOFF2 decompresses to the same table set.

### C5 — CI proof and the recipe for other repos
(a) `test.yml` in this repo runs `avar2-studio build` on
`examples/crispy-mini` from the installed package (not a dev checkout
path) and fails the job on non-zero — the command the docs promise is
the command CI checks. (b) README "Building in CI": the exact install
line (package + `fontc` + `gftools` pins), the command, the exit
codes, and a GitHub Actions snippet a font repo can paste; plus a
gftools-builder note (the `AVAR2_STUDIO_GFTOOLS_BUILDER=1` safety
valve stays internal — the documented path is the in-process one).
Evidence: the workflow run on the checkpoint push is green and its
log shows the build step.

### C6 — `doctor` fails CI before the build does
`avar2-studio doctor` reports; it does not exit non-zero. Add
`--strict` (exit 1 when a headless-build prerequisite is missing:
fontc, gftools, the engines where configured), so a CI job fails at
doctor with a named cause rather than mid-build. Evidence: test with
fontc unavailable → exit 1 and the name; with everything present →
exit 0.

### C7 — one committed build config drives the whole export
End goal (user, 2026-10-28): a full font export from the CLI from the
committed source, the mappings CSV and ONE config file — no flags
needed in CI. Add `<stem>-build.json` (CLI-owned, committed; written
by hand or by `avar2-studio build --write-config` from the current
sidecars) holding what C4 exposes as flags plus the two gftools steps
that already exist as transforms: `transforms` (which of
`round_corners`, `slant`, `spac`/`spac_widthaware` run, with their
params — the committed `<stem>-transforms.json` is the default, the
build config may enable/disable or override per run), `axes` (the
declared defaults from C1 are the source of truth; here only
`hide: [...]`), `default` (the resting style, C4), `instances` (named
instances from the CSV rows → `fix_instances`/`gen_fvar_instances`,
`stat: true` → `gen_stat`), `formats: ["ttf", "woff2"]`. Flags from C4
override the file. **Boundary:** everything design-authored stays
studio-only and read-only for the CLI — `<stem>-control.json`
(secondary axes, brace layers, instance values), `<stem>-grade.json`,
`<stem>-kinetikern.json`, instance coordinates in the CSV. The CLI
consumes them exactly as the studio's build does and offers no flag or
config key that edits them; a build config key that names one is a
usage error (exit 2) with a message pointing at the studio. Evidence:
`avar2-studio build Crispy.glyphs --config Crispy-build.json --out
fonts/` on a git-clean copy produces TTF + WOFF2 with the configured
transforms, hidden axes, resting style, named instances and STAT, and
the TTF is sha256-identical to the studio's export with the same
choices; a config naming `control` or `grade` exits 2.

### C8 — the worker's own build → test → deploy loop, and the verifier's, without touching the designer's Phase B
Contract (binding for the agent AND the verifier):
- **Reserved for the designer:** `~/Documents/Crispy` (never read for
  writing, never watched), port **5070** (live studio), port **5071**
  and `~/avar2-studio-test` (Phase B sanitised studio + its sync), and
  Fontra's port **8001 while Phase B runs** (B3 opens the editor from
  5071). Neither agent touches these; probes never target them.
- **Agent:** its own staging root OUTSIDE `/tmp` (macOS's periodic
  cleanup emptied `/private/tmp/av2-routine` on 2026-10-26 and broke a
  verifier clone the same way): `~/avar2-agent/` with a fresh Crispy
  copy per run staged per the routine, studio on **5072**, Fontra on
  **8002** (C9), CLI output under `~/avar2-agent/out/`. A
  `~/avar2-agent/loop.sh` the agent writes and keeps running: after
  each of its own pushes it (1) restages the copy, (2) runs
  `avar2-studio build` from the installed package on a git-clean
  checkout of the copy (the CLI test, C1–C7), (3) starts 5072 on the
  new bundle and runs the full `ui_probe.py` at idle, (4) appends the
  results to `LOCAL_TEST_RESULTS.md` and pushes. That push is the
  "deploy" of this cycle: the sprint branch's own CI (`test.yml` runs on
  `cleanup-2026-10`) is the deployed check; Pages and releases stay the
  user's call. A11/open-editor runs against 8002 only.
- **Verifier:** `~/avar2-verifier/` (also outside `/tmp`): a watcher on
  `origin/cleanup-2026-10` that, on `STATE: checkpoint`, runs
  `verify-checkpoint.sh` — fresh clone, gates by exit code, CLI build
  on a git-clean copy, probe on **5073** with Fontra on **8003** — and
  writes a report the verifier reviews into findings.md. Both loops
  stage their own copies; neither writes to the other's root.
- Evidence: each hand-off's results section names its root, ports and
  the git-clean check (`git status --porcelain` empty before the CLI
  build); a run that touched a reserved path or port is a FAIL of the
  hand-off regardless of the test results.

### C9 — `--fontra-port` (and `AVAR2_FONTRA_PORT`)
`state.FONTRA_PORT` is hard-coded 8001, so two studios cannot both
open the editor. Add the server flag + env (default 8001) so the
agent's 5072, the verifier's 5073 and the designer's 5071 each own a
Fontra. Evidence: two studios on different ports open the editor at
the same time; `open-editor` returns each one's port.

Out of scope: PyPI publishing (the user's, separately), a gftools
recipe-provider plugin, any change to the studio's export dialog, any
CLI path that writes a design sidecar.

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

## Verifier review of the Phase C checkpoint `2fcda7e` (2026-10-28) — C1/C2/C4/C6/C8/C9 VERIFIED; C3 and C5 with follow-ups; C7 FAILS at the resting style

Method: fresh clone of `2fcda7e` under `~/avar2-verifier` (`pip install
-e .[dev]`, plus Fontra and fontra-glyphs from git — see the
verifier-side note at the end), gates by exit code, CLI builds on a
git-clean copy of the designer's sources (`~/Documents/Crispy/sources`,
read-only; the copy committed with `Crispy-axis-metadata.json` copied
from the legacy file), studio on 5073 with Fontra on 8003 running the
16-scenario probe, then my own C3/C6/C7 commands. The report
`~/avar2-verifier/reports/2fcda7e.md` is the first pass; the two FAILs
in it were my environment and are re-run below.

- **C9 — VERIFIED (one studio; two on the agent's evidence).**
  `--fontra-port 8003`: `POST /api/control-axes/TST2/open-editor` →
  `{port: 8003, editing_original: false}`, server log `Started Fontra on
  http://127.0.0.1:8003`, direct `:8003/editor.html` and the proxy via
  5073 both 200; `tests/test_fontra_port.py` 4 of 5 pass (the fifth is
  P1). I ran one studio; two concurrent ones rest on the agent's
  5072/5074 run and the unit test.
- **C1 — VERIFIED.** Git-clean copy with the committed sibling: `build`
  exit 0, no metadata warning; fvar defaults wght 400 / wdth 100 /
  opsz 144, avar major 2; `pipeline._evaluate_with_font` at the CSV
  inputs → `Default 900 MaxOpsz` XTRA 725.89 / XOPQ 316.27 / YOPQ 235.20
  (csv 726.0 / 316.3 / 235.2), `Default 400 MaxOpsz` exact, `Default 1
  MinOpsz` XTRA 630.91 (630.9). Without either metadata file: exit 2
  with the readable message.
- **C2 — VERIFIED.** `--workdir`: `git status --porcelain --ignored` 0
  lines before and after; the workdir holds `build config.yaml jobs
  shadow`.
- **C3 — VERIFIED with one gap (P2).** Live: exit 0 (C1); exit 1 with
  the relocation message when an export cannot fit (see C7); exit 2 for
  missing metadata and for `--default wght=abc` (`error: --default
  value for 'wght' is not a number: 'abc'`). The gap: a source that
  exists but cannot be parsed (a one-line garbage `.glyphs`, metadata
  present) exits **1** with `error: build failed: The source file
  cannot be read: 'str' object has no attribute 'keys'` — the scope
  said bad input → 2, and the message is a Python attribute error.
- **C4 — VERIFIED (through the C7 runs).** `--hide-axes XTRA --format
  ttf` on top of a config that hides XOPQ → the font hides XTRA only;
  the TTF and WOFF2 from one config have identical table sets.
- **C5 — the new `cli-build` job is green on the checkpoint push (run
  40680788452); the python and rust jobs are red.** One python failure
  is Phase C's (P1); the rest pre-date the phase (CI0 below). The README
  documents the flags and the refusal.
- **C6 — VERIFIED.** `doctor --strict` → 1 with fontc and
  gftools-builder off PATH (both named, with fix lines), 0 with them on;
  a missing Fontra is reported as optional and fails neither mode.
- **C7 — FAILS at the resting style (P3).** `--write-config` emits the
  sidecar state (5 transforms, `instances.fix/stat false`, `default
  null`) and exits 0; a config carrying `control` → exit 2 with the
  studio pointer; both as specified. But the config's `transforms` and
  `instances` reach the build only through the export re-origin step:
  - config at the resting style (`default: null`) with slant **off**
    while the committed sidecar has it **on**, `instances.fix/stat
    true` → exit 0; the CLI's own shadow step honours the override
    (only `[transform] round_corners` runs, 7,597 layers, no twin
    layers) — yet the built font still carries the `slnt` axis and one
    named instance (`Default 400 MaxOpsz`), and the log has no
    `spac_widthaware` / `fvar named instances regenerated` / `STAT table
    rebuilt` lines;
  - the same config with `default: {wght: 400, opsz: 144}` (the resting
    location spelled out) → the export step runs in-process: SPAC
    re-injected, instances regenerated (9), STAT rebuilt, `slnt` gone.
  Cause: `cli.py` calls `transforms.config.set_override(...)`, a process
  global (`transforms/config.py:40-47`, read by `entries()` at 76), but
  the build runs in the worker subprocess (`build_jobs.py:358`) whose
  spec carries only `ENV_KEYS` and the staged on-disk `-transforms.json`
  (`SIDECAR_SUFFIXES`); the worker builds from the committed sidecar.
  The agent's live C7 evidence had `default: {wght: 400, opsz: 14}`, so
  it only exercised the export path — and on Crispy with the committed
  transforms (rounding + slant on) that re-origin is refused (`g`: 2,599
  tuples after relocation; the message says to export at the resting
  style or disable slant/rounding), so for Crispy as committed the
  config's transforms and instances currently never apply. Fix: carry
  the override to the worker — stage the override entries as the job's
  `-transforms.json` (the committed sidecar is still never rewritten),
  or put them in the spec and `set_override` in `build_worker` before
  the build; `instances.fix/stat` ride along as the two folded entries.
  Test: a config at the resting style with one transform flipped
  against the sidecar and `instances.fix true` → the built font (no
  `default`) carries the flipped state and the regenerated instances;
  pin it through the worker (`tests/test_build_jobs.py`), not through
  `set_override` in the test process.
  For the record, not a defect: `instances.fix` yields gftools' standard
  ladder (Thin … Black at the default of the other axes), not the 30
  studio rows — the agent's "9 named instances" are those.
- **C8 — VERIFIED.** `~/avar2-agent/loop.sh` names the reserved set
  (5070/5071/8001, `~/Documents/Crispy`, `~/avar2-studio-test`) and uses
  5072/8002; the final run `runs/20261028-181713/probe.log` has 16 PASS
  / 0 FAIL; the results sections carry root, ports and the git-clean
  check.

### Follow-ups for the agent (defects found in review — the standing loop)

- **P1 — `tests/test_fontra_port.py:78` asserts
  `importlib.util.find_spec("fontra") is not None`.** The launcher is
  stubbed on the line above, so the assert only couples the test to the
  venv: it fails on every plain `pip install -e .[dev]` (my clone: 1
  failed / 627 passed) and in both CI python jobs on every Phase C push
  since `65d2039` — the one CI regression of the phase. Drop the assert
  and its comment (Fontra is a git-only optional extra, not a dev
  dependency).
- **P2 — an existing but unreadable source exits 2 with a readable
  message** (`error: Crispy.glyphs could not be read: …`, no Python
  attribute error), pinned in `tests/test_cli_failures.py` with a
  garbage file beside a valid metadata sibling — the current
  "unloadable source" case there is not this one.
- **P3 — C7's override reaches the worker build** (above). P3 first: it
  changes what C7 claims.

### CI0 — recommended, NOT approved (user decision)

CI on `cleanup-2026-10` has been red since at least `2f51aad`
(2026-10-21), on every push: the python jobs fail
`tests/test_glyphs_launcher.py::test_open_starts_the_studio_on_the_first_free_port_and_stop_ends_it`
("no callback within 20.0s") and
`tests/test_worker_shadow.py::test_a_shadow_drawing_survives_the_worker_build`
(`[Errno 2] No such file or directory: 'fontmake' (avar2 build also
failed: Preview config not found)`); the rust jobs fail
`clamp_drops_stranded_sources` (`tests/clamp_oracle.rs:221`, fontmake
missing). `frontend` and `cli-build` are green. If approved: make the
python and rust jobs green on a plain runner (the worker-shadow fixture
supplies its preview config or the test skips without fontc; the
launcher test tolerates a slow runner; the clamp oracle installs
fontmake or skips without it).

### Verifier-side corrections

The first pass of my loop reported `control-axis FAIL (503)` and `cli
exit: 0` next to a metadata error. Both were mine: Fontra was absent
from the fresh venv (it installs from git; `pip install fontra
fontra-glyphs` finds no `fontra-glyphs` on PyPI), the staged copy lacked
the C1 sibling, and the exit code was read through a pipe.
`verify-checkpoint.sh` now installs Fontra from git, stages
`Crispy-axis-metadata.json` from the legacy file, reads the CLI exit
directly, checks `--ignored`, and prints pytest's FAILED lines. Every
number above is from the re-runs.

## Designer Part B finding — B7, the Kinetikern tab (2026-10-28; K1–K4 approved by the user, agent to pick up after the Phase C review)

Found by the designer during B7 on the sanitised studio (port 5071,
copy of the current `Crispy.glyphs`, SPAC transform on). B1–B6 pass.
State on that copy: `Crispy-kinetikern.json` with `enabled: true` and
one entry, `Default 400 MinOpsz` at looseness −0.60. The four notes,
checked against the code at `8425478`:

**K1 — Kinetikern has no presence outside its own tab: no row badge,
no off-switch.** Rows in the Instances tab get Grade, Rounding and
Slant badges (`InstanceRows.jsx:177-197` passes their props); nothing
for Kinetikern, and the app loads the Kinetikern state only inside
`KinetikernTab.jsx` (`api.getKinetikern`, line 141). The Transforms
menu (`Header.tsx:367-464`) lists Grade and the post-build transforms;
Kinetikern is absent. The sidecar already has the family-level
`enabled` flag (`kinetikern/sidecar.py:49,54`; `entries()` returns
nothing when it is off, line 95, so the build honours it; `set_enabled`
at line 108) — but nothing calls it: `git grep set_enabled` hits only
`sidecar.py`. Fix: (a) `PUT /api/kinetikern` with `{"enabled": bool}`
→ `set_enabled` + one shadow rebuild, and a "Kinetikern" row in the
Transforms menu (toggle, the count of spaced styles, copy along the
lines of "Per-style spacing from the Kinetikern tab. Off builds
without it and keeps the entries"); (b) a per-row badge on the
Instances tab in the Grade badge's pattern: a spaced style shows
"K −0.60" with Remove (the existing DELETE route) and a jump to the
Kinetikern tab with that style selected; an unspaced style shows no
badge (absence means not spaced — spacing is authored in the tab, so
no "+ Kinetikern" affordance on the row). Load the state in
`useStudioData` next to `grade` and refresh it after builds. Test:
unit for the route; a probe scenario that applies on one style →
that row carries the badge and the Transforms count reads 1; toggle
off → `/api/kinetikern` reports `enabled false`, the rebuilt font's
advance of a spaced glyph at that style is back to the unspaced
value, the badge shows the off state; toggle on → the delta is back.

**K2 — "Apply to all…" replaces every style's looseness with the
slider's value, silently.** `routes/kinetikern.py:163-182` loops every
style with parametric coordinates and calls `_kinetikern_apply_one`
with the one `looseness` from the request; `sidecar.set_entry` (line
126) drops the existing entry of the same name. The confirm
(`KinetikernTab.jsx:218`, "Each gets its own correction") is true of
the deltas and false of the looseness, and `docs/guides/kinetikern.md:40`
says the same. User decision: keep "all means all" and make the
overwrite visible, not skip the tuned styles. Fix: the button reads
"Apply −0.60 to all…" (the slider's value); the confirm names the
styles that already carry their own looseness and what they will
become ("Default 400 MinOpsz (−0.60) … will be re-spaced at 0.00");
with none spaced, the plain confirm; the guide line says it replaces
each style's looseness. No route change. Test: a node spec with one
style stored at −0.60 and the slider at 0 → the confirm text names
that style and its value; after confirming, `/api/kinetikern` shows
every style at 0.

**K3 — spaced styles are marked by a bare "●", Revert does not say
what it reverts, and the slider does not sync at load.**
`KinetikernTab.jsx:399,401` prefix spaced names with `● `; the Revert
button (line 436) is enabled when the selected style has an entry and
the status line (456) says "applied at looseness"; the designer did
not read any of that as "this style is spaced — select it and revert".
Also, lines 145-147 sync the slider to the entry on `[selected]` only,
but `entry` derives from `stored`, which arrives async (140-143): when
the initially selected style is the spaced one, the slider stays at 0
until the user re-selects. Fix: option text "Default 400 MinOpsz ·
spaced −0.60"; the Revert button reads "Revert Default 400 MinOpsz"
(its title says it is disabled during a build); the sync effect also
depends on `entry?.looseness`. Test: a node spec mounting with the
first style stored at −0.60 → the slider reads −0.60 on mount, the
option text carries "spaced −0.60", the Revert button names the style.

**K4 — size: the Font Size slider sits at the top of the left column
and nothing ties a style to its intended size.** The control exists
(`KinetikernTab.jsx:325-337`, shared with Preview and Instances, 9–144
pt) and the panes use it (465-474), but the designer did not find it
there; and unlike Preview, which starts at the opsz minimum
(`PreviewTab.jsx:119-128`), picking a style in this tab never moves
the size. User decision: both. Fix: (a) move the Font Size control
into `.kk-main`, above the before/after panes next to the sample
text (still the shared value); (b) `pickInstance` sets the size from
the style's `opsz` coordinate (`coordOf(inst, 'opsz')`, clamped to
the slider's range; no opsz axis → leave the size alone; Custom
location leaves it alone); a manual slider move afterwards wins until
the next pick. Test: a node spec: pick a style at opsz 72 → "Font
Size: 72pt"; pick one at opsz 144 → 144pt; move the slider to 36 →
stays 36 until the next pick; the control renders inside `.kk-main`.

One commit per item with its test, after the Phase C review below is
closed; the verifier then re-runs the designer's B7 sequence on a
fresh copy (apply on one style → badge, Transforms toggle, dropdown
text, Revert, size-on-pick).

## CI0 approved (user decision 2026-10-28) — the python and rust CI jobs green on a plain runner

Scope for the agent, after P1–P3 and K1–K4 (it may be interleaved while
a CI run is pending — each fix needs the real runner to confirm). The
three failures, red on every push since `2f51aad` (CI log of run
40680788452 and earlier runs are the evidence; none reproduces on a
developer machine, so root-cause each on the runner, not by guessing):

- **CI0a — `tests/test_worker_shadow.py::test_a_shadow_drawing_survives_the_worker_build`**
  fails with `Font build failed: …[Errno 2] No such file or directory:
  'fontmake' (avar2 build also failed: Preview config not found)`. The
  runner has fontc (`cli-build` passes with it), so the avar2 build path
  is what fails there — "Preview config not found" — and only then does
  the fontmake fallback get tried. Find what the test takes from a
  developer machine that a plain `pip install -e .[dev]` checkout lacks,
  and make the test self-contained. Skipping is not the fix: this test
  is the worker-subprocess guarantee (ADR 0003).
- **CI0b — `tests/test_glyphs_launcher.py::test_open_starts_the_studio_on_the_first_free_port_and_stop_ends_it`**
  fails with `no callback within 20.0s`. Establish whether the callback
  never fires on the runner or merely arrives late (the log's timings
  tell); a fixed 20 s wait on a shared runner is not a test of the
  launcher. Bound the wait on the studio's own readiness signal, not a
  clock.
- **CI0c — rust `clamp_drops_stranded_sources` (`wasm/fontc-web/tests/clamp_oracle.rs:221`)**
  fails because the oracle shells out to fontmake, which the rust jobs
  do not install. Install it in those jobs (it is the oracle, so the
  comparison is the point) rather than skip.

Acceptance: the `Test` workflow fully green (python ×2, rust ×2,
frontend, cli-build) on the checkpoint push, on both runners; the agent
cites the run id in `LOCAL_TEST_RESULTS.md`, and the verifier reads the
run, not the claim. `cli-build` keeps its semantics (C5). No test is
marked skip/xfail to get there.

User-owned, no agent action: the `instances.fix` Thin–Black ladder vs
the studio's named styles — the designer is taking that to the Google
Fonts team.

## Verifier review of the ROUND 18 checkpoint `4d96107` (2026-10-29) — P1/P2/P3 and K1–K4 VERIFIED; CI0a–c verified as fixes, CI0's acceptance NOT met on the checkpoint push; two follow-ups (K1b, CI0d)

Automated pass (`~/avar2-verifier/reports/4d96107.md`, fresh clone + venv, 2026-10-28 23:14): pytest **635 passed, 2 skipped** (P1's assert gone: a plain `pip install -e .[dev]` clone with no Fontra now passes), ruff clean, eslint 0, typecheck ok, unit ok, build ok; CLI build on a git-clean copy exit 0, checkout clean after (`--ignored`); probe 15/16. The one FAIL (`control-axis: 503`) was the verifier's venv, not the code: the script still installed Fontra by its PyPI names (no `fontra-glyphs` there). Script fixed to the two git URLs; the scenario re-run below passes.

Per item, my own runs (not the agent's log):

- **P3 — VERIFIED.** The exact case that failed last round, through the worker: git-clean copy of the committed Crispy sources, committed `Crispy-build.json` from `--write-config` with `slant.enabled: false` (round_corners + spac_widthaware left on), `instances.fix/stat: true`, `default: null`, sidecar still has slant on. `avar2-studio build` → exit 0, `Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,wdth,wght].ttf`: **no `slnt`**, ROND and SPAC present, **9 named instances** (Thin…Black), **STAT** present, `Crispy-transforms.json` unchanged, 0 dirty lines before and after. Before P3 this build kept `slnt` and one instance. Mechanism read: `build_jobs._stage` puts `transforms.config.override()` in the spec, `build_worker._bind_server_globals` installs it; `tests/test_build_config_worker.py` covers the seam and a real worker build (SPAC dropped by the config).
- **P2 — VERIFIED.** One-line garbage `Crispy.glyphs` beside a valid `Crispy-axis-metadata.json`, 4d96107 CLI: exit **2**, last stderr line `error: Crispy.glyphs could not be read: Unexpected character after key at line 1: 'g'`, no output files, no `build_worker` left. Cosmetic only: the loader's own `openstep_plist.parser.ParseError` line and an `Auto-building font from …` line precede the message in stderr.
- **P1 — VERIFIED.** The `find_spec("fontra")` assert is gone; see the 635-pass run above on a venv without Fontra.
- **K1 — VERIFIED, one copy defect (K1b).** Route: `PUT /api/kinetikern {}` → 400 `Body must include 'enabled' (true/false)`. Probe `kinetikern` scenario on 5073 (4d96107 code, Fontra on 8003): after an apply the Instances row shows **`K +0.45`**, the Transforms menu has a Kinetikern row, checked, "Per-style spacing from the Kinetikern tab. 1 style spaced. Off builds without it and keeps the entries.", count flag 5; off → `/api/kinetikern` `enabled: false` with the entry still listed, badge detached, row copy "Off — the build uses the source's own spacing. 1 style spaced; turn it back on to restore them.", count 4; on → entry back, badge back. My own Playwright pass (`k_live_check.py`, one style spaced at −0.60): badge `K -0.60`; popover **Open in Kinetikern** switches to the tab with `Default 1 MaxOpsz` selected and the slider at −0.60; **Remove** drops the entry (`instances: []`, `enabled: true`), one rebuild, badge detached.
  - **K1b (copy):** the popover hint reads "deltas live in the Kinetikern **sidecar**, not in this row" — UI copy must not name sidecar/JSON/CSV files (the standing rule: "the studio" / "the source file"). Say e.g. "Spaced by Kinetikern at −0.60. The deltas live in the Kinetikern tab, not in this row." Put the string in `frontend/src/kinetikern-copy.js` next to `badgeLabel` and pin it in `e2e/kinetikern-copy.spec.mjs` (assert it contains none of `sidecar`, `JSON`, `CSV`).
- **K2 — VERIFIED.** Slider at +0.25 → button **`Apply +0.25 to all…`**; clicking it raised the confirm "Space all 30 instances at +0.25? Each gets its own correction; the font rebuilds once. Default 1 MaxOpsz (-0.60) already has its own looseness and will be re-spaced at +0.25." Dismissed → `/api/kinetikern` unchanged. Guide line updated.
- **K3 — VERIFIED.** Dropdown reads `Default 1 MaxOpsz · spaced -0.60` (no `●`), the button reads `Revert Default 1 MaxOpsz`, and jumping from the badge shows that style's looseness on the slider (the `entry?.looseness` dependency).
- **K4 — VERIFIED.** `.kk-main .font-size-control` present (count 1); pick `Default 1 MinOpsz` (opsz 14) → `Font Size: 14pt`; pick `Wide 1000 MaxOpsz` (opsz 144) → `Font Size: 144pt`; manual move to 33pt holds; the next pick resets to the style's opsz.
- **C9 re-check (control-axis) — PASS** with Fontra present: `editor: {port: 8003}`, direct `127.0.0.1:8003/editor.html` 200 and `/fontra/editor.html` via 5073 200, server log "Started Fontra on http://127.0.0.1:8003". How it was run, stated plainly: the classifier blocked `pip install git+…fontra` into the 4d96107 venv, so the 5073 studio was started with last round's venv (Fontra 2026.10.1.dev79 from git) and `PYTHONPATH=clone-4d96107/src`; `pyproject.toml` did not change between 2fcda7e and 4d96107, and the K1 route (new at 4d96107) answered on that server, so it served 4d96107's code.
- **CI0a/b/c — VERIFIED as fixes.** Read: `_QuietServer` skips `getfqdn` in the launcher test and stub, `READY_TIMEOUT`/`WAIT` separated; `build_variable_font` also looks for fontc beside `sys.executable` (`tests/test_fontc_lookup.py`); `compare_clamp.py` exits 3 without fontmake, `clamp_oracle.rs` accepts 3, the rust job installs fontmake. Run **40721221145** on `2237df9`: all six jobs success (read from `gh run view`, not the log).
- **CI0 acceptance — NOT MET on the checkpoint push.** The acceptance was "a fully green Test workflow on the checkpoint push". The checkpoint push is `99da5aa` (STATUS/LOG commit on top of the code HEAD `4d96107`): run **40723781794** is **failure** — `python (macos-latest)`: `tests/test_avar2_default_relocation.py::test_a_failed_relocation_is_memoised`, line 320 `assert second.read_bytes() == first_bytes` (`1 failed, 631 passed, 5 skipped`); `4d96107`'s own run 40722978945 was cancelled by the next push. The same test failed on macOS at `0ee5c90` (run 40716800627) and passed at `2237df9`: **intermittent**, and not touched this round (last change `f08b002`). Locally it passes 5/5 in 0.6–2.6 s each.
  - **CI0d — approved scope (follows from CI0's acceptance, no new user decision needed):** make that assert content-based. Evidence: the test compares raw bytes of two saved fonts; `pipeline._font_content_digest`'s docstring says `head.modified` is a timestamp and excludes `head` for exactly this reason, and the sibling success-path test (`test_a_memo_hit_serves_the_same_content…`, same file) already compares digests. Hypothesis (fits "slow macOS runner, never locally"): on the cached-failure branch the un-relocated font is saved again, so two builds that straddle a second boundary differ in `head` only. Fix: assert `pipeline._font_content_digest(second) == pipeline._font_content_digest(first)` and, to prove the hypothesis rather than hide it, assert that the set of tables whose bytes differ is a subset of `{"head"}` (that assert's failure message should list the differing tables). Acceptance for CI0 then re-applies: a fully green Test workflow on the **checkpoint push commit itself** — STATUS cites the run id for the HEAD commit, not an earlier one.
- Minor: LOG.md's checkpoint paragraph cites P1 as `6689b6`; the commit is `668b9b6`.

Verdict: P1, P2, P3, K1, K2, K3, K4 verified at `4d96107`; CI0a–c verified as fixes. Open: **K1b** (copy) and **CI0d** (flaky byte assert + acceptance on the push itself). `VERIFIED_HEAD` moves when the next checkpoint's own run is green.

Verifier-side notes: `verify-checkpoint.sh` now installs Fontra from the git URLs; the loop otherwise ran unattended (watcher fired at 23:14, report 23:33). Artefacts: `~/avar2-verifier/{reports/4d96107.md, p3-out-4d96107, p3-build-4d96107.log, copy-4d96107 (committed Crispy-build.json in the P3 state), shots-rerun-4d96107/}`; the live check script is `~/avar2-verifier/k_live_check.py` (run against an idle studio with one spaced style).

## Observation (2026-10-29, designer report "instances look deformed on 5001 and 5071") — not an item until the user says so

What happened, from the logs and four repro builds (`~/avar2-verifier/diag/`):

- The Glyphs-menu launch at 16:01 opened a second studio (port 5001, current worktree code) on the **same source folder** as the designer's 5071 studio (`~/avar2-studio-test/sources`, because that copy was the font open in Glyphs). Two servers then shared one `.avar2-studio/` (shadow, build, jobs).
- The 5001 server ran C1's migration on first UI load: `axis metadata migrated to Crispy-axis-metadata.json`, and the legacy `.avar2-studio/axis-metadata.json` was **unlinked**.
- The 5071 server is pre-C1 code in memory (started 2026-10-28 15:39 from bce032f) with workers importing the live worktree. Its `_stage` copies `PREVIEW_FILES = ("axis-metadata.json", …)` from the preview dir and a `SIDECAR_SUFFIXES` list without `-axis-metadata.json`. After the migration it stages **no axis metadata**; the new-code worker finds neither sibling nor legacy beside the staged original, `_load_axis_metadata` yields `{}`, `_declared_axis_ranges` is empty, no row is a default row, the relocation target is empty, and the build skips relocation. 5071's jobs 26 and 28 (16:01:33–43, 2.7–2.9 s each) wrote the font both studios now serve: `Crispy[GRAD,LCWD,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,wdth,wght].ttf`, 176 KB, fvar defaults **XTRA 47 / XOPQ 1 / YOPQ 1 / wdth 5 / wght 1 / opsz 14** (the compiled corner), gvar `n` 15 tuples — versus the 14:56 build from the same copy: 829 KB, defaults 663.1 / 202.5 / 121.8 / 100 / 400 / 144, `n` 98 tuples. The real `~/Documents/Crispy/sources` was not touched (no sibling, no build today).
- Repro matrix with the HEAD code (`bdd63a1`, no uncommitted src) on a copy of that folder: sibling present → correct (1.09 MB, defaults at the ORIGIN row, `n` 152); legacy only → correct, and the UI load migrates without changing the font; **no metadata at all → the exact bad font** (176,904 B, corner defaults, `n` 15, 2.2 s) with `last_build_status: ok`, `degraded: None`, no origin warning. No source commit landed today, so the trigger was the migration, not a code change.

Two things worth a decision (user's call):
1. **Silent corner build.** With no declared axis metadata the pipeline builds an un-relocated font whose user axes default to the first master and reports `ok`. The "resting dip" warning (`state.LAST_ORIGIN_WARNING`) does not fire for this case. Under C3's own principle this should be loud: a build without declared user-axis defaults should surface a warning (health + banner), or refuse when a `default-origin` exists but cannot be honoured.
2. **Migration unlinks a file another process may still read.** One-time mixed-version hazard in this sprint (old server + new worker on one folder); in the field it only bites if two studio versions share a folder. Keeping the legacy file (copy, not move) until the sibling is committed would be the conservative form.

Designer-side recovery (not done by the verifier — 5071, 5001 and `~/avar2-studio-test` are the designer's): close the 5001 studio; restart 5071 with the same command and environment it was started with (the current worktree code then reads the sibling that now exists), or restore `.avar2-studio/axis-metadata.json` from `Crispy-axis-metadata.json` if 5071 must keep running as is — but a new-code process on that folder will migrate it again.

## Verifier review of the ROUND 19 checkpoint `aa6b87f` / push `bdd63a1` (2026-10-29) — K1b and CI0d VERIFIED; CI0 acceptance met on the push commit itself

- Automated pass (`~/avar2-verifier/reports/aa6b87f.md`, 15:38–15:55): pytest 635 passed / 2 skipped, ruff clean, eslint 0, typecheck ok, unit ok, build ok; CLI build on a git-clean copy exit 0, clean after; probe **16/16** on 5073 with Fontra on 8003 (the verifier venv now installs Fontra from the git URLs, so `control-axis` passes in the unattended run).
- **K1b — VERIFIED.** `117f99a`: the popover hint is `badgeHint()` in `frontend/src/kinetikern-copy.js` ("Spaced by Kinetikern at −0.60. The deltas live in the Kinetikern tab, not in this row."), pinned in the node spec; the probe's kinetikern scenario passed on this code.
- **CI0d — VERIFIED.** `83908a2`: the memo test compares `pipeline._font_content_digest` and asserts `_differing_tables(first, second) <= {"head"}`, with the failing message listing the tables — the hypothesis is proven by the assert rather than hidden.
- **CI0 acceptance — MET.** Test workflow run **40843477514** on `bdd63a1`, the checkpoint push commit itself: success. Runs 40842980952 (`aa6b87f`) and 40841117105 (`83908a2`) also green. Read from `gh run list`, not from the agent's note.

Verdict: the approved queue (P1–P3, K1–K4, K1b, CI0a–d) is fully VERIFIED at `aa6b87f`. Nothing is queued for the agent. Open for the user: the two observations above (silent corner build; migration unlinking the legacy file), the Thin–Black ladder (Google Fonts meeting), the fontra-embed `shared-studio-shim` push, the `cleanup-2026-10` → `github-pages` merge, and the 5071 restart.

## O1 and O2 approved (user decision 2026-10-29: "proceed with the rest")

Both come from the "Observation (2026-10-29 …)" section above; evidence and repro builds are there (`~/avar2-verifier/diag/`). One commit each with its test; the agent's loop after each push; `STATE: checkpoint` when both are in and the Test workflow run on that push commit is green.

**O1 — a build with no declared axis defaults must say so, not report `ok`.**
- Today: with `<stem>-axis-metadata.json` absent or `{}`, `_declared_axis_ranges` is empty, no CSV row is a default row, the relocation target is `{}`, the font rests at the compiled corner (user axes default to the first master: Crispy wght 1 / wdth 5 / opsz 14) and health says `last_build_status: ok`, `degraded: None`, `last_origin_warning: None`. The designer saw "deformed instances" with no message.
- Required (studio path): reuse S1(b)'s machinery — when relocation is on and the target is empty because no declared user-axis defaults exist while the CSV has mapped rows (or a `default-origin` exists), mark the build **degraded** with a reason that names the file (`<stem>-axis-metadata.json` missing or empty — the font rests at the compiled corner), surface it in health and the existing banner, and log it at warning level. Do not refuse in the studio (the preview must keep working); do not change the CLI's behaviour, which already exits 2 for missing metadata (C1) — add a test for that exit if none pins it.
- Also: `_load_axis_metadata` creating an empty `{}` file silently is part of the same silence — log it at warning level with the path.
- Test: a worker build of crispy-mini with the sibling removed → health `degraded` truthy, reason names the file, `last_build_status` still `ok`; with the sibling present → `degraded` None. Probe: the banner text visible in the UI for that state (one scenario or an assert in an existing one).

**O2 — the C1 migration must not unlink the legacy file.**
- Today: `_get_avar2_metadata_path()` writes the sibling and `legacy.unlink()`s `.avar2-studio/axis-metadata.json`. Any older process on the same folder (this sprint: the pre-C1 5071 server, whose `_stage` copies `PREVIEW_FILES` including the legacy name) then stages no metadata and builds at the corner.
- Required: copy, don't move. Write the sibling, keep the legacy file, log "migrated to <sibling>; legacy copy kept for older studio versions (safe to delete)". The sibling stays canonical (it is read first), so a stale legacy file is never consulted by current code.
- Test: after a first load with legacy only, both files exist with equal content and the sibling is what `_get_avar2_metadata_path()` returns; a second load does not rewrite either.

Not for the agent: B11 (pushes) and B12 (5070 on the real Crispy) are the designer's; the Thin–Black ladder goes to the Google Fonts meeting.

## B11 pushes approved for the agent (user decision 2026-10-29: "the agent can do the pushes too")

Sequenced **after** the O1/O2 checkpoint is verified (the deployed site must carry verified code): STATUS will say `APPROVED: B11a–c` at that point; do not start them before. No force-push, no history rewrite, no bare `git stash`, no PyPI publish.

- **B11a — Pages.** Merge `cleanup-2026-10` into `github-pages` with a regular merge commit (no squash, no rebase), push `github-pages`, then read the Pages workflow run for that push and confirm it is green and the site serves the new bundle (cite the run id and the served asset hash). Conflicts: tool code from the sprint branch; `github-pages`-only site content kept.
- **B11b — fontra-embed.** Push `~/Documents/fontra-embed` branch `shared-studio-shim` (HEAD `3de37a6`, on top of `f063a02`) to `origin` (`agyeiagyeiagyei/fontra-embed`), as a branch — opening a PR against its default branch is fine; merging it is the user's call. If fontra-embed has its own deploy workflow, read its run.
- **B11c — release wheels, dry run only.** Run `release.yml` via `workflow_dispatch` (artifacts to the run page; `publish_to: testpypi` allowed as the dry run), download the wheels, install one into a clean venv on this machine and run `avar2-studio doctor --strict`: both accelerators (spac-inject, kinetikern2) must be found. **Do not push a version tag** — a tag push publishes to PyPI and cannot be repeated for the same version; that step stays with the user.

Acceptance for each: the run id and its conclusion read from `gh`, plus the doctor output for B11c, in LOG.md; `STATE: checkpoint` after B11c.

## E1 approved (user decision 2026-10-29): "UFO + designspace" in the download popup, as a zip, with a spacing warning

Asked by the Google Fonts team via the designer: sources they can build with fontmake/gftools. The studio reads `.glyphs` and `.designspace` (`source_font.py`) but only writes TTF/WOFF2 (`routes/build.py::export_font`, `pipeline._export_pipeline`). Queue it **after O2 and before the checkpoint**, so the Pages merge (B11a) carries it. One commit with its tests.

**UI** (`frontend/src/components/PreviewTab.jsx`, the export modal): a third format next to TTF and WOFF2 — **"UFO + designspace (zip)"**. Copy: "Sources for fontmake and gftools: the masters as UFOs and a designspace 5 file with the avar2 mappings, the declared axis defaults and the named instances." When it is selected, a warning is always visible, not a tooltip: **"Spacing is not included. The SPAC axis and Kinetikern corrections are applied to the built font, not to the sources; a font built from these sources has the source's own spacing."** The "open at this style" control is disabled for this format with the reason "a designspace default must be a master location; the sources rest at the compiled origin". Hidden axes carry over (designspace `hidden="1"`). No size estimate for this format (none is cheap); the button reads "Download sources".

**Route**: `POST /api/export-font` accepts `format: "sources"` (400 message updated to name the three formats); same job + `/api/export/<job>/download` flow, served as `<family>-sources.zip`. The job does not need the built font: it writes from the staged active source (`state.GLYPHS_PATH`, the shadow with control-axis braces, grade braces, rounding and slant twin masters composed), via glyphsLib `to_designspace` + UFO save (glyphsLib 6.16.2 and fontTools 4.68 are in the venv; brace layers become sparse sources).

**Designspace content**: axes = the parametric axes with their master ranges and the created user axes (wght/wdth/opsz and any other declared ones) with min/default/max from the declared axis metadata; **`<mappings>`** = the CSV rows as designspace 5 axis mappings (input: user-axis location, output: parametric location), the default row handled exactly as `gen-avar2` does for the built font (dropped from the mappings; say so in the README); `<instances>` = the named rows with their user-axis locations; `hidden` on the hidden axes. A `README.txt` in the zip states: the studio version and commit, the source file and its sha256, which transforms were composed into the masters, the spacing warning verbatim, that the default row is not in the mappings and the font rests at the compiled origin (the studio's TTF relocates the default at build time; see docs/decisions/0001), and the fontmake command to build.

**CLI (user decision 2026-10-29: required, same item)**: `avar2-studio build SOURCE --out DIR --format sources` writes the same zip to `--out` (`<family>-sources.zip`), repeatable with `ttf`/`woff2` (`--format ttf --format sources` writes both); `formats: ["sources"]` accepted in `<stem>-build.json`; `--write-config` lists it. The CLI prints the spacing warning on stderr once when sources are written. Exit codes unchanged (0 / 1 / 2). `--default` with `sources` → exit 2 with "sources rest at the compiled origin; --default applies to ttf/woff2 only" (bad input, not a build failure). The CLI reference in the README/guides lists the format.

**Not in scope**: set-default relocation for sources, SPAC/Kinetikern in sources.

**Acceptance**: (1) unit: the zip contains `<family>.designspace`, one UFO per master (count = masters + sparse brace sources), `README.txt`; the designspace parses with `DesignSpaceDocument.fromfile`, `doc.axisMappings` has one entry per non-default CSV row, user axes carry the declared defaults, hidden axes are `hidden`. (2) round trip on crispy-mini in the test suite: `fontmake -m <designspace> -o variable` builds (fontmake is installed in CI by CI0c; skip with a reason if absent locally); the result has the same axis tags as the studio's TTF minus SPAC, and the outlines of three glyphs at every CSV row's location match the studio's TTF within 3 u (instantiate both with the instancer at the row's parametric output — advance widths excluded from the comparison, that is the spacing warning). (3) probe: a new `export-sources` scenario downloads the zip through the modal and asserts the warning text is visible before download. (4) UI copy mentions no sidecar/JSON/CSV file names ("the mappings", "the source file"). (5) CLI: `tests/test_cli_*`: `--format sources` on crispy-mini writes the zip with the same contents as (1), `--format ttf --format sources` writes both, `--default … --format sources` exits 2 with the message, the config form works, and the cli-build CI job builds the sources zip too.

## Documentation audit (2026-10-29) — review in DOCS_AUDIT.md, awaiting the user's decisions; two code notes approved as verifier follow-ups

The audit itself is `docs/sprints/cleanup-2026-10/DOCS_AUDIT.md` (verdict, proposed shape, must-fix list, decisions needed; the line-level fact tables are its appendices). No documentation is changed until the user decides.

Two code facts surfaced by the fact-check are not documentation and go to the agent now, one commit each, after E1:
- **A1** — `frontend/src/components/Header.tsx` ~132–133: the `enabledCount` assignment is followed by a duplicated `+ (kinetikern?.enabled !== false && spacedCount > 0 ? 1 : 0);` line, a no-op expression statement left by K1. Remove it; add the eslint rule that would have caught it (`no-unused-expressions`) if it does not fire on the current config, or say why not.
- **A2** — `src/avar2_studio/kinetikern/__init__.py:38` and `src/avar2_studio/transforms/builtin_spac_widthaware.py:69` cite `docs/decisions/0015` for the wheel-carried `bin/` binaries; no such record exists. Write decision 0015 (the platform wheels carry `spac-inject` and `libkinetikern2` under `avar2_studio/bin/`, built by `release.yml`'s platform matrix; lookup order env → wheel → repo build), dated from the commit that introduced the lookup, and add it to the decisions index.

E1 acceptance gains (6): the README's export paragraph, the CLI reference and SIDECARS (zip contents) carry the new format in the same commit, in whatever shape the documentation decisions below settle on; until then, the minimal line in each.

## D1 approved (user decision 2026-10-29): apply the documentation audit

`DOCS_AUDIT.md` is the spec: section 2 (the shape and the per-document targets), section 3 (the must-fix list), Appendix D (the tables of contents) and Appendices A–C (every stale row and every duplicate, with code evidence). Queue D1 after A2 and before the checkpoint; one commit per document (README; docs/README.md; CLI.md; the five guides, one each; SIDECARS; DEVELOPMENT; ARCHITECTURE; decisions; examples (one commit for the three); the archive/delete moves), each commit message naming the appendix rows it resolves.

Rules for the rewrite:
- Facts come from the code, never from the old text; a row marked WRONG or OUTDATED is fixed or removed, never softened. UNVERIFIABLE rows are removed unless the agent measures them (then the number and the date go in).
- The reader set (README, docs/README.md, the five guides, the three example READMEs) names tabs, menus and buttons as the UI spells them; no routes, module names, symbols, test names or sprint-ticket ids. Where a mechanism matters, one sentence and a link to the Architecture section.
- The README install section says the studio installs from git until 0.1.0 is on PyPI, with the `pipx install git+…` line first; the CI examples use the git URL too. When 0.1.0 is published, that is a one-line change.
- Every duplicate in Appendix C is written once, in the document Appendix C's first column points at, and linked from the others.
- The appendices (CLI.md, ARCHITECTURE, DEVELOPMENT, SIDECARS, decisions, the plugin README's development notes) keep their developer register; ARCHITECTURE and SIDECARS drop the "as of 2026-10-16" dating and the sprint-ticket annotations and get a "checked against the code at <sha>" line instead. DEVELOPMENT loses the two personal session rules.
- `docs/HANDOVER.md` moves to `docs/archive/`; `docs/authoring-instances.md` is deleted (every link already points at the guide); `docs/README.md` links `CODEBASE_AUDIT.md`, `sprints/` and `archive/` under one *Internal* line.
- E1's documentation lines (README export paragraph, CLI.md, SIDECARS zip contents) are written into the new shape, not the old.

Acceptance (verifier, at the checkpoint): the two fact-check briefs are re-run against the new documents and return no WRONG, OUTDATED or MISSING rows for anything landed by then; every relative link and anchor in the reader set and the appendices resolves (a link-check script or `markdown-link-check` run, output in LOG.md); line counts within the section-2 targets (README ≤ ~150, each guide ≤ ~100, docs/README.md one screen); a grep of the reader set for `/api/`, `_[a-z]+\(`, `.py`, `T[0-9]\.` and `tests/` returns nothing; the decisions index lists fourteen (fifteen with A2), 0007's header matches its update; the Pages site after B11a serves the new README.

## Verifier check of E1 at `e6f105b` (2026-10-29) — the exported mappings do not build: E1a required before the checkpoint

Run: `avar2-studio build examples/crispy-mini/sources/CrispyMini.glyphs --format ttf --format sources` from the branch code (exit 0, the spacing note on stderr, `CrispyMini-sources.zip` with 8 UFOs, a designspace with 7 `<mappings>`, 8 instances, 31 sources, README.txt as specified). Then `fontmake -m CrispyMini.designspace -o variable` (fontmake 3.12.2, fontTools 4.68.0):

- **fontmake exits 0 but logs one ERROR per mapping** — `In axis mapping from input {...}, some output axes are not in the variable-font: {'XOPQ': …, 'XTRA': …, 'YOPQ': …}` — and the built font has **no `avar` table at all**. Cause: `sources_export._mapping_xml` writes the `<output>` dimensions by axis **tag** (`XOPQ`), while designspace 5 dimensions are resolved by axis **name** (`X-Opacity`; fontTools `designspaceLib.split` matches `axis.name`). The `<input>` dimensions already use names (`Optical Size`, `Width`, `Weight`), so only the parametric side is wrong.
- The LOG's statement that "fontmake does not consume designspace 5 `<mappings>` at all" is incorrect, and the acceptance deviation built on it falls: with the three output names corrected in the exported file and nothing else changed, fontmake builds **avar version 2 with the 7 mappings, zero error lines**.
- With that patched font, the literal acceptance (2) is well-posed and met for every mapped row: at each row's user location (avar2 applied in both fonts via `getGlyphSet(location)`), `a`/`o`/`g` match the studio's TTF within **0.8–2.9 u**, advances within 0.6 u (SPAC rests at 0). The one exception is the **default row** (`Narrow Thin 144`): 8–9 u on outlines and 8 u on advances, because the studio's font is relocated to that row's output (XOPQ 6 / YOPQ 6) while the sources rest at the compiled origin (2 / 2) with the row dropped from the mappings — the resting dip of decision 0001, which the zip's README already states. The current test compared against the unmapped fontc build (0.0 u) and never asserted an `avar` table, which is why it stayed green over a broken export.

**E1a (required; one commit with its test, before A1):**
1. `_mapping_xml`: every `<dimension name>` is the axis **name**, input and output alike (take it from `doc.axes`, never from the tag).
2. The fontmake round-trip test asserts, on the fontmake font: `avar.majorVersion == 2`, the number of mappings equals the number of non-default CSV rows, and the fontmake log contains no `ERROR` lines; then outlines of `a`/`o`/`g` at every **non-default** row's user location within 3 u of the studio's TTF, advances within 1 u; the default row asserted separately against the unmapped fontc build (≤ 3 u) with a comment naming the resting dip and the measured 8–9 u.
3. LOG.md: replace the "fontmake does not consume mappings" paragraph with the corrected fact and the numbers above.

Everything else in E1 checked out at this commit: the CLI prints the note once, `--default … --format sources` is refused (`tests/test_cli_export_options.py`), the dialog's third chip carries the always-visible warning (`frontend/src/export-formats.js`, no sidecar/JSON/CSV words), README.txt names version, commit, sha256, composed transforms, the default-row note and the warning verbatim.

## Verifier check-in at `e6f105b` (2026-10-29 20:20) — tests pass; E2 approved

Full pass on a fresh clone (`~/avar2-verifier/reports/e6f105b.md`): pytest **652 passed, 2 skipped**; ruff clean; eslint 0; typecheck ok; unit ok; build ok; CLI build on a git-clean copy exit 0 and clean after; probe **18/18** once a verifier staging gap was corrected (the one FAIL, `first-load: ORIGIN badge missing`, came from the studio copy being staged without `Crispy-axis-metadata.json` — the real sources migrated to the sibling at 16:57 today — and the re-run with it passes with the built font at the ORIGIN row). That unintended no-metadata run also showed O1 doing its job: `Created an empty axis-metadata file … no axis defaults are declared yet, so the font rests at the compiled corner` in the log, and the `degraded-banner` scenario passes. CI is green on every push today including `e6f105b`. E1's one defect is E1a above.

## E2 approved (user decision 2026-10-29, after the tests-pass check): spacing in the exported sources

Queue after E1a, before A1. The studio's own preview and TTF keep the binary-stage injectors (decision 0004's budget stands); the **sources export** renders the same sidecars as source-level masters, so a fontmake build of the zip has the spacing. The sidecars stay the authored truth; two renderers.

- **SPAC** as metrics-only twin masters on a `SPAC` axis: for every master a twin at the axis maximum (and at the minimum when `min ≠ −max`), same outline, each glyph's LSB and RSB moved by the width-aware transform's per-glyph amount at that SPAC value (`builtin_spac_widthaware`'s model — `(ink / mean_ink) ** bias`, `scale`, composites spaced too). The transform is linear in SPAC, so one twin per end suffices. The uniform gftools SPAC, when that transform is the enabled one, is the flat amount.
- **Kinetikern** as metrics-only brace layers: for every entry in the Kinetikern sidecar (while `enabled`), for every glyph with a non-zero delta, a brace layer at the style's parametric location (the CSV row's output; other axes at their defaults) carrying the interpolated outline with LSB and advance moved by `−left` / `+right` as `kinetikern/inject.py` does; composites keep their own metrics. Composed onto the export's staged source after the control-axis and grade layers; never written to the committed source or the preview shadow.
- `.glyphs` sources only (decision 0013); a designspace project keeps today's warning.
- Copy: the always-visible warning becomes the resting-origin note ("The sources rest at the compiled origin; the studio's font relocates its default to the ORIGIN row, so the default row differs there"); the README.txt lists spacing under "Composed into the masters"; the CLI note changes accordingly; still no sidecar/JSON/CSV file names in the UI.
- **E2 report, measurement only (no pipeline change):** build Crispy with the same twins and braces in the preview shadow, rounding and slant on, and record in LOG.md `g`'s gvar tuple count and header bytes under relocation at the resting style and at `wght=400,opsz=14`, plus build time — the number that decides whether the studio pipeline could ever move to source level. No switch without a user decision.

Acceptance: (1) the designspace carries a `SPAC` axis with the twin sources and the Kinetikern braces as sparse sources; fontmake builds it with zero ERROR lines and `avar` version 2 (E1a's asserts stay). (2) At every non-default mapping row's user location, advances and sidebearings of `a`, `o`, `g`, `w`, `i` in the fontmake font match the studio's TTF within **1 u** at `SPAC = 0`, and at `SPAC = max` the per-glyph advance change matches the injector's within 1 u. (3) For every Kinetikern-spaced style, every corrected glyph's LSB and advance match the studio's TTF within 1 u at that style; with Kinetikern switched off, no braces are written. (4) The CLI zip is byte-identical in content to the dialog's for the same state. (5) Outlines at every row still within 3 u (E1a). (6) The E2 report numbers are in LOG.md.

## Verifier review of the ROUND 22 checkpoint `5e28b47` (2026-11-02) — O1, O2, E1a, E2a–d, A1, A2 VERIFIED; E1 and E2 FAIL on the real Crispy (two defects the mini fixture cannot show); D1 fails three of its five checks; NOT verified, B11 stays gated

**Automated pass** (`~/avar2-verifier/reports/5e28b47.md`; fresh clone, own venv with Fontra, fontc, fontmake): pytest 654 passed / 2 skipped; ruff, eslint, tsc, unit, frontend build green; CLI build on a git-clean copy of the real Crispy exit 0 and clean after; probe **18/18** on 5073. The first run (11:40, kept as `reports/5e28b47.run1-node20.md`) was red — `build: FAIL` and every probe scenario timed out — because this session's shell had lost nvm's Node 22 and the watcher inherited `/usr/local/bin/node` 20.5.1, which Vite 7 rejects; the server then answered 503 on `/`. Verifier environment, not the code; `verify-checkpoint.sh` now pins the Node path. CI: Test run 41389622037 on the push commit 6e7da10, six checks green (confirmed with `gh run view`).

**VERIFIED**
- **O1** (2ebfbb8, 267534a): the flag is set where the relocation target is computed, condition `relocate_at_build and avar2_cfg_early and not target and not maps_declared_axis` (the pre-existing S1(b) variable, hoisted so it exists when the block is skipped); `health.degraded`; the amber `.build-degraded-banner`; warning log; `_load_axis_metadata` warns with the path when it creates `{}`; `build_jobs._apply_build_state` carries the key over the worker boundary. `tests/test_build_degraded.py` runs the real worker (no sibling → reason names `CrispyMini-axis-metadata.json` and "compiled corner", status stays `ok`; sibling present → `None`; a declared axis the mapping never uses still counts as missing). CLI exit 2 without metadata pinned at `tests/test_cli_failures.py:103-105`. Probe `degraded-banner` PASS.
- **O2** (0b4fe2f): copy, keep, log line; `test_a_legacy_file_is_copied_not_moved` (both files present with equal content, neither rewritten on the second access).
- **E1a** (5e29181): `_axis_name` on input and output dimensions; the round trip asserts avar 2.0, `VarRegionList.RegionCount == rows − 1`, no ERROR lines, outlines ≤ 3 u and advances ≤ 1 u at every non-default row (agent: 2.18 u / 0.55 u). My own measurement on crispy-mini (`measure.py` in the verifier scratchpad): fontmake at the default row's USER location against the unmapped fontc build at ITS default: **0.00 u** outlines and advances — the sources rest exactly on the compiled origin. The test's part (d) compares against the masters instanced at the row's OUTPUT (8.12 u, advances 11.63 u) and its comment blames "the avar2 built from the 7 mapped rows interpolates across it"; the number is the resting dip (origin 2/2 vs the row's 6/6), not interpolation → E2f below.
- **E2a / E2b renderers** (1f5d264, 026b722) on crispy-mini: SPAC 0→40 and 0→−20, advance change AND LSB change against the injector's own output: worst **0.00 u**; absolute advances and LSB at SPAC 0 against the injected quiet build: 0.00 u. Kinetikern: `test_kinetikern_corrections_ride_the_sources` (LSB moves by `left`, advance by `left + right`, within 1 u; switch off → no braces). The `.notdef` ink-basis and UFO-level cloning facts in the commit text check out in the code.
- **E2c** (ed4a9ac): `g` carries **1,476** gvar tuples in the pass's own CLI build of the real Crispy (rounding + slant + SPAC on), matching the report; numbers are in LOG.md.
- **E2d** (1a595bf): probe `export-sources` asserts the resting note; PASS.
- **A1** (7e85745): the line is gone, `no-unused-expressions` is on, lint green. **A2** (8f6a2c3): decision 0015 exists and the index lists fifteen — two of its statements are WRONG (factcheck B, rows 0015:58-60 and 69-71) → D1a.

**FAILED — the two export items do not hold on the real Crispy.** Evidence, two independent zips at this commit: (a) `avar2-studio build --format sources` from the checkpoint's clone venv on a scratch copy of `~/Documents/Crispy/sources` (exit 0, copy clean after; `scratchpad/gate5e28b47/cli/`), and (b) the dialog's own zip downloaded by the probe from the 5073 studio on the Crispy copy (`~/avar2-verifier/exports/sources.zip`, 12:18). Both: 30,708 entries, 36 UFOs, 96 sources (60 sparse), 29 mappings, 15 axes, **0 SPAC twins**, **`DesignSpaceDocument.findDefault()` → None**.

- **E2e (blocking) — the spacing never reaches a shadowed source's export, dialog and CLI alike.** `Crispy-transforms.json` has `spac_widthaware` enabled (−100..100, bias 1.0, scale 1.25) and the pass's TTF carries SPAC, yet neither zip has a SPAC axis and the writer's log has no "SPAC as N metrics-only twin sources" line. Cause: `sources_export._spacing_transform(source_path)` → `transforms.config.entries(source_path)` → `sidecar_path_for` = `<dir of source_path>/<stem>-transforms.json`, and `source_path` is the ACTIVE source: in the worker the staged `src/active-Crispy.glyphs` (`build_jobs._stage` names it so; `build_worker._run_export_job` sets `state.GLYPHS_PATH = spec["glyphs_path"]`), on the CLI the transformed shadow `.avar2-studio/shadow/transformed/Crispy.glyphs`. Neither directory holds the sidecar. The Kinetikern sidecar is read from `state.ORIGINAL_PATH` and would have worked (Crispy's is `enabled: false`, 0 instances, so no evidence either way). crispy-mini has no control axes, so its active path IS the original and all seven unit tests pass over the defect.
  Fix: the transforms entries come from the ORIGINAL source (the worker already stages `<stem>-transforms.json` beside `original_path`; honour `transforms.config.override()` exactly as the build does). Pass `original_path` into `build_sources_zip` explicitly instead of deriving it from `source_path`. Tests: (i) unit — an active path named `active-<stem>.glyphs` in a job-style directory with the sidecars beside the original name → SPAC axis present, 16 twins on crispy-mini; (ii) the probe's `export-sources` on the Crispy copy asserts a SPAC axis with the sidecar's range and ≥ 1 twin source; (iii) the same through `pipeline._sources_pipeline` under the real worker if it is cheap.
- **E1b (blocking) — the designspace has no findable default source; fontmake fails on the real Crispy.** Both zips carry, beside the eleven real axes, `grad` (−10/0/10), `lcwd` (−1000/−1000/1000), `ymod` (−1000/−1000/1000) and `spac` (−40/0/40): the studio's auto-declared created-axis placeholders in `Crispy-axis-metadata.json` (`registered_tag` lowercase, `is_parametric: false`, `default: null`). `declared_user_axes` admits them; `existing = {a.tag for a in doc.axes}` compares case-sensitively against glyphsLib's uppercase tags; `_default_of` falls back to the MINIMUM when `default` is null and the tag has no traditional default; `axis.name = display_name` duplicates three names ('Grade', 'Lowercase width', 'Horizontal correction'). The default location then says 'Lowercase width': −1000 while every master sits at 0 → `findDefault()` None → `fontmake -m Crispy.designspace -o variable`: "Can't check compatibility: no source found at the default location of the designspace", exit 1 (`scratchpad/gate5e28b47/fm/fontmake.log`). Dropping the four axes from the XML gives the default source `Crispy-47-1-1.ufo` and no duplicate names. crispy-mini's metadata has no placeholders, so the suite is blind.
  Fix: a metadata entry becomes a designspace user axis only when no emitted axis has the same tag case-insensitively and no axis has its name; the studio's own created-axis placeholders (LCWD/YMOD/GRAD/SPAC/TST2 …) are never user axes in the export — masters, braces and E2's twins define them. `build_sources_zip` asserts unique tags (case-insensitive), unique names and `findDefault() is not None`, raising `SourcesError` otherwise. Tests: a metadata fixture carrying the placeholder entries → one axis per tag, unique names, a default source; the probe asserts `findDefault()` on the Crispy zip. The fontmake round trip of the real Crispy zip is too big for the suite; E1b's acceptance includes one manual `fontmake -m Crispy.designspace -o variable` of the Crispy zip by the agent, exit 0, recorded in LOG.md with the fvar axis list.
- **E1c — the CLI zip and the dialog zip differ (E2 acceptance 4), and both READMEs misreport.** Same entry list, different designspace bytes and README. The dialog's README: "Source file: active-Crispy.glyphs", sha256 of the STAGED copy, "Composed into the masters: round_corners, slant, spac_widthaware". The CLI's: "Source file: Crispy.glyphs", sha256 of the transformed SHADOW, "Composed into the masters: nothing" (`cli._write_sources` passes no `composed=`; the masters ARE the rounded and slanted shadow). Fix: the README names and hashes the designer's ORIGINAL file; the composed list is computed once by the writer from the original's sidecar (enabled source-stage transform types, plus the spacing summary), not passed in by each caller; a test builds the zip through both paths on the same state and asserts identical designspace XML, README and entry list. Also CLI.md:26 vs the code: the CLI names the zip after the source STEM, the dialog after the FAMILY — use the family in both and say so.
- **E2f** (nit, with E1a): fix the part (d) comment, add the 0.0 u identity assertion (fontmake at the default row's user location vs the unmapped build at its default), keep the ≤ 10 u dip bound with the number in the message.
- **E2g** (observation): `--format sources` still waits for and REQUIRES a successful full font build (`wait_for_idle` then `_failure_reasons` → exit 1 before the zip is written) although the comment says "needs no built font". Either document it in CLI.md ("the font build must succeed first") or skip the build when only sources are requested — the agent's choice, stated in the LOG.

**D1 — fails three of its five checks** (links and anchors resolve everywhere; the install wording is right; the decisions index lists fifteen):
- **D1a**: the two fact-check briefs re-run against this commit → `factcheck-5e28b47.md` (A: 7 WRONG/OUTDATED rows in the reader set, B: 43 in the appendices, plus the UNVERIFIABLE rows). Every row is fixed per the D1 rule, one commit per document naming the rows. The ones that change more than a sentence: (1) CLI.md 117-119 and decision 0015 say `release.yml` runs `doctor --strict` on the wheel and that strict fails on a missing accelerator — neither is true (`doctor.run`: strict fails only on `configured_missing`, an env override naming a missing file, or a failed `build_prereq`; the workflow has no doctor step). Fix the DOCS to the code — do not change strict, the cli-build CI job relies on it passing on a plain runner — and say in 0015 that nothing asserts wheel completeness yet (B11c will, below). (2) SIDECARS.md 10-11/24/25 and ARCHITECTURE.md 531-534 overstate "atomic and locked" for instance-renames.json, the worker's config.yaml, `save_font_atomically`, `result.json` and promotion, and contradict each other on config.yaml. (3) ARCHITECTURE.md 298-309/451 and 0007's Area / Where-it-lives describe the export by the rejected inserted-origin-master method (`_build_export_source` no longer exists). (4) Counts: 45 state globals, 656 tests / 622 functions, plugin tests 41 %, the python job is an OS matrix, six `v0.1.0.devN` tags exist (PyPI still has no project, checked 2026-11-02).
- **D1b**: the register grep (`/api/`, `_[a-z]+\(`, `.py`, `T[0-9]\.`, `tests/` over the reader set) returns **20** hits, not none: kinetikern.md 5 (a route, three test files, `routes/kinetikern.py`), grade.md 6 (`grade.py`, `braces.rs`, four route lines), transforms.md 5 (two Rust test files, a Python test, `LAST_TRANSFORM_ERROR` / `/api/health`), secondary-parametric-axes.md 1 (a route), README.md 1 ("your own `.py` transforms" → "your own Python transforms"), the two example READMEs one each (`docs/animate_*.py` → "the animation script in docs/"). Mechanism sentences become one line plus a link to ARCHITECTURE or CLI.md, as the rule says; the grep must return nothing.
- **D1c**: `secondary-parametric-axes.md` is **315** lines against the approved ~100. The Crispy walkthrough, the leak table and the recipes move to an appendix document (e.g. `docs/secondary-axes-walkthroughs.md`, linked from the guide and from docs/README.md under Appendices); the guide keeps the concepts and one walkthrough, ≤ ~150 lines. transforms.md (158) and authoring-instances.md (129) are accepted at ≤ ~160 once D1b's hits are gone; README 165 accepted.
- **K5** (nit, UI copy): the Kinetikern tab renders the server's 503 string "Kinetikern2 engine not built (native/kinetikern2/README.md)." — UI copy naming a repo file (`routes/kinetikern.py:30,143`, `KinetikernTab.jsx:459`). Reword to "The Kinetikern engine is not installed on this machine; see the Kinetikern guide." and pin it with the existing copy test.

**B11c amendment — the proof I specified proves nothing.** `doctor --strict` exits 0 with no accelerator installed at all. B11c's acceptance becomes: the release dry run's platform wheel, installed into a clean venv, contains `avar2_studio/bin/spac-inject` and `avar2_studio/bin/libkinetikern2.{dylib,so}`, and `avar2-studio doctor` (no `AVAR2_*` overrides set) lists both as found at the wheel path; `doctor --strict` exit 0 on that venv is reported but is not the proof. B11a–c stay gated until the ROUND 23 checkpoint is verified.

**Order for the agent (ROUND 23):** E2e → E1b → E1c (one commit each with its tests; then the agent's own fontmake build of the real-Crispy zip, exit 0, in LOG.md) → E2f → D1a (one commit per document, naming the factcheck rows it resolves) → D1b → D1c → K5 → `STATE: checkpoint` with the Test run on the push commit green in all six checks. My gate then re-runs: the CLI and dialog zips of the real Crispy (SPAC axis with twins, `findDefault()` not None, identical content), a fontmake build of that zip, the register grep, the line counts, and a spot re-check of the factcheck rows.

## Verifier early check of E2e at `cb24a2f` (2026-11-03) — E2e holds on the real Crispy (CLI path); new defect E2h: a symmetric SPAC range exports no negative side

Run: `avar2-studio build Crispy.glyphs --format sources` with the `cb24a2f` code on a git-clean scratch copy of `~/Documents/Crispy/sources` (exit 0, copy clean after). Test workflow run 41621073996 on `cb24a2f`: six jobs green.

**E2e — holds on the CLI path.** The zip now carries `SPAC` "Spacing" −100..100 (the sidecar's range) with **36** metrics-only twin sources, one per master; the writer logs `SPAC as 36 metrics-only twin sources (spac_widthaware, -100..100)` and the README lists "the SPAC axis as metrics-only twin sources". The dialog path is checked at the gate (it needs the studio). Two things this run shows that the next items must cover:
- the lowercase `spac` placeholder (−40/0/40, name "Spacing") now collides with the real `SPAC` by name as well — four duplicate pairs (grad, lcwd, ymod, spac), so every `<dimension name="Spacing">` in the designspace is ambiguous. E1b's case-insensitive rule removes it; E1b's test must include the `spac`/`SPAC` pair.
- the CLI README's sha256 is the hash of `.avar2-studio/shadow/transformed/Crispy.glyphs`, not of the designer's `Crispy.glyphs` (different hashes) — E1c as written.

**E2h (blocking, new) — with a symmetric range the exported font has no negative spacing.** `_add_spacing_axis` writes `ends = [hi] + ([lo] if abs(lo + hi) > 1e-9 else [])`; its docstring says "The transform is linear in SPAC, so one twin per end defines the axis: at the maximum always, and at the minimum when the range is not symmetric." That is wrong: varLib does not extrapolate across the default. A master only at +1 gets the support `(0, 1, 1)`, so every negative SPAC value interpolates to SPAC 0 (`VariationModel([{}, {"SPAC": 1.0}])` → 500, 500, 500, 510, 520 at −1, −0.5, 0, 0.5, 1). The injector writes two regions, `(−1, −1, 0)` with `lo` and `(0, 1, 1)` with `hi`, so the studio's TTF tightens and the fontmake build does not. Crispy's range is −100..100 — exactly this case.

Repro on crispy-mini (the `cb24a2f` writer, fontmake 3.12.2; advance change vs SPAC 0 at the default location; injector = `WidthAwareSpacTransform` on the quiet build):

| range | glyph | sources @lo | injector @lo | sources @hi | injector @hi |
|---|---|---|---|---|---|
| −40..40 | a | **0.0** | −88.0 | 88.0 | 88.0 |
| −40..40 | o | **0.0** | −90.0 | 90.0 | 90.0 |
| −40..40 | w | **0.0** | −94.0 | 94.0 | 94.0 |
| −20..40 | a | −44.0 | −44.0 | 88.0 | 88.0 |
| −20..40 | w | −47.0 | −47.0 | 94.0 | 94.0 |

The ROUND 22 renderer checks passed only because crispy-mini's sidecar is asymmetric (−20..40), so both twins were written.

Fix: always write a twin at each non-zero end (`lo` and `hi`), whatever the symmetry; fix the docstring. Acceptance: a test with a SYMMETRIC range (e.g. −40..40) that builds the exported sources with fontmake and asserts the advance change at SPAC `lo` equals the injector's within 0 u for the E2 glyph set (and `hi` still does); the real-Crispy zip carries **72** SPAC twins (36 masters × 2). Watch the source count: the zip goes from 132 to 168 sources — say in LOG.md whether fontmake's build time on the real Crispy zip (E1b's manual build) stays workable.

**E2h memory budget.** The verifier's CLI `--format sources` run on the real Crispy (full font build, then the writer deep-copying 36 masters in memory for the twins) took about **6 GB** of RAM and slowed the designer's 16 GB machine (user report, 2026-11-03). E2h doubles the twins, and the dialog export runs the same writer inside the studio process. Measure peak RSS of the real-Crispy export before and after E2h (`/usr/bin/time -l`, "maximum resident set size") and put both numbers in LOG.md; if E2h raises it materially, build and write each twin to disk one at a time instead of holding all the deep copies at once.

**Order (ROUND 23, amended):** E1b → E2h → E1c → E2f → E2g → D1a → D1b → D1c → K5 → checkpoint.

## Verifier review of E1b `b6d9e83` (2026-11-04) — E1b VERIFIED; the real-Crispy round trip finds E1d (every mapping off), E2i (spacing amounts) and E2j (components); the composite rule needs the user

**Method — no new real-Crispy build** (it costs ~6 GB of RAM on the designer's machine). Compared, with fontTools glyph sets and HarfBuzz (uharfbuzz 0.57), the agent's own fontmake build of the `b6d9e83` real-Crispy zip (`~/e1b-check/fm/Crispy-VF.ttf`; its CSV is byte-identical to `~/Documents/Crispy/sources/Crispy-avar.csv`) against the studio's TTF of the 5e28b47 gate run (`~/avar2-verifier/out-5e28b47/`). Control: with the studio's default parametric point set explicitly, the two fonts agree within 0.5 u advance / 0.9 u bounds for n a g o H e, at the default and with slnt −11, ROND 100, GRAD 10. The masters and outlines are right; what follows is mappings and spacing.

**E1b — VERIFIED** (b6d9e83). The exported designspace (`~/e1b-check/rt/Crispy.designspace`) has 12 axes, unique tags and names, one `SPAC`, no lowercase placeholders; fontmake exit 0, avar 2.0; `_validate_designspace` and the placeholder test are in. 658 tests, CI 41641912202 green.

**E1d (blocking, new) — every mapping in the fontmake build lands in the wrong place.** Through `wght/wdth/opsz` alone:

| row Wide 900 MinOpsz | `n` advance | XTRA normalized | XOPQ | YOPQ |
|---|---|---|---|---|
| studio TTF | 4552.1 | 0.853 (= CSV 1560.8) | 0.237 | 0.729 |
| fontmake build of the zip | **1807.4** | **0.084** (expected 0.908) | **0.240** (expected 0.342) | **0.406** (expected 0.848) |

Across the 30 CSV rows the worst advance difference (a g o n H e W) is 4,913 u, the median 1,913 u. With the parametric point set explicitly, the export gives the studio's 4552.1, so the avar2 table is at fault, not the masters.

Root cause: CSV line 27, `Wide 400 MinOpsz,1420.1,149.9,122.1,,,,14.1`, has parametric outputs but no WGHT/WDTH/OPSZ (an unfinished row in the designer's file). `read_csv_mappings` gives it empty `in_axes`, and `_mapping_xml` writes `<mapping><input /><output>…1420.1/149.9/122.1…</output></mapping>`. An empty input IS the default location, so fontmake offsets every other mapping by that output: XTRA 0.908 − 0.823 = **0.085**, XOPQ 0.342 − 0.102 = **0.240**, YOPQ 0.848 − 0.442 = **0.406**, exactly the observed values. The studio's build never does this: `config_generator._fill_identity_in_axes` fills such a row from the instance's own coordinates, or makes it an identity mapping.

Fix: the export takes its rows through the same preparation the build uses (the same function, not a copy), so the designspace mappings are the studio's avar2 rows. It never writes a mapping whose input is empty or sits at the default location; `_validate_designspace` rejects both, naming the row. Test: crispy-mini with an input-less row added.

**E2i (blocking, new) — the spacing amounts use the wrong ink basis.** The injector normalises against the ink widths of the built font at ITS default, which on Crispy is the relocated default (decision 0001, the `Default 400 MaxOpsz` design). `_add_spacing_axis` measures the default SOURCE, the 47-1-1 hairline master. Mean ink: studio basis **1749.8 u**, export basis **96.3 u**. Advance change SPAC 0→100 at the default (simple glyphs):

| glyph | i | n | o | e | H | m |
|---|---|---|---|---|---|---|
| studio TTF | 58 | 258 | 266 | 250 | 306 | 410 |
| export | **6** | 246 | 254 | **202** | **254** | 426 |

Each basis predicts its own font to the unit (`2 × round(100 × ink/mean × 1.25)`), so this is the only cause for simple glyphs. On crispy-mini the default row sits at the compiled origin, so the two bases coincide; that is why the ROUND 22 renderer check read 0.00 u. Fix: measure the ink basis on the design the injector sees, the studio's default (the default row's location). Do it from the quiet build the pipeline already makes, or by interpolating the sources there.

**E2j (blocking, new) — components break the twin arithmetic.** Per-side change SPAC 0→100 at the default, HarfBuzz (fontTools glyph sets give the same numbers for the export):

| glyph (what it is) | studio TTF adv / L / R | export adv / L / R |
|---|---|---|
| C (simple) | +306 / +153 / +153 | +246 / +123 / +123 |
| Cacute (C + acute) | +153 / +153 / 0 | +123 / **+246** / **−123** |
| m (simple) | +410 / +205 / +205 | +426 / +213 / +213 |
| w (flipped m) | +410 / +205 / +205 | +213 / **0** / +213 |
| M (composite) | +221 / +221 / 0 | +223 / **+446** / **−223** |
| W (flipped M) | +442 / +221 / +221 | +223 / **−223** / **+446** |

- **Transformed components.** The studio decomposes them (`pipeline.py:927`, `--decompose-transformed-components`), so w and W are simple glyphs there and track on both sides by their own ink. The export keeps them as composites and applies the composite rule. w opens on the right only, and W's flipped M carries M's own shift mirrored, so W's outline moves LEFT.
- **Plain composites are shifted twice.** `_shift_metrics` moves the base glyph's contours by its `d`, which every composite using it inherits, and then the composite is shifted by its own `d`. Cacute opens +246 on the left and CLOSES −123 on the right; M does the same at twice the scale. The export TTF has 118 composites against the studio's 106; 112 of 207 inked glyphs miss the writer's own rule (fontTools).
- Fix: a twin reproduces the studio's font glyph by glyph. Components the studio decomposes are decomposed in the twin, or treated as simple glyphs. A composite moves only by its own amount, with the base shift compensated, and mirrored for a flipped component. The composite TARGET waits for the user (next paragraph). Do the rest meanwhile.

**For the user — the studio's own composite rule (pre-existing, outside the approved scope).** In the studio's TTF, composites do not track like their bases. Cacute gets +153 on ONE side while C gets +153 on EACH side. M (a composite) gets +221 on one side while its mirror W (decomposed) gets +221 on each. Which side moves depends on the renderer: HarfBuzz says left, fontTools' glyph set says right. The injector's comment ("moving the LEFT phantom … opens BOTH sidebearings equally") holds in neither. Recommendation: a composite tracks exactly like its base glyph, the base's amount on both sides. This changes the injector and the export together, so it needs the user's approval; until then E2j's composite targets are open.

**Round-trip acceptance for E1d, E2h, E2i and E2j together.** Commit a comparison script in this folder. It builds the real-Crispy zip with fontmake and compares it, with HarfBuzz, to the studio's TTF of the same copy:
- at every non-default CSV row, the renormalized parametric location matches within 0.001, and every glyph's advance and extents match within 3 u;
- at SPAC `lo` and `hi` at each of those rows, every glyph's advance change and both sidebearing changes match within 1 u.

Put the numbers in LOG.md. crispy-mini's tests gain the four blind spots: a flipped composite, an accented composite, a symmetric SPAC range and an input-less row. Real-Crispy builds take ~6 GB: run one at a time and never alongside the probe studio.

**Order (ROUND 23, amended):** E1d → E2h → E2i → E2j (composite targets once the user decides) → E1c → E2f → E2g → D1a → D1b → D1c → K5 → checkpoint.

## User decision (2026-11-05) — composites track exactly like their base letter → E2k

The user answered the open question from "Verifier review of E1b `b6d9e83`": **a composite tracks exactly like its base letter.** Today the studio's own font gives Cacute +153 on one side while C gets +153 on each side, and M (a composite) one side while its mirror W gets both.

**E2k (approved, new; start after the verifier's verdict on the ROUND 24 checkpoint):**
- **Rule.** For every composite glyph, the SPAC change on EACH side equals its base glyph's: advance +2·d_base, left +d_base, right +d_base. The base is the component flagged USE_MY_METRICS, else the first component. Flipped and transformed composites (w = flipped m, W = flipped M) follow the same rule with their base's amount.
- **Scope.** The studio's injector (both the native and the fontTools path) and the sources export, together. Today's export targets match the old injector behaviour (E2j), so both must move.
- **Acceptance.** On the real Crispy, at every non-default CSV row and at SPAC `lo` and `hi`, every composite's per-side change equals its base's within 1 u in the studio's TTF and in the fontmake build of the exported zip. Measure with HarfBuzz. fontTools' glyph set disagrees with HarfBuzz on composite phantom points (Cacute: HarfBuzz left +153, fontTools right +153), so say in LOG.md which renderer each number comes from, and report FreeType if it is available. Extend `roundtrip_compare.py` and the crispy-mini tests with an accented composite and a flipped one.

## Verifier gate review of the ROUND 24 checkpoint `c1c8aca` (2026-11-05) — NOT verified: the spacing round trip fails over ALL glyphs, the CLI exits 0 on a degraded build, D1 finds new rows

**Automated pass** (`~/avar2-verifier/reports/c1c8aca.md`): pytest 666 passed / 2 skipped; ruff, eslint, tsc, unit and the frontend build green; the CLI build of a git-clean real-Crispy copy exits 0 and leaves the copy clean; probe **18/18 PASS**. CI: the checkpoint's own run (a9b658b) was cancelled by the verifier's docs push (a853c26, same code), and the run on a853c26 (41877996684) is marked failure by a runner glitch. Every step that ran there was green (pytest 2 min on ubuntu, cli-build green), but the macOS python job never got a runner and was cancelled at 15 min. The verifier re-ran it; cite the re-run at the next checkpoint.

**Method — all glyphs, not nine.** The dialog's zip (downloaded by the probe at 00:31) was built with fontmake: exit 0, 0 ERROR lines, 31 s, peak RSS 0.46 GB. That build was compared with the studio's TTF from the same copy (`~/avar2-verifier/out-c1c8aca/`) over all **496** common glyphs, by glyph id, with HarfBuzz, at the non-default CSV rows. The scripts are `~/avar2-verifier/scripts/allglyph_compare.py` and `spac_breakdown.py`. Row `Default 1 MaxOpsz` is excluded: the probe's `sidebar-edit-saves` scenario drags its XOPQ (5.0 → 275.3) before `export-sources` downloads the zip, so the zip carries the edit and the pre-probe TTF does not. That is a probe-ordering artifact, not a product defect. The copy's CSV is clean afterwards.

**VERIFIED**
- **E1d** (aab8092): 27 rows × 496 glyphs, advance/lsb/rsb/ink worst **3.0 u**, none over 3 u. The designspace has 29 mappings, none with an empty input or an input at the default location.
- **E2h** (c32a60b), structure: **72** SPAC twins, 36 at −100 and 36 at +100; 168 sources.
- **E1b** (b6d9e83, earlier) holds at this commit: 12 axes, unique tags and names, default source 47-1-1.
- **E1c**, dialog half: the README names `Crispy.glyphs` with sha256 `e1042814…`, the designer's own file, and the composed list is computed. The CLI half and the byte-identity of the two zips were not re-run this round, because a real-Crispy CLI export is the ~6 GB job. `test_the_cli_zip_and_the_dialog_zip_have_the_same_content` exists. Next gate.
- **E2f** (24ccd8d) and **E2g** (a83072d, CLI.md:34 "needs the font build to succeed first").
- **D1b** (89be9a8): the register grep returns 0 hits, down from 20.
- **D1c** (187e64b): 149 lines, with the appendix linked from the guide and from docs/README.md.
- **K5** (c1c8aca): the 503 names no repo file.

**FAILED — spacing (SPAC 0 → ±100 change, studio TTF vs fontmake build, 27 rows × 2 ends × 496 glyphs).** There are **13,034** mismatches over 1 u: 2,588 at +100 and 10,446 at −100. The checkpoint's "SPAC +100 28/28 inside 1 u for every glyph including composites" holds only for the nine letters `roundtrip_compare.py` checks by default (`--glyphs aognHeWiw`).

- **E2i-b (blocking, new) — glyphs whose production name differs from the source name get no spacing, silently.** The ink basis comes from the built font (production names `Euro`, `uni0162`, `uni021A`, …), but the UFOs use source names (`euro`, `Tcedilla`, `Tcommaaccent`). `if name not in twin_font: continue` skips them. In the 47-1-1 twins, `euro` keeps advance 186 and xMin 10 at both ends, against the studio's ±380. Composites are worse: `Tcedilla` keeps advance 156, but its `T` component's base moved, so its outline moves right by 162. At +100 the right side CLOSES by 162 u. This affects `Euro` and about 43 composites, at every row and both ends. Fix: map built-font names to source names (glyphsLib's production names / `public.postscriptNames`), and raise when an inked glyph of the built font has no source glyph. Never skip silently.
- **E2j-b (blocking, new) — mixed glyphs (components AND contours) do not match.** `Q` is `O` plus a tail contour, and `cent` is `c` plus a bar. The studio's build flattens them, so they are simple glyphs in the TTF; the twin treats them as composites. They mismatch at both ends at every row. Fix: treat them as the studio does.
- **E2i-c (blocking; the target needs the user) — the −100 cap reaches the whole family.** At the 47-1-1 hairline master almost every glyph's −100 amount exceeds its advance (T 156, C 155, A 175 against amounts of ~150 per side), so `_fit_dx` caps them to ~1 u. That twin is the default master's, so the cap enters every location through interpolation. At **26 of 27 rows**, 79–90 of the 103 simple glyphs miss the studio by more than 1 u. Some rows under-tighten by up to **315 u** (`trademark`, Narrow 1 MinOpsz) and others over-tighten (the Default/Wide 900 rows). The checkpoint's "SPAC −100 matching except W at the narrow rows" is wrong, and the LOG's "up to ~134 u at a narrow row" understates it. Within the studio's current constant-per-glyph amounts, no twin set can reproduce the negative end. The studio's own TTF has **96** negative raw advances at Narrow 1 MaxOpsz (fontTools; HarfBuzz clamps them to 0). The user is deciding how the studio treats a tightening larger than a glyph's advance: clamp it, or scale the amount to each instance's own width. The export follows that decision. Until then, the README, LOG and STATUS state the cap's measured reach and claim no exactness at −100.
- **.notdef** (minor): the export gives 0 against the studio's ±114. Write a `.notdef` into the UFOs, or say in the README that a compiler-synthesised `.notdef` is not spaced.
- **README nits**: the resting note appears twice, and the composed list names spacing twice ("spac_widthaware, the SPAC axis as metrics-only twin sources").
- **roundtrip_compare.py**: iterate every glyph by glyph id, not codepoints. Report per class (simple / composite / mixed / unencoded / .notdef) and per end. Exclude or rebuild for any row a probe scenario edited.

**FAILED — CLI exit contract (code; from the D1 fact-check, confirmed by the verifier in the code).**
- **O1b (blocking, new) — a compiled-corner build exits 0.** `_failure_reasons` (`cli.py:355-405`) never reads `LAST_BUILD_DEGRADED`, the flag O1 introduced (`pipeline.py:980-984`). The CLI's own pre-check refuses missing metadata with exit 2 precisely so that a corner build cannot ship. Under `--workdir` there is a second path to the same result: the pre-check looks for the legacy file at `source.parent/.avar2-studio/axis-metadata.json` (`cli.py:60-71`), the pipeline at `WORKDIR/.avar2-studio/` (`pipeline.py:1770-1776, 1848-1851`). The check passes, the build writes `{}` beside the source (`pipeline.py:2022-2025`), rests at the corner and exits 0. Fix: the CLI and the pipeline read the same legacy path, and `_failure_reasons` reports `LAST_BUILD_DEGRADED` (exit 1, its message on stderr). Tests for both.

**FAILED — D1a (docs).** `factcheck-c1c8aca.md` is the D1a2 spec, with its convergence rule. Last round's rows are fixed except three (A: all seven fixed; B: 40 fixed, 3 still wrong). The deeper pass finds new rows: reader set ~30 plus register rows the D1b grep patterns miss, appendices ~45. The user-facing one comes first: **CLI.md's install recipe installs stale code.** GitHub's default branch is `main` at `a37ca53` (2026-09-12), where `build` has no `--config/--workdir` and `doctor` no `--strict`. Pin the branch the docs describe, or state the PyPI form once it exists.

**Order for the agent (ROUND 25):** E2i-b → E2j-b → O1b → E2k (the user's composite rule) → D1a2 → `roundtrip_compare.py` over all glyphs, with the numbers per class and end in LOG.md → checkpoint. E2i-c waits for the user's answer on negative widths and is NOT a checkpoint condition. Until it lands, the docs state its measured reach. Real-Crispy builds take ~6 GB: run them one at a time.

## User decision (2026-11-05) — the spacing amount scales with each instance's own width → E2i-c

The user approved the verifier's recommendation for tightening beyond a glyph's advance. Today the studio's SPAC amount per glyph is one constant, measured at the default design. At the thinnest, narrowest style it exceeds the glyph's advance: the studio's TTF has 96 negative raw advances at Narrow 1 MaxOpsz at SPAC −100, and the export's cap distorts 26 of 27 rows. From now on **the amount scales with the instance**.

**Rule.** For glyph g at design location L and SPAC value v, the per-side amount is

    d_g(v, L) = v × scale × (ink_g(L) / mean_ink(L))^bias × (mean_ink(L) / mean_ink(D))

- `ink` is the glyph's ink width.
- `mean_ink` is the average over inked glyphs, as today.
- `D` is the studio's default design, the relocated default the injector measures today.

At L = D this is exactly today's amount, so the default does not change. With bias = 1 it reduces to `v × scale × ink_g(L) / mean_ink(D)`: each glyph's change is proportional to its own ink at that style. A thin, narrow style therefore tightens and loosens proportionally less.

The font carries the amount exactly at the masters and interpolates between them. Composites take their base letter's amount at L (E2k). Per-instance SPAC overrides from the CSV are unaffected.

**Scope.** The studio's injector (native and fontTools paths) and the sources export, together. In the export each master's twin carries that master's own amounts, so the cap (`_fit_dx`) has nothing left to do. Remove it, or prove it never triggers: the count of capped glyphs is 0. The implementation is the agent's choice, within decision 0004's budget. Interaction tuples with the parametric master regions, or SPAC composed at source level as the export does, are both acceptable if the numbers below hold. Write the choice and the reason in LOG.md, with a decision record if it changes 0004's reasoning.

**Acceptance (real Crispy, all glyphs by glyph id, HarfBuzz for per-side changes, fontTools for raw advances):**
1. At the default row, every glyph's SPAC ±100 change equals today's within 1 u.
2. At every parametric master location, every glyph's per-side change equals the rule within 1 u, in the studio's TTF.
3. No negative raw advance for any glyph at any CSV row at SPAC `lo` or `hi`, in the studio's TTF and in the fontmake build of the export. Today the studio's TTF has 96.
4. The export round trip holds at BOTH ends: every glyph, every non-default row, within 1 u, with no cap.
5. Budget: the gvar tuple count of `g` against today's 1,476, the largest per-glyph tuple header against the 64 KB ceiling, and the real-Crispy CLI build time and peak RSS, before and after, all in LOG.md.
6. crispy-mini tests pin the rule at two masters and the default. The Transforms guide and any UI copy describing the width-aware spacing say what it now does, in the guide's register.

## Verifier check of the ROUND 25 pushes (2026-11-05 08:30) — E2k breaks the Test run → E2k-b

Pushed since the gate: `5fbae7a` E2i-b, `9471ca8` E2j-b, `f660c42` O1b (Test run 41885366553 green), `6085233` E2k (Test run **41888373892 red**). Those four items are reviewed at the next gate; this entry is about the red run only.

**Evidence.** Both `rust` jobs fail in `cargo test --manifest-path wasm/fontc-web/Cargo.toml`, test `spac_matches_studio_oracle`. Section 1 (width-aware vs `WidthAwareSpacTransform`) matches on all 64 glyphs at −20/0/20/40. Section 2 (uniform vs gftools `gen-spac` through `SpacTransform`) fails at −20, +20 and +40 on the same five glyphs, 40 u each, which is 2 × 20 at −20 and 2 × 20 at +20 and +40:

```
✗ SPAC -20 (5 differ: {'U': (130, 170), 'W': (283, 323), 'u': (126, 166), 'w': (234, 274), 'nine': (116, 156)})
✗ SPAC +20 (5 differ: {'U': (210, 170), 'W': (363, 323), 'u': (206, 166), 'w': (314, 274), 'nine': (196, 156)})
✗ SPAC +40 (5 differ: {'U': (250, 170), …})          (wasm, oracle)
```

(The `only one SPAC transform can be enabled` line and the wasm-bindgen panic above it come from the deliberate `TRANSFORMS_BOTH` rejection check, not from this failure.)

**Root cause.** `6085233` changed `uniform_extras` in `wasm/fontc-web/src/spac.rs` to include composites (the LOG says "the uniform `spac` transform now includes composites too"). The Python path for `spac` shells out to gftools `gen-spac`, which skips composites (`if not hasattr(glyph, "coordinates"): continue`). Those five CrispyMini glyphs are non-`USE_MY_METRICS` composites, so they now track in the native engine and stay fixed in the Python one. The mode the user picks as "Spacing — uniform (gftools)" now gives a different font depending on which engine runs it. The LOG's "74 passed" ran `pytest -k spac`, which does not run the cargo oracle, so the break was not seen locally.

**E2k-b (approved fix, smallest safe change).** Restore `uniform_extras` to gftools parity: composites skipped, exactly as before `6085233`. Keep the E2k change in `widthaware_extras`, the fontTools fallback and the export: the user's rule is about the studio's width-aware spacing, which is the mode the export writes. Add one sentence to the uniform row of `docs/guides/transforms.md`: like gftools, the uniform mode leaves composites that are not decomposed at their own width; the width-aware mode spaces them like their base letter. Acceptance: `cargo test --manifest-path wasm/fontc-web/Cargo.toml` passes locally and the LOG says so; the Test run on the push is green in all six jobs. From now on, any change to `wasm/fontc-web/src/spac.rs` runs that cargo test before its push.

If the user wants the uniform mode to follow the composite rule as well, that is a separate decision. The Python path would then post-process gftools' output, the oracle would change, and the mode would no longer be "gftools".

### User decision (2026-11-05) — the uniform mode keeps gftools parity and its label says it is best with decomposed composites → E2k-b

The user: "we just need a label that tells designers that the tool is best with decomposed composites." So the uniform mode does NOT take the composite rule; E2k-b restores gftools parity as written above. The label is part of E2k-b:

- The uniform mode's description, in the Python spec (`builtin_spac.py`, `SpacTransform.spec.description`) and in its static-site mirror (`frontend/src/static-api.js`, `spac.description`), tells the designer the mode is best with decomposed composites, because composites that are not decomposed keep their own width. The current text, "every glyph tracks by the same amount", is untrue for those composites and goes. Suggested copy (the agent may tighten it, but not change its meaning, and not name any file or format): *"Inject a SPAC axis via gftools-gen-spac. Best with decomposed composites: like gftools, it tracks every outline glyph by the same amount and leaves composite glyphs at their own width."*
- The uniform row of `docs/guides/transforms.md` says the same thing in the guide's register.
- Acceptance: both descriptions are identical (a mirror test, if one exists, stays green); `cargo test` for fontc-web passes locally; the Test run on the push is green in all six jobs.

## Verifier note (2026-11-05 09:45) — the parked E2i-c export half lives only in /tmp

LOG (`81cf599`) parks the E2i-c export half as `/tmp/e2ic_export_half.patch` (5,035 bytes, 08:53). Nothing durable goes under `/tmp`, and a reboot would lose that work. The verifier keeps a copy at `~/avar2-verifier/saved/e2ic_export_half.2026-11-05.patch` (sha256 `f04dd1f02086a522…`, identical). Next loop: move the patch into the repo, as `docs/sprints/cleanup-2026-10/wip/e2ic_export_half.patch` in a sprint-docs commit, and point the LOG at that path. Do not apply it to the code before the injector half follows; the design note's reasoning stands. The E2i-c budget numbers (item 5 of its acceptance) are still owed at the checkpoint, with the corner-tuple design measured, not estimated.

## Verifier note (2026-11-05 11:50) — E2i-c: a cap in the injector changes the user's rule; measure the seven cases first

LOG `972858b`: the parked v3 work leaves an 8.02 u residual, and the export logs "7 per-glyph moves still hit the negative-advance cap" at SPAC −40 on the fixture. The hypothesis is to cap the injector's per-corner amounts too. Two things before any code:

1. **A cap is not the user's rule.** The user approved `d = v·scale·(ink_g(L)/mean(L))^bias·(mean(L)/mean(D))` with no cap. The point of the rule was that the cap never fires. If it still fires, the rule and the user's "no negative advances" requirement conflict there, and choosing between them is the user's decision, not an implementation detail. The same applies to capping both sides so they agree.
2. **Measure, then report.** List the seven moves: glyph (and its base, if composite), master location, that master's advance, ink and sidebearings, the rule's `d`, and the resulting advance. Then say which of these it is:
   - (a) a measurement or bookkeeping error (wrong ink, wrong width passed to `_fit_dx`, a composite read with its own ink, a production/source name mix-up). Fix it and the cap stops firing.
   - (b) the rule itself drives the advance below 1 u. At bias 1 the change is `−2·|v|·scale·ink_g(L)/mean(D)`, so this can only happen where ink exceeds about `advance·mean(D)/(2·|v|·scale)`, which in practice means overhanging glyphs (negative sidebearings) at hairline masters. Then run the same count on the real Crispy at its −100 end, from the existing artifacts if possible, and put both counts in LOG.

   If it is (b), park E2i-c at that point and set `STATE: blocked` with the numbers, so the verifier can bring the choice to the user. Do not add the injector cap.

Also: the v3 WIP went into the shared stash again (`24e89bb`, after the 11:40 note). The verifier has a copy at `~/avar2-verifier/saved/e2ic_wip_v3_stash_24e89bb.patch`. Restore it by SHA. Next time, a commit on a local branch.

### User decision (2026-11-05) — E2i-c gets a 1 u width floor, the same in the injector and the export → E2i-c acceptance amended

The user chose option 1 ("option 1 it is"), after the verifier explained what it influences. **Where the scaling rule would take a glyph's advance below 1 u at a master corner at the SPAC tight end, the corner amount is floored so the advance is exactly 1 u.** Everything else follows the rule exactly. This replaces the 11:50 note's "do not add the injector cap": the cap is now the user's rule, with the conditions below.

The rule, implemented once and used by both renderers:

- **Where:** the per-corner amount at the SPAC `lo` end only. The `hi` (loosening) end, the default and SPAC 0 never floor. The floor is 1 u, the same value as the export's `_fit_dx`. The corner values interpolate as today. The user accepts that a floored glyph tightens more slowly over the whole tight half at that style, and that the reduction fades towards the neighbouring masters.
- **Both sides identical:** the fontTools injector path and the export apply the same floor to the same corner values. The set of floored (glyph, corner) pairs is identical in both and is logged by both. The native engine keeps declining until it is ported.
- **Composites:** a composite takes its base's floored amount (E2k), and its own floor as well if that still leaves it below 1 u. Both cases are listed.
- **The condition (the user's, through the verifier's recommendation):** the floor is for overhanging glyphs. A floored (glyph, corner) where the glyph has a negative sidebearing, or ink wider than its advance, at that corner is expected. **If any floored glyph has no overhang at that corner** (both sidebearings ≥ 0), the range is too tight for that style: list those glyphs, set `STATE: blocked`, and the verifier brings it to the user (option 2: a shallower tight end). Do not ship the floor over them.
- **First, the bookkeeping check from the 11:50 note still applies:** any of the seven fixture cases that come from a measurement error are fixed, not floored.

**Amended E2i-c acceptance.** Items 1, 2, 5 and 6 are unchanged. Items 3 and 4 become:

3. No raw advance below 1 u for any glyph at any CSV row at SPAC `lo` or `hi`, in the studio's TTF and in the fontmake build of the export (fontTools, unclamped).
4. The export round trip holds at BOTH ends: every glyph, every non-default row, within 1 u. The floor is the only departure from the rule. LOG lists every floored (glyph, corner) with the rule's amount, the floored amount and its sidebearings, on the fixture and on the real Crispy at −100.

## Verifier spot check of `d3ba64c` (2026-11-05 15:50) — the 8 floored moves come from the slant, and the injector and the export disagree at slanted styles

**Verified:** the classification log (`test-output/e2ic-diag/classify_transformed.log`). It shows 8 floored pairs, colon and exclam, all overhanging, and the export's UFOs agree.

**Evidence.** These come from the export's own UFOs, in `classified-transformed.zip`, measured with ufoLib2 and BoundsPen. At every derived **Slanted** master, each glyph keeps its upright left sidebearing and its advance, and the right sidebearing takes the whole lean:

| master | glyph | upright lsb / rsb | slanted lsb / rsb | ink upright → slanted |
|---|---|---|---|---|
| 47-1-1 | H | 40 / 40 | 40 / −271 | 98 → 409 |
| 47-1-1 | l | 35 / 35 | 35 / −276 | 2 → 313 |
| 47-1-1 | colon | 11 / 11 | 11 / −223 | 14 → 248 |
| 47-1-1 | exclam | 17 / 17 | 17 / −294 | 2 → 313 |
| 47-1-1 | period | 11 / 11 | 11 / 8 | (sits on the baseline) |
| 47-1462-275 | H | 100 / 100 | 100 / −153 | 5942 → 6195 |

Rounded masters match upright, apart from 1–2 u. In running text this is harmless: every glyph leans by the same amount at each height, so the gaps between letters are unchanged. But a sheared bounding box is not the glyph's width.

**Consequence 1: the 8 floored moves are a slant artefact.** The export's E2i-c amount uses each of its 36 masters' own ink, so the slanted masters get the sheared ink. Colon at 47-1-1 Slanted gets `d = −100·1.25·248/1754.5 = −18`, which needs the floor. Upright it gets `−100·1.25·14/1754.5 = −1`, giving advance 34. At the hairline styles the slanted amounts are about 4× upright for H and 18× for colon.

**Consequence 2: the studio's font and the export disagree at every slanted style.** This is from reading the code, not yet measured. The injector's corners are `pipeline._master_locations`, the 9 masters of the `.glyphs` source. `slnt` and `ROND` are transform-injected, so they are not corners, and the injector's amount does not change along them. The export's twins measure the slanted ink. Calculated for the hairline H at SPAC −100: injector −7 per side, export −29 per side, a 44 u advance difference. The all-glyph round trip at slanted rows will show this. The fixture has no derived masters, so the 8.02 u fixture gap is a separate question.

**Recommended fix (awaiting the user's decision; do NOT change the rule yet):** the spacing amount comes from the parametric masters only. Each transform-derived master (slanted, rounded, slanted-rounded) takes its parent master's amounts, which the injector already does, and the export follows. Expected effect: the floor has nothing left to do on Crispy (it stays as a safety net), and slanted rows agree. Meanwhile, step (2), the injector floor, goes ahead unchanged.

## Verifier finding (2026-11-05 15:57) — E2i-c corners are normalized against the SOURCE default, so Crispy's narrow/hairline half gets the default's constant amount

**Reproduced** with the `e2ic-wip` injector (`9e8801f`, archived read-only to `~/avar2-verifier/samples/code`) on the agent's real-Crispy build without SPAC (`rt-e2ic`). The project's params are −100…100, bias 1, scale 1.25. Corners come from `pipeline._master_locations(Crispy.glyphs)`:

```
{} {YOPQ:1} {XOPQ:.239,YOPQ:.927} {XOPQ:1} {XTRA:1} {XTRA:1,YOPQ:1} {XTRA:1,XOPQ:1} {XOPQ:1,YOPQ:1} {XTRA:.914,XOPQ:.478,YOPQ:1}
```

Every corner is on the + side. `_master_locations` normalizes each master against the source designspace's own axis default, which is the 47-1-1 master at the minimum. The built font's default is the studio's relocated one: XTRA 663.1, XOPQ 202.5, YOPQ 121.8. So in the font, the masters at XTRA 47, XOPQ 1 and YOPQ 1 are at **−1**, and no SPAC tuple covers that half. There, every glyph gets the default's amount, which is exactly the pre-E2i-c behaviour. At `Narrow 1 MaxOpsz` (XTRA 47, XOPQ 2, YOPQ 2), SPAC −100, raw advances measured with fontTools:

| glyph | SPAC 0 | default's amount per side | raw advance at −100 |
|---|---|---|---|
| H | 182.1 | 152 | **−121.9** = 182.1 − 2·152 |
| n | 170.0 | 128 | **−86.0** = 170 − 2·128 |
| period | 36.3 | 22 | **−7.7** = 36.3 − 2·22 |

(HarfBuzz shows 0, because it clamps.) The crispy-mini fixture does not relocate its default, which is why the tests pass.

**The fix is verified in a sample (it is not the agent's code).** Normalize the masters against the built font's `fvar`, using its relocated default, and its `avar` segment maps. That gives 10 corners: the default plus 9 masters, with the narrow/hairline masters at −1. With the same injector:

- 0 raw advances below 1 u over 112,080 glyph checks: 120 named styles (the user's 30 CSV instances × upright/Slanted/Rounded/Slanted Rounded) at SPAC −100 and at each instance's own SPAC, with glyphs that are zero-width at SPAC 0 excluded. **The floor never fires.**
- The rule holds at the narrow end: H at `Narrow 1 MaxOpsz` is 164 / 182 / 200 at −100 / 0 / +100. The default is unchanged: H 2124 / 2428 / 2732.
- The HarfBuzz (HVAR) and fontTools (gvar) advances agree to 0 u. Slanted and rounded changes equal upright to 0 u.
- Cost: injection in 6.9 s, 0.65 GB peak.

Script: `~/avar2-verifier/samples/make_sample.py`; the correction is the block marked "Verifier correction".

**E2i-c-d (approved fix, no rule change):** corners must be normalized against the font being injected. The transform can normalize design locations itself from the font's `fvar`/`avar`, or the pipeline can normalize with the relocated default; the agent chooses. Add a crispy-mini case with a **relocated default**, which fails today and passes after the fix. Then re-check the 8 floored moves and the 8.02 u fixture gap, since both were measured with these corners.

Also: `e2ic-wip` commits binaries under `test-output/` (two ~9 MB TTFs in `9e8801f`). They must not reach the sprint branch; drop them when the branch is squashed or merged.

### User decision (2026-11-05 16:03) — slanted and rounded styles take their upright master's spacing amounts → E2i-c

The user judged the verifier's sample (`~/avar2-verifier/samples/Crispy-SpacingSample-proof.html`). It shows their 30 CSV instances, each Upright, Slanted, Rounded and Slanted Rounded at its own SPAC, with the corrected corners. Their verdict: "slants look good to me".

**The rule:** the E2i-c amount varies over the **parametric** design space only, meaning the source's masters, normalized against the built font (E2i-c-d). Every transform-derived master takes its parent master's per-glyph amounts exactly: Slanted, Rounded and Slanted Rounded, and any later derived axis by the same principle. The injector already does this. **The export follows it:** its twins at derived masters carry the parent's amounts, not amounts from their own (sheared) ink.

**Why (the user's model):** a shear moves every glyph by the same amount at each height, so the gaps between letters are unchanged, and a sheared bounding box is not a width. Per-instance spacing (the CSV's SPAC column) then means the same thing on a slanted instance as on an upright one, so a designer can tune a slanted instance with its own SPAC value.

**Acceptance (added to E2i-c):** at every derived master, every glyph's per-side twin delta equals its parent's within 1 u. The all-glyph round trip includes slanted and rounded rows and holds at both SPAC ends. The 1 u floor stays as the user's safety net, and LOG lists anything it touches (expected: nothing, once E2i-c-d is in).

## Verifier note (2026-11-05 16:27) — the export's parent lookup (`e2ic-wip` 3cad084) is name- and order-dependent, and it falls back silently

`sources_export._parent_name` strips " Slanted Rounded", " Slanted" or " Rounded" from the style name. The derived master uses the parent's amounts only `if parent in measured_by_style`, so only when the parent source comes **earlier** in `doc.sources`. Otherwise it silently keeps its own sheared measurement, which is exactly the behaviour the user rejected. A designer's own master whose name ends in " Rounded" would also be paired with another master by name.

**Before the checkpoint (part of E2i-c, no rule change):**
- Resolve parents **by location**: the source whose location matches on every axis except the transform-injected ones (`slnt`, `ROND`, …), with those at their defaults. Resolve in a separate pass after measuring all sources, so order does not matter.
- A derived master with no parent **raises**. It does not fall back.
- Add one test where the derived source comes before its parent, and one where a non-derived master's name ends in " Rounded".
- LOG the count, for example "27 derived masters → 9 parents", on the real Crispy.

## Verifier note (2026-11-05 17:40) — the avar axis-count defect predates E2i-c, is on `main`, and has a second form the new test cannot see

The agent found it (87fa900): adding SPAC to fvar left avar's segment-map count behind. The verifier confirmed the impact and measured how far it reaches.

**Impact, measured.** FreeType checks that avar's axis count equals fvar's. When they differ it ignores avar **and avar2**, so wght/wdth/opsz stop working; HarfBuzz tolerates the mismatch. H advance at wght 100 / 400 / 900:

| font | HarfBuzz | FreeType |
|---|---|---|
| unspaced Crispy build (avar 11 = fvar 11) | 1699 / 2428 / 2963 | 1699 / 2428 / 2963 |
| the same font with avar deleted (control) | 2428 / 2428 / 2428 | 2428 / 2428 / 2428 |
| verifier's spacing sample (avar 11, fvar 12) | 1699 / 2428 / 2963 | **2428 / 2428 / 2428** |
| studio build at c1c8aca (avar header **29**, fvar 12) | 1699 / 2428 / 2963 | **2428 / 2428 / 2428** |
| Crispy's tracked `fonts/Crispy[…SPAC…].ttf`, 2026-09-17 (avar 9, fvar 10) | 178 → 1466 across wght | **178 at every wght, wdth, opsz** |

**Reach.** Every SPAC-carrying TTF on this machine since 10-28 has a mismatched header, 37 of 37. That includes the builds verified at aa6b87f, 5e28b47 and c1c8aca, the agent's c1–c7 builds, and exports. The two SPAC-free builds are fine. `origin/main`'s injector has the same unguarded `fvar.axes.append`, and the comment says it was vendored from `gen_spac.add_spacing_axis`, which is where the September production font got it. **This is a defect in released code, not an E2i-c regression.**

**Two forms.**
- (a) **avar = fvar − 1**: the stale copy. Seen on the CLI and injector paths; 87fa900 fixes it by loading avar so fontTools recompiles it.
- (b) **avar header 29, fvar 12**: every full studio-pipeline build (`.avar2-studio/build/…`, `out-*`). There are only 11 real segment maps, then the avar2 offsets; yet avar's own VarStore is already padded to 12 axes, so some step other than the old injector wrote it. The writer is not identified. Candidates are the Kinetikern injection, `_strip_injected_axes` (instancer), and the relocation's whole-font save.

**Verifier simulation:** loading the c1c8aca build, doing `avar.segments.setdefault("SPAC", {})` and saving rewrites the header to 12. FreeType then equals HarfBuzz at wght 100/900, wdth 25, opsz 14 and wght 900 × SPAC ±100: [1699, 2963, 1006, 2385, 2657, 3269] on both. So 87fa900's mechanism should repair (b) as well when SPAC runs, because SPAC is late in the chain. **That is a prediction, not a verified result.**

**The new test cannot see form (b).** bdcb670 asserts `set(font["avar"].segments) == set(fvar tags)`. fontTools pairs avar's maps with the fvar tags and drops the excess, so the c1c8aca font passes that assertion, 29-axis header and all.

**Before the checkpoint (part of E2i-c; it is a correctness fix, not a rule change):**
1. The test reads the **raw header**: `struct.unpack(">H", font.getTableData("avar")[6:8])[0] == len(fvar.axes)` on the saved file.
2. Add one FreeType-vs-HarfBuzz parity assertion (H advance at a non-default wght) if freetype-py is available in the test env. Otherwise state in LOG that it was checked by hand.
3. Name the writer of the 29 and fix it at the source, or show that it only occurs with SPAC present. A SPAC-off studio build must also have avar = fvar.
4. On the real Crispy: a **studio-pipeline** build and the `--format sources` export round trip both pass checks 1 and 2.

**Gate (added):** raw avar header = fvar axis count, and FreeType = HarfBuzz for H at wght 100/900, wdth 25, opsz 14 and SPAC ±100. Checked on the studio build, the CLI build and the export. Crispy's tracked production font is the user's to rebuild; this repo does not touch it.

## Verifier note (2026-11-05 17:55) — the A/B fork does not close the round-trip gap: the studio injector does not interpolate between masters the way the sources do

The agent attributes the 56 failing SPAC-end checks to one point: the relocated default, where the studio measures its own ink (H 152 u) and the sources interpolate (96 u). It offers (A) export a twin at the default, or (B) give the injector's default corner the interpolated value. **Neither changes the rows.**

**Cause, measured.** The injector builds `VariationModel(corners)` over 10 points: the relocated default plus the 9 masters, normalized against the built font. In that model each master's contribution is the product of its normalized coordinates, so on any axis line through the default the amount stays the default's. The sources' model, with its default at 47-1-1, interpolates across the whole box. The two agree only at the 10 points; every CSV row lies between them.

- **Real fonts** (rt-crispy, HarfBuzz). The row "Width 220, Weight 400" sits on an axis line through the default, and H there is 3489 u, 44% wider than at the default (2428). The change at SPAC −100 is:

  | font | at the default | at "Width 220, Weight 400" |
  |---|---|---|
  | studio, `out/` | −304 | **−304** (identical; O, n, o, a, e, m identical too) |
  | sources → fontmake, `fm/` | — | −432, in proportion to width |

  B would make the studio's value about −192 at that row (2 × 96), further from the sources' −432.
- **Structural test** (`~/avar2-verifier/scripts/model_fork.py`): Crispy's 9 real master locations and 29 mapping rows, with a known amount that is linear in XTRA/XOPQ/YOPQ. The sources reproduce it exactly at every row. The studio is off by up to 42.8 on a scale of about 90; on-axis rows get exactly the default's value. The worst gap is **42.8 today, 42.8 with B and 42.8 with A**: in this ideal case the default's measured and interpolated values coincide, so neither option touches the rows.

**What this means for the rule.** The exported sources follow the user's E2i-c rule between masters ("the spacing amount scales with each instance's own width"). The studio's preview does not: rows on an axis line through the default get the default's amount however wide they are.

**Recommendation (C), pending the user's OK.** The sources are the reference; they are what fontmake and Google Fonts build. The studio injector reproduces the sources' amount function: the source model over the masters, re-expressed in the built font's space the way the build-time relocation re-expresses outlines. That means corners on the grid of every master coordinate plus the relocated default per parametric axis, valued from the source model, which is at most 4 × 5 × 4 points on Crispy. The agent picks the mechanism. **Cost to measure first:** SPAC tuples per glyph against the budget in decision 0004 (g 1,476) and the 64 KB per-glyph gvar ceiling. If it does not fit, the user decides between a heavier font and an approximate preview.

**Acceptance (unchanged, and now reachable):** studio = sources within tolerance at all 28 rows × both SPAC ends × every class, with slanted and rounded rows included.

**Also, from the rt-crispy artifacts:**
- Two runs disagree. `rt.json` (17:44) has worst 397 u and `.notdef` 0. `roundtrip.json` and `failures.jsonl` (17:48) have worst 315 u and **`.notdef` 114 u at every row and both ends** (168 checks). Say which pair of fonts each run compared, and which is final.
- The top-level `rt-crispy/Crispy[…].ttf` (sha ae3325b3) still has the **header-29 avar**, and FreeType drops wght/wdth/opsz there. `out/` and the studio-pipeline build in `work/` (identical, sha cf971a15) have 12/12, and FreeType equals HarfBuzz within 1 u. Say where the top-level file came from. The raw-header test from the 17:40 note is still needed.
- Keep exactly one `STATE:` line in STATUS and put the detail in `NEXT:`. The verifier's scripts now read only the first `STATE:` line.

## Verifier note (2026-11-05 18:16) — B, as the user chose it ("amounts live at the masters and are interpolated everywhere else"), holds between masters only with a full corner grid; the 10-corner plan still fails the rows

**1. The interior-master gap does not reproduce on the committed code.** On rt-crispy `out/` (studio, bdcb670) against `fm/` (fontmake), HarfBuzz, H/O/n pinned at each of the 9 masters, the advances and the change at SPAC −100 agree within 2 u. That includes Black Narrow Max 47-350-255 (−212 / −212) and Max 900 1571-700-275 (−846 / −846). The LOG's 106.1 against 55.7 and 423.0 against 376.3 must come from the reverted attempt's build or from other points. State which. As written, "the interior masters disagree" is not a property of the committed injector.

**2. Fixing the default corner, in any coordinate space, leaves the rows wrong.** The plan in NEXT keeps one gvar corner per master plus the base. That model is flat along every axis line through the relocated default (17:55 note: row "Width 220, Weight 400" gets the default's change, −304, where the sources give −432). Giving the base the sources' value only moves the flat level; that row would then read about −192.

**3. What makes B hold everywhere (verified with the model test, `~/avar2-verifier/scripts/model_fork.py`).**
- Take the **corners on the grid** of every master coordinate plus the relocated default, per parametric axis: XTRA {47, 663.1, 1571, 1715} × XOPQ {1, 202.5, 350, 700, 1462} × YOPQ {1, 121.8, 255, 275}. That is 79 corners besides the base.
- **Value** each corner with the **sources' model**: the masters only, normalized against the designspace axes. It is still "amounts live at the masters", which is the user's B.
- The supports stay in the built font's normalization.
- Result: studio = sources to **0.000000** at all 29 mapping rows and at 2,000 random points. The 10-corner version is off by up to 128 at the random points.

This is the faithful implementation of the user's B. It adds no new rule.

**Cost to measure first.** The grid is an upper bound of 79 × 2 SPAC tuples per glyph, against 18 today. Drop all-zero tuples (YOPQ barely moves many advances), then report tuples per glyph, file size, injection time and peak RSS. Check the worst glyph against decision 0004's budget (g 1,476) and the 64 KB per-glyph gvar ceiling. If it does not fit, STATE: awaiting user decision, with the numbers.

**Also:** the reverted attempt is parked in `/tmp`, and the sprint rule is nothing durable under `/tmp`. Move it to `~/avar2-agent/`.

## User decision (2026-11-05 18:55) — the default lands on the default master; otherwise a bigger file, with a warning on export → E2i-c + new item E2l

The user's words: "yes i will do that. i'll know that without that i'll have a bigger file. drop a warning on export, considering also that on export we have qa checkbox for default specificity".

**What they confirmed:**
- In Crispy, the master the ORIGIN row lands on becomes the **default master in the source** (Glyphs: Variable Font Origin).
- The build's default move is then a no-op, and with spacing on, studio = sources **exactly with today's corners** (model test: 0.000).
- **No grid is needed for Crispy.**

**E2i-c (completes B):**
- **Coinciding case.** When the resting location is the source's default master, keep today's corners. **Acceptance:** run the real-Crispy round trip on a COPY whose ORIGIN row and default master coincide (do not edit the user's sources). It passes at both SPAC ends for every class, including slanted and rounded rows. Show the default move is a no-op there (no extra regions) and report the studio TTF's size.
- **Off-master case.** When the resting location is anywhere else, the user accepts a bigger file. Read as: the studio stays exact, using the corner grid (18:16 note: every master coordinate plus the resting location, per parametric axis, valued from the sources' masters-only model).
  - Measure tuples per glyph and the size against decision 0004 and the 64 KB per-glyph ceiling.
  - If the ceiling breaks, STATE: awaiting user decision, with the numbers.
  - The verifier has told the user this reading, and they can still choose approximate instead.

**E2l (new): a size warning on export.**
- **When it shows.** TTF/WOFF2 export, when the font's resting location is not the source's **default master**. The resting location is the ORIGIN row, or the "Set default at" location when that checkbox is on, and the warning follows the checkbox and the preview location live.
- **What it says.**
  - The file is larger because the studio rebuilds the variations around a style that isn't the default master.
  - The fix: if a master sits at that location, make it the default master in the source file (Glyphs: Variable Font Origin); otherwise add a master there and make it the default.
  - Always visible, like the sources note; not a tooltip.
  - UI copy names no sidecar, CSV or JSON files.
- **When it stays silent.** No warning when the location is the default master, within the existing location tolerance.
- **The sources note.** RESTING_NOTE ("the default row differs there") shows only when the ORIGIN row is NOT the default master, because otherwise it is false.
- **Backend.** It says whether the resting location is the default master, another master (its name), or no master, so the dialog does not guess.
- **Tests.** Default master / another master / no master, each with "Set default at" on and off, plus the sources note on and off. Also an e2e check that the text appears.
- **Evidence.** Report the TTF size of the coinciding copy against the current Crispy.

## Verifier note (2026-11-05 21:42) — ROUND 28: the avar repair holds; the coincide run built the wrong code, so (a) and (b) in NEXT are artefacts

**1. 932de1b (avar repair) — verified on real fonts.** `~/avar2-verifier/scripts/ft_hb_check.py` reads the raw header and compares FreeType with HarfBuzz advances (H n o W period trademark .notdef g m i) at 9 locations: default, wght min/max, wdth min/max, opsz min, SPAC min/max, and wght max + wdth min + SPAC min.

| font | raw avar / fvar | worst \|FT−HB\| |
|---|---|---|
| `rt-crispy/Crispy[…].ttf` (18:12, header 29) | 29 / 12 | **3,923** (wdth min: FT H 2428, HB 626) |
| `rt-cli-check/out/…ttf` (21:29, with the repair) | 12 / 12 | **0.0** |
| `rt-crispy/avar-recompile-check.ttf` | 12 / 12 | 0.0 |

- The repaired font's HarfBuzz advances equal the header-29 font's HarfBuzz advances at every location (wght min H 1610, wdth min 626). So the repair changes nothing HarfBuzz showed, and FreeType now agrees.
- Nothing is lost. The 17 extra "maps" in the header-29 table are not segment maps: the 13th holds 170 pairs whose from-values are not ascending (0, 0.0038, 0, 0, …). FT and HB never read them for any fvar axis.
- **The LOG's account of the writer does not fit the bytes.** fontTools compiling avar against a 29-axis fvar would also set the avar2 VarStore's `RegionAxisCount` to 29 (`otTables.VarRegionList.preWrite` forces it to `len(fvar.axes)`). In this table it is 12, with 28 regions. So something else wrote the 29 maps. The repair is a sound backstop; name the real writer when convenient (log the raw header after each stage of one CLI build). This does not block.

**2. The coincide run (rt-coincide, 21:15) and rt-cli-check (21:29) ran `cleanup-2026-10`, which does not contain E2i-c.**
- `git merge-base --is-ancestor bdcb670 cleanup-2026-10` is false. `_parent_master` and `_master_locations` exist only on `e2ic-wip`, and the worktree has been on `cleanup-2026-10` since 18:12.
- The fonts carry the old constant-amount signature: every glyph has 2 SPAC-only tuples, .notdef included. An E2i-c injector build has 20 SPAC tuples on H/n/trademark, 18 of them multi-axis, and none on .notdef.

| build | .notdef | H | trademark |
|---|---|---|---|
| rt-coincide/out (21:15) | 2 SPAC tuples, 0 multi-axis | 2 / 0 | 2 / 0 |
| rt-cli-check/out (21:29) | 2 / 0 | 2 / 0 | 2 / 0 |
| rt-crispy/out (18:00, e2ic-wip code) | **0** | 20 / 18 | 20 / 18 |

- **NEXT (a), the .notdef ±114 u**, is the pre-E2i-c injector, which spaces .notdef; E2i-c already excludes it. There is no new writer to hunt.
- **NEXT (b), trademark 169–315 u at −100**, is most likely the old capped tightening (b688994) against the export. Re-check it on E2i-c code before diagnosing anything.
- **The LOG's "avar 12/12" for rt-coincide is wrong.** Its raw header is **29**: it was built before the repair. Correct the LOG.
- **Do not reuse the rt-crispy/out (18:00) numbers either.** At the default location its SPAC −100 change for H is −846, but the rule gives the old constant amount there (−306 in the other builds). That build is the reverted B attempt, as the LOG says.
- `~/avar2-agent/venv` holds a non-editable avar2_studio install dated 11-02 that matches neither branch. Its `avar2-studio` command runs neither branch's code.

**3. The acceptance build must be the code that will land, with provenance recorded.**
- **Code.** Bring 932de1b onto `e2ic-wip` with a cherry-pick or merge, no rewrite or force-push. Alternatively, merge e2ic-wip into the sprint branch once it is ready for review.
- **Provenance at the head of every build.log:**
  - `git rev-parse HEAD`
  - the `git status --porcelain` count of the code checkout
  - `python -c "import avar2_studio; print(avar2_studio.__file__)"`
- **Signature check before comparing anything:** H has multi-axis SPAC tuples, .notdef has none, and the raw avar header = fvar.

**4. The coincide copy should be the file the user will actually have.**
- **Keep the ORIGIN row, with `out` = the default master's coordinates.** The export already leaves the default row out of `<mappings>` (sources_export docstring), so nothing should refuse it. If anything does, that is a defect to fix, because the user's file will keep the row.
- **Use a master other than 47-1-1 as Variable Font Origin**, e.g. 1715-1-1 (nearest today's ORIGIN at 1420.1/149.9/122.1), or the master the user picks. With 47-1-1 nothing moves at all, so the run only proves the no-relocation path. The user's real file moves the default master, and that is the path to prove: the sources' axis defaults and normalization change, and the studio's move must be a no-op.
- **Optional baseline.** One 47-1-1 run (ORIGIN kept) as a baseline. Each run costs about 96 s / 1 GB plus fontmake 59 s / 0.5 GB, one at a time.
- **Pass criteria (unchanged):** both SPAC ends, every class, slanted and rounded rows included; no extra regions; TTF size against the current Crispy.

**5. E2l evidence already in hand.** rt-coincide's build.log prints RESTING_NOTE ("the default row differs there") although the resting location there IS the default master. That is the false note E2l removes; use it as a test case.

## Verifier note (2026-11-05 23:35) — ROUND 29's blocker is the wrong row: Crispy rests at "Default 400 MaxOpsz", not "Wide 400 MinOpsz"; the relocation is doing real work

**Evidence (rt-coincide2, e2ic-wip 82b0807):**
- The build's own `work/.avar2-studio/config.yaml` has exactly **one** default-input row: in `wght 400 / wdth 100 / opsz 144` → out **XTRA 663.1 / XOPQ 202.5 / YOPQ 121.8**. That is the CSV row **"Default 400 MaxOpsz"**, and it is what `pipeline._is_default_row` selects as `default_rows[0]`, the relocation target. Every Crispy studio build rests there; it is the D of the 17:55 and 18:16 model tests.
- "Wide 400 MinOpsz" has blank user inputs. The config generator turns it into a parametric identity row: in = out = its own XTRA/XOPQ/YOPQ, which is row 0 in rt-coincide2's config, 1715/1/1 after the edit. It never anchors the resting state.
- So in rt-coincide2 the compiled font rests at the new Variable Font Origin, 1715/1/1 (the degraded final TTF, 765,636 B, has those fvar defaults), and the target is 663.1/202.5/121.8.
  - The existing per-axis short-circuit (`abs(v - ax.defaultValue) < 1e-6: continue`, pipeline step 3.5) correctly does NOT fire, because the target differs.
  - The relocation from the 1715-1-1 corner to that interior point overflows Z (2,011 tuples).
- **There is no "relocation runs when the target equals the defaults" bug. Do not add a short-circuit**: one already exists and it is right.
- The ROUND 28 copy had the same misidentification. With "Wide 400 MinOpsz" dropped, the font still rested at 663.1/202.5/121.8 and was relocated, which is why its TTF is the same 10.5 MB as the original's.
- My 21:42 note repeated it ("nearest today's ORIGIN at 1420.1/149.9/122.1"). That was wrong: the resting location is 663.1/202.5/121.8.

**The user-faithful coincide copy, corrected:**
- **Revert the "Wide 400 MinOpsz" edit**: keep the user's row as it is.
- **Set "Default 400 MaxOpsz" `out` to a master's coordinates**, and make that master the Variable Font Origin with matching metadata defaults.
- **Use 1715-1-1, as now**, for the non-trivial case where the default master moves away from 47-1-1. Expected outcome:
  - target = compiled defaults, so `limits` is empty and the relocation never runs;
  - no Z overflow, no extra regions, and a much smaller studio TTF (the relocation's ~17× gvar densification is gone).
- Alternative, closer to the user's real file: add a master at 663.1/202.5/121.8 and make it the default. That is a design change only the user can make. Do not attempt it in the copy beyond what Glyphs-free tooling can do faithfully.
- Then the acceptance as before: both SPAC ends, every class, slanted and rounded rows, the signature check, provenance in build.log, and the TTF size against the current Crispy.

**Side observation (no action now):** relocating from the 1715-1-1 corner overflows Z, while relocating from the 47-1-1 corner fits. For E2l's "Set default at" exports on a font whose default master is a different corner, the overflow is the existing degraded-build path ("Export at the resting style, or reduce twin masters"). Note it in E2l's tests if the dialog shows that error.

## Verifier note (2026-11-06 00:02) — ROUND 30's acceptance build ran `cleanup-2026-10` again; its own provenance header says so

**Evidence:**
- `test-output/rt-coincide2/build.log` (written 23:41) begins:

  ```
  commit: 2c8e7680668d…
  branch: cleanup-2026-10
  ```

  The worktree reflog shows the checkout moving `e2ic-wip → cleanup-2026-10` at 23:30, with no move back before the 23:39 build. The LOG's "provenance: e2ic-wip 82b0807" is therefore wrong. The header was written but not read.
- The font has the old injector's signature. In `rt-coincide2/out/…ttf` (750,836 B):
  - **no glyph has a multi-axis SPAC tuple**;
  - 204 glyphs have exactly 2 SPAC-only tuples and 292 have none;
  - H has 2 SPAC-only tuples with d = 166. The LOG's "H 59 (57 multi-axis)" does not describe this file;
  - .notdef has the two SPAC-only tuples with d = 39.
- So all three acceptance items are again the pre-E2i-c injector (the 21:42 signature stands):
  - (1) .notdef ±78;
  - (2) IJ +100 constant 166 (166 is H's d, worth a look once on E2i-c);
  - (3) trademark at −100.
- **The 750,836 B size is valid only for the constant injector.** E2i-c's per-corner SPAC tuples will add to it. Re-measure.

**What does stand from ROUND 30:**
- The resting-row correction holds: target = compiled defaults, so `limits` is empty and nothing is relocated. That is independent of the injector.
- **One code-independent lead.** Rows over 3 u are only `nine` lsb at the 900 rows: Default 900 Max/MinOpsz 6 u, Wide 900 Max/MinOpsz 13 u. The value is the same with SPAC blank (0) and 25.6, so it is not spacing. Re-check it on E2i-c. If it stays, diagnose it as an outline or metrics round-trip difference, before judging any SPAC end.

**Process fix: the build must not depend on what the shared worktree has checked out.**
- Build from code that cannot move under you. Two options:
  - a dedicated worktree, e.g. `git worktree add ~/avar2-agent/e2ic-wt e2ic-wip`;
  - an export, e.g. `git archive e2ic-wip | tar -x -C ~/avar2-agent/e2ic-src`.
  Run the build with that path as `PYTHONPATH`. The sprint worktree stays on `cleanup-2026-10` for docs.
- The build script must **assert** the branch and commit it expects, and fail if `avar2_studio.__file__` is not under that path.
- Check the signature before running `roundtrip_compare.py`:
  - H has multi-axis SPAC tuples;
  - .notdef has none;
  - raw avar = fvar.

## Verifier finding (2026-11-06 00:10) — `nine` (a 180°-rotated `six`) misses the grade layers in the export: a decomposed composite does not follow its base glyph's sparse layers

The user's pointer: "9 is a component based on 6". In the source, every one of nine's 9 master layers is the single component `six` at `angle = 180`, with pos.x varying per master. Neither glyph has brace layers in the .glyphs.

**Measured (rt-coincide2; the export code is the same on both branches, so this does not depend on the injector):**

| row (HarfBuzz) | six lsb/rsb, studio | six lsb/rsb, sources | nine lsb/rsb, studio | nine lsb/rsb, sources |
|---|---|---|---|---|
| Wide 900 MaxOpsz | 142 / 81 | 142 / 81 | **55 / 142** | **68 / 129** |
| Default 900 MaxOpsz | 98 / 66 | 98 / 66 | 54 / 98 | 60 / 93 |
| Default 400 MaxOpsz (on a master) | 200 / 100 | 200 / 100 | 100 / 200 | 100 / 200 |

- **six is identical in both fonts.** In the studio, nine's rsb = six's lsb at every row, as a rotated copy should be. In the sources it is not.
- **Cause.** The exported designspace carries **grade sparse layers** inside `Crispy-47-1-1.ufo` and its twins, at row locations for GRAD −10 / 0 / +10, e.g. `{726, 316.3, 235.2, 0, 100, 0, 0, 0}` and `{1560.8, 500.9, 233.4, 0, 100, 0, 0, 0}`.
  - **six** has a glyph in them.
  - **nine**, which the export decomposes because its component is transformed, is **missing from the GRAD 0 layers** at 726/316.3/235.2 (Default 900 MaxOpsz), 1560.8/500.9/233.4 (Wide 900), 123.7/212.9/165.8 and 663.1/202.5/121.8.
  - At the GRAD ±10 layers at 1560.8, nine is present but is **not** rotated six (six lsb 231 vs nine rsb 199).
  - So in fontmake, nine interpolates from its masters only while six follows its grade layer. That gives 6 u at Default 900 and 13 u at Wide 900, exactly the rows over tolerance.
  - The studio follows: fontc builds the composite at the base glyph's brace locations, then decomposes it.
- **Scope.** Every glyph the export decomposes ("908 components decomposed (19 glyphs)") whose components have sparse layers is at risk: grade layers now, correction or brace layers in general. The round trip only crossed 3 u on nine.

**Fix direction (the agent's to choose; root cause, not a per-glyph patch):** when the export decomposes a composite, write the decomposed composite into every sparse layer where any of its components (recursively) has a glyph. Use that component layer, with the composite's own offset, angle and advance interpolated at that location; the advance must match what the studio produces there. Alternatively, do not decompose there and let the compiler handle the transformed component. Either way the gate is the same: nine at the 900 rows within 3 u, and a sweep proving no decomposed glyph lacks a sparse layer its base has.

Separate question for the grade engine (check, not a finding yet): at GRAD ±10 the exported nine is not rotated six. Say whether the grade engine treats nine as its own glyph by design.

## Verifier finding (2026-11-06 03:15) — ROUND 31: the pinned build holds; the SPAC-end residual starts at the masters (per-tuple rounding); nine's layer must interpolate nine

**Verified on `rt-coincide2`** (build.log: `~/avar2-agent/e2ic-wt`, e2ic-wip `80e96e8`, 0 dirty, `avar2_studio` from the worktree):
- TTF 765,636 B. Raw avar 12 = fvar 12. avar2 has 28 regions, RegionAxisCount 12.
- FreeType = HarfBuzz at 9 locations: 0.0, and 1.0 at the wght max + wdth min + SPAC min corner (integer rounding).
- `.notdef` has 0 tuples.
- H has 75 tuples, 18 of them SPAC (16 multi-axis). 203 of 496 glyphs carry multi-axis SPAC tuples.
- Correction to the LOG: H has 68 multi-axis tuples of 75, not 73.
- Stale evidence: `failures.jsonl` (02:31) comes from an attempt build (nine 5,046 u) and does not match `rt.json` (02:48). Regenerate it on every run or delete it.

**The SPAC-end residual is not rounding noise.** Over 28 rows × 496 glyphs, the signed studio − sources change in advance averages +0.06 u at −100 and −0.06 u at +100. Per glyph, though, it has one sign:
- g and its accented forms (gbreve, gcaron, …), slash, backslash and yen sit at +1.7 u at −100 and −1.6 u at +100.
- 46–51 glyphs have a mean of at least 1 u.

The difference starts at the parametric masters (`~/avar2-verifier/scripts/spac_at_masters.py`). There the exported twin UFO and fontmake's TTF agree exactly; the studio does not:

| master | glyph | twin UFO = fontmake (−100) | studio (−100) |
|---|---|---|---|
| 47-1-1 | period | −2 | 0 |
| 47-1-1 | g | −10 | −8 |
| 1715-1462-1 | g | −842 | −840 |
| 1715-1-275 | slash | −144 | −142 |
| 47-1462-1 | H | −570 | −572 |

The +100 end mirrors this.

**Cause** (read in the code; confirm with the master check after the fix): the injector passes FLOAT corner amounts to `model.getDeltas()` and then rounds each tuple delta on its own (`round(delta)`). At a master the font therefore gives Σ round(Δᵢ), while the export writes `dx = round(amount)` per master. A 1 u error per side becomes 2 u on the advance, and it interpolates into every row.

**Fix:**
- Compute each corner's per-side amount as the export's integer: the same `round()` and the same integer floor as `_fit_dx`, ideally one shared function.
- Then call `model.getDeltas(ints, round=round)`. fontTools rounds sequentially, so every corner reconstructs exactly.

**Gate:**
- The studio equals the twin at every master, at both ends, to 0 u.
- At rows, worst ≤ 2 u (four integer HarfBuzz reads).
- No glyph has a signed mean of 0.5 u or more across rows (`spac_signed.py`, `spac_perglyph.py` in `~/avar2-verifier/scripts/`).
- The 1 u SPAC tolerance in `roundtrip_compare.py` cannot tell rounding from a real offset; replace it with this pair.

**nine: the synthesized layer must interpolate nine at the layer's location.** I measured the four GRAD 0 sparse locations (studio vs sources):
- Advance already agrees: 2779/2779, 5173/5172, 1149/1149, 2196/2196. Ink agrees too.
- Only the position is off: lsb 54/60, 55/68, 40/41, 66/68.
- In the studio, nine's rsb equals six's lsb exactly at all four locations (98, 142, 46, 107). So the rotated six sits at an x-offset equal to nine's own advance there.

So the layer at L is:
- the advance and the component transform interpolated from nine's OWN masters at L (masters-only, as fontmake already does for nine);
- the outline: six's layer at L under that transform.

Attempt 3's advance of 127 is nine's advance and offset at its parent master 47-1-1 (pos.x 127), copied instead of interpolated. six's advance (1,365) is not the target either.

The studio's nine is a simple glyph (fontc flattens it), so the studio is the reference here.

## Verifier finding (2026-11-06 10:02) — ROUND 32 verified; the sparse-layer sweep finds one more: `cent` (mixed) misses `c`'s Lowercase-width layers

**Verified on `rt-coincide2`** (build.log: e2ic-wt, e2ic-wip `457e107`, 0 dirty, worktree import; `failures.jsonl` and `rt.json` are both from this run):
- 765,776 B; raw avar 12 = fvar 12; FreeType = HarfBuzz (≤ 1 u, integer rounding).
- `spac_at_masters.py`: twin UFO = studio = fontmake at every master and both ends — 112 of 112 checks (g, slash, period, H, yen, nine, Q, gbreve × 7 masters × 2 ends).
- `spac_signed.py`: mean −0.004 / +0.003 u, sd ≈ 0.2, mixed signs, 6 reads at 2 u.
- `spac_perglyph.py`: worst glyph mean 0.39 u (H at +100); none ≥ 0.5.
- Rows worst 0.00 u. nine is fixed.

**The sweep (gate from 00:10).** I compared every glyph, studio vs sources, at all 60 sparse-layer locations of the exported designspace: grade, Lowercase width (LCWD), slanted and rounded twins (`~/avar2-verifier/scripts/sparse_sweep.py`).
- 495 of 496 glyphs agree within 1 u everywhere, including nine at GRAD ±10. That closes the earlier GRAD ±10 question.
- One glyph fails: **`cent`**. It is a *mixed* glyph (`c` plus its own contours), flattened by both renderers. It fails at all 12 LCWD 100 layers and at the GRAD ±10 layers, up to 1,128 u.
  - At XTRA 1715 / XOPQ 1462 / YOPQ 275, LCWD 100: the studio's cent has ink 7,904 and lsb 718 (it follows `c`'s LCWD layer: `c` ink 6,788 → 7,904 in both fonts). The sources' cent stays at ink 6,788, lsb 663.
  - Advance is 9,045 in both.
- The guard "only pure transformed components get a synthesized layer" (457e107) leaves mixed glyphs out. The 28-row round trip cannot see it, because every row is at LCWD 0, upright, sharp.

**Fix direction.** Same principle as nine: a flattened mixed glyph gets a layer wherever a component's base has one. At L:
- its OWN contours are interpolated from its own masters (masters-only);
- the component part is the base's layer at L, under the component transform interpolated at L;
- advance is interpolated from its own masters;
- contours come in the same order the flatten pass uses at the masters, so fontmake sees compatible sources.

If Q gets the same treatment, it must stay compatible. Q currently passes (≤ 1 u) because O has no layers there.

**Gate:**
- `sparse_sweep.py` shows 0 glyphs over 1 u.
- Add the sparse-location sweep to the round trip, so non-row locations are covered.

**For the user (design, not a code item):** cent's own advance does not follow LCWD in the studio, which follows Glyphs' behaviour. So at that corner, cent's right side shrinks from 1,594 to 423 u. The export fix will reproduce that faithfully. Whether cent should widen with `c` is the designer's call.

## Verifier finding (2026-11-06 12:30) — ROUND 33 verified; before the checkpoint: tests for the three fixes, and no silent skips in the sparse-layer pass

**Verified on `rt-coincide3`** (e2ic-wip `c9849fa`, pinned, 0 dirty; the unzipped designspace is byte-identical to the build's zip):
- `sparse_sweep.py`: 60 locations × 496 glyphs, **0 glyphs over 1 u**.
- Masters: 96 of 96 exact (twin = studio = fontmake), cent included.
- Per-glyph SPAC means: worst 0.39 u.
- Raw avar 12 = fvar 12; FreeType = HarfBuzz (≤ 1 u).
- 24 sparse layers added.

**Before the checkpoint** (not blocking the grid-cost or E2l work):

1. **No tests for 75e6c55, ca90f44 or c9849fa.** Only 8ec4175 carries one. Each fix needs a fixture test that fails on the old code:
   - the injector reproduces the export's integer per-master amount at every corner;
   - a rotated-component glyph gets its base's sparse layer, with its OWN interpolated advance and offset;
   - a mixed glyph gets the layer, with its own contours interpolated and in the right contour order.
2. **Silent skips.** `_mixed_model` returns `None` when a glyph has more than one component, or when its masters are incompatible, and the pass `continue`s. A glyph like that whose base has sparse layers drifts again, exactly like cent did, with no trace.
   - The mixed path also sets only `components[0]`'s transform.
   - Report every decomposed or mixed glyph that has a base with sparse layers but got no layer, in `build.log` next to the "N sparse layers added" line. A designer-facing warning would do too.
   - Better still, handle more than one component: every component takes its own interpolated transform.
   - Crispy has none today (the sweep is clean), so this is about other fonts.
