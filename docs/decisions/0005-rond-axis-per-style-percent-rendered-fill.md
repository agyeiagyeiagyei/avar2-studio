# 0005 — Rounding is a ROND axis with per-style percentages; corners are classified by the rendered fill

- **Status:** Accepted
- **Date:** 2026-09-29 source-stage transform (`120c635`); 2026-10-02 ROND axis + wasm port (`388c3a1`); 2026-10-03 per-style % (`b45084c`); 2026-10-11 rendered-fill classification (`fe5376d`)
- **Area:** `transforms/corner_rounding.py`, `transforms/builtin_round_corners.py`, `wasm/fontc-web/src/round_corners.rs`

## Context

Crispy's source is drawn sharp; rounding is a build-time finishing pass
over the composed shadow (never the designer's file) so it stays tunable
and switchable. Three questions had to be settled: how the designer
chooses how much rounding each style gets, whether rounding is a
fixed bake or an axis, and how a corner knows whether it is an outer
corner or a counter when Crispy builds counters as overlap.

## Decision

**Rounding IS the ROND axis, grade-style.** Enabling the transform always
builds sharp/rounded twins of every master and brace on `ROND` 0–100
(default 0 = sharp). Per-style rounding is a **percent per named
instance** — `default_pct` broadly, `style_pcts` per style (the instance
rows' R badge) — stamped as each fvar instance's ROND coordinate in the
transform's font-stage `apply()`. Styles explore live on the axis;
percent-only edits take the fast rebuild path (`font_stage_param_keys`
on the spec — the server skips the shadow re-round when only those
changed). Two ordering rules the chain enforces: the stamp runs **after**
`fix_instances` (which regenerates the instance list), and the chain's
injected-axis strip covers FONT-stage injectors only (stripping ROND
would tear the axis out of the compile). Twinning trap: a layer copy
that still shares its parent glyph registers itself with the glyph the
moment `layerId` is assigned — copy detached, append later.

**Radius blend.** outer = `outer_pct`%·XOPQ + `outer_xtra_pct`%·XTRA,
counters likewise with the inner dials; floors for the light end; the
tangent offset capped at 2r (`T_CAP`) on shallow bends; clamped to 48 %
of the shorter adjacent segment (`ROOM`); under 1 unit the quad collapses
onto the corner — the same four nodes, coincident, which keeps every
layer point-compatible. **Concentric thin walls**: an ink-concave corner
within an outer round's radius, reached through ink, takes (outer radius
− wall) instead of the counter share, or the outer arc everts a hairline
wall. **Per-master overrides** (`master_overrides`, units) are pinned
deltas on the parametric plane via a fontTools `VariationModel`: exact at
named masters, zero at unnamed ones, blended between; italic twins share
their upright's value; unknown names fail the build loudly.

**Classification follows the rendered fill, never contour identity.**
Crispy builds counters as overlap — b/n/q are single self-crossing
contours, some masters fold the counter path over itself — so direction
sign, even-odd containment and other-contour winding all misread them
(fold corners took the outer radius and bit the white: "counters rounded
in on themselves"). Each corner now probes the **nonzero winding** — the
renderer's own rule — at the two sector midpoints on a circle around it,
radius a quarter of the gap to the nearest non-incident segment (clamped
to 0.25…4 units, so hairline shells two units apart still resolve). Both
probes ink → buried construction (R's leg in its stem, a fold flap),
collapses; both white or no room to probe → ambiguous, collapses;
otherwise the white sector's span picks ink-convex (outer) vs ink-concave
(inner). A correction layer's radius follows its *target*'s XOPQ/XTRA.

## Consequences

- The static demo runs the same engine ported to the wasm crate
  (`round_corners_source`, mutating the plist before fontc-wasm compiles,
  overrides via fontdrasil's VariationModel, deterministic twin ids). The
  cargo oracle compares node-for-node against the Python engine on
  CrispyMini: **delta 0.000000 over ≈77 k nodes, both modes**;
  `tests/fixtures/FoldedMini.glyphs` (Crispy's folded-counter n and d) is
  what catches classification drift.
- One parity gap by design: computed control/grade braces in the static
  build are bytes-level tuples derived from already-rounded masters, so a
  correction layer's radius there is the interpolated one, not the
  desktop's own-target radius.
- Known limits (a few units, in ink, at extremes): where the source itself
  everts (counters filling past wght 900 — intended design), a rounded tip
  can cross the already-crossing contour; at high width shares an arm-end
  arc can graze the far edge of a tapering wedge.
- A "white-room cap" for ROND was discussed and parked; no written record
  of its terms exists (see the decisions index).

## Where it lives

- `transforms/corner_rounding.py` (constants `ROOM`, `T_CAP`, `PROBE_MAX`),
  `transforms/builtin_round_corners.py` (spec: `stage="source"`,
  `injected_axis_tag="ROND"`, `source_priority=50`,
  `requires_axis_tags=("XOPQ",)`).
- `tests/test_round_corners_transform.py` (the spec, 30+ tests),
  `tests/test_round_corners_styles.py`.
- `wasm/fontc-web/src/round_corners.rs`, `tests/round_corners_oracle.rs`,
  `spike/compare_round_corners.py`.
