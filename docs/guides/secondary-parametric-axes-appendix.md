# Secondary parametric axes — walkthroughs and recipes

The companion to [secondary-parametric-axes.md](secondary-parametric-axes.md):
the step-by-step material, the failure recipes and the editor detail, kept out
of the reference so the reference can be read in one sitting. Terms are defined
there.

## A brace layer is an *alternate* outline at a *non-default* location

Every glyph already has one outline: the **master** (its normal drawing), which
lives at the axis **default**. A brace layer is an *additional* drawing pinned
at some *other* location on the axis — "here's how `e` should look when `crbr`
is all the way up."

Consequence: **you cannot place a brace layer at the axis default** of an axis
you mean to drive. The default is the master's territory; a layer there would
collide with it. That is why the *Add applicable glyphs* modal requires the
secondary-axis pin to be **non-default** — it is what makes the layer a brace
layer instead of a duplicate master. (The one deliberate exception is a *baked*
correction axis whose default sits on its corrected end — see
[decision 0011](../decisions/0011-ymod-baked-corrections.md).)

## What "good coverage" looks like depends on where the default sits

| Axis | Default sits… | Layers a glyph needs to be fully defined |
|---|---|---|
| `-100…100`, default `0` | in the **interior** | one reaching `-100` **and** one reaching `+100`. Master handles `0`. |
| `0…40`, default `0` | on the **min** (edge) | one reaching `40`. There is no travel below `0` — the master *is* the `0` end. |
| `0…40`, default `40` | on the **max** (edge) | one reaching `0`. Mirror of the above. |

For an **edge-default axis** a single layer at the far extreme fully defines the
glyph and clears the warning — the studio does **not** warn about a "missing
layer below the default" when the default is already at the bottom of the range.

## Fixing an extrapolation warning

**+ Add layer for `<glyph>`** at the axis extreme, from the glyph's tray: your
existing layer stays as an intermediate and the new one anchors the end of the
range. (To move an existing layer instead, edit its location — the delta API
carries the drawing across the move.)

## One location across every applicable glyph

Once an axis covers several glyphs, a new location usually needs to land on
*all* of them:

- **+ Add layer for all N glyphs** at the bottom of the axis row opens the add
  modal targeting the whole coverage set; one layer is created per covered
  glyph. Name extra glyphs in the field to widen coverage in the same step.
- **Add to all applicable glyphs** — the same toggle inside any add or
  duplicate modal.

A glyph that already has a layer at that exact location is left alone — the
sidecar de-duplicates on (glyph, location) — so spanning is safe to repeat.

## A worked correction axis

This is the mechanism for a glyph-scoped correction axis. Take a
lowercase correction axis `LCOR` (0…100, default 0). At the wide-ultra
corner the global axes drive XOPQ to 1462, which reads too heavy on the
lowercase. Add a layer for every lowercase glyph *at that corner* with
`LCOR` at 100 and the correction **as if XOPQ 1100**: at the corner with
`LCOR` up, each lowercase glyph renders as it would at XOPQ 1100 — stems
lighter — while every uncovered glyph (the capitals) is untouched. The
layer sits at the corner so it engages there; its influence fades toward
the neighbouring masters exactly like any brace layer, and an avar2
mapping column can ramp `LCOR` in on the rows that need it.

In the add modal, tick **Correction** and override only the axes that
should differ. The layer row shows the target as *as if XOPQ 1100 ·
computed*, and Fontra's source list names it "… → as if XOPQ1100".

## Pin the correction, or it leaks

A brace layer compiles to a gvar tuple whose peak is the layer's location in
*normalized* space, and **an axis whose normalized peak is 0 is omitted from the
tuple — which makes it unrestricted**. Normalized 0 means "sits at the axis
default", so an omitted axis no longer bounds the correction.

For a plain brace layer that is harmless. For a correction layer it is not: the
correction applies at *every* value of the omitted axis.

Crispy hits this squarely. Its default master is at XTRA 47, and the natural
correction corner (47 · 1462 · 275) is *also* at XTRA 47 — so XTRA drops out of
the tuple and the lowercase correction leaks across the whole width axis. (The
numbers are production Crispy's, read from its shipped `fvar` on 2026-11-05:
XTRA 47/47/1715, XOPQ 1/1/1462, YOPQ 1/1/275 — min/default/max.)

