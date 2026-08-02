# Migration plan: avar2-studio on GitHub Pages (static, server-independent)

Status: planning reference — written before the `github-pages` branch work began.
Sources are cited inline; unverified items are flagged as such.

## Goal

Deliver avar2-studio as a fully static web app on GitHub Pages: lightning
fast, no server component. All font processing runs client-side
(WASM/JS) or is precomputed in CI. The Flask/Docker app remains the
full-featured desktop and hosted-demo path during the transition.

Reference codebases: [fontations](https://github.com/googlefonts/fontations)
(+ [fontc](https://github.com/googlefonts/fontc)) and
[fontspector](https://github.com/fonttools/fontspector).

## How fontspector does it (the deployment template)

Fontspector is a pure-Rust workspace (~15 crates): a framework crate
(`fontspector-checkapi`), one crate per QA profile, a thin CLI, and
`fontspector-web` — an official WASM build that statically links the
*same* profile crates for the browser. The production app at
<https://fonttools.github.io/fontspector/> is exactly our target model.
Patterns to copy:

1. **Single source of truth** — identical code compiles native and wasm;
   platform differences isolated to `cfg(wasm)` dependency gates
   (network access is desktop-only by construction).
2. **Thin JSON boundary at the WASM edge** — font bytes in
   (`{filename: Uint8Array}`), JSON results out; frontend-agnostic.
3. **Compute in a Web Worker** — the UI never stalls
   (`fontspector-web/www/src/webworker.ts`).
4. **Size discipline** — `wasm-opt -Oz`, `+bulk-memory`.
5. **Pages workflow ≈ 40 lines** — `wasm-pack build` → `vite build` →
   `actions/upload-pages-artifact` + `actions/deploy-pages` on every push
   to main (`.github/workflows/pages.yml` in that repo).
6. **Deliberately reduced web feature set** — no subprocess plugins, no
   network checks — rather than forced parity.

Cautionary precedent: FontBakery's browser attempt was Pyodide-based and
known-incomplete; fontspector replaced it with native Rust→WASM. Treat
Pyodide as a fallback, not the spine.

## Per-stage feasibility (our pipeline, from the `/api` surface)

| Stage | Browser-viable? | Path |
|---|---|---|
| `.glyphs` → VF build (`/build`) | ✅ proven in production | fontc is a library: `generate_font() -> Vec<u8>`, in-memory `.glyphs` input (`Input::GlyphsMemory`), single-threaded `default-features = false` (no rayon), wasm32 in upstream CI. [Counterpunch](https://github.com/counterpunchspace/editor) ships this (~8 MiB wasm); older reference: [fontc-web](https://github.com/simoncozens/fontc-web). |
| avar2 generation (`/build-avar2`) | ⚠️ viable, not off-the-shelf | fontc has no avar2 support ([fontc#2008](https://github.com/googlefonts/fontc/issues/2008)). Port gftools' `gen_avar2` (~150 lines of fontTools) via Pyodide, or port the Rust avar2 builder from [babelfont-rs](https://github.com/simoncozens/babelfont-rs/blob/main/babelfont/src/convertors/fontir/avar2.rs) into our blob. |
| Width measurement (`/text-width`) | ✅ mature | [harfbuzzjs](https://github.com/harfbuzz/harfbuzzjs): `setVariations()` + shaped `xAdvance`. **Unverified:** its HB_TINY build may strip avar2-aware measurement — check `config-override.h` before relying on it. |
| Preview rendering | ✅ already server-free | Browsers apply variable fonts natively via `font-variation-settings`; the preview only needs built font bytes. |
| `/mapped-location` | ✅ easy | avar2 segment evaluation is small math; JS/Rust port. |
| Instance/config/CSV authoring | ✅ easy | JSON/CSV manipulation in JS; state → localStorage / File System Access API. |
| Source surgery (instance CRUD → `.glyphs`, grade shadow layers, control-axis braces) | ⚠️ hard part | Needs a Rust `.glyphs` writer (babelfont-rs reads/writes) or a format change. |
| Transforms (SPAC injection), `/export-font` default rebuild | ⚠️ table surgery | fontTools under Pyodide (fontTools is a prebuilt Pyodide package) or Rust write-fonts. |
| Full fontmake/ufo2ft path | ❌ dead end | C-extension chain (cffsubr, pyclipper, compreffor); no Pyodide precedent. fontc replaces this stack. |

## Phased plan

- **Phase 1 — static-read studio.** Frontend on Pages; CI pre-builds the
  example fonts into static artifacts (fontc runs natively in Actions).
  Preview, mapped-location, text-width (harfbuzzjs), instance/CSV
  authoring, config export/import — all client-side. No wasm build yet;
  tiny payload. *This branch starts here.*
- **Phase 2 — in-browser builds.** One Rust wasm blob: fontc (per the
  Counterpunch recipe) + an avar2 step ported from babelfont-rs, in a
  Web Worker, lazy-loaded when a user uploads a source. The full studio
  loop with zero backend.
- **Phase 3 — parity ports, deliberately.** SPAC / grade /
  export-rebuild each get a port-or-defer decision (fontspector's
  reduced-web-profile precedent).

## Open verifications (before Phase 2 commits us)

- harfbuzzjs avar2 support in its HB_TINY build config.
- Real compressed wasm payload size (Counterpunch's is 8.38 MiB raw).
- babelfont-rs avar2 builder fidelity vs gftools' (recall our
  dup-axis shim history — `src/avar2_studio/build/_shims/`).
- `.glyphs` writeback strategy for instance CRUD.
- Whether SPAC/grade ports justify Rust, or a Pyodide+fontTools island
  is acceptable for those paths only.

## Phase 3 decision record

Resolved as the phases landed:

- **avar2 generation — PORT.** avar v2 is VarStore-based
  (DeltaSetIndexMap + ItemVariationStore, not the old segment-map
  format). Build side: `add_avar2(font_bytes, csv)` in the fontc-web
  wasm crate using fontc's own machinery (fontdrasil VariationModel +
  write-fonts ivs_builder — the same code fontc uses for HVAR), verified
  structurally against gftools-gen-avar2 as oracle. Eval side (the
  Preview tab's mapped-location reflection): `frontend/src/avar2-eval.js`,
  a pure-JS VarStore evaluator.
- **harfbuzzjs — NOT NEEDED.** The width chips come from instance data;
  no shaping happens client-side. The HB_TINY/avar2 question is moot.
- **SPAC on uploaded fonts — DEFER.** It's per-glyph hmtx + advance
  surgery; revisit if the static demo gains per-glyph editing. The
  bundled demo is covered by the baked spac-on/off variants.
- **Grade — PORT.** The pure-weight model (grade.py: XOPQ+YOPQ drive,
  XTRA follows at COMP_RATIO) needs no contour offsetting: each brace
  is the glyph's own outline instanced at the light/dark grade coords,
  advance-equalised by a symmetric shift (grade_shadow.py's algorithm).
  `apply_grade(font_bytes, grade_json, instance_coords_json)` in the
  fontc-web wasm crate injects them as gvar tuples at GRAD ∓10 plus the
  GRAD fvar axis; verified against fontTools' instancer as oracle.
- **Control axes (secondary parametric axes) — PORT (computed
  braces).** `apply_control_axes(font_bytes, control_json)`: each
  `{glyph, location}` layer becomes an fvar axis + a gvar tuple whose
  delta is the glyph's instanced outline at the pinned location minus
  the default instance's — the brace effect without a shadow source.
  Designer-drawn brace outlines stay a full-app feature (the static
  port computes them).
- **export-font (hidden axes / default-location rebuild) — PORT.**
  `set_default_location` (fvar defaults move to the user's location,
  parametric defaults to the avar2-eval'd mapped location, avar2
  regenerated around the new origin) and `set_hidden_axes` (fvar flags
  bit 0x0001) in the wasm crate; options in the Preview tab's export
  modal.
- **STAT regeneration — PORT.** `regen_stat(font_bytes)` in the wasm
  crate: a hand-port of gftools `axisregistry.build_stat` (the
  google-fonts-axisregistry crate deviates from the Python oracle, so
  no crate reuse), registry data for 53 GF axes generated into
  `stat_registry.rs`, zero structural diffs against real
  `axisregistry.build_stat(font, [])`. Runs on every export.

## Coverage audit (phases 1–2)

Structural + behavioral design-space coverage, found at upload time
and listed in the Header's Coverage panel (click a finding → preview
jumps to the location):

- **Layer A (structural, `frontend/src/gvar.js` + `coverage.js`)**:
  reads every glyph's gvar tuple regions and reports axis-extreme
  corners no source's tent reaches (with per-axis edge coverage) and
  sources outside the axis box (out-of-range braces/masters). These
  never error at build time — they only fail in axis usage.
- **Layer B (behavioral, `measure_at` in the wasm crate)**:
  skrifa-draws probe glyphs at sweep locations and integrates filled
  outline area as a stem-darkness proxy (canvas 2D
  `fontVariationSettings` proved unreliable in Chrome). Sweeps per
  axis (at default, and with each other axis pinned at min/max)
  flag **collapses** (weight rises then dies below half its peak —
  the extrapolation-collapse signature) and **inert** default sweeps
  (weight needs more than one axis). Verified against Pillow stem
  darkness in `tests/measure_oracle.rs` (behavior agreement, not
  value equality).

## Zip workspace format

Project zips travel whole projects between the hosted tool and the full
app — the static side mirrors the server's `_load_project_zip` contract
(`frontend/src/zip-workspace.js`). One archive carries:

```
<stem>.glyphs                          # or:
<stem>.designspace + *.ufo/ dirs       # designspace projects
<stem>-avar.csv                        # avar2 mappings (edited state)
<stem>-control.json                    # secondary parametric axes (if any)
<stem>-transforms.json                 # transforms (if any)
.avar2-studio/axis-metadata.json       # axis ranges/display names (if any)
.avar2-studio/build/<stem>-VF.ttf      # current build (preview)
```

Import (Load Font → Upload): exactly one source per archive; the
sidecars are harvested from beside it; harvested control axes /
transforms apply through the same bundle machinery as JSON config
imports. `.designspace` projects load from the baked preview TTF —
fontc compiles UFO sources from the filesystem only, so the browser
can't compile them (no in-memory input upstream); the preview build is
required in the zip and the error says so when it's missing. Rebuild is
glyphs-only for the same reason. Everything after the initial compile
(avar2 regeneration, export options, STAT) already runs off font bytes,
so nothing else is lost.

Export (Config → Download workspace (.zip)): sources verbatim, sidecars
re-emitted from live state (the CSV is the authoring source of truth),
current font bytes as the preview build. A zip we export loads cleanly
in the full app, and a zip zipped from a real project folder loads in
the hosted tool.

## Session persistence

Uploaded sessions auto-restore across reloads (`frontend/src/session.js`
+ the persist/restore path in `static-api.js`). The live dataset's
serializable state — source text (glyphs) or zip entries (designspace),
current font bytes, authoring CSV, sidecars, axis metadata — is written
to one IndexedDB record: immediately on upload, debounced 500ms after
each authoring mutation. Boot restores from it WITHOUT recompiling (the
stored font bytes already carry the avar2 table), so resume is instant.
Snapshots are never persisted; switching to an example clears the
record, and Load Font → "Forget this project" clears it AND unloads
back to the example. Persistence is best-effort (quota failures warn,
editing continues; version mismatch or a corrupt record wipes and boots
fresh). UI state (tab, preview text, slider positions) is deliberately
not persisted, and a multi-project "recent" list stays future work.

