# Glyph-scoped axes — design notes

> **Status:** Design / not yet implemented. v1 target is **read-only
> visualisation** of existing per-glyph axis coverage; authoring lands
> in v2 as a duplicate-layer staging model that mirrors how the studio
> already handles instance edits.

## Reframe — "control axes" as the umbrella

The studio today has two recognised axis kinds:

1. **Parametric axes** — declared in the source file, backed by
   master deltas. `is_parametric: true` in `axis-metadata.json`.
   Read-only on min/max because the masters themselves define the
   travel.
2. **Traditional / avar2-input axes** — declared via the **+ Add**
   button under AVAR2 MAPPINGS. The user picks tag, display name,
   default, min, max. The axis exists only to be routed through the
   sibling `-avar.csv` mapping table to parametric outputs.
   `is_parametric: false` in `axis-metadata.json`.

The glyph-scoped axes we want to add are a **third backing** for
something that looks just like (2) from the user's perspective —
declared in the studio, with user-picked min/max, but **wired up to
glyph-level variation instead of to the avar2 mapping table.**

That common surface deserves a name. The unifying concept is a
**control axis**: an axis the *designer* declares (not the source
file) with its own range, whose effect is realised through one or
more backings:

| Backing | Where the variation lives | Existing today? |
|---|---|---|
| **avar2 mapping** | a column in `<basename>-avar.csv` routed to parametric output axes | ✓ |
| **glyph-scoped** | brace layers in `.glyphs` or alternate UFO masters tied to a named subset of glyphs | ✗ — what this doc proposes |
| (future) **hybrid** | both — a control axis that drives avar2 routing AND has per-glyph master deltas | also ✗ |

The min/max declaration plumbing — `AddAxisModal`, `EditAxisModal`,
`axis-metadata.json`, `POST/PUT /api/avar2/axis` — already exists
and is correct for control axes. What's missing is:

- A way to mark a control axis's backing as **glyph-scoped** instead
  of (or in addition to) avar2-mapping.
- A way to enumerate which glyphs back it (read from brace layers /
  masters in v1; editable in v2).
- UI labels that don't conflate "I declared this axis" with "this
  axis must route through avar2".

The endpoint name `/api/avar2/axis` is a little misleading once
control axes can have non-avar2 backings — keep the route for
back-compat, but the *data shape* should grow a `backing` discriminator
(`'avar2' | 'glyph_scoped' | 'hybrid'`). Storage layout in
`axis-metadata.json`:

```jsonc
{
  "CRBR": {
    "display_name": "Crossbar",
    "registered_tag": "crbr",
    "min": -100,
    "max": 100,
    "default": 0,
    "is_parametric": false,
    "backing": "glyph_scoped",     // ← NEW
    "glyph_coverage": [            // ← NEW (v2 — read-derived in v1)
      "A", "E", "F", "H", "e", "f", "t"
    ]
  }
}
```

The rest of this doc proceeds with the "glyph-scoped backing"
specifics — but with the understanding that we're describing **one
backing of a control axis**, not a separate axis class.

## What problem this solves

In a parametric font, **most axes are font-wide** — every glyph
participates by being interpolated across all masters. But some
useful axes only deform a subset of glyphs:

- A `crossbar` axis that shortens or lengthens horizontal strokes on
  `e`, `f`, `t`, `H`, `A`, `e`, `€` — and does nothing elsewhere.
- A `serif` axis that toggles bracket shape on letters with feet, no
  effect on bowls and rounds.
- A `corner` axis that softens corners only on glyphs with sharp
  exterior corners (`M`, `K`, `Z`, ...).

The current Crispy / Roboto Delta examples don't surface this; every
parametric axis is global to every glyph. The next thing avar2-studio
should know how to talk about is **glyph-scoped axes** — axes that
exist font-wide in the designspace but whose effect is constrained to
a named subset of glyphs.

## Mental model — implicit scoping

We picked the **implicit** model: every axis is global; coverage is a
property of each glyph, not of the axis declaration. A glyph "covers"
an axis if and only if it ships variation along that axis — via a
brace layer (in `.glyphs`) or an alternate master (in `.designspace`).
Glyphs without coverage just stay static at all positions along the
axis.

This matches the underlying tech exactly. fontmake, fontc, and
glyphsLib all read brace layers / master locations and produce gvar
deltas only for the glyphs that have them; the rest of the font is
unaffected. We don't need a new fontc feature, we just need a UI that
*tells the truth* about what's already there.

The "explicit" alternative — declaring that axis X applies only to
glyphs A/B/C in the designspace itself — would require either a new
spec extension or non-standard metadata. The implicit model is
zero-effort on the format side.

