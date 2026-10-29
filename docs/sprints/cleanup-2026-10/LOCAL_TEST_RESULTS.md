# Local test results — sprint `cleanup-2026-10`

Run of `LOCAL_TEST_ROUTINE.md` on a copy of Crispy. One line per test ID:
verdict + the evidence that backs it. A claim without a command,
request log, screenshot path or table dump is NOT-VERIFIED.

> **Status 2026-10-20 (S0): every Part-A scenario PASSES.** The post-fix
> results are below; the sections from “Environment (round 9)” down are the
> original run that found the seven FAILs, kept as history.

## S0 results — after the round-10 fixes and the review follow-ups

**Environment.** Worktree `~/Documents/avar2-studio-sprint`, branch
`cleanup-2026-10`, head `a62486b` (S0 commits on the verifier's
`364ae67`/`17bd59d`). Copy `SCRATCH=/private/tmp/av2-routine`, re-staged per
the routine (`.glyphs`, `-avar.csv`, `-control.json`, `-grade.json`,
`-transforms.json`, authored `.avar2-studio/`). Server on 5072 with
`HOME=$SCRATCH/home` and absolute accelerator paths; `~/Documents/Crispy`
and port 5070 untouched. Probe `ui_probe.py`; logs `server15.log`–`server19.log`,
`probe-all3.log`, `probe-all4.log`, `probe-all6.log`, `probe-all7.log`,
`probe-export3.log`, `probe-config7.log`, `probe-grade2.log`, `probe-syncexport.log`; screenshots `ui-shots/`.

**Idle is `building: false` AND `build_job: null`** — the WOFF2 compress job
runs after the build and greys out Download / logs 409s. The probe waits for
that before every scenario (`wait_ui_idle`, 600 s) and for the header chrome
to be interactive (`wait_header_ready`), because the header menus are
`disabled={busy}`.

**Staging gate before A1 [PASS]:** `origin_warning: null`,
`font_built: true`, `last_build_status: ok`, `opsz` default `144`, SRC row
`Default 400 MaxOpsz` with `origin: source`, 30 rows.

**Full sequential run: 16/16 PASS, `EXIT=0`** (`probe-all7.log`, server
`server23.log`, copy re-staged immediately before it — CSV sha
`b13128f7…`). Every scenario below ran once, in order, from a drained
queue.

