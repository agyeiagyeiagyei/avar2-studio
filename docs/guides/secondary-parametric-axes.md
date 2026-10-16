# Secondary parametric axes

The studio's surface for declaring axes that deform only some glyphs,
previewing them, and editing their outlines. This is the user reference;
how the shadow and the build fit together is in
[ARCHITECTURE.md](../ARCHITECTURE.md), the sidecar format is in
[SIDECARS.md](../SIDECARS.md#stem-controljson), and the design record
behind the feature is archived in [design-notes.md](../archive/design-notes.md).

## Vocabulary

A **secondary parametric axis** is an axis whose effect is constrained to
a named subset of glyphs — realised by **glyph-scoped variation**: brace
layers in `.glyphs`, alternate UFO masters in `.designspace`. Glyphs
without coverage stay static across the axis range. The code, routes and
sidecar call the same thing a **control axis**; any route or filename
quoted here uses `control`.

Secondary parametric axes have **two origins**, and both surface under
the SECONDARY PARAMETRIC AXES sidebar section:

- **Source-derived** (`source: "source"`) — a scoped axis that already
  exists in the source file, read out of its brace layers / alternate
  masters. Read-only in the studio. Roboto Delta's case-split
  `XOUC`/`YOUC`/`XTUC` are these.
- **Studio-declared** (`source: "studio"`) — an axis the *designer*
  declares in the studio with a chosen min/max/default, stored in the
  `<stem>-control.json` sidecar. These carry the edit/delete affordances
  and the brace-layer authoring flow.

Secondary parametric axes are **parallel to**, not a replacement for,
AVAR2 MAPPINGS axes:

| | AVAR2 MAPPINGS axes | SECONDARY PARAMETRIC AXES |
|---|---|---|
| Declared by | designer via **+ Add** (AVAR2 modal) | designer via **+ Add** (secondary-axis modal), or read from source |
| Effect | routed through `-avar.csv` to parametric output axes | brace layers / alternate UFO masters drive per-glyph variation |
| Sidecar | `<stem>-avar.csv` | `<stem>-control.json` (studio-declared only) |
| Source mutation | direct (instance flow) | indirect, via a shadow file (studio-declared) |
| Sidebar section | AVAR2 MAPPINGS | SECONDARY PARAMETRIC AXES |

There are **no hybrids** and no explicit "axis type" field. Routing is by
**coverage**: `glyph_coverage._classify` labels an axis `universal` (100 %
of glyphs vary → stays under AVAR2 MAPPINGS / parametric) or `scoped`
(anything less → SECONDARY PARAMETRIC AXES); the frontend renders only
`scoped` rows there. A tag that exists both source-derived *and*
studio-declared merges into one row.

## Applicable glyphs, coverage and extrapolation

This is the mental model a designer needs when authoring a secondary
parametric axis. It is the part that is easy to get wrong, so it is
spelled out in full.

### A brace layer is an *alternate* outline at a *non-default* location

Every glyph already has one outline: the **master** (its normal drawing),
which lives at the axis **default**. A brace layer is an *additional*
drawing pinned at some *other* location on the axis — "here's how `e`
should look when `crbr` is all the way up."

Consequence: **you cannot place a brace layer at the axis default** of an
axis you mean to drive. The default is the master's territory; a layer
there would collide with it. That is why the *Add applicable glyphs*
modal requires the secondary-axis pin to be **non-default** — it is what
makes the layer a brace layer instead of a duplicate master. (The one
deliberate exception is a *baked* correction axis whose default sits on
its corrected end — see [decision 0011](../decisions/0011-ymod-baked-corrections.md).)

### "Applicable glyphs" = the covered subset

A secondary parametric axis only deforms the glyphs you give it layers
for — its **applicable glyphs** (coverage). Every other glyph stays
static as the slider moves. The axis row lists these glyphs; each one
expands to show its layers. Coverage is **derived** from the unique glyph
names in the axis's `layers` list — it is not stored separately.

### Between the master and your layer: interpolation. Past it: extrapolation.

```
default (master)            your layer                axis max
   │                            │                         │
   ●────────────────────────────●·························?
        interpolation ✓            extrapolation ✗
     (bounded, well-behaved)   (unbounded, usually breaks)
```

- **Between** the master (at the default) and your outermost layer,
  outlines **interpolate** — bounded, predictable.
- **Beyond** your outermost layer, out to the axis extreme, there is no
  authored drawing to interpolate *to*, so the renderer **extrapolates** —
  projects the delta past your outline. This usually overshoots and breaks
  the glyph.

The studio flags this: a glyph shows **⚠ extrapolates** when, on a side
of the default that has axis travel, its outermost layer does not reach
the extreme.

### What "good coverage" looks like depends on where the default sits

| Axis | Default sits… | Layers a glyph needs to be fully defined |
|---|---|---|
| `-100…100`, default `0` | in the **interior** | one reaching `-100` **and** one reaching `+100`. Master handles `0`. |
| `0…40`, default `0` | on the **min** (edge) | one reaching `40`. There is no travel below `0` — the master *is* the `0` end. |
| `0…40`, default `40` | on the **max** (edge) | one reaching `0`. Mirror of the above. |

For an **edge-default axis** a single layer at the far extreme fully
defines the glyph and clears the warning — the studio does **not** warn
about a "missing layer below the default" when the default is already at
the bottom of the range.

### Fixing an extrapolation warning

Two ways, both in the glyph's tray:

- **Pin layers to axis extremes** — moves your outermost layer(s) out to
  the axis min / max. The outline carries over; only the location
  changes (the delta API carries a drawing across the move).
- **+ Add layer for `<glyph>`** at the extreme — keeps your existing
  layer as an intermediate and adds a new anchor at the extreme.

### One location across every applicable glyph

Once an axis covers several glyphs, a new location usually needs to land
on *all* of them:

- **+ Add layer for all N glyphs** at the bottom of the axis row opens
  the add modal targeting the whole coverage set; one layer is created
  per covered glyph. Name extra glyphs in the field to widen coverage in
  the same step.
- **Add to all applicable glyphs** — the same toggle inside any add or
  duplicate modal.

A glyph that already has a layer at that exact location is left alone —
the sidecar de-duplicates on (glyph, location) — so spanning is safe to
repeat.

## Correction layers — "as if at" another parametric point

A brace layer normally starts as the glyph's natural shape at its own
location and is then drawn by hand in Fontra. A **correction layer** is
different: its outline is *computed* as the glyph interpolated at a
**target** parametric point, and re-derived on every rebuild.

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

### Pin the correction, or it leaks

A brace layer compiles to a gvar tuple whose peak is the layer's location
in *normalized* space, and **an axis whose normalized peak is 0 is
omitted from the tuple — which makes it unrestricted**. Normalized 0
means "sits at the axis default".

For a plain brace layer that is harmless. For a correction layer it is
not: the correction applies at *every* value of the omitted axis.

Crispy hits this squarely. Its default master is at XTRA 47, and the
natural correction corner (47 · 1462 · 275) is *also* at XTRA 47 — so
XTRA drops out and the lowercase correction leaks across the whole width
axis. Measured on a 2000 upm:

| | leak at the far end of the ramp | leak at the opposite corner |
|---|---|---|
| correction alone | 276 units | 578 units |
| with an anchor layer | 13–24 units | 1 unit |

The fix is an **anchor**: a second layer for the same glyph, at the same
secondary-axis value, at a *different* value of the unpinned axis, with
**no correction**. Its delta is zero, so it changes nothing where it sits
and bounds the tuple everywhere else. For Crispy that is a plain layer at
(XTRA 1715, XOPQ 1462, YOPQ 275) alongside the corrected one at XTRA 47.
The studio flags a glyph whose correction is unpinned on a parametric
axis with **⚠ unpinned correction**, naming the axis and the anchor to add.

Rules:

- A target overrides **parametric** axes only; a target on the secondary
  axis itself is ignored.
- **Pin every parametric axis, or add an anchor.**
- Computed layers are **re-derived on every rebuild**, so changing the
  target takes effect. Fontra edits on such a layer are overwritten —
  remove the correction to hand-draw it instead.
- A target outside the master range extrapolates like any out-of-range
  coordinate.

### Corrections in the static (GitHub Pages) app

The static app builds control-axis geometry itself:

- **Plain brace layer** — a drawn outline is spliced into the source
  before compiling (`compile_with_overlays`); a layer without a drawing
  keeps a demo approximation: engaging the axis morphs the glyph toward
  its own shape at the layer's parametric location, pinning only the
  secondary axis.
- **Correction layer** — the same model as the full app: the delta is
  measured from the layer's own location to its target.

Corrections are also **pinned automatically** there: where the full app
needs a hand-authored anchor layer, the static build emits a companion
tuple at the unpinned axis's extreme carrying the negated delta, so an
anchor layer carried in from a bundle is simply a plain layer that
contributes nothing at its own location.

## Per-style values

A named style can sit at a chosen value of a secondary axis (an ultra at
`LCWD 100`). Set it on the row's slider; it is saved **in the studio's
control data**, never as a CSV column (why: [decision 0008](../decisions/0008-per-instance-control-values-in-sidecar.md)),
hydrated back onto the row, and stamped onto the matching fvar named
instance at build time.

## Previewing a secondary axis

Every declared secondary axis gets a slider in two places: the
**Instances tab sidebar**, under its own group beneath the core
parametric sliders (every row re-renders at that value — only applicable
glyphs change), and the **Preview tab**. The slider value is **preview
state, not instance data**: it is stripped from instance saves and never
lands in the CSV or the source. The eye icon on the axis row disables it
in preview for a quick with/without comparison (session-local).

## The shadow source file

For studio-declared secondary-axis authoring the studio operates on a
**shadow source file** — never the original.

```
<source-dir>/
  Crispy.glyphs                       ← original. Never written for secondary-axis work.
  Crispy-avar.csv                     ← AVAR2 MAPPINGS.
  Crispy-control.json                 ← declared axes, layer locations, drawings, per-style values.
  .avar2-studio/
    shadow/Crispy.glyphs              ← derived: original + sidecar axes + brace layers (+ grade braces).
    shadow/transformed/Crispy.glyphs  ← derived: the shadow after slant / round corners.
    build/                            ← the preview font.
```

### How `regenerate_shadow` works

On each secondary-axis action (and once at load), `regenerate_shadow`:

1. Re-copies the original to the shadow path (the shadow is fully
   derived; it is never updated incrementally).
2. Appends any sidecar-declared axes missing from the shadow's axis list,
   pads every master's and existing brace layer's coordinate vector, and
   adds a **Virtual Master** per axis extreme so an axis spanned by no
   master still has a real range (glyphsLib would otherwise derive a
   zero-width range and fontra-glyphs would drop the axis, collapsing the
   braces onto one location).
3. Seeds a brace layer for each sidecar entry. The outline comes from, in
   priority order: the **stored `outline`** in the sidecar (a captured
   drawing); the **preserved prior-shadow outline** when its geometry no
   longer matches the seed signature it was stamped with (a hand edit);
   otherwise a fresh **interpolation of the glyph at the layer's
   parametric location** (a correction uses its target), falling back to
   a copy of the default master only when the masters are not
   point-compatible for that glyph. Untouched seeds therefore **follow
   master edits**.
4. Applies studio-side instance renames, writes the file atomically, and
   stamps every freshly seeded layer with its seed signature from the
   saved file.

The shadow is created lazily and used by the build only once an axis has
layers (or an instance is graded, or a source transform is on). The
studio also watches the **original**: editing it in Glyphs regenerates
the shadow from it and rebuilds, so master, instance and glyph edits
propagate without a reload.

### Where drawings live

A drawn outline lives in the shadow **and** in the sidecar. It is
captured from the shadow into `-control.json` on every Fontra save (the
watcher), on every configuration export, and it is carried along when a
layer's coordinates are edited through the modal. A sidecar with outlines
is therefore a complete backup: wiping `.avar2-studio/` and regenerating
reproduces the drawings. Correction layers are never captured (they are
recomputed) and untouched seeds are skipped (so the sidecar only ever
holds real drawing).

### Re-seeding

A captured drawing is restored in preference to re-seeding — which also
freezes it against later edits to the masters it was drawn over. The
layer list shows **source changed** on a drawing whose masters moved
since capture (`source_sig`). **Re-seed** (`POST
/api/control-axes/<tag>/reseed`, body `{layers?, force}`) drops the stored
drawing so the next regeneration pulls the current source through; it
reports `reseeded`, `blocked` (layers holding a drawing — refused with 409
unless `force`), `computed` (correction layers, nothing to re-seed),
`clean` (no drawing, already up to date) and `missing`. There is no undo
once the sidecar is written.

## Source-format scope

- **`.glyphs`** — brace layers live inside each glyph; the shadow is a
  single `.glyphs` copy. Everything above applies.
- **`.designspace`** — no brace layers; the equivalent is a **pooled
  sparse UFO per unique brace location** (`<stem>-studio-<slug>.ufo`
  beside the shadow document, attached as an extra `<source>`). Pools
  that still match a sidecar location are kept across regenerations
  (drawn outlines included); only missing glyphs are re-seeded and stale
  pools deleted. Declaring axes and adding layers works, but this path
  lacks correction targets, captured outlines, seed signatures and
  instance renames, writes non-atomically, and a configuration bundle
  with control axes cannot be imported onto a `.designspace` target.
  Sprint item T2.15 decides parity or an explicit "not supported".

## Editing in Fontra

Brace-layer outlines are edited inline through an **embedded Fontra**,
opened from the ↗ button on any layer row, in a resizable **right-side
drawer** trimmed to a brace-edit surface — sidebars and drawing tools are
hidden — that drops straight into edit mode on the chosen glyph at the
brace location. Fontra runs same-origin behind the studio's own server
(ARCHITECTURE §7); "Open in new tab" opens the raw editor.

**Multi-source batch editing.** In a studio-axis session the drawer
exposes Fontra's sources list, trimmed to *studio layers only*, so you can
edit several brace locations at once. Masters and source-derived layers
are deliberately neither shown nor editable — that is what stops shadow
regeneration (which rebuilds masters from the original) from ever
discarding an edit. Seeded layers are point-compatible by construction,
and the drawing tools stay hidden to keep the batch in sync.

**Reference measurements.** Drawing a horizontal correction means
matching a glyph's bars to the ones that already read correctly — E's to
H's — at the same designspace point. The HUD shows the edited glyph's
pre-edit stem / bar / contrast / advance and the same figures for
reference glyphs (`H,N,O` by default), all cut at the edited layer's exact
coordinates with the secondary axis off (`GET
/api/control-axes/<tag>/reference-metrics?glyph=&index=&refs=&location=`;
the location is sent live by the shim so a glyph with several layers on
the axis measures at the one being edited).

Saving in Fontra writes the shadow; the studio captures the drawing into
the sidecar and rebuilds. The browser demo's editor is the separate
[fontra-embed](https://github.com/agyeiagyeiagyei/fontra-embed) bundle —
same sidecar, same drawings ([decision 0010](../decisions/0010-fontra-embed-gpl-subproject.md)).

## What stays untouched

The AVAR2 MAPPINGS section, `-avar.csv` and the instance flow work
exactly as before. No shadow is interposed for instance editing; "Save to
source file" still writes directly. The shadow strategy is reserved for
secondary parametric axes, where the iteration loop and structural risk
justify it.

## Not built

- **Push-to-source / demote** — no endpoint writes a studio axis into the
  original; add/delete mutate only the sidecar.
- **Sync-state tri-state** for axes — the red/orange/green state, SRC
  badge and demote flow exist only for *instances*; axis rows carry just
  `studio` and `scoped`.
- **Editing-context trio** — interpolation-compatibility validation on
  Fontra save; context-string editing (the alternate inside `Adhesion`);
  axis-aware context rendering (focus glyph at its brace location, context
  glyphs at the sliders' values).
- **Persisted disable-in-preview** — the eye toggle is session-local.
- **`.designspace` parity** — see "Source-format scope".
