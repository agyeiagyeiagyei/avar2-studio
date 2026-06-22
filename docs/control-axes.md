# Control axes — design notes

> **Status:** Design / not yet implemented. v1 ships read-only
> visualisation of any glyph-scoped variation that already exists in
> the source. v2 adds authoring through a shadow source file with a
> sidecar-as-source-of-truth staging model. v3 (aspirational)
> embeds [Fontra](https://github.com/fontra/fontra) so brace-layer
> drawing happens inline instead of round-tripping through Glyphs.app.

## Vocabulary

A **control axis** is an axis the *designer* declares (not the source
file) with a chosen min/max/default. Its effect is realised by
**glyph-scoped variation** — brace layers in `.glyphs`, alternate UFO
masters in `.designspace` — affecting only a named subset of glyphs.
Glyphs without coverage stay static across the axis range.

Control axes are **parallel to**, not a replacement for, AVAR2
MAPPINGS axes:

| | AVAR2 MAPPINGS axes (existing) | CONTROL AXES (new) |
|---|---|---|
| Declared by | designer via **+ Add** modal | designer via **+ Add Control Axis** (v2) |
| Effect | routed through `-avar.csv` to parametric output axes | brace layers / alternate UFO masters drive per-glyph variation |
| Sidecar | `<basename>-avar.csv` | `<basename>-control.json` |
| Source mutation | direct (instance flow) | indirect, via shadow file |
| Sidebar section | AVAR2 MAPPINGS | CONTROL AXES (parallel) |
| Hybrid? | no — an axis is one or the other |

An axis is exactly one of: parametric (source-backed, full glyph
coverage), AVAR2 MAPPINGS, or CONTROL AXES. No hybrids in v2.

## The shadow source file

For authoring, the studio operates on a **shadow source file** —
never the original. This makes control-axis experimentation cheap
(designers can add/remove coverage glyphs many times before
committing) and keeps the original safe.

```
<source-dir>/
  Crispy.glyphs                       ← original. Studio NEVER writes
                                        to this directly. The designer
                                        edits it freely for everything
                                        outside control axes.
  Crispy-avar.csv                     ← AVAR2 MAPPINGS staging (existing).
  Crispy-control.json                 ← CONTROL AXES sidecar — canonical
                                        source of truth for declared
                                        control axes + their per-glyph
                                        outline overrides.
  .avar2-studio/
    shadow/
      Crispy.glyphs                   ← derived. Original + sidecar
                                        applied. The preview always
                                        builds from this.
    build/
```

### Sidecar-as-source-of-truth (model α)

The sidecar is canonical; the shadow is derivable from
`original + sidecar`. This means:

- Blow away `.avar2-studio/` and the shadow regenerates from the
  original + sidecar with no data loss.
- When the designer edits the original (outlines, kerning, OT
  features), the studio detects the mtime change, regenerates the
  shadow from scratch, and re-applies the sidecar on top.
- When the designer draws brace layers in Glyphs.app (on the
  shadow), the studio captures the new outlines back into the
  sidecar as glif/layer snippets. The shadow can then always be
  re-derived.

### Sidecar JSON shape

Layers are stored per-glyph as an **array** keyed by location vector
(not stringified — the location is a JSON object so axis-order
quirks don't break lookups). The location is sparse: only axes the
designer explicitly pinned appear; unspecified axes interpolate at
build time.

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
      "coverage": ["A", "E", "F", "H", "e", "f", "t"],
      "layers": {
        "e": [
          {
            "location": { "crbr": -100 },
            "glif": "<glyph>... raw glif XML ...</glyph>"
          },
          {
            "location": { "crbr": 100 },
            "glif": "..."
          },
          {
            "location": { "crbr": -100, "XOPQ": 78 },
            "glif": "..."        // short crossbar at Light specifically
          },
          {
            "location": { "crbr": 100, "XOPQ": 407 },
            "glif": "..."        // long crossbar at Bold specifically
          }
        ],
        "f": [ /* … */ ]
      }
    }
  ]
}
```

Key choices:

- **Per-glyph array, not object keyed by stringified location.**
  Stringifying axis dicts is fragile — `{crbr:-100,XOPQ:78}` and
  `{XOPQ:78,crbr:-100}` mean the same thing but produce different
  string keys.
- **`location` is sparse.** Only axes the designer pinned appear.
  Other axes interpolate from masters / other brace layers at
  preview / build time.
- **`glif` is raw glif XML**, captured back from the editor on save.
  Sidecar is the canonical store (model α); shadow regenerates from
  `original + sidecar` on every original-mtime change.
- **`version: 1`** so future tooling can read/write the format.

### Lazy shadow creation

The shadow is **only created on the first control-axis action**. If
a font has zero control axes declared in `-control.json`, the shadow
directory doesn't exist and the preview builds from the original.
The moment the user clicks **+ Add Control Axis** (v2), the studio:

1. Copies the original source tree → `.avar2-studio/shadow/`
2. Applies the (empty) control axis declaration to the shadow's
   axis list
3. Switches the build path to point at the shadow

This avoids paying the shadow-creation cost for users who only use
AVAR2 MAPPINGS / instance editing.

### Drift between original and shadow

The original is the source of truth for everything *except* the
control-axis layer:

- Outline edits, kerning, OT features, master coordinates → happen on
  the original. The studio watches the original's mtime and
  regenerates the shadow on change.
- Control-axis layer (axis declarations + brace-layer outlines) →
  lives in the sidecar. Survives shadow regeneration because the
  shadow re-derives from `original + sidecar`.

The designer never has to think about syncing. They edit the
original for normal font work and the shadow for control-axis
drawings; the studio glues them together.

## Authoring round-trip (v2)

Control-axis editing in v2 looks like this:

1. **Declare** in the studio: `+ Add Control Axis` → modal captures
   tag/display name/min/max/default. Saved to `-control.json`.
2. **List coverage** in the studio: a text field per axis. Designer
   types glyph names (one per line, optional `# group comments`).
   Saved to `-control.json`.
