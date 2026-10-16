# 0012 — Axis tags keep their authored case: foundry tags uppercase, registered tags lowercase

- **Status:** Accepted
- **Date:** verified end to end 2026-10-10 (`SWCH` probe on a Crispy copy); committed 2026-10-16 (`6996bbd`); Fontra session/response and `update_axis` leftovers closed the same day (sprint T1.12–T1.14)
- **Area:** `control_axes._validate_tag`, `_normalise`, `csv_io.normalize_in_axis_name`, axis metadata

## Context

fvar tags are case-sensitive. The sidecar used to lowercase every control
axis tag, while the CSV uppercases every header and the OpenType
convention is the other way round: unregistered (foundry) axes are
uppercase (`XOPQ`, `GRAD`, `ROND`, `SPAC`, `LCWD`, `YMOD`) and registered
axes lowercase (`wght`, `wdth`, `opsz`, `slnt`, `ital`). The mismatch was
not cosmetic: when a CSV out-column header's case differs from the fvar
tag, gen-avar2 invents a second zero-range axis — a Crispy build was seen
in the wild with both `LCWD` and `lcwd`.

## Decision

- The control sidecar **preserves the authored case**: `_validate_tag`
  enforces four printable ASCII characters and no longer lowercases;
  `_normalise` re-emits tags as written. Matching stays
  **case-insensitive** everywhere (`.lower()` comparisons; duplicate
  checks mean two axes can never differ by case alone); labels print the
  authored case (`"YMOD 100"`, not `"ymod 100"`), including in the Fontra
  session, the open-editor response and the shim's label regex.
- Convention: **foundry tags uppercase, registered tags lowercase.** The
  CSV keeps uppercase headers for everything; `normalize_in_axis_name`
  maps the registered ones (`WGHT`→`wght`, `CONTRAST`→`cntr`, legacy
  `-e` suffix stripped) back to lowercase; `axis-metadata.json` keys are
  uppercase with a lowercase `registered_tag`; the built-font filename
  preserves case per tag (`Crispy[GRAD,LCWD,…,opsz,slnt,wdth,wght].ttf`);
  the slant transform injects lowercase `slnt`, rounding and spacing
  inject uppercase `ROND` / `SPAC`.
- The Add-axis modal accepts uppercase tags.

## Consequences

- Still lowercased (known, audit §4): designspace shadow labels and pool
  slugs. Ad-hoc `.upper()` / `.lower()` sites (~40) remain the symptom
  of there being three CSV readers and three axis-split rules; one axis
  model across Python, Rust and JS is sprint item T3.5.
- Studio data authored before the change holds lowercase tags; nothing
  migrates them, and nothing needs to — matching is case-insensitive.

## Where it lives

- `control_axes._validate_tag` (docstring states the convention),
  `control_axes._normalise`, `regenerate_shadow` (`tag_by_index`),
  `update_axis` (case-insensitive range check).
- `csv_io.normalize_in_axis_name`, `server._load_axis_metadata`
  (`registered_tag`), `server._initialize_preview_config_from_glyphs`
  (filename case).
- `tests/test_control_axis_tag_case.py`, `tests/test_open_editor.py`
  (the `"XOUC"` pin).
