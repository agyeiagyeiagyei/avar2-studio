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