3. **Studio writes seed brace layers** to the shadow. Each coverage
   glyph gets layers at axis-min and axis-max by default, **at the
   interpolated outline for that location** (not the default master
   copy). The preview now shows the axis as a slider that doesn't
   visually deform anything yet (because the layers match what would
   have been interpolated anyway).
4. **Add layers at custom locations** — per-glyph "+ Add layer at
   custom location…" button opens a modal with one input per axis
   (parametric + AVAR2 MAPPINGS + control). Designer pins whichever
   axes they want; control axis is required non-default. Studio
   writes a seed layer at that full location vector. The "seed =
   interpolated value at the location" rule means the layer is a
   no-op visually until edited, which avoids surprise visual snaps.
5. **Open shadow in editor** — a button per layer (per location).
   Opens Fontra (Path 2 — iframe) navigated to that glyph at that
   exact location. Designer draws the alternate outlines.
6. **Studio captures back into sidecar.** When the shadow's mtime
   changes (editor saved), the studio reads the brace-layer
   outlines for the coverage glyphs and stores them as raw glif XML
   in the sidecar's `layers` block, keyed by the location vector.
   The shadow is now derivable from `original + updated sidecar`.
7. **Iterate.** Steps 4–6 repeat as the designer refines outlines or
   adds/removes layer locations.
8. **Push to source** — explicit user action. The studio applies the
   sidecar's axis declarations + brace layers to the original. After
   a successful push the axis transitions from orange (in-sidecar
   only) to green (in source). The sidecar entry can be cleared or
   kept as a record.

Same tri-state sync semantics as instances:

| Dot | Sidecar | Original |
|---|---|---|
| 🔴 Red | edits in flight (modal open, unsaved coverage edit) | unchanged |
| 🟠 Orange | axis declared + outlines captured | original doesn't know about this axis yet |
| 🟢 Green | sidecar reflects what's already in the original | axis + brace layers present |

Demote = "remove control axis from source": axis declaration + brace
layers stripped from the original; sidecar entry kept; axis flips
back to orange.

## Source-format scope for v2 authoring

Brace-layer authoring is structurally very different across the two
formats the studio supports:

### `.glyphs`

Brace layers live inside each glyph's `<layers>` block. Adding a
brace layer means appending one layer entry per coverage glyph,
keyed by location. `glyphsLib` already round-trips this. The shadow
file is a single `.glyphs` file copy; mutations are localised.

### `.designspace`

There are no brace layers as such — the equivalent is **a new UFO
master at the brace location**, containing only the coverage glyphs.
Other glyphs in that location interpolate from the existing masters
since the new UFO doesn't carry entries for them. This is the Roboto
Delta pattern.

Heavier than `.glyphs` because:

- Each unique brace location requires its own UFO directory with
  the minimum metadata files (`fontinfo.plist`, `metainfo.plist`,
  `layercontents.plist`, etc.).