| ID | Verdict | Evidence |
| --- | --- | --- |
| A1 | **PASS** | health `{origin_warning: null, font_built: true, last_build_status: ok}`; `/api/axes` 12 axes (XTRA XOPQ YOPQ LCWD YMOD GRAD slnt ROND opsz wdth wght SPAC); SRC row `Default 400 MaxOpsz`, `origin: source`, ORIGIN badge; 30 rows == the CSV's 30 data rows. |
| A2 | **PASS** | `first-load`: 30 rows, `document.fonts.status == "loaded"` (family `Crispy-VF`), ORIGIN badge present, zero console errors. `ui-shots/a2-first-load.png`. |
| A3 | **PASS** | `select-no-save`: row click → Preview; only `GET /api/mapped-location`; no PUT, no `POST /api/build`; `build_stale` stayed false. |
| A4 | **PASS** | `preview-no-build`: wght drag + Reset → only `mapped-location`; no build. |
| A5 | **PASS** | `sidebar-edit-saves`: exactly one `PUT /api/instance/…?csv_only=true`, CSV row updated, `build_stale` true, opening Preview → exactly one `POST /api/build` → `last_build_status ok`. |
| A6 | **PASS** (was FAIL) | `sync-dots`: the untouched source row is **green**, a studio row stays orange, an edited row goes red then green again after the save. Fix: compare only the axes the CSV carries (`frontend/src/instance-sync.js`); node spec `e2e/instance-sync.spec.mjs` (9 assertions, incl. `663.1000061035156` vs `663.1`). `ui-shots/a6-sync-dots.png`. |
| A7 | **PASS** (was FAIL) | `flyout-source`: **`source PUT status: 200`**, a rebuild lands, the row turns green, the `.glyphs` carries the new value. Root cause: the stale `Def → Default 400 MaxOpsz` alias; the route now falls back to the requested name, resyncs its own CSV row and never 500s. `ui-shots/a7-after-save-to-source.png`. |
| A8 | **PASS** (was FAIL) | `lifecycle`: `create with raw defaults blocked by native validation: False`; create/duplicate/rename/delete land in API+CSV. Fix: `step="any"`. |
| A9 | **PASS** | `multi-broadcast`: shift-select 3 rows, one LCWD drag → 3 `?csv_only=true` PUTs, the broadcast bodies carry **only** LCWD. |
| A10 | **PASS** (was FAIL) | `grade`: the Transforms menu opens, `grade: {"enabled": true, "default_pct": 0.25, "intensity": 1.0}`, per-row G badge present, save/remove instance grade works. Fix: the provider value exposes `transforms`/`grade`; guard `e2e/transforms-context.spec.mjs` (5 failures if removed again — `tsc` cannot see `.jsx`, see § Follow-ups). `ui-shots/a10-grade.png`. |
| A11 | **PASS** | `control-axis`: new `TSTn` axis created, listed as a control axis, shadow regenerated, one rebuild, `POST /api/control-axes/<tag>/open-editor` → `{editing_original: false, port: 8001}`, Fontra editor 200. |
| A12 | **PASS** (with the documented gvar limit) | `export` on a pristine copy: TTF `10,533,616 B` + WOFF2 `916,644 B` (twice back-to-back, and again right after `sync-dots`); set-default at wght 921.8 and 605.8 both return the readable limit error (“glyph 'B' has too many variation tuples … 2927/2915 tuples; dropping the 1259/1274 sub-visible ones was not enough”), no traceback, no partial download. `exports/default.ttf`, `exports/default.woff2`. Harness note: the scenario waits for the rebuild the app itself starts when Preview opens stale (A6's source save leaves it stale), and counts the WOFF2-compress-window 409s out loud instead of failing on the window it creates. |
| A13 | **PASS** | `kinetikern`: proposal returns; Apply persists + rebuilds (KKRN in the built font); Revert removes it (no KKRN). |
| A14 | **PASS** | `tester`: Bold/Italic/Center/Tracking → `fontWeight 700`, `fontStyle italic`, `letterSpacing 1.6px`, family `Crispy-VF`; zero console errors. |
| A15 | **PASS** | `load-font`: `Crispy Mini` → `family_name CrispyMini`, 8 rows; back to the copy by uploading `.glyphs` + CSV + metadata → `Crispy`, 30 rows, no row left selected. |
| A16 | **PASS** (was FAIL) | `config-roundtrip` on a pristine copy: **133 layers / 697 pins before and after**; `layer/axis payload changed: []`; `byte differences: []` on an already-canonical sidecar (the first round-trip differs only by canonicalisation: tag case, `0.0`→`0`); the Crispy CSV's sha256 is unchanged across the import. Fixes: case-insensitive tag matching, **no pin pruning at all** (a pin on an axis the source lacks is a warning), CSV read/write with `newline=""` through `sidecar_io.write_text`. |
| A17 | **PASS** | Watcher: one `glyphsLib.load/save` on the copy → exactly one `[jobs] build-… started`, `finished: success=True`; no “Unexpected EOF”; the UI shows the rebuild then the fresh font. |
| A18 | **PASS** (was FAIL) | Truncation inside a glyph (60 % of the file): `last_build_status=failed`, `last_build_error="The source file cannot be read: Expected terminating ')' for array at line 51222"`, `source_transform_error` set, every mutating request → **`PUT 409 application/json` `{"build_blocked": true, …}`**, `/api/font` still served the last-good **10,644,388 B**; after restoring the file and touching it, one build cleared everything (`ok`, `PUT 200 application/json`). The mapping-failure fallback is pinned by `test_a_readable_source_that_misses_the_mapping_still_builds`. |
| A19 | **PASS** (was FAIL) | Static Pages build on 5075: example switch, sliders drive the font (`wght 100 → 753.1`), **`api hits [] count 0`**, **`console errors [] count 0`**. Fix: the backend-served index carries `<meta name="avar2-backend">` (+ `Cache-Control: no-store`); production without it goes straight to static mode. `tests/test_ui_index_marker.py`. |
| A20 | **PASS** | `console-clean` visits Instances/Preview/Kinetikern/Tester with zero console errors when started at true idle (build **and** compress done). Before the idle guard the same scenario logged three 409s from `/api/font?format=woff2`. |
| A21 | **PASS** | pytest **547 passed** with the two accelerator env vars; `ruff check src tests` clean; frontend `lint` 0 errors, `typecheck` clean, `test:unit` all passed (incl. the two new specs), `npm run build` OK; `native/kinetikern2 cargo test` 53 passed; **`wasm/fontc-web cargo test` compiles and passes** with `CARGO_TARGET_DIR` on the warm target (round 9's BLOCKED was a full disk). Disk after the S6 clean-up: 5.1 GiB free. |

### S0 commits (one per FAIL)

| Commit | Scope |
| --- | --- |
| `ab23b88` | A8 — `step="any"` in `DuplicateModal` |
| `778eb23` | A10 — provider value + `e2e/transforms-context.spec.mjs` + `test:unit` wiring |
| `c9762ef` | A16 — case-insensitive tags, no pin pruning, CSV bytes through `sidecar_io` |
| `fc4dba2` | A18 — pre-flight (cached loader) + the fallback pin test |
| `522b4f7` | A19 — backend marker + `no-store`, static-mode detection |
| `a62486b` | A6/A7 — subset sync comparison (`instance-sync.js` + spec), alias fallback, CSV resync, studio-only delete |

### Review follow-ups — how each was closed

1. **Commit per FAIL + push + checkpoint** — the table above; `STATE: checkpoint` in `STATUS.md`.
2. **A10 guard** — *not* via typecheck: `frontend/tsconfig.json` sets `allowJs: true` and **no `checkJs`**, and the hook has no `.d.ts`, so `.jsx` files are parsed but never type-checked; a missing context field cannot fail `npm run typecheck`. Guard is `e2e/transforms-context.spec.mjs` (wired into `test:unit`): it reads the provider's `value` keys and every consumer's `useTransforms()` destructure, and fails on a key the provider does not expose. Negative proof: removing `transforms,`/`grade,` from the value → 5 failures (2 named + 3 consumer assertions); restored → all pass.
3. **A6 spec** — `e2e/instance-sync.spec.mjs`: 9 assertions (float32 noise, a real divergence, the 0.02 tolerance boundary, “no comparable axis ⇒ in sync”, non-numeric compare).
4. **A16 data-loss proof** — 133 layers / 697 pins preserved, payload diff `[]`, byte diff `[]`; the four sequential rebuilds stay a backlog item (S2), not fixed here.
5. **A18** — (a) `test_a_readable_source_that_misses_the_mapping_still_builds`; (b) the pre-flight uses `load_source_cached` (mtime+size keyed); (c) truncation evidence above; (d) mutating routes answer JSON `409`, verified live, not only in the unit test.
6. **A19** — marker ships; the index is served `Cache-Control: no-store`; the static scenario shows no `/api/*` hits and no console errors. Re-checked against today's code: `npx vite build --base=/avar2-studio/ --outDir dist-pages` (the `pages.yml` command) served on 8123 under its base path → **8 rows, `document.fonts.status loaded`, `api requests count 0`, `console errors count 0`**, and `grep -c avar2-backend dist-pages/index.html` → **0** (the static artifact carries no marker, so the client never probes).
7. **A21** — `wasm/fontc-web cargo test` green with the warm `CARGO_TARGET_DIR`; my `/private/tmp` clones deleted (`av2-wheel-test` 303 MB, `av2-pw` 162 MB, `av2-local-test` 18 MB), `av2-routine` (51 MB) kept as evidence.
8. **Full Part A re-run at idle** — the table above; every scenario now starts from a drained queue.

### New items for the backlog (not S1–S6)
- **The watcher ignores an older mtime.** Restoring a corrupt source with `cp -p`/`copy2` (mtime going backwards) does not trigger a rebuild; the gate stays closed until the file is touched. `git checkout` and editor saves set mtime=now, so the exposure is low.
- **`npm run lint` counts a local Pages build.** With `frontend/dist-pages/` present, `eslint .` reports ~690 errors inside the built JS (round 8's note). CI lints *before* it builds `dist-pages`, so it is only a local annoyance — the directory is deleted here (13 MB) and lint is back to **0 errors / 34 warnings**. Adding `dist-pages/` to the eslint ignores is a one-line change for whoever wants it.
- **ORIGIN-row nudge ≈ gvar limit** and **four rebuilds per config import** — written up in `findings.md` § Round 10; S1 and S2 own them.

## Environment (round 9, pre-fix record)

- Worktree: `~/Documents/avar2-studio-sprint` at `de4de1a` (branch
  `cleanup-2026-10`). The probe/routine changes in this run are uncommitted
  at the time of writing (see "Probe changes" below).
- Copy: `SCRATCH=/private/tmp/av2-routine`, staged per the corrected
  routine: `Crispy.glyphs`, `Crispy-avar.csv`, `Crispy-control.json`,
  `Crispy-grade.json`, `Crispy-transforms.json`, plus the authored
  `.avar2-studio/{axis-metadata.json,instance-renames.json,default-origin/}`.
- Server: port 5072, `HOME=/private/tmp/av2-routine/home`, accelerators
  absolute paths into the worktree (`AVAR2_SPAC_INJECT`,
  `AVAR2_KK2_DYLIB`). Logs: `/private/tmp/av2-routine/server.log`
  (first pass, 16:38–20:03) and `server2.log` (re-run of the failing
  probes, 20:0x). `~/Documents/Crispy` and port 5070 were never touched.
- Probe: `ui_probe.py` (extended, see below), screenshots in
  `/private/tmp/av2-routine/ui-shots/`, exports in
  `/private/tmp/av2-routine/exports/`.
- Staging gate before A1 [PASS]: `/api/health` → `origin_warning: null`,
  `font_built: true`, `last_build_status: ok`; `/api/avar2/axes` →
  `metadata.OPSZ.default == 144`.

## Part A — round-9 verdicts (pre-fix, superseded by § S0 results)

| ID | Verdict | Evidence |
| --- | --- | --- |
| A1 | **PASS** | health `{status: ok, font_built: true, last_build_status: ok, origin_warning: null}`; `/api/avar2/axes` metadata OPSZ default 144, parametric axes XTRA/XOPQ/YOPQ, traditional WGHT/WDTH/OPSZ/SPAC; `/api/axes` 12 axes (XTRA XOPQ YOPQ LCWD YMOD GRAD slnt ROND opsz wdth wght SPAC), LCWD/YMOD `is_control_axis: true`; `/api/instances` 30 names == the CSV's 30 data rows; the SRC row is `Default 400 MaxOpsz` (`arg… origin: source`, `instance-renames.json` applied) and carries the ORIGIN badge (A2 screenshot). |
| A2 | **PASS** | `ui_probe first-load`: 30 rows == `/api/instances`; `document.fonts.status == "loaded"` with family `Crispy-VF`; `[data-instance-name='Default 400 MaxOpsz'] .origin-badge-default` text `ORIGIN`; zero console errors. Shot `ui-shots/a2-first-load.png`. |
| A3 | **PASS** | `ui_probe select-no-save`: row click → wait 3 s → Preview. Request log: only `GET /api/mapped-location`; no PUT, no `POST /api/build`; `build_stale` stayed false. Shot `ui-shots/select-no-save.png`. |
| A4 | **PASS** | `ui_probe preview-no-build`: wght drag + Reset. Only `GET /api/mapped-location`; `mapped-location` read-outs changed; Reset restored the slider; no build. Shot `ui-shots/preview-no-build.png`. |
| A5 | **PASS** | `ui_probe sidebar-edit-saves`: exactly one `PUT /api/instance/Default%201%20MaxOpsz?csv_only=true` ≈0.8 s after the drag; CSV row in the copy updated; `build_stale` true; opening Preview → exactly one `POST /api/build`; after it `build_stale` false, `last_build_status ok`. Shot `ui-shots/sidebar-edit-saves.png`. |
| A6 | **FAIL** | Studio row: red draft → orange after save (works). Untouched SRC row `Default 400 MaxOpsz` is **orange, not green**. `GET /api/avar2/instances` shows `avar2_mapping.out == {XOPQ 202.5, XTRA 663.1, YOPQ 121.8}` and the same values in `glyphs_coordinates`, so the CSV does *not* diverge from the source. Shot `ui-shots/FAIL-sync-dots.png`; re-run at 20:1x in `server2.log`. Root cause below. |
| A7 | **FAIL** | Flyout → “Save to source file” returns **HTTP 500** `{"error":"Failed to update instance 'Default 400 MaxOpsz' in source file"}`; no build starts; the `.glyphs` keeps XOPQ 202.5. Direct repro: `curl -X PUT localhost:5072/api/instance/Default%20400%20MaxOpsz -d '{"coordinates":{"XOPQ":210,...}}'` → 500. Shot `ui-shots/FAIL-flyout-source.png`. Root cause below. |
| A8 | **FAIL** | `+ New Instance` with the pre-filled defaults is blocked by **native form validation**: axis defaults from `/api/axes` carry float32 noise (`XTRA 663.1000061035156`) while the inputs use `step=0.1`; the browser shows “Please enter a valid value. The two nearest valid values are 663.1 and 663.2” and no `POST /api/instance` fires. Shots `ui-shots/a8-create-validation-blocked.png`, `ui-shots/FAIL-lifecycle.png` (from the first pass). After rounding every axis input to 0.1 the rest passes: create (`POST /api/instance`, API+CSV), duplicate (proposes `LT Test One Copy`), rename (rebuild + rename in API/CSV), delete (studio rows delete straight away — no modal by design; row + CSV gone). |
| A9 | **PASS** | `ui_probe multi-broadcast`: shift-selection over rows 0–2 (`Default 1 MaxOpsz`, `Default 1 MinOpsz`, `Narrow 1 MaxOpsz`); one LCWD drag → three `?csv_only=true` PUTs (the anchor's auto-save + one broadcast PUT per other row); the two broadcast bodies contain **only** `LCWD`; on disk only `LCWD` changed for those rows. Shot `ui-shots/a9-multi-select.png`. |
| A10 | **FAIL** | The header has **no Transforms button** (`header button` = `['Load Font ▾', 'Config ▾', 'REBUILD']`; `body` does not contain “Transforms”), although `GET /api/transforms` returns 7 transforms (round_corners/slant/spac_widthaware enabled) and `GET /api/transforms/grade` returns `enabled: true`. The G badge is absent on every row. Shots `ui-shots/FAIL-grade.png`, `ui-shots/dbg_create2.png` (header without Transforms). Root cause below. |
| A11 | **PASS** | `ui_probe control-axis`: modal created `TST2`; `/api/axes` contains `TST2` with `is_control_axis: true`; shadow regenerated and a rebuild landed; `POST /api/control-axes/TST2/open-editor` → `{project: 'Crispy.glyphs', editing_original: false, port: 8001}`; `GET http://127.0.0.1:8001/editor.html?...` answered 200. Shot `ui-shots/a11-control-axis.png`; editor killed with the server. |
| A12 | **PASS** (with the documented gvar limit) | Preview → Download font…: TTF 10,852,880 B and WOFF2 914,080 B downloaded (`exports/default.ttf`, `exports/default.woff2`); fontTools: axis sets identical to the preview font (13 axes), 496 glyphs compared at wght 900 (mapped XOPQ 316.27/XTRA 725.89/YOPQ 235.2), **0 point-count mismatches, max drift 0.0 u**; WOFF2 decompresses with the same axes (`flavor: woff2`). Set-default at wght 921.8 and at 605.8 both returned the **readable** error: “glyph 'B' has too many variation tuples for OpenType's per-glyph gvar limit after relocating the default (2927/2915 tuples; dropping the 1259/1274 sub-visible ones was not enough). Export at the resting style, or reduce twin masters (disable slant or rounding) for this export.” No traceback; no partial download. Shot `ui-shots/a12-export.png`; job ids `export-1792413245385-27`, `…-28`, `…-29`. |
| A13 | **PASS** | `ui_probe kinetikern`: proposal returns (`GET /api/kinetikern/propose?instance=Default+1+MaxOpsz&looseness=0.45`); Apply (`PUT /api/kinetikern/instances/…`) persists the entry and rebuilds; the built font's table tags include **KKRN** (`fvar`, `gvar`, `avar`, `KKRN`); Revert (`DELETE`) removes the entry and the rebuild's font has **no** KKRN. Shot `ui-shots/a13-after-apply.png`. |
| A14 | **PASS** | `ui_probe tester`: typed text; select-all → Bold/Italic/Center/Tracking 0.05 em. Runs: `fontWeight 700`, `fontStyle italic`, `letterSpacing 1.6px`, `text-align center`, family `Crispy-VF`; zero console errors. Shot `ui-shots/a14-tester.png`. |
| A15 | **PASS** | `ui_probe load-font`: Load Font → `Crispy Mini` → `POST /api/load-source`, family `CrispyMini`, 8 rows; back to the copy by uploading `Crispy.glyphs` + `Crispy-avar.csv` + `axis-metadata.json` → family `Crispy`, 30 rows, no row left selected. Shot `ui-shots/a15-after-load-font.png`. (The upload path carries no `-control/-grade/-transforms/-kinetikern` sidecars, so the returned copy carries only the CSV+metadata; the routine asks for a row/axis swap, which is what it verified.) |
| A16 | **FAIL** | `ui_probe config-roundtrip`: the exported bundle (`config-bundle.json`, 231,286 B) is **rejected by its own dry-run import** — 163 errors, all `layer on axis 'lcwd' (glyph '…'): pins axis 'LCWD', which this source does not have.` (`POST /api/config/import {dry_run:true}` → 200 with `errors` populated). The import button stays disabled, so the round-trip cannot be applied. Shot `ui-shots/FAIL-config-roundtrip.png`. Root cause below. |
| A17 | **PASS** | Idle server, then `glyphsLib.load(copy); font.save(copy)` (498 glyphs, 1 instance). `server2`/`server.log`: exactly **one** `[jobs] build-1792414685491-30 started (build)` and `finished: success=True in 44.04s`; `grep -c 'Unexpected EOF' server.log` → **0**; UI load afterwards: `document.fonts.status loaded`, rows present. |
| A18 | **FAIL** | Appended `<<<corrupt>>>` to the copy's `.glyphs`. The watcher's rebuild **succeeded** (`build-…-32/33 finished: success=True in 2.58/2.08 s`) and produced a degraded fallback `Crispy[SPAC,XOPQ,XTRA,YOPQ,opsz,wdth,wght].ttf` (603,524 B) instead of `last_build_status: failed`; the gate never closed. A `PUT /api/instance/Default%201%20MaxOpsz?csv_only=true` returned **500** (HTML) with `openstep_plist.parser.ParseError: Junk after plist at line 82480`, and `GET /api/font` served the 603 KB fallback in place of the full 10.8 MB font. After restoring the file, one build restored the full font (13 axes, 10,852,880 B) and the PUT returned 200. |
| A19 | **FAIL** (minor; by design?) | Static Pages build served on 5075: example switch Roboto Delta Mini (1 row) ↔ Crispy Mini (8 rows), sliders drive the font (`wght 100 → 753.1`, `font-variation-settings` follows), no backend data requests. But `selectApiMode()` issues `GET /api/health` on load; on the static host it 404s and Chrome logs `Failed to load resource: 404`, so the “no `/api/*` request / zero console errors” expectation is not met. Shot `ui-shots/a19-static.png`. |
| A20 | **PASS** (with a timing note) | `ui_probe console-clean` visits Instances/Preview/Kinetikern/Tester with zero console errors when run at idle. If run immediately after a build while the **compress** job still runs, three `GET /api/font?format=woff2` return 409 and Chrome logs them; the 409s stop once compression finishes. Idle is `building: false` **and** `build_job: null` — the routine's idle rule only covered the first. |
| A21 | **PASS** (two scope notes) | Fresh clone of `de4de1a` in `/private/tmp/av2-clean`: `pip install -e '.[dev]'` OK; **pytest 536 passed** in 71.27 s (with the two accelerator env vars); `ruff check src tests` → “All checks passed!”. Frontend: `npm ci` 355 packages; `npm run lint` **0 errors** (34 warnings); `typecheck` clean; `test:unit` “all passed”; `npm run build` OK (chunk-size warning only). Rust: `native/kinetikern2 cargo test` → **53 passed** (plus 1 ignored); `wasm/fontc-web cargo test` **could not compile — No space left on device** (disk at 100%, 1.2 GB free), so that crate's tests are BLOCKED by the machine, not by code. Note: `ruff check .` (repo root, including `examples/` and `spike/`) reports **13 pre-existing** errors (I001/F401/B007/F841/RUF100 outside `src`/`tests`); round 7's “ruff clean” is only true for `src tests`. |

## S1 — reproduction recipe for the ORIGIN-nudge relocation failure

The round-11 review could not reproduce the round-10 claim on a *pristine*
copy, so S1(c)(d) are parked on a reproducible recipe. Here it is, with the
control it was missing: **a control axis with its seeded brace layers** is
what makes the 1 % ORIGIN nudge overflow the relocation.

Script: `docs/sprints/cleanup-2026-10/s1_recipe.py` (mutates only the
scratch copy). Copy re-staged per the routine (CSV sha `b13128f7…`), server
on 5072, logs `s1-recipe.log` (accumulated) and `s1-recipe-pristine.log`
(control).

| Stage | status / `avar2_error` | axes | font | `g` tuples | `B` tuples |
| --- | --- | --- | --- | --- | --- |
| pristine baseline | ok / None | 12 | 10,533,616 B | 1,476 | 1,328 |
| **pristine + nudge** (`XOPQ 202.5 → 187.89`) | **ok / None** | 12 | 11,042,136 B | 1,467 | 1,383 |
| pristine + second nudge (`→ 173.28`) | ok / None | 12 | 11,013,112 B | 1,466 | 1,402 |
| + control axis `TST2` + reseeded brace layers | ok / None | 13 | 10,853,032 B | 1,332 | 1,281 |
| **+ same nudge** (`→ 187.89`) | **build failed** / “glyph 'g' has too many variation tuples … after relocating the default (1767 tuples; dropping the 425 sub-visible ones was not enough)” | **10** | **641,432 B** | 85 | 77 |
| + second nudge (`→ 173.28`) | same failure (1,767 / 428 dropped) | 10 | 641,012 B | 85 | 77 |

Reading:

- On a pristine Crispy the nudge relocates cleanly at both steps — the
  verifier's result reproduces exactly (their `g` 1,471 / `B` 1,386).
- With **one control axis and its brace layers** present, the same nudge
  takes `g` from 1,332 to 1,767 tuples *after relocation* (+33 %) and the
  per-glyph header budget overflows; the studio then serves a **10-axis,
  641 KB plain fallback** (`g` down to 85 tuples, mapped axes gone) and
  reports it only through `avar2_error`, with `last_build_status: ok`.
- Grade and Kinetikern were **not** needed: both endpoints answered 503
  (“Fontra subprocess is not running”) in this run, so the accumulation is
  the control axis + its seeded braces alone.
- The second nudge adds nothing (`1,767` again) — the failure is the
  brace-layer tuple count crossing the ceiling, not the distance moved.

This is what S1(a) must catch before the instancer and what S1(b) must not
make worse: a 641 KB unmapped font instead of the 10.8 MB avar2 font with
its axes intact.

### S1(a) measurement — the instancer is the cost, and no cheap exact check exists

Before writing the pre-check, measured on the two calibration fonts
(`s1_estimate_probe.py`, fontTools 4.63.0, both with `avar2` stripped as the
pipeline does):

| Font | `g` before | `instantiateVariableFont` (XOPQ 202.5 → 187.89) | `g` after |
| --- | --- | --- | --- |
| 12-axis pristine (`/tmp/calib-12axis.ttf`, 10,533,616 B) | 1,476 | **27.3 s** | 2,036 |
| 13-axis with `TST2` + braces (`/tmp/calib-13axis.ttf`, 10,853,032 B) | 1,332 | **27.3 s** | 1,824 |

Three things this settles:

1. **The instancer is ~27 s of a ~45 s build** — the pre-check is worth
   having, and it is also the only expensive step that a failing relocation
   wastes.
2. **No cheap exact pre-check exists.** The cost is gvar itself, not the
   other tables: stripping everything except `fvar/gvar/glyf/loca/head/maxp`
   still takes 27.2 s (and fails without `hmtx`). So "run the instancer on a
   pared clone" is not cheaper.
3. **The split is not predictable from a cheap rule.** Measured candidates
   against the real post-count on the 13-axis font (`g`): base+involved
   2,344 (real 1,824), base+spans 1,622, base+peak-between 1,787. Only the
   last is a true lower bound, and on the pristine font the real post-count
   (2,036) is *above* the budget too — that build succeeds only because
   `_fit_gvar_headers` then prunes ~569 sub-visible tuples down to 1,467.
   A gate on the pre-prune count would therefore refuse a build that works.

So S1(a) cannot be a sound cheap pre-check in its current shape. Proposed
instead, for the verifier's call: (i) **S1(b) first** — on failure serve the
un-relocated avar2 font (13 axes, avar2 table, ~1,332-tuple gvar) instead of
the 10-axis 641 KB fallback, which removes the damage; (ii) make the failing
case *cheap* by **memoizing the relocation** on (source mtime, sidecars,
target) so an unchanged retry costs 0 s instead of 27 s; (iii) only then
consider a pruned-aware budget model, whose threshold would have to sit
between the measured 1,467 (works, pruned) and 1,767 (fails) cases.

## S1(b) — a failed relocation keeps the avar2 font (2026-10-20)

Same recipe, same copy staging, `head 18c5d82` (S1(b) + the worker-boundary
fix) versus the table above. Recipe now also applies the grade and
Kinetikern entry (it used POST; both are PUT routes — fixed).

| Stage | status / `origin_relocation_error` | axes | font | `g` tuples |
| --- | --- | --- | --- | --- |
| + control axis + braces (+ grade + KKRN) | ok / None | 13 | 10,894,284 B | 1,333 |
| **+ 1 % ORIGIN nudge** | **degraded** / “glyph 'g' has too many variation tuples … (1767 tuples; dropping the 428 sub-visible ones was not enough)” | **13** | **792,256 B** | **86** |
| + second nudge | degraded / same | 13 | 792,080 B | 86 |

Bar (set by the review): after the nudge the served font must keep 13 axes
and the avar2 table, `last_build_status` must not read `ok`, and health must
carry the relocation error. All three hold:

- `fontTools` on the served bytes: axes `XTRA XOPQ YOPQ LCWD YMOD TST2 GRAD
  slnt ROND opsz wdth wght SPAC`, `avar` present, XOPQ default at the
  compiled origin (the un-relocated resting style);
- `/api/health`: `last_build_status: degraded`,
  `origin_relocation_error: "glyph 'g' has too many variation tuples …"`,
  `avar2_error: None` (the avar2 step itself did not fail);
- `/api/font` HTTP 200, 792,080 B — versus **641,432 B / 10 axes** before the
  fix, with the mapped axes gone.

`degraded` is a third status on purpose: `failed` closes the build gate and
would have stopped the designer nudging the ORIGIN row back (all mutating
routes 409). The gate therefore stays open, and the amber banner says the
preview rests at the compiled origin and how to clear it.

**Status plumbing.** The first S1(b) run reported `degraded` with
`origin_relocation_error: None` in health: builds run in a worker
subprocess, and the field was missing from both transfer lists
(`build_worker._apply_result`, `JobQueue._apply_build_state`). Added, and
pinned by `test_the_relocation_error_survives_the_worker_boundary`.

### The one number that did not add up: relocation INFLATES gvar

The review read 13 axes / 792,256 B / `g` 86 tuples against 10,894,284 B /
1,333 tuples one build earlier and concluded the served font had "the axes
and the avar2 table but almost no variation data". The sizes are right; the
conclusion is not. Measured on the staged source behind the still-degraded
test server (`head e3f7551`, `fontTools 4.63`):

| artifact | bytes | total tuples | `g` | axes | note |
| --- | --- | --- | --- | --- | --- |
| (i) fontc alone, same source + flags | 608,820 | 7,701 | 83 | 9 | `.avar2-studio/shadow/transformed/Crispy.glyphs` |
| (ii) `out` after the failed relocation | 792,296 | 8,304 | 86 | 13 | `build/Crispy[…TST2…].ttf` |
| (iii) `/api/font` (served) | 792,296 | 8,304 | 86 | 13 | **sha256 equal to (ii)** |

So (ii) is the compile **plus** the avar2 step's created axes — `opsz`,
`wdth`, `wght`, `SPAC` — and nothing was pruned on the way: the 603 extra
tuples are the injected axis data (`SPAC` participates in 408 tuples, `LCWD`
223, `GRAD` 3,065 …). The failure path writes no bytes at all: it closes the
mutated in-memory font and skips its save, so `out` is still what steps 2–3
wrote.

The 10,533,616 B / 124,370-tuple font is the **relocated** one — a second
copy of that exact build exists as `exports/default.ttf` with the same
124,370 tuples. Relocation is what inflates gvar, because the instancer
re-expresses every delta against the new default: a *feasible* 1-unit XOPQ
relocation of the served 8,304-tuple font takes `g` from 86 to **213** and
the total to **17,900**; at the recipe's 1 % target the instancer reported
`g` at 1,767, and the successful pre-nudge build had 1,476. Those are the
same order — the collapse is the *absence of relocation*, not lost data.

The twins are live in the served font, not inert: tuple participation is
`XOPQ` 6,313, `YOPQ` 6,081, `XTRA` 5,629, `slnt` 3,413, `ROND` 3,316,
`GRAD` 3,065, `SPAC` 408, `LCWD` 223.

Correction to the table above: its pre-nudge row (10,894,284 B / 1,333
tuples) came from the earlier run. In the run that produced the 792 KB font
the pre-nudge build was 10,533,616 B / 124,370 tuples / 12 axes (no `TST2` —
the secondary axis arrived with the recipe's accumulate step). Comparing
across runs is what made the two numbers look like a collapse of the same
font.

**Test now pins the bytes, not just the axes.**
`test_a_relocation_that_cannot_fit_keeps_the_avar2_font` builds the same
config a second time with `relocate_default=False` and requires the failed
build's output to be **byte-identical** to it (plus tuple-dict equality and a
non-empty gvar). If anything ever writes the half-instanced in-memory font,
that assertion fails where "has an avar table" would have passed.

**The degraded state is visible — proven live (answering the early note).**
The note reads "the degraded state is invisible today"; the banner already
shipped in `18c5d82` (`StatusBanners.jsx` reads `originRelocationError` from
`useStudioData`). Loaded against the still-degraded test server
(`/private/tmp/av2-routine/ui-shots/s1b-banner.png`), the page renders exactly
one amber banner:

> The font cannot be relocated onto the ORIGIN row, so the preview rests at
> the compiled origin instead. Every axis and mapping is intact — put the
> ORIGIN row back to clear this. *glyph 'g' has too many variation tuples for
> OpenType's per-glyph gvar limit after relocating the default (1767 tuples;
> dropping the 427 sub-visible ones was not enough). Export at the resting
> style, or reduce twin masters (disable slant or rounding) for this export.*

…while `REBUILD` and every slider stay live (that is the difference from
`failed`). Repro: `get_by_role`-free probe — `document.querySelectorAll
('.error-banner')` yields one node whose `innerText` is the text above.

That probe found a second defect of my own making: the pre-existing
`origin_warning` banner ("Build-time default relocation is off …") also
appears on this path — the failed relocation sets `relocate_at_build = False`
— so the first build of the fix showed **two** amber boxes saying the same
thing with different words. `originWarning` is now suppressed while
`originRelocationError` is set (that banner keeps its own job: no row at the
axis defaults). One banner per condition, verified by re-running the probe
(1 node, text above).

**Status enum documented.** `routes/health.py`'s docstring now lists `ok` /
`degraded` / `failed` with what each means for the gate, and ARCHITECTURE §5
says the same next to the 409 rule. The Fontra catch-all's namespace set is
cached on `current_app.extensions` instead of being rebuilt per request.

**Side finding 1 closed.** The Fontra catch-all now answers 405 (path exists
for another method) or 404 (no such route) as JSON for studio `/api/`
namespaces, derived from the URL map; non-studio paths still proxy.
`tests/test_fontra_catchall.py` covers all three cases, and the recipe's own
calls now use PUT.

**Side finding 2 noted.** The baseline font size varies run to run
(10,533,616 B / 10,472,360 B verifier / 10,894,284 B here after grade+KKRN) —
the accumulated state differs per run and the SPAC injector's pruning is not
byte-stable. A12 size comparisons should carry a tolerance.

## Part B — designer

**NOT RUN.** B1–B10 need Glyphs 3, the Fontra UI, design apps and a real judgement of the
shapes/spacing; B11–B12 need a push and the live 5070 studio. Nothing was started there.

## Findings behind the FAILs (root cause + smallest repro)

### 1. A6 — a source row can never be green (`useInstanceEditing.jsx`)

`getInstanceSyncStatus` calls `_sameCoords(csvCoords, sourceCoords)`. `csvCoords` is the CSV
`out` map (3 keys: XOPQ/XTRA/YOPQ); `sourceCoords` is `instance.source_coordinates`, which the
server populates with every axis (8 keys here, including `GRAD`, `LCWD`, `YMOD`, `ROND`,
`slnt` at their defaults). `_sameCoords` returns false when the key counts differ, so a source
row whose parametric values match the CSV still reports “CSV diverges from source” (orange).
Repro: `/api/avar2/instances` for `Default 400 MaxOpsz` has identical `out` and
`glyphs_coordinates` values, while `.sync-status-dot` carries `sync-status-orange`.

### 2. A7 — stale rename alias turns “Save to source file” into a 500

`instance-renames.json` is `{"Def": "Default 400 MaxOpsz"}`, but the `.glyphs` instance is
already named **`Default 400 MaxOpsz`** (glyphsLib: `instances: [('Default 400 MaxOpsz', …)]`).
`_source_instance_name("Default 400 MaxOpsz")` maps it back to `Def`;
`update_instance_in_glyphs` then finds no instance named `Def` and the route returns 500.
Repro: the curl in A7; the source file is unchanged. `_source_instance_name` should fall back
to the requested name when the alias target is not present in the source (same for the delete
and rename paths).

### 3. A8 — New Instance is blocked by HTML step validation

`DuplicateModal.jsx` renders the axis inputs with `step={0.1}` and seeds them with
`axis.default` straight from `/api/axes` (`663.1000061035156`, `121.8000030517578`, …). The
browser's constraint validation rejects those values, so the form's submit event never fires
(Playwright/`Enter`/forced click all no-op). Fix options: `step="any"` (the server clamps
anyway), or round the seeded values to the input's precision.

### 4. A10 — the Transforms menu and G badges are unreachable

`useTransforms()`'s provider value (`hooks/useTransforms.jsx:282-300`) exposes handlers and
derived `rounding`/`slant`, but **not** `transforms` or `grade` (those live in
`useStudioData`). `Header.tsx:75` and `InstanceRows.jsx:12` destructure `transforms`/`grade`
from it, so the header condition `familyName && (transforms.length > 0 || grade)` is always
false and the G badge's `gradeEnabled={!!grade?.enabled}` is always false. The grade /
transform sidecars still drive the build (the font has GRAD/ROND/slnt/SPAC), but the designer
cannot open the menu or grade a row. Two-line fix: re-export `transforms, grade` in the
provider value.

### 5. A16 — a config bundle cannot import into the source it came from

The bundle's `control_axes` declare `lcwd` and include layers that pin it; the dry-run
validator checks those layers against the source's own axis list (XTRA/XOPQ/YOPQ + user axes)
*before* the control-axis sidecar is in play, so every `lcwd` layer is rejected
(“pins axis 'LCWD', which this source does not have”). 163 such errors on a self-export.

### 6. A18 — a source that no longer parses does not trip the build gate

The corrupt `.glyphs` makes the build pipeline fall back to a reduced font (7 axes, 603 KB)
and report `success: true`; `LAST_BUILD_STATUS` stays `ok`, so `_refuse_edits_while_source_is_broken`
never fires. The route handlers then hit `openstep_plist.ParseError` and answer **500**, and
the degraded fallback replaces the last-good full font. A parse failure (or any source-loader
failure) should end as `last_build_status: failed` and keep the last-good artifact.

### 7. A19 — one `/api/health` 404 on the static demo

`selectApiMode()` probes `/api/health` to decide between server and static providers. On
GitHub Pages/`http.server` that 404s and Chrome logs it, so the demo's console is not clean.
This is inherent to the probe; either accept/document it or probe in a way that does not log
(e.g. a same-origin image/script probe, or a build-time flag).

### 8. Console-clean timing (A20)

`/api/font?format=woff2` correctly answers 409 while the background compress job runs
(`routes/build.py:88-91`). Any page load in that window logs three 409s. Not a product
failure, but the routine must define idle as `building: false` **and** no `build_job`.

## Routine/probe defects fixed during this run (uncommitted at time of writing)

- Setup snippet used `~` for the two accelerator paths in the same command that reassigns
  `HOME`; zsh expands the tilde against the **new** home, so both engines silently vanished
  (SPAC fell back to fontTools, Kinetikern/`propose` answered 503). Changed to `$WT/...`.
- `ui_probe.py` extended: `--sources`, accepted `window.confirm` dialogs, response captures,
  FAIL screenshots, one scenario per test ID (A2, A6–A16), and run-level assertions (Tester
  runs, multi-select anchor semantics).
- Staging `.avar2-studio` state and the `origin_warning`/OPSZ-default gate were already fixed
  in `de4de1a`.

## Recommended next steps

1. Fix the four release blockers first (A6 `_sameCoords`, A7 alias fallback, A10 provider
   value, A8 step validation), then A16 (control-axis layers in the import validator) and
   A18 (parse failures must fail the build / keep the last-good font). Re-run the affected
   probe scenarios and A18's shell script.
2. A12's set-default export is blocked by the documented per-glyph gvar limit on this
   twin-heavy source; the readable error is correct. The proposed “twin-free export” option
   (already noted in round 7) is the way to ship a non-default resting style.
3. A19/A20 are routine-expectation adjustments (the `/api/health` probe and the compress-job
   idle definition) unless the designer wants the console 404 removed.
4. Part B (B1–B12) is still entirely outstanding and needs the designer.

## S1(a′) — relocation memo (2026-10-20)

Relocating the origin is the expensive step: the instancer over a dense gvar
plus the per-glyph header fit, ~27 s on Crispy before counting the rest of the
build. Its result depends only on the compiled font's content and the target
window, so it is memoised under `<build dir>/.relocation-memo/` (successes as
the relocated TTF, failures as `{error}` JSON), keyed on

    sha256( all table data except `head` ) + per-axis (min, new default, max)
    + GVAR_PRUNE_TOL            # a different tolerance prunes different tuples

`head.modified` is a save timestamp, so hashing file bytes would miss on every
rebuild of an unchanged source; `head` is the only table excluded. Two entries
per outcome are kept (`RELOCATION_MEMO_KEEP`) — the relocated fonts are ~9 MB
each; older ones are evicted by mtime.

**The memo has to be told where to live.** Builds run in a per-job directory
(`build_jobs._stage` copies the source set into `jobs/<id>/src`), only the font
and the config are promoted back, and the worker's stdout is `DEVNULL`. A memo
written next to the job's own build dir therefore died with the job — the first
live run showed exactly that: two 46 s builds, no memo entry, no hit. The
stable path now travels in the job spec (`memo_dir`) into
`state.RELOCATION_MEMO_DIR`, and the outcome is carried back as
`origin_relocation_memo` (`"hit"`/`"miss"`) through the same two lists as
`origin_relocation_error`, because the worker's log cannot be read.

### Evidence (live, copy of Crispy at 5072, this worktree)

| Scenario | build | memo | wall clock |
| --- | --- | --- | --- |
| failing relocation, first build (asymmetric twins, 13 axes) | degraded | miss | **37.25 s** |
| same state, unchanged build | degraded | **hit** | **15.59 s** |
| same state again | degraded | hit | 14.87 s |
| pristine staging, startup build (relocation succeeds) | ok | miss | 43.28 s |
| 1 % ORIGIN-row nudge (new target) | ok | miss | **57.84 s** |
| CSV-only edit on another row — compile and target unchanged | ok | **hit** | **19.83 s** |

So the memo removes the instancer and the header fit from a build whose compile
and target are unchanged: **57.84 s → 19.83 s (−38.0 s)** on the success path
and **37.25 s → 15.59 s (−21.7 s)** on the failure path (the failure path also
skips the ~27 s inflation, which is why its saving is smaller than the success
path's). Both memo entries from the two misses were present afterwards
(9,099,908 B and 8,975,448 B), and the hit wrote nothing new.

- **(i) CSV-only edit after a successful relocation** — rows 5–6 of the table.
- **(ii) Second build after a failure, same target** — rows 1–2: the failure
  is answered from the memo instead of re-running the instancer.
- **(iii) A source edit invalidates the memo** — covered by
  `test_a_source_edit_invalidates_the_memo` (changes one glyph's width in
  CrispyMini → second build reports `miss`). Live, the same mechanism showed as
  the pristine staging's startup build missing against the accumulated state's
  entry.
- **(iv) Exports use the same memo** — `_export_pipeline` now passes
  `state.RELOCATION_MEMO_DIR` too, so a repeat export of the same style hits.
  Not yet measured live (an export run is a full build + transform chain +
  WOFF2); the export path calls the same `_build_in_process` with
  `relocate_target`, which is the code under test.

**Acceptance — same output content as an uncached run.** The memo stores the
post-relocation bytes, so a hit copies them into `out` and the later steps
(avar2, fvar instances) run exactly as they did before. The test compares
`pipeline._font_content_digest` of the cached and uncached outputs; byte
equality is not available because `head.modified` is a fresh timestamp on every
save (`tests/test_avar2_default_relocation.py`, both memo tests).

## Notes for the next round

1. **`head.modified` defeats byte comparison of two builds.** Any "same bytes"
   claim about two separate builds has to compare table content, not files. The
   S1(b) reference-build assertion was changed to `_font_content_digest` for the
   same reason.
2. **Worker log lines are invisible.** `build_jobs` spawns the worker with
   `stdout=DEVNULL, stderr=DEVNULL`, so every `log.info` inside a build is lost
   unless the supervisor copies it into the result. Any future "the log says"
   evidence for build-time behaviour needs a field, not a log line.

## S4 — cached source loader in the read-only routes (2026-10-20)

Seven route call sites parsed an 11 MB transformed `.glyphs` on every request.
Six of them only ask the parsed font for names/axes and mutate the *file*
through their own load, so they now use the mtime-keyed `load_source_cached`:
`routes/instances.py` ×4 (the auto-save PUT, rename, promote-to-source, delete)
and `routes/control_axes.py` ×2 (the glyph fingerprint, the axis name→tag map).
The A18 pre-flight was already cached. The third location in the review,
`routes/load.py`, turned out to be the upload route's own function name
(`def load_source()`), not a parse site.

Measured on the copy at 5072, `PUT /api/instance/<row>?csv_only=true`, five
alternating values (so nothing dedupes), median of five:

| | median |
| --- | --- |
| before (uncached `load_source`) | **2,951 ms** |
| after (`load_source_cached`) | **6.5 ms** |

**One behaviour had to be preserved.** `load_source_cached` called `stat()`
before the cache lookup, so a *missing* path raised `FileNotFoundError` where
the uncached loader raised from the parse — four writeback tests caught the
difference (they point `GLYPHS_PATH` at a shadow that does not exist and expect
the route's fallback). The cached loader now falls through to `load_source`
when `stat()` fails, so "the source cannot be read" means the same thing on
both paths (`tests/test_source_writeback_targets.py`, 4 tests).

## S5 — idle means no build AND no compress (2026-10-20)

`building` clears when the build lands, but the WOFF2 compress job runs after
it, so a page load in that window asked for `/api/font?format=woff2` and got a
409 (three console errors per load, A20). Three call sites now wait for the
queue to be empty, not just for `building` to clear:

- `InstanceRows.jsx` and `PreviewTab.jsx`: the preview-font registration is
  skipped while `build_job` is set (and re-runs when it clears);
- `App.jsx`: the "rebuild once when Preview opens stale" effect also requires
  `buildJob == null`.

The probe already treated `build_job` as busy (`ui_probe.py:71`).

## S1(c) — the Preview under a degraded build (2026-10-20)

When `/api/health` carries `origin_relocation_error`, the font's default could
not be moved onto the ORIGIN row, so its own avar2 table no longer anchors the
resting state: driving the user axes renders the compiled origin, not the
authored style. `PreviewTab` now pins the parametric axes at
`/api/mapped-location`'s post-mapping values (the oracle the tab already
fetches for the slider read-outs — the same technique the instance rows use)
and holds the user axes at their defaults, so the canvas still follows the
sliders. A one-line amber notice under the axis panel says the resting state is
approximated by the mapping until the ORIGIN row is back on a master, and that
exports will say the same.

## S1(d) — the ORIGIN badge tooltip (2026-10-20)

`csv_io.origin_master_report()` now rides each matched row as
`origin_master: {nearest, on_master, max_delta, delta}` — the nearest compiled
master to the row's parametric `out` and the per-axis gap. The ORIGIN badge
tooltip appends it: on a master it says the origin is a point the source
already interpolates; off a master it names the nearest one and the largest
gaps, and says the build has to synthesise the origin with the instancer (which
is what densifies gvar).

On the Crispy copy the ORIGIN row (`Default 400 MaxOpsz`, out XTRA 663.1 /
XOPQ 202.5 / YOPQ 121.8) reports `nearest 47-1-1, on_master False, delta
{XTRA 616.1, XOPQ 201.5, YOPQ 120.8}` — an interior point, which is why the
relocation has to run at all. The 1 % nudge moves only the XOPQ term
(201.5 → 203.5).

## S2 — one build per import: does not reproduce at this head (2026-10-20)

The review measured five builds for one config import (`…-7/-9/-11/-13/-15`).
On this head the same action produces **exactly one**:

| action | builds started |
| --- | --- |
| `POST /api/config/import` with the current bundle (dry_run false) | 1 |
| the same import with one mapping cell changed (149.9 → 151.5) | 1 |

Commands: `GET /api/config/export` → `{"bundle": <bundle>, "dry_run": false}`,
then count `[jobs] build-… started` in the server log. The watcher's
self-write suppression (time window + mtime watermark) plus the coalescing
timer in `schedule_shadow_rebuild` cover the shadow/transformed copies this
import regenerates; nothing further was needed. **The multi-select broadcast
half of S2 is still unmeasured** (it needs the UI: LCWD across three rows, then
open Preview) — that is the one open piece.

## S1(a′) — the review's two items (2026-10-20)

**Hazard closed: the plain-fallback picker can no longer return a memo file.**
`build_variable_font` ended with `output_dir.rglob("*.ttf")` and returned the
first hit; the memo lives at `<build>/.relocation-memo/<key>.ttf`, inside that
tree, so the fallback could hand back a relocated avar2 font from an earlier
compile as "the plain build". The pick is now one function,
`pipeline._pick_variable_font`, which skips the memo directory and takes the
newest candidate; pinned by
`test_the_plain_build_picker_never_returns_a_memo_file` (a memo entry with a
deliberately newer mtime still loses to the real build output). The other
pickers (`server.py` 509/514, `routes/build.py` 190, `build_jobs.py` 66,
`pipeline.py` 1141) are flat `glob("*.ttf")` and cannot see the memo tree.

**Proof added: the memo sits BEFORE gen-avar2.**
`test_a_relocation_is_memoised_and_reused` now changes one mapping row's `out`
between the two builds and requires `memo == "hit"` *and* the second font to
carry a regenerated avar table — plus a direct check that every memo entry has
**no** `avar` table while the built font has one. So a mapping-only edit costs
a memo hit and still re-runs gen-avar2, which is why the output content matches
an uncached run.

## S1(c) — overrides win (round-11 correction, 2026-10-20)

`renderCoords` pinned *every* parametric axis to the oracle, including ones the
designer had dragged. The mapping now lives in `frontend/src/preview-coords.js`
(`previewRenderCoords`), where an overridden axis keeps its slider value:

    pinned[tag] = overrides.has(tag) || oracle is not a number
                  ? effectiveCoords[tag]      // explicit input / no oracle
                  : oracle                    // follow the mapping

With the user axes held at their defaults the avar2 delta is zero, so the
override renders exactly — the one mode where a direct parametric input is
exact. Pinned by `frontend/src/preview-coords.spec.mjs` (chained into
`npm run test:unit`): healthy pass-through, degraded pinning, override wins,
missing oracle falls back, tag case preserved.

## S2 — the broadcast half, measured through the UI (2026-10-20)

`/private/tmp/av2-routine/s2_broadcast.py` drives the real page on the 5072
copy (multi-select rows 1–3, drag LCWD, open Preview) and counts
`[jobs] build-… started`:

| step | PUTs | builds |
| --- | --- | --- |
| LCWD broadcast over 3 multi-selected rows | 3 (one per row) | **0** |
| opening Preview (stale) | — | **1** |
| whole action | 3 | **1** |

So S2 is met in both halves: one config import → one build, and a 3-row
broadcast plus Preview → one build.

## S1(c) — drift probe on a reproducing degraded build (2026-10-21)

Fresh scratch (`/private/tmp/av2-routine` was cleared overnight), pristine
Crispy copy restaged, then `s1_recipe.py` re-ran the round-10 recipe to reach
the failure for real: control axis TST2 + seeded braces + grade + Kinetikern →
`ok`, 13 axes, 10,894,284 B, `g` 1,333 tuples; ORIGIN nudge
(XOPQ 202.5 → 187.89) → **`degraded`**, 13 axes, 792,256 B, `g` 86; second
nudge → same. So the state the probe runs against is the one the review
objects to, not a synthetic one.

`/private/tmp/av2-routine/drift_probe.py` drives the Preview tab at three wght
inputs and compares three things: the oracle **the tab itself fetched** (its own
`/api/mapped-location` request/response, captured from the network), what the
sample element actually renders with (computed `font-variation-settings`), and
the rendered advance of `g`/`e`/`B` against fontTools instancing the served
font at those same coordinates:

| wght | applied XOPQ | oracle XOPQ | gap | user axes at defaults | drift g / e / B (font units) |
| --- | --- | --- | --- | --- | --- |
| 1 | 15.00 | 15.00 | **0.00** | yes | 0.09 / 0.03 / 0.03 |
| 400 | 170.16 | 170.16 | **0.00** | yes | 0.12 / 0.00 / 0.00 |
| 1000 | 597.92 | 597.92 | **0.00** | yes | 0.12 / 0.12 / 0.03 |

Worst render drift **0.12 font units** against the 3-unit bar; the notice
(“.preview-relocation-notice”) renders once, screenshot
`/private/tmp/av2-routine/ui-shots/s1c-notice.png`.

**Correction to my first attempt at this probe**, worth recording because it
looks like a bug and is not: it re-requested `/api/mapped-location` with only
`{"wght": value}` and got *different* numbers from the page (e.g. 618.79 vs
597.92 at wght 1000). The tab sends **every** axis — the parametric ones at
their fvar defaults (XTRA 47, XOPQ 1, YOPQ 1…), not at the ORIGIN row's out —
and the mapping is evaluated there. The page is self-consistent: it applies the
oracle it asked for, exactly. A future probe must capture the tab's own request
rather than re-deriving it.

## ADR 0001 — amendment for (b)/(c) (2026-10-21)

`docs/decisions/0001-build-time-default-relocation.md` gained the amendment
paragraph the S1 spec asked for: why a relocation that cannot fit keeps the
avar2 font and reports `degraded` (rather than `failed`, which would close the
build gate the designer needs to walk the ORIGIN row back), how the Preview
approximates the resting state under it, and the memo that pays the relocation
cost once per (compiled font, target).

## Part A self-run at the S1 checkpoint (2026-10-21)

Fresh scratch, pristine Crispy copy restaged (`sources/` + sidecars +
`.avar2-studio`), server on 5072 with the SPAC injector and Kinetikern dylib.

**A1 launch gate — PASS**

```
origin_warning: None
font_built: True | last_build_status: 'ok'
relocation_error: None | memo: 'miss'
font: Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf
      10,533,616 B | 12 axes | opsz default 144 | 30 rows
```

**A2–A16, A20 (`ui_probe.py`, one run): 16/16 PASS, exit 0, 17 screenshots**
(`/private/tmp/av2-routine/ui-shots/`):

| scenario | A# | scenario | A# |
| --- | --- | --- | --- |
| select-no-save | A3 | flyout-source | A7 |
| preview-no-build | A4 | lifecycle | A8 |
| sidebar-edit-saves | A5 | multi-broadcast | A9 |
| console-clean | A20 | grade | A10 |
| first-load | A2 | control-axis | A11 |
| sync-dots | A6 | kinetikern | A13 |
| export | A12 | tester | A14 |
| config-roundtrip | A16 | load-font | A15 |

**A17 watcher — PASS**: an external save of the source (new bytes + new mtime)
started **exactly one** build (`first seen after 46.3 s`, status `ok`).

**A18 build gate — PASS**:

```
baseline:  status='ok'     font=HTTP 200 10,533,616 B PUT=200
truncated: status='failed' (2 s) error="The source file cannot be read: Expected
           terminating ')' ..." font=HTTP 200 10,533,616 B PUT=409
restored:  status='ok'     (36 s)  font=HTTP 200 10,533,616 B PUT=200
```

The last-good font keeps serving through the failure (same byte count), every
mutating route answers 409, and the save recovers the build.

**Not run this session: A19 (static demo) and A21 (clean install).** Both need
artifacts this scratch does not have — A19 a served static build, A21 a fresh
venv install of the wheel — and faking them from the dev server would prove
nothing. They stay open for the verifier's fresh-clone run.

### Two traps this run found (both in the harness, not the product)

1. **A18's restore must bump the mtime.** `shutil.copy2` restores the *old*
   mtime, so the watcher correctly ignored the "restored" write and the studio
   stayed `failed` for the whole 181 s timeout. `os.utime(path, None)` after the
   copy makes it a real external save again (recovered in 36 s).
2. **A15 leaves the studio on the uploaded copy.** `load-font` uploads the
   staged `.glyphs` (no sidecars), so the studio drops to the bare 3-axis source
   and a 68 KB font. A17/A18 run immediately afterwards measured *that* source,
   not the copy — they must run against a server started on the copy (or after
   re-attaching the sidecars). My first A17/A18 attempts reported "0 builds" and
   "truncation did not fail" for exactly this reason; both were harness order,
   not product behaviour.

## S1(c) — the oracle evaluates the AUTHORED mapping (round-12 finding 1, 2026-10-21)

The review is right, and my drift probe could not have caught it: it compared
the canvas with the oracle **the tab itself fetched** (self-consistent by
construction) and, with auto-opsz on, never sat at the default inputs — so it
never compared against the authored row.

**Fix.** `pipeline._authored_mapping_table()` builds the mapping the authored
rows describe with the parametric defaults moved onto the ORIGIN row's `out` —
the table the font would carry if the relocation had been possible. It needs
only `fvar` (no gvar), costs milliseconds, and runs when the relocation fails;
`/api/mapped-location` evaluates it instead of the built table whenever
`origin_relocation_error` is set. The result travels as `{axes, segments}`
through the build report → `state.LAST_ORIGIN_AUTHORED_AVAR` → the worker and
job transfer lists, and the failure's memo entry carries it too, so a memo hit
does not lose it.

**Tests (acceptance iii).**
`test_the_degraded_oracle_uses_the_authored_mapping`: at the ORIGIN inputs the
parametric axis reads its **authored out** (663.1) rather than the compiled
default (47.0), a mapped stop still maps (wght 700 → 550), and the authored
data is ignored when there is no relocation error.
`test_a_degraded_build_records_the_authored_mapping`: the degraded build's own
report carries the ORIGIN row's out (XTRA 800, XOPQ 300) as the parametric
defaults, with non-empty segments.

**Live acceptance (i)/(ii): not yet reproduced here.** My staged copies
inherit the Crispy repo's `.avar2-studio` studio staging, which already
declares a `TST2` axis; the recipe then adds a second one and the build now
fails *earlier* (`avar2_error … 'TST2'`, 11 axes, 697,316 B, status `ok`), so
it no longer reaches `degraded`. The verifier's own copies (`crispy-r12…`) do
not carry that residue, so (i)/(ii) are cleanest there — or from a staging made
without `.avar2-studio`.

*(Checked while investigating: the Crispy working tree's dirty files are
pre-existing WIP — mtimes Oct 10–16, before this session. Nothing here wrote to
`~/Documents/Crispy`; every staging lives under `/private/tmp/av2-routine`.)*

## Finding 2 — open-editor says what is missing (2026-10-21)

`server.FontraUnavailable` is raised from `_ensure_fontra_running` **before any
spawn** when `fontra` or `fontra_glyphs` is not importable; the route answers
**503** with `Fontra is not installed: pip install git+…fontra… fontra-glyphs`
and `optional_dependency: "fontra"`, instead of "fontra exited immediately
(exit code 1)".

`tests/test_open_editor_fontra.py`: with `find_spec` answering like a clean
venv, `POST /api/control-axes/XTRA/open-editor` returns 503 with that message
and **nothing is spawned** (a stub `Popen` fails the test if it is), and
`_ensure_fontra_running` raises the same diagnosis for any future caller.

## S1(c) again — the oracle now evaluates the WHOLE authored avar (2026-10-21)

The round-12 note is right: my `_authored_mapping_table` serialised only
`avar.segments` (the avar **1** per-axis maps) and the mapping that moves the
parametric axes lives in the avar **2** `VarStore`, so the oracle returned the
ORIGIN row's `out` at *every* input — and my unit test encoded that by using a
hand-written segment table and asserting the axis did not move.

**What changed.** `_write_authored_mapping_font()` generates the authored
mapping (parametric defaults moved onto the ORIGIN row's `out`) and saves the
font **whole** as `<memo dir>/<key>.authored.ttf`; `fvar` and `avar` have to
come out of one object or the VarStore's RegionAxisCount stops matching the
axis list. `/api/mapped-location` loads that mini font and evaluates it through
`_evaluate_with_font` — the *same* function the built font uses, so the healthy
and degraded paths cannot drift apart in how they evaluate. It is cached per
(authored path, mtime), and the failure memo carries the path, so a memo hit
keeps it.

**Sidecar, not entry.** `<key>.authored.ttf` lives beside the memo entries and
`_memo_evict` now (a) does not count it against the keep-2 budget and (b) drops
it when its entry goes, plus any orphan whose entry is already gone.

**Acceptance at unit level** (`tests/test_avar2_default_relocation.py`):

- (i) at the ORIGIN inputs the parametric axes read the row's `out`
  (XTRA 800 / XOPQ 300 on crispy-mini) — within 1 u;
- (ii) at **two** other authored rows' inputs they read *those* rows' `out`
  (wght 900 → 400/700, wght 1 → 3000/10) — within 1 u;
- (iii) the table is a real gen-avar2 one: `avar.majorVersion == 2`, and the
  parametric axis demonstrably **moves** with wght (> 100 units), which the
  segment-only version could not do;
- eviction: the authored sidecar survives while its entry lives and goes with
  it.

**Live acceptance (iv) — not run here, and the two ways my staging fails to
reproduce `degraded` are worth recording:**

1. *With* the repo's `.avar2-studio` (the normal staging): it already declares
   a `TST2` axis, so the recipe adds a second one and the build fails earlier
   (`avar2_error … 'TST2'`, 11 axes, 697 KB, status `ok`) instead of degrading.
2. *Without* `.avar2-studio`: the axis metadata is missing, so the generated
   axes take default = min (opsz 14, wdth 5, wght 1), no row sits at the
   defaults, the relocation has no target and never runs — status `ok`,
   792 KB, `origin_warning: None`.

So (iv) wants a staging that keeps `.avar2-studio/axis-metadata.json` but not
the shadow/control residue — which the verifier's own `crispy-r12` copies
already are (they reached `degraded` with 14 axes). Recommend running
`degraded_preview.py` + the drift probe there.

*(Environment note: `/private/tmp` was wiped twice today, taking the scratch
with it. Every staging is recreated from `~/Documents/Crispy/sources`, which
remains untouched — its dirty files predate this session.)*

### S1(c) round-12 note 3 — served defaults are translated, not taken as overrides

The oracle now evaluates the **authored** model (whole-`fvar`+`avar` mini font,
`.authored.ttf`) — but the Preview sends **every** axis, with the
non-overridden parametric axes at the *served* font's `fvar` defaults. On
Crispy those served defaults are the axis minimums, while in the authored
model the defaults have moved onto the ORIGIN row's `out`. Taken literally, an
echoed served default normalises to −1 and the whole mapping clamps at the
minimum — which is exactly the round-12 live symptom.

`_translate_served_defaults()` now rewrites the incoming coordinate dict before
evaluation: for every axis whose default *moved* between served and authored,
a value equal to the served default (within 1e-6) becomes the authored
default. Anything else is an explicit override and stays absolute. Skipped
when `state.VARIABLE_FONT_PATH` is unset (nothing served to translate from).

`tests/test_avar2_default_relocation.py::test_the_degraded_oracle_handles_the_full_coordinate_dict`
sends the dict exactly as `PreviewTab` builds it and pins the acceptance table:

| inputs (all axes, served defaults unless noted) | XTRA | XOPQ |
| --- | --- | --- |
| none overridden (ORIGIN) | 800 | 300 |
| `wght = 900` (Default/MaxOpsz row) | 400 | 700 |
| `wght = 1` (Narrow/MinOpsz row) | 3000 | 10 |

within 1 u, and the test first asserts the served defaults really are the
*unmoved* ones (so it cannot pass for the wrong reason).

Gates at this state: `ruff check src tests` clean; `pytest tests/ -q` →
**562 passed** (561 + the new test), 3/3 consecutive runs of the relocation
file green (12 passed).

Live acceptance (iv) still wants the verifier's `crispy-r12` copies, for the
staging reasons recorded above.

## Phase C — CI-grade `avar2-studio build` (items C1–C9)

Agent root: `~/avar2-agent/` (staging under `~/avar2-agent/c9` for this
item; the loop from C8 will use `~/avar2-agent/`). Ports used by the agent:
5072 (its studio) — never 5070/5071 or Fontra 8001.

| ID | Result | Evidence |
| --- | --- | --- |
| C9 | PASS | `--fontra-port` + `AVAR2_FONTRA_PORT`; two studios, each with its own Fontra. Unit: `tests/test_fontra_port.py` — 5 passed (env wins over the default; bad env falls back to 8001; the flag beats the env at startup; an out-of-range flag exits 2; `open-editor` answers with the configured port). Live: studios on **5072** (`--fontra-port 8002`) and **5074** (`--fontra-port 8004`) over two copies of `examples/crispy-mini`; `POST /api/control-axes/crbr/open-editor` → `port 8002, direct http://127.0.0.1:8002/editor.html?project=CrispyMini.glyphs` and `port 8004, …8004…`; both direct editors and both same-origin proxies answered **200 at the same time**; server logs show `Started Fontra on http://127.0.0.1:8002` and `:8004` with their own roots. Studios and Fontra children stopped afterwards; 5072/5074/8002/8004 all closed. |
| C1 | PASS | Declared axis defaults ship with the source as the committed `<stem>-axis-metadata.json`. Unit: `tests/test_axis_metadata_sibling.py` (8) — sibling is canonical, the writer writes there, a legacy `.avar2-studio/` file is migrated once (content verbatim, legacy removed), the sibling wins when both exist, `_declared_axis_defaults` reads the explicit path, the CLI requires one of the two homes, an unreadable legacy file is kept and still read. Job staging carries the sibling (`SIDECAR_SUFFIXES`, covered by `tests/test_build_jobs.py`). Live (`~/avar2-agent/c1`, git-clean copy `git status --porcelain` = 0 lines before the build): `avar2-studio build src/Crispy.glyphs --out out` → exit 0, stderr carries no metadata warning; built fvar defaults wght 400 / wdth 100 / opsz 144; avar majorVersion 2; `_evaluate_with_font` at `Default 900 MaxOpsz` inputs → XTRA 725.89 (csv 726.0, Δ0.11), XOPQ 316.27 (316.3, Δ0.03), YOPQ 235.20 (235.2, Δ0.00). Same checkout without the metadata in either home → exit 2 and `error: axis metadata not found: neither Crispy-axis-metadata.json nor .avar2-studio/axis-metadata.json exists beside Crispy.glyphs…`. Studio on the same copy: `origin_warning` null, `/api/avar2/axes` metadata OPSZ default 144, build ok, `src/` still 0 dirty lines. |
| C2 | PASS | `--workdir DIR`: the tool workdir (config, shadow, build output, relocation memo, `instance-renames.json`, backups) resolves under DIR; authored sidecars stay beside the source. Unit: `tests/test_build_workdir.py` (4) — beside-source default, override path, `_apply_source_path` creates nothing beside a committed checkout, the CLI sets the override before loading the source. Live (`~/avar2-agent/c2`, fresh `git clone` of the committed copy): without the flag → `git status --porcelain --ignored` = `!! src/.avar2-studio/` (1 line); with `--workdir work` → exit 0 and **0 lines**, workdir under `work/.avar2-studio/{config.yaml,build,jobs}`. Font equality: A-vs-B differ only in `head` (`head.modified` 3876025681 vs 3876025604); every other table is sha256-identical. The build is not byte-reproducible run to run — two builds *without* the flag differ the same way (c401d36b vs bd36a347) — so the acceptance is table-for-table identity, not raw file hash; pinning `head.modified` (SOURCE_DATE_EPOCH) would be a separate change. |
| C3 | PASS | Every failure reason reaches the exit message. `cli._failure_reasons` prints one `error:` line per recorded `LAST_*` reason; the degraded case now carries `origin_relocation_error` instead of "unknown". Unit: `tests/test_cli_failures.py` (7) — healthy = no reasons; degraded names the relocation error and never "unknown"; all four recorded errors are reported; failed-with-no-error still says so; **the three exit codes** 0/1/2 pinned (ok → 0 + font copied, degraded → 1, missing metadata → 2, unloadable source → 2); the `error:` prefix is asserted on captured stderr. Live (`~/avar2-agent/c3`, the `s1_recipe.py` state: control axis TST2 + braces + grade on `Default 1 MinOpsz` + a Kinetikern entry + the ORIGIN nudge): the studio reported `last_build_status degraded` with `origin_relocation_error` = "glyph 'g' has too many variation tuples…"; the CLI on the same copy exited **1** with `error: origin relocation failed (degraded build — the avar2 mapping is not the authored one): glyph 'g' has too many variation tuples for OpenType's per-glyph gvar limit after relocating the default (1767 tuples…)` — no "unknown". Exit 2 also seen live in C1 (missing metadata) and pinned in the unit test (unloadable source); exit 0 in C1. |
| C4 | PASS | Export options on the CLI, via `pipeline._export_pipeline` (no second pipeline): `--hide-axes XOPQ,XTRA`, `--default wght=400,opsz=14`, `--format ttf|woff2` (repeatable). Unit: `tests/test_cli_export_options.py` (12) — parsing, dedupe, no-export path copies the served font, flags reach the pipeline per format, malformed `--default` → exit 2, export failure → exit 1 with the readable message. Live (`~/avar2-agent/c4/mini`, the bundled example): CLI `--hide-axes XOPQ --default wght=400,opsz=14 --format ttf --format woff2` → exit 0, two artifacts (277,532 B TTF + 79,560 B WOFF2). Same options through the studio (`POST /api/export-font`, download) → 277,532 B TTF; table-for-table the two are identical except `head` (`head.modified`); raw sha256 7c40ccdd… vs ee243e07…. fvar: `XOPQ` hidden flag **True**, all others False; defaults re-origined to `wght 400`, `opsz 14` (others rebased). WOFF2 decompresses to the same 18-table set as the TTF. |
| C5 | PASS (locally simulated; the workflow itself runs on the checkpoint push) | (a) `.github/workflows/test.yml` gains a `cli-build` job: non-editable `pip install .` into a throwaway venv, an assertion that `avar2_studio` resolves from **site-packages**, then `avar2-studio build examples/crispy-mini/sources/CrispyMini.glyphs --out … --workdir …` from outside the checkout, then a `git status --porcelain --ignored` check (excluding the `__pycache__/hatch_build…pyc` that `pip install .` itself leaves). Simulated locally on a fresh clone of `0a080d2` in `~/avar2-agent/c5`: `pip install .` → `/…/venv/lib/python3.11/site-packages/avar2_studio/__init__.py`; build → exit 0 in 2.83 s + 0.64 s compress, artifact `out/CrispyMini[SPAC,XOPQ,XTRA,YOPQ,opsz,wdth,wght].ttf` 83,716 B; status check → 0 lines. (b) README "Building in CI": install line (fontc + gftools are regular pinned dependencies), `doctor --strict` + `build … --config … --workdir …`, exit codes 0/1/2, a paste-ready Actions snippet, and the note that `AVAR2_STUDIO_GFTOOLS_BUILDER=1` stays internal. The Command-line table documents `--fontra-port`, `--workdir`, `--hide-axes`, `--default`, `--format`; "Your files" lists the committed `<stem>-axis-metadata.json`. |
| C6 | PASS | `avar2-studio doctor --strict` exits 1 only when a **headless-build prerequisite** is missing — fontc, gftools-builder, glyphsLib, or a native engine the environment points at (`CheckResult.build_prereq` / `configured_missing`); UI-only checks (frontend bundle, Fontra) are reported but do not fail it. Default mode is unchanged (any failed check → 1). `doctor.main(argv)` now takes its own args (the CLI passes them through). Unit: `tests/test_doctor_strict.py` (7) — strict pass, missing fontc named with its fix command, UI-only failures ignored, configured-but-missing engine fails, default mode still reports every failure, engine env handling. Live: `avar2-studio doctor --strict` → exit 0, `All build prerequisites present.`; with `PATH=/tmp/fakebin:/usr/bin:/bin` (fontc and gftools-builder off PATH) → exit 1, `✗ fontc not on PATH`, `✗ gftools-builder not on PATH`, `2 build prerequisite(s) missing.` plus both `brew install fontc` / `pipx install gftools` fix lines. |
| C7 | PASS | One committed `<stem>-build.json` drives the whole export: `axes.hide`, `default`, `transforms` (params; `instances.fix/stat` fold into the two gftools transforms), `formats`. The build reads it by default when it sits beside the source (no flags in CI); C4 flags override it; `--write-config` emits it from the sidecars. Design-authored state stays read-only: a config naming `control`/`grade`/`kinetikern`/instance coordinates is exit 2 with "… is studio-authored state — open the source in avar2-studio and edit it there". Transforms come from the committed sidecar unless the config overrides them **for that run** (`transforms.config.set_override`, the sidecar is never rewritten). Unit: `tests/test_build_config.py` (16) + 5 CLI wiring cases in `tests/test_cli_export_options.py`. Live (`~/avar2-agent/c7`, git-clean clone + committed `Crispy-build.json`): `avar2-studio build src/Crispy.glyphs --out out --workdir work` (no flags) → exit 0, TTF + WOFF2, checkout 0 dirty lines. Font: `XOPQ` hidden, defaults `wght 400` / `opsz 14` (parametric axes rebased), **9 named instances**, **STAT** present, `SPAC` axis from the config's `spac_widthaware` params; WOFF2 table set == TTF. Studio export with the same choices on the same copy: every table identical except `head` (raw sha256 3e562b66… vs 5d379cc5…), same hidden flags, same defaults, 9 instances. Config naming `control` → exit 2 with the studio pointer (live). |
| P3 | PASS | C7's transform override now reaches the **worker build**: `build_jobs._stage` puts `transforms.config.override()` in the job spec (`transform_override`) and `build_worker._bind_server_globals` installs it before the build, so a config's transforms and the folded `instances.fix/stat` entries apply at the resting style too — the committed `<stem>-transforms.json` is still never rewritten. Unit + end-to-end: `tests/test_build_config_worker.py` (3) — the spec carries the override and the worker bootstrap returns it verbatim (sidecar file unchanged), no override leaves the sidecar in charge, and a real worker build with a config that disables the sidecar's `spac_widthaware` produces a font **without** the SPAC axis. Live, the verifier's failing case (`~/avar2-agent/p3`, git-clean clone, committed `Crispy-build.json` with `default: null`, `transforms` = fix_instances + gen_stat only, `instances.fix/stat` true; sidecar has round_corners + slant + spac_widthaware on): `build` → exit 0, font `Crispy[GRAD,LCWD,XOPQ,XTRA,YMOD,YOPQ].ttf` — **no ROND, slnt or SPAC**, **9 named instances**, **STAT** present; checkout 0 dirty lines. Before the fix the same build left `slnt` and one instance in the font (the worker read the sidecar). |
| P1 | PASS | `tests/test_fontra_port.py` no longer asserts that Fontra is importable — the launcher is stubbed, so the assert only coupled the test to the venv and failed on a plain `pip install -e .[dev]` clone (the verifier's 1 failed / 627 passed, and both CI python jobs since `65d2039`). `pytest tests/test_fontra_port.py -q` → 5 passed. |
| P2 | PASS | An existing but unparseable source is a **LOAD** error: `avar2-studio build` probes the source with the cached loader after applying it and exits **2** with `error: Crispy.glyphs could not be read: <parse message>` — before a worker burns a build on it (the queued auto-build is cancelled), never `build failed: … object has no attribute …`. Unit: `tests/test_cli_failures.py::test_an_unreadable_source_exits_2_with_a_readable_message`. Live (`~/avar2-agent/p2`, a one-line garbage `.glyphs` beside a valid metadata sibling): exit **2**, `error: Crispy.glyphs could not be read: Unexpected character after key at line 1: 'a'`, no output directory, no worker left running. |
| K1 (route) | PASS | `PUT /api/kinetikern` (designer B7 K1, backend half): `{enabled: bool}` → `sidecar.set_enabled`, and one `schedule_shadow_rebuild` **only when the value changes** (a redundant PUT must not queue a build). 400 without the `enabled` key or without a source; a non-bool is coerced with `bool()`. `tests/test_kinetikern_enabled_route.py` → 3 passed (400 missing key, 400 no source, on→off schedules once / repeat schedules none / off→on schedules again). The new rule shows up in `tests/test_url_map_snapshot.py` as its own entry (`'/api/kinetikern OPTIONS,PUT'`) — the contract snapshot gained a rule, it did not change one. The UI half (Transforms-menu toggle + per-row badge) is a separate commit. |
| K2 | PASS | "Apply to all…" makes the overwrite visible instead of skipping tuned styles: the button carries the slider's value (`Apply −0.60 to all…`) and the confirm names every style that already has its own looseness and what it will become. `frontend/src/kinetikern-copy.js` (`applyAllLabel`, `applyAllConfirm`, `spacedStyles`) + `e2e/kinetikern-copy.spec.mjs` (chained into `test:unit`): negative/zero/positive labels, one tuned style → "Default 400 MinOpsz (-0.60) … will be re-spaced at 0.00", plural for two, nothing tuned → the plain confirm. Guide updated (`docs/guides/kinetikern.md`: it replaces each style's own looseness). No route change. |
| K3 | PASS | Spaced styles are marked with words, not a bare "●": the dropdown reads `Default 400 MinOpsz · spaced −0.60`, the Revert button reads `Revert Default 400 MinOpsz` (its title still explains the build-disabled state), and the slider sync effect now also depends on `entry?.looseness`, so a copy whose initially selected style is the spaced one shows that value on mount. `instanceOptionLabel` / `revertLabel` pinned in `e2e/kinetikern-copy.spec.mjs` (spaced / unspaced option text, no dot marker, Revert naming). |
| K4 | PASS (pure half + placement) | (a) the Font Size control moved from the left column into `.kk-main` above the before/after panes, next to the sample text (still the shared value); (b) `pickInstance` sets the size from the style's `opsz` via `fontSizeForStyle` (clamped to 9–144 pt; no opsz → unchanged; Custom leaves it alone), and a manual slider move afterwards wins until the next pick. Spec cases in `e2e/kinetikern-copy.spec.mjs`: opsz 72 → 72, 144 → 144, 4 → 9 (clamped), no opsz → null. The placement is asserted in `ui_probe.py`'s kinetikern scenario (`.kk-main .font-size-control` present, plus the K3 option/Revert text after an apply). |

### Loop run 2026-10-28T10:43:55Z — root `~/avar2-agent`, ports 5072/8002

- staged copy: `/Users/agyei/avar2-agent/runs/20261028-172608/checkout/src` (git-clean checkout, committed files only)
- git-clean check: **1** line(s) before the CLI build, **1** after
- `avar2-studio build` (installed package `/Users/agyei/avar2-agent/venv`): exit **0**
  - artifact: `/Users/agyei/avar2-agent/runs/20261028-172608/out/Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`
- studio on 5072 (Fontra 8002), state at probe time: idle
- `ui_probe.py all`: exit **1** — PASS 14 / FAIL 2 (log: `/Users/agyei/avar2-agent/runs/20261028-172608/probe.log`, shots: `/Users/agyei/avar2-agent/runs/20261028-172608/shots`)

  Failing scenarios:
  - FAIL control-axis: timed out
  - FAIL load-font: Page.click: Timeout 30000ms exceeded.
- reserved paths/ports untouched: `~/Documents/Crispy`, 5070, 5071, 8001, `~/avar2-studio-test`

<!-- run 2026-10-28T11:00:01Z discarded: the probe was killed (exit 143)
     by the executor's own cleanup pkill, so it produced no verdict. -->

### Loop run 2026-10-28T11:14:39Z — root `~/avar2-agent`, ports 5072/8002

- staged copy: `/Users/agyei/avar2-agent/runs/20261028-180003/checkout/src` (git-clean checkout, committed files only)
- git-clean check: **0** line(s) before the CLI build, **0** after
- `avar2-studio build` (installed package `/Users/agyei/avar2-agent/venv`): exit **0**
  - artifact: `/Users/agyei/avar2-agent/runs/20261028-180003/out/Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`
- studio on 5072 (Fontra 8002), state at probe time: idle
- `ui_probe.py all`: exit **1** — PASS 15 / FAIL 1 (log: `/Users/agyei/avar2-agent/runs/20261028-180003/probe.log`, shots: `/Users/agyei/avar2-agent/runs/20261028-180003/shots`)
  (the probe's studio runs from the checkout so the bundled examples exist; the CLI build above came from the installed package)

  Failing scenarios:
  - FAIL load-font: [Errno 2] No such file or directory: '/Users/agyei/avar2-agent/runs/20261028-180003/checkout/src/.avar2-studio/axis-metadata.json'
- reserved paths/ports untouched: `~/Documents/Crispy`, 5070, 5071, 8001, `~/avar2-studio-test`

### Loop run 2026-10-28T11:31:51Z — root `~/avar2-agent`, ports 5072/8002

- staged copy: `/Users/agyei/avar2-agent/runs/20261028-181713/checkout/src` (git-clean checkout, committed files only)
- git-clean check: **0** line(s) before the CLI build, **0** after
- `avar2-studio build` (installed package `/Users/agyei/avar2-agent/venv`): exit **0**
  - artifact: `/Users/agyei/avar2-agent/runs/20261028-181713/out/Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`
- studio on 5072 (Fontra 8002), state at probe time: idle
- `ui_probe.py all`: exit **0** — PASS 16 / FAIL 0 (log: `/Users/agyei/avar2-agent/runs/20261028-181713/probe.log`, shots: `/Users/agyei/avar2-agent/runs/20261028-181713/shots`)
  (the probe's studio runs from the checkout so the bundled examples exist; the CLI build above came from the installed package)
- reserved paths/ports untouched: `~/Documents/Crispy`, 5070, 5071, 8001, `~/avar2-studio-test`

### Loop run 2026-10-28T15:46:49Z — root `~/avar2-agent`, ports 5072/8002

- staged copy: `/Users/agyei/avar2-agent/runs/20261028-223121/checkout/src` (git-clean checkout, committed files only)
- git-clean check: **0** line(s) before the CLI build, **0** after
- `avar2-studio build` (installed package `/Users/agyei/avar2-agent/venv`): exit **0**
  - artifact: `/Users/agyei/avar2-agent/runs/20261028-223121/out/Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`
- studio on 5072 (Fontra 8002), state at probe time: idle
- `ui_probe.py all`: exit **1** — PASS 15 / FAIL 1 (log: `/Users/agyei/avar2-agent/runs/20261028-223121/probe.log`, shots: `/Users/agyei/avar2-agent/runs/20261028-223121/shots`)
  (the probe's studio runs from the checkout so the bundled examples exist; the CLI build above came from the installed package)

  Failing scenarios:
  - FAIL kinetikern: no K badge on the spaced row (Default 1 MaxOpsz)
- reserved paths/ports untouched: `~/Documents/Crispy`, 5070, 5071, 8001, `~/avar2-studio-test`
| K1 (UI) | PASS (loop run `20261028-225007`: **16/16**) | Spaced rows now carry a `K -0.60` badge and the Transforms menu has a Kinetikern section. First loop run (`20261028-223121`) **failed** the probe on `no K badge on the spaced row (Default 1 MaxOpsz)`: the badge read the sidecar from `useStudioData`, whose only refresh was the 2 s health poll, and the assert ran before that tick. Fixed at the source rather than by sleeping in the probe: `KinetikernTab.reloadStored` pushes the payload it already fetches into the shared state, so an apply/revert here badges the row immediately, and the probe now waits for the badge to attach/detach (60 s) instead of snapshotting. The scenario asserts: badge text starts `K `, the menu toggle is on with `1 style spaced`, off → `/api/kinetikern` `enabled false` **with the entry still listed**, badge gone, copy starts `Off`; on → `enabled true`, entry back, badge back. The build-honours-off seam is `sidecar.entries()` (returns `[]` while off), pinned by `tests/test_kinetikern_enabled_route.py`. `e2e/kinetikern-copy.spec.mjs` pins the badge and menu strings. |
| CI0a | PASS (local) | The launcher test no longer waits exactly as long as the launcher does: `READY_TIMEOUT` (what the launcher allows, 20 s) and `WAIT` (the test's ceiling, 3× that) are now separate, so a loaded runner is not read as a dead launcher. `pytest tests/test_glyphs_launcher.py -q` → 5 passed. |
| CI0b | PASS (local, root cause) | `No such file or directory: 'fontmake'` was not a test fixture problem: `build_variable_font` looked fontc up with `shutil.which` only, so a build started without the venv on PATH (CI's `.venv/bin/pytest`, the Glyphs launcher, an absolute-path launch) fell through to fontmake — not a dependency — and died. It now also looks for fontc beside `sys.executable`. `tests/test_fontc_lookup.py` → 2 passed (PATH wins when both exist; beside-the-interpreter is used when PATH has none, and a fontmake fallback fails the test). |
| CI0c | PASS (local) | The clamp oracle's reference build is fontmake, which the rust job never installed; the comparator now exits **3** (clean "cannot judge") when fontmake is absent instead of raising `FileNotFoundError`, the Rust test accepts 3 with a comment, and the rust job installs fontmake so the anti-mangling bound actually runs. `python wasm/fontc-web/spike/compare_clamp.py` (no args, fontmake present) still reaches the usage path; the Rust change is a two-line assertion change (no cargo run locally — the fontations stack is a 10+ min cold build, and CI is the acceptance). |
| CI0 (acceptance) | PASS | A fully green **Test** workflow on the checkpoint push: run `40721221145` (commit `2237df9`) — **python (ubuntu), python (macos), rust (ubuntu), rust (macos), frontend, cli-build all success**, 3m05s. That run is the acceptance the user set for CI0a–c; the three fixes it needed are the rows above plus the reverse-DNS one (`HTTPServer.server_bind` doing `socket.getfqdn` cost 35 s per server on the macOS runner, so the launcher's stub missed its 20 s deadline). |
| K1 (parity) | PASS | `api.setKinetikernEnabled` shipped in K1's `api.js` without an entry in `e2e/api-parity.spec.mjs`'s `DESKTOP_ONLY`, which the frontend CI job caught (`FAIL setKinetikernEnabled — live-only and not in DESKTOP_ONLY`). Registered against the `kinetikern` capability, like its five siblings; `node e2e/api-parity.spec.mjs` → all passed. |
| CI0a (real cause) | PASS | The launcher test's failure was not impatience: `test_probe_tells_free_other_and_studio_apart` took **35.05 s** on the macOS runner. `HTTPServer.server_bind` calls `socket.getfqdn` for `server_name`, a reverse DNS lookup that has no resolver on the runner and blocks for tens of seconds — the stub in `STUB_STUDIO` therefore reached `serve_forever` long after the launcher's 20 s deadline. Both the fixture and the stub now bind through a `_QuietServer` that skips the lookup (127.0.0.1 only, the name is never used). `pytest tests/test_glyphs_launcher.py -q` → 5 passed in **4.0 s** (was 8.6 s), and the wider `WAIT` from CI0a stays. |

### Loop run 2026-10-28T16:08:11Z — root `~/avar2-agent`, ports 5072/8002

- staged copy: `/Users/agyei/avar2-agent/runs/20261028-225007/checkout/src` (git-clean checkout, committed files only)
- git-clean check: **0** line(s) before the CLI build, **0** after
- `avar2-studio build` (installed package `/Users/agyei/avar2-agent/venv`): exit **0**
  - artifact: `/Users/agyei/avar2-agent/runs/20261028-225007/out/Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`
- studio on 5072 (Fontra 8002), state at probe time: idle
- `ui_probe.py all`: exit **0** — PASS 16 / FAIL 0 (log: `/Users/agyei/avar2-agent/runs/20261028-225007/probe.log`, shots: `/Users/agyei/avar2-agent/runs/20261028-225007/shots`)
  (the probe's studio runs from the checkout so the bundled examples exist; the CLI build above came from the installed package)
- reserved paths/ports untouched: `~/Documents/Crispy`, 5070, 5071, 8001, `~/avar2-studio-test`
| K1b | PASS | The badge popover said "deltas live in the Kinetikern **sidecar**, not in this row" — UI copy never names sidecar/JSON/CSV files (the standing rule). The string moved out of `KinetikernBadge.jsx` into `kinetikern-copy.js` as `badgeHint(looseness)`: "Spaced by Kinetikern at -0.60. The deltas live in the Kinetikern tab, not in this row." `e2e/kinetikern-copy.spec.mjs` pins it (carries the value, names the tab, says the row does not own the spacing) and asserts it contains none of `sidecar`, `JSON`, `CSV`, `.json`, `.csv` — plus the same check on the Transforms menu copy. Grepped the rest of `frontend/src`: every other `sidecar` hit is a code comment or docstring, so this was the only user-visible one. |

### Loop run 2026-10-29T08:12:21Z — root `~/avar2-agent`, ports 5072/8002

- staged copy: `/Users/agyei/avar2-agent/runs/20261029-145640/checkout/src` (git-clean checkout, committed files only)
- git-clean check: **0** line(s) before the CLI build, **0** after
- `avar2-studio build` (installed package `/Users/agyei/avar2-agent/venv`): exit **0**
  - artifact: `/Users/agyei/avar2-agent/runs/20261029-145640/out/Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf`
- studio on 5072 (Fontra 8002), state at probe time: idle
- `ui_probe.py all`: exit **0** — PASS 16 / FAIL 0 (log: `/Users/agyei/avar2-agent/runs/20261029-145640/probe.log`, shots: `/Users/agyei/avar2-agent/runs/20261029-145640/shots`)
  (the probe's studio runs from the checkout so the bundled examples exist; the CLI build above came from the installed package)
- reserved paths/ports untouched: `~/Documents/Crispy`, 5070, 5071, 8001, `~/avar2-studio-test`
| CI0d | PASS (local) | `test_a_failed_relocation_is_memoised` compared the two saved fonts' raw bytes, so two builds straddling a second boundary differ in `head.modified` and the test is intermittent on a slow runner (red on the macOS legs of runs 40723781794 and 40716800627, never locally). It now asserts `pipeline._font_content_digest(second) == pipeline._font_content_digest(first)` — the helper excludes `head` for exactly this reason — and, to prove the hypothesis rather than hide it, that the set of tables whose bytes differ is a subset of `{'head'}` (the failure message lists the differing tables). The new `_differing_tables()` helper was checked directly: identical copies → `set()`, a bumped `head.modified` → `{'head'}`, a changed `name` table → `{'head', 'name'}`. `pytest tests/test_avar2_default_relocation.py -q` → 12 passed; `pytest tests/ -q` → 637 passed; `ruff check src tests` clean. |
