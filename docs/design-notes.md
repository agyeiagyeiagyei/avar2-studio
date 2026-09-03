# Secondary parametric axes — design notes

The design record behind [secondary parametric axes](secondary-parametric-axes.md):
what isn't built yet, and the alternatives weighed along the way. The user
reference is the sibling doc; how the *shipped* code works under the hood is in
[HANDOVER.md](HANDOVER.md).

## Naming seam

The product term is **secondary parametric axes**. The code, API routes
(`/api/control-axes`), frontend components, and the `-control.json` sidecar keep
the older internal name **control axes** — so any code, route, or filename
quoted in the docs uses `control`.

## Shipped vs. not

**Shipped:** read-only coverage; `+ Add` / edit / delete of studio-declared
axes; applicable-glyph & brace-layer authoring via `-control.json` + a shadow,
on `.glyphs` (brace layers) and `.designspace` (pooled sparse UFO sources);
inline outline editing through an embedded Fontra drawer with studio-restricted
multi-source batch editing; disable-in-preview.

**Not built:** push-to-source / demote sync and the tri-state badge for
secondary axes; and the editing-context trio (interpolation-compat
validation, context-string editing, axis-aware context rendering).
Model α is half-landed — see below.

## Model α vs. model β — where outlines live

- **Model β (current behaviour).** The sidecar stores only axis
  declarations + brace-layer *locations* — no glif/outline XML by
  default. Drawn outlines live only in the shadow `.glyphs`; they
  survive regeneration because `regenerate_shadow` reads them back out
  of the *previous* shadow. **Wiping `.avar2-studio/` loses drawn
  outlines** — with no outline in the sidecar and no prior shadow,
  every brace layer re-seeds as a copy of the default master.
- **Model α (half-shipped).** The sidecar schema carries an `outline`
  value-dump (paths/nodes, components, anchors, width), and
  `regenerate_shadow` restores it ahead of the prior-shadow copy and
  any seed — a sidecar with outlines regenerates the drawings with the
  shadow wiped. `capture_outlines` does the shadow → sidecar copy,
  skipping untouched seeds (via a seed signature stamp) and correction
  layers (recomputed anyway). **But no server path calls it yet** — the
  wiring (e.g. on Fontra drawer close) is the missing slice, so model β
  behaviour is what users get today.

## Not-built roadmap

- ~~**Original↔shadow auto-sync**~~ **Shipped.** The watcher matches the
  original's path too (and watches its directory); a change there runs
  `_resolve_active_source()` — regenerating the shadow from the original —
  then syncs the CSV and rebuilds.
- **Capture-back into sidecar (model α).** Half-shipped: the schema, the
  `capture_outlines` copy, and the restore in `regenerate_shadow` all
  exist (tested round-trip), but nothing invokes the capture — closing
  the Fontra drawer just rebuilds, so outlines persist only inside the
  shadow until the trigger is wired.
- **Push-to-source / demote.** No endpoint writes a studio axis into the
  original; add/delete mutate only the sidecar.
- **Sync-state tri-state.** The red/orange/green tri-state, SRC badge, and
  demote flow exist only for *instances*. Secondary-axis rows carry just the
  `studio` and `scoped` badges. A per-axis in-sidecar-vs-in-source tri-state
  depends on push-to-source landing first.
- **Editing-context trio.** Not built: (1) interpolation-compat validation on
  Fontra save (compare the brace layer's contour/point structure against the
  master); (2) context-string editing (render the alternate inside `Adhesion`);
  (3) axis-aware context rendering (focus glyph at its brace location, context
  glyphs at the sliders' current values) — would need two location vectors
  through the Fontra protocol.
- **Persisted disable-in-preview.** The eye-toggle state is session-local (a
  React `Set`), not saved to the sidecar or localStorage.

## Fontra integration — paths considered

Path 2 shipped (embedded same-origin drawer); the rest are design-only.

| Path | Approach | Status |
|---|---|---|
| 1. Separate-tab Fontra | Own port; "Open in editor" opens a new tab. | design-only |
| 2. iframe / same-origin proxy | Run Fontra alongside and embed it. Shipped as a same-origin reverse proxy + focused UI + drawer + fragment nav. | **shipped** |
| 3. avar2-studio as a Fontra view plug-in | Repackage the React app as a `fontra.views` entry-point; drop Flask; one server, one tab. | design-only (natural v3) |
| 4. Fontra backends as a library dep | Keep Flask; read via `fontra.backends` instead of `glyphsLib`/`designspaceLib`. | design-only |

Path 3 is the cleanest end-state but means replacing Flask with Fontra's
aiohttp server, repackaging the React build as a Python package, and adopting
Fontra's project-manager model — a project on its own, with a real risk of
shipping two half-products. Path 2 bounds scope and proves the value first.
Path 4 doesn't deliver the editor that motivates the work; it's a possible
invisible step toward Path 3. If Path 3 is ever pursued, keep the sidecar
simple enough to be a second consumer (stable `version`, no studio-specific
per-axis metadata); the model-α `outline` field it would read already exists
in the schema.

## Open questions

- **Coverage-compute perf** — Roboto Delta's full source is ~3000 glyphs × ~20
  UFOs; iterating every master per load may be slow. Cache by mtime, or compute
  lazily when the panel expands?
- **Composite inheritance** — if `e` covers `crbr` and `é` composes `e`, is `é`
  covered directly or "inherited"?
- **Push-to-source granularity** — per-axis (likely) or all at once?
