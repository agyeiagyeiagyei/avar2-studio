# Documentation fact-check at `c1c8aca` (verifier, 2026-11-05)

The two D1 briefs were re-run against the ROUND 24 checkpoint checkout (`~/avar2-verifier/clone-c1c8aca`, read-only) by two verifier subagents. Where the code tells against a row, the verifier read the code itself before listing it as blocking: the install recipe, the `--workdir` metadata path and the degraded exit.

Only rows that do not hold are listed. They are the spec for **D1a2**. The rule is the same as D1: facts come from the code, and a WRONG, OUTDATED or MISSING row is fixed or removed, never softened. An UNVERIFIABLE claim about a production font is measured, with number and date, or removed.

**Convergence rule.** The next check is these rows, plus a fresh pass over only the lines D1a2 changes. A new pass over unchanged text is not part of this item's acceptance.

**Previous spec (`factcheck-5e28b47.md`).**
- Section A: all seven rows FIXED. Two clauses remain UNVERIFIABLE: "Roboto Delta has more" (authoring-instances.md:115) and the appendix's Crispy corner numbers (appendix:58-91).
- Section B: 40 FIXED, 3 STILL WRONG, all repeated below. They are ARCH lock/atomic, DEV test counts and DEV plugin-test share.

## A. Reader set (README, docs/README.md, the six guides incl. the new appendix, the three example READMEs)

