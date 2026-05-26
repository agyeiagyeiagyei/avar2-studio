# avar2-studio

Visual authoring and preview tool for avar2 variable fonts.

`avar2-studio` is a desktop tool for type designers who work in parametric
designspaces (XOPQ / YOPQ / XTRA, ROND, etc.) and want to expose familiar
traditional axes (wght, wdth, opsz) to end users through an avar2 mapping.

Point it at a `.glyphs` file and it opens a browser-based editor for tuning
parametric instances and authoring traditional→parametric mappings, with the
preview rendering the actual built avar2 font (no approximations).

## Status

Early development — extracted from the
[Crispy](https://github.com/agyeiagyeiagyei/Crispy) font project. v0.1 in
progress; see [CHANGELOG](CHANGELOG.md) when one exists.

## Install

```bash
brew install fontc                # or: cargo install fontc
pipx install avar2-studio
```

`fontc` is required and must be on `PATH`. Run `avar2-studio doctor` (coming in
v0.1) to verify your environment.

## Usage

```bash
avar2-studio /path/to/MyFont.glyphs
```

The tool creates a sibling `.avar2-studio/` directory next to your `.glyphs`
file for its working state, and a sibling `MyFont-avar.csv` for the avar2
mappings. Commit the CSV; gitignore the `.avar2-studio/` directory.

Open the printed URL (default `http://localhost:5001`) in a browser.

## Development

```bash
git clone https://github.com/agyeiagyeiagyei/avar2-studio
cd avar2-studio
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd frontend && npm ci && npm run build && cd ..
avar2-studio /path/to/MyFont.glyphs
```

For frontend hot-reload during development, run the React dev server in one
terminal (`cd frontend && npm start`) and the backend in another
(`avar2-studio /path/to/MyFont.glyphs`). The React dev server proxies API
requests to the backend.

## License

Apache-2.0. See [LICENSE](LICENSE).
