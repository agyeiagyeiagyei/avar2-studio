# Example source files

Two small, self-contained parametric fixtures, each opening directly in the
studio:

```bash
avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs
avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace
```

They pair to show the **two parametric-axis layouts** avar2-studio works on top
of:

| | [crispy-mini](./crispy-mini/) | [roboto-delta-mini](./roboto-delta-mini/) |
|---|---|---|
| Source format | `.glyphs` | `.designspace` + 19 UFOs |
| Parametric axes | `XTRA`, `XOPQ`, `YOPQ` (unified) | nine, case-scoped: `XOUC`/`YOUC`/`XTUC`, `XOLC`/`YOLC`/`XTLC`, `XOFI`/`YOFI`/`XTFI` |
| Masters / sources | 8 (the corners of the three-axis box) | 19 (a base plus one source per axis extreme) |
| Glyphs | 63 (`A–Z`, `a–z`, `0–9`, `space`, `.notdef`) | 63 (same subset) |
| Named rows | 8 in `CrispyMini-avar.csv`, with declared axis defaults, a `wght`/`wdth`/`opsz` mapping and a width-aware SPAC transform | none — the designspace declares one `Default` instance and no `wght` |
| Shows | "one stroke vocabulary describes the whole font" | "each script case needs its own optical tuning" |

**Crispy Mini** derives from [Crispy](https://github.com/agyeiagyeiagyei/Crispy):
uppercase, lowercase and figures share one stroke contrast and counter
proportion, so a single thick-stroke slider does the right thing everywhere.
Eight masters at the corners of the box, eight authored rows in the CSV.

**Roboto Delta Mini** derives from Roboto Delta: readable from 8 pt to display
sizes, caps and lowercase reach their best contrast at different stroke values,
so it ships three trios instead of one. Moving `XOUC` thickens caps without
touching lowercase.

avar2-studio treats both identically — core axes on Crispy Mini, control axes on
Roboto Delta Mini, mappings authored in the same CSV shape once you give a row
user-axis coordinates.

The parametric → stylistic workflow, with an animation of each, is in
[../docs/guides/authoring-instances.md](../docs/guides/authoring-instances.md).
Each fixture's own README covers what it *is*: axes, coverage, provenance.
