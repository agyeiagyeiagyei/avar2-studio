# avar2-studio — codebase & process audit (2026-10-16)

Input for the cleanup doc. Six parallel read-only audits (server/API,
build-export-worker pipeline, control axes + editor integrations, frontend,
Rust/tests/dev-process, documentation) over the working tree as of
2026-10-16: branch `github-pages`, HEAD `6ca2c75`, plus uncommitted work
(Kinetikern tab + engine, tag-case preservation, README/HANDOVER edits).
Line numbers are from that snapshot; symbol names are the stable reference.

Legend: **[V]** verified by reading the cited code, running a probe, or
reproducing; **[I]** inferred from evidence, not reproduced. Effort S/M/L.

---

## 0. Executive summary — what changes what you do next

1. **Preview builds drop uncaptured Fontra drawings [V].** The worker
   snapshot (`build_jobs._stage`) copies the original, the sidecars and
   UFOs but never `.avar2-studio/shadow/`; the worker's
   `_resolve_active_source` regenerates the shadow from original + sidecar
   with `preserved_layers = {}`. Drawings reach the sidecar only via
   `capture_outlines`, whose sole caller is config-bundle export
   (`config_port.py:94`). Anything drawn in Fontra since the last config
   export is absent from every preview build (exports stage the server-side
   active source as `active-<name>`, so they are only partly affected).
   Regression introduced by the worker rework; on Crispy 28 control-axis
   layers currently have neither a captured outline nor a computed one.
2. **Untouched brace seeds silently stop following master edits [V, probed].**
   `regenerate_shadow` keeps a prior brace whenever its nodes differ from
   the current default master — which, since seeds interpolate at their
   corner, is always. Edit a master → seeds freeze; `reseed` reports them
   clean. The seed signature that would settle "seed vs edit" is computed
   (`_restamp_seeds`) but never consulted in regen.
3. **Desktop edit modal still loses drawings [V].** The list endpoint strips
   `outline`/`source_sig` (server.py ~4520), so `LayersEditor`'s carry-over
   guard copies `undefined`; `apply_layer_delta` has no carry-over; the
   shadow copy is keyed by location and goes too.
4. **A fresh clone cannot `pip install` [V, reproduced].** `a550f7c` deleted
   `src/avar2_studio/static/.gitkeep` on this branch; hatch's
   `force-include` of that dir fails metadata generation. `pages.yml`
   hard-codes the `mkdir -p` workaround.
5. **Work at risk [V].** 7 commits unpushed (~10 days: job queue, native
   SPAC, WOFF2, source cache); the entire Kinetikern feature (engine,
   bridge, package, tests, tab) is untracked; `native/kinetikern2` is
   ignored only by an uncommitted `.gitignore` line.
6. **The build gate turns "cancel" into a stuck studio [V].** Cancelling a
   job sets `LAST_BUILD_STATUS="failed"`, and the `before_request` gate
   then 409s every mutating request until a build succeeds
   (`test_cancel_a_running_build` asserts this behaviour).
7. **Mid-build saves leave the server stale [V].** `_run_one` runs only the
   CSV hook on `source_refresh`; nothing re-runs `_resolve_active_source`
   server-side, so `/api/instances`, `/api/axes`, coverage keep serving the
   old shadow (observed 2026-10-15: a PATCH was needed to refresh), and the
   CSV is synced from the stale transformed copy.
8. **One file, two locks, most writers take neither [V].** `-control.json`
   mutators `add_axis`/`update_axis`/`remove_axis`/`reseed` run unlocked;
   instance values use a second lock; 12 CSV write sites truncate in place
   with no lock and no atomic rename, from three different threads.
9. **The UI unmounts on every data refresh [V].** `loadData` flips a
   full-screen "Loading…" gate; toggling a transform on the Preview tab
   resets sliders, overrides and font size.
10. **Live and static API clients have drifted with no parity check [V].**
    9 live methods missing in static (Kinetikern ships half-implemented on
    Pages), 4 static-only, signature drift on `openControlAxisInEditor`,
    `getMasters` stubbed to `[]` in the demo.
11. **Export job polling keys on `status.last` [V code / I race].** Any job
    finishing inside the poll window overwrites `last`; the client then
    spins up to 20 min and reports a timeout although the artifact exists.
12. **Nothing is gated by CI [V].** No test/lint/cargo workflow; ruff not
    installed; the release workflow can publish an untested tag; the
    committed 7.45 MB wasm blob is stale vs `round_corners.rs` and is ~65 %
    of a 116 MB `.git`.
13. **Docs are out of step [V].** README links/images 404 (10 of 13 images
    never committed; links to `blob/main` for files absent on `main`);
    HANDOVER.md mixes live decisions with dead history and contradicts the
    code in ≥6 places; 7+ env vars vs 3 documented; no architecture,
    pipeline-order, sidecar-schema, or job-API doc anywhere.

---

## 1. Repository & process

**State [V].** `github-pages` is the working branch (24 ahead of `main`,
which is stale since `a37ca53`, 2026-09-12). Crispy's CI installs
`avar2-studio @ git+…@github-pages` — the branch is load-bearing and
undocumented. 7 merged local side branches linger; tags stop at
`v0.1.0.dev6` although `a550f7c` is "Release 0.1.0" and `pyproject` says
`0.1.0`.

