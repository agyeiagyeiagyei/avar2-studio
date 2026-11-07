# 0001 — Build-time default relocation is ON

- **Status:** Accepted
- **Date:** 2026-10-04 (mechanism); 2026-10-11 gate `AVAR2_RELOCATE_BUILD_DEFAULT` (commit `66ce7ca`); reaffirmed ON after the October "off" experiment
- **Area:** build pipeline (`server._build_in_process`, steps 0.5 and 3.5)

## Context

An avar2 table is an ItemVariationStore: every mapping row contributes a
delta from the compiled default, weighted by a tent built from the row's
*normalized input* location. Two facts follow:

1. **The default location maps to itself.** A mapping row whose `in`
   coordinates all sit at the user-axis defaults — the **ORIGIN row**,
   the one that says what the font should look like at rest — cannot be
   expressed by avar2 at all. fontTools' `_add_avar` silently used such a
   row as the model *base* and subtracted its `out` from every other
   row's deltas. Reproduced to the F2Dot14 unit on Crispy: XOPQ
   0.2158 − 0.1283 = 0.0875, XTRA's impossible negative delta; wght 900
   landed 616 / 201 / 121 units short of its authored location. Every
   consumer (fontTools, HarfBuzz, exports) was equally under-driven. This
   had been misdiagnosed for weeks as "the server's reflection evaluator
   understates the table" — the table was wrong, not the evaluator.
2. **Tents are zero-anchored at the input default.** Dropping the ORIGIN
   row from the table instead of relocating does keep every other stop
   exact, but then the whole *neighbourhood* of the default input
   collapses toward the compiled corner: not a point dip but a valley
   across the default cross. Observed: wght 399 mapped to XTRA 92 instead
   of ≈663. (The Aug 2026 "dead default cross" — rows only at the four
   extreme corners, every tent's near edge at normalized 0 — is the same
   physics seen from the authoring side; see the archived
   `debugging-2026-08-18.md` §1.)

## Decision

`_build_in_process` detects the ORIGIN row (step 0.5: every `in` value
equals the declared default of its axis; more than one such row fails
loudly) and, before gen-avar2 (step 3.5), **relocates the parametric
fvar defaults to that row's `out` location** with fontTools' instancer
(`instantiateVariableFont` with `(min, out, max)` limits, ranges kept) on
the still avar-free font. The ORIGIN row then compiles as a true identity
and every other row anchors to the real resting state.

This is **on by default**. `AVAR2_RELOCATE_BUILD_DEFAULT=0` restores the
small-but-valleyed font: the ORIGIN row is then excluded from the
preview table and `LAST_ORIGIN_WARNING` says so. The flag is read from
the environment only (it is forwarded to the build worker through
`build_jobs.ENV_KEYS`); making it a per-build parameter is decision 0007.

## Amendment (2026-10-20): what "the relocation cannot fit" now does

Sprint cleanup-2026-10 attacked the one failure mode this decision always
carried: on a twin-heavy source (slant + rounding brace layers) relocating the
origin to an interior row densifies gvar past OpenType's **64 KB per-glyph
tuple header** limit, and `fontTools` then refuses the save. The build used to
answer by serving the plain fallback — axes and mappings gone, status still
`ok` — which was the worst of both worlds.

Two things changed, neither of them the decision itself:

1. **(b) The failure keeps the avar2 font.** The relocation runs on the
   compiled font in memory and the save happens after the fit, so a failure
   simply keeps what is on disk: every axis, the avar2 table, the compiled
   default. `LAST_BUILD_STATUS` reports **`degraded`** (a third status, not
   `failed`): the preview is wrong about the resting style but every mapping is
   intact — and `failed` would close the build gate and stop the designer
   nudging the ORIGIN row back, which is the only way out. `origin_relocation_error`
   in `/api/health` carries the reason and the UI shows it.
2. **(c) The Preview approximates the resting state under (b).** With the
   default stuck at the compiled origin, the font's own avar2 table cannot
   anchor the resting style, so the Preview pins the parametric axes at
   `/api/mapped-location`'s post-mapping values for the current inputs and
   holds the user axes at their defaults — the same technique the instance rows
   use — with a notice saying the resting state is approximated by the mapping
   until the ORIGIN row is back on a master. An axis the designer drags is an
   explicit override and renders exactly (user axes at defaults ⇒ the avar2
   delta is zero).

The cost of the relocation is now paid once per (compiled font, target): the
result — success *or* the failure verdict — is memoised in
`<build dir>/.relocation-memo/`, keyed on a digest of the compiled font's tables
(`head` excluded: it carries a timestamp), the exact per-axis window and the
prune tolerance. On Crispy an unchanged-compile build drops from 57.8 s to
19.8 s, and the failure case from 37.3 s to 15.6 s.

## Consequences

- Relocating the origin to an interior point while preserving rendering
  everywhere **intrinsically** splits every corner-anchored gvar region
  around the new origin: ≈7× tuple growth, 57 KB → 495 KB of gvar on
  Crispy; with the rounding and slant twins the preview font reached
  9.1 MB (12 axes, 110 302 gvar tuples over 117 glyphs, `g` carrying
  1 531). Relocation densification is the dominant size driver of the
  preview font. This is not an inefficiency to optimise away.
- The preview font therefore sits a few KB under gvar's 64 KB per-glyph
  tuple-header ceiling, which constrains everything added after it
  (decision 0004, the native SPAC injector).
- Shipped fonts stay small either way: the export's "set default" uses
  the interpolated-origin-master method (≈0.67 MB measured) — decision
  0007 records why that method is nonetheless the wrong long-term answer.
- A preview that misrenders near its resting weight was judged worse
  than a large one; that is the whole trade.

## Where it lives

- `server._build_in_process` — steps 0.5 (detection, `LAST_ORIGIN_WARNING`,
  multi-row failure) and 3.5 (relocation), and the `not relocate_at_build`
  branch that drops the row.
- `tests/test_avar2_default_relocation.py` — exactness at every row on a
  real fontc compile; two default rows fail loudly.
- `build/_shims/gftools-gen-avar2` and `_declared_axis_ranges` — the
  declared `[min, default, max]` triples that make "sits at the default"
  decidable (upstream gen-avar2 would set default = min).
- Static demo gap: the wasm `add_avar2` still has the subtract-base
  behaviour; default-located rows are not to be trusted there until the
  relocation is ported (`set_default_location` exists in the crate).
