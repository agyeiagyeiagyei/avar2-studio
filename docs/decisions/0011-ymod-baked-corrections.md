# 0011 — Baked corrections: a correction axis rests at its corrected end (ymod default = 100), a dead axis by design

- **Status:** Accepted. Decided in conversation on Crispy; this record is distilled from the code comments the decision left behind.
- **Date:** 2026-10-11 (`66ce7ca`)
- **Area:** control axes on Crispy (`YMOD`), `server._build_export_source`, the Fontra shim, `server.update_control_axis`

## Context

A correction layer computes a glyph's outline "as if at" another
parametric point (`target` in the control sidecar). Crispy's `LCWD` is
the explorable kind: default 0, engaged by the avar2 mapping where the
lowercase needs it. Crispy's horizontal correction (`YMOD`, "Horizontal
correction") is different: the designer wants the correction in the
**resting** font, everywhere, with no slider to move — but the only
mechanism the studio has for a per-glyph outline change is a brace layer
on an axis.

## Decision

Declare the correction axis with its **default at the corrected end**
(ymod 0…100, default 100) and pin the correction layers at the axis
default over the master corners. The resting font then already carries
the correction; the axis's other end is the uncorrected drawing and is
never exposed. The axis is "dead" to users by design — it exists as the
carrier of the bake, not as a dimension anyone drives.

## Consequences

Several places had to stop assuming "a brace is at a non-default value":

- **The Fontra shim** identifies a studio brace by the presence of the
  control-axis key in the layer's own sparse source location, not by
  `value !== default` on the dense location — the old test went blind
  the day the default moved onto the braces' own value: masters and braces
  all read "at default" and every row got hidden
  (`_FONTRA_STUDIO_SHIM_JS`, `isStudioItem`).
- **Export staging** (`_build_export_source`): baked-correction braces
  can sit at a master's *exact* location. fontc renders the brace there,
  but fontmake's `Instantiator` refuses duplicate source locations
  ("Locations must be unique", first seen on glyph `B`), so each such
  brace glyph is copied **over** the coincident master's glyph and the
  brace source dropped; sources that sit exactly at the new export origin
  are dropped too (the instantiator already returns the source outline at
  an exact source location; keeping them hands fontc two sources at one
  location — "duplicate location", glyph `multiply`). A coincident *full*
  master fails loudly instead.
- **Axis-default edits** must re-run the full `_resolve_active_source`,
  not a bare `regenerate_shadow`: the compile source is the transformed
  shadow, and refreshing only stage 1 rebuilt with the old default
  whenever grade or rounding was enabled (observed: ymod default stuck at
  100 after the sidecar said 0).
- **The reference-metrics HUD** takes an explicit `location`: a glyph can
  carry several layers on the axis (`e`'s six ymod layers), so "the
  glyph's first sidecar layer" is the wrong place to measure.
- Decision 0008 is what lets a named style carry its own `YMOD` value
  without touching the CSV.

## Where it lives

- `server._build_export_source` (the two "baked-correction" blocks),
  `server._FONTRA_STUDIO_SHIM_JS` (`isStudioItem`),
  `server.update_control_axis`, `server.control_axis_reference_metrics`.
- `tests/test_correction_layers.py`, `tests/test_corner_names_injected_axes.py`
  (ymod at its default resolves to the master name).
- Guide: `docs/guides/secondary-parametric-axes.md` (correction layers).
