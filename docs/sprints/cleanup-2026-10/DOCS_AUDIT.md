# Documentation audit — avar2-studio, 2026-10-29 (for approval)

Scope: every reader-facing document on `cleanup-2026-10` at `80bd04e` — the
README, the docs map, the five guides, the three reference documents
(Architecture, Development, Sidecars), the decision records, the Glyphs-plugin
README and the three example READMEs. Every claim was checked against the code
on the branch; the evidence tables are in the appendices. The sprint folder and
`CODEBASE_AUDIT.md` are internal records and were not audited for the reader.

## 1. Verdict

The documentation is complete and mostly right, but it was last brought in line
on 16 October and the sprint has moved on since. Three things need fixing before
it goes to the Google Fonts team:

1. **Stale facts.** About sixty statements no longer match the code. Most are the
   reference documents describing gaps that have since been closed; a few are in
   the README itself, and one is a promise the README cannot keep (install from
   PyPI — the package is not published).
2. **Too technical for the first reader.** The README is 432 lines and reads as a
   manual; the guides each start for a designer and end for a developer
   (routes, module internals, test names). A designer or a Google Fonts reviewer
   has to wade through engineering detail to find how the tool is used.
3. **Duplication.** The same facts are written in three or four places (the CSV
   schema, the environment variables, the example comparison, the Glyphs-plugin
   list, the Kinetikern physics). Every duplicate is a place to go stale.

Nothing here blocks the hand-over in principle; it is a focused rewrite of the
reader set and a fact pass over the appendices.

## 2. Proposed shape

**Reader set** — short, designer-facing, no file-format internals:

| Document | Target | What changes |
|---|---|---|
| `README.md` | ~150 lines | What it is, try it in the browser, install (honest about PyPI), *your files* as the one table, the six features in a paragraph each with a link, a ten-line *Command line* pointer, the docs map, license. Out: the CSV schema (→ Sidecars), the CI recipe and exit codes (→ CLI reference), both environment tables (→ Development), the second Glyphs-plugin description. |
| `docs/README.md` | one page | Two tables: *For designers* and *Appendices (for developers)*; sprint and audit records under one *Internal* line. |
| `docs/guides/*.md` (5) | ≤ ~100 lines each | Keep the designer half of each. Move "how it reaches the font", routes, source-level generation, `regenerate_shadow` internals and the static-app geometry into Architecture, where most of it already is. Kinetikern gets the K1–K4 UI; secondary axes loses the removed button and the undecided designspace question (decided in 0013). |
| `examples/*/README.md` (3) | rewrite | Describe the fixtures as shipped (Roboto Delta Mini has nine axes and nineteen sources; Crispy Mini ships eight mapping rows, a transforms file and declared axis defaults). One comparison table, in `examples/README.md` only. |

**Appendices** — developer-facing, kept, each gets a fact pass:

| Document | Status | Pass needed |
|---|---|---|
| `docs/CLI.md` | **new** | The headless build reference: flags, the committed build config and its keys, exit codes, the CI recipe, `doctor --strict`, and `--format sources` when E1 lands. Today this is scattered across the README. |
| `docs/ARCHITECTURE.md` | keep | Dated 16 Oct; ~20 rows stale (watcher re-pointing, Fontra port, locks, CLI, relocation memo, health keys, API parity). The longest pass. |
| `docs/SIDECARS.md` | keep | Every "atomic write: no / lock: none (T2.1)" annotation is stale (all done); build-dir retention done; one wrong claim about dropped keys. Remove the sprint-ticket ids: it is a format spec, not a changelog. |
| `docs/DEVELOPMENT.md` | keep | "No test workflow yet" (there is), the wasm "committed" claim (built in CI), the Fontra port, test counts, the platform-wheel release jobs; remove the two personal session rules (Crispy venv, port 5070). |
| `docs/decisions/` | keep | Index says twelve records (fourteen); 0007 header says "not built" while its own 19 Oct update says built; decision 0015 is cited by code and does not exist. |
| `src/avar2_studio/glyphs/README.md` | keep | Accurate. Long (360 lines); trim the per-control restatements if desired, no fact changes. |
| `docs/HANDOVER.md` | archive | A redirect table from 16 Oct; its content is history. |
| `docs/authoring-instances.md` | delete | A "moved" stub whose stated reason is gone (all links already point at the guide). |
| `docs/CODEBASE_AUDIT.md`, `docs/sprints/` | internal | Keep, out of the reader map. |

