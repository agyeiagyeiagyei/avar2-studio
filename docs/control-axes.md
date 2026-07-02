# Control axes — design + implementation notes

> **Status:** Partly shipped. This doc is part reference for what's
> built and part design notes for what isn't; every section is tagged
> **`SHIPPED`** or **`DESIGN-ONLY`** so the two don't blur.
>
> **Shipped:** read-only coverage visualisation; `+ Add` / edit /
> delete of studio-declared control axes; applicable-glyph &
> brace-layer authoring on `.glyphs` via the `-control.json` sidecar +
> a shadow source file; inline outline editing through an embedded
> [Fontra](https://github.com/fontra/fontra) drawer (a same-origin
> reverse proxy — integration Path 2, and then some); disable-in-preview.
>
> **Design-only:** push-to-source / demote sync and the red/orange/green
> tri-state for control axes; `.designspace` authoring; capturing drawn
> outlines back into the sidecar (true "model α"); mtime-driven shadow
> regeneration; and the "editing context" trio (interpolation-compat
> validation, context-string editing, axis-aware context rendering).
>
> Where the code and an older draft of this doc disagreed, the doc was
> corrected against the code (July 2026 reconciliation). A few
> known bugs surfaced during that pass are called out inline as
> **`KNOWN GAP`**.

## Vocabulary

A **control axis** is an axis whose effect is constrained to a named
subset of glyphs — realised by **glyph-scoped variation**: brace
layers in `.glyphs`, alternate UFO masters in `.designspace`. Glyphs
without coverage stay static across the axis range.

Control axes have **two origins**, and both surface under the
CONTROL AXES sidebar section:

- **Source-derived** (`source: "source"`) — a scoped axis that already
  exists in the source file, read out of its brace layers / alternate
  masters. Read-only in the studio. Roboto Delta's case-split
  `XOUC`/`YOUC`/`XTUC` are these.
- **Studio-declared** (`source: "studio"`) — an axis the *designer*
  declares in the studio with a chosen min/max/default, stored in the
  `<basename>-control.json` sidecar. These carry the edit/delete
  affordances and the brace-layer authoring flow.

Control axes are **parallel to**, not a replacement for, AVAR2
MAPPINGS axes:

| | AVAR2 MAPPINGS axes | CONTROL AXES |
|---|---|---|
| Declared by | designer via **+ Add** (AVAR2 modal) | designer via **+ Add** (control-axis modal), or read from source |
| Effect | routed through `-avar.csv` to parametric output axes | brace layers / alternate UFO masters drive per-glyph variation |
| Sidecar | `<basename>-avar.csv` | `<basename>-control.json` (studio-declared only) |
| Source mutation | direct (instance flow) | indirect, via a shadow file (studio-declared) |
| Sidebar section | AVAR2 MAPPINGS | CONTROL AXES |

There are **no hybrids** — but note there's no explicit "axis type"
field. Routing is by **coverage**: `glyph_coverage._classify` labels
an axis `universal` (100% of glyphs vary → stays under AVAR2 MAPPINGS /
parametric) or `scoped` (anything less → CONTROL AXES). The frontend
renders only `kind === 'scoped'` rows under CONTROL AXES. A tag that
exists both source-derived *and* studio-declared is merged into one
row, never split into a hybrid. (An earlier draft had a third
`partial` kind for 80–100% coverage; it was dropped — the threshold
was arbitrary and couldn't tell a deliberate near-full scope from an
accidental gap. Some stale `partial` references still linger in
frontend comments but the backend never emits it.)

## Applicable glyphs, coverage & extrapolation `SHIPPED`

This is the mental model a designer needs when authoring a control
axis. It's the part that's easy to get wrong, so it's spelled out
here in full.

### A brace layer is an *alternate* outline at a *non-default* location

Every glyph already has one outline: the **master** (its normal
drawing), which lives at the axis **default**. A brace layer is an
*additional* drawing pinned at some *other* location on the axis —
"here's how `e` should look when `crbr` is all the way up."

Consequence: **you cannot place a brace layer at the axis default.**
The default is the master's territory; a layer there would collide
with it. That's why the *Add applicable glyphs* modal requires the
control-axis pin to be **non-default** — it's not an arbitrary rule,
it's what makes the layer a brace layer instead of a duplicate master.