| file:line | statement | verdict | evidence |
|---|---|---|---|
| README.md:22-24 | both demo fonts are live: edit, declare secondary axes, grade, rebuild | WRONG (Roboto Delta Mini) | a `.designspace` has no `sourceText` (`zip-workspace.js:120`); `buildFont` throws "Rebuilding a .designspace needs the full app" (`static-api.js:1508-1509`); `rebuildUploadFont` throws (`:1086-1087`) and backs createControlAxis/setGrade/setInstanceGrade (`:1780-1840`) |
| README.md:59-60 | `--strict` exits non-zero when an accelerator is missing | WRONG | accelerator checks return ok=True (`doctor.py:313-321, 337-344`); strict fails only on `configured_missing` or a failed `build_prereq` (`doctor.py:382-384`) |
| README.md:77 | the source is touched only on an explicit row action | WRONG (incomplete) | ↗ on a source-derived axis layer opens and saves the ORIGINAL in Fontra (`routes/control_axes.py:195-204`; "Editing your actual source file.", `FontraEditorModal.jsx:149`) |
| README.md:83; examples/crispy-mini/README.md:25 | the build exits 2 without `<stem>-axis-metadata.json` | WRONG (incomplete) | only when the legacy `.avar2-studio/axis-metadata.json` is also missing (`cli.py:79-81`); contradicts CLI.md:78-79 |
| README.md:128-130 | the built-in transforms list | MISSING | "Smooth unhinted rendering" (`fix_unhinted`, `transforms/registry.py:66`, `builtin_gftools.py:77-78`) |
| README.md:135-136 | Download workspace (.zip) as a studio feature | WRONG (scope) | rendered only when `staticMode && allowImportInStatic` (`Header.tsx:578-587`); implemented only in `static-api.js:1464` |
| authoring-instances.md:9-14 | in both example fonts the avar2 table translates the traditional axes | WRONG (Roboto Delta Mini) | no `-avar.csv`, no `wght` in that fixture (its README line 29 says so) |
| authoring-instances.md:26-33 | "Crispy ships" XOPQ 2–1016, YOPQ 2–462, XTRA 94–3330 | WRONG | those are Crispy MINI's ranges (`CrispyMini-axis-metadata.json`); production Crispy spans XTRA 47–1715, XOPQ 1–1462, YOPQ 1–275 |
| authoring-instances.md:89-92, 100, 115 | production Roboto Delta adds case-scoped/counter axes; "9 (or more)"; "has more" rows | UNVERIFIABLE | no measurement or date |
| grade.md:66-68 | the badge's **slider** is bounded by `max_pct`; an undeliverable grade is unreachable | WRONG | a number input (`GradeBadge.jsx:78-86`); at cap 0 `maxPercent = 100` (`:27`) and a warning shows (`:91`) |
| grade.md:73-76 | the **Grade panel** shows a per-instance verdict | WRONG (UI name) | no such panel; per-instance verdicts are in the G badge popover (`GradeBadge.jsx:94`); the Transforms menu shows axis scope only (`GradeDiagnostics.jsx:20-22`) |
| grade.md:93-95 | grade declared but not generated → the status banner says why | WRONG | `grade_error` is exposed (`routes/health.py:80`) but nothing in `frontend/src` reads it; `StatusBanners.jsx` has no grade banner (the CLI exit-1 part is right) |
| grade.md:99-101 | the font grows by a few KB of gvar per graded instance | UNVERIFIABLE | no measurement |
| grade.md:115-116 | sidecar `pct` omitted = the default | WRONG | the resolved value is written at save (`grade.py:209`); a missing `pct` reads as 0.25 (`_normalise`); contradicts grade.md:60-62 |
| kinetikern.md:24-25; README.md:59 | the tab says the engine is not built | OUTDATED | since K5: "Kinetikern2 engine not available in this installation, so the Kinetikern tab is disabled." (`routes/kinetikern.py:30,143`) |
| secondary-parametric-axes.md:40-41 | AVAR2 MAPPINGS axes mutate the source directly | WRONG | declaring writes the CSV and metadata only (`routes/avar2.py:501, 527`); the source is written by instance-row actions (`server.py:363,382,421`) |
| secondary-parametric-axes.md:44-46 | an axis that moves every glyph stays under AVAR2 MAPPINGS | WRONG | such axes render under "Core / parametric axes" (`Sidebar.jsx:323-331`) |
| secondary-parametric-axes.md:105-106 | the Instances sidebar slider value is preview state | WRONG | saved per row: "glyph-scoped · saved per row" (`Sidebar.jsx:317-319, 339`; `useStudioData.jsx:661-664`); contradicts :93-98 |
| secondary-parametric-axes.md:132-135 | ↗ on any layer row; saving writes the shadow | WRONG (incomplete) | source-derived axes open and save the original (`routes/control_axes.py:195-204`) |
| secondary-parametric-axes.md:144-145 | rows carry `studio` and `scoped` badges | WRONG | no "studio" badge; only source-derived rows show "scoped" (`ControlAxes.jsx:177-183`); the slider badge reads "secondary" (`AxisControl.jsx:131-137`) |
| appendix:58-63, 83-85, 90-91 | production Crispy XOPQ 1462, default at XTRA 47, corner (47·1462·275), anchor (1715,1462,275) | UNVERIFIABLE | consistent with today's Crispy, but undated |
| appendix:148-149 | "Open in new tab" opens the raw editor | WRONG | the drawer renders only "Done editing" (`FontraEditorModal.jsx:154-162`); `directUrl` is never rendered |
| appendix:159-165 | HUD shows pre-edit stem/bar/contrast/advance; reference `H,N,O` by default | WRONG | columns bar / stem / s/b, no advance (`fontra/shim.js:227`, `embed-shim.js:170`); live every 400 ms (`shim.js:405-412`); one typed reference glyph, blank = off (`shim.js:221-232`) |
| examples/README.md:28-29 | Roboto Delta readable from 8 pt | UNVERIFIABLE | production claim |
| examples/README.md:33-34; crispy-mini/README.md:33-35; roboto-delta-mini/README.md:35-36 | Roboto Delta Mini's axes appear "as control axes" | WRONG + register | the sliders sit under "Core / parametric axes" (`routes/source.py:80,97`; `Sidebar.jsx:323-324`) and are also listed read-only in SECONDARY PARAMETRIC AXES; the UI never says "control axes" |
| examples/README.md:37-38 | the guide animates each workflow | WRONG | only `regular_to_bold.gif` is embedded (authoring-instances.md:94) |
| crispy-mini/README.md:43-44; roboto-delta-mini/README.md:38-39 | the animation comes from the web article's scripts | WRONG | produced by the script beside the GIF (`examples/*/docs/animate_*.py`), as authoring-instances.md:130-131 says |

**Register rows.** These are code names, response schemas, routes and status codes in the reader set. The D1b grep patterns do not catch them, but they break the D1 register rule just the same.

