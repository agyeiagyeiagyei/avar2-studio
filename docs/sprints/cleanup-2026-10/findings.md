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