### "Applicable glyphs" = the covered subset

A control axis only deforms the glyphs you give it layers for — its
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

## The shadow source file `SHIPPED` (with caveats)

For studio-declared control-axis authoring, the studio operates on a
**shadow source file** — never the original. This keeps
experimentation cheap and the original safe.

```
<source-dir>/
  Crispy.glyphs                       ← original. The studio does not
                                        write control-axis edits here.
                                        The designer edits it freely
                                        for everything else.
  Crispy-avar.csv                     ← AVAR2 MAPPINGS staging (existing).
  Crispy-control.json                 ← CONTROL AXES sidecar — declared
                                        control axes + their brace-layer
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

On each control-axis action (and once at load), `regenerate_shadow`:

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

### Where outlines actually live — model β, not model α `DESIGN-ONLY` for α

The **eventual** design goal is *model α*: the sidecar is canonical,
drawn outlines are captured back into `-control.json` as glif
snippets, and the shadow is fully re-derivable from `original +
sidecar` with **no data loss**.

**That is not what ships.** What ships is *model β, best-effort*:

- The sidecar stores only axis declarations and brace-layer
  **locations** — `{glyph, location}`. **No outline / glif XML is
  ever written to the sidecar.**
- Drawn outlines live **only in the shadow `.glyphs`**. They survive a
  regeneration because `regenerate_shadow` reads them back out of the
  *previous shadow*, not the sidecar.
- **`KNOWN GAP` — wiping `.avar2-studio/` loses drawn outlines.**
  With no glif in the sidecar and no prior shadow to read from, every
  brace layer re-seeds as a no-op copy of the default master. The
  older "blow away `.avar2-studio/` and regenerate with no data loss"
  claim only becomes true once model α lands.

### Sidecar JSON shape `SHIPPED`

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

### Lazy shadow creation `SHIPPED` (partial)

The shadow is only created once a control axis exists:
`regenerate_shadow` returns `None` when the sidecar has zero axes (and
for non-`.glyphs` sources), so the shadow directory is absent until
the first **+ Add**.

**`KNOWN GAP` — the build path does not switch to the shadow on
*add*.** `create_control_axis` deliberately keeps the build pointed at
the original; the switch to the shadow happens only when
`set_layers` writes the first brace layer (gated on the axis having
`layers`). Worse, the load-time and delete-time swap is gated on a
dead `ax.get("coverage")` key that the current sidecar never emits —
so **after a restart the build can stay on the original and authored
brace deltas drop out of the preview until the user re-saves a
layer.** This is a bug, not a design choice; fixing the gate to test
`layers` is the intended behaviour.

### Drift between original and shadow `DESIGN-ONLY`

The intent is that the designer never thinks about syncing: edit the
original for normal font work, the shadow for control-axis drawings,
and the studio glues them together by watching the original's mtime
and regenerating the shadow on change.

**This auto-sync is not built.** In reality:

- There is **no mtime-driven shadow regeneration.** The file watcher's
  handler only re-syncs the CSV and triggers a build; it never calls
  `regenerate_shadow`. The shadow is refreshed only on explicit
  control-axis actions (add / set-layers / update / delete) and once
  at load.
- When the shadow is the active build path, the watcher observes the
  **shadow** directory, not the original — so edits to the original
  aren't even detected. Runtime-loaded sources (`/api/load-source`)
  get no watcher at all.

So today, after editing the original outside the studio, you must
trigger a control-axis action (or reload) to fold those changes into
the shadow. Automatic drift-handling is a model-α-era goal.

## Authoring round-trip

Where the 8-step loop stands today:

1. **Declare** — `SHIPPED`. **+ Add** → `POST /api/control-axes` →
   `add_axis` writes the sidecar. Modal captures tag / display name /
   min / max / default; tag is immutable, the rest are editable via
   `PATCH`.
2. **Coverage** — `SHIPPED, redesigned`. There is **no** per-axis
   coverage textarea ("one per line / `# comments`"). That was
   removed. Coverage is **derived** from the unique glyph names of the
   explicit brace layers you add via the **+ Add applicable glyphs**
   modal.
