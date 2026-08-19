# Handover — project state & undocumented knowledge (July 2026, updated August 2026)

This doc captures everything a new developer needs that is **not**
already written down. Read the existing docs first — this deliberately
doesn't repeat them:

- [README.md](../README.md) — what the tool is, install, launch,
  working-tree layout, axis surface, Preview tab, transforms (user-facing)
- [docs/authoring-instances.md](./authoring-instances.md) — the
  instance/mapping authoring workflow, both example fixtures
- [docs/secondary-parametric-axes.md](./secondary-parametric-axes.md) — secondary
  parametric ("control") axes: the user reference (authoring, coverage, editing)
- [docs/design-notes.md](./design-notes.md) — the same feature's design record:
  what isn't built, alternatives weighed, the Fontra integration paths
- [docs/grade.md](./grade.md) — the grade feature's weight model
- [docs/migration-github-pages.md](./migration-github-pages.md) — the
  static (GitHub Pages) port: wasm crate internals, coverage audit,
  corner pinning, dropping out-of-range sources, zip workspaces
- [docs/debugging-2026-08-18.md](./debugging-2026-08-18.md) — the
  Aug-18 bug-hunt record: dead default cross, avar2-eval spec-layout
  rewrite, grade clamping at the box edge, stale-CSV axis loss, the
  mapping lint — supersedes several claims in §4 below (noted inline)

Everything below is tribal knowledge as of this handover.

---

## 1. Repo state at handover

- **Branching & deploys:** the working branch is `github-pages` (ff'd
  to `main` periodically — `main` is never ahead). Pushes to either
  branch trigger `.github/workflows/pages.yml`, which builds the static
  demo (`frontend/dist-pages`) and deploys it to GitHub Pages:
  <https://agyeiagyeiagyei.github.io/avar2-studio/>. Commit + push IS
  the deploy. The **fly.io demo is gone** (fly.toml and its Dockerfile
  deleted, the fly app destroyed) — don't look for it.
- Notable parked branch: `grade-comparison` (WIP — grade-master
  comparison sidebar using uharfbuzz advances).
- **Releases:** pushing a `v0.1.0.devN` tag triggers
  `.github/workflows/release.yml`, which builds the React bundle,
  assembles the wheel (bundle force-included at
  `src/avar2_studio/static/`), and attaches it to a GitHub Release. The
  latest release is **v0.1.0.dev6 (June) but `pyproject.toml` is at
  dev8** — the released wheel predates nearly everything below; tagging
  a fresh release is the single highest-leverage chore. Not on PyPI yet.
- **The Crispy repo** (`~/Documents/Crispy`) is the parent project this
  tool was extracted from. The legacy `preview-app/` and all avar2
  tooling there are now in `archive/avar2-tooling/` (gitignored,
  pending deletion review) — **do not develop there**; avar2-studio is
  canonical.

## 2. Development environment — the traps

- **The repo `.venv` is the only correct environment.** It has
  avar2-studio installed **editable**, plus packages NOT in
  `pyproject.toml`:
  - `fontra` and `fontra-glyphs` — soft deps, installed **from git**
    (neither is on PyPI under those names; see the docstring at
    `_ensure_fontra_running` in `server.py`). Only needed for the
    embedded outline editor; everything else works without them.
  - (`flask-sock` used to be missing from pyproject — it's a declared
    dep now, and `pytest` is installed for `tests/`.)
- **Trap:** the system Framework Python (`/Library/Frameworks/.../3.11`)
  has an old released wheel installed. If you launch with the wrong
  interpreter you silently run month-old code — endpoints 404 and the
  sidebar breaks. `ps` shows venv processes as the *Framework binary
  path* (venv shims exec the base interpreter), so you cannot tell from
  `ps` which env a server is using. When in doubt:
  `python -c "import avar2_studio; print(avar2_studio.__file__)"` —
  it must point into this repo's `src/`. Don't use the *version* to
  identify the env: the venv's editable-install metadata reports a stale
  devN — harmless but misleading.
- **Frontend deploy loop:** the served bundle is
  `src/avar2_studio/static/` (gitignored, CI-assembled for wheels).
  Locally after any frontend change:
  `cd frontend && npm run build && rsync -a --delete build/ ../src/avar2_studio/static/`
  — then just reload the browser (no server restart needed for
  frontend-only changes; backend changes need a server restart).
  **This rsync is easy to forget** — a stale static dir cost a
  debugging session once (the Space tab was "missing" because the
  served bundle was a month old). Check the mtimes first when the app
  seems to lack a feature you just wrote.
