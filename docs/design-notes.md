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
secondary axes; capturing drawn outlines back into the sidecar ("model α");
mtime-driven shadow regeneration; and the editing-context trio
(interpolation-compat validation, context-string editing, axis-aware context
rendering).

## Model α vs. model β — where outlines live

- **Model β (ships).** The sidecar stores only axis declarations + brace-layer
  *locations* — no glif/outline XML. Drawn outlines live only in the shadow
  `.glyphs`; they survive regeneration because `regenerate_shadow` reads them
  back out of the *previous* shadow. **Wiping `.avar2-studio/` loses drawn
  outlines** — with no glif in the sidecar and no prior shadow, every brace
  layer re-seeds as a copy of the default master.
- **Model α (goal).** Capture drawn outlines back into `-control.json` as glif
  snippets, so the shadow is fully re-derivable from `original + sidecar` with
  no data loss. This is the prerequisite for a truly disposable `.avar2-studio/`,
  for original↔shadow auto-sync, and for a future Fontra Path 3 reading outlines
  from the sidecar.

## Not-built roadmap

- **Original↔shadow auto-sync.** No mtime-driven `regenerate_shadow`: the
  watcher re-syncs the CSV and triggers a build but never regenerates the
  shadow, and when the shadow is the active build path the watcher observes the
  shadow, not the original. After editing the original outside the studio, a
  secondary-axis action (or reload) is needed to fold the change in. A
  model-α-era goal.
- **Capture-back into sidecar.** Closing the Fontra drawer just rebuilds;
  outlines persist only inside the shadow (model β).
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
per-axis metadata) — and note model α is a prerequisite for Fontra reading
outlines out of it.

## Open questions

- **Coverage-compute perf** — Roboto Delta's full source is ~3000 glyphs × ~20
  UFOs; iterating every master per load may be slow. Cache by mtime, or compute
  lazily when the panel expands?
- **Composite inheritance** — if `e` covers `crbr` and `é` composes `e`, is `é`
  covered directly or "inherited"?
- **Push-to-source granularity** — per-axis (likely) or all at once?
