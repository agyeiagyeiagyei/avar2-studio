# avar2-studio

Visual authoring and preview tool for avar2 variable fonts.

`avar2-studio` is a desktop tool for type designers who work in
parametric designspaces (XOPQ / YOPQ / XTRA, ROND, GRAD, etc.) and
want to expose familiar traditional axes (`wght`, `wdth`, `opsz`,
`cntr`, `ital`) to end users through an avar2 mapping.

Point it at a `.glyphs` file and it opens a browser-based editor for
tuning parametric instances and authoring traditional→parametric
mappings. The preview renders the **actual built avar2 font** — the
browser applies the real avar2 table — so there are no JS-side
approximations of what the shipped font will do.

## Status

**Pre-release (v0.1.0.dev0).** Extracted from the
[Crispy](https://github.com/agyeiagyeiagyei/Crispy) font project
through three phases of work: lift-and-shift, genericization, and
single-build-path consolidation. Verified end-to-end on Crispy and
on a second font (Jaro, single-axis).

Not yet on PyPI. **Install from source** until v0.1.0 ships — see
below.

## Install (from source)

This is the path that works today. PyPI install will be added when
v0.1.0 is tagged.

You need `fontc` and `gftools` on `PATH`. The Python side is just a
normal pip install.

```bash
# 1. fontc — Rust binary, not on PyPI
brew install fontc           # or: cargo install fontc

# 2. avar2-studio itself
git clone https://github.com/agyeiagyeiagyei/avar2-studio
cd avar2-studio
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 3. Build the frontend bundle that the server will serve
cd frontend && npm ci && npm run build && cd ..

# 4. Run it on a .glyphs file
avar2-studio /path/to/MyFont.glyphs
```

The server prints a URL (default `http://localhost:5001`). Open it in
a browser.

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

- [ ] `avar2-studio doctor` subcommand for environment checks
- [ ] Release CI: build the React bundle, assemble the wheel, publish to PyPI
- [ ] v0.1.0 release
- [ ] SPAC axis support (deferred from v0.1 — needs spacing recalibration logic ported from Crispy's build pipeline)

## License

Apache-2.0. See [LICENSE](LICENSE).
