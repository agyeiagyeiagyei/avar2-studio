# Crispy Mini — avar2-studio demo fixture

A small self-contained demo source derived from
[Crispy](https://github.com/agyeiagyeiagyei/Crispy). Three parametric
axes (`XOPQ`, `YOPQ`, `XTRA`), 29 source-defined instances pre-mapped
to traditional axes via the sibling CSV — the "designer's source has
intent baked in" workflow.

```bash
avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs
```

## Why this exists alongside `roboto-delta-mini`

These two fixtures pair to demonstrate the two main parametric-font
authoring approaches avar2-studio supports:

| | `crispy-mini` | `roboto-delta-mini` |
|---|---|---|
| Source format | `.glyphs` | `.designspace` + UFOs |
| Parametric axes | `XOPQ`, `YOPQ`, `XTRA` (unified) | `XOPQ`, `YOPQ`, `XTRA` (relabeled from `XOUC`/`YOUC`/`XTUC` — see roboto-delta-mini/README) |
| Glyph scope | Affects all glyphs (caps + lowercase + digits) | Affects uppercase only — lowercase is rendered from the default master |
| Starting instances | 29 source-defined (`Thin Condensed`, `Regular`, `Bold`, …) | 0 |
| Designer intent | "Even tone — one stroke axis describes the whole font" | "Case-aware — caps need different optical tuning than lowercase" |

Both fixtures end up at the same destination — a variable font with an
avar2 table mapping a single `wght` slider to a coherent parametric
location — but they get there from different starting points.

## Axes

| Tag | Name | Range | Default | Notes |
|---|---|---|---|---|
| `XTRA` | X-Transparency | 94–3330 | 94 | Counter width |
| `XOPQ` | X-Opacity | 2–1016 | 1016 | Thick stroke |
| `YOPQ` | Y-Opacity | 2–462 | 462 | Thin stroke |

Crispy doesn't ship a traditional `wght` axis in the source. To add one
for the avar2 demo, you can either:

1. Add a `wght` axis to the .glyphs file (in Glyphs.app or via the
   API), with no master coverage — the studio's avar2 mapping will
   define what `wght` values resolve to in parametric space.
2. Or work purely in parametric space and let users discover Crispy
   through `XOPQ`/`YOPQ`/`XTRA` directly.

The shipped Crispy production font does (1); this minimal fixture
starts at (2) so the demo is reproducible without modifying the source.

## Glyph coverage

64 glyphs: `A–Z`, `a–z`, `0–9`, `space`, plus an auto-generated `.notdef`
that fontc adds at compile time.

## How it was made

```python
# 1. Pull from origin/main of agyeiagyeiagyei/Crispy:
git worktree add /tmp/crispy-source origin/main
cp /tmp/crispy-source/sources/Crispy.glyphs examples/crispy-mini/sources/CrispyMini.glyphs

# 2. Drop glyphs outside the keep set via glyphsLib:
from glyphsLib import load
font = load("examples/crispy-mini/sources/CrispyMini.glyphs")
KEEP = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz") | \
       {"zero","one","two","three","four","five","six","seven","eight","nine","space"}
for g in list(font.glyphs):
    if g.name not in KEEP:
        del font.glyphs[g.name]
font.familyName = "Crispy Mini"
font.save("examples/crispy-mini/sources/CrispyMini.glyphs")
```

That's the entire recipe — Crispy's .glyphs format keeps everything in
one file, so no per-UFO subsetting like `roboto-delta-mini` needs. From
127 glyphs to 63 (Crispy doesn't ship `.notdef`; fontc synthesizes one),
~900 KB → ~640 KB.

## License

Crispy is the author's own work and is included here under the same
license as the parent avar2-studio repo (Apache-2.0).
