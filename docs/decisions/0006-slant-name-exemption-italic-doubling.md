# 0006 — Styles named "Italic"/"Slanted" are exempt from the slant stamp; the instance list is doubled gftools-style

- **Status:** Accepted
- **Date:** 2026-10-10 (`5e8f71f`), finished October 2026
- **Area:** `transforms/builtin_slant.py`, `transforms/slanting.py`, `wasm/fontc-web/src/fix.rs`

## Context

The slant transform is a source-stage step that builds italics at build
time on a `slnt` axis: every master gains a slanted twin at its own angle
(linear in mean advance between the Narrowest 11° and Widest 6° dials —
the rule that made XCrispy's italics — overridable per master), the
shared axis end (slnt = −steepest angle) means "each master fully
slanted", and each named style takes its slant as a **percent of the
axis minimum** (`default_pct` / `style_pcts`, the S badge), stamped as
fvar instance coordinates in the transform's font-stage `apply()`.

The "Clean fvar instances" transform (gftools `fix_instances`) **doubles**
the instance list of a font with a `slnt`/`ital` axis: the Roman weight
set plus an Italic set ("Thin Italic" …, "Regular Italic" → "Italic")
pinned at that axis's minimum. Without a rule, the percent stamp then
overwrote gftools' Italic instances back to the default percent — upright
"Thin Italic"s — which is exactly what the desktop did before this
decision.

## Decision

A style whose name contains **"Italic" or "Slanted" (case-insensitive) is
italic by name and is never stamped**: it keeps its own `slnt`
coordinate, so the doubled Italic set stays at full slant while the Roman
set follows `default_pct` / `style_pcts`. The rule lives in
`is_slant_exempt` on both sides (`builtin_slant.py` and `fix.rs`); the
desktop `validate()` rejects a `style_pcts` entry naming an exempt style;
the instance rows hide the S badge for them. The wasm `fix_instances`
port does gftools' doubling, oracle-compared against real gftools on a
slanted WasmTest build (`tests/fix_oracle.rs`).

Mechanics the decision assumes (mirroring the Slant Master plugin's
settled choices): shear about y = x-height/2, LSB (node bounds) and
advance preserved, no extrema correction, x grid-snapped `floor(x+0.5)`;
components take the translation compensation `dx += dy·tan` (their 2×2
stays as drawn — the Glyphs component model has no shear term); anchors
shear about y0; brace layers interpolate their angle on the parametric
plane. **Slant runs before rounding** (`source_priority` 10 vs 50) so
rounds land on the slanted drawings and arcs stay round.

## Consequences

- Two traps both engines hit, now fixed and regression-checked (e2e 27's
  "ROND 100 moves points", all of e2e 28): a twin master layer must **not**
  carry `associatedMasterId` — fontc's glyphs-reader classifies any layer
  with the key (even equal to its own layerId) as a non-master and silently
  drops its geometry, so the axis appears in fvar, gvar gets nothing and the
  axis is inert (glyphsLib omits the key on save, which is why the desktop
  never hit it; the wasm engines remove it on master twins); and glyphsLib
  positions must be written as `Point`, not tuples — a tuple assigned to
  `comp.position` / `anchor.position` serializes as a quoted string
  (`pos = "(88, 0.0)";`) that fontc rejects, which only shows at
  save+compile time, never in an in-memory pytest. A transform that writes
  positions must round-trip one save through fontc in its test.
- The cargo oracle (`tests/slant_oracle.rs` + `spike/compare_slant.py`)
  compares node-for-node including components and anchors: delta 0.000000
  over ≈37 k points, both param sets.

## Where it lives

- `transforms/builtin_slant.py` (`is_slant_exempt`, spec: `stage="source"`,
  `injected_axis_tag="slnt"`, `source_priority=10`,
  `font_stage_param_keys=("default_pct", "style_pcts")`),
  `transforms/slanting.py` (`master_angles`).
- `tests/test_slant_transform.py`; `wasm/fontc-web/src/slanting.rs`,
  `src/fix.rs` (`stamp_pct_instances`, shared with ROND), `tests/fix_oracle.rs`,
  `tests/slant_oracle.rs`.
