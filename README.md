# avar2-studio

Visual authoring and preview tool for avar2 variable fonts.

`avar2-studio` is a desktop tool for type designers who work in
parametric designspaces (XOPQ / YOPQ / XTRA, ROND, GRAD, etc.) and
want to expose familiar traditional axes (`wght`, `wdth`, `opsz`,
`cntr`, `ital`) to end users through an avar2 mapping.

Point it at a `.glyphs` file or a `.designspace` and it opens a
browser-based editor for tuning parametric instances and authoring
traditional→parametric mappings. The preview renders the **actual built
avar2 font** — the browser applies the real avar2 table — so there are
no JS-side approximations of what the shipped font will do.

Beyond mappings, you can declare **control axes** — designer-named
axes that deform only a chosen subset of glyphs — author their brace
layers visually, and draw the outlines themselves in an embedded
[Fontra](https://github.com/fontra/fontra) editor. A **post-build
transforms** menu applies optional VF→VF steps (spacing axes, gftools
fixes, your own scripts) to every build.

## Status

**Pre-release.** Latest GitHub release: `v0.1.0.dev6`; the repo is at
`v0.1.0.dev7` (unreleased). Extracted from the
[Crispy](https://github.com/agyeiagyeiagyei/Crispy) font project
through three phases of work: lift-and-shift, genericization, and
single-build-path consolidation. `dev6` added `.designspace` support
alongside `.glyphs`, plus a two-tier instance model that keeps
exploratory grid points out of the designer's source file. Since
then: glyph-scoped **control axis** authoring (declare an axis, pick
its glyphs, author brace layers, draw outlines in an embedded
Fontra), a free-form **Preview** tab for the built font, a
**post-build transforms** framework, and a ~19× faster edit-rebuild
loop. Not yet on PyPI.

## How it works

A parametric font doesn't ship a `Regular` or `Bold` knob — it ships
axes that describe *the shape of the strokes*: `XOPQ` for vertical
stroke thickness, `YOPQ` for horizontal, `XTRA` for counter width
(and similar). End users want familiar `wght` / `wdth` / `opsz`
sliders, not those. avar2-studio's job is to translate "what a
designer would call a style" into "where in parametric space that
style lives," and emit an avar2 mapping table that connects the two.

In practice: you point avar2-studio at a `.glyphs` or `.designspace`,
author named instances (`Regular Condensed`, `Bold`, `Thin
Extended`, …) by tuning their parametric coordinates in the UI, and
the build pipeline writes an avar2 table that makes a user-facing
`wght=400` slider resolve to the parametric point you chose. Each
instance is one row in a sibling `{FamilyName}-avar.csv`; a usable
designspace is a few dozen of those rows spanning the design grid.

[docs/authoring-instances.md](./docs/authoring-instances.md) is the
single reference for this workflow. It walks both approaches —
[`examples/crispy-mini`](./examples/crispy-mini/)'s unified three-axis
layout and [`examples/roboto-delta-mini`](./examples/roboto-delta-mini/)'s
case-split nine-axis layout — with animations of each, the
parametric-coords-per-instance tables, and a heuristic for picking
between them on your own source.

## Install

```bash
pipx install https://github.com/agyeiagyeiagyei/avar2-studio/releases/latest/download/avar2_studio-0.1.0.dev6-py3-none-any.whl
avar2-studio /path/to/MyFont.glyphs       # or /path/to/MyFont.designspace
```

All dependencies — including `fontc` (the Rust font compiler, via
its PyPI package) and `gftools` — are in the wheel's deps. Run
`avar2-studio doctor` to confirm your environment is OK.

> The released wheel is `dev6`. Control axes, the Preview tab, and
> post-build transforms landed after that release — install from
> source (below) to use them today.

## Install from source (for development)

If you want to hack on avar2-studio itself, install editable from a
clone and build the frontend locally:

```bash
git clone https://github.com/agyeiagyeiagyei/avar2-studio
cd avar2-studio
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd frontend && npm ci && npm run build && cd ..
```

## Launch

```bash
avar2-studio /path/to/MyFont.glyphs        # or /path/to/MyFont.designspace
```

The server prints a URL (default `http://localhost:5001`). Open it in
a browser — the initial font build runs on launch and takes a couple
of seconds.

You can also launch with **no source at all**:

```bash
avar2-studio
```

The UI opens with a **Load Font** dropdown in the top bar — pick a
bundled example or upload your own `.glyphs` file. From a dev
checkout the two example fixtures are offered directly:

- **Crispy Mini** — `.glyphs`, unified parametric axes (all glyphs
  vary along `XOPQ`/`YOPQ`/`XTRA`)
- **Roboto Delta Mini** — `.designspace`, case-split parametric axes
  (only uppercase varies)

Examples are staged into `~/.avar2-studio/workspace/<example>/`, so
your edits persist across launches and the repo fixtures stay
git-clean. The two fixtures surface different axis layouts in the
sidebar — see
[Axis surface](#axis-surface--where-each-axis-appears-in-the-sidebar)
below for why.

Server options:

| flag | default | |
|---|---|---|
| `--port` | `5001` | avoids macOS AirPlay on 5000 |
| `--host` | `127.0.0.1` | |
| `--build-dir` | `.avar2-studio/build/` next to the source | |
| `--csv` | sibling `<basename>-avar.csv` | |
| `--no-fontc` | off | compile with fontmake instead of fontc |
| `--debug` | off | Flask auto-reload (can kill in-flight builds) |

`avar2-studio doctor` checks the environment (fontc, gftools,
frontend bundle, …) without needing a source file.

## Usage

```bash
avar2-studio /path/to/MyFont.glyphs
```

The tool sets up sibling files next to your `.glyphs` file:

```
~/work/MyFont/
├── MyFont.glyphs             # your source (read, and written back when you save edits)
├── MyFont-avar.csv           # authored avar2 mappings — commit this
├── MyFont-control.json       # authored control axes + brace layers — commit this
├── MyFont-transforms.json    # enabled post-build transforms + params — commit this
└── .avar2-studio/            # tool-managed: config, build output, shadow source — gitignore this
    ├── config.yaml
    ├── axis-metadata.json
    ├── build/
    │   └── MyFont[XOPQ,XTRA,YOPQ].ttf
    └── shadow/
        └── MyFont.glyphs     # your source + studio-authored brace layers, regenerated on edit
```

The three sidecars are the designer's authored artifacts and should
be committed alongside the source (`-control.json` and
`-transforms.json` only appear once you use those features).
`.avar2-studio/` is tool-managed and should be added to your
project's `.gitignore` — builds compile from the shadow copy once a
control axis has brace layers, so **your original source file is
never modified** by control-axis authoring.

## Axis surface — where each axis appears in the sidebar

Every axis lands in one of two sidebar sections. The tool decides by
looking at how many glyphs actually deform when that axis moves:

**CORE / PARAMETRIC AXES** — axes that deform *every* glyph in the
font (100% coverage). These are the "universal" parametric handles —
what most designers think of when they say "the parametric axes."
The studio treats them as the base designspace surface, and
traditional axes get mapped *into* this space via avar2.

**CONTROL AXES** — axes that only deform *some* glyphs. Two
badges disambiguate why:

- `scoped` — the axis was authored to affect a subset of glyphs
  rather than everything: Roboto Delta's case-split axes only
  touch uppercase; a "crossbar" axis might only touch letters
  with crossbars (A, E, F, H, T, e, f, t, …); a figure-only axis
  might only touch digits. Expand the row to see the exact
  covered set — if it looks incomplete for what you intended,
  that's a hint that a few glyphs are missing masters upstream.

- `studio` — declared inside avar2-studio (via **+ Add** next to
  CONTROL AXES) rather than in the source file. Lives in the
  sibling sidecar (`<basename>-control.json`); builds compile from
  a shadow copy of your source with the brace layers written in,
  so the original file is never touched. Use this to prototype new
  axes without editing the .glyphs / .designspace directly.

Studio-declared axes are fully authorable: expand the row to add
applicable glyphs, pin brace layers at any designspace location
(recently used custom locations are remembered, and any layer can be
duplicated to a new location), and click a layer's ↗ to draw its
outline in the embedded Fontra editor. Rows warn when a glyph's
authored layers don't reach the axis extremes (the rendered font
would extrapolate). Layer saves land instantly; the preview font
recompiles in the background and catches up within a few seconds.
[docs/control-axes.md](./docs/control-axes.md) is the design +
implementation deep-dive.

**The two example fixtures show this in action:**

- **crispy-mini** — `XOPQ` / `YOPQ` / `XTRA` deform every glyph, so
  they surface under CORE / PARAMETRIC AXES with no badge.
- **roboto-delta-mini** — `XOUC` / `YOUC` / `XTUC` only touch the
  uppercase glyphs (Roboto Delta's case-split design), so they
  surface under CONTROL AXES with a `scoped` badge. Nothing wrong;
  the classifier is reading Roboto Delta's actual authored coverage.

Classifier logic: [glyph_coverage.py `_classify()`](./src/avar2_studio/glyph_coverage.py).

## Preview tab

The main area has two tabs. **Instances** is the authoring surface —
the instance grid plus the sidebar sliders. **Preview** free-form
drives the **built font** the way an end user's app would: every fvar
axis is a live slider, grouped into

- **User axes** — the avar2-mapped axes you authored (`wght`, `wdth`, …)
- **Control axes** — glyph-scoped axes
- **Parametric axes** — the underlying designspace handles, collapsed
  by default

Because the browser renders the actual compiled font, what you see —
the avar2 table, control-axis deltas, transform-injected axes like
`SPAC` — is exactly what ships. A **Download** button saves the
current built `.ttf`.

## Post-build transforms

The **Transforms** dropdown in the header holds optional VF→VF steps
that run on the compiled font after every build. All are off by
default; which are enabled (and their params) is saved to the
committed `<basename>-transforms.json` sidecar, so a project's
transform chain survives reloads and travels with the repo.

Built-ins:

- **Spacing — uniform (gftools)** — inject a `SPAC` axis via
  `gftools-gen-spac`: every glyph tracks by the same amount. Values
  are per-side, so ±N ≈ ±2N advance units.
- **Spacing — width-aware** — our own `SPAC`: each glyph is loosened
  in proportion to its width, so wide glyphs get more air than
  narrow ones. Composite glyphs are handled.
- **Clean fvar instances** — regenerate named instances to match the
  current axes (`gftools fix-instances`).
- **Rebuild STAT table** — from the Google Fonts axis registry;
  registered axes only (`gftools gen-stat`).
- **Smooth unhinted rendering** — add gasp + prep so an unhinted VF
  anti-aliases at all sizes (`gftools fix-unhinted`).

Both spacing transforms move only phantom points (sidebearings and
advances) — outlines never change — and at most one enabled transform
may inject a given axis tag.

**Write your own:** drop a `.py` subclassing `Transform` into
`~/.avar2-studio/transforms/` and it appears in the dropdown on the
next launch. The folder is created with a README template on first
run.

## Frontend hot-reload (optional)

If you're iterating on the frontend, the React dev server has hot
reload. Run the backend and the React dev server in separate
terminals:

```bash
# Terminal 1: backend (API on :5001)
avar2-studio /path/to/MyFont.glyphs

# Terminal 2: React dev server (UI on :3000, proxies API to :5001)
cd frontend && npm start
```

Open `http://localhost:3000` in a browser.

## Roadmap

- [x] `avar2-studio doctor` subcommand for environment checks
- [x] Release CI: tag push builds the React bundle, assembles the wheel, attaches it to a GitHub Release
- [x] SPAC axis support — shipped as post-build transforms (uniform via gftools + our width-aware variant)
- [ ] PyPI publish (replace the release URL above with `pipx install avar2-studio`)
- [ ] v0.1.0 release (current pre-releases are `v0.1.0.devN`)
- [ ] `.designspace` control-axis authoring (brace-layer authoring is `.glyphs`-only right now; `.designspace` sources get read-only coverage)
- [ ] Push-to-source sync (write studio-declared axes from the sidecar into the `.glyphs` on request)
- [ ] Grade-master comparison panel (parked on the `grade-comparison` branch; uses uharfbuzz per-glyph advances)

## License

Apache-2.0. See [LICENSE](LICENSE).
