# Example source files

Small, self-contained parametric-font fixtures designed to be opened
directly with avar2-studio:

```bash
avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs
avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace
```

The two fixtures pair to show the **two main parametric-axis layouts**
real-world variable fonts use, with avar2-studio working on top of
both:

| | [crispy-mini](./crispy-mini/) | [roboto-delta-mini](./roboto-delta-mini/) |
|---|---|---|
| Source format | `.glyphs` | `.designspace` + UFOs |
| Parametric axes | `XOPQ`, `YOPQ`, `XTRA` (unified) | `XOUC`, `YOUC`, `XTUC` (uppercase-scoped) |
| Author intent | "One stroke axis describes the whole font" | "Caps and lowercase need independent optical tuning" |
| Slider effect | Every glyph responds | Only uppercase responds |
| Pre-defined instances | 29 (`Thin Condensed`, …, `Black`) | 0 |
| Glyph count | 64 (A–Z, a–z, 0–9, space + auto `.notdef`) | 64 (same) |

**Crispy's even-tone design** lets one set of axes (`XOPQ` / `YOPQ` /
`XTRA`) describe the whole font — uppercase and lowercase share the
same stroke contrast and counter proportions, so a single thick-stroke
slider does the right thing everywhere.

**Roboto Delta's broader brief** — readable at 8 pt through display
sizes — needs case-specific tuning. Caps and lowercase reach optimal
contrast at different stroke values, so the source ships nine
case-scoped parametric axes (three for caps: `XOUC` / `YOUC` / `XTUC`;
three for lowercase: `XOLC` / `YOLC` / `XTLC`; three for figures:
`XOFI` / `YOFI` / `XTFI`). This mini fixture ships only the uppercase
trio; that's why lowercase letters appear in the preview but don't move
when you scrub the sliders.

avar2-studio handles both layouts identically: each fixture has an
empty traditional `wght` axis the studio's CSV brings to life, the
output VF gets an avar2 table mapping `wght` to a coherent parametric
location, and the same UI affordances (create studio-only instance,
add to source, edit coords) apply.

See each fixture's README for axes table, glyph coverage, how it was
made, and a sample CSV mapping for authoring.
