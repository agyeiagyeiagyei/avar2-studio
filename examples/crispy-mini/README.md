# Crispy Mini — avar2-studio demo fixture

A small self-contained demo source derived from
[Crispy](https://github.com/agyeiagyeiagyei/Crispy). Three parametric axes and
eight masters at the corners of their box; **63 glyphs** (`A–Z`, `a–z`, `0–9`,
`space`, `.notdef`).

```bash
avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs
```

| Axis | Name | Range |
|---|---|---|
| `XTRA` | X-Transparency (counter width) | 94 – 3330 |
| `XOPQ` | X-Opacity (vertical strokes) | 2 – 1016 |
| `YOPQ` | Y-Opacity (horizontal strokes) | 2 – 462 |

The masters are the eight corners: `(94, 2, 2)` through `(3330, 1016, 462)`.

## What ships beside the source

| File | What it holds |
|---|---|
| `CrispyMini-avar.csv` | **eight named rows** — `Narrow Thin 144` … `Ultra Wide Heavy 144` — with parametric coordinates plus `wght` (100–900), `wdth` (20–200) and `opsz` (12–144), so seven of them are avar2 mappings |
| `CrispyMini-axis-metadata.json` | the declared axis defaults and display names (`opsz` defaults to 144, `wght` to 100) — the build exits 2 without it |
| `CrispyMini-transforms.json` | a width-aware SPAC transform, `min -20 / max 40`, on by default |

The `.glyphs` source declares **no instances**: the named styles live in the CSV,
which is the authoring surface the studio writes. The build's compiled origin is
the first master `(XTRA 94, XOPQ 2, YOPQ 2)`.

Every axis lands under **CORE / PARAMETRIC AXES** in the sidebar, because every
glyph varies along it. Contrast
[`roboto-delta-mini`](../roboto-delta-mini/), whose axes are case-scoped and
appear as control axes.

## The workflow it demonstrates

Duplicate a row, tune its parametric coordinates, give it user-axis values, and
the build writes an avar2 table that routes the familiar axes onto the point you
authored.
[../../docs/guides/authoring-instances.md](../../docs/guides/authoring-instances.md)
walks one shipped row (`Narrow Heavy 144`) axis by axis, and
`docs/animate_regular_condensed.py` produces the GIF beside it.
