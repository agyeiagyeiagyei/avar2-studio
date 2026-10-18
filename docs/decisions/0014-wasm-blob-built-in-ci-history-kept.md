# 0014 — The wasm binary is built, not committed; history is kept

- **Status:** Accepted (user decision, 2026-10-17 / 2026-10-19)
- **Date:** 2026-10-19
- **Area:** `frontend/src/wasm/fontc-web/`, `.github/workflows/pages.yml`, `.github/workflows/release.yml`

## Context

`fontc_web_bg.wasm` (7.45 MB) was committed on every rebuild — 25 revisions,
roughly two thirds of the repository history by size — and the committed copy
lagged the Rust sources (audit §1). The Pages demo and the wheel's static mode
consumed it unrebuilt.

## Decision

- **History stays.** No `filter-repo`, no force-push, no LFS migration of past
  revisions: clones keep the old blobs; only the growth stops.
- **Going forward the binary is a build output.** It is ignored and untracked;
  `pages.yml` and both frontend-bundle jobs in `release.yml` build it with
  `wasm-pack` (rust toolchain + wasm32 target + rust-cache) right before the
  frontend build. The small wasm-bindgen JS glue stays tracked so the frontend
  builds and tests without a Rust toolchain — `fontc-worker.js` imports the
  glue statically and the binary is resolved at runtime (Vite only warns when
  it is absent).
- Locally, static mode needs one `wasm-pack build --release --target web
  --out-dir ../../frontend/src/wasm/fontc-web` from `wasm/fontc-web/`
  (docs/DEVELOPMENT.md). A newer wasm-bindgen regenerates a slightly different
  glue; commit the regenerated glue when it changes.

## Consequences

Clones stop growing by ~3 MB per wasm rebuild; the Pages demo always ships the
engine matching the Rust sources; the Pages/release workflows gain a Rust step
(cold ≈10+ min, warm-cached afterwards).
