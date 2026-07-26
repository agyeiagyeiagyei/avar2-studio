# Grade — how it works

The user-facing overview is in the [README](../README.md#grade). This document
covers the mechanics. **Draft — to be expanded.**

## What a grade is

A same-advance weight adjustment: darkens/lightens a style while every glyph
keeps its advance width, so text never reflows. Implemented as a `GRAD` axis
(−10 … 0 … +10) driven per-instance.

## The pure-weight model

One `grade%` knob per instance; the rest is derived:

- `dXOPQ = grade% × XOPQ` — stem weight (driver)
- `dYOPQ = grade% × YOPQ` — horizontal weight (driver)
- `dXTRA = COMP_RATIO × dXOPQ` — counters follow to hold the width (`COMP_RATIO ≈ 2.0`)

Weight leads; counters follow. Because `XTRA` scales with the stem move (small
on light styles), the grade reads as weight, not condensing, across the range.

## Advance holding (equalisation)

Per-glyph metric equalisation locks each glyph's advance to its base value —
so advance is held *exactly*, independent of the XTRA/XOPQ balance. `XTRA` just
keeps the equalisation trims small.

## Source-level generation

Grade is a source transform (not a post-build VF→VF step). It injects `GRAD`
brace layers + virtual masters onto a **shadow** `.glyphs` (never the
original), composing after the control-axis shadow. `grade.py` (model +
sidecar) / `grade_shadow.py` (brace generation).

- brace placement, interpolation, equalisation
- virtual masters + why the axis needs them
- composition with control axes; idempotent regeneration

## Why the shipped font stays compact

Brace layers bloat the intermediate `.glyphs`, but the compiler collapses them
into `gvar` deltas — the built font grows only modestly (a few KB of `gvar`),
not by full masters.