The fix is an **anchor**: a second layer for the same glyph, at the same
secondary-axis value, at a *different* value of the unpinned axis, with **no
correction**. Its delta is zero, so it changes nothing where it sits and bounds
the tuple everywhere else. For Crispy that is a plain layer at (XTRA 1715,
XOPQ 1462, YOPQ 275) alongside the corrected one at XTRA 47. The studio flags a
glyph whose correction is unpinned on a parametric axis with **⚠ unpinned
correction**, naming the axis and the anchor to add.

Rules:

- A target overrides **parametric** axes only; a target on the secondary axis
  itself is ignored.
- **Pin every parametric axis, or add an anchor.**
- Computed layers are **re-derived on every rebuild**, so changing the target
  takes effect. Fontra edits on such a layer are overwritten — remove the
  correction to hand-draw it instead.
- A target outside the master range extrapolates like any out-of-range
  coordinate.

### Corrections in the static (GitHub Pages) app

The static app builds control-axis geometry itself: a drawn brace layer is
spliced into the source before compiling, a layer without a drawing keeps a demo
approximation (engaging the axis morphs the glyph toward its own shape at that
location), and a correction layer uses the same delta model as the full app.
Corrections are also pinned automatically there — where the desktop app needs a
hand-authored anchor layer, the static build emits a companion tuple at the
unpinned axis's extreme carrying the negated delta. Details in
[ARCHITECTURE.md](../ARCHITECTURE.md).

## The shadow source file

For studio-declared secondary-axis authoring the studio works on a **shadow
source file**, never the original:

```
<source-dir>/
  Crispy.glyphs                       ← original; never written for secondary-axis work
  Crispy-control.json                 ← axes, layer locations, drawings, per-style values
  .avar2-studio/
    shadow/Crispy.glyphs              ← derived: original + sidecar axes + brace layers
    shadow/transformed/Crispy.glyphs  ← derived: after slant / round corners
    build/                            ← the preview font
```

Every action regenerates it from the original plus the sidecar: the original's
glyphs are read, the sidecar's layer locations become brace layers (a correction
layer's outline is recomputed, a drawn one is re-attached), grade braces are
added, and the result is what gets compiled. Drawings live in the sidecar, never
in the shadow, so the sidecar alone is a complete backup; **Re-seed** drops a
layer's drawing and re-derives it from the current source, reporting `reseeded`,
`blocked` (a layer still holds a drawing — 409 unless `force`), `computed`,
`clean` or `missing`. There is no undo once the sidecar is written. The sequence
in full is in [ARCHITECTURE.md](../ARCHITECTURE.md).

## Editing in Fontra

Brace-layer outlines are edited inline through an **embedded Fontra**, opened
from the ↗ button on any layer row, in a resizable **right-side drawer** trimmed
to a brace-edit surface — sidebars and drawing tools are hidden — that drops
straight into edit mode on the chosen glyph at the brace location. Fontra runs
same-origin behind the studio's own server (ARCHITECTURE §7). The drawer's one
button is **Done editing**, which closes it and rebuilds the preview from the
shadow; drawing, saving and the layer list are Fontra's own, inside the drawer.

**Multi-source batch editing.** In a studio-axis session the drawer exposes
Fontra's sources list, trimmed to *studio layers only*, so you can edit several
brace locations at once. Masters and source-derived layers are deliberately
neither shown nor editable — that is what stops shadow regeneration (which
rebuilds masters from the original) from ever discarding an edit. Seeded layers
are point-compatible by construction, and the drawing tools stay hidden to keep
the batch in sync.

**Reference measurements.** Drawing a horizontal correction means matching a
glyph's bars to the ones that already read correctly — E's to H's — at the same
designspace point. The HUD compares the edited glyph with one reference glyph in
three columns — **bar**, **stem** and **s/b** (sidebearing) — re-measured every
400 ms as you draw, and both rows are cut at the edited layer's exact
coordinates with the secondary axis off (the location is sent live by the shim so
a glyph with several layers on the axis measures at the one being edited). Type
the reference's glyph name into the field (its placeholder is `H`); clear it to
hide the reference row and the overlay. There is no advance column: the width is
what the s/b column is there to keep an eye on.

Saving in Fontra writes the shadow; the studio captures the drawing into the
sidecar and rebuilds. The browser demo's editor is the separate
[fontra-embed](https://github.com/agyeiagyeiagyei/fontra-embed) bundle — same
sidecar, same drawings
([decision 0010](../decisions/0010-fontra-embed-gpl-subproject.md)).