## 3. What is wrong today — the must-fix list

Plain-language summary; line-level evidence in Appendices A and B.

**README**
- Says "install from PyPI" and uses `pip install avar2-studio` in the CI examples. There is no PyPI release (tags stop at a dev tag; the release workflow publishes on a `v*` tag only). Either publish first or say "from git until 0.1.0 is on PyPI".
- The Kinetikern section predates this week's changes: no mention of the on/off switch in the Transforms menu, the per-row spacing badge, the labelled dropdown, the Apply-to-all confirm, or the font-size control.
- The build-options table omits `--config` / `--write-config` (and `--config` then appears unexplained in the CI example); exit-code text misses the unreadable-source and bad-config cases.
- The Crispy CSV example shows rows that do not exist in the file; the dependency-pinning claim is only partly true.
- The Glyphs plugins are described twice.

**Guides**
- *Secondary parametric axes*: documents a button that was removed, and presents the designspace question as undecided (decision 0013 settled it: a clean 501).
- *Kinetikern*: predates K1–K4 entirely; the "Custom…" label is wrong; the route list lacks the new switch.
- *Authoring instances*: fixture facts are from an older Crispy Mini; "four widths and seven weights" is wrong (three and five); the Regular Condensed and Roboto rows it walks through do not exist in the shipped files.
- *Transforms*: two test paths are wrong; the committed build config's transform override (Phase C) is missing.
- *Grade*: no contradictions.

**Reference documents** — see the appendix table; the pattern is the same in all three: a "known gap / sprint item" note that was fixed in the sprint and never updated.

**Decisions** — the index count, 0007's header, the missing 0015.

**Examples** — all three READMEs describe older fixtures; one README anchor is broken.

**Pending by design** — when E1 lands (UFO + designspace zip, `--format sources`), the README's export paragraph, the CLI reference and Sidecars (zip contents) need a line each. Add that to E1's acceptance so the docs move with the code.

## 4. Decisions needed

1. **Shape.** Approve the reader-set / appendix split above, or keep the current flat set and fix facts only. *Recommendation: the split; it is what makes "not too technical" and "not bloated" true at the same time.*
2. **PyPI.** Publish 0.1.0 before the hand-over, or let the README say "from git until 0.1.0 is on PyPI". *Recommendation: the honest line now; publishing is a separate decision you already hold (B11c dry run first).*
3. **Who applies it.** The agent, as sprint item D1 after E1 and before the Pages merge (B11a), so the site ships the updated docs — or me. *Recommendation: the agent, one commit per document, with the fact tables below as its checklist; I verify each claim again at the checkpoint.*
4. **Internal records.** Keep `CODEBASE_AUDIT.md` and the sprint folder linked from the docs map under *Internal*, or unlink them. *Recommendation: keep, one line.*

**Decisions taken (user, 2026-10-29): all four recommendations approved** — the reader-set / appendix split; the README says "from git until 0.1.0 is on PyPI"; the agent applies it as sprint item D1 (findings.md), after E1 and before the Pages merge, with the verifier re-checking every row below at the checkpoint; the internal records stay linked under *Internal*.

Two code notes surfaced by the audit, not documentation: `frontend/src/components/Header.tsx` carries a duplicated no-op expression line after the `enabledCount` assignment (from K1), and two modules cite a decision 0015 that was never written (the wheel-carried `bin/` binaries). Both go to the agent as one-line items regardless of the decisions above.

---

## Appendix A — fact-check: README, Sidecars, Development

Checked at `80bd04e`. Severity: WRONG contradicts the code; OUTDATED was true and changed; MISSING is a landed feature the document should state; UNVERIFIABLE could not be confirmed either way.

### README.md

