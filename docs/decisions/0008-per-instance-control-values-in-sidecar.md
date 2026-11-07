# 0008 — Per-instance control-axis values live in the control sidecar, never as CSV columns

- **Status:** Accepted
- **Date:** 2026-10-11 (`66ce7ca`)
- **Area:** `control_axes.instance_values`, `server.update_instance`, `server._stamp_control_instance_values`

## Context

A named style can sit at a chosen value of a secondary (control) axis —
`LCWD 100` for a lowercase-corrected ultra, `YMOD` for the baked
horizontal correction. The obvious home, a column in `<stem>-avar.csv`
next to the parametric coordinates, is poison: the CSV reader
**uppercases headers**, and every column that is not a mapping input
counts as a required parametric output, so a `ymod` column minted a bogus
`YMOD` user axis, blank cells aborted the whole avar2 build
("parametric axis 'lcwd' is blank"), and the bogus axis then compiled as
a real fvar axis — the LCWD/SLNT/YMOD/ROND column bug. SPAC, the one
transform-injected coordinate the CSV does carry, works only because the
config generator excludes it from `out:` and the SPAC transforms read it
themselves.

## Decision

- `<stem>-control.json` carries `instance_values: {tag: {instance name:
  value}}`, written under `_INSTANCE_VALUES_LOCK` and carried through
  `_normalise` so layer edits (which round-trip the whole sidecar) never
  wipe it; renames and deletes fan out to it.
- `PUT /api/instance/<name>` splits incoming coordinates three ways:
  source axes go to the CSV row and the source writeback, `SPAC` goes to
  the CSV row only, control-axis values go to the sidecar; everything in
  `_non_mapping_axis_tags()` (control axes, GRAD, every transform-injectable
  tag) is subtracted from any CSV schema or row derivation.
- `GET /api/instances` hydrates the values back onto each row; the build
  stamps them onto matching fvar named instances in
  `_stamp_control_instance_values`, which runs **after** the transform
  chain because `fix_instances` regenerates the instance list.
- Studio-only rows have no fvar instance to stamp (same as SPAC); the UI
  previews them from the hydrated coordinates.

## Consequences

- The CSV stays an avar2 artifact: `Instance Name`, parametric columns,
  user-axis columns, and `SPAC`. Nothing else may become a column.
- The config bundle carries the values inside its `control_axes` section
  for free.

## Where it lives

- `control_axes.instance_values` / `set_instance_values` /
  `rename_instance_values` / `remove_instance_values`, `_normalise`.
- `server.update_instance`, `server._non_mapping_axis_tags`,
  `server._stamp_control_instance_values`.
- `tests/test_instance_control_values.py`, `tests/test_instance_csv_schema.py`,
  `tests/test_instance_spac_persistence.py`.
- Schema: `docs/SIDECARS.md`.
