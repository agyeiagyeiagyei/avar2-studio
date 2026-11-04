# Roboto Delta Mini — case-scoped parametric fixture

A designspace fixture derived from Roboto Delta, showing the **case-split**
parametric layout: nine axes in three trios, one per script case, across
**19 sources** and **67 glyphs** per UFO.

```bash
avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace
```

| Case | Thick stroke | Thin stroke | Counter width |
|---|---|---|---|
| Uppercase | `XOUC` 2 – 310 | `YOUC` 2 – 280 | `XTUC` 244 – 741 |
| Lowercase | `XOLC` 2 – 310 | `YOLC` 2 – 280 | `XTLC` 244 – 741 |
| Figures | `XOFI` 2 – 310 | `YOFI` 2 – 280 | `XTFI` 244 – 741 |

Each trio has the same range and its own default (`96 / 79 / 463`), so moving
`XOUC` thickens caps without touching lowercase, and `XOFI` does the same for
digits.

## What ships

- `RobotoDeltaMini.designspace` — the nine axes, **19 sources** (a base plus one
  source per axis extreme; each source is named for its location,
  `RobotoDeltaMini-<tag><value>.ufo`, e.g. `RobotoDeltaMini-XOUC310.ufo`) and a
  single `Default` instance at the trios' defaults.
- 19 UFOs, one per source.

There is **no mappings CSV and no `wght` axis**: this fixture is about the
parametric layout, not about routing. To see routing, open
[crispy-mini](../crispy-mini/), whose CSV carries the `wght`/`wdth`/`opsz`
addresses; the same shape applies here once you give a row user-axis
coordinates.

The nine axes appear in the sidebar as control axes: they are case-scoped, so
no single slider moves every glyph.
[../../docs/guides/authoring-instances.md](../../docs/guides/authoring-instances.md)
covers both layouts side by side, and the Regular → Bold animation beside it
comes from the web article's scripts.