- Launch used throughout development:
  `.venv/bin/python -m avar2_studio <source> --port 5070` (default port
  is 5001). Blind launch (no source) opens the Load Font dropdown.
- **Don't screenshot/capture against a source inside the repo** — the
  server writes sidecars + `.avar2-studio/` next to it. Stage a copy in
  /tmp (plus its `-avar.csv`) and point the server there.

## 3. Architecture — what the docs don't cover

### Source-path model
- `ORIGINAL_PATH` = the user's real source file. `GLYPHS_PATH` = the
  **active build source**, which is promoted to the shadow copy
  (`<dir>/.avar2-studio/shadow/<name>`) **once any studio axis has at
  least one brace layer** (`server.py` ~832; a declared-but-empty axis
  builds from the original). Sidecars (`-avar.csv`, `-control.json`,
  `-transforms.json`) always resolve against `ORIGINAL_PATH` — resolving
  against the shadow was a bug class once (CSV written into the shadow
  dir); don't reintroduce it.
- Built-in examples are **staged** to `~/.avar2-studio/workspace/<id>/`
  on first load (edits persist; repo fixtures stay git-clean). Uploads
  go to `~/.avar2-studio/workspace/uploaded/`, which is **wiped on every
  new upload**.
- Outline preservation is "model β": drawn brace-layer outlines live
  **only in the shadow file** and survive regeneration by being read
  back from the previous shadow. **Deleting `.avar2-studio/` loses
  drawn outlines.** True model α (outlines captured into the sidecar)
  is designed but unbuilt — see secondary-parametric-axes.md.

### Build orchestration (server.py)
- `trigger_build()` coalesces: if a build is running, it sets
  `REBUILD_PENDING` and the running build re-runs afterward (bounded at
  3 passes). Never call `_run_build()` directly.
- Layer edits return in ~3ms; `schedule_shadow_rebuild()` debounces
  shadow regen + build by **1.2s** (`_REBUILD_DEBOUNCE_SECONDS`).
  `BACKGROUND_WORK` folds into `/api/health`'s `building` flag — the
  frontend's "rebuilding preview…" indicator hangs off that.
- **The 19× perf fix** (`control_axes.py`, `_interpolated_seed`):
  `copy.deepcopy(ref_paths, {id(layer): None})` — the memo entry stops
  deepcopy walking `GSPath.parent → GSLayer → GSGlyph → GSFont` and
  cloning the entire font per brace layer (was ~750ms *per layer*,
  ~18s per rebuild on 24 layers; now ~1ms). If a rebuild ever gets
  slow again, profile before touching anything: fontc itself compiles
  these fonts in **~0.09s**; regen dominates at ~0.9s.
- Layer mutations go through the **delta API**
  (`POST /api/control-axes/<tag>/layers/delta`, add/remove lists) under
  `SIDECAR_LOCK`. The whole-list `PUT /layers` still exists but the
  frontend no longer uses it for edits — whole-list replace from a
  stale client cache silently dropped layers (a real data-loss bug).
- **Axis-metadata ranges self-heal** (Aug 2026): traditional
  (non-parametric) axes seeded with the `-1000/1000` placeholder extent
  (or a degenerate `[0,0]`, as transform-injected axes like SPAC report)
  are re-derived on read — from the enabled transform's params, the
  GRAD registry extent, or the CSV column's own spread
  (`_repair_placeholder_ranges` / `_derive_traditional_range` in
  `server.py`). Hand-edited ranges are never clobbered; without this the
  placeholder leaked into real fvar.