| file:line | text |
|---|---|
| grade.md:37, 66, 81, 106-108 | `grade.grade_coords`, `grade.max_pct_for`, `max_pct`, `grade_shadow.apply_grades`, `apply_grade`, `grade-model.js` |
| kinetikern.md:65, 67-77, 92 | `propose`; `coupling = 0`, `skip_pass2`; response keys `spaced`, `applied`, `location`, `looseness_applied`, `upm`, `mean_*_per_1000`, `ms`, `engine` |
| transforms.md:31, 33, 42, 66, 70, 126, 152 | "the PUT refuses it"; `requires_axis_tags`, `get_axes`, `params.instance_spac`, `ENV_KEYS`, `Transform.validate`, `transform_override` |
| secondary-parametric-axes.md:21-22, 27, 31, 124 | a vestigial route mention; `source: "source"` / `"studio"` (coverage API fields); "answers 501" |
| appendix:40, 137-139 | "the delta API"; `reseeded`/`blocked`/`computed`/`clean`/`missing`, "409 unless `force`" (the UI action is ⟳ "Update from source") |

Accepted exception: transforms.md:131-136, the plugin authoring contract (`Transform`, `TransformSpec`, `ParamSpec`, `apply(...)`, `ctx`). It is the user-facing API for people who write transforms.

## B. Appendices (CLI.md, ARCHITECTURE.md, SIDECARS.md, DEVELOPMENT.md, decisions index + 0007 + 0015, the Glyphs plugin README)

