# Architecture

How avar2-studio is put together, as of 2026-10-16 (branch
`cleanup-2026-10`). Everything here was checked against the code; the
reference is always a symbol (`pipeline._resolve_active_source`), never a
line number. Sidecar schemas are in [SIDECARS.md](SIDECARS.md), the
developer loop in [DEVELOPMENT.md](DEVELOPMENT.md), and the *why* behind
the load-bearing choices in [decisions/](decisions/README.md).

Vocabulary: the product says **secondary parametric axes**; the code,
routes, sidecar and this document say **control axes** for the same
thing.

## 1. Processes

### Desktop (the installed app)

**Flask server** — `avar2_studio.server`, one process. Importing the
module builds the Flask app (CORS, `flask_sock`), creates
`JOBS = build_jobs.JobQueue(server)` — whose supervisor thread starts at
once — and registers `atexit`. `main()` parses the CLI, loads the source
through `_apply_source_path` (sidecar bootstrap, `_resolve_active_source`,
`JOBS.cancel_all()`, first build), schedules the watchdog `Observer`
(the active source's directory; the original's directory when it differs;
the shadow directory when source transforms make `transformed/` the
active source; recursive only for `.designspace`), and runs `app.run`.
Request threads serve the API. Layer edits return in milliseconds and
`schedule_shadow_rebuild` coalesces them into one regen + build after a
0.4 s debounce (`_REBUILD_DEBOUNCE_SECONDS`); `BACKGROUND_WORK` folds
that pending work into `/api/health`'s `building`.

**Build worker** — `python -m avar2_studio.build_worker <spec.json>`, one
fresh interpreter per job (`build`, `export`, `compress`). It imports
`pipeline` (Flask-free — no app or supervisor thread in the worker),
points the path globals at the job snapshot
(`_bind_server_globals`), runs the very same pipeline functions, writes
`progress.jsonl` (phases plus a stderr tee) and an atomically renamed
`result.json`, and never writes shared state. The supervisor promotes.
Only the env names in `build_jobs.ENV_KEYS` are forwarded. Decision 0003.

**Fontra subprocess** — spawned on demand by `open-editor`
(`_ensure_fontra_running`): `python -m avar2_studio._fontra_launch
--http-port 8001 filesystem <root>`, which applies `_fontra_patch` (keeps
studio brace layers editable in fontra-glyphs) before Fontra starts.
Restarted when the root changes or the served shadow's mtime moved;
stopped on source swap. `FONTRA_PORT` is a single global (8001): two
studios on one machine fight over it. The studio reverse-proxies it
same-origin (§7).

**Glyphs plugin launcher** — `glyphs/Avar2Studio.glyphsPlugin/…/studio_launcher.py`
runs `python -m avar2_studio SOURCE --port N` from the interpreter recorded
in `~/.avar2-studio/glyphs-plugin.json`, reusing a studio already serving
the file on ports 5001–5010, log in `~/.avar2-studio/glyphs-plugin.log`.

### Static (GitHub Pages)

The same React bundle with a different data provider. `index.jsx` runs
`selectApiMode()` before the first render: it probes `/api/health` with a
1.5 / 4 / 8 s retry ladder — a response (any status) or a connection
refusal decides at once, a timeout retries (a desktop server busy in a
build). On a static host `Object.assign(api, staticOverrides)` swaps in
`static-api.js`, a two-tab edit lock starts (BroadcastChannel), the
IndexedDB session is restored or the default example loaded; a *blind*
static tab (a desktop tab that lost the probe race) keeps polling and
reloads when the backend answers.

All font work runs in the **fontc-web wasm crate** inside one Web Worker
(`fontc-worker.js`; `fontc-compile.js` serializes calls through a queue).
The bundled examples are staged by `scripts/snapshot_static_demo.py` as
project zips plus a pristine fontc compile (`frontend/public/static-demo/`)
and load through the upload path, so they are editable. The outline
editor is the **fontra-embed** iframe driven over postMessage (§7,
decision 0010).

## 2. Module map

### Python — `src/avar2_studio/`