**Packaging [V].** 12 runtime deps; `fontmake`, `ufoLib2`, `brotli` are
imported directly but only satisfied transitively; `fontra`/`fontra-glyphs`
optional and undeclared. Wheels never carry `spac-inject` or
`libkinetikern2.dylib`; discovery is `Path(__file__).parents[3]/…` (checkout
only) → a pip-installed studio silently falls back to the fontTools SPAC
path whose gvar overflow the native injector exists to avoid
(`builtin_spac_widthaware.py` logs only on native *failure*, not absence);
`/api/kinetikern*` answers 503. `doctor` checks neither binary and checks
`fontc` on the plain PATH, not the venv-first `_builder_env` PATH the build
uses.

**Two venvs [V].** `.venv` (dev8 metadata) and `~/Documents/Crispy/venv`
(dev0 metadata, older fontTools/glyphsLib/gftools, no fontra) are both
editable installs of this checkout. The 2026-10-14 `flask_sock` boot
failure was the Crispy venv's editable metadata never re-synced after
`pyproject` deps changed — the mechanism remains.

**Git weight [V].** `fontc_web_bg.wasm` 7.45 MB × 25 revisions ≈ 76 MB
compressed; 0 packs, 4,073 loose objects (`git gc` never run);
`wasm/fontc-web/target/` 9.3 GB and `pkg/` (stale Aug build) on disk.

**CI [V].** `pages.yml` (snapshot + vite build; no wasm-pack, so Pages ships
the committed blob) and `release.yml` (sdist/wheel + PyPI; stale CRA
comments; Node 20 vs 22 in pages). No pytest, ruff, tsc, eslint or cargo
job. `eslint.config.js` and `tsconfig.json` exist but no npm script runs
them (eslint: 4 errors + 52 warnings; `tsc --noEmit` fails on
`HeaderProps`).

**Watcher lifecycle [V].** The watchdog `Observer` is scheduled once in
`main()`; `/api/load-source` never reschedules it (TODO at server.py:8133),
so a source swap or blind launch has no file watching. `OBSERVER` is a
local of `main()` read in its `finally` → `UnboundLocalError` on shutdown
when the watcher branch never ran.

---

## 2. Build / export / worker pipeline

**Map [V]** (worker subprocess unless noted): trigger (request/timer/
watcher thread) → coalesce (`JOBS.request_build`) → `_stage` snapshot
(supervisor thread; CSV hook `sync_csv_with_glyphs` *writes* the CSV here,
unlocked) → spawn `python -m avar2_studio.build_worker` → worker imports
`server` (constructs a Flask app and starts a supervisor thread it never
uses) → `_resolve_active_source` (control shadow → grade braces →
source-stage transforms slant 10 / round 50 over `shadow/transformed/`) →
`config_generator.update_config` → fontc → `fix_font` + STAT → **step 3.5
default-origin relocation** (instancer, `AVAR2_RELOCATE_BUILD_DEFAULT`
default on) → gen-avar2 (shim) → fvar instances → `_apply_transform_chain`:
strip injected axes → Kinetikern bake (uncommitted) → font-stage transforms
(SPAC via native `spac-inject`, 300 s timeout, fontTools fallback) →
ROND/slnt stamps → `_stamp_control_instance_values` → `result.json` →
supervisor `_promote` → low-priority WOFF2 compress job.
Export: `POST /api/export-font` → 202 → same worker; with
`default_location` it runs `_build_export_source` (interpolated origin
master) **and then step 3.5 again**.

