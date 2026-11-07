# Documentation fact-check at `5e28b47` (verifier, 2026-11-02)

The two D1 acceptance briefs re-run against the checkpoint checkout
(`~/avar2-verifier/clone-5e28b47`, read-only) by two verifier subagents. Only
the rows that did not hold are listed; the per-file OK counts follow each
table. These rows are the spec for D1a (findings.md, "Verifier review of the
ROUND 22 checkpoint"). Rules as approved for D1: facts come from the code, a
WRONG or OUTDATED row is fixed or removed, never softened; an UNVERIFIABLE row
about the production Crispy is measured (number + date) or removed.

## A. Reader set (README, docs/README.md, the five guides, the three example READMEs)

| file:line | statement | verdict | evidence |
|---|---|---|---|
| `examples/README.md:19` | "Glyphs \| 63 (`A–Z`, `a–z`, `0–9`, `space`, `.notdef`) \| 63 (same subset)" | WRONG | Crispy Mini: 63 `glyphname` entries, no `.notdef` (26+26+10+1 = 63; with `.notdef` it would be 64). Roboto Delta Mini: every one of the 19 UFOs has **67** glyphs = those 63 + `.notdef`, `idot`, `idotless`, `jdotless` (`examples/roboto-delta-mini/subset.py` KEEP_GLYPHS). Not the same subset. |
| `examples/crispy-mini/README.md:5-6` | "**63 glyphs** (`A–Z`, `a–z`, `0–9`, `space`, `.notdef`)" | WRONG | 63 is right; `.notdef` is not in the fixture (0 matches in `CrispyMini.glyphs`). |
| `examples/roboto-delta-mini/README.md:5` | "**19 sources** and **63 glyphs**" | WRONG (glyph count) | 19 sources OK; 67 glyphs per UFO (`contents.plist` in all 19). |
| `examples/roboto-delta-mini/README.md:24-25` | "the sources are named `temp_master.N` because they came out of a generated split" | OUTDATED | `temp_master` occurs only in this README. The designspace sources are `stylename="Default"`, `"XOUC2"`, `"XOUC310"`, … with `filename="RobotoDeltaMini-<tag><value>.ufo"`. |
| `docs/README.md:7` | "…the files it reads and writes, **the six features**" | WRONG | `README.md` `## Features` has **seven** bold feature paragraphs (Instances & avar2 mappings, Secondary parametric axes, Preview & export, Grade, Kinetikern, Build transforms, Configuration export / import). |
| `README.md:92-94` | "**mapping lint** flags rows the build would silently drop" (as a studio feature) | WRONG (scope) | `lintAvar2Mappings` (`frontend/src/avar2-lint.js`) is imported only by `frontend/src/static-api.js` and runs only inside the static (browser demo) dataset builders; no import in `api.js`, `hooks/`, `components/`; no Python counterpart. The desktop app never runs it. |
| `docs/guides/kinetikern.md:71-72` | "lists, per glyph, the current and proposed `lsb` / `rsb` / `advance` with an `applied` flag" | WRONG (minor) | `routes/kinetikern.py:57` sets `g["applied"]` to the stored `[left, right]` delta pair already in the build; the per-glyph boolean is `spaced` (`kinetikern/__init__.py:243`). |
| `docs/guides/authoring-instances.md:112-113` | "The Crispy production font has **30 authored instances spanning three widths, five weights and two optical sizes**; Roboto Delta has dozens more." | UNVERIFIABLE | Neither source is in the checkout; nothing under `examples/`, `tests/`, `docs/` records the counts. (The verifier's copy of the live Crispy CSV has 30 rows — state the number with its date, or drop the sentence.) |
| `docs/guides/secondary-parametric-axes.md:167-181` | Crispy leak table ("276 units / 578 units", "13–24 units / 1 unit"), "default master is at XTRA 47", corner "(47 · 1462 · 275)", anchor "(XTRA 1715, XOPQ 1462, YOPQ 275)" | UNVERIFIABLE | Measurements on the production font; no fixture, test or decision record reproduces them. |
| `README.md:99-100` | "Crispy's `LCWD` renders lowercase at the heavy end *as if at* a lighter XOPQ while the capitals stay put" | UNVERIFIABLE | Claim about the production source; `LCWD` appears in the repo only in image alt text / `docs/images/correction-layers.png`. |
| `README.md:20,13,52-53,119,159`; `examples/README.md:23`; `kinetikern.md:3`; `secondary-parametric-axes.md:292` | External URLs | UNVERIFIABLE offline | Internally consistent with `pyproject.toml` `Demo`, `pages.yml --base`, `native/kinetikern2/README.md`, `_fontra_launch.py` / `_fontra_patch.py` imports. Not a D1a row. |

OK counts: README 49/52; docs/README.md 18/19; authoring-instances 17/18;
kinetikern 30/31; transforms 46/46; secondary-parametric-axes 40/41; grade
30/30; examples/README 14/15; crispy-mini README 16/17; roboto-delta-mini README
11/13. Links and anchors: every relative link (36 file targets, 5 images, 2
GIFs) and every anchor resolves. Install wording: "no PyPI release yet, install
from git" is consistent with `pyproject.toml` 0.1.0, `release.yml`, and the
tags (only `v0.1.0.dev1`–`dev6`; PyPI has no project). No reader-set file quotes
a UI string that names a sidecar.

Aside found by the brief (code, not docs): the server's 503 string
`"Kinetikern2 engine not built (native/kinetikern2/README.md)."`
(`routes/kinetikern.py:30,143`) is rendered verbatim in the tab
(`KinetikernTab.jsx:459`) — UI copy that names a repo file.

## B. Appendices (CLI.md, ARCHITECTURE.md, SIDECARS.md, DEVELOPMENT.md, decisions index + 0007 + 0015, plugin README)

| file:line | statement | verdict | evidence |
|---|---|---|---|
| docs/CLI.md:26 | "`sources` writes `<family>-sources.zip`" | WRONG | `cli.py:296` `out = args.out / f"{args.source.stem}-sources.zip"` — the source stem; the argparse help (cli.py:124) repeats the `<family>` wording. (The dialog's zip IS family-named, `pipeline._sources_pipeline`; pick one and say which.) |
| docs/CLI.md:68-70 | "The build reads … `<stem>-axis-metadata.json` (… without it the build exits 2 …)" | WRONG (incomplete) | `cli.py:83-85` `_axis_metadata_problem` is None if **either** the sibling or the legacy `.avar2-studio/axis-metadata.json` exists; exit 2 only when both are missing (SIDECARS.md:237-239 has it right). |
| docs/CLI.md:115 | doctor "Reports … the compiler (`fontc`, then `fontmake`)" | WRONG | `doctor.py:347-358` `CHECKS` = python, fontc, gftools, glyphslib, static_bundle, fontra, spac_injector, kinetikern_engine, install_shadowing, stale_processes; `fontmake` does not occur in doctor.py. |
| docs/CLI.md:117-119 | "`--strict` … is how a release workflow asserts a fresh install is complete" | WRONG | `.github/workflows/release.yml` has no `doctor` step; no workflow runs `avar2-studio doctor`. And `--strict` fails only on `configured_missing` (an env override naming a missing file) or a failed `build_prereq` (`doctor.py:382-391`); an absent accelerator passes. |
| docs/CLI.md:122 | "A `pointing at a missing file` note means an environment override is set and broken" | WRONG (wording) | `doctor.py:312/338` emit `($AVAR2_SPAC_INJECT={env} does not exist)` / `($AVAR2_KK2_DYLIB={env} does not exist)`. |
| docs/ARCHITECTURE.md:89 | "`state.py` — the 34 mutable runtime-state globals" | OUTDATED | 45 module-level assignments in `state.py`. |
| docs/ARCHITECTURE.md:101 | "`examples.py` — the bundled example projects and their staging into `~/.avar2-studio/workspace/`" | WRONG | `examples.py` holds only the `_BUILTIN_EXAMPLES` registry; staging is `routes/load.py:45-72` (`_staged_workspace_for`). |
| docs/ARCHITECTURE.md:82-114 | Python module map covers `src/avar2_studio/` | OUTDATED (incomplete) | Undocumented: `fontra/` (`focus.css`, `shim.js`, `embed-shim.js`, read by `routes/fontra.py`), `glyphsapp/` (two AppleScripts, read by `server.py:169,226`), `build/__init__.py`. Documented-but-absent: none. |
| docs/ARCHITECTURE.md:133 | frontend `components/` list | OUTDATED (incomplete) | Unlisted: `InstancesTab.jsx`, `BuildVeil.jsx`, `KinetikernBadge.jsx`, `StatusBanners.jsx`; the whole `hooks/` dir (six hooks); top-level `build-wait.js`, `export-formats.js`, `instance-sync.js`, `kinetikern-copy.js`, `preview-coords.js`, `promote-flush.js`. |
| docs/ARCHITECTURE.md:138-141 | `lib.rs` exports list | OUTDATED (incomplete) | `wasm/fontc-web/src/lib.rs:79` also exports `round_corners_source_result`. |
| docs/ARCHITECTURE.md:298-309 | Export: "stage an interpolated origin master as a designspace (`_build_export_source`: …) … Decision 0007 records why this still densifies" | OUTDATED | `_build_export_source` exists nowhere in `src/` or `tests/`. `pipeline._export_pipeline` (1597-1721) copies the built font and, for a `default_location`, calls `_build_in_process(…, default_overrides=requested, relocate_default=True)` — the lossless relocation 0007's 2026-10-19 status describes as built. |
| docs/ARCHITECTURE.md:387-393 | Build routes list | OUTDATED (incomplete) | Omits `GET /api/build/<job_id>` (`routes/build.py:48`; `tests/test_url_map_snapshot.py:27`). |
| docs/ARCHITECTURE.md:428-430 | Kinetikern routes list | OUTDATED (incomplete) | Omits `PUT /api/kinetikern` (`routes/kinetikern.py:71`). |
| docs/ARCHITECTURE.md:451 | "Export options \| worker job: hidden axes, set default (inserted origin master), TTF/WOFF2" | OUTDATED | No inserted origin master; `routes/build.py:150` also accepts `format` = `sources`. |
| docs/ARCHITECTURE.md:458 | "`tests/e2e/api-parity.spec.mjs` pins the parity" | WRONG (path) | File is `frontend/e2e/api-parity.spec.mjs`; `tests/e2e/` does not exist. |
| docs/ARCHITECTURE.md:460-462 | live-only list | OUTDATED (incomplete) | `DESKTOP_ONLY` (`frontend/e2e/api-parity.spec.mjs:30-42`) also has `getMasters`, `getBuildJob`, `setKinetikernEnabled`. |
| docs/ARCHITECTURE.md:466 | "`getMasters` is stubbed to `[]` in static" | WRONG | `static-api.js:1336` "No getMasters here (capabilities.masters is false …)"; `staticOverrides` has no `getMasters`; the parity spec lists it DESKTOP_ONLY. |
| docs/ARCHITECTURE.md:531-534 | "every shadow save (`save_font_atomically`) is atomic (`sidecar_io.atomic_writer` / `write_json`) and runs under `sidecar_io.LOCK`, as do `result.json` and promotion" | WRONG (in part) | `source_font.py:35-62` `save_font_atomically` uses its own `mkstemp` + `os.replace`, no lock; `build_worker.py:164-167` writes `result.json` with `write_text` + `os.replace`; `build_jobs.py` never references `sidecar_io`/`LOCK`, `_promote` is `shutil.move`. Atomic-by-rename yes; "under `sidecar_io.LOCK`" no. |
| docs/ARCHITECTURE.md:257 | stray sentence "After the relocation, `pipeline._fit_gvar_headers` …" | WRONG (misplaced) | Inserted mid-sentence inside step 3.5, breaking the numbered list. |
| docs/SIDECARS.md:10-11 | "Every writer here goes through `sidecar_io`: `atomic_writer` for text and `write_json` for JSON" | WRONG | `instance_aliases.py:42-45` `save` → `p.write_text(json.dumps(...))`; `build/config_generator.py:1112` `config_path.write_text(output)` (the worker's `update_config` path). |
| docs/SIDECARS.md:24 | `.avar2-studio/instance-renames.json` … Atomic **yes** \| Locked **yes** | WRONG | `instance_aliases.py:45` plain `write_text`; no `sidecar_io`, no lock. |
| docs/SIDECARS.md:25 | `.avar2-studio/config.yaml` … "(`config_generator.update_config` in the worker, promoted back)" \| Atomic **yes** \| Locked **yes** | WRONG | `config_generator.write_config` (849-860, 1112): backup is `shutil.copy2` to `.yaml.backup`, write is `write_text`; only `_initialize_preview_config_from_glyphs` uses `atomic_writer` (pipeline.py:2279). ARCHITECTURE.md:533-534 itself says config.yaml is the one thing that is not — the two docs contradict each other. |
| docs/SIDECARS.md:28-31 | "Every loader returns an empty schema-shaped document for a missing or unreadable file … corrupt files are not surfaced as errors yet" | OUTDATED | `control_axes/schema.py:39,54` `SidecarCorruptError` + `load_errors()`; `routes/health.py:65` exposes `sidecar_errors`; SIDECARS.md:35-36 says the opposite two sentences later. |
| docs/SIDECARS.md:217-218 | legacy `.avar2-studio/axis-metadata.json` "migrated to the sibling (then removed) on first access" | WRONG | O2: `pipeline.py:1850-1894` copies and keeps the legacy file. |
| docs/DEVELOPMENT.md:86 | env table = "Every name the code reads" | OUTDATED (incomplete) | Unlisted readers: `AVAR2_STUDIO_COMMIT` (`sources_export.py:59-60`, package code), `STUB_WORKER_RUNS`, `STATIC_URL`, `GRADE_GLYPHS`/`GRADE_CONFIG`, `OUT_TTF`, `FONTRA_EMBED_DIST`, `WIDTH_MATCHER_RESOURCES` (tests/specs). |
| docs/DEVELOPMENT.md:94 | "`AVAR2_FONTRA_PORT` \| `state.py` (`fontra_port`)" | WRONG (symbol) | Reader is `state._fontra_port_from_env` (state.py:14-30). |
| docs/DEVELOPMENT.md:104 | "`AVAR2_SPIKE_FONT`, `AVAR2_ORACLE`, `AVAR2_CSV` \| `tests/avar2_oracle.rs`, `braces_oracle.rs`" | OUTDATED | Read in `wasm/fontc-web/tests/common/mod.rs:35,43,64`; used via avar2_oracle, braces_oracle, spac_oracle, stat_oracle. |
| docs/DEVELOPMENT.md:116-118, 223 | "654 passed, 0 skipped … 620 `def test_` functions" | OUTDATED | `pytest --collect-only` → 656; `def test_` → 622. |
| docs/DEVELOPMENT.md:139 | "Glyphs-plugin tests (about 60 % of the suite)" | WRONG | 256 of 622 (41 %). |
| docs/DEVELOPMENT.md:148-150 | fixtures "from `tests/fixtures/` (`FoldedMini.glyphs`, `StrandedMini.glyphs`, regenerated by `spike/make_stranded_fixture.py`)" | WRONG (path) | They are `wasm/fontc-web/tests/fixtures/{FoldedMini,StrandedMini}.glyphs` and `wasm/fontc-web/spike/make_stranded_fixture.py`; repo-root `tests/fixtures/` holds only `fontra_rewrite_*.html`. |
| docs/DEVELOPMENT.md:157-160 | "`npm run test:unit` runs the node-only specs: `avar2-eval`, `grade-model`, `avar2-lint`" | OUTDATED | `frontend/package.json:12` runs 11 specs; line 77 of the same doc says "eleven". |
| docs/DEVELOPMENT.md:165 | "`static-demo.spec.mjs` (28 sections)" | OUTDATED | 31 numbered sections (1–30 plus 24b). |
| docs/DEVELOPMENT.md:166 | "`editor-bridge.spec.mjs` (55 checks)" | OUTDATED | 57 `ok(` call sites. |
| docs/DEVELOPMENT.md:192-193 | "As of 2026-10-30 **no `v*` tag has been cut**, so 0.1.0 is not on PyPI" | WRONG (first half) | Tags `v0.1.0.dev1`–`dev6` exist (2026-06-06/07) and `release.yml` ran green on each (GitHub Releases). No final `v0.1.0`; PyPI has no `avar2-studio` project (checked 2026-11-02). Say: no final tag, dev tags exist, nothing on PyPI. |
| docs/DEVELOPMENT.md:194-195 | "`python` (a version matrix running pytest + ruff)" | WRONG | `test.yml:21-31`: `os: [ubuntu-latest, macos-latest]`, Python pinned `"3.11"` — an OS matrix. |
| docs/DEVELOPMENT.md:194 | test.yml runs "on every push and PR" | WRONG (imprecise) | `test.yml:8-13`: push/PR only for `main`, `github-pages`, `cleanup-2026-10` (+ `workflow_dispatch`). |
| docs/DEVELOPMENT.md:204-205 | "Crispy's CI installs `avar2-studio @ git+…@github-pages`" | UNVERIFIABLE | Not in this repo. |
| docs/decisions/0015:58-60 | "`doctor --strict` fails when either is missing, so CI can assert a fresh install is complete — `release.yml` does that on the built wheel" | WRONG | `release.yml` has no `doctor` step. `doctor.py:313-321/339-344` return `ok=True` with `configured_missing=bool(env) and not found`; `run()` fails strict only on `configured_missing` or a failed `build_prereq` — an absent accelerator does **not** fail `--strict`. |
| docs/decisions/0015:69-71 | "`release.yml`'s `dist-platform-*` job (cargo builds, staging into `src/avar2_studio/bin/`, `doctor --strict` on the installed wheel)" | WRONG | Job is `build-platform-wheels` (release.yml:137); `dist-platform-*` is the artifact-name pattern; no `doctor --strict` step. |
| docs/decisions/0007:5 | Area: "`server._export_pipeline`, `server._build_export_source`, `server._build_in_process`" | OUTDATED | `_build_export_source` gone; `_export_pipeline`/`_build_in_process` live in `pipeline.py:1597/808` and are not in server.py's back-import list. |
| docs/decisions/0007:54 | "the flag travels in the job `spec`, not `ENV_KEYS`" | OUTDATED (not as built) | The spec has no relocation key; `ENV_KEYS` still carries `AVAR2_RELOCATE_BUILD_DEFAULT`; `pipeline.py:958-960` reads it when `relocate_default is None`; the export hard-codes `relocate_default=True`. |
| docs/decisions/0007:57 | "the first export test … (`_export_pipeline` has none today)" | OUTDATED | `tests/test_export_relocation.py`, `test_export_active_source.py`, `test_cli_export_options.py`, `test_gvar_header_fit.py` exist. |
| docs/decisions/0007:71-74 | Where it lives: "`server._build_export_source` (docstring) … `server._build_in_process` step 3.5" | OUTDATED | Same as the Area row; the 2026-10-19 status update says the staging is gone but these sections were not revised. |

OK counts: CLI.md 31/36; ARCHITECTURE.md 85/98; SIDECARS.md 66/72;
DEVELOPMENT.md 65/78; decisions/README.md 20/20 (15 files ↔ 15 rows, every
link resolves); 0015 17/20; 0007 11/15; plugin README 42/42. Links: all 25
relative links and both anchors in the eight files resolve.
