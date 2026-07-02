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

## Status

**Pre-release.** Latest tag: `v0.1.0.dev6`. Extracted from the
[Crispy](https://github.com/agyeiagyeiagyei/Crispy) font project
through three phases of work: lift-and-shift, genericization, and
single-build-path consolidation. v0.1.0.dev6 adds `.designspace`
support alongside `.glyphs`, plus a two-tier instance model that
keeps exploratory grid points out of the designer's source file.
Not yet on PyPI.

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

Then launch against either bundled example fixture to try it without
supplying your own source:

```bash
# .glyphs source, unified parametric axes (all glyphs vary along XOPQ/YOPQ/XTRA)
avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs

# .designspace source, case-split parametric axes (only uppercase varies)
avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace
```

The server prints a URL (default `http://localhost:5001`). Open it in
a browser. First launch takes ~10s while the initial font build runs.
The two fixtures surface different axis layouts in the sidebar — see
[Axis surface](#axis-surface--where-each-axis-appears-in-the-sidebar)
below for why.

## Usage

```bash
avar2-studio /path/to/MyFont.glyphs
```

The tool sets up a sibling working directory next to your `.glyphs`
file:

```
~/work/MyFont/
├── MyFont.glyphs           # your source (read, and written back when you save edits)
├── MyFont-avar.csv         # the authored avar2 mappings — commit this
└── .avar2-studio/          # tool-managed: config, axis metadata, build output — gitignore this
    ├── config.yaml
    ├── axis-metadata.json
    └── build/
        └── MyFont[XOPQ,XTRA,YOPQ].ttf
```

`MyFont-avar.csv` is the designer's authored artifact and should be
committed alongside the `.glyphs` file. `.avar2-studio/` is
tool-managed and should be added to your project's `.gitignore`.

## Axis surface — where each axis appears in the sidebar

The sidebar groups every axis into one of two sections based on how
many glyphs actually vary along it:

| Coverage of glyphs varying | Section | Meaning |
|---|---|---|
| 100% | CORE / PARAMETRIC AXES | Every glyph moves with this axis. The universal parametric-designspace surface. |
| ≥ 80% but < 100% | CONTROL AXES · `partial` badge | Nearly-universal, likely a coverage gap. Usually a smell — designer forgot to author a few glyphs at an alternate master. |
| < 80% (or 0%) | CONTROL AXES · `scoped` badge | Glyph-scoped by design — only a subset of glyphs varies (case-split, digit-only, crossbar-bearing letters, etc.). |
| Studio-declared | CONTROL AXES · `studio` badge | New control axis you declared in the studio. Lives in the sidecar until you build. |

This is why the two example fixtures look different in the sidebar:

- **crispy-mini** — its parametric axes (`XOPQ`/`YOPQ`/`XTRA`) vary
  every glyph, so they all land under CORE / PARAMETRIC AXES.
- **roboto-delta-mini** — its parametric axes (`XOUC`/`YOUC`/`XTUC`)
  only vary the uppercase glyphs by design (Roboto Delta uses
  case-split axes), so they land under CONTROL AXES with a `scoped`
  badge. That's the classifier reading Roboto Delta's actual
  authored surface, not a misclassification.

Classifier logic: [glyph_coverage.py `_classify()`](./src/avar2_studio/glyph_coverage.py).

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
- [ ] PyPI publish (replace the release URL above with `pipx install avar2-studio`)
- [ ] v0.1.0 release (current pre-releases are `v0.1.0.devN`)
- [ ] SPAC axis support (deferred from v0.1 — needs spacing recalibration logic ported from Crispy's build pipeline)
- [ ] Grade-master comparison panel (parked on the `grade-comparison` branch; uses uharfbuzz per-glyph advances)

## License

Apache-2.0. See [LICENSE](LICENSE).
