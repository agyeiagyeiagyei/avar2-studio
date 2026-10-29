# Decision records

One file per decision, numbered, dated, with a status. Each states the
context, the decision, its consequences and where it lives in the code
(symbols and tests, never line numbers), and carries the measurements
that justified it so the code comments can shrink to a pointer
(`See docs/decisions/NNNN`). Numbers are stable; a reversed decision
gets a new record that supersedes the old one rather than an edit.

| # | Decision | Status | Date |
|---|---|---|---|
| [0001](0001-build-time-default-relocation.md) | Build-time default relocation is ON (zero-anchored tents → the valley when off) | Accepted | 2026-10-04 / 2026-10-11 |
| [0002](0002-ttf-preview-background-woff2.md) | TTF preview; WOFF2 compressed in the background, sizes shown before download | Accepted | 2026-10-11 |
| [0003](0003-worker-subprocess-finish-then-rebuild.md) | Builds in a worker subprocess; finish-then-rebuild, not cancel-and-restart | Accepted | 2026-10-13 |
| [0004](0004-kinetikern-font-stage-phantom-tuples.md) | Kinetikern corrections as font-stage phantom-point tuples, before SPAC | Accepted | 2026-10-15 |
| [0005](0005-rond-axis-per-style-percent-rendered-fill.md) | Rounding is a ROND axis with per-style %; corners classified by the rendered fill | Accepted | 2026-10-02 / 2026-10-11 |
| [0006](0006-slant-name-exemption-italic-doubling.md) | "Italic"/"Slanted" styles are exempt from the slant stamp; gftools-style doubling | Accepted | 2026-10-10 |
| [0007](0007-export-set-default-relocation-direction.md) | Export "set default": inserted-master method rejected; lossless relocation to a parameterised default is the direction | Accepted direction, not built (T3.8) | 2026-10-16 |
| [0008](0008-per-instance-control-values-in-sidecar.md) | Per-instance control-axis values in the control sidecar, never CSV columns | Accepted | 2026-10-11 |
| [0009](0009-drop-out-of-range-sources.md) | Out-of-range sources are dropped (Glyphs.app / fontmake semantics) | Accepted; static-only action | 2026-08-03 |
| [0010](0010-fontra-embed-gpl-subproject.md) | The browser editor is fontra-embed, a separate GPL sub-project over postMessage | Accepted | 2026-09-30 |
| [0011](0011-ymod-baked-corrections.md) | Baked corrections: a correction axis rests at its corrected end (dead axis by design) | Accepted | 2026-10-11 |
| [0012](0012-foundry-tags-uppercase-registered-lowercase.md) | Authored tag case preserved: foundry tags uppercase, registered lowercase | Accepted | 2026-10-16 |
| [0013](0013-designspace-authoring-501.md) | Secondary-axis authoring is .glyphs-only; designspace sources get a clean 501 | Accepted | 2026-10-16 |
| [0014](0014-wasm-blob-built-in-ci-history-kept.md) | The wasm binary is a CI build output; repository history is kept intact | Accepted | 2026-10-19 |
| [0015](0015-native-accelerators-ship-in-platform-wheels.md) | `spac-inject` and `libkinetikern2` ship inside the platform wheels; env → wheel → repo build | Accepted | 2026-10-18 |

## Not yet recorded

Three decisions exist only in conversation and have no written source
anywhere in the repository; they are listed here so they are either
written down by their owner or consciously dropped: the parked **ROND
"white-room cap"**, the **collision badge**, and the **"Def as a real
master" shelf**. Do not infer their terms from the code.

## Writing one

Copy the shape of any record above. Keep "Context" to the facts that
made the decision necessary, keep every number (what was measured, on
which font, when), and end with the symbols and tests that implement it.
The code-comment policy is in [`../DEVELOPMENT.md`](../DEVELOPMENT.md#code-comments-and-where-history-goes).
