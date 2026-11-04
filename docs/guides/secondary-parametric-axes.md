# Secondary parametric axes

The studio's surface for declaring axes that deform only some glyphs, previewing
them, and editing their outlines. This is the reference; the walkthroughs, the
failure recipes (extrapolation, the correction anchor) and the Fontra editing
detail are in
[secondary-parametric-axes-appendix.md](secondary-parametric-axes-appendix.md).
How the shadow and the build fit together is in
[ARCHITECTURE.md](../ARCHITECTURE.md), the sidecar format is in
[SIDECARS.md](../SIDECARS.md#stem-controljson), and the design record behind the
feature is archived in [design-notes.md](../archive/design-notes.md). Axes and
the instance flow are untouched: the shadow strategy applies to secondary axes
only, and "Save to source file" still writes directly.

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

Secondary parametric axes are **parallel to**, not a replacement for, AVAR2
MAPPINGS axes: the former are declared with the secondary-axis modal (or read
from the source), act through brace layers / alternate UFO masters, and are
stored in `<stem>-control.json` when studio-declared; the latter are declared
with the AVAR2 modal, route through `<stem>-avar.csv` onto the parametric
output axes, and mutate the source directly. They appear in their own sidebar
sections and never mix.

There are **no hybrids** and no explicit "axis type" field. Routing is by
**coverage**: an axis that moves every glyph stays under AVAR2 MAPPINGS, an
axis that moves some of them is *scoped* and appears under SECONDARY
PARAMETRIC AXES. A tag that exists both source-derived *and* studio-declared
merges into one row.


## Applicable glyphs and coverage

A secondary parametric axis only deforms the glyphs you give it layers for — its
**applicable glyphs** (coverage). Every other glyph stays static as the slider
moves. Coverage is **derived** from the unique glyph names in the axis's
`layers` list, not stored separately.

A brace layer is an *alternate* outline pinned at a *non-default* location: the
master is the drawing at the default, and layers cover the rest. Between the
master and your outermost layer the outlines **interpolate**; beyond it the
renderer **extrapolates**, which usually overshoots and breaks the glyph. The
glyph row flags the shapes that matter — **⚠ extrapolates** and **⚠ unpinned
correction** (below).

How many layers a glyph needs depends on where the default sits — an
edge-default axis is fully defined by one layer at the far extreme, an
interior default needs one per side. The three cases and the step-by-step
fix for a warning are in
[the appendix](secondary-parametric-axes-appendix.md#what-good-coverage-looks-like-depends-on-where-the-default-sits)
and
[the fixing walkthrough](secondary-parametric-axes-appendix.md#fixing-an-extrapolation-warning),
including
[one location across the whole coverage set](secondary-parametric-axes-appendix.md#one-location-across-every-applicable-glyph).

## Correction layers — "as if at" another parametric point
A brace layer normally starts as the glyph's natural shape at its own
location and is then drawn by hand in Fontra. A **correction layer** is
different: its outline is *computed* as the glyph interpolated at a
**target** parametric point, and re-derived on every rebuild.

The mechanism is a glyph-scoped axis: a layer for every glyph it should
reach, at the corner where it applies, with the correction as if at a
lighter or heavier parametric point — uncovered glyphs stay untouched. The
worked `LCOR` example (with the exact numbers) is in
[the appendix](secondary-parametric-axes-appendix.md#a-worked-correction-axis).

A correction layer's tuple is **unrestricted on any axis whose normalized peak
is 0**, which is how a correction leaks across an unpinned axis; pin every
parametric axis or add an anchor layer. The mechanism, the anchor recipe and
the studio's warnings are in
[the appendix](secondary-parametric-axes-appendix.md#pin-the-correction-or-it-leaks).

## Per-style values
A named style can sit at a chosen value of a secondary axis (an ultra at
`LCWD 100`). Set it on the row's slider; it is saved **in the studio's
control data**, never as a CSV column (why: [decision 0008](../decisions/0008-per-instance-control-values-in-sidecar.md)),
hydrated back onto the row, and stamped onto the matching fvar named
instance at build time.


## Previewing a secondary axis

Every declared axis gets a slider in the **Instances tab sidebar** (under
its own group; only applicable glyphs change), the **Preview tab** and the
**Kinetikern tab**. The value is **preview state, not instance data** — it
never lands in the CSV or the source — and the eye icon disables the axis
for a session-local with/without comparison.

## The shadow source file

Studio-declared axes are authored on a **shadow source file**, never the
original: it is regenerated from the original plus the sidecar on every
action, and the drawings live in the sidecar, so the sidecar alone is a
complete backup. The file layout and the regeneration sequence are in
[the appendix](secondary-parametric-axes-appendix.md#the-shadow-source-file).

## Source-format scope

- **`.glyphs`** — brace layers live inside each glyph; the shadow is a
  single `.glyphs` copy. Everything above applies.
- **`.designspace`** — read-only for secondary axes. No brace layers exist, so
  the equivalent would be a pooled sparse UFO per brace location, and that path
  lacks correction targets, captured drawings, seed signatures and instance
  renames and writes non-atomically. **Authoring answers 501** with that
  explanation ([decision 0013](../decisions/0013-designspace-authoring-501.md));
  everything already declared still builds, and a configuration bundle carrying
  control axes cannot be imported onto a `.designspace` target.


## Editing the outlines

Brace-layer outlines are edited inline through an **embedded Fontra**, opened
from the ↗ button on any layer row and trimmed to a brace-edit surface; in a
studio-axis session it can carry several studio layers at once. Saving writes
the shadow, the studio captures the drawing into the sidecar and rebuilds.
Multi-source batch editing, the reference-measurement HUD and the browser
demo's editor are in
[the appendix](secondary-parametric-axes-appendix.md#editing-in-fontra).

## Not built

- **Push-to-source / demote** — nothing writes a studio axis into the
  original; add/delete mutate only the sidecar.
- **Sync-state tri-state** for axes — the red/orange/green state and SRC badge
  exist only for *instances*; axis rows carry `studio` and `scoped`.
- **Editing-context trio** — interpolation-compatibility validation on save,
  context-string editing, axis-aware context rendering.
- **Persisted disable-in-preview** — the eye toggle is session-local.
- **`.designspace` parity** — see "Source-format scope".