**Findings.**
- H1 snapshot omits the shadow → drawings dropped (summary #1) [V].
- H2 mid-build source save: CSV synced from stale shadow; read endpoints
  stale until something forces a resolve (summary #7) [V].
- H3 export relocation densifies the shipped font [V path / I size]:
  `_export_pipeline` rounds the origin location (`round(mapped)`) while the
  ORIGIN row keeps CSV decimals (662.2/630.9 on Crispy); `_build_in_process`
  then sees a 0.2-unit mismatch and runs the instancer rebase anyway — the
  ~7× gvar growth the inserted-origin method exists to avoid. No per-call
  switch exists; relocation reads the env only.
- H4 cancel → gate 409 (summary #6) [V]; `cancel_all` during
  `_apply_source_path` can let the old job's `_finish` land after the new
  source reset state.
- M1 Kinetikern `strip()` removes every tuple that is phantom-only on
  XTRA/XOPQ/YOPQ — the current preview font already has 22 such genuine
  compile tuples in `three`; the marker should be explicit, not a shape
  heuristic [V].
- M2 double build after every server-side resolve [I]: the suppression
  window is set before the source-stage chain, the `transformed/` copy is
  written ~3 s later outside the 2.5 s window, and `GLYPHS_PATH.parent` is
  that directory. Check the log for "Source changed mid-build" after a
  control-axis edit.
- M3 designspace sources: the recursive watch covers `.avar2-studio/jobs/`,
  where staging copies UFOs → `.glif` events → perpetual rebuild [I].
- M4 export poll keyed on `status.last` (summary #11) [V].
- M5 shared-state gaps: supervisor writes the CSV unlocked; CSV, grade and
  transforms sidecars are written non-atomically (control and kinetikern
  are atomic) [V].
- M6 `_running` cleared before `_read_result`/`_promote`; `wait_for_idle`
  can return the previous outcome [V].
- M7 native vs fontTools SPAC divergence [V]: Rust splits the CSV on `,`
  without quoting, requires a literal `SPAC` header, takes `cells[0]` as the
  name and voids the column on one bad cell; Python uses `DictReader` on
  `Instance Name`. Instance names with commas silently lose per-instance
  SPAC on the native path. Rust-side VarStore padding for MVAR/BASE/VVAR/
  COLR/GDEF not verified.
- L: `/api/build` also waits on exports; `.avar2-studio/build/` accumulates
  (33 files / 78 MB after the tag-case rename; newest-by-mtime wins);
  canonical `config.yaml` `sources:` dangles at a deleted job dir;
  `sources/fonts/variable/` leftovers beside the designer's sources;
  sidecars are not watched at all (an atomic replace of `-control.json`
  never triggers; observed 2026-10-15); `_gc_paused` is redundant in the
  worker; `_read_phase` re-reads the whole `progress.jsonl` per 2 s poll.
- Three CSV readers and three parametric/user axis-split rules
  (config_generator, csv_io, server) plus grade/kinetikern re-parsing by
  hand, plus the Rust injector's own parser [V].

**Performance (measured on Crispy) [V].** transformed source 7.3 MB, 7,649
layers, 36 masters (9 real × slant/ROND twins); preview TTF 9.9 MB, 12
axes, 110,302 gvar tuples over 117 glyphs (max `g` = 1,531); last build
≈36 s wall-clock (resolve → promote). Structural: source-stage chain +
7 MB glyphsLib save, fontc on 36 masters, relocation densification (the
dominant size driver), native SPAC re-encode, WOFF2 13–16 s off the preview
path. Removable: ~5 extra parses of `transformed/` per build
(`_load_axis_metadata` ×3 via `_declared_axis_ranges`, config_generator
×2, ≈5 s), no-op strips on fresh compiles, the M2 double build if
confirmed. The 179 s `last_build_time` seen on 2026-10-14 is a Unix
timestamp field misread as a duration; measure `status.last.duration`.

**Parameterised default relocation for export (decided direction, not
built).** Needs `_build_in_process(..., relocate_default)` replacing the env
read (env as default, spec-driven per job); export passes `False`;
reconcile the `round()` vs decimal-row mismatch (tolerance or write the
rounded location back); carry the flag in `spec`, not `ENV_KEYS`; hoist
`_declared_axis_ranges` to one call per build; add the first export test
(`_export_pipeline` has none) asserting tuple counts stay natural.

---

## 3. Server / API architecture (`server.py`, 8,180 lines)

**Inventory [V].** ≈4,750 code lines, 1,358 comment-only, 767 docstring,
420 embedded JS, 886 blank; 152 top-level functions, no module classes.
71 `@app.route` on 70 handlers + 1 websocket; 37 mutable module globals,
75 `global` declarations across 31 names, reached into by `build_jobs`,
`build_worker` and ~60 `setattr(server, …)` sites in 17 test files. Four
locks (`SIDECAR_LOCK` at 3 sites, `_REBUILD_TIMER_LOCK`,
`_CSV_BUILD_TIMER_LOCK` dead, `_MAPPED_FONT_LOCK`); unprotected:
`GLYPHS_PATH` re-pointing, shadow regeneration, CSV writes, caches,
`EDITING_INSTANCES`. Embedded assets ≈22 KB: `_FONTRA_STUDIO_SHIM_JS`
(420 lines, hard-coded `'lcwd'` filter), focus CSS f-string, a 503 HTML
fragment, two AppleScript programs. Env: `AVAR2_STUDIO_DEMO`,
`AVAR2_STUDIO_GFTOOLS_BUILDER`, `AVAR2_RELOCATE_BUILD_DEFAULT`,
`AVAR2_STUDIO_DEBUG` (+ `AVAR2_SPAC_INJECT`, `AVAR2_KK2_DYLIB`,
`AVAR2_STUDIO_AXIS_RANGES` elsewhere). Import-time side effects: Flask app
+ CORS + Sock, `JOBS = JobQueue(...)` starts the supervisor thread,
`atexit`, `sys.exit` on missing glyphsLib.

**Correctness [V].** `--csv`/`--build-dir` are dead (reset by
`_apply_source_path`; the worker stages sibling sidecars only). `OBSERVER`
`UnboundLocalError` on shutdown. Watcher not re-pointed on source swap.
Multipart upload path traversal (`f.filename` unsanitised; demo mode only).
Suppression windows set *after* the writes they suppress. Six
`request.get_json()` sites without `silent=True` (415 HTML → "Invalid JSON
response" in the UI); unchecked `data['name'].strip()` → 500. Dead code:
`create_instance_in_glyphs`, `schedule_debounced_build` + its timer/lock,
routes `/api/avar2/mappings` and `/api/avar2/sync-csv` (callerless; the
latter duplicates `sync_csv_with_glyphs` line for line), five
`rebuild_in_background` threads wrapping a non-blocking call,
`get_font_family_name` parsing the whole source to return `path.stem`,
empty `routes/` dir. Rename/delete fan-out to grade/kinetikern/control
sidecars is `except Exception: pass`. 144 `except Exception`, 136
`print(file=sys.stderr)`, 0 `logging`.

**Concurrency [V].** `_resolve_active_source` (rewrites shadow, grade
shadow, transformed copy, re-points `GLYPHS_PATH`) runs unlocked from
request threads (7 call sites), the debounce timer, the watchdog thread,
and under `SIDECAR_LOCK` only in config import; overlapping callers write
the same `transformed/<name>.glyphs` while `_stage` copies it [I torn
snapshot → gate 409]. `_gc_paused` toggles process-wide GC from any thread.
Heavy request-thread work remains: synchronous resolve on 7 routes;
`PUT /api/instance/<n>` with source writeback runs osascript (10 s timeout)
+ a second full save; `/api/glyphs-file-status` spawns `osascript` every
2 s poll; `/api/text-width` re-parses and instantiates the VF per request;
`kinetikern_apply_all` runs the engine per instance synchronously.
Uncached `load_source` on read paths despite `6ca2c75`
(`_load_axis_metadata`, `get_avar2_axes` ×2, `_grade_instance_coords`,
`_grade_param_ranges`, `_get_glyphs_axis_tags`, `create_instance`).

**Maintainability [V].** Nine functions > 150 lines (`main` 323,
`_build_in_process` 258, `get_avar2_axes` 215, `update_instance` 211,
`_build_export_source` 186, …). History-as-comments: 41 lines of
slice/phase/"observed"/"used to"; stale examples contradicted by code 40
lines later; two 15–17-line METHOD NOTEs; module docstring still names
`glyphs-preview-server.py`. Duplication: CSV I/O three ways (20 raw `csv.*`
sites vs `_csv_io`), tag-case normalisation ad hoc at 37 `.upper()` sites
+ `.lower()` comparisons + a `[key, key.upper(), key.lower()]` probe while
`normalize_in_axis_name` exists; fvar `axis_by_tag` rebuilt 4×; four
overlapping excluded-tag sets; a hard-coded axis-name table
("X-Opacity" for XOPQ). Naming: `GLYPHS_PATH` is the active source of
either format; `*_from_glyphs` helpers take designspaces; singular
`/api/instance/<n>` vs plural elsewhere; `/api/transforms/grade` for a
non-transform.

**Decomposition proposal (order = least risk first).**
(a) assets → package files (`fontra/shim.js`, `fontra/focus.css`,
AppleScript → `glyphsapp.py`) + dead-code deletion, guarded by a
byte-identical `_rewrite_html_paths` output test;
(b) `state.py` for all globals + migrate the ~60 test `setattr` sites and
`build_jobs`/`build_worker` in one commit;
(c) `pipeline.py` (`_resolve_active_source`, `trigger_build`/`_run_build`,
`_perform_avar2_build`/`_build_in_process`, transform chain,
`_apply_source_path`) and make `build_worker` import it — no Flask in
workers (verify `python -c "import avar2_studio.build_worker"` pulls no
Flask);
(d) `export.py`, `fontra/{process,proxy}.py`, `source_ops.py`;
(e) one blueprint per commit (`routes/{ui,source,build,text_width,
instances,health,avar2,coverage,transforms_grade,kinetikern,control_axes,
config,load}.py`) with a `sorted(app.url_map.iter_rules())` snapshot test;
(f) `watch.py` with `start/stop/reschedule` so `/api/load-source` can
re-point it; `main()` shrinks to argparse + wiring.

---

## 4. Control axes, shadow, Fontra, Glyphs plugins

**Sidecar `<stem>-control.json` [V].** `version` read and re-emitted,
never checked; `axes[] {tag, display_name, default, min, max, layers[]}`;
layer `{glyph, location{tag:v}, target?, outline?{width, paths, components,
anchors}, source_sig?}`; `instance_values{tag:{instance:v}}`. One legacy
migration; no version gate; unknown fields dropped at every level (probed);
a corrupt file loads as empty and the next writer overwrites it (probed;
backup taken, nothing surfaced). `_save` backs up on every write, bounded
to 20 per filename — twenty slider moves evict the last pre-edit copy of
layers/drawings within seconds, and given #1/#3 backups are the only
recovery path.

**Findings [V unless marked].**
- Seeds frozen after master edits (summary #2, probed).
- Drawings persist only in the shadow until config export; modal loses
  them (summary #3).
- Tag-case leftovers after the uncommitted change: `update_axis` looks up
  `loc.get(tag_norm)` with the lowercased tag against authored-case keys
  (range validation silently skipped for uppercase tags — probed with
  `SUBS`); `FONTRA_EDITOR_SESSION` and four responses still `tag.lower()`
  (`test_open_editor.py:103` pins `"xouc"`); the shim's `labelRe` is built
  from `cfg.tag` while labels now print authored case; designspace labels
  and pool slugs stay lowercased.
- Two locks, most writers unlocked (summary #8).
- `.glyphs` vs `.designspace` paths drifted: the designspace regen ignores
  `target` and `outline`, has no seed signature, lowercases labels, has no
  instance aliases, "existing glyph wins" permanently, and writes
  non-atomically — while `create_control_axis` advertises both formats.
- Watcher: only `on_modified`, only the active source/original/`.glif`;
  sidecar edits by hand/git never regenerate the shadow.
- `_FONTRA_STUDIO_SHIM_JS`: 427 lines of JS in a Python string, no lint/
  tests, double-escaped regexes; fontra-embed's port has diverged (auto-
  enables editing on studio rows, adds a "→ computed" rule, lacks the
  source-name sort). Cross-repo contract bug: `metrics-hud.js` calls
  `getReferenceMetrics(g, loc)` but `embed-font.js` forwards `[refs]` only
  → the static HUD always measures at the opening layer.
- `_normalise_layers` docstring says last-write-wins; code is first-wins.
- Stale Fontra assumptions: "shadow/ contains only the shadow file"
  (predates `transformed/` and pooled UFOs; Fontra lists to depth 3);
  fontra-glyphs holds the parsed font in memory with no external-change
  watch, restart only on the next `open-editor` → a regen while the drawer
  is open leaves Fontra stale [I: a later save could write stale glyph data
  over the regenerated shadow].

**Glyphs plugins [V].** 7 bundles, 8,047 lines; plugins import nothing
from `avar2_studio`; 19 byte-identical function bodies across bundles
(~281 lines: `_currentFont`, `_showPanel`/`_hidePanel`, `_dbg`, `_rgba`,
…); the spacing contract is copied between `slant_math.py` and
`width_spacing.py` (README says "reuses"); `_path_segments`/`ink_span`/
`_compose` in three files. Seven identical 120 KB loader binaries tracked
(a Glyphs requirement; 840 KB in the wheel). `pip uninstall` leaves
dangling symlinks; a recreated venv stales the recorded interpreter;
`doctor` checks none of this. ParametricMasters is renamed "Metrics
Parity" in the UI only; `InstanceDelta`'s docstring describes a removed
heartbeat; all three of `glyph_coverage`, `regenerate_shadow` and the
bridge assume `font.masters[0]` is the default rather than Variable Font
Origin (consistent, undocumented).

**Proposed layout.** `control_axes/` package, format unchanged,
`__init__` re-exporting today's names: `schema.py` (load/save/normalise/
validate/lock/migrations/layer identity), `naming.py`, `shadow_glyphs.py`
(regen, seeding, virtual masters, restamp, extract), `shadow_designspace.py`
(parity or an explicit NotImplemented), `outlines.py`. Replace the Python-
string shim with one implementation: fontra-embed's `focused-ui.js`/
`metrics-hud.js` are already the port — have the desktop proxy inject a
built copy parameterised by the same session config (keeps GPL code in
fontra-embed, lintable, harness-testable), with a plain static asset as the
fallback.

---

## 5. Frontend (`frontend/`, 23,701 lines incl. 6,356 CSS)

**Inventory [V].** `App.jsx` 2,559 lines: 50 `useState`, 7 `useEffect` (4
with `exhaustive-deps` disabled), 22 `useCallback`; threads 44 props to
Sidebar, 32 to InstanceRows (InstanceRow takes 45), 20 to Header; drilling
depth 4. Six dead state hooks, two constant props still threaded. Orphans
with no importer: `Avar2Preview.jsx` (+css), `UpdateButton.jsx` (+css),
`ui/button.tsx` (+2 deps), and in the WIP `SpaceTab.{jsx,css}` (modified
*and* orphaned by the same WIP — resolve intent before deleting). Client-
side font math (`avar2-eval`, `var-model`, `grade-model`, `fvar`,
`coverage`/`gvar`, `mappings-csv`, `zip-workspace`) is a browser twin of
server/Rust code — `grade-model.js` is explicitly the third copy of one
model. TSX only for Header/ImportConfigModal/ui kit; `tsconfig` exists but
nothing runs it. No dark mode; 35 `:root` tokens mirrored by hand; hex
literals outside tokens (SpaceTab.css 59, Header.css 18, …). Vite dev proxy
covers `/api` only (Fontra routes unproxied → embedded editor cannot work
under `npm run dev` [I]).

**Findings [V].**
- Full-UI "Loading…" gate on every refresh (summary #9).
- Boot: `index.jsx` renders nothing until `selectApiMode` resolves (ladder
  1.5/4/8 s → up to 13.5 s blank); mount then fires health ×5,
  `avar2/instances` ×3, `avar2/axes` ×3, `glyph-coverage` ×2 before the
  first poll; with no built font `await api.buildFont()` runs *before* the
  data `Promise.all` under "Loading…". Residual static latch after >13.5 s
  of health silence.
- api.js ↔ static-api.js drift (summary #10); no parity check.
- Export poll on `status.last`; no `AbortController`; the modal's Cancel is
  disabled while exporting (summary #11).
- Four byte-similar 60 s "wait for !building" loops + the same
  font-reload dance; `waitForFontReady` re-created per render.
- Five different building/stale signals across Header, veil, ControlAxes,
  Kinetikern, FontraEditorModal; `buildStale` never shown; nine native
  `window.confirm`s beside React modals; one undismissable error banner.
- 52 `catch` blocks in App; auto-save and multi-select broadcast failures
  are `console.warn` only (sync dot lies).
- UI copy leaks file-format internals (project rule): "Save to avar2-studio
  (CSV)", "rewrites the sidecar", "Lives in <basename>-control.json",
  "studio sidecar", "avar2 mapping CSV", "JSON file", "Not valid JSON".
- Accessibility: sliders have no accessible name (label not associated),
  icon-only buttons title-only, hand-rolled modals without `role="dialog"`/
  Escape/focus management (only ImportConfigModal uses Radix Dialog).
- CSS: `.modal-overlay` ×3, duplicated rules across dead and live files,
  KinetikernTab depends on PreviewTab.css classes implicitly; three badge
  components share 70 of 83 lines.

**Shrink plan (M).** `useStudioData()` (data state, loaders, health poll;
gate only the first load), `useBuildStatus()` (one building/stale signal +
veil/banners), `useTransforms()` + context (−12 Header / −9 InstanceRows
props), `useControlAxes()` + context (−10 Sidebar props),
`useInstanceEditing()` inside an `InstancesTab` container; App ≈250-line
shell. `api.capabilities = {kinetikern, woff2, cancel, masters,
sourceWriteback}` per provider + a 20-line parity test. Static mode: keep
but fence (it is the README's front door and the broadest test), build the
wasm in CI, stop committing the binary, exclude static-only assets from the
desktop wheel via a Vite env flag.

---

## 6. Rust crate, tests, fixtures

**Crate [V].** `wasm/fontc-web`: cdylib + rlib, fontc/glyphs-reader/
fontdrasil from git `main` pinned only by `Cargo.lock`; no
`rust-toolchain`; wasm exports (`compile_glyphs`, `add_avar2`,
`apply_transforms`, `apply_control_axes`, `apply_grade`, `round_corners_
source`, `slant_source`, …) plus the native `spac-inject` binary — the
native build is documented only in `WORKER_BUILD_PLAN.md`. Every Rust
module is a port of a Python one (round corners, SPAC, braces/grade, slant,
STAT/fix) with cargo-side oracles that shell out to Python via a
hard-coded `/Users/agyei/…/.venv/bin/python` default (10 files). Four
oracles need hand-made `/tmp/fontc-wasm-spike.ttf` / `/tmp/av2-oracle.ttf`
(absent now → 4 of 14 test binaries fail); `correction_oracle` defaults to a
dead Claude-scratchpad path and returns silently → vacuous pass. Parity is
enforced only when someone regenerates `/tmp` by hand. `native/kinetikern2`
vendored (MIT, license present), macOS-only dylib naming.

**Python tests [V].** 441 collected / 441 passed / 29.1 s; 62 % are
Glyphs-plugin tests (fake modules, no app needed). `tests/fixtures/` is
empty; a documented `real_jobs` marker is unregistered. Untested: ~54 of 72
routes (`/api/build*`, `/api/export-font`, export download, control-axis
POST/PATCH/DELETE/layers/reseed/reference-metrics, config export/import,
avar2 axis POST/PUT, mapped-location, text-width, instance rename/
add-to-source/editing/DELETE, kinetikern GET/PUT/DELETE/all, Fontra proxy,
websocket); `build_worker.py` never executed (stub only); the real export/
WOFF2 path; `cli build`, `doctor`; SPAC injection and its fontTools
fallback; `builtin_gftools.py`; `_fontra_patch`/`_fontra_launch`;
`punctuation.py`. `fontc` is hard-coded at `REPO/.venv/bin/fontc` in 3
files (8 tests skip silently elsewhere).

**Recommendations.** Minimal CI (`test.yml`: ubuntu+macos matrix →
`mkdir -p static`, `pip install -e .[dev]`, `ruff check`, `pytest -q`;
frontend `npm ci && vite build` + lint + tsc; rust: toolchain + wasm32
target + rust-cache, `cargo test` for the crate, `cargo build --release`
for kinetikern on macOS, wasm compile check). Fixtures: generate the spike
font deterministically in-test from `examples/crispy-mini` via
`fontc_web::compile_glyphs` cached under `target/tmp` (keep
`AVAR2_SPIKE_FONT`/`AVAR2_ORACLE` overrides), derive python from
`CARGO_MANIFEST_DIR`, mark `correction_oracle` `#[ignore]` with a reason.
Add: one fontc-gated end-to-end worker test on crispy-mini (build → export
TTF/WOFF2 → parsable font), a Flask smoke table over the untested routes,
a pytest SPAC parity test (native vs fallback), a `_fontra_patch` unit test.
Stop committing the wasm blob (build in CI; LFS or release asset); `git gc`.
Longer term: platform wheels carrying `spac-inject` and the dylib, or make
the Rust SPAC engine the only implementation once distribution exists.

---

## 7. Documentation

| Doc | Verdict | Key problems [V] |
|---|---|---|
| `README.md` | update; split Development out | 10 of 13 images never committed; links to `blob/main` for files absent on `main`; 3 env vars of ≥7; Fontra caption contradicts the bullet above it; `pipx install` unverified (no `v0.1.0` tag) |
| `AGENTS.md` | keep, trim | "Uploads wipe `uploaded/`" likely stale; execution-style prose duplicated byte-for-byte with Crispy's |
| `docs/HANDOVER.md` | split: live → ARCHITECTURE/DEVELOPMENT/decisions; history → archive | title "July/August" with October content; debounce "1.2 s" vs code 0.4; "41 server tests" vs ~420; `server.py ~832`; §3 vs §4.6 contradict on caching; §4.8 says designspace authoring is roadmapped while the code has it; §6 points at a section that marks itself superseded; orphaned paragraph at L253–254 |
| `docs/WORKER_BUILD_PLAN.md` | archive after extracting ADRs | line refs stale; placeholder left above real entries; never marked COMPLETE; export-formats section is a genuine decision record |
| `docs/secondary-parametric-axes.md` | update | says designspace isn't handled (it is); omits `instance_values`; `reseed`/`reference-metrics` undocumented |
| `docs/grade.md` | update | still "Draft"; missing `intensity`, `clamp_to_headroom`, `max_pct`, the achieved-move fix |
| `docs/design-notes.md` | merge → decisions + roadmap | ADR-grade content |
| `docs/debugging-2026-08-18.md`, `docs/migration-github-pages.md` | archive; lift fixture recipe / Phase-3 decisions | dated logs; zip format omits `-grade.json`/`-kinetikern.json` |
| `docs/authoring-instances.md` | keep → `docs/guides/` | 29 vs 30 instances |
| `docs/readme-screenshots.md` | keep until shots land | names the missing images; "four other transforms" is now six |
| `docs/images/` | prune | 7 of 12 files referenced by nothing |

**Gaps (nothing to find today).** Full env-var table (backend
`AVAR2_STUDIO_DEBUG/DEMO/GFTOOLS_BUILDER`, `AVAR2_RELOCATE_BUILD_DEFAULT`,
`AVAR2_SPAC_INJECT`, `AVAR2_KK2_DYLIB`, `AVAR2_STUDIO_AXIS_RANGES`; worker
forwards only `ENV_KEYS`; frontend `VITE_API_URL`, `VITE_FONTRA_EMBED_URL`,
`window.AVAR2_EMBED_URL`; cargo `AVAR2_SPIKE_FONT`, `AVAR2_ORACLE`); how to
run (venv rule, 5070 convention, log, health payload keys); the pipeline
order and its physics (gvar 64 KB per-glyph header ceiling, relocation
densification, zero-anchored avar2 tents / the valley, F2Dot14
quantisation, `associatedMasterId`, `Point`-not-tuple); every sidecar
schema (`-avar.csv`, `-control.json`, `-grade.json`, `-transforms.json`,
`-kinetikern.json`, `axis-metadata.json`, `instance-renames.json`,
`config.yaml`, `backups/`, `jobs/<id>/`, the config bundle, the workspace
zip); the job-queue API (`POST /api/build`, `GET /api/build/status`,
`DELETE /api/build/<id>`, `POST /api/export-font` → 202,
`GET /api/export/<id>/download`, `GET /api/font?format=woff2|download=1`,
`progress.jsonl`/`result.json`, `KEEP_EXPORTS=5`); the Fontra desktop proxy
and the fontra-embed postMessage protocol v0; tests/fixtures (conftest's
tmp copies, cargo oracles, e2e prerequisites, unexplained fixture dirs);
the two-repo relationship, the `github-pages` branch policy and Crispy's CI
pin; a module map / architecture overview (`routes/` empty, `build/_shims`,
`config_port`, `instance_aliases`, `_fontra_launch/_patch`, `kinetikern/`,
the wasm crate, frontend `static-api`/`session`/`zip-workspace`/
`avar2-eval`/`coverage`/`gvar`).

**Proposed structure.** `README.md` (user-facing only) ·
`docs/ARCHITECTURE.md` (processes, module map, source-path model, pipeline
order + constraints, API surface, desktop-vs-static parity table) ·
`docs/DEVELOPMENT.md` (venvs, launch/port/log, rsync loop, env table,
tests + fixtures + e2e, CI/Pages/release, branch policy + Crispy pin, smoke
playbook) · `docs/SIDECARS.md` · `docs/guides/` (authoring-instances,
secondary-parametric-axes, grade, kinetikern, transforms) ·
`docs/decisions/` (ADR, dated, status) · `docs/archive/`.

**ADRs to write (distilled from HANDOVER + code comments).** 0001 build-time
default relocation ON (zero-anchored tents → valley when off; measured
numbers); 0002 TTF preview / background WOFF2 cache + sizes in the modal;
0003 worker subprocess, finish-then-rebuild not cancel-restart; 0004
Kinetikern as font-stage phantom tuples, not shadow braces; 0005 ROND = axis
+ per-style % with rendered-fill corner classification; 0006 slant name
exemption + italic doubling; 0007 export "set default" — inserted-master
method rejected after testing (interior drift), lossless relocation to a
parameterised P is the direction; 0008 per-instance control values in the
sidecar, not a CSV column; 0009 drop out-of-range sources; 0010
fontra-embed as a separate GPL sub-project over postMessage; 0011 ymod
baked corrections (default = corrected state, dead axis by design); 0012
foundry tags uppercase, registered tags lowercase. **No textual trace
anywhere** of "ROND white-room cap parked", the collision badge, or the
Def-as-real-master shelf — they exist only in chat and must be written down
or dropped.

**Code-comment policy.** A comment states the invariant and what breaks if
it is violated (≤6 lines); measurements, dates, "observed/once/the hard
way" move to an ADR or a named test; comments end with `See
docs/decisions/NNNN` and/or `tests/test_x.py::name`; docs reference
symbols, never line numbers; docstrings describe the contract, not its
history. Measured: `server.py` has 8 comment runs ≥12 lines, `control_
axes.py` 3; "observed" ×5, "measured" ×14, "once" ×18, "Crispy" ×12.
Keep as-is: the Virtual Masters explanation (`control_axes.py` ~927–941),
`_apply_transform_chain`'s idempotency note, the Kinetikern ordering
comment. Move: the two METHOD NOTEs (server.py ~7099–7150), the "authored
rows come FIRST" parenthetical, the two-failed-stamp-orderings story
(`control_axes.py` ~1265–1276 → one sentence + test pointer).

---

## 8. Cross-cutting themes

1. **Duplicated axis/CSV logic** — three Python CSV readers and three
   parametric/user split rules + grade/kinetikern hand parsing + the Rust
   injector's parser + the browser's `mappings-csv.js`; `grade-model` ×3
   (Python, Rust, JS). One `AxisModel`/CSV module on the Python side, JSON
   handed to the Rust binary, and a parity test is the structural fix.
2. **Tag case** — the uncommitted preservation is correct in direction but
   incomplete (`update_axis`, Fontra session/responses, shim `labelRe`,
   designspace labels); `.upper()`/`.lower()` ad hoc at ~40 sites.
3. **Locking and atomic writes** — one lock per file, taken by every
   mutator including the supervisor; one atomic-write helper used by CSV,
   grade, transforms, control, kinetikern alike.
4. **State ownership after the worker rework** — the server-side canonical
   shadow and the worker snapshot are two sources of truth; drawings
   (H1), stale reads (H2) and the exports' `active-<name>` all follow from
   that. Decide: either the server resolves and the worker only compiles
   the staged result, or the worker's resolved output is promoted back.
5. **Silent failure** — `except Exception: pass`/print, `console.warn`
   saves, corrupt sidecar → empty, native injector absent → silent
   fallback, oracle fixture absent → vacuous pass. A `logging` migration and
   "surface or raise" as the rule.
6. **History in the wrong place** — narrative comments, HANDOVER.md, and
   chat hold the decisions; ADRs + tests should.
7. **Static mode** — valuable but expensive: ≈4.9 k lines of ports, a
   stale 7.45 MB blob in git, drift with no parity check.
8. **Distribution of native accelerators** — undocumented builds, checkout-
   only discovery, no doctor checks.

---

## 9. Prioritised backlog (seed for the cleanup doc)

**Tier 0 — safety (do first, hours).**
- Commit the Kinetikern feature + `.gitignore`; push `github-pages`.
- Restore `src/avar2_studio/static/.gitkeep` (or replace `force-include`);
  verify with a scratch-clone `uv build`.
- Stage `.avar2-studio/shadow/<name>` into the job snapshot (or
  `capture_outlines` before staging) — stops the drawing loss (H1). Then
  run `capture_outlines` once on Crispy so current drawings are in the
  sidecar.
- Cancel must not trip the build gate (restore the previous
  `LAST_BUILD_STATUS` or add a distinct "cancelled").
- Decide the seed-vs-edit rule in `regenerate_shadow` via the existing seed
  signature; add the probe as a test ("edit a master, untouched seed moves").
- Carry `outline`/`source_sig` server-side in `apply_layer_delta` (modal
  path); call `capture_outlines` on Fontra save / drawer close.

**Tier 1 — quick wins (≤1 h each).**
Server: `global OBSERVER`; suppression windows before writes (and after
the `transformed/` save); `secure_filename` on uploads; `get_json(silent=
True)` + 400 at the six sites; delete dead code (two routes, debounce
timer, `create_instance_in_glyphs`, `rebuild_in_background` threads,
`routes/`); `get_font_family_name` without parsing; `load_source_cached`
on the listed read paths; hoist `_declared_axis_ranges` to once per build;
fail loudly when `--csv` is passed; `_running = None` after promotion;
re-resolve in `_run_one`'s `source_refresh` branch and the watcher's
mid-build branch.
Control axes: case-insensitive lookup in `update_axis` (+ `SUBS` test);
stop lowercasing the Fontra session/responses; case-insensitive `labelRe`,
drop the `'lcwd'` hardcode; fix the first-wins docstring; forward
`location` in `embed-font.js`.
Frontend: resolve the SpaceTab WIP intent; fix `HeaderProps` → tsc green;
replace the 4 irregular-whitespace chars → eslint error-free; add
`lint`/`typecheck`/`test:unit` scripts and run them in both workflows;
delete orphans + 2 deps; reword the 12 copy leaks; fence Kinetikern in
static mode; `aria-label` on sliders; dismissable error banner.
Rust/tests/process: derive python from `CARGO_MANIFEST_DIR`; outputs under
`target/tmp`; `#[ignore]` the vacuous oracle; `pip install -e .[dev]` +
`[tool.ruff]` + triage; `doctor` checks for `spac-inject`, the dylib and
their env overrides; log when the native injector is absent; delete
`wasm/fontc-web/pkg/`; register or delete `real_jobs`; prune merged
branches; tag `v0.1.0` if published; `git gc`.
Docs: fix README images/links (relative, not `blob/main`); write the env
table; fix the HANDOVER contradictions that are one-line edits (0.4 s,
test counts, L253–254).

**Tier 2 — S/M.**
One lock + one atomic-write helper for every sidecar (incl. the
supervisor's CSV hook); `logging` migration; `state.py` extraction + test
migration; assets out of Python strings; export polling on a per-job
endpoint + `AbortSignal` + working Cancel; `useBuildWait()` /
`useBuildStatus()`; `api.capabilities` + parity test; Radix Dialog for the
hand-rolled modals; CI `test.yml` + deterministic oracle fixtures; version
gate + unknown-key passthrough + corrupt-file surfacing in `_normalise`/
`load`; watch sidecars and handle moved/created events (with a watchdog
event-class test); exclude `.avar2-studio/jobs/` from the recursive watch;
Kinetikern explicit tuple marker; `build/` reaper; designspace path: 501
the authoring routes or reach parity; docs restructure (ARCHITECTURE,
DEVELOPMENT, SIDECARS, guides, decisions, archive) + the 12 ADRs.

**Tier 3 — L / structural.**
`pipeline.py` + Flask-free worker; blueprints; `control_axes/` package;
single shim implementation shared with fontra-embed; one axis/CSV model
across Python, Rust and JS; App.jsx shrink to a shell with per-tab
containers; stop committing the wasm blob (CI build, LFS/release asset,
optional history rewrite); parameterised default relocation for export
(with the first export test); native-binary distribution (platform wheels
or Rust-only SPAC); watcher lifecycle object.

---

## 10. What was not verified

PyPI availability of 0.1.0; the M2 double build (log grep pending); the M3
designspace perpetual-rebuild loop; H3's size effect on exports (code path
verified, growth inferred); whether the Rust SPAC path pads MVAR/BASE/VVAR/
COLR/GDEF like the Python one; Fontra writing stale data over a regenerated
shadow; `capture_outlines` behaviour on `.designspace` beyond "returns 0";
the exact frequency of the suppression-window miss; `kinetikern_apply_all`
duration. Everything else above was read in code, probed, or reproduced by
the audits on 2026-10-16.