3. **Auto-seeded min/max layers** — `NOT BUILT`. Brace layers are
   explicit-only ("no auto seeding at axis-min/max"). And seeds are a
   **copy of the default-master outline**, not "the interpolated
   outline for that location" — so a freshly-added layer is a
   duplicate of the master, not a visual no-op that matches
   interpolation.
4. **Add layers at custom locations** — `SHIPPED`. Per-glyph
   "+ Add layer for `{glyph}`" and top-level "+ Add applicable glyphs"
   open `AddBraceLocationModal` → `PUT /api/control-axes/<tag>/layers`
   → `set_layers`. The designer pins whichever axes they want; the
   control axis is required non-default.
5. **Open in editor** — `SHIPPED`. The ↗ per-layer button →
   `POST /api/control-axes/<tag>/open-editor` spawns Fontra on the
   shadow and opens the glyph at that location, in edit mode, in a
   right-side drawer (see "Editing in Fontra").
6. **Capture back into sidecar** — `NOT BUILT`. Drawn outlines are
   **not** captured into the sidecar (no glif field, no mtime capture
   of Fontra saves). Closing the drawer just triggers a rebuild;
   outlines persist only inside the shadow `.glyphs` (see model β
   above).
7. **Iterate** — steps 4–5 repeat.
8. **Push to source** — `NOT BUILT`. There is no push-to-source
   endpoint for control axes. Add/delete mutate only the sidecar; the
   original is never written for control-axis work.

### Sync state `DESIGN-ONLY` for control axes

The red/orange/green tri-state, the SRC badge, and the demote flow
described in earlier drafts **exist only for instances**, not control
axes. Control-axis rows carry just two badges: a `studio` tag on
studio-authored layer rows, and the `scoped` kind badge on the axis
row. A per-axis in-sidecar-vs-in-source tri-state (and the
push/demote actions that would drive it) is future work that depends
on step 8 landing first.

## Source-format scope `SHIPPED` (.glyphs) / `DESIGN-ONLY` (.designspace)

Brace-layer authoring differs structurally across the two formats.

### `.glyphs`

Brace layers live inside each glyph's `<layers>` block. Adding one
means appending a layer entry per applicable glyph, keyed by location.
`glyphsLib` round-trips this; the shadow is a single `.glyphs` copy
and mutations are localised. This is what ships.

### `.designspace`

There are no brace layers — the equivalent is a **new UFO master at
the brace location**, containing only the applicable glyphs (others
interpolate from existing masters). This is the Roboto Delta pattern,
and it's heavier: each unique location needs its own UFO directory,
UFOs have to be *pooled* across glyphs sharing a location, and the
`<sources>` block has to stay consistent.

### `.glyphs` only for now

Control-axis **authoring** works on `.glyphs` sources only.
`regenerate_shadow` short-circuits (returns `None`) for any
non-`.glyphs` suffix — that silent skip is where `.designspace`
deferral is actually enforced.

**`KNOWN GAP` — there is no `.designspace` guard-rail in the UI.**
The **+ Add** button is *not* disabled for `.designspace` sources, and
clicking it *succeeds*: `add_axis` writes the sidecar, `/api/axes`
surfaces the new axis as a live-but-no-op slider, and only
`regenerate_shadow` silently returns `None`. So a `.designspace` user
can declare a control axis that can never be authored (no shadow, no
"Open in editor"). A disabled button + explanatory tooltip is the
intended design but is **not yet implemented**.

The v1 **read-only** coverage panel does work on `.designspace` —
Roboto Delta's existing case-split masters show up there; users just
can't author new ones yet.

## Disable in preview `SHIPPED`

Each control-axis row has an **eye-icon toggle** (👁 enabled /
👁‍🗨 disabled — an emoji with a tooltip; there's no text "disable"
label and no keyboard shortcut). When toggled off:

- At render, a **derived** copy of the coordinates
  (`previewCoordinates`) overrides that axis's value with its default
  before building the `font-variation-settings` string. The slider /
  edit state itself is untouched, so re-enabling instantly restores
  the designer's value.
- It's **frontend-only** — no backend call, no rebuild.
- State is **session-local**: a plain React `Set`, not persisted to
  localStorage or the sidecar. It's cleared on every source load /
  swap and pruned when an axis is deleted.
