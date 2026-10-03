# avar2-studio

Visual authoring and preview tool for avar2 variable fonts.

For type designers working in parametric designspaces (XOPQ / YOPQ /
XTRA, …) who want to expose familiar axes (`wght`, `wdth`, `opsz`, …)
to end users. Author named instances at parametric coordinates;
avar2-studio builds the avar2 table that makes a user's `wght=400`
slider land on the point you chose.

Point it at a `.glyphs` or `.designspace` file and it opens a
browser-based editor on the **actual built font**. Declare
**secondary parametric axes** that deform only chosen glyphs, chain
optional **post-build transforms** onto every build, and — with
[Fontra](https://github.com/fontra/fontra) installed alongside — draw
those glyphs' brace layers in an embedded editor.

![avar2-studio — the Instances tab, each instance rendering live in the built font](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/instances-overview.png)

## Try it in the browser

The [hosted demo](https://agyeiagyeiagyei.github.io/avar2-studio/) runs
the whole studio in your browser, nothing to install. It opens on the
bundled Crispy Mini project; **Load Font ▾ → Examples** also has Roboto
Delta Mini. Both are live projects: edit instances and mappings, declare
secondary axes, grade styles, change transform parameters, rebuild —
compiled in the browser by [fontc](https://github.com/googlefonts/fontc)
built to WebAssembly (a 7 MB download, fetched once). Your work persists
in the browser between visits until you choose **Forget this project**.

To work on your own font, use **Load Font ▾ → Upload .glyphs or project
.zip…** (a file picker; there is no drag-and-drop). Loose files can be a
`.glyphs` with its `-avar.csv` and `axis-metadata.json` beside it. The
other sidecars — `-control.json`, `-transforms.json`, `-grade.json` —
travel only inside a project `.zip` (zip the folder your source sits
in), or come in through **Config ▾ → Import configuration…**.

What the browser cannot do, compared with the installed app:

- write anything back to your source file — get your work out with
  **Config ▾ → Download workspace (.zip)…** or **Export configuration…**
- recompile a `.designspace` project: fontc-wasm cannot read UFOs from a
  filesystem, so those load from the built font inside their zip, and
  edits that need a rebuild are unavailable (Roboto Delta Mini included)
- open the outline editor — **now available**: the demo embeds the
  [fontra-embed](https://github.com/agyeiagyeiagyei/fontra-embed) bundle
  (Fontra client as a separate GPL sub-project, driven over postMessage);
  drawings stay source-level in the control-axis sidecar

![The Load Font menu: bundled examples, upload, forget](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/load-font-menu.png)

## Install

There are two ways in, and they are not interchangeable.

**The studio (GUI).** Install from PyPI. The package carries the built
frontend, and `fontc` and `gftools` come with it as dependencies:

```bash
pipx install avar2-studio
avar2-studio path/to/MyFont.glyphs       # or .designspace
```

The first build runs on launch; open `http://localhost:5001`. Launching
with no source opens a **Load Font** picker for your own file.

**Headless (CI, scripts).** Installing from git gives you `build` and
`doctor` but **no frontend bundle** — the server answers 503 until you
build one yourself (see [Development](#development)):

```bash
pipx install git+https://github.com/agyeiagyeiagyei/avar2-studio
avar2-studio build path/to/MyFont.glyphs --out fonts/
```

**Optional: the embedded outline editor.** Fontra is not a dependency.
To draw brace layers inside the studio, add it to the same environment
(`pip install` the same two URLs in a venv):

```bash
pipx inject avar2-studio git+https://github.com/fontra/fontra.git git+https://github.com/fontra/fontra-glyphs.git
```

`avar2-studio doctor` reports what is present. Compiling with fontmake
instead of fontc (`--no-fontc`) needs fontmake installed as well.

**In Glyphs.** The package also carries a set of Glyphs 3 plugins: a
**Window → avar2 Studio** menu that opens the studio on the font you are
editing, and six parametric design tools (Corner Radii, Instance Delta,
Multi-Source Edit, Metrics Parity, Slant Master, Width Matcher).
Link them into Glyphs once, from the same install:

```bash
avar2-studio install-glyphs-plugins    # then restart Glyphs
```

[src/avar2_studio/glyphs/README.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/src/avar2_studio/glyphs/README.md)
documents the menu and each tool.

## Your files

Everything the studio authors lives next to your source, named after
the source's stem, so it can be committed with it:

| File | Commit? | Holds | Written by |
|---|---|---|---|
| `MyFont.glyphs` / `.designspace` | yes, it's yours | the source | you; the studio only on an explicit row action (`Save to source file`, `Remove from source file`, delete with source) |
| `MyFont-avar.csv` | yes | avar2 mappings (schema below) | the Instances tab; backed up before every rewrite |
| `MyFont-control.json` | yes | secondary parametric axes: applicable glyphs, layer locations, drawn outlines | the SECONDARY PARAMETRIC AXES panel and the outline editor |
| `MyFont-transforms.json` | yes | which transforms are on, with their parameters | the Transforms menu |
| `MyFont-grade.json` | yes | grade on/off, default %, intensity, headroom clamp, per-style anchors (`.glyphs` sources only) | the Grade toggle and the `G` badges |
| `.avar2-studio/` | no, gitignore it | `build/`, `shadow/`, `config.yaml`, `backups/` (the last 20 of each sidecar), `axis-metadata.json` (display names and ranges of the axes you declare) | the studio |

Studio axis authoring never edits your source: once studio-authored
brace layers exist, builds compile from a shadow copy in
`.avar2-studio/shadow/`, regenerated from your source every time you
save it in Glyphs. A few error messages still call the mappings file by
its old name, `avar2-mappings.csv`; the file the studio reads is
`MyFont-avar.csv`.

### The mappings CSV

`MyFont-avar.csv` is the authoring source of truth: one row per named
instance, one column per axis. Crispy's, abridged:

```
Instance Name,XTRA,XOPQ,YOPQ,WGHT,WDTH,OPSZ
Def,663.1,202.5,121.8,400.0,100.0,48.0
Width Matcher Preview,47.0,1462.0,275.0,,,
```

- `Instance Name` is required, spelled exactly like that.
- Columns named `WGHT`, `WDTH`, `OPSZ`, `CONTRAST`, or ending in `-E`,
  are **user axes** (they become the registered tags, `wght` and so
  on); every other column is a **parametric axis** the source compiles.
- A row with user-axis values is an avar2 mapping: those user
  coordinates land on the parametric point in the row. A row with
  empty user cells, like the second one, is a plain parametric instance.
- A blank parametric cell is an error; a blank user cell is skipped for
  that axis. Rows that resolve to the same input location are
  deduplicated (the first wins), and `opsz` drops out of the mapping
  when every row shares one value.
- `SPAC` is never a column: spacing comes from the transforms.

## Features

### Instances & avar2 mappings

The **Instances** tab is the authoring surface: create named instances
and tune each one's parametric coordinates with the sidebar sliders;
each row renders live in the built font. Give an instance user-axis
coordinates under **AVAR2 MAPPINGS** and it becomes an avar2 mapping.
[docs/authoring-instances.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/authoring-instances.md) walks the
full workflow.

![The Instances sidebar: sample text, core and secondary parametric axes, and the avar2 mappings of the selected style](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/instances-sidebar-mappings.png)

- **CORE / PARAMETRIC AXES** deform every glyph in the font;
  **SECONDARY PARAMETRIC AXES** deform only chosen glyphs (`scoped`
  and `studio` badges; next section).
- A row's `SRC` badge marks an instance that exists in the source. The
  coloured dot's flyout saves a row to the studio CSV, promotes it into
  the source, or removes it from there.
- **Mapping lint** flags rows the build would silently drop: grid
  points no axis reaches, and rows sitting at the default location
  (discarded, skewing every sibling row).

### Secondary parametric axes

Sometimes the global axes are *almost* right. Crispy's `wght` runs to
1000, and the heavy end gains contrast that sits wrong on the
lowercase — so Crispy declares `lcwd`, an axis that renders each
lowercase glyph there *as if at* a lighter XOPQ, while the capitals
stay put. A secondary parametric axis is a slider that deforms only
the glyphs you give it, built from brace layers pinned anywhere in the
designspace.

- **+ Add** to declare one: name it, pick its glyphs, pin the
  locations. New locations can land on every applicable glyph in one
  submit.
- **Correction layers** compute an outline *as if at* another
  parametric point, re-derived on every build; the studio warns when a
  correction is unpinned and would leak along an axis.
- Draw the other layers in the **embedded Fontra editor** (installed
  app with Fontra), against a reference font, with live measurements
  in the HUD.
- Everything lands in the `-control.json` sidecar, drawings included.

![The lcwd axis expanded: its applicable glyphs, their layers, and a correction layer computed as if at another XOPQ](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/secondary-axes-layers.png)

![At the ultra corner, engaging lcwd narrows the lowercase only; the capitals do not move](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/preview-lcwd.gif)

![Embedded Fontra editor: drawing a brace layer against a reference font, with live measurements. Installed app with Fontra only; not yet in the browser demo](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/fontra-reference.png)

Deep-dive:
[docs/secondary-parametric-axes.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/secondary-parametric-axes.md).

### Preview & export

The **Preview** tab drives the built font the way an end user's app
would: type directly in the specimen, move any fvar axis. Moving a
user axis reflects the computed avar2 mapping onto the parametric
sliders.

![The Preview tab at a mapped location: user axes, secondary axes, transform axes, and the parametric sliders following the mapping](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/preview-user-axes.png)

![Sweeping wght drives the parametric sliders through the avar2 mapping](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/preview-wght-sweep.gif)

A note on rendering engines, as of September 2026: the exported font's
avar2 works wherever avar2 is implemented, and HarfBuzz-based stacks
and recent WebKit/CoreText apply it. Chrome does not apply avar2 in its
text pipeline yet, so in the in-browser preview the glyphs follow the
fvar axes while the sliders show the computed mapping. What ships is
the real table either way.

**Download font…** opens an export dialog: mark axes as hidden in the
exported font, or **set a default** — pick an axis combination and the
export interpolates a master there and makes it the font's origin (the
source file is never touched).

![Export dialog: mark axes hidden, set the default location](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/export-modal.png)

### Space tab

The **Space** tab shows the designspace as an orbitable Noordzij cube —
N-dimensional, so a fourth master-covered axis renders as a tesseract
you can spin. Masters, brace layers and instances appear as points in
the axis box, and corner chips render live specimens at their exact
locations: red when a corner has no source coverage (a ghost), with
one-click pinning that **synthesizes the corner by extrapolation** when
no sweep reaches it (and refuses inline, with the reason, when it
can't). The findings rail lists the same audit — missing corners,
out-of-range sources, collapses — with the fixes alongside. Crispy has
no master at its wide, heavy, high-contrast corner, so it shows as a
ghost with a `Pin` button and a `Missing corner` finding.

![The Space tab: the designspace as an orbitable cube with live corner specimens, a ghost corner, and the coverage findings](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/space-tab-missing-corner.png)

### Grade

Darken or lighten a style **without changing its advance widths**.
Turn **Grade** on in the Transforms menu, then click a style's **`G`**
badge to set its %; grades interpolate between the styles you set, so
you grade a few anchors, not every style.
[docs/grade.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/grade.md).

![A style's grade popover: the percentage, its headroom, remove and save](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/grade-badge-popover.png)

### Build transforms

Optional steps that run on every build, saved to
`MyFont-transforms.json`. Most are VF→VF, applied to the compiled font:

- **Spacing — uniform (gftools)** / **Spacing — width-aware** — a
  `SPAC` axis; sidebearings and advances only, outlines never change.
- **Clean fvar instances**, **Rebuild STAT table**, **Smooth unhinted
  rendering** (`gftools`).

**Round corners** runs earlier, on the shadow source just before the
compile (your source file is never edited): every line-line corner of
every master and brace gains a round whose radius blends the layer's
stroke (XOPQ) with its width (XTRA), outer corners and counters
separately, with per-master unit overrides in the sidecar when the
formula isn't enough. Enabling it adds a `ROND` axis (0-100, default
0 = sharp) that rounds live — advances never move along it — and each
named style takes its own rounding as a percent, grade-style: a broad
**Default rounding %** in the menu, and an **R badge** on any instance
row for that style's own percent. The browser demo runs the same engine,
ported to the wasm crate and held node-identical to the desktop by an
oracle test.

Write your own: drop a `.py` subclassing `Transform` into
`~/.avar2-studio/transforms/` and it appears in the menu on the next
launch.

![The Transforms menu: grade settings, then the post-build transforms with their parameters](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/transforms-menu.png)

### Configuration export / import

The **Config** menu moves a whole studio configuration between sources
or machines as a single JSON bundle — secondary parametric axes, avar2
mappings, transforms, grade, corner pins. Import validates first and
is all-or-nothing: a report lists anything the loaded source is
missing, and nothing is applied until you confirm. A bundle whose
mappings section is empty leaves your CSV as it is. **Download
workspace (.zip)…** packs the source, every sidecar and the current
build into one archive that opens here or in the browser demo.

![The Import configuration dialog with its validation summary](https://raw.githubusercontent.com/agyeiagyeiagyei/avar2-studio/main/docs/images/config-import-modal.png)

### Glyphs plugins

Installed with `avar2-studio install-glyphs-plugins`, **Window → avar2
Studio** in Glyphs 3 opens the studio on the font being edited (reusing
a studio already serving it), stops it, and toggles the design tools
that ship alongside: **Corner Radii** (rounded-corner audit and
scaling), **Instance Delta** (a master or instance drawn behind the
edited glyph, with the advance delta), **Multi-Source Edit** (node drags
propagated across masters), **Metrics Parity** (masters sharing the
metric-driving axis values must share metrics), **Slant Master** (an
italic master sheared from a master or instance, width-matched to a
reference) and **Width Matcher** (a new master width-matched to a
reference). Each is described in
[src/avar2_studio/glyphs/README.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/src/avar2_studio/glyphs/README.md).

## Command line

Server: `avar2-studio [SOURCE] [options]`

| Option | Default | What it does |
|---|---|---|
| `SOURCE` (or `--glyphs PATH`) | none | the `.glyphs` or `.designspace` to open; without it the studio starts with a Load Font picker |
| `--port` | `5001` | port to serve on (5000 is taken by AirPlay on macOS) |
| `--host` | `127.0.0.1` | interface to bind |
| `--build-dir` | `.avar2-studio/build/` next to the source | where built fonts go |
| `--csv` | `<stem>-avar.csv` next to the source | mappings file |
| `--no-fontc` | off | compile with fontmake instead of fontc |
| `--debug` | off | Flask debug mode with auto-reload (can interrupt an in-flight build) |

`avar2-studio build SOURCE --out DIR [--csv PATH]` runs one headless
build — the same pipeline the studio runs on load (sidecars,
control-axis shadow, grade layers, avar2) with no server, watcher or
editor — and copies the font into `DIR`. Exit 0 prints the font's
path; 1 means the build or the avar2 mapping failed (a plain fallback
font is not a pass); 2 means the source did not load. Crispy's CI is
the reference use.

`avar2-studio doctor` checks Python (3.10 or newer), fontc,
gftools-builder, glyphsLib, the frontend bundle, Fontra (optional), a
second install shadowing this one, and stale processes holding the
studio's ports.

`avar2-studio install-glyphs-plugins` symlinks the bundled Glyphs 3
plugins into `~/Library/Application Support/Glyphs 3/Plugins/` and
records this Python in `~/.avar2-studio/glyphs-plugin.json` for the
menu's *Open in avar2 Studio*; `--uninstall` reverses both.

Environment variables: `AVAR2_STUDIO_DEBUG=1` (same as `--debug`),
`AVAR2_STUDIO_GFTOOLS_BUILDER=1` (build through an external
`gftools-builder` instead of in-process), `AVAR2_STUDIO_DEMO=1` (show
the landing overlay, for a shared host).

## Development

```bash
git clone https://github.com/agyeiagyeiagyei/avar2-studio
cd avar2-studio
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd frontend && npm ci && npm run build && rsync -a --delete build/ ../src/avar2_studio/static/ && cd ..
avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs
```

The rsync matters: the server serves `src/avar2_studio/static/`, and
without it answers 503 "Frontend bundle not present". The examples in
`examples/` run from a checkout only; they are not in the wheel.

- Frontend hot reload: `npm run dev` in `frontend/` (Vite on
  `http://localhost:5173`, proxying `/api` to the server on 5001).
- Tests: `python -m pytest tests/ -q`; Rust: `cargo test` in
  `wasm/fontc-web`.
- Browser build, end to end: `python scripts/snapshot_static_demo.py`
  stages the example projects, then in `frontend/` run
  `npx vite build --base=./ --outDir dist-pages`, serve it
  (`python3 -m http.server 8123 -d dist-pages`) and run
  `STATIC_URL=http://localhost:8123 node e2e/static-demo.spec.mjs`
  (needs Chrome).
- WebAssembly compiler: `wasm-pack build --target web --out-dir
  ../../frontend/src/wasm/fontc-web` in `wasm/fontc-web`.
- Fontra for the editor: `pip install git+https://github.com/fontra/fontra.git git+https://github.com/fontra/fontra-glyphs.git`.
- Pages deploy: `.github/workflows/pages.yml`, on push to `main` or
  `github-pages`. Releases: `.github/workflows/release.yml`, on a `v*`
  tag (bump `pyproject.toml` first): it builds the frontend, the sdist
  and the wheel, attaches them to a GitHub Release and publishes them
  to PyPI through trusted publishing. Run it by hand with
  `publish_to: testpypi` for a dry run on TestPyPI.
- `site/` and `netlify.toml` are the standalone project page (Netlify),
  not part of the package.

New to the codebase? [docs/HANDOVER.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/HANDOVER.md) covers the
architecture, environment traps, and known issues.

## Related docs

- [docs/authoring-instances.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/authoring-instances.md) — instances and mappings, step by step
- [docs/secondary-parametric-axes.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/secondary-parametric-axes.md) — glyph-scoped axes, brace and correction layers
- [docs/grade.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/grade.md) — how grade works
- [docs/design-notes.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/design-notes.md) — design record behind secondary axes
- [docs/migration-github-pages.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/migration-github-pages.md) — design record of the browser build
- [docs/HANDOVER.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/docs/HANDOVER.md) — project state and undocumented knowledge
- [examples/README.md](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/examples/README.md) — the bundled example projects

## Roadmap

- [x] Outline editing in the browser demo (Fontra as a separate GPL
  sub-project — [fontra-embed](https://github.com/agyeiagyeiagyei/fontra-embed)
  — embedded and driven over `postMessage`)
- [ ] Push-to-source sync (write studio-declared axes from the sidecar
  into the `.glyphs` on request)
- [ ] Grade-master comparison panel (parked on the `grade-comparison`
  branch)

## License

Apache-2.0. See [LICENSE](https://github.com/agyeiagyeiagyei/avar2-studio/blob/main/LICENSE).
