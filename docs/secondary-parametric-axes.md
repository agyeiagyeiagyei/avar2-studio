# Secondary parametric axes

The studio's surface for declaring axes that deform only some glyphs, previewing
them, and editing their outlines. This is the user reference; the design record
(what isn't built, the alternatives weighed, the Fontra integration paths) is in
[design-notes.md](design-notes.md), and how the shipped code works under the hood
is in [HANDOVER.md](HANDOVER.md).

## Vocabulary

A **secondary parametric axis** is an axis whose effect is constrained to a named
subset of glyphs — realised by **glyph-scoped variation**: brace
layers in `.glyphs`, alternate UFO masters in `.designspace`. Glyphs
without coverage stay static across the axis range.

Secondary parametric axes have **two origins**, and both surface under the
SECONDARY PARAMETRIC AXES sidebar section:

- **Source-derived** (`source: "source"`) — a scoped axis that already
  exists in the source file, read out of its brace layers / alternate
  masters. Read-only in the studio. Roboto Delta's case-split
  `XOUC`/`YOUC`/`XTUC` are these.
- **Studio-declared** (`source: "studio"`) — an axis the *designer*
  declares in the studio with a chosen min/max/default, stored in the
  `<basename>-control.json` sidecar. These carry the edit/delete
  affordances and the brace-layer authoring flow.

Secondary parametric axes are **parallel to**, not a replacement for, AVAR2
MAPPINGS axes:

| | AVAR2 MAPPINGS axes | SECONDARY PARAMETRIC AXES |
|---|---|---|
| Declared by | designer via **+ Add** (AVAR2 modal) | designer via **+ Add** (secondary-axis modal), or read from source |
| Effect | routed through `-avar.csv` to parametric output axes | brace layers / alternate UFO masters drive per-glyph variation |
| Sidecar | `<basename>-avar.csv` | `<basename>-control.json` (studio-declared only) |
| Source mutation | direct (instance flow) | indirect, via a shadow file (studio-declared) |
| Sidebar section | AVAR2 MAPPINGS | SECONDARY PARAMETRIC AXES |

There are **no hybrids** and no explicit "axis type" field. Routing is by
**coverage**: `glyph_coverage._classify` labels an axis `universal` (100% of
glyphs vary → stays under AVAR2 MAPPINGS / parametric) or `scoped` (anything
less → SECONDARY PARAMETRIC AXES); the frontend renders only `scoped` rows
there. A tag that exists both source-derived *and* studio-declared merges into
one row.

## Applicable glyphs, coverage & extrapolation

This is the mental model a designer needs when authoring a secondary
parametric axis. It's the part that's easy to get wrong, so it's spelled out
here in full.

### A brace layer is an *alternate* outline at a *non-default* location

Every glyph already has one outline: the **master** (its normal
drawing), which lives at the axis **default**. A brace layer is an
*additional* drawing pinned at some *other* location on the axis —
"here's how `e` should look when `crbr` is all the way up."

Consequence: **you cannot place a brace layer at the axis default.**
The default is the master's territory; a layer there would collide
with it. That's why the *Add applicable glyphs* modal requires the
secondary-axis pin to be **non-default** — it's not an arbitrary rule,
it's what makes the layer a brace layer instead of a duplicate master.

### "Applicable glyphs" = the covered subset

A secondary parametric axis only deforms the glyphs you give it layers for — its
**applicable glyphs** (a.k.a. coverage). Every other glyph stays
static as the slider moves. The axis row lists these glyphs; each
one expands to show its layers. Coverage is **derived** from the
unique glyph names in the axis's `layers` list — it is not stored
separately.

### Between the master and your layer: interpolation. Past it: extrapolation.

Take an axis and one glyph with one brace layer:

```
default (master)            your layer                axis max
   │                            │                         │
   ●────────────────────────────●·························?
        interpolation ✓            extrapolation ✗
     (bounded, well-behaved)   (unbounded, usually breaks)
```

- **Between** the master (at the default) and your outermost layer,
  outlines **interpolate** — bounded, predictable.
- **Beyond** your outermost layer, out to the axis extreme, there's
  no authored drawing to interpolate *to*, so the renderer
  **extrapolates** — projects the delta past your outline. This
  usually overshoots and breaks the glyph.

The studio flags this: a glyph shows **⚠ extrapolates** when, on a
side of the default that has axis travel, its outermost layer doesn't
reach the extreme.

### What "good coverage" looks like depends on where the default sits

The default's position on the axis decides how many layers a glyph
needs. The master owns the default endpoint, so you never author a
layer *there* — you anchor the *extremes that have travel*:

| Axis | Default sits… | Layers a glyph needs to be fully defined |
|---|---|---|
| `-100…100`, default `0` | in the **interior** | one reaching `-100` **and** one reaching `+100`. Master handles `0`. |
| `0…40`, default `0` | on the **min** (edge) | one reaching `40`. There's no travel below `0` — the master *is* the `0` end — so no "below" layer exists or is wanted. |
| `0…40`, default `40` | on the **max** (edge) | one reaching `0`. Mirror of the above. |

This is why, for an **edge-default axis**, a single layer at the far
extreme fully defines the glyph and clears the warning — and why the
studio does **not** warn about a "missing layer below the default"
when the default is already at the bottom of the range. There is no
below.

### Fixing an extrapolation warning

Two ways, both in the glyph's tray:

- **Pin layers to axis extremes** — moves your outermost layer(s) out
  to the axis min / max. The outline data carries over; only the
  location changes. Fastest fix when your layer is already the right
  shape, just at the wrong coordinate.
- **+ Add layer for `<glyph>`** at the extreme — keeps your existing
  layer as an intermediate and adds a new anchor at the extreme. Use
  when you want a distinct drawing at the extreme, not just a moved
  copy of the intermediate.

## The shadow source file

For studio-declared secondary-axis authoring, the studio operates on a
**shadow source file** — never the original. This keeps
experimentation cheap and the original safe.

```
<source-dir>/
  Crispy.glyphs                       ← original. The studio does not
                                        write secondary-axis edits here.
                                        The designer edits it freely
                                        for everything else.
  Crispy-avar.csv                     ← AVAR2 MAPPINGS staging (existing).
  Crispy-control.json                 ← SECONDARY PARAMETRIC AXES sidecar — declared
                                        secondary parametric axes + their brace-layer
                                        LOCATIONS (not outlines).
  .avar2-studio/
    shadow/
      Crispy.glyphs                   ← derived. Original + sidecar axes
                                        + brace layers. The preview
                                        builds from this once an axis
                                        has layers.
    build/
```

### How `regenerate_shadow` works

On each secondary-axis action (and once at load), `regenerate_shadow`:

1. Copies the original source tree → `.avar2-studio/shadow/`.
2. Appends any sidecar-declared axes missing from the shadow's axis
   list, and pads every master's and existing brace layer's coordinate
   vector to match.
3. Seeds a brace layer for each `{glyph, location}` entry in the
   sidecar. The seed outline is a **copy of the glyph's default-master
   outline**, *or* — if a previous shadow already had a drawing at that
   location — the **preserved prior-shadow outline** (via
   `_extract_brace_outlines`).

`.designspace` is not handled: `regenerate_shadow` returns `None` for
any non-`.glyphs` source (see "Source-format scope").

> **Outlines live only in the shadow, not the sidecar** — so wiping
> `.avar2-studio/` loses drawn outlines. This "model β" limitation, and the
> "model α" design that would fix it, are in [design-notes.md](design-notes.md).

### Sidecar JSON shape

```jsonc
{
  "version": 1,
  "axes": [
    {
      "tag": "crbr",
      "display_name": "Crossbar",
      "default": 0,
      "min": -100,
      "max": 100,
      "layers": [
        { "glyph": "e", "location": { "crbr": -100 } },
        { "glyph": "e", "location": { "crbr": 100 } },
        { "glyph": "e", "location": { "crbr": -100, "XOPQ": 78 } },
        { "glyph": "f", "location": { "crbr": 100 } }
      ]
    }
  ]
}
```

Key facts about the real shape:

- **`layers` is a flat per-axis array of `{glyph, location}`** — *not*
  a per-glyph object keyed by glyph name. Grouping-by-glyph is a
  frontend display concern (`LayersEditor` builds a `byGlyph` Map);
  it isn't the storage shape.
- **No `glif` / outline field.** Entries are `{glyph, location}` only.
- **No `coverage` field.** Coverage is derived from the unique glyph
  names in `layers`. (`coverage` and `extra_locations` exist only as
  *legacy* keys, migrated into `layers` on load and never re-emitted.)
- **`location` is sparse**, a JSON object keyed by axis **tag** — only
  the axes the designer pinned appear; the rest interpolate at build
  time. It's a JSON object (not a stringified key) so axis-order
  quirks don't break lookups.
- **`version: 1`** so future tooling can read/write the format.

The shadow is created lazily: it appears only once an axis has its first brace
layer (the build stays on the original until then). After editing the original
outside the studio, trigger a secondary-axis action or reload to fold the change
into the shadow — there's no automatic original↔shadow sync yet (see
[design-notes.md](design-notes.md)).

