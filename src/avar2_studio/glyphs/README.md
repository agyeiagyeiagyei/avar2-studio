# Glyphs plugins

Glyphs 3 plugins that ship with avar2-studio: a hub that opens the studio
on the font you are editing, and six design tools for parametric
families (written for Crispy, generic since). The package is the source
of truth; the copies Glyphs loads are symlinks into it.

## Install

With avar2-studio installed (see the main README), run once:

```bash
avar2-studio install-glyphs-plugins
```

This links every bundle in this directory into
`~/Library/Application Support/Glyphs 3/Plugins/` and records which
Python runs the studio in `~/.avar2-studio/glyphs-plugin.json`. Restart
Glyphs. Because they are symlinks, a package upgrade — or an edit in a
checkout installed with `pip install -e` — is live after the next
Glyphs restart. `avar2-studio install-glyphs-plugins --uninstall` removes
the links and the record.

## The avar2 Studio menu (`Avar2Studio.glyphsPlugin`)

**Window → avar2 Studio** is the hub:

- **Open in avar2 Studio** — starts the studio on the current font's file
  (the font must be saved) and opens `http://127.0.0.1:5001` in the
  browser. A studio already serving that file — one started from a
  terminal included — is reused instead; if the port is busy with
  something else, the next free one up to 5010 is taken. The first build
  runs before the page loads; a notification reports the URL once it
  answers, or the failure, with the server's log tail in the Macro panel.
- **Stop avar2 Studio** — ends the server this menu started (it is also
  stopped when Glyphs quits). Servers started elsewhere are left alone.
- **Corner Radii**, **Instance Delta**, **Parametric Masters**, **Slant
  Master**, **Width Matcher** — toggle the reporters; the check mark
  mirrors View → Show ….
- **Multi-Source Edit** — selects the tool in the toolbar.

The reporters still appear under **View → Show …** and the tool in the
toolbar, exactly as when installed on their own.

## Corner Radii (`CornerRadii.glyphsReporter`)

Audits and edits rounded corners. Enable via **View → Show Corner Radii**;
a floating panel opens alongside the Edit view.

Every rounded corner in the current glyph is detected and least-squares-fit
with a circle. The overlay draws:

- **gray circle** — the current fitted radius (amber when the round is
  poorly circular: fit residual > 8% of the radius)
- **blue label** — the radius at the current scale factor
- **blue outline + handles** — live preview of the corner as it would look
  after scaling
- **red lens** (optional) — where a scaled outer round would collide with
  a counter round

Panel controls:

- **Outer × / Inner ×** — independent scale factors for exterior-contour
  vs counter corners (fields, or −/+ in 0.05 steps). **Reset ×** restores
  1.00.
- **Show** toggles — Circles, Handles, Outlines; **Flag outer/inner
  overlaps**; **Baseline corners only** (restricts detection to corners
  whose virtual corner sits within ±10 units of y = 0).
- **Apply to** — scope: Current glyph / All glyphs / All glyphs, this
  master (the "→" hint shows which master was resolved) / Entire font,
  plus per-master checkboxes. Font-wide scopes also transform
  brace/bracket layers.
- **Apply** — rewrites node positions in place, keeping the same node
  slots, so masters stay interpolation-compatible.