| file:line | statement | verdict | evidence |
|---|---|---|---|
| CLI.md:103, 115 | `pip install "git+https://github.com/agyeiagyeiagyei/avar2-studio"` then `doctor --strict` / `build --config … --workdir` | **WRONG (blocking: users get stale code)** | GitHub's default branch is `main` at `a37ca53` (2026-09-12; verifier: `git ls-remote --symref origin HEAD`); there `build` takes only `--out`/`--csv` and `doctor` has no `--strict`, so the recipe exits 2. Crispy's own CI pins `@github-pages` |
| CLI.md:77-79 | defaults come from the sibling or the legacy `.avar2-studio/axis-metadata.json` | **WRONG under `--workdir` (code defect: O1b in findings)** | the CLI pre-check reads `source.parent/.avar2-studio` (`cli.py:60-71`); the pipeline reads `WORKDIR/.avar2-studio` (`pipeline.py:1770-1776, 1848-1851`), writes `{}` to the sibling beside the source (`pipeline.py:2022-2025`), and the build rests at the compiled corner with exit 0 |
| CLI.md:83-84, 23, 122-123 | design-authored state is never written by the build; `--workdir` keeps a CI checkout untouched | WRONG (edge cases) | `_initialize_preview_csv_from_glyphs` creates `<stem>-avar.csv` beside the source when missing (`pipeline.py:2152-2190`); metadata migration and the empty-file write go beside the source (`pipeline.py:1876-1881, 2022-2025`) |
| CLI.md:92 | exit 2 causes | MISSING | a `SourcesError` exits 2 (`cli.py:240-242, 315-317`; `test_a_sources_error_is_a_usage_error`); a `--write-config` OSError exits 2 (`cli.py:157-159`); `--format sources` needs a `.glyphs` source (not said at :17/:26) |
| CLI.md:144-149 | install-glyphs-plugins | MISSING (minor) | `--uninstall` (`glyphs_install.py:96-99`) |
| ARCHITECTURE.md:62-70 | `selectApiMode()` probes `/api/health` with a 1.5/4/8 s ladder; a blind static tab keeps polling | OUTDATED | detection is the `<meta name="avar2-backend">` marker (`static-api.js:2069-2072`); the ladder runs only in Vite dev (`:2077-2096, 2116`) |
| ARCHITECTURE.md:98, 536-547; SIDECARS.md:10-19 | every sidecar writer takes `sidecar_io.LOCK` and writes atomically, two exceptions named (STILL WRONG from 5e28b47) | WRONG | not locked or not atomic: `config_generator.write_config` (`:1112`), `instance_aliases.save` (plain `write_text`, `:45`), the upload/zip loader copies (`shutil.copy2`/`FileStorage.save`, `routes/load.py:179-219, 318-331`); config.yaml promotion is `shutil.move` (`build_jobs.py:544-547`); `SIDECAR_LOCK`, `sidecar_io.LOCK` and `_INSTANCE_VALUES_LOCK` are one RLock (`server.py:126`, `control_axes/schema.py:121`) |
| ARCHITECTURE.md:114 | `glyphsapp/` AppleScripts open the source / reveal in Glyphs | WRONG | `document_modified.applescript` (unsaved-changes probe, `server.py:169`), `reload_document.applescript` (save, close, reopen, `server.py:226`) |
| ARCHITECTURE.md:134 | `wasm/fontc-web/` is the committed wasm-pack output | WRONG | `fontc_web_bg.wasm` is gitignored (`.gitignore:67`) |
| ARCHITECTURE.md:152-153 | every Rust module has a cargo oracle test | WRONG | `iup.rs`, `extrapolate.rs`, `stat_registry.rs` have none |
| ARCHITECTURE.md:172-173 | ORIGINAL_PATH is never written by studio authoring | WRONG | source writeback writes it (`routes/instances.py:415-425, 710, 799`) |
| ARCHITECTURE.md:236-237 | result files reaped by `KEEP_RESULTS = 50` | WRONG | it bounds the in-memory `_results` (`build_jobs.py:54-57, 237-245`); `result.json` goes with the job dir (`:424-425`) |
| ARCHITECTURE.md:289-290, 454 | fallback is fontc, or fontmake with `--no-fontc` | WRONG (incomplete) | also falls back to fontmake when fontc fails or is missing (`pipeline.py:1352, 1354`) |
| ARCHITECTURE.md:291-292 | `/api/health` exposes every `LAST_*` | WRONG (minor) | not `LAST_ORIGIN_AUTHORED_AVAR` (`health.py:36-93`) |
| ARCHITECTURE.md:302-305 | which `LAST_*` fail the CLI exit | MISSING (code defect: O1b) | `LAST_BUILD_DEGRADED` is in neither list; the CLI exits 0 on it (`cli.py:355-405`) |
| ARCHITECTURE.md:357-358 | four stacked SPAC axes observed once | UNVERIFIABLE | the code says "several" (`pipeline.py:220`) |
| ARCHITECTURE.md:379-381 | five UI routes give 503 without `static/` | WRONG (minor) | only `GET /`; `/manifest.json` 404, `/favicon.ico` 204 (`routes/ui.py:14-26, 61-73`) |
| ARCHITECTURE.md:408 | `DELETE /api/instance/<name>` options | MISSING (minor) | `?source_only=true` (`routes/instances.py:743-756`) |
| ARCHITECTURE.md:447-448 | the fall-through takes all methods | WRONG (minor) | no PATCH (`tests/test_url_map_snapshot.py:16`) |
| ARCHITECTURE.md:464 | coverage audit / pins / out-of-range live routes exist but are dead | WRONG | `/api/glyph-coverage` is live and used (`api.js:365` → `Sidebar.jsx:30, 376`); the dead one is `/api/text-width` |
| ARCHITECTURE.md:477 | `openControlAxisInEditor` takes two extra arguments in static | WRONG | identical signatures (`api.js:541`, `static-api.js:1938`; `api-parity.spec.mjs:183-188`) |
| ARCHITECTURE.md:500 | the shim is `_FONTRA_STUDIO_SHIM_JS` | WRONG (symbol) | `_studio_shim_script()` (`routes/fontra.py:123-128`) |
| ARCHITECTURE.md:521-522 | `init` session fields | MISSING (minor) | `auto_enable_edits: true` (`editor-bridge.js:780`) |
| ARCHITECTURE.md:548-556 | watcher classes; sidecars not watched; `jobs/` covered | OUTDATED | a `sidecar` class exists (`watch.py:128-131, 205-209, 236-239`); `jobs/` events dropped (`:115-120`); created/moved handled (`:158-168`) |
| SIDECARS.md:29 | `instance-renames.json` Locked: no | WRONG | written under `@server._sidecar_locked` (`routes/instances.py:565-566, 594`) |
| SIDECARS.md:41-42 | grade/transforms/Kinetikern always write their current version | WRONG (minor) | grade re-emits the stored version (`grade.py:412`) |
| SIDECARS.md:86-87 | CSV sync before every staged build | WRONG | only for `source_refresh` in (original, source) (`build_jobs.py:332-345`) |
| SIDECARS.md:256-258 | an imported axis-metadata file migrates to the sibling | WRONG when a sibling exists | the import writes `.avar2-studio/axis-metadata.json` (`routes/load.py:179-186, 211-215, 328-333`); an existing sibling wins (`pipeline.py:1874-1875`), so a re-upload's metadata is ignored |
| SIDECARS.md:299-317 | `.avar2-studio/` layout | MISSING | `build/.relocation-memo/` (`pipeline.py:697-698`) |
| SIDECARS.md (whole) | files the studio writes | MISSING | `<stem>-build.json` (`build_config.py:207-210`) |
| DEVELOPMENT.md:118, 231 | 667 collected, 632 `def test_` (STILL WRONG) | OUTDATED | 668 / 633 |
| DEVELOPMENT.md:141 | plugin tests ~20 %: 126 of 632 (STILL WRONG) | WRONG | 193 tests stub modules; tests touching plugin bundles 256 of 633 (13 files, 40 %) |
| DEVELOPMENT.md:146 | `cargo test` compiles 15 binaries | WRONG (minor) | 15 + lib + `spac-inject` harnesses = 17 |
| DEVELOPMENT.md:149 | "crisp-mini" | WRONG (typo) | `examples/crispy-mini` |
| DEVELOPMENT.md:184-189 | pages.yml step order | WRONG (minor) | wasm-pack runs before Node/npm ci/test:unit (`pages.yml:46-56`) |
| DEVELOPMENT.md:190-193 | release.yml: five jobs on a `v*` tag; build steps | WRONG / MISSING (minor) | `publish-testpypi` runs only on `workflow_dispatch` (`release.yml:272-274`), four on a tag; the wasm-pack steps (`:55-56`) omitted |
| DEVELOPMENT.md:28 | without the dylib the Kinetikern routes answer 503 | WRONG (minor) | only propose, `PUT instances/<n>`, `PUT all` (`routes/kinetikern.py:29-30, 137-151, 187-191`) |
| DEVELOPMENT.md:198, 251-252 | release.yml green on each dev tag; shadow ≈1 s, fontc ≈0.09 s, Crispy ≈36 s | UNVERIFIABLE | no record or date (code says fontc 0.06 s, relocation ~27 s, `pipeline.py:816, 1008`) |
| decisions/0007:29-31 | `_build_export_source` strips non-exporting glyphs | OUTDATED | removed symbol, present tense |
| decisions/0007:33-39 | `_export_pipeline` rounds; no per-call switch | OUTDATED | exact `mapped`, no round (`pipeline.py:1662-1667`); `relocate_default`/`relocate_target` (`:808-813, 952-963`) |
| decisions/0007:57 | the spec does not carry the env switch | WRONG | `AVAR2_RELOCATE_BUILD_DEFAULT` in the spec env (`build_jobs.py:44-46, 494`; `build_worker.py:83-87`) |
| decisions/0007:59-62 | four tests assert natural tuple counts | WRONG (overclaim) | one does (`test_export_build_keeps_tuple_counts_natural`) |
| decisions/0007:66-68 | until T3.8 lands, not point-identical | OUTDATED | its own status says built 2026-10-19 |
| decisions/0007:72-74 | corrections composed in `control_axes.shadow_designspace` | WRONG (symbol) | `.glyphs` composition is `regenerate_shadow` in `shadow_glyphs.py` |
| decisions/0015:38-39 | env override used when the named file exists | WRONG (precision) | a set override naming a missing file returns None, no fall-through (`kinetikern/__init__.py:42-45`, `builtin_spac_widthaware.py:72-75`) |
| decisions/0015:53-54 | one wheel per platform instead of one universal wheel | WRONG | the `py3-none-any` wheel is still built and published (`release.yml:39-136, 253-270`) |
| glyphs/README.md:106, 151, 186, 270 | "View → Instance Delta" etc. | WRONG (minor wording) | Glyphs says "View → Show …", as the same README does at :39, :42, :47 |

All relative links and #anchors in both sets resolve: 90 targets and 14 anchors in A, and 25 links and 2 anchors in B.