### /api/glyph-coverage response contract
Classification: `kind` = `universal` (100% coverage) vs `scoped`;
`source` = `"source"` (derived from brace layers / alternate masters)
vs `"studio"` (sidecar-declared; overlay re-tags and overrides name,
range, and layers from the sidecar). Per scoped axis:
- `layers`: `[{glyph, location, location_user}]`.
  **`location` is DESIGN space** (what's authored in the source);
  **`location_user` is USER space** (design mapped back through the
  designspace axis `<map>`; equal for `.glyphs` and unmapped axes;
  **`null` when the map couldn't be confidently inverted** — round-trip
  + axis-bounds checks in `glyph_coverage.py`). Anything driving the
  *compiled* font (thumbnail `font-variation-settings`) or Fontra's
  location bar must use `location_user`; display/warnings use
  `location`. On `null`, the frontend opens the glyph *without* a
  location fragment — never navigate wrong.
- `min` / `default` / `max`: **design space** (map outputs considered,
  so non-monotonic maps can't invert the range). Frontend falls back to
  the built font's fvar via `allAxes` only when these are absent.
- `glyph_chars`: glyph name → character (from source unicode data).
  Thumbnails typeset *text*, so `"eight"` must render as `"8"`; no
  codepoint → no thumbnail.

### open-editor routing (the file-safety invariant)
`POST /api/control-axes/<tag>/open-editor` routes **per axis**, not by
shadow existence:
- **Studio axis** (in the sidecar) → requires the shadow (400 with
  "add coverage glyphs first" if absent); Fontra serves the `shadow/`
  dir. The user's original file is never touched by studio authoring —
  this is the product's core promise (README states it).
- **Source-derived axis** → Fontra serves the ORIGINAL file directly,
  as a **single-file root** (so sibling projects in the folder aren't
  exposed), and the response carries `editing_original: true`, which
  the drawer surfaces as "Editing your actual source file."
Getting this routing wrong is how a designer's outlines end up in a
file that regeneration deletes — it was found and fixed in review;
treat any change here as high-risk.

### Fontra integration
- Spawned as a subprocess via `avar2_studio._fontra_launch` (applies the
  `_fontra_patch` monkeypatch — makes fontra-glyphs keep studio brace
  layers editable — before Fontra reads anything).
- Reverse-proxied same-origin under `/fontra/*` (HTTP + websocket; the
  websocket leg is why `flask-sock` is a hard dep). Same-origin is what
  allows the focused-UI CSS injection into the iframe.
- **`FONTRA_PORT` is a single global (8001).** Two studio instances on
  one machine fight over it: each process tracks only *its own* Fontra
  child, so instance B can silently proxy to instance A's Fontra (wrong
  root → 403s). Fine for the normal single-instance case; known
  limitation otherwise.
- Fontra is restarted when the watched source file's mtime changes
  (shadow rewritten by a layer edit) so its backend doesn't serve a
  stale cache.
- `/api/coverage` (the structural design-space audit) depends on the
  Fontra subprocess — it 500s with "Fontra subprocess is not running"
  when Fontra isn't up, and the frontend silently shows no Coverage
  button. If the panel is "missing" in the full app, check Fontra first.

### Transforms internals (user-facing story is in README)
- Registry: built-ins + any `.py` subclassing `Transform` in
  `~/.avar2-studio/transforms/` (folder auto-created with a README
  template). Discovery is idempotent per process — new scripts appear
  on next launch.
- `_apply_transform_chain(vf_path)` is the **single chokepoint**,
  wrapping both `VARIABLE_FONT_PATH` promotion points (avar2 build and
  plain fallback) — a transform must never be applied anywhere else.
  At most one enabled transform may inject a given fvar axis tag
  (`injected_axis_tag` enforcement in `registry.set_active`).
- `gftools-gen-spac --out` is **buggy upstream** (treats the arg as a
  full path despite documented as a dir) — the uniform SPAC transform
  copies the font and runs `--inplace` instead. gen-spac numbers are
  **per-side**: ±N ≈ ±2N advance units.
- Width-aware SPAC (`builtin_spac_widthaware.py`) moves **only phantom
  points** (LSB/advance gvar deltas) — outlines never move. Composite
  subtleties, both learned the hard way: moving the *left* phantom
  shifts the whole component (so composites get left-phantom-only
  treatment), and `USE_MY_METRICS` composites inherit their base's
  delta and are skipped (injecting would double it).
- Transform-injected axes (SPAC) have no source master, so
  `get_axes` overlays the **built font's fvar** and appends unseen tags
  with `has_master_coverage: True` — that's why SPAC shows as a
  parametric slider at all.

### The static (GitHub Pages) app
The Pages demo is the same React frontend with a static provider
(`frontend/src/static-api.js`) swapped in when `/api/health` doesn't
answer (`selectApiMode`). All font work happens in a **Rust/wasm
crate** (`wasm/fontc-web`, built with wasm-pack into
`frontend/src/wasm/fontc-web/` — commit the pkg; a rebuild is
`wasm-pack build --target web --out-dir ../../frontend/src/wasm/fontc-web`
plus `rm` the pkg's `.gitignore`). The crate compiles `.glyphs`
in-browser (fontc) and does table surgery: `add_avar2`, `pin_corner`,
`clamp_out_of_range`, `apply_transforms` (SPAC), `apply_control_axes`,
`apply_grade`, `set_default_location`, `set_hidden_axes`, `regen_stat`,
`measure_at`. Details + the rationale for each port:
docs/migration-github-pages.md.
- **Rebuild pipeline** (`rebuildUploadFont`, shared by Rebuild and the
  transforms toggles): compile → avar2 → control axes → grade →
  SPAC transforms → corner pins → out-of-range drop. Applied state
  can't be un-baked, so changing the transform set means rebuilding
  from source with the new set — the same path keeps bundle-imported
  state (control axes, grade) alive across rebuilds.
- **Drop out-of-range sources** (`clamp_out_of_range`): stranded
  sources (braces/masters outside the axis box) are DROPPED — the
  Glyphs.app/fontmake semantics, which the divergence oracle proved
  (fontc alone extrapolated). Their tuples' packed deltas AND peaks are
  zeroed (zero deltas make the tuple inert; zeroed peaks make the
  peak-reading audit see the drop — zeroing a LIVE peak is the mangling
  case, default advance 166→2154) and HVAR is rebuilt. glyf/hmtx stay
  byte-identical (`tests/clamp_oracle.rs` asserts it).
- **Sessions** persist uploads in IndexedDB (`frontend/src/session.js`)
  — fontBytes plus all authoring state; restores skip the recompile.
  When testing, remember a prior session auto-restores (a "first visit"
  may not be one).
- **e2e** (`frontend/e2e/static-demo.spec.mjs`, 23 sections):
  `python3 -m http.server 8123 -d frontend/dist-pages` after
  `npx vite build --base=./ --outDir dist-pages`, then
  `node e2e/static-demo.spec.mjs`. Uses system Chrome via
  playwright-core. Downloads get verified with the venv's Python
  (fontTools/Pillow). Oracle tests for the crate are cargo
  (`cargo test` in `wasm/fontc-web`) with fontmake/gftools/Pillow as
  the references.
- **Snapshot datasets** (the bundled examples on Pages) are read-only:
  transforms toggle between two baked states; uploads get the real
  pipeline.

### Frontend patterns worth knowing
- **AVAR2 MAPPINGS sliders** (`Sidebar.js`): optimistic drafts keyed
  `"<instance>:<axisColumn>"`; commits debounce 450ms, **flush on
  pointer-up and on unmount** (a cleared-not-flushed timer silently
  lost edits once), and all commits run through **one serialized
  promise chain** — the server rewrites the whole CSV per commit and
  has an external-edit mtime guard, so interleaved commits either race
  or get rejected. Don't "simplify" the chain away.
- Sample-text width fitting polls `/api/text-width` continuously; the
  log is dominated by it, that's normal.
- Classic bug fixed twice, remember it: `input.files` is a **live**
  FileList — copy it (`Array.from`) *before* clearing `input.value`.
- Driving the UI from scripts: synthetic `input`-event dispatches leave
  the mapped-location reflection one step behind (the request fires
  with pre-change state); real `page.mouse` drags don't. Not a product
  bug — a harness trap that cost a false bug report.

## 4. Known issues / sharp edges (ranked)

1. **Chrome doesn't apply avar2 in its text pipeline** (verified on
   Chrome 151, Aug 2026): the in-browser preview specimen follows the
   fvar axes only — user-axis moves show in the *sliders* (computed
   reflection) but not the glyphs. The exported font's avar2 is valid
   (HarfBuzz applies it: ink darkness 338→1238, advance 178→3521 at a
   mapped corner; WebKit/CoreText also implement avar2). Nothing to
   fix in-repo — watch Chrome's support before claiming "what you see
   is what ships" again.
2. **The server's avar2 reflection evaluator understates the table.**
   `/api/mapped-location` at a mapped corner returns too little (XTRA
   unchanged, XOPQ/YOPQ ~90% of the CSV row's values) while HarfBuzz on
   the same font produces the full effect — the Python evaluator is the
   outlier, not the table. Users see the understatement as sliders not
   going all the way.
3. ~~`avar2-eval.js` (static) parses only wasm-written avar2 tables~~
   **Superseded (Aug 18):** the parser was rewritten to the spec layout
   (offset fields, entryFormat-aware DSIM, OT tent semantics) after it
   turned out to parse NOTHING correctly — reflection had been silently
   dead (see debugging-2026-08-18.md §4). It now has a fontTools oracle
   test (`e2e/avar2-eval.spec.mjs`). Whether it handles the
   server-written layout has not been re-verified — test before wiring
   a "load a server-built font into the static app" path.
4. **Shadow wipe loses drawn outlines** (model β, §3) — biggest
   data-integrity foot-gun for users.
5. **Fontra port 8001 collision** across simultaneous instances (§3);
   `/api/coverage` 500s silently when Fontra is down (§3).
6. `/api/glyph-coverage` re-parses the source (and re-reads every glif
   for `.designspace`) on **every request** — fine at fixture scale,
   linear cost on production fonts. Memoize on `(path, mtime)` when it
   starts to hurt.
7. `.glyphspackage` (Glyphs 3 folder format) can't be uploaded via the
   file picker — browsers can't post directories; needs zip upload or a
   server-side path field.
8. `.designspace` **authoring** is read-only: coverage + layers panel +
   Fontra-on-original work, but declaring studio axes / brace-layer
   authoring is `.glyphs`-only (roadmapped). The static app can't
   compile UFO sources at all (designspace zips load the baked preview
   TTF).
9. Naming seam: UI says "secondary parametric axes"; code, API routes,
   CSS, sidecar, and docs keep "control axes" (anchor comment at the
   top of `ControlAxes.js`).
10. CrispyMini CSV trivia: the two `Ultra Wide Thin` rows have **blank
    wght cells** (resolve to the wght default 400, not 100 like the
    Narrow Thin rows) — looks unintended, left as-is pending the
    designer's call. (Their blank *opsz* cells were filled in Aug 2026:
    they used to collide rows onto shared avar2 corners, dropping
    `Ultra Wide Heavy 144` from the table.)

## 5. Verification playbook

```bash
.venv/bin/python -m pytest tests/ -q            # 41 server tests
cd wasm/fontc-web && cargo test                 # crate oracles (avar2/braces/clamp/pin/spac/stat/measure)
cd frontend && npx vite build --base=./ --outDir dist-pages
python3 -m http.server 8123 -d frontend/dist-pages &
cd frontend && node e2e/static-demo.spec.mjs    # full suite against :8123 (green end-to-end since Aug 18)
cd frontend && node e2e/avar2-lint.spec.mjs     # mapping lint (pure JS, no server)
cd frontend && node e2e/grade-model.spec.mjs    # grade cap model vs the Python oracle
cd frontend && node e2e/avar2-eval.spec.mjs     # avar2 evaluator vs fontTools (needs /tmp fixture)

.venv/bin/python -m avar2_studio examples/crispy-mini/sources/CrispyMini.glyphs --port 5070
curl -s localhost:5070/api/health            # status ok, font_built true, building false
curl -s localhost:5070/api/glyph-coverage    # axes with layers/min/default/max/glyph_chars
curl -s -X POST localhost:5070/api/control-axes/<studio-tag>/open-editor
                                             # editing_original false, project = shadow file
```
The cargo oracles need `/tmp/fontc-wasm-spike.ttf` and
`/tmp/av2-oracle.ttf` — regeneration recipe in
[debugging-2026-08-18.md](./debugging-2026-08-18.md) (fontc from the
Crispy design repo's venv + `spike/build_oracle.py`). `clamp_oracle`
compiles the LIVE `../Crispy/sources/Crispy.glyphs` and fails vacuously
once that design file no longer carries stranded sources —
environmental, not a code failure.

Repeat with `examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace`:
scoped axes must show the read-only layers panel with digit/letter
thumbnails, and open-editor must return `editing_original: true` with
the `.designspace` as project. In the UI: add a brace layer on a studio
axis — the save is instant, "rebuilding preview…" appears, and the
preview catches up in a few seconds. Healthy perf: shadow regen ~1s,
fontc ~0.09s, startup ~1-2s.

## 6. Suggested first week

1. Tag a release (`pyproject` is at dev8; the latest release is dev6
   and predates control axes, the Preview tab, transforms, grade, the
   whole static app).
2. Fix the server's avar2 reflection evaluator (§4.2) — or route the
   full app through the wasm/JS evaluator that already agrees with
   HarfBuzz.
3. Then the roadmap in README (PyPI, `.designspace` authoring,
   push-to-source sync) in whatever order the designer needs.
