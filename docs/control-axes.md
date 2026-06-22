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

The sidecar JSON shape:

```jsonc
{
  "axes": [
    {
      "tag": "crbr",
      "display_name": "Crossbar",
      "default": 0,
      "min": -100,
      "max": 100,
      "coverage": ["A", "E", "F", "H", "e", "f", "t"],
      "layers": {
        // Per-glyph, per-axis-position outline snapshots captured
        // back from the shadow file. v2 fills these in as the
        // designer draws.
        "e": {
          "-100": { "/* glif outline data */": "..." },
          "100":  { "/* glif outline data */": "..." }
        }
      }
    }
  ]
}
```

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
3. **Studio writes seed brace layers** to the shadow: every coverage
   glyph gets a layer at axis-min and axis-max, initially identical
   to the default master. The preview now shows the axis as a slider
   that doesn't visually deform anything yet (because the layers
   match the default).
4. **Open shadow in Glyphs.app** — a button per control axis. The
   designer draws the alternate outlines for each coverage glyph at
   the min and max positions.
5. **Studio captures back into sidecar.** When the shadow's mtime
   changes (Glyphs.app saved), the studio reads the brace-layer
   outlines for the coverage glyphs and stores them in the sidecar's
   `layers` block. The shadow is now derivable from `original +
   updated sidecar`.
6. **Iterate.** Steps 4–5 repeat as the designer refines outlines.
7. **Push to source** — explicit user action. The studio applies the
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
