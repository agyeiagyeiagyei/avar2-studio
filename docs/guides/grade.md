# Grade — how it works

Darken or lighten a style **without changing its advance widths**, so
text never reflows. Turn **Grade** on in the Transforms menu, then click a
style's **`G`** badge to set its percentage; grades interpolate between
the styles you set, so you grade a few anchors, not every style. This
document covers the mechanics; the file format is in
[SIDECARS.md](../SIDECARS.md#stem-gradejson).

## What a grade is

A same-advance weight adjustment, implemented as a registered `GRAD` axis
(−10 … 0 … +10) driven per instance. Rows on the Instances tab render at
GRAD 0 — the badge alone marks the grade; the Preview tab's GRAD slider
(in the *Transform axes* group, like SPAC) is where the grade is
inspected. The axis only materialises in the built font once the toggle
is on **and** at least one instance is graded: with none there are no
brace layers and the axis would collapse.

## The pure-weight model

One `grade%` knob per instance; the rest is derived (`grade.py`):

- `dXOPQ = grade% × XOPQ` — stem weight, the driver
- `dYOPQ = grade% × YOPQ` — horizontal weight, the second driver (`K_YOPQ = 1.0`)
- `dXTRA = COMP_RATIO × dXOPQ` — counters follow to hold the width (`COMP_RATIO = 2.0`: one unit of XOPQ widens about two, so XTRA reclaims about two — stable across weights)

Weight leads; counters follow. Because XTRA scales with the *stem* move
(small on light styles), the grade reads as weight, not condensing,
across the range. The light and dark braces take half the move each way:
a 25 % grade at XOPQ 200 puts the dark brace at XOPQ 225 and the light
one at 175.

## Headroom and the achieved move

The driver's move is limited to what **both** its own axis range and the
follower's room can absorb, and the horizontals are then scaled by the
fraction actually achieved (`grade.grade_coords`). Two things fall out:

- An instance already at the XOPQ ceiling darkens by nothing, and its
  counters and bars stay put. Before August 2026 the follower was computed
  from the *requested* stem move and clamped separately, so grading an
  instance at the parametric box ceiling moved XTRA by the full amount:
  the "dark" brace became a pure counter-condense inside a held advance
  and the spacing deformed, worse as the percentage grew. The follower
  now tracks the **achieved** driver move per side, in both engines
  (`grade.py` and `braces.rs`); away from the edges nothing changed.
- **`clamp_to_headroom`** (on by default, "Limit grade to counter
  headroom" in the menu): an instance pinned at the XTRA floor — the
  widest, heaviest corners, where counters are already as tight as the
  design allows — has no room to open counters, so it does not thicken
  either. Without the cap every added stem unit lands in the counters and
  the grade bleeds shut. Turn it off for the uncompensated look; advance
  is held exactly either way.

## Intensity

**Intensity %** is a global multiplier on every instance's `grade%`: the
built grade is `pct × intensity`. One knob ratchets the whole axis up or
down without re-tuning each anchor; 0 disables every grade without
forgetting the authored values; 1.0 is "as authored". **Default grade %**
only seeds newly graded instances — changing it never alters the built
font, so it saves without a rebuild.

## Caps and diagnostics

The badge's slider is bounded by **`max_pct`** (`grade.max_pct_for`): the
largest percentage before any axis would clamp at that instance, so a
grade the parametric space cannot deliver is simply unreachable. A cap of
exactly 0 is a real answer — an instance on the XTRA floor can take no
grade — and is reported as such (filtering zero caps out once advertised
the next-loosest axis's cap instead, which is how the widest instances
came to offer grades that could only bleed into the counters).
`GET /api/transforms/grade` returns the sidecar plus `max_pct` per
instance and `diagnostics` — `error` (the axis will not build, or this
instance contributes nothing), `warning` (builds but degraded), `info` —
which the Grade panel shows, because these failure modes are invisible in
the preview until you drag the slider to an extreme.

## Source-level generation

Grade is a source transform, not a VF → VF post-build step. For every
graded instance `grade_shadow.apply_grades` injects onto the **shadow**
`.glyphs` (never the original), composed after the control-axis shadow:

1. A per-glyph interpolator over the parametric masters **plus any
   parametric curve braces** (so round glyphs get fontmake-accurate
   geometry). Masters off the parametric plane — an italic master at
   `ital 1`, which shares its upright's parametric coordinates — are
   excluded, or the model fails with "Locations must be unique"; grading
   the plane grades the family, because the italics are sheared from the
   uprights width for width.
2. The glyph's outline interpolated at the instance's **light** and
   **dark** grade coordinates (the instance's *true* avar2-mapped
   parametric location, taken from the source instance or the CSV row).
3. **Equalisation**: each brace's advance is set to the glyph's base
   advance at the instance (the interpolated width, rounded), shifting the
   outline symmetrically — this is what holds the advance across GRAD with
   zero phantom-point delta.
4. The two braces at `(instance parametric coords × GRAD ∓10)` and a
   **Virtual Master pair** at the same location × GRAD extremes, so the
   GRAD axis has a real range.

Regeneration is idempotent: the grade pass starts from the freshly
regenerated control shadow (or a fresh copy of the original when no
control axis exists), so re-runs never stack braces. `.glyphs` sources
only; `.designspace` grade is future work. A grade declared but not
generated sets `LAST_GRADE_ERROR` (`grade_error` in `/api/health`) and
`avar2-studio build` exits 1, because a font missing its GRAD axis must
not pass as a release.

## Why the shipped font stays compact

Brace layers bloat the intermediate `.glyphs`, but the compiler collapses
them into `gvar` deltas — the built font grows by a few KB of gvar per
graded instance, not by full masters. (The build-time default relocation,
not grade, is what makes the *preview* font large — decision 0001.)

## The static port

The browser demo injects the same braces as gvar tuples at GRAD ∓10 with
`apply_grade` in the wasm crate, verified against fontTools' instancer as
oracle; the cap model is ported in `grade-model.js` and oracle-tested
against the Python to 1e-9. One deliberate difference: the static GRAD
tuple is global along the axis (server braces are scoped to the
instance's parametric location), so multiple grades sum there — the
designer prefers the global behaviour for preview.

## Routes and storage

`GET|PUT /api/transforms/grade` (`enabled`, `default_pct`, `intensity`,
`clamp_to_headroom`), `PUT|DELETE /api/instances/<name>/grade`
(`{pct}`, omitted = the default). Everything persists in
`MyFont-grade.json`; per-instance grades survive the toggle being off,
and the configuration bundle carries the whole declaration.