| line | claim | actual | severity |
|---|---|---|---|
| 55–61 | "Install from PyPI… `pipx install avar2-studio`" | no release tag; release.yml publishes on `v*` only; DEVELOPMENT.md:197 itself says 0.1.0 is not on PyPI | WRONG (today) |
| 348, 366 | `pip install avar2-studio` in the CI examples | same | WRONG (today) |
| 346–347 | deps "pinned with majors-only upper bounds" | pyproject.toml:33–42: glyphsLib `<7`, fontc `<2`; gftools, fontTools, uharfbuzz have no upper bound | WRONG (partial) |
| 133–137 | "Crispy's, abridged" rows `Def`, `Width Matcher Preview` | header is `Instance Name,XTRA,XOPQ,YOPQ,WGHT,WDTH,OPSZ,SPAC`; neither row exists | OUTDATED |
| 242–254 | "**Apply to all…** at one looseness" | button reads `Apply −0.60 to all…` (`applyAllLabel`); confirm names each tuned style (`applyAllConfirm`) | MISSING |
| 242–254 | no family on/off switch | `PUT /api/kinetikern {enabled}`; Transforms menu "Kinetikern" row (Header.tsx:451–461) | MISSING |
| 157–176, 242–254 | no row badge | Instances rows show `K −0.60` with Open / Remove (KinetikernBadge.jsx) | MISSING |
| 246–248 | "pick one, set the looseness" | dropdown reads `<style> · spaced −0.60`; Font Size control above the panes follows the picked style's opsz | MISSING |
| 329–334 | build options table (4 rows) | omits `--config` (default `<stem>-build.json`), `--write-config`, `--csv`; config keys `format, format_version, axes.hide, default, transforms, instances.fix|stat, formats`; studio-authored keys → exit 2 (build_config.py:32–65) | MISSING |
| 324–326 | exit 2 "means the source did not load" | also unreadable source, bad `--default`/`--hide-axes`, bad or missing config, missing axis metadata (cli.py:134–214) | MISSING |
| 333 | `--default` "refused with a readable error" | relocation failure → status `degraded`, exit 1 (cli.py:313–329); memo in `.relocation-memo`; the studio shows a degraded banner | MISSING |
| 390–397 | runtime env table | omits `AVAR2_FONTRA_PORT` (state.py:14–40) | MISSING |
| 377–381 | doctor checks fontc | doctor uses `shutil.which` only (doctor.py:94); the build also looks beside `sys.executable`, so doctor can fail where `build` succeeds | note |
| 22–29 | "7 MB download" | not measured | UNVERIFIABLE |

Verified correct: the server CLI table (306–315), the `.avar2-studio/` row (120), the axis-metadata row (121), install-glyphs-plugins (383–386), the doctor list (377–381), transform names (261–270), user scripts dir (272–274), config export/import (278–287), Load Font / Config menu labels (24–36), Kinetikern unavailable in the browser (49), `--fontra-port` default, the `grade-comparison` branch.

Duplication: 289–302 restates 93–104 and the plugin README; 128–153 duplicates SIDECARS 29–77; 390–401 duplicates DEVELOPMENT 94–109 (six identical rows); 373–375 duplicates the gftools-builder row; 377–381 restates `doctor` output.

### docs/SIDECARS.md

| line | claim | actual | severity |
|---|---|---|---|
| 12 | CSV: "Atomic write: no (T2.1)", "Lock: none (T2.1)" | every CSV writer uses `sidecar_io.atomic_writer` under `sidecar_io.LOCK` (csv_io.py:419; routes/instances.py; pipeline.py:2142) | OUTDATED |
| 13 | control: four writers "unlocked (T2.1)"; two locks | all four `@_locked` under `sidecar_io.LOCK`; one process-wide RLock (schema.py:121, 207, 257, 336, 438) | OUTDATED |
| 14, 15, 17 | grade / transforms / axis-metadata "Atomic write: no" | all through `sidecar_io.write_json` (grade.py:436; transforms/config.py:104; pipeline.py:2056–2066) | OUTDATED |
| 16 | kinetikern: "Lock: none" | every writer `@_locked` (kinetikern/sidecar.py:26–32) | OUTDATED |
| 27 | "unknown keys are dropped at every level" | the control sidecar carries unknown keys through (schema.py:25–32, 583–585, 608–610) | WRONG (control) |
| 26 | "`version` is read and re-emitted" | grade re-emits; transforms and kinetikern always write their schema version | WRONG (partial) |
| 287 | `build/` "accumulates — T2.14" | `KEEP_BUILDS = 2`, `reap_build_dir` (build_jobs.py:50–63) | OUTDATED |
| 341–342 | `server._load_project_zip` | lives at routes/load.py:91 | OUTDATED (symbol) |
| 8 | "as of 2026-10-16" | rows above changed since | OUTDATED |
| 12, 13, 26, 287 | T2.1 / T2.10 / T2.14 "pending" | T2.1 and T2.14 done; T2.10 (surface corrupt files) still returns empty docs | OUTDATED except T2.10 |

