# Kinetikern — per-style spacing

Space a named style with [Kinetikern2](https://github.com/bghryct/kinetikern2)'s
engine (Mirko Velimirovic, MIT): Pass 1 of its kinetic equilibrium over glyph
triplets, with its punctuation and figures conventions on, run on the **built
font at that style**. The result is stored as per-glyph side deltas and baked into
every build as small variation tuples, so each style you space stays spaced while
the masters keep interpolating underneath. Kerning is not part of this pass. The
reasons behind the mechanism are in
[decision 0004](../decisions/0004-kinetikern-font-stage-phantom-tuples.md).

## Setting up

The engine is a Rust library the studio loads through ctypes. Installed wheels
carry it; from a checkout build it once:

```bash
cd native/kinetikern2 && MACOSX_DEPLOYMENT_TARGET=11.0 cargo build --release
```

Discovery order is `$AVAR2_KK2_DYLIB`, then the wheel-carried
`avar2_studio/bin/libkinetikern2.*`, then the repo build
([decision 0015](../decisions/0015-native-accelerators-ship-in-platform-wheels.md)).
Rust 1.74+. `avar2-studio doctor` reports which one it found; without any of them
the tab says the engine is not built and everything else keeps working. The tab
is desktop-only — the browser demo hides it.

## The tab

**Kinetikern** sits beside Instances and Preview and lists every style behind a
labelled dropdown (`<style> · spaced −0.60`, or `Custom location`). A **Font
Size** control above the two panes follows the picked style's `opsz`, clamped to
9–144. Pick a style, set the **looseness** (−1 tight … +1 loose; 0 is the
engine's calibrated middle), and compare the built font (before) with the
proposal (after) over the shared sample text.

The sliders work as on the Preview tab: user axes drive the parametric location
through the avar2 mapping, a dragged parametric slider overrides it; the engine
always spaces the **parametric** location with every other axis at its default,
so the secondary and transform axes only change how the panes render.

- **Apply** stores the proposal for that style and rebuilds.
- **Revert** drops it and rebuilds; the button names the style it will revert.
- **Apply −0.60 to all…** (the button carries the slider's value) spaces every
  style that has parametric coordinates at that one looseness in one rebuild,
  which keeps the family consistent. All means all: it **replaces each style's
  own looseness**, and the confirm names the styles it will re-space and the
  value they will become. The per-glyph deltas are still recomputed per style.
- **Custom location** spaces any parametric location you name; the entry then
  carries that `location` instead of following a style, under the name you type
  into **Name for this location**.

### Where the state shows

- **Instances tab** — a spaced row carries a `K −0.60` badge; clicking it opens
  the two things you can do from there (open the style in the Kinetikern tab, or
  remove its entry). An unspaced row shows nothing.
- **Transforms menu** — a **Kinetikern** row switches the feature off for the
  whole family (`PUT /api/kinetikern {enabled}`). Entries are kept while it is
  off, and the row says so; while it is off the fonts carry no corrections and
  the row badges disappear, because the build ignores every entry.

## What a proposal is

`propose` instantiates the served variable font at the style's parametric
location with fontTools, feeds the glyf outlines of every encoded letter, figure,
punctuation mark and symbol to the engine, and runs Pass 1 only (`coupling = 0`,
`skip_pass2`). Sidebearings come from **hmtx, not glyf bounds**: a phantom-point
variation (a SPAC tuple, an earlier Kinetikern tuple) moves hmtx and leaves the
outline where it was, and the contours handed to the engine are shifted to where
they actually render. The answer lists, per glyph, the current and proposed
`lsb` / `rsb` / `advance` with an `applied` flag (`new` equals `cur` for a glyph
the engine leaves alone; the word space is never set by the engine), plus
`location`, `looseness_applied`, `upm`, and a few statistics
(`mean_lsb_delta_per_1000`, `mean_rsb_delta_per_1000`, `ms`, `engine`).

## What gets stored

`MyFont-kinetikern.json` holds, per corrected style, the looseness it was spaced
at and `{glyph: [left, right]}` whole-unit deltas **relative to what the masters
interpolate to at that style**, so a later recalibration carries the corrections
along. Deltas are keyed by the **source** glyph name (fontc compiles production
names: `euro` → `Euro`; the studio maps by code point both ways). Apply stores
`existing + (engine target − current build)`: once the build carries the entry,
re-applying changes nothing, so a double apply between builds self-corrects.
Schema in [SIDECARS.md](../SIDECARS.md#stem-kinetikernjson).

## Verifying

`tests/test_kinetikern.py` (skips without the engine),
`tests/test_kinetikern_inject.py` (Crispy Mini through fontc, so it needs fontc)
and `tests/test_kinetikern_enabled_route.py` (the family switch). On a real font:
apply to all, wait for the rebuild, then `propose` on several styles must report
a remaining mean of 0 and `|new − cur| ≤ 2`.

The bake order, the route table and the API semantics for Kinetikern are in
[ARCHITECTURE.md](../ARCHITECTURE.md) (pipeline order, `routes/kinetikern.py`).
