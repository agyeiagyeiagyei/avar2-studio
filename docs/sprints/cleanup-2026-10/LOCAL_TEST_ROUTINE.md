# Local test routine — sprint `cleanup-2026-10` on a Crispy copy

Hand-off for verifying the sprint build end to end before anything is
pushed to Pages or merged. Two halves: **Part A** is what an agent runs
(Playwright + API + file checks), **Part B** is what only the designer
can judge or reach (Glyphs, Fontra, installed fonts, deploys). Results go
in `LOCAL_TEST_RESULTS.md` next to this file, one line per test ID:
`ID | PASS/FAIL/SKIP | evidence (command, request log, screenshot path,
table dump)`. A claim without evidence is NOT-VERIFIED.

## Ground rules

- **Never** touch `~/Documents/Crispy` (sources or sidecars) and never
  start, stop or probe port **5070**. Everything runs on a copy.
- The copy: stage the current on-disk `Crispy.glyphs`, `Crispy-avar.csv`,
  `Crispy-control.json`, `Crispy-grade.json` and `Crispy-transforms.json`
  from `~/Documents/Crispy/sources/` **and the designer-authored state
  inside `.avar2-studio/`**: `axis-metadata.json` (the declared axis
  defaults — `opsz` default 144 is what makes `Default 400 MaxOpsz` the
  ORIGIN row; without it the registered convention 72 applies, no row sits
  at the defaults, and the studio shows the origin warning),
  `instance-renames.json` and `default-origin/`. Never copy `build/`,
  `jobs/`, `shadow/`, `backups/` or `*.ninja`; the studio regenerates
  those. The working tree there is dirty by design; copy what is on disk,
  do not check out.
- Staging check before any test: `/api/health` → `origin_warning` is
  null and `/api/avar2/axes` → `OPSZ.default` is 144. If either fails the
  copy is incomplete; fix the staging, do not work around it.
- Isolate the server: its own port (5072 here) and `HOME=<scratch>` so
  `~/.avar2-studio/` is never written. Open-editor spawns Fontra on 8001
  (single global port); kill it with the server.
- A full Crispy build is ≈40 s plus ≈15 s WOFF2 compress. `POST
  /api/build` blocks until the build ends. Between tests that build wait
  for BOTH `building: false` and `build_job: null` on `/api/health`: the
  compress job outlives `building`, and `/api/font?format=woff2` answers
  409 (a console error in Chrome) until it finishes.
- Verify font-level claims by reading the built tables with fontTools,
  never by eyeballing the preview. Verify UI claims with screenshots you
  have looked at, plus the request log.

## Setup

```bash
WT=~/Documents/avar2-studio-sprint          # the sprint worktree (branch cleanup-2026-10)
PY=~/Documents/avar2-studio/.venv/bin/python
SCRATCH=/path/to/scratch; mkdir -p $SCRATCH/sources $SCRATCH/home
SRC=~/Documents/Crispy/sources
cp $SRC/Crispy.glyphs $SRC/Crispy-avar.csv $SRC/Crispy-control.json $SRC/Crispy-grade.json $SRC/Crispy-transforms.json $SCRATCH/sources/
mkdir -p $SCRATCH/sources/.avar2-studio
cp $SRC/.avar2-studio/axis-metadata.json $SRC/.avar2-studio/instance-renames.json $SCRATCH/sources/.avar2-studio/
cp -R $SRC/.avar2-studio/default-origin $SCRATCH/sources/.avar2-studio/

# bundle: build and copy into the served static dir (no --delete: static/.gitkeep is tracked)
(cd $WT/frontend && npm ci && npm run build) && rsync -a $WT/frontend/build/index.html $WT/frontend/build/assets $WT/src/avar2_studio/static/

# Accelerator paths MUST be absolute: in `HOME=... VAR=~/x cmd`, zsh expands
# the tilde against the NEW home, so the server silently loses both engines
# (SPAC falls back to fontTools, Kinetikern answers 503).
HOME=$SCRATCH/home PYTHONPATH=$WT/src \
AVAR2_SPAC_INJECT=$WT/wasm/fontc-web/target/release/spac-inject \
AVAR2_KK2_DYLIB=$WT/native/kinetikern2/target/release/libkinetikern2.dylib \
$PY -m avar2_studio $SCRATCH/sources/Crispy.glyphs --port 5072 > $SCRATCH/server.log 2>&1 &

curl -s localhost:5072/api/health   # wait for font_built true, building false, last_build_status ok

# static (Pages) demo, optional for A19
(cd $WT/frontend && npx vite build --base=/ --outDir dist-pages && cd dist-pages && python3 -m http.server 5075 &)

# headless browser for Part A
pip install playwright && playwright install chromium
python3 -I $WT/docs/sprints/cleanup-2026-10/ui_probe.py --url http://localhost:5072 all
```