Verified correct: SPAC minting, the legacy CSV name, BOM handling, external-edit check, control tag rules and migrations, the grade and transforms normalisers, the kinetikern normaliser, the axis-metadata migration (moves and unlinks — O2 will change this), placeholder repair, exit 2 without metadata, zip import, the jobs snapshot, retention counts, zip safety checks, the config bundle semantics.

Register and length: developer-facing throughout; the sprint-ticket references read as a changelog. 29–77 duplicates README 128–153; 307–337 duplicates README 278–287; 249–276 (`config.yaml`) is internal plumbing with no designer-visible contract.

### docs/DEVELOPMENT.md

| line | claim | actual | severity |
|---|---|---|---|
| 39–41 | "three fontc-gated test files hard-code `REPO/.venv/bin/fontc`" | three do; five more gate on `Path(sys.executable).with_name("fontc")` | OUTDATED |
| 46–48 | "the file watcher is only scheduled at launch" | routes/load.py:222, 339, 360 call `WATCHER.reschedule()` | OUTDATED |
| 52–53 | Fontra "8001, a single global port" | `--fontra-port` / `AVAR2_FONTRA_PORT` | OUTDATED |
| 81–82 | "`npm run test:unit` (the three node-only specs)" | package.json runs 11 specs | OUTDATED |
| 84–85 | wasm "output is committed" | gitignored (.gitignore:64–67); built in CI (pages.yml:40–48, release.yml:46–56) | WRONG |
| 90 | env table = "every name the code reads" | missing `AVAR2_FONTRA_PORT` and seven test-only names | MISSING |
| 97 | `AVAR2_STUDIO_DEMO` read by `server.health` | routes/health.py:40 | OUTDATED (symbol) |
| 107 | spike/oracle fonts default to `/tmp/...` | generated in-test under `target/tmp/` | OUTDATED |
| 119, 226 | "484 passed, 0 skipped on 2026-10-16" | 607 `def test_` in 90 files today | OUTDATED |
| 132–137 | "`test_worker_shadow.py` is the one real-worker build"; fontc-gated list | four more real-worker tests; list incomplete | OUTDATED |
| 143 | "`cargo test` compiles 14 test binaries" | 16 | OUTDATED |
| 146–156 | oracles "need two hand-made fonts until T2.9" + the `/tmp` recipe | fixtures generated in-test from crispy-mini; test.yml says nothing under `/tmp` is needed | OUTDATED |
| 185–190 | pages.yml "ships the committed wasm blob" | self-contradictory; built with wasm-pack, blob gitignored | WRONG (first half) |
| 191–199 | release.yml summary | omits the platform-wheel matrix and `release-platform-assets` (release.yml:132–251; decision 0015) | MISSING |
| 200–201 | "There is no test workflow yet (T2.9)" | `.github/workflows/test.yml` exists: python matrix, cli-build, frontend, rust | OUTDATED |
| 29–38, 49–53 | Crispy venv caveat; port 5070 is the designer's | one person's session rules, not project documentation | remove |
| 138, 171–172, 246–248 | "about 60 %", "28 sections / 55 checks", timings | not counted or measured | UNVERIFIABLE |

Verified correct: dev extras, Fontra from git, spac-inject source, kinetikern2 toolchain, Kinetikern 503s, `.venv` ignored, default port 5001, launcher 5001–5010, fontra-embed 8099, health keys, the 503 without a bundle, vite config, ruff baseline, conftest fixtures, `ENV_KEYS`, the gftools-gen-avar2 shim, pages.yml steps, release.yml publishing, Crispy CI installing `@github-pages`, the smoke endpoints.

Duplication: 96–101 duplicates README 390–397; 219–221 duplicates AGENTS.md; 226 repeats 119; 251–258 duplicates the conventions paragraph in docs/README.md; 45–48 overlaps README 306–319.

