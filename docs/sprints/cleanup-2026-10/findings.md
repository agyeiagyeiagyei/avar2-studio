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
