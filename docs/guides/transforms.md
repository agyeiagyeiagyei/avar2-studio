# Build transforms

Optional steps that run on every build, saved per project in
`MyFont-transforms.json` and switched from the **Transforms** menu.
There are two kinds, and the order they run in is fixed by the pipeline
(see [ARCHITECTURE.md §4](../ARCHITECTURE.md#4-pipeline-order-and-its-physics)):

- **Font-stage** (`stage="font"`, the default): a VF → VF step over the
  compiled font — one `.ttf` in, a `.ttf` out. Never touches the source,
  the shadow or the CSV.
- **Source-stage** (`stage="source"`): a pre-compile step over the
  composed shadow `.glyphs` (never the designer's file), after the
  control-axis and grade braces are generated, so everything that
  interpolates goes through it together. Slant and round corners live
  here; they also have a small font-stage `apply()` that stamps per-style
  values onto the fvar instances.

## The built-ins

| Id | Name | Stage | Axis | Parameters |
|---|---|---|---|---|
| `spac` | Spacing — uniform (gftools) | font | `SPAC` | `min` (−20), `max` (40) |
| `spac_widthaware` | Spacing — width-aware | font | `SPAC` | `min` (−40), `max` (40), `bias` (1.0, 1–4), `scale` (1.25, 0.1–10) |
| `fix_instances` | Clean fvar instances | font | — | — |
| `gen_stat` | Rebuild STAT table | font | — | — |
| `fix_unhinted` | Smooth unhinted rendering | font | — | — |
| `round_corners` | Round corners | source (+ font-stage stamp) | `ROND` | `default_pct`, `outer_pct` (15), `inner_pct` (5), `outer_xtra_pct` (3), `inner_xtra_pct` (1), `outer_min`, `inner_min`, `style_pcts` (table), `master_overrides` (table) |
| `slant` | Slant | source (+ font-stage stamp) | `slnt` | `default_pct`, `narrow_angle` (11), `wide_angle` (6), `style_pcts` (table), `master_angles` (table) |

At most one *enabled* transform may inject a given axis tag (two SPAC
injectors would produce a font with two SPAC axes); the PUT refuses it.
`round_corners` requires the source to declare `XOPQ`
(`requires_axis_tags`) and is refused otherwise with a message naming the
missing tag.

### Spacing (SPAC)

Both spacing transforms add a `SPAC` axis whose effect is purely
sidebearings and advances: only the phantom points move, outlines are
byte-identical across the axis. Because the axis has no source master,
it shows up in the studio as an ordinary slider under *Transform axes*
(`get_axes` overlays the built font's fvar). Each named style can carry
its own `SPAC` coordinate: it lives in the `SPAC` column of
`MyFont-avar.csv` (minted on first use, excluded from avar2, read by the
transform when it pins instance coordinates; blank = 0).

- **Uniform** shells out to `gftools-gen-spac` on a copy with `--inplace`
  (its `--out` option is buggy upstream). Its numbers are **per side**:
  ±N ≈ ±2N advance units.
- **Width-aware** is the studio's own: each glyph's delta scales with its
  ink width relative to the font's own mean ink width (`(ink / mean_ink)
  ** bias`), so the font loosens by a consistent *proportion* — an
  average-width glyph tracks exactly like the uniform transform, wide
  glyphs get proportionally more, narrow less; composites are spaced too
  (gen-spac skips them). Two things learned the hard way: moving the
  *left* phantom shifts the whole component, so composites get
  left-phantom-only treatment, and `USE_MY_METRICS` composites inherit
  their base's delta and are skipped. The transform prefers the native
  Rust injector `spac-inject` (the same engine the browser demo runs,
  parity-tested by `tests/spac_oracle.rs`; 300 s timeout) because it
  splices tuples with write-fonts' compact encoding; the fontTools
  fallback re-encodes the whole gvar table and can overflow its 64 KB
  per-glyph header ceiling on twin-heavy fonts, which is why the log says
  which path ran. The native parser splits the CSV on commas and expects
  a literal `SPAC` header, so an instance name containing a comma loses
  its per-style SPAC on that path.

### gftools fixers

Thin wrappers that load the font, call the gftools function in-process
and save in place (atomic tmp + replace): `fix_instances` regenerates
the named instances (and, with a `slnt`/`ital` axis, doubles them into a
Roman and an Italic set — see [decision 0006](../decisions/0006-slant-name-exemption-italic-doubling.md)),
`gen_stat` rebuilds STAT from the axis registry, `fix_unhinted` applies
the unhinted-rendering fixes.

### Round corners

Runs on the shadow just before the compile: every line-line corner of
every master and brace gains a round whose radius blends the layer's
stroke (XOPQ) with its width (XTRA), outer corners and counters
separately, with per-master unit overrides when the formula is not
enough. Enabling it adds a `ROND` axis (0–100, default 0 = sharp) that
rounds live — advances never move along it — and each named style takes
its rounding as a percent: **Default rounding %** in the menu, an **R
badge** per row. Percent-only edits take a fast path (the shadow is not
re-rounded). The geometry, the rendered-fill corner classification that
makes it work on overlap-built counters, the oracle discipline and the
known limits are in [decision 0005](../decisions/0005-rond-axis-per-style-percent-rendered-fill.md).

### Slant

Runs one step earlier (so rounds land on the slanted drawings): every
master is sheared at its own angle — steeper for narrow masters,
shallower for wide, between the two dials, overridable per master in
degrees — about half the x-height, sidebearings and advances preserved,
on a `slnt` axis (0 = upright). Each named style takes its slant as a
percent (**Default slant %**, the **S badge**) — except styles whose name
contains *Italic* or *Slanted*: those are italic by name, never stamped,
and keep their own `slnt`, so gftools' Italic set stays at full slant.
[Decision 0006](../decisions/0006-slant-name-exemption-italic-doubling.md).

## Ordering, failure and idempotency

- Source stage: `slant` (priority 10) then `round_corners` (50),
  whatever the sidecar order. Font stage: the sidecar's order, except
  that the source-stage transforms' stamps run **last** (after
  `fix_instances` has regenerated the instance list), and the Kinetikern
  bake runs **first**, before SPAC.
- Before the chain runs, any axis a font-stage transform is about to
  inject is stripped from the incoming font (pinned to its default and
  instanced away), so applying the chain twice lands in the same place.
  Source-stage axes (ROND, slnt) are built into the compile and are never
  stripped.
- A transform that raises, or returns a path that does not exist, is
  skipped and the chain keeps the last good font; the failure is recorded
  in `LAST_TRANSFORM_ERROR` (`transform_error` in `/api/health`) so the UI
  can show an enabled-but-failing transform instead of a silent no-op. A
  source-stage failure keeps the untransformed source and sets
  `LAST_SOURCE_TRANSFORM_ERROR`; `avar2-studio build` exits 1 on it.
- Parameters are validated **before** they are persisted
  (`Transform.validate`), so a bad configuration is rejected at the API
  with a message rather than failing at build time.

## Writing your own

Drop a `.py` file into `~/.avar2-studio/transforms/` (the studio creates
the folder, with a README template, on first run). It must define a
`Transform` subclass with a `spec = TransformSpec(id=…, name=…, params=[ParamSpec(…)])`
and an `apply(self, vf_path, params, ctx) -> Path` that returns the
(possibly new) `.ttf`, written into `vf_path.parent`. `ctx` carries
`build_dir`, `source_path`, `glyphs_path`, `family` and a `log` callable.
Discovery runs once per process: new scripts appear after the next
launch. Scripts run in-process — the same trust model as a Glyphs or
fontmake plugin — so only add scripts you trust. A malformed script is
logged and skipped, never crashes the studio.

## Where it is stored

`MyFont-transforms.json` — the ordered list of `{type, enabled, params}`;
schema in [SIDECARS.md](../SIDECARS.md#stem-transformsjson). The
`transforms` section of a configuration bundle carries the same list and
is replaced wholesale on import.