## Reading existing coverage — v1 scope

avar2-studio already loads `.glyphs` via `glyphsLib` and
`.designspace` via `fontTools.designspaceLib`. The data we need is
already accessible:

### From a `.glyphs` source

For each glyph in the font, walk its layers. Any layer whose
``attributes.coordinates`` (or the older brace-layer ``name``
convention ``{300, 100}``) puts it at a non-default position on some
axis means **that glyph covers that axis**.

```python
# Sketch — not implemented yet.
def glyph_coverage_from_glyphs(font) -> dict[str, list[str]]:
    """Return {axis_tag: [glyph_name, ...]} — glyphs with brace
    layers at non-default positions on the axis."""
    coverage = defaultdict(set)
    for glyph in font.glyphs:
        for layer in glyph.layers:
            location = _layer_axis_location(layer)  # dict {axis_tag: value}
            if not location:
                continue
            for tag, value in location.items():
                axis = font.axes_by_tag[tag]
                if value != axis.default_value:
                    coverage[tag].add(glyph.name)
    return {tag: sorted(names) for tag, names in coverage.items()}
```

### From a `.designspace` source

For each master listed in the designspace, the master's
``<location>`` lists the axis values it sits at. A master at a
non-default position contributes variation to *every glyph it
contains*. We open each UFO and list its glyphs — any glyph present
in that UFO covers every axis whose location value differs from the
default.

```python
# Sketch — not implemented yet.
def glyph_coverage_from_designspace(ds) -> dict[str, list[str]]:
    """Return {axis_tag: [glyph_name, ...]} for designspace sources."""
    defaults = {a.name: a.default for a in ds.axes}
    coverage = defaultdict(set)
    for source in ds.sources:
        ufo = load_ufo(source.path)
        non_default_axes = [
            tag for tag, value in source.location.items()
            if value != defaults[tag]
        ]
        if not non_default_axes:
            continue  # default master — contributes nothing axis-specific
        for glyph_name in ufo.keys():
            for tag in non_default_axes:
                coverage[tag].add(glyph_name)
    return {tag: sorted(names) for tag, names in coverage.items()}
```

### Caveats

- A glyph can cover an axis at one location and not another (think
  brace layer at axis=200 but not at axis=400). For the v1 list-view
  we collapse this to "covers / doesn't cover"; a more detailed
  diagram (axis × glyph heatmap) is a v2 nicety.
- "Doesn't cover" really means "interpolates as the default with no
  glyph-specific delta" — the glyph still renders at any axis
  position, it just doesn't change shape.
- Composite glyphs that reference a covered component (e.g. `é`
  referencing a covered `e`) automatically inherit coverage at
  render-time. For the v1 list-view we report the *direct* coverage
  only — components are a v2 concern.

### Endpoint shape

```
GET /api/glyph-coverage
→ {
    "axes": [
      {
        "tag": "XOPQ",
        "name": "X-Opacity",
        "covers": ["A", "B", "C", "D", ...],
        "covers_count": 245,
        "total_glyphs": 245    # universal coverage
      },
      {
        "tag": "CRBR",
        "name": "Crossbar",
        "covers": ["A", "E", "F", "H", "e", "f", "t"],
        "covers_count": 7,
        "total_glyphs": 245    # glyph-scoped — narrow coverage
      }
    ]
  }
```

The frontend uses ``covers_count / total_glyphs`` to badge axes as
**universal** (~100% coverage), **scoped** (small named subset), or
**partial** (somewhere in between — usually a smell worth flagging).

### Frontend surface

A new collapsible panel under AVAR2 MAPPINGS titled **GLYPH COVERAGE**
(or **PER-GLYPH AXES** — naming TBD):

```
GLYPH COVERAGE
  XOPQ  universal   245 glyphs                    ▾
  YOPQ  universal   245 glyphs                    ▾
  XTRA  universal   245 glyphs                    ▾
  CRBR  scoped      7 glyphs    A E F H e f t     ▾
  SERF  scoped      18 glyphs   B D E F G ...     ▾
```

Click an axis row → expands to show the full glyph list (clickable
glyph names that select that glyph in a preview pane — v2). For v1
just the list is enough.

## Authoring direction — v2

The user authors:

1. **Alternate drawings** in Glyphs.app or their UFO editor. avar2-studio
   never touches outlines — that's a font-editor responsibility.
2. **Coverage list** in avar2-studio. The studio surfaces an editable
   per-axis glyph list. Adding a glyph to the list creates a *hook*:
   a brace layer or alternate UFO entry seeded with the default
   shape. The user then opens Glyphs/UFO editor and edits that hook.