- The studio has to **pool** UFOs across glyphs — if `e` and `f`
  both have a brace layer at the same location, they go in the
  same UFO, not two separate ones.
- The `.designspace` `<sources>` block gains an entry per pooled
  location; mutation has to keep that block consistent.

### v2 ships `.glyphs` authoring; `.designspace` defers to v2.5

v2 supports control-axis authoring on `.glyphs` sources only. The
"+ Add Control Axis" button is disabled with a tooltip explaining
why for `.designspace` sources. v1's read-only coverage panel still
works on `.designspace` — Roboto Delta's existing case-split
masters are visible there; users just can't author new ones in the
studio yet.

The deferral keeps v2 scope bounded. The UFO-pooling design + the
extra mutation surface adds roughly another week of work and a
class of edge cases (UFO naming conflicts, dedup logic) we'd
rather hit in v2.5 after the `.glyphs` flow is proven.

## Editing context inside the embedded editor (for later)

When Fontra is embedded for brace-layer editing (Path 2, v2),
designers will need more than a single-glyph editor view. Three
requirements parked here that v2's Fontra integration has to
handle:

### 1. Interpolation-compatibility validation

The hard rule of variable font masters: every drawing for a glyph
must have the same number of contours, the same number of points
per contour, and matching on-curve / off-curve types. A
well-meaning edit in Fontra (adding a point to a brace layer, for
example) can silently break interpolation.

The studio should validate compatibility on every Fontra save:

- Read the just-saved brace layer's glif from the shadow.
- Compare against the default master's outline structure for that
  glyph (contour count, points-per-contour, point types).
- On mismatch, raise a sidebar warning on the offending layer:
  `⚠ e @ {crbr:-100} — 4 contours, default master has 3`.
- Don't auto-revert. The designer might be mid-edit and adding a
  point intentionally; they need to fix it on the default master
  too, in which case the warning clears on next save.

Likely implementation path: `fontTools.pens.RecordingPen` to walk
each layer once and compare summaries.

### 2. Context-string editing

Designers don't edit `e` in isolation — they edit it next to `T`,
`h`, `qu`, `n`, etc. to see how the new crossbar reads in real
text. The "Open in editor" affordance should:

- Take a context string the designer types into the avar2-studio
  panel (default: `Adhesion` or whatever sample-text the studio's
  preview is using).
- Pass it to Fontra so the embedded editor renders the focus glyph
  surrounded by that context.
- Fontra's text-editing view ("text" mode) already supports this;
  we need URL params or a `postMessage` to drive it.

### 3. Axis-aware context rendering

Context glyphs around the focus should render at **the axis
parameters set in the avar2-studio UI** (the slider values the
designer has dialled in the sidebar), NOT at the focus glyph's
brace-layer location.

So if the designer is editing `e @ {crbr:-100, XOPQ:78}` while the
avar2-studio sliders are set to `XOPQ=187, XTRA=290, YOPQ=130,
crbr=0`:

- The **focus glyph** (the `e` being edited) renders at its
  brace-layer location: `{crbr:-100, XOPQ:78, XTRA=290, YOPQ=130}`
  (other axes interpolating).
- The **context glyphs** (`T`, `h`, etc.) render at the UI's
  current settings: `{XOPQ=187, XTRA=290, YOPQ=130, crbr=0}`.

This lets the designer see how the alternate reads inside text the
font typically renders at, not inside text frozen at the alternate
location. It also exposes regressions: if the new crossbar makes
`e` look out of place at Regular weight specifically, the
designer sees that immediately.

Likely implementation: extension of the Fontra URL / message
protocol to pass two location vectors — `focus_location` and
`context_location`. Whether Fontra natively supports per-glyph
location overrides in its text view is unverified; if not, we
either contribute the support upstream or fall back to "focus
glyph in isolation" with the context coming from a parallel
avar2-studio preview pane.

These three requirements are not v2 day-one — the day-one v2 ships
with the basic Fontra iframe and a single-glyph view. But the
sidecar / URL / message protocol design should leave room for them
so v2.x can land them additively.

## Disable in preview (v1)

Each control axis row has a small toggle — eye icon, "disable" label,
keyboard shortcut TBD. When toggled off:

- The slider for that axis is forced to the axis's `default` value at
  preview render time, regardless of any user-set value or CSV row.
- Implementation: frontend-only. The font-variation-settings string
  passed to the preview overrides the disabled axis's value with the
  default before rendering.
- The state is **session-local** (not persisted) in v1. v2 can
  persist it in the sidecar if useful.