- The toggle renders for **all** control-like axes (source-derived and
  studio-declared alike) and the override applies per instance-preview
  row.

It answers "what does my font look like *without* this control axis's
deformation?" — useful for spotting regressions on non-coverage glyphs
or comparing against a baseline. (Persisting the state is future work.)

## Editing in Fontra `SHIPPED` (Path 2, and beyond the plan)

Inline brace-layer editing is done through an **embedded Fontra**,
implemented as **integration Path 2** below — but the shipped version
materially exceeds the original "iframe Fontra" plan.

What ships:

- **Subprocess lifecycle.** On the first "Open in editor",
  `_ensure_fontra_running` launches `fontra --http-port 8001
  filesystem <shadow>`, reuses the warm process when the content root
  matches, waits for the port to bind, and tears it down via
  `_stop_fontra` + an `atexit` hook.
- **Same-origin reverse proxy, not a cross-origin iframe.** The drawer
  loads `/fontra/editor.html?project=<name>` on avar2-studio's *own*
  origin; a `/fontra/<path>` proxy forwards to `127.0.0.1:8001`,
  rewriting absolute HTML/CSS/importmap paths. A `/websocket` leg is
  proxied too, plus root-level runtime routes (`/lang`, `/data`,
  `/images`, `/webfonts`, `/projectlist`, `/serverinfo`) and `/api` +
  root catch-alls, so Fontra runs fully same-origin. This is what
  makes the next item possible and sidesteps CORS entirely.
- **Focused-UI CSS injection.** `_FONTRA_FOCUSED_CSS` is injected
  before `</head>`, hiding Fontra's sidebar panels
  (designspace-navigation, reference-font, glyph-search,
  related-glyphs, …) and drawing tools (pen, knife, shape) — leaving a
  trimmed brace-edit surface rather than the raw Fontra UI.
- **Right-side drawer.** `FontraEditorModal` is a `position: fixed`
  right drawer (60vw, min 600px) with a left-edge resize handle whose
  width persists in localStorage. Not a modal, not the overview.
- **Fragment-based navigation into edit mode.** Navigation uses a
  base64 + zlib **URL fragment** (mirroring Fontra's
  `dumpURLFragment`), not query params: the viewInfo carries the glyph
  as `text`, `selectedGlyph: { …, isEditing: true }` (drops straight
  into edit mode), and the full location vector overlaying the sparse
  layer location on axis defaults — **keyed by axis display name**
  (not tag) to match `fontra-glyphs`. An "Open in new tab" escape uses
  the direct `:8001` URL.

Together these resolve the two open unknowns the design flagged — CORS
behaviour and jump-to-glyph navigation — both handled.

### Editing-context futures `DESIGN-ONLY`

Three deeper requirements are **not** built (the shipped viewInfo
passes only the single focus glyph as `text`):

1. **Interpolation-compatibility validation** — on each Fontra save,
   compare the brace layer's contour/point structure against the
   default master and warn on mismatch (e.g. via
   `fontTools.pens.RecordingPen`). Not implemented.
2. **Context-string editing** — render the focus glyph inside a
   context string (`Adhesion`, `The quick…`) so the designer sees the
   alternate in real text. Not implemented.
3. **Axis-aware context rendering** — render the *focus* glyph at its
   brace location while the *context* glyphs render at the studio
   sliders' current values, exposing "does this alternate read at
   Regular?" regressions. Not implemented; would need two location
   vectors through the Fontra protocol.

## Fontra integration — paths considered

For the record, the four integration paths evaluated. **Path 2 is
what shipped** (above); the rest are `DESIGN-ONLY`.

| Path | Approach | Status |
|---|---|---|
| **1. Separate-tab Fontra** | Launch Fontra on its own port; "Open in editor" opens a new browser tab. | design-only |
| **2. iframe / same-origin proxy** | Run Fontra alongside us and embed it. **Shipped as a same-origin reverse proxy + focused UI + drawer + fragment nav — beyond the original iframe sketch.** | **SHIPPED** |
| **3. avar2-studio as a Fontra view plug-in** | Repackage our React app as a `fontra.views` entry-point; drop Flask; one server, one tab. | design-only (natural v3) |
| **4. Fontra backends as a library dep** | Keep Flask; read via `fontra.backends` instead of `glyphsLib`/`designspaceLib`. | design-only |

