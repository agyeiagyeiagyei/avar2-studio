# Kinetikern — per-style spacing

Space a named style with [Kinetikern2](https://github.com/bghryct/kinetikern2)'s
engine (Mirko Velimirovic, MIT): Pass 1 of its kinetic equilibrium over
glyph triplets, with its punctuation and figures conventions on, run on
the **built font at that style**. The result is stored as per-glyph side
deltas and baked into every build as small variation tuples, so each
style you space stays spaced while the masters keep interpolating
underneath. Kerning is not part of this pass. The reasons behind the
mechanism are in [decision 0004](../decisions/0004-kinetikern-font-stage-phantom-tuples.md).

## Setting up

The engine is a Rust library the studio loads through ctypes. Build it
once from a checkout:

```bash
cd native/kinetikern2 && MACOSX_DEPLOYMENT_TARGET=11.0 cargo build --release
```

(Rust 1.74+; `AVAR2_KK2_DYLIB` points elsewhere if you keep the build
outside the repo; `avar2-studio doctor` reports whether it was found.)
Without it the tab reports that the engine is not built and everything
else keeps working. The tab is desktop-only — the browser demo hides it.

## The tab

**Kinetikern** sits beside Instances and Preview and lists every style
the way the plugin lists masters. Pick one, set the **looseness** (−1
tight … +1 loose; 0 is the engine's calibrated middle), and compare the
built font (before) with the proposal (after) over the shared sample
text. The sliders work as on the Preview tab: user axes drive the
parametric location through the avar2 mapping, a dragged parametric
slider overrides it; the engine always spaces the **parametric** location
with every other axis at its default — the secondary and transform axes
only change how the panes render.

- **Apply** stores the proposal for that style and rebuilds.
- **Revert** drops it and rebuilds.
- **Apply to all…** spaces every style that has parametric coordinates at
  one looseness in one go, which keeps the family consistent (one rebuild).
- **Custom…** spaces any parametric location you name; the entry then
  carries that `location` instead of following a style.

## What a proposal is

`propose` instantiates the served variable font at the style's
parametric location with fontTools (`instantiateVariableFont`), feeds the
glyf outlines of every encoded letter, figure, punctuation mark and
symbol to the engine, and runs Pass 1 only (`coupling = 0`,
`skip_pass2`). Sidebearings are read from **hmtx, not glyf bounds**: a
phantom-point variation (a SPAC tuple, an earlier Kinetikern tuple)
moves hmtx and leaves the outline where it was, and the contours handed
to the engine are shifted to where they actually render. The answer
lists, per glyph, the current and proposed `lsb` / `rsb` / `advance`
(`new` equals `cur` for a glyph the engine leaves alone; the word space
is never set by the engine), plus `location`, `upm`, the looseness and a
few statistics (`mean_lsb_delta_per_1000`, `mean_rsb_delta_per_1000`,
`ms`, `engine`).

## What gets stored

`MyFont-kinetikern.json` holds, per corrected style, the looseness it was
spaced at and `{glyph: [left, right]}` whole-unit deltas **relative to
what the masters interpolate to at that style** — so a later
recalibration of the masters carries the corrections along. Deltas are
keyed by the **source** glyph name (fontc compiles production names:
`euro` → `Euro`; the studio maps by code point both ways). Apply stores
`existing + (engine target − current build)`: once the build carries the
entry, re-applying changes nothing, so a double apply between builds
self-corrects on the next one. Schema in [SIDECARS.md](../SIDECARS.md#stem-kinetikernjson).

## How it reaches the font

The bake is a **font-stage step at the top of the transform chain** —
after the compile, the default relocation and avar2, **before SPAC**: one
gvar tuple per corrected style per glyph that moves phantom point 1 by
−left and point 2 by +right (outlines never move, composites stay rigid,
`USE_MY_METRICS` composites are skipped), then HVAR is rebuilt. Supports
come from a fontTools `VariationModel` over the corrected locations, so
every style is exact and the field blends between them; earlier Kinetikern
tuples are stripped first so a re-run lands in the same place. Corrections
also ride exports. The order matters for a measured reason: at the
injection point the worst Crispy glyph is at ≈44 KB of gvar tuple headers
and all 30 styles fit; after SPAC it is at ≈63 KB and only 18 would. An
overflow is reported as a transform error and the font builds without
corrections.

## Verifying

`tests/test_kinetikern.py` and `tests/test_kinetikern_inject.py` (CrispyMini
through fontc; they skip without fontc and the engine). On a real font:
apply to all, wait for the rebuild, then `propose` on several styles must
report a remaining mean of 0 and `|new − cur| ≤ 2`.

## Routes

`GET /api/kinetikern` (the sidecar), `GET /api/kinetikern/propose?instance=<name>|coordinates=<json>&looseness=<t>`,
`PUT /api/kinetikern/instances/<name>` (`{looseness, location?}`),
`DELETE /api/kinetikern/instances/<name>`, `PUT /api/kinetikern/all`
(`{looseness}`). 404 before the first build, 503 without the engine.