3. **avar2 mapping rows** — same as today.

### The "hook glyph" pattern

The user suggested a glyph like ``e.crossbar`` — a synthetic
component glyph whose only job is to hold axis-scoped variation. The
main ``e`` references ``e.crossbar`` as a component; the crossbar
glyph has brace layers on the relevant axis, but ``e`` itself stays
clean on the global axes.

This is a real font-engineering pattern and works well for axes that
deform isolated parts of letters (crossbar, terminal, joint, dot,
etc.). The studio's UI for this could be:

```
AXIS: CRBR (Crossbar)

Component glyphs (carry the variation):
  e.crossbar
  f.crossbar
  t.crossbar
  H.crossbar
  + add component glyph…

Glyphs that reference one of these components:
  e (uses e.crossbar)
  f (uses f.crossbar)
  t (uses t.crossbar)
  H (uses H.crossbar)
  é, è, ê, ë (composite, references e)
  ...
```

This is a v2 surface; the v1 read-only view just lists glyphs with
direct coverage and surfaces their layer/master locations.

### Duplicate-layer staging

The user's spec: "eventually we'll get to authoring on a duplicate
layer that we'll then allow the user to write to the glyphs file,
much like avar2studio handles instances now."

This mirrors the existing CSV-vs-source split:

| Pattern | Today (instances) | Tomorrow (glyph coverage) |
|---|---|---|
| Source of truth | `.glyphs` / `.designspace` instance list | `.glyphs` brace layers / `.designspace` masters |
| Studio's working copy | `<basename>-avar.csv` | `<basename>-coverage.json` (proposed sidecar) |
| Save to studio | Updates CSV row only | Updates JSON sidecar only |
| Save to source | Writes back to source file | Writes brace layers / master entries to source |
| Demote | Source row → studio-only | Source-declared coverage → JSON-only |

The same red / orange / green sync model + flyout pattern carries
over. The user picks coverage in the studio, it lives in a sidecar
JSON until they explicitly push to source. Glyphs.app authoring of
the actual outlines happens externally; the studio never touches
glyph shapes.

## Branch + commits ahead

This branch (``glyph-scoped-axes``) is for the v1 read-only work:

1. **Backend** — `glyph_coverage.py` module with the two functions
   sketched above. New `GET /api/glyph-coverage` endpoint. Extend
   `axis-metadata.json` schema with the `backing` discriminator and
   teach `_load_axis_metadata` to default existing entries to
   `backing: "avar2"` for back-compat.
2. **Frontend** — new `GlyphCoverage` component rendered under
   AVAR2 MAPPINGS (panel may eventually want a CONTROL AXES rename
   once the reframe lands in the UI). Reads ``/api/glyph-coverage``
   on font load.
3. **No source mutation** in v1. The "Edit alternates in Glyphs.app"
   note is the only authoring affordance — users open their font
   editor, modify glyphs, re-load in the studio to see updated
   coverage.

v2 (separate branch later): the duplicate-layer staging + sidecar
JSON + flyout integration. **+ Add** modal grows a "Backing" radio
(avar2-input / glyph-scoped / hybrid) so the user picks the backing
when declaring a control axis.

## Open questions still

- **Designspace sources with thousands of glyphs** — Roboto Delta's
  full source has ~3000 glyphs per UFO. Reading every UFO at load
  time to compute coverage might be slow. Cache per `.designspace`
  mtime? Compute lazily when the panel is opened?
- **The "partial" coverage case** — when an axis covers more than a
  named-subset but less than every glyph (say 60%), is that a smell
  worth flagging or just a fact about the font? Probably a smell —
  partial coverage usually means the designer forgot to author a few
  glyphs at the alternate master.
- **Glyph-list separator format for the editable coverage list (v2)** —
  comma-separated? newline-separated? regex-supported? The user
  mentioned "perhaps by some kind of text list" — I lean
  newline-separated with optional grouping comments (``# letters with
  crossbars``) for readability.
- **UI rename — AVAR2 MAPPINGS → CONTROL AXES?** Once a control
  axis can have a glyph-scoped backing, calling the section "AVAR2
  MAPPINGS" misleads. But the rename ripples through tooltips,
  empty-state hints, and at least one CSS classname. Do it as a
  pre-v1 cosmetic pass, or defer until v2 when the backing selector
  actually lands? Leaning defer — the existing label is wrong but
  not actively misleading until users can pick a non-avar2 backing.
- **Back-compat for `axis-metadata.json` without `backing`** —
  treat absence as `backing: "avar2"`. `_load_axis_metadata` should
  fill the field in on load so the rest of the code can assume it's
  present. No migration needed; the field just appears on the next
  write.
