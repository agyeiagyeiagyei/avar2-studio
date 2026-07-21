# Handover — project state & undocumented knowledge (July 2026)

This doc captures everything a new developer needs that is **not**
already written down. Read the existing docs first — this deliberately
doesn't repeat them:

- [README.md](../README.md) — what the tool is, install, launch,
  working-tree layout, axis surface, Preview tab, transforms (user-facing)
- [docs/authoring-instances.md](./authoring-instances.md) — the
  instance/mapping authoring workflow, both example fixtures
- [docs/secondary-parametric-axes.md](./secondary-parametric-axes.md) — secondary parametric
  ("control") axes: design + implementation, tagged SHIPPED/DESIGN-ONLY

Everything below is tribal knowledge as of this handover.

---

## 1. Repo state at handover

- **Active branch: `glyph-scoped-axes`** — all recent work lands here
  first. At handover it has **unpushed commits ahead of
  `origin/glyph-scoped-axes` and of `main`** (mapping sliders + rename +
  source-derived layers panel; axis-map inversion; glyph-name thumbnails;
  two upload fixes; this doc).
  Nothing is lost if you just push the branch and fast-forward main —
  that has been the merge pattern throughout (no PRs, ff-only).
- Other branches: `grade-comparison` (parked WIP — grade-master
  comparison sidebar using uharfbuzz advances), `designspace-support` /
  `collapse-helper-scripts` / `example-roboto-delta-demo` (historical,
  already merged or superseded).
- **Releases:** latest GitHub release is `v0.1.0.dev6`; `pyproject.toml`
  says `0.1.0.dev7` (never tagged). Pushing a `v0.1.0.devN` tag triggers
  `.github/workflows/release.yml`, which builds the React bundle,
  assembles the wheel (bundle force-included at
  `src/avar2_studio/static/`), and attaches it to a GitHub Release.
  Not on PyPI yet.
- **The Crispy repo** (`~/Documents/Crispy`) is the parent project this
  tool was extracted from. Its `preview-app/` is the **legacy
  predecessor — do not develop there**; avar2-studio is canonical. The
  Crispy repo currently has a dirty working tree (legacy preview-app
  edits, plus an untracked `sources/.avar2-studio/` workdir and
  `sources/Crispy-avar.csv` from driving the real Crispy through this
  tool). Someone should triage/commit that separately.

## 2. Development environment — the traps

- **The repo `.venv` is the only correct environment.** It has
  avar2-studio installed **editable**, plus three packages that are NOT
  in `pyproject.toml`:
  - `flask-sock` — a **hard dependency** (`server.py` imports it at
    module level for the Fontra websocket proxy). **A fresh
    `pip install` of this repo crashes on launch** until it's added to
    `pyproject.toml`. Known gap; one-line fix, top of the list.
  - `fontra` and `fontra-glyphs` — soft deps, installed **from git**
    (neither is on PyPI under those names; see the docstring at
    `_ensure_fontra_running` in `server.py`). Only needed for the
    embedded outline editor; everything else works without them.
- **Trap:** the system Framework Python (`/Library/Frameworks/.../3.11`)
  has the released `dev6` wheel installed. If you launch with the wrong
  interpreter you silently run month-old code — endpoints 404 and the
  sidebar breaks. `ps` shows venv processes as the *Framework binary
  path* (venv shims exec the base interpreter), so you cannot tell from
  `ps` which env a server is using. When in doubt:
  `python -c "import avar2_studio; print(avar2_studio.__file__)"` —
  it must point into this repo's `src/`. Don't use the *version* to
  identify the env: the venv's editable-install metadata still reports
  `0.1.0.dev5` even though `pyproject.toml` says `dev7` — stale
  dist-info, harmless but misleading.
- **Frontend deploy loop:** the served bundle is
  `src/avar2_studio/static/` (gitignored, CI-assembled for wheels).
  Locally after any frontend change:
  `cd frontend && npm run build && rsync -a --delete build/ ../src/avar2_studio/static/`
  — then just reload the browser (no server restart needed for
  frontend-only changes; backend changes need a server restart).
- Launch used throughout development:
  `.venv/bin/python -m avar2_studio <source> --port 5070` (default port
  is 5001). Blind launch (no source) opens the Load Font dropdown.

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

## 4. Known issues / sharp edges (ranked)

1. **No test suite.** `tests/` holds only an (empty) fixtures dir;
   pytest isn't installed in the venv. All verification to date has
   been endpoint-level smoke tests + adversarial code review. The
   highest-value first tests: `glyph_coverage.compute_coverage` on both
   fixtures (ranges, layers, `location_user` incl. the non-invertible
   case), the open-editor routing matrix, and the transforms conflict
   guard.
2. **`flask-sock` missing from pyproject deps** — fresh installs crash
   (see §2).
3. **Shadow wipe loses drawn outlines** (model β, §3) — biggest
   data-integrity foot-gun for users.
4. **Fontra port 8001 collision** across simultaneous instances (§3).
5. `/api/glyph-coverage` re-parses the source (and re-reads every glif
   for `.designspace`) on **every request** — fine at fixture scale,
   linear cost on production fonts. Memoize on `(path, mtime)` when it
   starts to hurt.
6. `.glyphspackage` (Glyphs 3 folder format) can't be uploaded via the
   file picker — browsers can't post directories; needs zip upload or a
   server-side path field.
7. `.designspace` **authoring** is read-only: coverage + layers panel +
   Fontra-on-original work, but declaring studio axes / brace-layer
   authoring is `.glyphs`-only (roadmapped).
8. Naming seam: UI says "secondary parametric axes"; code, API routes,
   CSS, sidecar, and docs keep "control axes" (anchor comment at the
   top of `ControlAxes.js`).

## 5. Smoke-test playbook (no test suite, so this is the bar)

```bash
.venv/bin/python -m avar2_studio examples/crispy-mini/sources/CrispyMini.glyphs --port 5070
curl -s localhost:5070/api/health            # status ok, font_built true, building false
curl -s localhost:5070/api/glyph-coverage    # axes with layers/min/default/max/glyph_chars
curl -s -X POST localhost:5070/api/control-axes/<studio-tag>/open-editor
                                             # editing_original false, project = shadow file
```
Repeat with `examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace`:
scoped axes must show the read-only layers panel with digit/letter
thumbnails, and open-editor must return `editing_original: true` with
the `.designspace` as project. In the UI: add a brace layer on a studio
axis — the save is instant, "rebuilding preview…" appears, and the
preview catches up in a few seconds. Healthy perf: shadow regen ~1s,
fontc ~0.09s, startup ~1-2s.

## 6. Suggested first week

1. Push the pending commits on `glyph-scoped-axes`; fast-forward main.
2. Add `flask-sock` to pyproject deps; decide how to document the
   fontra/fontra-glyphs git installs (extras? doctor check exists).
3. Stand up pytest + the three test families from §4.1.
4. Tag `v0.1.0.dev8` so a released wheel finally contains control
   axes, the Preview tab, and transforms (README currently warns the
   released `dev6` predates them).
5. Then the roadmap in README (PyPI, `.designspace` authoring,
   push-to-source sync) in whatever order the designer needs.