`ui_probe.py` covers A3, A4, A5 and A20 and is the template for the
rest: every scenario records non-polling requests with timestamps,
captures console errors, screenshots, and asserts. Extend it rather than
hand-driving the browser; keep a scenario per test ID.

Useful selectors: tabs `button.main-tab:has-text('Preview')` (also
Instances, Kinetikern, Tester); rows `[data-instance-name='<name>']`;
sliders `input.axis-slider[aria-label*='(wght)']` (Preview sliders live
under `.preview-tab`, sidebar sliders outside it); Preview reset
`button.preview-reset`; landing overlay `button.dl-enter`.

## Part A — agent (Playwright, API, files)

Each test: steps → expected → evidence. "No build" means no `POST
/api/build` in the request log and no `[jobs] build-… started` line in
`server.log` during the test.

| ID | Area | Steps | Expected |
| --- | --- | --- | --- |
| A1 | Launch | Start the server as above; `GET /api/health`, `/api/build/status`, `/api/axes`, `/api/avar2/axes`, `/api/instances` | `status ok`, `font_built true`, `last_build_status ok`, `origin_warning` null; `OPSZ.default` 144 and `Default 400 MaxOpsz` is the ORIGIN row; axes include the parametric trio, user axes, LCWD/YMOD, GRAD/slnt/ROND/SPAC as configured; instance list matches the copy's CSV rows |
| A2 | First load | Open `/`, dismiss landing if shown; tabs render; row count = `/api/instances`; `document.fonts` holds the preview family | Zero console errors; row names match; font registered (check `document.fonts.check` or `.status === 'loaded'`) |
| A3 | Select ≠ edit (round-8 fix) | Click a studio row, wait 3 s, open Preview | No PUT, no build, `build_stale` stays false (`ui_probe.py select-no-save`) |
| A4 | Preview sliders | Preview → drag wght, wdth, opsz, a parametric axis; Reset | Only `GET /api/mapped-location` traffic; the mapped parametric read-outs change; Reset restores every default; no build (`preview-no-build`) |
| A5 | Auto-save + one rebuild | Select a studio row, drag XOPQ in the sidebar; then open Preview | Exactly one `PUT /api/instance/<name>?csv_only=true` ≈0.8 s after the drag ends; CSV row in the copy holds the new XOPQ; `build_stale` true; Preview open → exactly one build; afterwards `build_stale` false, new font URL (`sidebar-edit-saves`) |
| A6 | Sync dots | After A5: the edited row's dot is orange (studio-saved, not in source); a source row untouched is green; a row with an unsaved draft (drag then compare before the 0.8 s save) is red | Class names on the row's status element match; screenshot |
| A7 | Flyout → source | On a SRC row, change a coordinate, flyout "Save to source file" | `PUT` without `csv_only`; a build starts immediately; the copy's `.glyphs` instance has the new value (glyphsLib read, not the UI); CSV synced; dot green |
| A8 | Row lifecycle | Create new studio instance; duplicate; rename; delete (modal) | Each lands in `/api/instances` and the CSV; rename rebuilds; delete asks for confirmation and removes the row + CSV row; selection state sane after each |
| A9 | Multi-select broadcast | Shift-click two rows, drag LCWD once | One PUT per selected row, each with only LCWD; other coordinates of those rows unchanged on disk (control sidecar) |
| A10 | Grade | Header → Transforms → enable Grade; set a grade on a row's G badge; wait for build; `GET /api/transforms/grade` | GRAD axis in `/api/axes` and in the built font's `fvar`; advance widths unchanged between GRAD 0 and the row's grade for a sample (fontTools `hmtx` after instancing) |
| A11 | Secondary parametric axis | Add a control axis (modal), then `POST /api/control-axes/<tag>/open-editor` | Axis appears in `/api/axes` with `is_control_axis`; shadow regenerates; open-editor returns `editing_original false`, project = the shadow; Fontra answers on 8001; close it |
| A12 | Export | Preview → download: TTF at default; WOFF2; TTF at a non-default location with "set default" | Sizes shown before download match the files within 1 %; TTF opens with fontTools, axis set equal to the preview's; set-default export: instancing at the chosen location vs the preview font at the same absolute parametric location → point counts equal, max drift ≤ 3 u (the drift probe from round 7); at wght 900 either succeeds or returns the readable gvar-limit error, never a traceback |
| A13 | Kinetikern | Kinetikern tab: pick an instance, change looseness, Apply; `GET /api/kinetikern` | Proposal returns; Apply persists per-instance values; the built font carries the `KKRN` marker table; Revert clears |
| A14 | Tester | Tester tab: type text, toggle bold/italic, alignment, letter-spacing | Renders with the preview family; no console errors |
| A15 | Load Font | Header → Load: switch to a built-in example, then back to the copy's `.glyphs` | Rows and axes swap; no leftover editing state (selection cleared); health `family_name` follows |
| A16 | Config round-trip | Header → export config; re-import it | Import is a no-op diff on the sidecars (compare files before/after) |
| A17 | Watcher | With the server idle, rewrite the copy's `.glyphs` via glyphsLib load+save; watch `server.log` for 60 s | Exactly one build (the T0.7 acceptance probe); no "Unexpected EOF"; UI shows the rebuild then the fresh font |
| A18 | Build gate | Break the copy (e.g. corrupt a glyph's path in the `.glyphs`), trigger a build; try a PUT; restore; rebuild | `last_build_status failed` with a readable `last_build_error`; mutating requests get 409; the last-good font keeps serving; after restore + rebuild everything clears |
| A19 | Static demo | Open `http://localhost:5075`; switch examples; move Preview sliders | No request to `/api/*`; wasm compile succeeds; sliders drive the font; zero console errors |
| A20 | Console hygiene | Visit every tab once (`console-clean`) | Zero console errors across A2–A19; warnings listed in the results file |
| A21 | Clean install | Fresh clone of the sprint branch, venv, `pip install -e .[dev]`, pytest, ruff, frontend lint/typecheck/test:unit/build, cargo test | Same green as round 7 (533/536 tests with the engine env vars, ruff clean, lint 0 errors, Rust 22/22) |

Stop and report (do not work around) anything that needs the real
Crispy, the real 5070 studio, or a push.

## Part B — designer (what an agent cannot judge or reach)

| ID | Area | Steps | What you are judging |
| --- | --- | --- | --- |
| B1 | Glyphs round-trip | Open the **copy** in Glyphs 3; edit a glyph; save | One rebuild, preview updates with the edit; no second build; the instance rows survive the save |
| B2 | Instance edit in Glyphs | Change an instance's coordinates in Glyphs; save | The row updates in the studio and its dot turns green; the CSV matches |
| B3 | Fontra editing | Open a secondary axis in the editor; move a brace layer's points; save | "Rebuilding preview…" appears, the preview catches up, the shape is what you drew; the original `.glyphs` is untouched (`editing_original false`) |
| B4 | Shapes and mapping | Preview at the corners: Narrow/Default/Wide × 1/400/900/1000 × opsz 14/144 | Outlines, counters past wght 900 (the deliberate ultra fill), rounding, slant and LCWD/YMOD behave as designed; nothing kinks or collapses |
| B5 | Set-default export in a design app | Export with "set default" at a non-default style; open the TTF in Figma/Illustrator/Font Book | The resting style IS the chosen one; the named instances read right; every axis range intact |
| B6 | WOFF2 on a page | Drop the WOFF2 into a test page in Safari, Chrome and Firefox with `font-variation-settings` | Renders identically to the TTF; hidden axes still respond; file size as the modal predicted |
| B7 | Spacing feel | Compare the SPAC axis and a Kinetikern proposal at three styles with real text | Which one you would ship per style; whether "either/or" holds up in practice |
| B8 | Grade in use | Set a grade on a text style; set the global grade scale to 50 % and 0 % | Darkening without reflow at text sizes; the white-room cap behaves (styles with tight counters do not darken) |
| B9 | Responsiveness | Drag sliders, select rows, switch tabs while a build runs | Nothing feels stuck; the build veil tells you what is happening; no surprise rebuilds (the round-8 bug) |
| B10 | Glyphs plugins | `avar2-studio install-glyphs-plugins` from the studio venv, restart Glyphs, use the avar2 Studio hub on the copy | The hub opens the studio on the current font; the design tools still run |
| B11 | After the push | Pages site, fontra-embed deploy, the release wheels (doctor finds both accelerators on a clean install) | Only confirmable by pushing; do this last, after A1–A21 and B1–B9 pass |
| B12 | The real studio | Only after everything above: start 5070 on the real Crispy | Same behaviour as the copy; this is the acceptance |

## Reporting

- Append rows to `LOCAL_TEST_RESULTS.md` as you go; commit the results
  file and any probe-script changes to the sprint branch (approved);
  nothing else is pushed without a decision.
- A FAIL gets: the test ID, the request log or command output, the
  screenshot, and the smallest reproduction. Do not fix and re-report in
  the same line; a fix is a separate commit with the FAIL referenced.
