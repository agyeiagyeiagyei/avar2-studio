# 0004 — Kinetikern corrections are font-stage phantom-point tuples, not shadow braces

- **Status:** Accepted
- **Date:** 2026-10-15 (validated on Crispy); committed 2026-10-16 (`3693c19`)
- **Area:** `kinetikern/inject.py`, `server._apply_transform_chain`

## Context

The Kinetikern tab spaces a named style with Kinetikern2's Pass 1 and
stores the result as per-glyph side deltas in `<stem>-kinetikern.json`.
The obvious way to bake them — a sparse master (brace layer) at the
instance's location in the shadow — was built and validated at source
level (decomposed composites, off-plane braces shifted by the field) and
then dropped, for a measured reason:

- the build-time default relocation (decision 0001) multiplies every
  glyph's gvar tuples ≈15× (H: 55 → 838), and the preview font sits a
  few KB under gvar's **64 KB per-glyph tuple-header ceiling**;
- a sparse master at an instance location, with its slant and rounding
  twins, is shredded the same way (+150 tuples per glyph per instance),
  and the avar2 build fails with `int too big to convert` on
  `font.save()`.

## Decision

The bake is a **font-stage step at the top of `_apply_transform_chain`**
— after compile, relocation and avar2, **before SPAC**:

- one gvar tuple per corrected instance per glyph that moves phantom
  point 1 by −left and phantom point 2 by +right; outlines never move,
  composites stay rigid (a component ignores its base's phantoms),
  `USE_MY_METRICS` composites are skipped; `add_HVAR` afterwards;
- supports and deltas come from a fontTools `VariationModel` over the
  corrected locations, so every corrected instance is exact and the field
  blends between them; beyond the outermost instance the correction holds
  (OpenType keeps a tuple's full value past its peak when no end
  coordinate is written);
- locations are **quantized to F2Dot14 before the model**: a coordinate
  that rounds to the default must *be* the default, because a stored peak
  of 0 means "axis not involved" and a model that still saw a 1e-8 region
  there put a −900-unit delta on it (dollar came out with advance 0);
- a correction at the default location is the model's base value and goes
  in as a tuple with no axes (applies everywhere); the other tuples are
  solved relative to it;
- `strip()` removes the module's earlier tuples first, so re-running the
  chain is idempotent;
- deltas are keyed by **source** glyph name (fontc compiles production
  names: `euro` → `Euro`); the routes map by code point both ways;
- `-kinetikern.json` is in `build_jobs.SIDECAR_SUFFIXES` — the first live
  run built without it.

Decisions from the designer that shaped the feature: deltas (not absolute
values) in the sidecar, so a later recalibration of the masters carries
the corrections along; validate before building; no "fit to masters"; the
tab is named after the plugin; no extra axis.

## Consequences (the budget)

At the injection point the worst Crispy glyph (`g`) is at ≈44 KB of
tuple headers; all 30 instances fit (≈1.4 KB more). After SPAC's native
injector the same glyph is at ≈63 KB and only 18 would fit — which is
why **Kinetikern runs before SPAC**. The native injector re-encodes and
keeps the tuples (checked: error ≤ 1 unit). An overflow raises inside the
chain and is reported as a transform error; the font builds without
corrections. Verified live on Crispy: apply to all, rebuild, then
`propose` on several instances reports a remaining mean of 0 and
`|new − cur| ≤ 2`.

Known gap: `strip()` identifies its tuples by shape (phantom-only on the
parametric axes). The current preview font already carries 22 genuine
phantom-only compile tuples in `three`; an explicit marker is sprint item
T2.13.

## Where it lives

- `kinetikern/inject.py` (module docstring), `kinetikern/__init__.py`
  (`propose`, reads sidebearings from `hmtx`, not glyf bounds, because a
  phantom-point variation moves hmtx and leaves the outline).
- `server._apply_transform_chain` (ordering comment), `_kinetikern_apply_one`
  (stores `existing + (engine target − current build)`).
- `tests/test_kinetikern.py`, `tests/test_kinetikern_inject.py`.
- Guide: `docs/guides/kinetikern.md`.