| Module | Role |
|---|---|
| `__init__.py`, `__main__.py` | package; `python -m avar2_studio` → `cli.main` |
| `cli.py` | dispatch: `doctor`, `build SOURCE --out DIR`, `install-glyphs-plugins`, otherwise `server.main()` |
| `server.py` | the Flask app: construction, the build gate (`before_request`), route registration (`routes.register`), the source-file read/CRUD helpers routes share, the debounced-build triggers, `main()` (argparse + wiring: watcher via `watch.WATCHER`, periodic fallback, `app.run`). Pipeline functions are imported back from `pipeline.py`, so `server.X` call sites stay valid |
| `state.py` | the 37 mutable runtime-state globals as plain module attributes; everyone reads/writes `state.X` (never `from state import X` — that binds a copy) |
| `watch.py` | the source-file watcher: `GlyphsFileHandler` (classification, debounce, suppression watermarks, settle/dedup, mid-build and idle branches) + `SourceWatcher` (`start`/`stop`/`reschedule` — `/api/load-source` re-points it); call-ins go through late-bound `server.X` |
| `routes/` | one Flask blueprint per route group (`ui`, `source`, `health`, `build`, `text_width`, `coverage`, `config`, `transforms_grade`, `kinetikern`, `avar2`, `instances`, `control_axes`, `load`, `fontra` incl. the proxy + websocket); handlers read `state.X` and call pipeline/server helpers late-bound; `tests/test_url_map_snapshot.py` pins the route table |
| `pipeline.py` | Flask-free build/export pipeline: source-path model (`_apply_source_path`, `_resolve_active_source`), build (`_run_build`, `_perform_avar2_build`, `_build_in_process`, `build_variable_font`), transform chain (`_apply_transform_chain`, `_strip_injected_axes`, `_stamp_control_instance_values`), export (`_export_pipeline`, `_save_woff2`), the preview-path getters and axis metadata (`_load_axis_metadata`, `_save_axis_metadata`, `_derive_traditional_range`, `_repair_placeholder_ranges`), `_builder_env`/`_declared_axis_ranges`, watcher suppression (`_suppress_watcher`) |
| `build_jobs.py` | `JobQueue`: coalescing build requests, FIFO exports, lowest-priority WOFF2 compress; snapshot staging (`_stage`), supervisor, promotion, export reaping (`KEEP_EXPORTS`) |
| `build_worker.py` | worker entry: `build` / `export` / `compress` handlers, progress + result files |
| `build/config_generator.py` | CSV → `config.yaml` `stat` and `avar2` sections (`update_config`); the in/out column split and row dedupe |
| `build/_shims/gftools-gen-avar2` | patched gen-avar2 for the external `gftools builder` path (fvar dedupe + declared ranges via `AVAR2_STUDIO_AXIS_RANGES`); the in-process build applies the same patch directly |
| `source_font.py` | format dispatch (`.glyphs` via glyphsLib, `.designspace` via designspaceLib); `load_source_cached` (keyed on path, mtime, size — `.glyphs` only); `save_font_atomically`; axes, masters, instances and instance CRUD on the source |
| `csv_io.py` | CSV readers, the parametric/user column split, source→CSV sync (`update_csv_from_glyphs`), `backup_sidecar`, `normalize_in_axis_name` |
| `control_axes/` | package re-exporting the old flat module's namespace: `schema.py` (`-control.json` I/O and normalisation; layer delta / reseed), `naming.py`, `outlines.py`, `shadow_glyphs.py` / `shadow_designspace.py` (shadow regeneration, seeds, virtual masters, seed signatures, `capture_outlines` / `_extract_brace_outlines`) |
| `grade.py`, `grade_shadow.py` | `-grade.json` and the grade model (`grade_coords`, `max_pct_for`); GRAD braces + virtual masters composed onto the shadow |
| `glyph_coverage.py` | per-axis coverage (`universal` / `scoped`), per-glyph layers with `location` (design) and `location_user` (user space, `null` when the map cannot be inverted), `glyph_chars` |
| `config_port.py` | config bundle export (captures drawings first), validate, apply |
| `instance_aliases.py` | `.avar2-studio/instance-renames.json`, applied on every shadow regen |
| `transforms/` | `base.py` (the `Transform` / `TransformSpec` / `ParamSpec` / `BuildContext` contract), `registry.py` (built-ins + `~/.avar2-studio/transforms/`), `config.py` (`-transforms.json`), built-ins `builtin_spac.py`, `builtin_spac_widthaware.py`, `builtin_gftools.py`, `builtin_round_corners.py` + `corner_rounding.py`, `builtin_slant.py` + `slanting.py` |
| `kinetikern/` | `__init__.py` (engine loader, `propose`), `bridge.py` and `punctuation.py` + `.json` (vendored from Kinetikern2), `sidecar.py`, `inject.py` (the bake) |
| `_fontra_launch.py`, `_fontra_patch.py` | the Fontra subprocess entry and the fontra-glyphs monkeypatch |
| `doctor.py`, `glyphs_install.py` | environment checks (incl. `spac-inject` and the Kinetikern dylib); Glyphs plugin symlinks + launcher record |
| `glyphs/` | seven Glyphs 3 bundles (the hub + six tools), documented in `glyphs/README.md` |
| `static/` | the built frontend (gitignored, `.gitkeep` kept so the wheel's force-include never fails) |

### Frontend — `frontend/src/`

| Module | Role |
|---|---|
| `index.jsx` | boot: `selectApiMode()` then render |
| `App.jsx` | the shell and state hub; tabs **Instances**, **Preview**, **Kinetikern** (desktop only), **Tester** |
| `api.js` | the live provider (one method per route; export polling) |
| `static-api.js` | the static provider: examples, uploads, the in-browser rebuild pipeline (`rebuildUploadFont`), sessions, bundle import, editor sessions |
| `session.js` | IndexedDB persistence of the uploaded workspace |
| `zip-workspace.js` | project zip read/write (mirrors `server._load_project_zip`) |
| `mappings-csv.js` | the CSV model (instances + mappings) for uploads |
| `fvar.js`, `gvar.js`, `coverage.js` | minimal TTF readers; the structural coverage audit |
| `avar2-eval.js`, `avar2-lint.js` | avar v2 VarStore evaluator (the Preview tab's reflection); the mappings lint |
| `var-model.js`, `grade-model.js` | ports of fontTools' `VariationModel` and of `grade.py`'s cap model (the third copy, with `braces.rs`) |
| `editor-bridge.js` | parent side of the fontra-embed editor (§7) |
| `fontc-compile.js`, `fontc-worker.js`, `wasm/fontc-web/` | the wasm worker and the committed `wasm-pack` output |
| `fontLoad.js`, `mac-glyph-names.js`, `utils/`, `lib/` | preview face registration from bytes; helpers |
| `components/` | `Header.tsx`, `Sidebar.jsx`, `InstanceRows` / `InstanceRow` / `InstanceFlyout`, `AxisControl`, `ControlAxes` + `LayersEditor` + `AddControlAxisModal` + `AddBraceLocationModal` + `EditAxisModal`, `AddAxisModal`, `DeleteInstanceModal`, `DuplicateModal`, `GradeBadge` / `GradeDiagnostics`, `RoundingBadge`, `SlantBadge`, `PreviewTab`, `TesterTab`, `KinetikernTab`, `FontraEditorModal`, `ImportConfigModal.tsx`, `DemoLanding`, `ui/` (Radix dialog and dropdown) |
| `e2e/` | Playwright and node specs — see DEVELOPMENT |

### Rust — `wasm/fontc-web/` (cdylib + rlib)

`lib.rs` exports `compile_glyphs`, `compile_with_overlays`, `glyph_model`,
`round_corners_source`, `slant_source`, `add_avar2`, `apply_control_axes`,
`apply_grade`, `apply_transforms`, `set_default_location`, `set_hidden_axes`,
`regen_stat`, `measure_at`, `pin_corner`, `clamp_out_of_range`. Modules:
`braces.rs` (control-axis and grade tuples), `spac.rs` + `bin/spac-inject.rs`
(the SPAC engine, also built natively as the `spac-inject` CLI the desktop
prefers), `round_corners.rs`, `slanting.rs`, `fix.rs` (instance cleanup,
italic doubling, ROND/slnt stamps), `stat.rs` + `stat_registry.rs`,
`measure.rs`, `extrapolate.rs`, `iup.rs`, `editor.rs`. Every module is a
port of a Python one and has a cargo oracle test (`tests/*_oracle.rs`,
comparison scripts in `spike/`). Dependencies: fontc, glyphs-reader and
fontdrasil from git `main`, pinned by `Cargo.lock`.

### Vendored engine — `native/kinetikern2/`

Kinetikern2's Rust engine (MIT), built to
`target/release/libkinetikern2.dylib` and loaded over ctypes;
`UPSTREAM_COMMIT` names the pinned revision.

### Elsewhere

`scripts/snapshot_static_demo.py` (Pages staging), `spike/corner_audit.py`,
`examples/` (crispy-mini `.glyphs`, roboto-delta-mini `.designspace`),
`site/` + `netlify.toml` (the standalone project page), `rules/`.

## 3. The source-path model

```
ORIGINAL_PATH          the designer's file. Never written by studio
                       authoring; sidecars resolve against it.
      │  control_axes.regenerate_shadow  (+ grade_shadow.apply_grades)
      ▼
.avar2-studio/shadow/<name>                 the canonical shadow:
                                            original + control axes +
                                            brace layers + GRAD braces
      │  source-stage transforms (slant, round corners)
      ▼
.avar2-studio/shadow/transformed/<name>     the build copy
      │  build_jobs._stage  (per job)
      ▼
.avar2-studio/jobs/<id>/src/…               the worker's snapshot
      │  fontc + the pipeline, in the worker
      ▼
.avar2-studio/build/<Family>[axes].ttf      promoted by the supervisor
```

- `ORIGINAL_PATH` is the file the user pointed at. `GLYPHS_PATH` (the
  name predates designspace support) is the **active source** — what the
  read endpoints and the build operate on. It is the original until a
  studio feature adds per-glyph deltas the original lacks; then it is the
  shadow, or the transformed copy of the shadow.
- **Sidecars always resolve against `ORIGINAL_PATH`** (`_get_preview_dir`,
  `_get_preview_csv_path`, every `sidecar_path_for`). Resolving against
  the shadow was a bug class once (the CSV written into the shadow dir).
- `_resolve_active_source()` is the single authority, serialized on
  `_RESOLVE_LOCK` (callers: request threads, the debounce timer, the
  watchdog thread, the job supervisor; the config import nests
  `SIDECAR_LOCK` → `_RESOLVE_LOCK`, never the reverse):
  1. `control_axes.regenerate_shadow(ORIGINAL_PATH)` — wipe and re-derive
     from original + sidecar: append missing axes, pad every master's and
     brace's coordinates, add **Virtual Masters** so a brace-only axis has
     a real range, seed every sidecar layer (a stored `outline` outranks a
     preserved prior-shadow edit, which outranks a fresh interpolated seed;
     seed-vs-edit is decided by the seed signature `xyz.avar2studio.seed-sig`
     stamped from the saved file; correction `target`s are always
     recomputed), apply instance renames, write atomically, re-stamp.
  2. `grade_shadow.apply_grades` composes GRAD braces + virtual masters
     onto the same shadow (fresh copy only when there is no control shadow).
  3. Enabled **source-stage transforms** (`slant` priority 10, then
     `round_corners` 50) run over a copy at `shadow/transformed/<name>` —
     never the canonical shadow, whose preserve-drawn-outlines pass would
     capture a rounded brace as a hand drawing. A failure keeps the
     untransformed source and sets `LAST_SOURCE_TRANSFORM_ERROR`.
  4. `GLYPHS_PATH` = the shadow (or transformed copy) if any axis has
     layers, any instance is graded, or a source transform ran; otherwise
     the original.
- Every server-side write of a watched file re-arms the watcher
  suppression (`_suppress_watcher`: a time window **and** an mtime
  watermark, so a late-processed self-event is still dropped).
- **The worker's snapshot** (`JobQueue._stage`): the original, its sidecars
  (`SIDECAR_SUFFIXES`), the legacy `avar2-mappings.csv`, the small
  preview-dir files (`axis-metadata.json`, `instance-renames.json`,
  `config.yaml`) into `src/.avar2-studio/`, the control shadow for
  `.glyphs` sources (so uncaptured Fontra drawings survive), the UFOs for
  a designspace. Not `transformed/` — the worker re-derives it from the
  staged shadow in its own `_resolve_active_source`. Exports also stage
  the current built font and, when the active source is not the original,
  an `active-<name>` copy of it.
- **Promotion** (`_promote`, supervisor only, same-filesystem renames): the
  built font into `.avar2-studio/build/`; the job's regenerated
  `config.yaml` over the canonical one; export artifacts into
  `.avar2-studio/exports/<job-id>-<name>` (`KEEP_EXPORTS = 5`); the WOFF2
  cache beside the font. Job directories are removed after `_finish`.
- **Workspaces outside the project:** bundled examples are staged once to
  `~/.avar2-studio/workspace/<id>/` (edits persist, the repo fixture stays
  clean); uploads go to `~/.avar2-studio/workspace/uploads/<slug>/`, one
  directory per font, the outgoing source archived under its
  `.avar2-studio/archive/` (last five), studio data preserved across
  re-uploads.

## 4. Pipeline order and its physics

### Desktop build (worker, `_run_build`)

1. `_resolve_active_source` on the snapshot (§3).
2. `config_generator.update_config(csv, config.yaml, source=GLYPHS_PATH)` —
   regenerates the `stat` and `avar2` sections from `<stem>-avar.csv`
   (rows resolving to the same input location are deduped, first wins;
   blank user cells skipped; `opsz` dropped when every row shares one value).
3. `_build_in_process` (the gftools-builder steps in one process —
   `AVAR2_STUDIO_GFTOOLS_BUILDER=1` keeps the subprocess builder as a
   comparison valve):
   - **0.5** find the ORIGIN row (every `in` at its declared default;
     more than one fails loudly; none → `LAST_ORIGIN_WARNING`);
   - **1** `fontc -o … --flatten-components --decompose-transformed-components`;
   - **2** `gftools.fix.fix_font`; **3** STAT from the config;
   - **3.5** default relocation: fontTools instancer moves the parametric
   After the relocation, `pipeline._fit_gvar_headers` keeps every glyph's gvar header block under the u16 limit by dropping only sub-visible tuples (≤2 u) where it overflows, or fails with a readable error naming the glyph (far interior defaults on twin-heavy fonts).
     fvar defaults to the ORIGIN row's `out` on the still avar-free font
     (`AVAR2_RELOCATE_BUILD_DEFAULT`, default on — decision 0001);
   - **4** `gen_avar2_mapping` with `gen_fvar_axes` patched (drop `in:`
     axes the font already has; give created axes their declared
     `[min, default, max]`);
   - **5** `gen_fvar_instances`;
   - sanity: duplicate fvar tags fail the avar2 build loudly.
4. `_apply_transform_chain` (the single chokepoint, also on the plain-VF
   fallback and on exports):
   - strip previously injected **font-stage** axes (`_strip_injected_axes`
     pins them to default and instances them away) so re-runs are
     idempotent — never a source-stage axis like ROND;
   - **Kinetikern bake** (`kinetikern.inject.apply_to_font`) — decision 0004;
   - font-stage transforms in sidecar order, with the source-stage
     transforms' font-stage `apply()` (ROND / slnt instance stamps) **last**,
     after `fix_instances` regenerates the instance list;
   - a transform that raises or returns a missing path is skipped and
     recorded in `LAST_TRANSFORM_ERROR`; the chain keeps the last good font.
5. `_stamp_control_instance_values` — per-instance control-axis values
   onto matching fvar instances (decision 0008).
6. Promote; queue the WOFF2 compress job (decision 0002).

If the avar2 build fails, `_run_build` falls back to a plain
`build_variable_font` (fontc, or fontmake with `--no-fontc`) through the
same transform chain and records `LAST_AVAR2_ERROR`; `/api/health` exposes
every `LAST_*` so the UI can say which stage degraded. `avar2-studio
build` treats any of them as a failing exit.

### Export (worker, `_export_pipeline`)

Copy the current built font; if a `default_location` was requested, map it
(an exact authored CSV row wins over the built table, because the preview
table cannot express the ORIGIN row with relocation off), stage an
interpolated origin master as a designspace (`_build_export_source`:
non-exporting glyphs stripped, baked-correction braces merged into
coincident masters, sources coincident with the new origin dropped),
regenerate the config with blank cells materialised, run
`_build_in_process` with `default_overrides`, then the transform chain;
flag hidden axes; convert to WOFF2 on request. Decision 0007 records why
this still densifies and where it is going.

### Static rebuild (`static-api.js rebuildUploadFont`)

`compile_glyphs` (or `compile_with_overlays` when drawn layers exist;
`round_corners_source` / `slant_source` applied to the plist first) →
`add_avar2` → `apply_control_axes` (computed braces) → `apply_grade` →
`apply_transforms` (SPAC) → corner pins → `clamp_out_of_range`. Each stage
rebuilds HVAR from the current gvar. Applied state cannot be un-baked, so
changing the transform set rebuilds from source.

### The physics every change must respect

- **gvar's 64 KB per-glyph tuple-header ceiling.** The preview font sits
  a few KB under it on Crispy (`g`: ≈44 KB of headers before SPAC, ≈63 KB
  after). Anything that adds tuples must be budgeted; an overflow raises
  inside the chain.
- **Relocation densification.** Relocating the origin to an interior
  point splits every region around it: ≈7× gvar (57 KB → 495 KB), ≈15×
  per-glyph tuple counts (H: 55 → 838). It is intrinsic, not an
  inefficiency, and it is why Kinetikern cannot be a sparse master.
- **Zero-anchored avar2 tents / the valley.** A row at the input default
  cannot be expressed; dropping it instead of relocating collapses the
  whole neighbourhood of the default toward the compiled corner
  (wght 399 → XTRA 92 instead of ≈663). Decision 0001.
- **F2Dot14 quantisation.** gvar stores normalized peaks in F2Dot14;
  quantise locations before solving a model, or a 1e-8 region at the
  default becomes a "peak 0 = axis not involved" tuple that applies
  everywhere (a −900-unit delta once gave `dollar` advance 0).
- **Kinetikern before SPAC**: after SPAC's native injector only 18 of 30
  instances fit the ceiling; the injector preserves earlier tuples (error
  ≤ 1 unit).
- **Relocate before inject; strip before re-apply.** Relocation runs on
  the avar-free font before gen-avar2 (fontTools refuses partial
  instancing once avar2 exists); injected axes are stripped before the
  chain so running it twice lands in the same place (four stacked SPAC
  axes were observed once).
- **Twin layers and glyphsLib serialization.** A master twin must not
  carry `associatedMasterId` (fontc drops its geometry); positions must be
  `Point`, not tuples (they serialize as quoted strings fontc rejects).
  Decision 0006.
- **The shadow is state; the transformed copy is derived.** Never run a
  transform over the canonical shadow.

## 5. API surface

All JSON unless noted. Mutating methods (POST/PUT/PATCH/DELETE) are
refused with **409** while `LAST_BUILD_STATUS == "failed"`
(`_refuse_edits_while_source_is_broken`), except `/api/build`,
`/api/load-source` and the `…/editing` markers. The Fontra catch-alls
come last, so an unknown `/api/*` path is forwarded to Fontra rather
than 404ing.

**UI and assets** — `GET /`, `GET /static/<path>`, `GET /asset-manifest.json`,
`GET /manifest.json`, `GET /favicon.ico` (503 "Frontend bundle not present"
without `static/`).

**Health and source** — `GET /api/health` (keys: `status`, `demo`,
`glyphs_path`, `original_path`, `source_format`, `font_built`,
`family_name`, `vf_family_id`, `built_font_filename`, `last_build_time`,
`last_build_status`, `last_build_error`, `transform_error`, `avar2_error`,
`grade_error`, `source_transform_error`, `origin_warning`, `build_stale`,
`building`, `build_job`); `GET /api/glyphs-file-status` (osascript probe
of unsaved changes); `GET /api/examples`; `POST /api/load-source` (JSON
`{example}` or multipart: one `.glyphs` + sibling files, or one project
`.zip`; filenames sanitised).

**Build, artifacts, jobs** — `POST /api/build` (request + wait for idle;
blocks); `GET /api/build/status` (`running {job_id, kind, elapsed, phase}`,
`queued`, `last`, `sizes`); `DELETE /api/build/<job_id>` (cancel);
`POST /api/export-font` (`{hidden_axes, default_location, format}` → 202 +
`job_id`); `GET /api/export/<job_id>/download`; `GET /api/font`
(`?download=1` attachment; `?format=woff2` the cache or 409);
`GET /api/avar2-font`.

**Instances** — `GET /api/instances` (rows with `origin: source|studio`,
hydrated control values, SPAC); `GET /api/masters`;
`PUT /api/instances/order`; `POST /api/instance`;
`PUT /api/instance/<name>` (coordinates; `csv_only`; source writeback via
Glyphs.app when open); `DELETE /api/instance/<name>` (`?csv_only=true`);
`PUT /api/instance/<name>/rename`; `POST /api/instance/<name>/add-to-source`;
`POST|DELETE /api/instance/<name>/editing` (shield a row from the CSV
sync); `GET /api/text-width`.

**Axes and avar2 mappings** — `GET /api/axes` (source axes overlaid with
the built font's fvar, so transform-injected axes appear);
`GET /api/avar2/axes` (user axes with metadata, parametric axes);
`GET /api/avar2/instances`; `POST /api/avar2/axis`;
`PUT|DELETE /api/avar2/axis/<name>`;
`PUT /api/avar2/mapping/<instance>/<axis>`; `GET /api/mapped-location`
(evaluate the built avar2 table); `GET /api/glyph-coverage`.

**Control axes** — `GET /api/control-axes` (outlines stripped; `has_outline`,
`source_changed` per layer); `POST /api/control-axes`;
`PATCH|DELETE /api/control-axes/<tag>`; `PUT /api/control-axes/<tag>/layers`
(whole-list replace — the UI no longer uses it for edits);
`POST /api/control-axes/<tag>/layers/delta` (`{add, remove}`, carries
drawings across a replace); `POST /api/control-axes/<tag>/reseed`
(`{layers?, force}` → 409 `blocked` when a drawing would be discarded);
`GET /api/control-axes/<tag>/reference-metrics`
(`glyph`, `index`, `refs`, `location`); `POST /api/control-axes/<tag>/open-editor`;
`GET /api/fontra-shim-config`.

**Transforms and grade** — `GET|PUT /api/transforms`;
`GET|PUT /api/transforms/grade` (`enabled`, `default_pct`, `intensity`,
`clamp_to_headroom`; the GET adds `max_pct` and `diagnostics`);
`PUT|DELETE /api/instances/<name>/grade`.

**Kinetikern** — `GET /api/kinetikern`; `GET /api/kinetikern/propose`
(`instance=` or `coordinates=`, `looseness=`; 503 without the engine);
`PUT|DELETE /api/kinetikern/instances/<name>`; `PUT /api/kinetikern/all`.

**Configuration** — `GET /api/config/export` (the bundle, as a download);
`POST /api/config/import` (`{bundle, dry_run}`; all-or-nothing).

**Fontra proxy** — `GET|POST|PUT|DELETE /fontra/` and `/fontra/<subpath>`;
`GET /lang/<subpath>`, `/data/<subpath>`, `/images/<subpath>`,
`/webfonts/<subpath>`, `/projectlist`, `/serverinfo`; the fall-throughs
`/api/<rest>` (all methods) and `GET /<rest>`; the `/websocket` relay.

## 6. Desktop vs static parity

| Capability | Desktop | Static (Pages) |
|---|---|---|
| Compile | fontc (fontmake with `--no-fontc`) in a worker | fontc-wasm in a Web Worker; `.glyphs` only — `.designspace` projects load their baked preview TTF and cannot rebuild |
| avar2 | gen-avar2 with the relocation (0001) | wasm `add_avar2` — still subtract-base for a default-located row |
| Control axes | shadow braces; drawn and computed layers | drawn layers spliced source-level (`compile_with_overlays`); computed layers as bytes-level tuples; corrections auto-pinned |
| Grade | shadow braces per instance (scoped to its location) | `apply_grade` tuples, global along the axis (multiple grades sum) |
| Rounding, slant | source-stage on the shadow | same engines ported, node-identical oracles |
| SPAC | native `spac-inject` (fontTools fallback) | wasm `apply_transforms` |
| Kinetikern | engine routes + tab | not available (tab hidden) |
| Export options | worker job: hidden axes, set default (inserted origin master), TTF/WOFF2 | `set_default_location` + `set_hidden_axes` + `regen_stat` in-browser |
| Source writeback, reseed from source | yes | throws with guidance |
| Outline editor | Fontra subprocess, same-origin proxy | fontra-embed iframe over postMessage |
| Coverage audit / corner pins / out-of-range drop | — (live routes exist but are dead since the Space tab went) | `coverage.js` + `measure_at`, `pin_corner`, `clamp_out_of_range` (no UI since T1.17) |
| Persistence | files next to the source | IndexedDB session + workspace zip |

Provider method drift, measured on `api.js` vs `static-api.js`'s
`staticOverrides` (sprint T2.7 adds `api.capabilities` and a parity test):

- **live only:** `getBuildStatus`, `cancelBuild`, `getKinetikern`,
  `proposeKinetikern`, `applyKinetikern`, `revertKinetikern`,
  `applyKinetikernAll`, `getWoff2PreviewUrl`, `getWoff2DownloadUrl`;
- **static only:** `addMappingRow`, `checkSyncStatus`, `clampOutOfRange`,
  `exportWorkspaceUrl`, `flushEditorRebuilds`, `forgetSession`,
  `getCoverage`, `pinCorner`;
- `getMasters` is stubbed to `[]` in static; `openControlAxisInEditor`
  takes two extra arguments there.

## 7. Fontra contracts

### Desktop: proxy and shim

`POST /api/control-axes/<tag>/open-editor` routes **per axis**, not by
whether a shadow exists: a **studio axis** (in the sidecar) requires the
shadow (400 "add coverage glyphs first" otherwise) and Fontra serves the
`shadow/` directory; a **source-derived axis** opens the original as a
single-file root (sibling projects stay hidden) and the response carries
`editing_original: true`, which the drawer surfaces. Getting this wrong is
how a designer's outlines end up in a file that regeneration deletes —
treat changes here as high risk. The response gives the same-origin
`url` (`/fontra/editor.html?project=<name>`), a `direct_url` on 8001 as an
escape hatch, the authored-case `tag`, and `project`.

The session is recorded in `FONTRA_EDITOR_SESSION` — `studio`, `tag`,
`axis_name`, `axis_default`, `glyph` — and served to the injected shim by
`GET /api/fontra-shim-config`. On the way back through the proxy
(`_proxy_to_fontra`), HTML gets its absolute paths and import map
rewritten under `/fontra/` and, before `</head>`, the session-aware focus
stylesheet (`_fontra_focus_css`: hides panels and chrome; studio sessions
keep the designspace-navigation panel) plus `_FONTRA_STUDIO_SHIM_JS`
(sources list narrowed to this axis's studio layers, identified by the
axis key in each source's sparse location; batch editing; the metrics
HUD, which asks `reference-metrics` with the edited layer's location).
CSS gets `url(/…)` rewritten. The shim self-deactivates unless the config
marks a studio session. A Fontra save writes the shadow; the watcher
captures drawings into the sidecar and rebuilds; the drawer close also
rebuilds.

### Static: the fontra-embed protocol, v0

Every message is `{ "avar2-embed": true, v: 0, type, … }`; a different
`v` raises an error rather than being parsed. The studio opens the iframe
at `embedEditorUrl()` and keeps the Fontra axis space keyed by **display
name** (the desktop contract), mapping to sidecar tags itself.

Embed → studio: `ready` (first contact; the studio answers `init`),
`editor-ready`, `rpc {id, method, args}`, `editFinal {change}`,
`editIncremental {change}` (guarded silently), `dirty {value}`,
`close-request`, `error {error}`.

Studio → embed: `init {font: {title}, session: {studio, tag, axis_name,
axis_default, glyph}, glyphName, location}`, `rpc-result {id, value |
error}`, `message {headline, message}` (user-facing notice), `reloadData
{pattern}` (revert an optimistic edit).

RPC methods served by the bridge: `getGlyphMap`, `getAxes`, `getSources`,
`getUnitsPerEm`, `getCustomData`, `getBackEndInfo`, `isReadOnly`,
`getGlyph` (the session glyph; others answer empty), `getKerning`,
`getFeatures`, `getFontInfo`, `findGlyphsThatUseGlyph`, and the studio
extra `getReferenceMetrics(refs, location)`. Guards (`guardChange`): glyph
edits only, this glyph only, layer outlines only, master and `target`
layers read-only, structural ops rejected. Decision 0010.

## 8. Concurrency, locks and the watcher

- Locks: `SIDECAR_LOCK` (sidecar mutations through the delta API, capture,
  config import), `_RESOLVE_LOCK` (RLock; serializes
  `_resolve_active_source`), `control_axes._INSTANCE_VALUES_LOCK`,
  `_MAPPED_FONT_LOCK` (the mapped-location font cache),
  `_REBUILD_TIMER_LOCK`. Atomic writes: the control and kinetikern
  sidecars, every shadow save (`save_font_atomically`), `result.json`,
  promotion. Not yet atomic or locked: the CSV (twelve write sites, three
  threads), grade and transforms sidecars — sprint T2.1.
- The watcher (`GlyphsFileHandler.on_modified`) classifies each event
  (`_classify_watched_path`: `source`, `original`, `shadow`, `glif`),
  debounces 0.5 s per path, drops self-writes by window **and** mtime
  watermark, waits for the save to settle (`_wait_for_save_to_settle`),
  processes one event per final mtime, then: mid-build → capture drawings
  (shadow saves), re-resolve, `request_build(source_refresh=…)`; idle →
  resolve (original) or capture + CSV sync (shadow/source), then
  `trigger_build`. Known gaps: the observer is scheduled once in `main()`
  and not re-pointed by `/api/load-source` (T3.10); sidecar files are not
  watched (T2.11); the designspace recursive watch covers `jobs/` (T2.12).
- `trigger_build()` only ever submits to `JOBS`; `_run_build` must never
  run in the server process.