- **Sharpen** — replaces each round with a single sharp corner node at the
  virtual corner (batched equivalent of Glyphs' "Sharpen Corners").

All edits are wrapped in one undo group per layer. The geometry core is
`Contents/Resources/cornerfit.py`, a pure-Python module with no Glyphs API
dependency (nodes are duck-typed), so it can also run under glyphsLib or
in scripts.

## Instance Delta (`InstanceDelta.glyphsReporter`)

Draws a comparison glyph — from a master or an interpolated instance —
behind the glyph being edited, and reports the advance-width difference.
Enable via **View → Instance Delta**.

- **Compare** popup lists `Master: …` then `Instance: …` entries. A master
  is read straight off the glyph (exact, free); an instance is
  interpolated once via `instance.interpolatedFont` and cached —
  re-interpolated on selection change, font change, or the **Refresh**
  button. The Width Matcher scratch instance is excluded from the list.
- Both outlines share x = 0, so the advance delta reads directly as the
  gap between the gray edit-advance marker and the colored
  instance-advance marker (**Advance markers** toggles them).
- The overlay draws for every glyph in the tab; the readout tracks the
  active glyph.
- Closing the panel with the red X turns the reporter off (panel and
  overlay) until it is re-selected in the View menu.

## Multi-Source Edit (`MultiSourceEdit.glyphsTool`)

A Select-tool variant that propagates node drags across masters. Pick it
in the toolbar; its floating panel lists a **Sync edits** toggle plus one
checkbox per master (all checked by default).

With sync on, dragging a node applies the same delta live to the node at
the same (path, node) index in every checked master of that glyph. The
delta is read back from the first selected node in the active layer, so
snapping and constraints are captured; a multi-node drag syncs as a rigid
translation. Only point moves are synced — structural edits (adding or
deleting nodes) are not. Each drag closes as one undo step per target
layer. Closing the panel turns sync off; selecting the tool again
re-shows it.

`Contents/Resources/Icon.glyphs` is the design source for the toolbar
icon (`toolbarIconTemplate.pdf`); it is not used at runtime.

## Parametric Masters (`ParametricMasters.glyphsReporter`)

Audits parametric-master consistency in real time. Enable via **View →
Parametric Masters**; a floating panel opens alongside the Edit view.
Nothing is drawn into the Edit view.

The rule: masters that share the axis values driving horizontal metrics
should share horizontal metrics. Masters are grouped by a pair of axes —
**XTRA + XOPQ** by default (the horizontal transparent and opaque
factors) — and any glyph whose advance width, LSB, or RSB differs within
a group is flagged.

- **Group by** popup offers every pair of the font's axes, so other
  hypotheses (e.g. XTRA + YOPQ) can be checked too.
- Font-wide by default; **Current glyph only** narrows the audit to the
  Edit view's glyph.
- **Live** updates while you draw (throttled to one scan per second);
  **Refresh** forces a rescan.
- Only groups of 2+ masters are audited. Consensus is the first member's
  metrics; deviations over 1 unit are flagged. Each row names the group by
  the pair's tagged values (`XTRA 47 · XOPQ 1462`), lists the members with
  the coordinates that tell them apart (`47-1462-1 (YOPQ 1) · 47-1462-275
  (YOPQ 275)`), and reports the largest advance, LSB and RSB deviation
  from the first member. Double-click a row to open the glyph at the
  group's first master.

## Slant Master (`SlantMaster.glyphsReporter`)

Starts the italic masters: shears every glyph of each chosen source — a
master, or an instance interpolated on the fly — by that source's own
angle and appends each result as a **new master**, leaving the uprights
untouched. Enable via **View → Slant Master**; a floating panel opens
alongside the Edit view and the result previews live over the glyph you
are editing.

- The **list** has one row per source, `Master: …` then `Instance: …`
  (Width Matcher's scratch instance excluded), with a tick box and an
  **Angle°** (default 10). Upright masters start ticked; masters that
  are slanted already, and instances, are opt-in. Double-click an angle
  to change it; **Set all angles to** writes one value into every row.
  An angle is kept in the source file with its master or instance
  (`userData`, key `xyz.avar2studio.slant-angle`), so it is there in the
  next session; ticks last for the session.
- **W %** and **H %** scale around the origin; **Origin** picks the shear
  pivot: baseline, x-height ÷ 2 (default) or cap-height ÷ 2, read from
  each source master's metrics, so stems stay visually centred. These
  apply to every row.
- **Decompose components** (on) flattens composites before the shear; off,
  components stay attached with their translation re-derived (a
  component whose linear part is not the identity is decomposed anyway,
  and counted).
- **Fix extrema** (off by default) inserts nodes at the new left/right
  extrema of the sheared curves, removes the stale ones where a
  least-squares refit stays within 1 unit (curve-to-stem joins are kept),
  and evens out the handles next to the new nodes. It changes the node
  structure, so the new master stops interpolating with the uprights
  until they are redrawn to match — the status lists those glyphs. The
  check compares the node types path by path, not the counts: inserting
  an extremum and removing the stale one leaves the count alone and
  still moves the start node.
- **Set italic angle on new master** (on) writes each row's angle to its
  master, which is what makes Glyphs measure its sidebearings along the
  slant. When the font declares an `ital` or `slnt` axis the **Axis**
  field sets the coordinate of **every** new master on it — one value,
  whatever the angles, because the slanted masters have to share a
  position on the axis. Its default is 1 for `ital` and, for `slnt`,
  minus the angle of the first ticked row; it follows the rows until
  something else is typed there. Without such an axis the masters share
  their sources' coordinates and the status says so.
- **Match widths** (on) spaces the new masters against a **Reference**.
  **Each source itself** (the default) spaces every new master against
  the upright it came from, so each keeps its own widths; picking a
  master or instance of this font, or of any other open document
  (`Doc.glyphs › Master: …`, values scaled by UPM), makes that the
  reference of every ticked row. Spacing uses Width Matcher's
  **Spacing** contracts — reference sidebearings, or the reference advance
  (plus **Adv offset**) with sidebearings redistributed proportionally /
  centred / keep-LSB. Glyphs the reference lacks fall back to their base
  glyph (`a.smcp` → `a`) or keep their sheared width; empty glyphs take the
  reference advance.
- The **overlay** follows the master being edited: it draws that master
  sheared by its row's angle in blue, placed where Apply will put it,
  and the reference in magenta when it is another master or instance,
  advance markers (gray = current, blue = planned, magenta = reference),
  and, with Fix extrema on, orange dots where nodes will be inserted
  (filled) or removed (hollow) and gray dots for kept joins. The planned
  sidebearings in the readout are measured the way Glyphs will measure
  the new master — along the slant, around half the x-height, when it
  is to carry the italic angle — and agree with what Apply writes to
  about a unit (Glyphs keeps sidebearings in whole units). A master
  whose row is not ticked, and an instance row, draw nothing. The layers
  are untouched until **Apply**.
- **Apply (n)** creates one master per ticked row, named
  `<source> <Name suffix>` (suffix `Italic` by default). Every node,
  anchor and kept component of the new layers is rounded to the font's
  grid (Font Info › Other; a grid of 0 leaves them as the shear put
  them), and the overlay shows and measures the rounded outline. It refuses
  before creating anything when a name is taken or the Axis value is not
  a number. A glyph that fails is named in the status, with the
  traceback in the Macro panel, and the rest still land. Each glyph's
  layer change is an undo step, but the masters themselves are not
  undoable — **Remove last** deletes the masters the last Apply created.
  Apply interpolates instance sources and references afresh;
  **Refresh** does the same for the overlay after edits.
- Extracted from the *Slant Glyphs* script in
  [docrepairtools](https://github.com/agyeiagyeiagyei/docrepairtools)
  (same author, relicensed Apache-2.0 here; inspired by filipenegrao's
  `slant_glyphs.py`). The math lives in `slant_math.py`,
  `slant_extrema.py` and `slant_paths.py` beside the plugin, covered by
  `tests/test_slant_master_*.py`; `test_slant_master_plugin.py` drives
  the panel glue with Glyphs stood in for.

## Width Matcher (`WidthMatcher.glyphsReporter`)

Creates a new master whose advance widths match an existing reference
master's. Enable via **View → Width Matcher**. It draws nothing into the
Edit view; everything happens in the panel.

- **Reference** popup picks the master to match.
- **Axis sliders** (one per design axis, with numeric fields) drive a real
  GSInstance named *Width Matcher Preview* kept in `font.instances`, so
  Glyphs' own interpolation engine (brace layers included) produces the
  generated outlines. Slider range runs from the master minimum to 3× the
  master maximum, allowing extrapolation. The instance is visible in Font
  Info while the tool is in use.
- The **preview** draws the reference glyph (gray) and generated glyph
  (blue) ink-centered on each other, with markers at both advance boxes.
  Readouts: advance Ref vs Gen with Δ, ink width Ref vs Gen with Δ (the
  value you drive to zero by nudging sliders), and a "Saved: LSB/RSB/Adv"
  line predicting what Save will actually write.
- **Spacing** popup picks the saved master's spacing contract: reference
  sidebearings verbatim, or reference advance (plus **Adv offset**) with
  sidebearings redistributed proportionally / centred / keep-LSB. Empty
  glyphs take the reference advance.
- **Save as Master** interpolates the working instance, appends it as a
  new master, copies every glyph's layer across, and re-spaces each layer
  per the chosen mode. Afterwards it re-audits sidebearings (metrics keys
  can rewrite them when the interface updates resume) and reports drift
  in the status line.

## Development notes

- All seven bundles follow the standard Glyphs 3 Python plugin template
  (bundle + stub loader, `NSPrincipalClass`, `PyMainFileNames`); the
  six tools open vanilla `FloatingWindow` panels, the hub has no UI
  beyond its menu.
- The hub (`Avar2Studio.glyphsPlugin`) is a General plugin: `start()`
  appends the submenu to the Window menu. Reporter items look the
  instance up in `Glyphs.reporters` by class name and call
  `Glyphs.activateReporter` / `deactivateReporter`, so their check
  marks stay in step with the View items; the tool item calls the
  window controller's `setToolForClass:`. Launching lives in
  `studio_launcher.py`, pure Python (covered by
  `tests/test_glyphs_launcher.py`): it reads
  `~/.avar2-studio/glyphs-plugin.json` (written by the installer),
  reuses a studio already serving the font on ports 5001–5010,
  otherwise runs `python -m avar2_studio SOURCE --port N` with the
  interpreter's `bin/` first on PATH (Finder-launched apps do not see
  the shell's PATH, and the server finds `fontc` through it), waits
  for `/api/health`, then opens the browser. The server's output goes
  to `~/.avar2-studio/glyphs-plugin.log`; the server is stopped from
  the menu or when Glyphs quits. Glyphs' own Python runs this code, so
  it stays 3.8-compatible.
- View toggles reach a reporter through `willActivate` /
  `willDeactivate`, which Glyphs calls on the plugin instance (the
  selectors are in GlyphsCore, and RedArrow relies on them). The SDK's
  `ReporterPlugin` does not forward them to `activate()` /
  `deactivate()` — only `SelectTool` does — so the reporters implement
  the two methods directly, as real ObjC selectors (no
  `@objc.python_method`). `willActivate` builds and shows the panel,
  `willDeactivate` hides it. A panel's red X calls
  `Glyphs.deactivateReporter(self)`, the SDK's own API, so the View item
  follows; the next `willActivate` rebuilds the window. `foreground()`
  also shows the panel lazily, as a fallback.
- Glyphs remembers enabled reporters across launches in its
  `visibleReporters` default and re-enables them at start-up — that, not
  a launch-time `activate()` call, is what used to put panels on screen
  before anything was asked for. These panels are wanted on demand only,
  so each reporter drops itself from that list in `start()`
  (`_forgetRestoredToggle`); the next View click puts it back for the
  session. Do not reintroduce menu-item trampolines or draw-timing
  heuristics for this: Glyphs uses separate `activateReporter:` /
  `deactivateReporter:` actions and rebuilds the reporter menu, so a
  hooked item goes stale. (Multi-Source Edit is a toolbar tool without a
  View item; it keeps a 2 s activate grace.)
- Each `plugin.py` has a module-level `DEBUG = False`. Flip it to `True`
  to append instrumentation to `/tmp/<plugin>-debug.log` while
  developing; it ships off.