### What Fontra is

A web-based font editor structured like avar2-studio (Python server +
JS client, default port 8000). Reads `.designspace`, `.ufo`, `.ttf`,
`.otf`; pluggable backends for new formats. It exposes plug-in APIs
(Project Manager, View, Filesystem backend, Static content) documented
in [`docs/plugin-structure.md`](https://github.com/fontra/fontra/blob/main/docs/plugin-structure.md).
The View plug-in is what would make Path 3 possible.

### Why not Path 3 / 4 yet

Path 3 (view plug-in) is the cleanest end-state but requires replacing
our Flask server with Fontra's aiohttp one, restructuring the React
build as a Python package, and adopting Fontra's project-manager /
font-loading model — a project on its own; we'd risk shipping two
half-products. Path 2 keeps scope bounded and proves the value first.
Path 4 (Fontra backends as an internal lib) doesn't deliver the editor
that motivates the exercise; it's a possible migration step toward
Path 3, invisible to the user.

### Sidecar-format implication for a future Path 3

If we ever migrate toward Path 3, the sidecar should stay simple
enough that Fontra could be a second consumer: a stable `version`ed
file format, no avar2-studio-specific metadata baked into per-axis
records that Fontra couldn't interpret. (Note: capturing outlines into
the sidecar — model α — is a prerequisite for Fontra reading drawings
out of it; today outlines live in the shadow, not the sidecar.)

## What v1 read-only delivered (backend shape)

The read-only coverage surface that v1 shipped, still current:

- `glyph_coverage.compute_coverage(font)` dispatches to private
  `_coverage_from_glyphs` / `_coverage_from_designspace` — walk each
  glyph's intermediate-position layers (`.glyphs`) or masters at
  non-default locations (`.designspace`), returning per-axis coverage.
- `GET /api/glyph-coverage`:
  ```jsonc
  {
    "axes": [
      { "tag": "XOPQ", "name": "X-Opacity",
        "covers": ["A", "…", "z"], "covers_count": 245,
        "total_glyphs": 245, "kind": "universal" },
      { "tag": "crbr", "name": "Crossbar",
        "covers": ["A","E","F","H","e","f","t"], "covers_count": 7,
        "total_glyphs": 245, "kind": "scoped" }
    ]
  }
  ```
  - `kind: "universal"` (full coverage) → stays under AVAR2 MAPPINGS /
    parametric.
  - `kind: "scoped"` (anything less) → surfaces under CONTROL AXES.

Everything the older "Out of v1" list marked as future has since
**shipped**, except two items. Shipped since v1: the `-control.json`
sidecar, the shadow source file, the **+ Add** control-axis modal
(create *and* edit), the coverage editor (redesigned as the explicit
`LayersEditor` brace-layer authoring UI), and the "Open in editor"
button (the embedded Fontra drawer). **Still not built:**
push-to-source / demote for control axes, and persisted disable state.

## What stays untouched

The existing AVAR2 MAPPINGS section, `-avar.csv`, and instance flow
work exactly as before. No shadow is interposed for instance editing;
"Save to source file" still writes directly. The shadow strategy is
reserved for control axes, where the iteration loop and structural
risk justify it.

## Open questions

- **`.designspace` authoring** — the UFO-pooling design (a UFO per
  pooled brace location, dedup, `<sources>` consistency) is unbuilt.
- **Coverage-compute perf** — Roboto Delta's full source is ~3000
  glyphs × ~20 UFOs; iterating every master at each load may be slow.
  Cache by mtime? Compute lazily when the panel expands?
- **Composite-glyph inheritance** — if `e` covers `crbr` and `é`
  composes `e`, is `é` shown as covered directly, or "inherited"?
- **Push-to-source granularity** — per-axis (likely) or all at once?
  Unbuilt.
- **Model α migration** — capturing drawn outlines into the sidecar so
  `.avar2-studio/` is truly disposable, and so a future Fontra Path 3
  could read outlines from the sidecar.
- ~~Coverage-editor text format (newline / `# comments` / regex
  ranges)~~ — resolved by supersession: the text-field approach was
  dropped for explicit brace layers.
