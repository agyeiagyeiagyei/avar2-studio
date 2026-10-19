# Local test results — sprint `cleanup-2026-10`

Run of `LOCAL_TEST_ROUTINE.md` on a copy of Crispy. One line per test ID:
verdict + the evidence that backs it. A claim without a command,
request log, screenshot path or table dump is NOT-VERIFIED.

## Environment

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

## Part A — agent

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
