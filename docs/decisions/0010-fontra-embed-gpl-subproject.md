# 0010 — The browser editor is fontra-embed, a separate GPL sub-project driven over postMessage

- **Status:** Accepted
- **Date:** 2026-09-30 (`95b0cdf`); desktop embedding (same-origin proxy) 2026-06-23
- **Area:** `frontend/src/editor-bridge.js`, `frontend/src/static-api.js`, the fontra-embed repository

## Context

avar2-studio is Apache-2.0; Fontra is GPL. On the desktop the outline
editor is Fontra's own server, spawned as a subprocess and reverse-proxied
same-origin so the studio can inject a focused-UI stylesheet and a
session shim (that was the second of four integration paths weighed in
the archived `design-notes.md`: 1 separate tab, **2 iframe + same-origin
proxy — shipped**, 3 avar2-studio as a Fontra view plug-in, 4 Fontra
backends as a library). The Pages demo has no Python backend at all, so
none of those paths works there, and vendoring Fontra's client into the
studio bundle would pull GPL code into an Apache project.

## Decision

- **fontra-embed** (separate GPL repository, deployed to its own Pages
  site) builds Fontra's client from pinned `src-js` with a custom
  postMessage entry and no WebSocket. Its focused UI (trimmed panels and
  tools, sources-list narrowing, metrics HUD) is a port of the desktop
  shim.
- The studio iframes it and plays Fontra's **FontHandler** role from
  `editor-bridge.js`: it answers the font RPC from an in-memory
  VariableGlyph model built by the wasm `glyph_model` (source-level) plus
  the control-axis sidecar, applies `editFinal` back into the sidecar as a
  layer's stored `outline`, and coalesces rebuilds (one in flight, one
  pending — `trigger_build`'s spirit).
- The wire protocol is **versioned (`v: 0`)** on every message under the
  `avar2-embed` key; a mismatch fails loudly instead of parsing the wrong
  shape. Messages and RPC methods are listed in `docs/ARCHITECTURE.md`.
- **Write guards live in the bridge**, not in the embed's hidden tools:
  masters are read-only (they belong to the source file), correction
  (`target`) layers are read-only (recomputed every build), structural
  edits (point/contour add/remove) are rejected so a drawn layer stays
  compatible with the masters — the same reason the desktop hides the pen
  and knife. Rejected edits are reverted with a `reloadData` push.
- Drawn layers are **source-level**: the same sidecar `outline` schema
  as the desktop, spliced by the wasm `compile_with_overlays`; computed
  layers stay on the bytes-level `apply_control_axes` path. The same
  sidecar builds the same outlines on the desktop (verified cross-mode).
- The embed URL is pinned (`embedEditorUrl()`: production = the
  fontra-embed Pages site, dev = `localhost:8099`), overridable with
  `VITE_FONTRA_EMBED_URL` at build time and `window.AVAR2_EMBED_URL` at
  runtime.

## Consequences

- Two shim implementations exist: the desktop's 420-line Python-string
  `_FONTRA_STUDIO_SHIM_JS` and fontra-embed's `focused-ui.js` /
  `metrics-hud.js`, which have drifted (the embed auto-enables editing on
  studio rows, adds a "→ computed" rule, lacks the source-name sort).
  Having the desktop proxy inject a built copy of the embed's code,
  parameterised by the same session config, is sprint item T3.4.
- Cross-repo contract bugs show up as HUD-level symptoms: the static HUD
  measured at the opening layer because `embed-font.js` did not forward
  `location` to `getReferenceMetrics` — fixed in fontra-embed on
  2026-10-16 (T1.16), not yet deployed at the time of writing.
- Persistence: the sidecar rides the IndexedDB session and the workspace
  zip; a header-only mappings CSV means "no mappings" on load; a two-tab
  edit lock runs over BroadcastChannel.

## Where it lives

- `frontend/src/editor-bridge.js` (header comment is the contract),
  `frontend/src/static-api.js` (glyph editor section),
  `frontend/src/components/FontraEditorModal.jsx` (`bridge` prop).
- `frontend/e2e/editor-bridge.spec.mjs` (serves dist-pages +
  fontra-embed/dist, system Chrome), `frontend/e2e/prod-smoke.mjs`.
- Desktop counterpart: `server.open_control_axis_in_editor`,
  `_ensure_fontra_running`, `_proxy_to_fontra`, `_FONTRA_STUDIO_SHIM_JS`,
  `_fontra_patch`.
