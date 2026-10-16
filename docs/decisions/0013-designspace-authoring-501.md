# 0013 — Secondary-parametric-axis authoring is .glyphs-only; designspace sources get a clean 501

- **Status:** Accepted
- **Date:** 2026-10-16 (sprint T2.15)
- **Area:** `src/avar2_studio/server.py` (`_designspace_authoring_refusal`), `src/avar2_studio/control_axes.py` (`_regenerate_shadow_designspace`)

## Context

`create_control_axis` advertised both source formats, but the designspace
shadow path had drifted far behind the .glyphs one: its regen ignores
`target` (computed correction layers) and `outline` (captured drawings),
has no seed signatures (so no seed-vs-edit tracking), lowercases labels,
has no instance aliases, keeps "existing glyph wins" permanently, and
writes non-atomically. Reaching parity is a large port (the whole
capture/stamp/alias/atomic-write stack re-done over pooled UFOs), with no
current user asking for it.

## Decision

Secondary-axis **authoring** routes refuse .designspace sources with a
plain **501** (`_designspace_authoring_refusal`): POST `/api/control-axes`,
PUT/POST layers (+delta), reseed, PATCH/DELETE axis. Reads keep working —
`GET /api/control-axes`, `open-editor`, `reference-metrics` — and existing
sidecars still build (the designspace shadow regen still runs on load).
Nothing is silently half-written anymore.

## Consequences

- The failure mode moves from silent drift to an explicit refusal with a
  reason and a pointer here.
- Parity remains possible later: this ADR stands until someone ports the
  capture/stamp stack to the designspace shadow, at which point the 501
  guard comes out and this flips to superseded.
- .glyphs authoring is untouched (all of T0.x/T2.x applies there only).

## Where it lives

- `src/avar2_studio/server.py` — `_designspace_authoring_refusal` + the
  route guards; coverage in `tests/test_designspace_authoring_501.py`.
- Sprint log: `docs/sprints/cleanup-2026-10/LOG.md` (T2.15).
