# Roboto Delta Mini — avar2-studio demo fixture

A small, self-contained demo source for avar2-studio derived from
[googlefonts/roboto-delta](https://github.com/googlefonts/roboto-delta).
Three parametric axes, one (empty) traditional axis, no source-defined
instances — ready to author an avar2 mapping against.

```bash
avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace
```

## Why this exists

Roboto Delta is the canonical real-world parametric font. The upstream
variable font is **shipped without an avar2 layer** — designers see
`wght`, `wdth`, `opsz` sliders in their type tester, but those sliders
have no master data driving them; the parametric axes (XOUC, YOUC, …)
do. avar2-studio's job is to fill that gap: define traditional →
parametric mappings via a CSV the build pipeline uses to write an avar2
table.

This fixture is the smallest meaningful slice of Roboto Delta that
demonstrates the workflow end-to-end. It's a good place to try
avar2-studio without needing Glyphs.app or the full ~hundreds-of-MB
Roboto Delta repo.

## What's inside

```
sources/
├── RobotoDeltaMini.designspace        # 4 axes, 7 sources, 0 instances
├── RobotoDeltaMini-Default.ufo        # wght=400, XOUC=96, YOUC=79, XTUC=463
├── RobotoDeltaMini-XOUC2.ufo          # XOUC=2 corner
├── RobotoDeltaMini-XOUC310.ufo        # XOUC=310 corner
├── RobotoDeltaMini-YOUC2.ufo          # YOUC=2 corner
├── RobotoDeltaMini-YOUC280.ufo        # YOUC=280 corner
├── RobotoDeltaMini-XTUC244.ufo        # XTUC=244 corner
└── RobotoDeltaMini-XTUC741.ufo        # XTUC=741 corner
OFL.txt                                # SIL Open Font License (required)
subset.py                              # How the UFOs were trimmed
```

### Axes

| Tag | Name | Range | Default | Notes |
|---|---|---|---|---|
| `wght` | Weight | 100–900 | 400 | No master coverage. Drives parametric axes via avar2. |
| `XOUC` | Uppercase Thick Stroke | 2–310 | 96 | Two corner masters. |
| `YOUC` | Uppercase Thin Stroke | 2–280 | 79 | Two corner masters. |
| `XTUC` | Uppercase Counter Width | 244–741 | 463 | Two corner masters. |

`wght` is declared with no underlying master deltas. Moving it does
nothing **until** you author an avar2 mapping that translates
`wght` → `(XOUC, YOUC, XTUC)` values via the studio's sibling
`RobotoDeltaMini-avar.csv`.

### Glyph coverage

64 glyphs: `A–Z`, `a–z`, `0–9`, `.notdef`, `space`. Lowercase letters
are present but are **not** affected by the XOUC/YOUC/XTUC sliders —
those axes are scoped to uppercase by design. (Roboto Delta uses
case-split parametric axes; this fixture only ships the uppercase set.
The full font also has XOLC/YOLC/XTLC for lowercase and XOFI/YOFI/XTFI
for figures.)

## Try it

1. Install avar2-studio (`pipx install …`).
2. Run it against the designspace:

   ```bash
   avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace
   ```

3. In the UI, create a few studio-only instances — e.g. `Thin`, `Bold`,
   `Black` — at increasing XOUC/YOUC values. Each row lands in
   `RobotoDeltaMini-avar.csv`, not in the designspace itself.
4. Add `wght` values in the CSV (manually for now, or via a CSV editor):

   ```
   Instance Name,WGHT,XOUC,YOUC,XTUC,SPAC
   Thin,100,30,30,463,0
   Regular,400,96,79,463,0
   Bold,700,220,160,463,0
   Black,900,300,260,463,0
   ```

5. Trigger a build. The output VF will have an avar2 table mapping the
   user's `wght` input to the corresponding parametric coords. Test
   `wght: 400 → wght: 700` in a browser and the caps thicken without
   leaving the parametric design space.

## How it was made

`subset.py` records the recipe. To re-derive from upstream:

```bash
# 1. Sparse-clone roboto-delta and pull only the 7 masters we need.
git clone --depth 1 https://github.com/googlefonts/roboto-delta.git /tmp/roboto-delta-clone

# 2. Copy them out, renaming to a sane prefix.
mkdir -p /tmp/roboto-delta-mini/sources
SRC=/tmp/roboto-delta-clone/Source/Roman
cp -R $SRC/Roboto-Delta-wght400.ufo /tmp/roboto-delta-mini/sources/RobotoDeltaMini-Default.ufo
cp -R $SRC/Roboto-Delta-XOUC2.ufo   /tmp/roboto-delta-mini/sources/RobotoDeltaMini-XOUC2.ufo
# (...etc for the other 5 corners)

# 3. Subset each UFO to A-Z, a-z, 0-9, .notdef, space.
python subset.py     # paths inside the script point at /tmp/roboto-delta-mini

# 4. Hand-author RobotoDeltaMini.designspace pointing at the subsetted UFOs.
```

The subsetter drops ~1000 glyphs per master (Cyrillic, Greek, diacritics,
math, punctuation, etc.), nukes `features.fea` and `groups.plist` /
`kerning.plist` (they reference dropped glyphs), and strips any
component references in kept glyphs that point at dropped ones. Each
master shrinks from ~4.5 MB to ~400 KB.

## License

Roboto Delta is licensed under the SIL Open Font License 1.1 — see
[OFL.txt](./OFL.txt). This subset inherits the same license; the
copyright holders are listed in each UFO's `fontinfo.plist`.