## Appendix B — fact-check: guides, Architecture, plugins, examples

### docs/guides/authoring-instances.md

| line | claim | actual | severity |
|---|---|---|---|
| 64–66 | "opens at its `Default` instance — the first master…" | CrispyMini.glyphs has no instances; 8 rows live in `CrispyMini-avar.csv`; the first master is 94/2/2 | OUTDATED |
| 70–73 | Regular Condensed row wght 400 wdth 40 → 456.3/228.9/164.8 | no such row; WDTH declared 20–200 | UNVERIFIABLE |
| 137–140 | Roboto Delta Mini `Regular`/`Bold` rows | the designspace declares nine parametric axes only, no `wght`, no CSV | UNVERIFIABLE |
| 150 | "+ New instance" | label is `+ New Instance` (Sidebar.jsx:682) | WRONG (label) |
| 179–181 | "Crispy… 30 instances spanning four widths and seven weights" | 30 rows; three widths, five weights, two optical sizes | WRONG |

Designer-facing. "What both approaches share" / "Choosing between them" / "Hands-on" duplicate examples/README.md.

### docs/guides/secondary-parametric-axes.md

| line | claim | actual | severity |
|---|---|---|---|
| 114–118 | "**Pin layers to axis extremes**" | button removed (commit cc2eaf8); LayersEditor offers Add layer / Add applicable glyphs / Add layer for all | OUTDATED |
| 306–319, 373 | designspace "T2.15 decides parity or not supported" | decided: decision 0013; designspace authoring answers 501 (routes/control_axes.py:47–58) | OUTDATED |
| 227 | slider "in two places" | also in the Kinetikern tab (KinetikernTab.jsx:364) | MISSING |

Everything else verified. Titled "user reference" but half developer material; "The shadow source file", "How regenerate_shadow works", "Corrections in the static app", "Vocabulary", "Re-seeding" duplicate ARCHITECTURE §3, §5, §6.

### docs/guides/grade.md

No contradictions (every constant, cap, route and test verified). Minor: ungraded rows show `+ Grade`, not a `G` badge. "Source-level generation", "The static port", "Routes and storage" are developer register and duplicate ARCHITECTURE §3/§5 and decision 0001.

### docs/guides/kinetikern.md

| line | claim | actual | severity |
|---|---|---|---|
| 21–22 | engine via build or `AVAR2_KK2_DYLIB` | also the wheel-carried `bin/libkinetikern2.dylib`; code cites decision 0015, which does not exist | MISSING |
| 46–47 | "**Custom…**" | option is `Custom location` + a "Name for this location" field | WRONG (label) |
| — | family on/off switch | `PUT /api/kinetikern {enabled}`; entries kept while off | MISSING |
| — | Transforms menu row | "Kinetikern" row, "N styles spaced" / "Off — … keeps the entries" | MISSING |
| — | Instances row badge | `K −0.60`, popover Open in Kinetikern / Remove, only while enabled | MISSING |
| 28–31 | dropdown | `<style> · spaced −0.60` | MISSING |
| — | Font Size | above the before/after panes; follows the picked style's opsz, clamped 9–144 | MISSING |
| 51–63 | propose | route also returns per-glyph `applied` and `looseness_applied` | MISSING |
| 95–96 | test gating | `test_kinetikern_inject.py` needs fontc only; `test_kinetikern_enabled_route.py` exists | minor |
| 102–105 | routes | lacks `PUT /api/kinetikern` | MISSING |

"How it reaches the font" duplicates ARCHITECTURE §4 and decision 0004; "What gets stored" duplicates SIDECARS; "Routes" duplicates ARCHITECTURE §5.

### docs/guides/transforms.md

| line | claim | actual | severity |
|---|---|---|---|
| 58–60, 68 | `tests/spac_oracle.rs`, `tests/spac_csv_parity.rs` | both under `wasm/fontc-web/tests/` | WRONG (path) |
| 58–64 | native-injector lookup | also the wheel-carried `bin/spac-inject`; forwarded to the worker via `ENV_KEYS`; reported by doctor | MISSING |
| 141–146 | "Where it is stored" | the committed `<stem>-build.json` can carry a transforms list that overrides the sidecar for a headless build; `instances.fix|stat` fold into `fix_instances`/`gen_stat`; the override travels to the worker (`transform_override`); `--write-config` emits it; design-only keys refused | MISSING |

