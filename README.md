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

Every axis lands in one of two sidebar sections. The tool decides by
looking at how many glyphs actually deform when that axis moves:

**CORE / PARAMETRIC AXES** — axes that deform *every* glyph in the
font (100% coverage). These are the "universal" parametric handles —
what most designers think of when they say "the parametric axes."
The studio treats them as the base designspace surface, and
traditional axes get mapped *into* this space via avar2.

**CONTROL AXES** — axes that only deform *some* glyphs. Three
badges disambiguate why:

- `scoped` — coverage under 80%. The axis was authored to affect
  a subset by design: Roboto Delta's case-split axes only touch
  uppercase glyphs; a "crossbar" axis might only touch letters
  with crossbars (A, E, F, H, T, e, f, t, …); a figure-only axis
  might only touch digits. Read this as intentional.

- `partial` — coverage between 80% and 100%. Nearly-universal,
  which usually means it *was* meant to be universal but a few
  glyphs got missed when authoring the alternate master. Read this
  as **check your source** — likely a bug, not a design choice.
  If it *is* deliberate, the badge is harmless and can be ignored.

- `studio` — declared inside avar2-studio (via **+ Add** next to
  CONTROL AXES) rather than in the source file. Lives in the
  sibling sidecar (`<basename>-control.json`) until a build writes
  it into the source. Use this to prototype new axes without
  editing the .glyphs / .designspace directly.

**The two example fixtures show this in action:**

- **crispy-mini** — `XOPQ` / `YOPQ` / `XTRA` deform every glyph, so
  they surface under CORE / PARAMETRIC AXES with no badge.
- **roboto-delta-mini** — `XOUC` / `YOUC` / `XTUC` only touch the
  uppercase glyphs (Roboto Delta's case-split design), so they
  surface under CONTROL AXES with a `scoped` badge. Nothing wrong;
  the classifier is reading Roboto Delta's actual authored coverage.

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
