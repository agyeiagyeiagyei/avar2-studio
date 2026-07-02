# Crispy Mini — avar2-studio demo fixture

A small self-contained demo source derived from
[Crispy](https://github.com/agyeiagyeiagyei/Crispy). Three parametric
axes (`XOPQ`, `YOPQ`, `XTRA`), **one `Default` instance** anchored at
the first master's location — the minimum needed for the font to
export and for the studio's UI to render a starting row. The intended
workflow is to duplicate that row and tune the parametric coords to
author each new style.

The parametric → stylistic workflow walkthrough — including the
animation that morphs `Adhesion` from the corner `Default` to a
`Regular Condensed` location — lives in the centralized
[../../docs/authoring-instances.md](../../docs/authoring-instances.md)
alongside the matching Roboto Delta walkthrough.

```bash
avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs
```

**What you'll see in the sidebar:** `XTRA` / `XOPQ` / `YOPQ` land
under **CORE / PARAMETRIC AXES** — every glyph varies along them, so
the studio's classifier treats them as universal. Contrast with
[`roboto-delta-mini`](../roboto-delta-mini/), whose parametric axes
are uppercase-scoped and appear under CONTROL AXES instead. See the
[top-level README's "Axis surface"
section](../../README.md#axis-surface--where-each-axis-appears-in-the-sidebar)
for the classification rules.

## Why this exists alongside `roboto-delta-mini`

These two fixtures pair to demonstrate the two main parametric-axis
layouts real-world variable fonts use:

| | `crispy-mini` | `roboto-delta-mini` |
|---|---|---|
| Source format | `.glyphs` | `.designspace` + UFOs |
| Parametric axes | `XOPQ`, `YOPQ`, `XTRA` (unified) | `XOUC`, `YOUC`, `XTUC` (uppercase-scoped) |
| Glyph scope | Affects every glyph (caps + lowercase + digits) | Only uppercase glyphs respond; lowercase renders from the default master |
| Starting instances | 1 (`Default` at first master) | 1 (`Default` at first master) |
| Designer intent | "Even tone — one stroke axis describes the whole font" | "Case-aware — caps need different optical tuning than lowercase" |

Crispy's even-tone design means one set of parametric axes covers the
whole font; Roboto Delta's broader optical-size brief forced a
case-split layout (the upstream ships uppercase + lowercase + figures
axes separately — this fixture only ships the uppercase trio). Both
fixtures end up at the same destination through avar2-studio: a VF with
an avar2 table mapping a user-facing `wght` slider to a coherent
parametric location.

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