Table and "Writing your own" are the reader parts; the rest is developer register duplicating ARCHITECTURE §3/§4 and SIDECARS.

### docs/ARCHITECTURE.md

| line | claim | actual | severity |
|---|---|---|---|
| 21–26, 524–526 | observer scheduled once, not re-pointed (T3.10) | `/api/load-source` calls `WATCHER.reschedule()` (routes/load.py:222, 339, 360) | OUTDATED |
| 39–40, 207–212 | only `ENV_KEYS` forwarded; snapshot = sidecars + preview files | spec also carries `transform_override` and `memo_dir`; `SIDECAR_SUFFIXES` includes `-axis-metadata.json` | MISSING |
| 42–49 | `--http-port 8001`; Fontra port "a single global" | `AVAR2_FONTRA_PORT` / `--fontra-port` | OUTDATED |
| 82 | `cli.py`: "`build SOURCE --out DIR`" | also `--workdir`, `--hide-axes`, `--default`, `--format`, `--config`, `--write-config`; the in-process export path; refuses without axis metadata | MISSING |
| 84 | "37 mutable runtime-state globals" | 44 | OUTDATED |
| 78–104 | module map | no rows for `build_config.py`, `sidecar_io.py`, `studio_log.py`, `examples.py` | MISSING |
| 88 | pipeline symbols | missing `_fit_gvar_headers`, the relocation memo constants, `_build_status`, the sibling-metadata helpers | MISSING |
| 96, 135 | `zip-workspace.js` mirrors `server._load_project_zip` | lives in routes/load.py | OUTDATED |
| 131 | `e2e/` under `frontend/src/` | it is `frontend/e2e/` | WRONG |
| §3 | axis metadata | the sibling file as canonical with one-time migration is not mentioned | MISSING |
| 218–222 | `KEEP_EXPORTS = 5` | also `KEEP_BUILDS = 2`, `KEEP_RESULTS = 50` | MISSING |
| 244–248 | step 3.5 relocation | the relocation memo is not described | MISSING |
| 252–253 | "`fontc -o …`" | fontc resolved on PATH with the interpreter's dir first; plain build falls back to beside `sys.executable` | MISSING |
| 282 | "`build` treats any `LAST_*` as a failing exit" | `LAST_TRANSFORM_ERROR` and `LAST_ORIGIN_WARNING` do not fail the exit (cli.py:294–344) | WRONG |
| 345–350, 360–366 | health keys | also `sidecar_errors`, `origin_relocation_error`, `origin_relocation_memo`, `degraded` | OUTDATED |
| 367–370, 386–389 | build / Kinetikern routes | `GET /api/build/<job_id>` and `PUT /api/kinetikern` absent | MISSING |
| 441–450 | "T2.7 adds `api.capabilities`"; provider drift lists | landed (api.js:51, static-api.js:1314, e2e/api-parity.spec.mjs); lists stale | OUTDATED |
| 470–476 | `_FONTRA_STUDIO_SHIM_JS` injected before `</head>` | shim read from `fontra/embed-shim.js` by `routes.fontra._studio_shim_script`; no such constant | OUTDATED |
| 511–516 | "Not yet atomic or locked: the CSV, grade, transforms (T2.1)" | all under `sidecar_io.LOCK` with atomic writers | OUTDATED |
| 3 | "as of 2026-10-16" | every row above postdates it | OUTDATED |

Developer-facing, consistent. §4 physics bullets repeat decisions 0001/0004/0006 and the Kinetikern guide; §6 repeats §2 and the guides' static-port paragraphs; §7 repeats decision 0010.

### src/avar2_studio/glyphs/README.md

No contradictions: the hub, the seven bundles, the installer record, the launcher ports, every file and test named exist; "Metrics Parity" is the menu and bundle display name while the folder is still `ParametricMasters.glyphsReporter` (fine). Length: Slant Master (~75 lines) and Corner Radii (~45) restate every panel control; "Development notes" overlaps the module docstrings.

### examples/README.md, crispy-mini/README.md, roboto-delta-mini/README.md

