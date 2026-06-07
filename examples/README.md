# Example source files

Small, self-contained parametric-font fixtures designed to be opened
directly with avar2-studio:

```bash
avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs
avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace
```

Both fixtures expose the same three parametric axes — `XOPQ` (thick
stroke), `YOPQ` (thin stroke), `XTRA` (counter width) — and an empty
traditional `wght` axis that an avar2 mapping in the studio's CSV
brings to life. They're calibrated to demonstrate two different
real-world parametric authoring approaches:

| | [crispy-mini](./crispy-mini/) | [roboto-delta-mini](./roboto-delta-mini/) |
|---|---|---|
| Source format | `.glyphs` | `.designspace` + UFOs |
| Author intent | Even tone across cases | Case-aware optical tuning |
| Slider effect | All glyphs respond | Only uppercase responds (in this fixture) |
| Pre-defined instances | 29 (`Thin Condensed`, …, `Black`) | 0 |
| Glyph count | 64 (A–Z, a–z, 0–9, space + auto `.notdef`) | 64 (same) |

Both pair to the same destination: a variable font with an avar2 table
mapping a user-facing `wght` slider to a coherent point in parametric
space. They differ in their starting assumption about whether one
parametric "thick stroke" axis is enough or whether caps and lowercase
need to be tuned independently. The bigger Roboto Delta source ships
both (`XOUC`/`XOLC`/`XOFI` for thick stroke, etc.); this mini fixture
exposes only the uppercase set so the comparison is direct.

See each fixture's README for axes table, glyph coverage, how it was
made, and a sample CSV mapping for authoring.
