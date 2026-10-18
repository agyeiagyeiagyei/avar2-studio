# 0007 — Export "set default": the inserted-master method is rejected; lossless relocation to a parameterised default is the direction

- **Status:** Accepted direction, **not built** (sprint item T3.8). The code still runs the inserted-master staging.
- **Date:** 2026-07-21 inserted-origin-master export (`edd4b5a`); 2026-10-04 measurements; 2026-10-16 direction recorded (audit §2)
- **Area:** `server._export_pipeline`, `server._build_export_source`, `server._build_in_process`

## Context

The export dialog's "set a default" picks an axis combination and ships a
font that opens at that style. The 2026-07 implementation — "the
designer's formulation" — materialises the loaded font as a designspace
with **one added master**, a fully interpolated instance (outlines,
metrics, kerning) at the mapped parametric location, set as the default,
so fontc compiles the export resting on that master natively with
natural-size gvar (≈0.67 MB on Crispy, against the ≈6–7× split-region
growth of a binary instancer rebase).

On 2026-10-04 both "insert one interpolated master and recompile" routes
(designspace + fontmake `Instantiator`; native `.glyphs` insertion) were
built and **measured**: exact at master corners (0.0 u), but the
re-solved model pins the field through the new centre and deviates
**between** masters — 468 u at an interior corner, 223 u at an authored
style row on the CrispyMini fixture, frozen YOPQ-only movement at the
origin. It is a subtly different font that silently shifts calibrated
styles. (The earlier designspace attempt's "lost YOPQ deltas", first
blamed on glyphsLib conversion infidelity, was this same math; glyphsLib
was innocent.) What *was* real: `export = 0` glyphs break every
designspace compile ("interpolation-incompatible paths") because the
export-flag lib key lands on only some masters' glifs — so
`_build_export_source` strips non-exporting glyphs, their kerning and
groups.

The current pipeline compounds this (audit §2 H3, path verified, size
inferred): `_export_pipeline` rounds the export location
(`round(mapped)`) while the ORIGIN row keeps the CSV's decimals
(662.2 / 630.9 on Crispy); `_build_in_process` then sees a 0.2-unit
mismatch and runs the step-3.5 instancer rebase **as well** — the
densification the inserted-origin method exists to avoid. There is no
per-call switch: relocation reads `AVAR2_RELOCATE_BUILD_DEFAULT` only.

## Decision

The inserted-master formulation is rejected as the long-term method
because of the interior drift. The direction is a **lossless relocation
to a parameterised default P** — the same instancer rebase the preview
build uses (decision 0001), made an explicit per-build parameter:

- `_build_in_process(..., relocate_default)` replaces the environment
  read (the env value stays the default); the export passes `False`
  for the step it does not want, or the requested location for the one
  it does;
- the `round()` vs decimal-row mismatch is reconciled (a tolerance, or
  the rounded location written back);
- the flag travels in the job `spec`, not `ENV_KEYS`;
- `_declared_axis_ranges` is computed once per build (done, T1.8);
- the first export test asserts that tuple counts stay natural
  (`_export_pipeline` has none today).

## Consequences

- Until T3.8 lands, preview and a "set default" export are **not
  point-identical between masters**, and the export may be denser than
  intended. Both are known and accepted for now.
- The baked-correction handling in `_build_export_source` (decision 0011)
  — merging braces coincident with masters over the master glyph, and
  dropping brace sources coincident with the new origin — stays
  necessary for any method that stages a designspace.

## Where it lives

- `server._build_export_source` (docstring), `server._export_pipeline`
  (the "authored rows come FIRST" lookup — an exact row match uses the
  authored `out:` because the preview table cannot express the ORIGIN
  row when relocation is off), `server._build_in_process` step 3.5.
- `build_jobs.JobQueue._stage` (export spec), `build_worker._run_export_job`.
- History: archived `HANDOVER-2026-10.md` §4.2 (the Oct 4 method note).

## Status update (2026-10-19)

Built: the export compiles the resolved active source with the lossless
relocation to the chosen instance's authored location; measured identical
to the preview at the ORIGIN style (0.0 u). Relocating to a far interior
style can exceed gvar's per-glyph header limit on a twin-heavy font;
`pipeline._fit_gvar_headers` prunes sub-visible tuples where needed and
otherwise fails with a readable error (export at the resting style, or
without slant/rounding twins — a proposed follow-up option).