The toggle answers "what does my font look like *without* this
control axis's deformation?" — useful for sanity-checking that the
axis isn't causing regressions on non-coverage glyphs, or for
comparing against a baseline.

## What v1 actually delivers

Strictly read-only. v1 makes the studio able to **show** any
glyph-scoped variation that already exists in the source file.

### Backend

- New module `glyph_coverage.py` with two functions:
  - `coverage_from_glyphs(font)` — walk each glyph's layers, collect
    intermediate-position layers, return `{axis_tag: [glyph_name]}`.
  - `coverage_from_designspace(ds)` — walk masters at non-default
    locations, intersect their UFO glyph sets with the axis tags
    that vary at that location.
- New endpoint `GET /api/glyph-coverage`:
  ```jsonc
  {
    "axes": [
      {
        "tag": "XOPQ",
        "name": "X-Opacity",
        "covers": ["A", "B", ..., "z"],
        "covers_count": 245,
        "total_glyphs": 245,
        "kind": "universal"          // computed: full coverage
      },
      {
        "tag": "CRBR",
        "name": "Crossbar",
        "covers": ["A", "E", "F", "H", "e", "f", "t"],
        "covers_count": 7,
        "total_glyphs": 245,
        "kind": "scoped"             // small named subset → control-axis-shaped
      }
    ]
  }
  ```
  - `kind: "universal"` → axis stays under AVAR2 MAPPINGS / parametric
  - `kind: "scoped"` → axis surfaces under CONTROL AXES
  - `kind: "partial"` → most glyphs but not all — flagged as a smell

### Frontend

- New `CONTROL AXES` section in the sidebar, sibling to AVAR2 MAPPINGS.
- Each axis row: tag, display name, coverage count badge, expand
  caret. Expanded view shows the glyph list (plain text for v1) and
  the disable-in-preview toggle.
- No editing affordances — no +Add, no coverage editor, no push to
  source. v1 is pure read.

### Out of v1

- The `-control.json` sidecar
- The shadow source file
- `+ Add Control Axis` modal
- Coverage editor
- The "Open in editor" button
- Push-to-source / demote flows
- Persisted disable state

## Fontra in v2 — scoping