## Source-format scope

Brace-layer authoring differs structurally across the two formats, but both
author through a shadow.

- **`.glyphs`** — brace layers live inside each glyph's `<layers>` block; the
  shadow is a single `.glyphs` copy and mutations are localised.
- **`.designspace`** — no brace layers; the equivalent is a **pooled sparse UFO
  source per unique brace location** (one UFO holding every applicable glyph
  pinned there, attached as an extra `<source>`), named
  `<stem>-studio-<slug>.ufo` beside the shadow document. Because the pools are
  studio-owned, a pool that still matches a sidecar location is kept as-is
  across regenerations (drawn outlines included); only missing glyphs are
  re-seeded and stale pools deleted.

## Disable in preview

Each secondary-axis row has an **eye-icon toggle** that renders the preview with
that axis pinned to its default — answering "what does my font look like
*without* this deformation?" It's frontend-only (no rebuild), applies to every
secondary axis, and leaves the slider value untouched, so re-enabling restores
it instantly. The state is session-local (not persisted yet).

## Editing in Fontra

Brace-layer outlines are edited inline through an **embedded
[Fontra](https://github.com/fontra/fontra)**, opened from the ↗ button on any
layer row. It appears as a **right-side drawer** (resizable, width persisted),
trimmed to a brace-edit surface — Fontra's sidebars and drawing tools are hidden
— and drops straight into edit mode on the chosen glyph at the brace location.
Fontra runs same-origin behind avar2-studio's own server (proxy and lifecycle
details in [HANDOVER.md](HANDOVER.md)); an "Open in new tab" escape hatch opens
the raw editor.

**Multi-source batch editing.** In a studio-axis session the drawer exposes
Fontra's sources list, trimmed to *studio layers only*, so you can edit several
brace locations at once. Masters and source-derived layers are deliberately
neither shown nor editable — that's what stops shadow regeneration (which
rebuilds masters from the original) from ever discarding an edit. Seeded layers
start as master copies/interpolations, so they're point-compatible by
construction, and the drawing tools stay hidden to keep the batch in sync.

## What stays untouched

The existing AVAR2 MAPPINGS section, `-avar.csv`, and instance flow
work exactly as before. No shadow is interposed for instance editing;
"Save to source file" still writes directly. The shadow strategy is
reserved for secondary parametric axes, where the iteration loop and structural
risk justify it.

---

Design record — unbuilt features, alternatives weighed, the Fontra integration
paths, and open questions — is in [design-notes.md](design-notes.md).