| file:line | claim | actual | severity |
|---|---|---|---|
| examples/README.md:18–36 | Roboto: `XOUC/YOUC/XTUC` only, "only uppercase responds" | nine axes (XO/YO/XT × UC/LC/FI), nineteen UFOs | OUTDATED |
| examples/README.md:21 | Crispy Mini "1 (`Default` at first master)" | no instances in the source; eight rows in the CSV | OUTDATED |
| examples/README.md:39 | "each fixture has an empty traditional `wght` axis" | neither source has a `wght` axis; Crispy Mini's are studio-declared | WRONG |
| crispy-mini:5–9, 59–69 | "one `Default` instance… starts at (2)"; no wght | ships the CSV (WGHT/WDTH/OPSZ, 8 rows), declared defaults, a transforms file | OUTDATED |
| crispy-mini:21–28, roboto:105–113 | section names; `README.md#axis-surface…` anchor | labels are `Core / parametric axes` and `SECONDARY PARAMETRIC AXES`; the anchor does not exist | OUTDATED + WRONG |
| crispy-mini:55–57 | defaults XOPQ 1016, YOPQ 462 | origin master 94/2/2 | WRONG |
| roboto:5–7, 59–80, 138–152 | "three parametric axes… one empty `wght`… 7 sources, 0 instances" | nine axes, no `wght`, nineteen sources, one `Default` instance | OUTDATED / WRONG |
| roboto:50–55, 87–94 | lowercase don't respond; "64 glyphs" | lowercase and figure axes shipped; 67 glyphs | OUTDATED |
| roboto:118 | "add `wght` values in the CSV manually" | AVAR2 MAPPINGS `+ Add` and the mapping route exist | OUTDATED |
| all three | links to `docs/guides/authoring-instances.md` | correct; the `docs/authoring-instances.md` stub is therefore deletable | — |

The example comparison is written three times (examples/README, crispy-mini/README, the authoring guide); the uppercase-only point three times in roboto's README, and it is no longer true.

## Appendix C — duplication map

| Fact | Written in |
|---|---|
| The mappings CSV schema | README 128–153; SIDECARS 29–77 |
| Runtime environment variables | README 390–397; DEVELOPMENT 94–109 |
| The Glyphs plugins and their tools | README 93–104; README 289–302; glyphs/README.md |
| The example comparison | examples/README.md 15–42; crispy-mini/README.md; guides/authoring-instances.md |
| Kinetikern "how it reaches the font" and the KB figures | guides/kinetikern.md; ARCHITECTURE §4; decision 0004 |
| Grade source-level generation / routes | guides/grade.md; ARCHITECTURE §3 and §5 |
| Shadow source and `regenerate_shadow` | guides/secondary-parametric-axes.md; ARCHITECTURE §3 |
| Transform ordering and failure semantics | guides/transforms.md; ARCHITECTURE §3/§4 |
| Static-app geometry / parity | guides (grade, kinetikern, secondary axes); ARCHITECTURE §6; decision 0010 |
| Code-comment conventions | docs/README.md; DEVELOPMENT 251–258 |
| The configuration bundle | README 278–287; SIDECARS 307–337 |

## Appendix D — proposed table of contents

**README.md**: avar2-studio · Try it in the browser · Install (studio from git until 0.1.0 is on PyPI; headless; optional editor; optional accelerators; in Glyphs) · Your files (the table only) · Features (Instances & mappings · Secondary parametric axes · Preview & export · Grade · Kinetikern · Build transforms · Configuration export/import) one paragraph each · Command line (ten lines + link) · Documentation · Roadmap · License.

**docs/CLI.md** (new appendix): `avar2-studio build` · the committed build config and its keys · exit codes · `doctor --strict` · building in CI (the yaml) · `install-glyphs-plugins` · environment variables the build reads · `--format sources` (when E1 lands).

**Guides**: each = what it is for the designer · the controls · what gets saved (one line, by name of the tab, not the file) · a link to the Architecture section for the mechanism.

**docs/README.md**: For designers (README, the five guides, examples) · Appendices for developers (CLI, Architecture, Development, Sidecars, Decisions, the plugin README's development notes, kinetikern2 README) · Internal (codebase audit, sprint records, archive).