The most awkward part of v2 as drafted is step 4 of the authoring
loop — the Glyphs.app round-trip. Switching apps to draw an outline,
saving, then coming back to see the preview update is tolerable but
not great. [Fontra](https://github.com/fontra/fontra) is a candidate
to replace that round-trip with inline editing.

### What Fontra actually is

A web-based font editor structured the same way as avar2-studio: a
Python server + JavaScript client. Default port 8000. Reads
`.designspace`, `.ufo`, `.ttf`, `.otf` out of the box; pluggable
backend system for new formats. Crucially, Fontra exposes four
plug-in APIs via Python entry-points (documented in
[`docs/plugin-structure.md`](https://github.com/fontra/fontra/blob/main/docs/plugin-structure.md)):

| Plug-in API | Surface |
|---|---|
| **Project Manager** | controls how fonts are loaded + the project-pick UI |
| **View** | bundles web assets (HTML/CSS/JS) as a Python package, registers a URL prefix |
| **Filesystem backend** | handles a new source format |
| **Static content** | adds a virtual web folder |

The View plug-in mechanism is what makes embedding plausible — we
register avar2-studio's React bundle as a view, Fontra serves it
under a URL prefix alongside its own editor. One server, one tab.

### Four integration paths, scoped

| Path | Approach | Effort | Difficulty | UX win |
|---|---|---|---|---|
| **1. Separate-tab Fontra** | Launch Fontra on its own port; "Open in editor" opens Fontra in a new browser tab pointed at the shadow. Studio's mtime watcher picks up changes. | ~2 days | low | marginal — still a context switch, just to a tab |
| **2. iframe Fontra inside avar2-studio** | Run Fontra alongside us; iframe it into a panel. Navigate via URL (`?glyph=e&location=crbr:-100`); communicate via `postMessage` if needed. | ~1 week | medium | inline editing in the same page |
| **3. avar2-studio as a Fontra view plug-in** | Repackage avar2-studio as a Python package with a `fontra.views` entry-point. Bundle our React app under a URL prefix Fontra serves. Drop our Flask server; talk to Fontra's project manager. | ~3 weeks | high | one server, one tab, shared backend |
| **4. Fontra's backends as a library dep** | Keep our Flask server; replace direct `glyphsLib` / `designspaceLib` reads with calls to `fontra.backends.designspace.DesignspaceBackend`. No UI change. | ~1 week | medium | invisible to user; sets up path 3 later |

### Recommended for v2

**Path 2 (iframe).** It's the smallest path that meaningfully beats
the Glyphs.app round-trip. Concretely:

1. Add Fontra as a Python dependency (`pip install fontra`).
2. On first control-axis action, spawn the Fontra server as a
   subprocess on port `8001` (or any free port), pointing it at the
   shadow folder.
3. The "Open in editor" button opens an iframe (or a side-drawer
   modal) loading `http://127.0.0.1:8001/fontoverview?...` filtered
   to the axis's coverage glyphs. Fontra navigates to a glyph in
   response to URL query params; we use that for jump-to-glyph.
4. mtime-watcher (already in the studio) picks up the shadow file's
   saves and re-captures brace-layer outlines into the sidecar.
5. Closing the iframe / drawer doesn't kill Fontra — keep it warm
   for the next "Open in editor" click.

### Risks and unknowns for Path 2

- **Does Fontra accept jump-to-glyph URL params?** Likely yes (the
  editor URL is its primary navigation surface), but worth verifying
  before committing. If not, navigation is a `postMessage` away or
  we just open Fontra's overview and let the user click in.
- **Cross-origin iframe behaviour.** Both servers are on
  `127.0.0.1`; default headers may need a CORS / `frame-ancestors`
  loosen. Fontra is open-source and likely amenable to a config
  flag, but not yet verified.
- **postMessage protocol.** If we want bidirectional state
  (clicking a coverage glyph in our panel → Fontra navigates to it),
  we need a small message protocol. Fontra doesn't appear to
  document one publicly; we'd be defining it. Fallback: pure URL
  navigation, less interactive.
- **Save flow.** Fontra writes to disk on save. Our mtime watcher
  handles that, but two file-watchers (Fontra's + ours) can race on
  the same file. Path 2 may need explicit save coordination.

### Why not Path 3 in v2

Path 3 (avar2-studio as a Fontra view plug-in) is the cleanest end
state but requires:

- Replacing our Flask server with Fontra's aiohttp-based one
- Restructuring our React build output as a Python package
- Adopting Fontra's project-manager and font-loading model — which
  means rewriting `_apply_source_path`, `_get_avar2_csv_path`, and
  most of the source-load plumbing
- Living with Fontra's port and routing decisions

That's a v2-sized project on its own. We'd be hosting both products
in one v2 and likely shipping neither well. Path 2 keeps v2's scope
bounded; Path 3 is the natural v3 if Path 2 proves the value.

### Why not Path 4 either

Path 4 (use Fontra's backends internally) doesn't deliver the editor
that motivates the whole exercise. Useful as a future migration step
toward Path 3 but invisible to the v2 user.

### Design implications for v2 sidecar format

If we expect to migrate toward Path 3 eventually, the sidecar
schema should stay simple enough that Fontra could become a second
consumer:

- Keep the layer-snapshot format inside `-control.json` as raw glif
  XML strings (Fontra's data model is close enough that a converter
  is shallow).
- Don't bake avar2-studio-specific metadata into the per-axis
  records that Fontra would have no way to interpret.
- Treat the sidecar as a stable file format with a `version` field,
  so future tooling can read/write it.

## What stays untouched

- The existing AVAR2 MAPPINGS section, `-avar.csv`, and instance flow
  all continue to work exactly as today. No shadow file is interposed
  for instance editing; "Save to source file" continues to write
  directly. The shadow strategy is reserved for control axes where
  the iteration loop and structural risk justify it.

## Open questions still

- **Coverage compute perf** — Roboto Delta full source has ~3000
  glyphs × ~20 UFOs. Iterating every master's UFO at every font load
  may be slow. Cache by `.designspace` mtime? Compute lazily when the
  CONTROL AXES panel is expanded?
- **Coverage editor text format** — newline-separated with `# group
  comments` for v2? Regex / range support (`A-Z`, `[aeiou]`)?
- **Composite-glyph inheritance** — when `e` covers the CRBR axis and
  `é` is a composite referencing `e`, does `é` inherit coverage
  automatically? At the gvar level yes, but should the studio's
  coverage panel show it as covered directly, or just as
  "inherited"?
- **Push-to-source granularity** — push one control axis at a time,
  or all at once? The instance flow pushes per row; control-axis push
  might be naturally per-axis (an axis is the unit of
  studio-declared-vs-in-source state).
