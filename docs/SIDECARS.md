# Sidecars and other files the studio writes

Everything the studio authors lives next to the source, named after the
source's stem, so it can be committed with it; tool-managed state lives
under `.avar2-studio/` beside the source and should be gitignored. Every
path is resolved against `ORIGINAL_PATH` — the designer's file — never
against the shadow. The schemas below are what the `_normalise` / `load`
functions accept and what the writers emit, as of 2026-10-16.

| File | Commit? | Writer(s) | Backup before rewrite | Atomic write | Lock |
|---|---|---|---|---|---|
| `<stem>-avar.csv` | yes | Instances tab (`update_instance` …), the source→CSV sync, config import, mapping routes | yes (`csv_io.backup_sidecar`) | no (sprint T2.1) | none (T2.1) |
| `<stem>-control.json` | yes | secondary-axes panel, the outline editor via capture, config import | yes | yes | `SIDECAR_LOCK` for layer mutations; `_INSTANCE_VALUES_LOCK` for instance values; `add_axis` / `update_axis` / `remove_axis` / `reseed` unlocked (T2.1) |
| `<stem>-grade.json` | yes | Grade toggle and G badges, config import | no | no | none |
| `<stem>-transforms.json` | yes | Transforms menu, config import | yes | no | none |
| `<stem>-kinetikern.json` | yes | Kinetikern tab | no | yes | none |
| `.avar2-studio/axis-metadata.json` | no | `_load_axis_metadata` (auto-populate), avar2 axis routes | yes | no | none |
| `.avar2-studio/instance-renames.json` | no | instance rename | no | no | none |
| `.avar2-studio/config.yaml` | no | `_initialize_preview_config_from_glyphs`, every build (`config_generator.update_config` in the worker, promoted back) | optional | no | — |

Backups go to `.avar2-studio/backups/<filename>.<YYYYMMDD-HHMMSS>`, the
last **20 per filename**; a slider session can evict the last pre-edit
copy in seconds, so a backup is a convenience, not an archive. Every
loader returns an empty schema-shaped document for a missing **or
unreadable** file and the next writer overwrites it (surfacing corrupt
files is sprint T2.10); `version` is read and re-emitted, never checked;
unknown keys are dropped at every level.

## `<stem>-avar.csv`

The authoring source of truth for named instances and avar2 mappings:
one row per instance, one column per axis.

```
Instance Name,XTRA,XOPQ,YOPQ,OPSZ,WGHT,WDTH
Narrow Thin 144,94.0,6.0,6.0,144.0,100.0,20.0
Ultra Wide Thin 144,2895.4,12.0,10.0,144.0,,200.0
```

- `Instance Name` is required, spelled exactly so. Headers are read
  case-insensitively (uppercased internally; the original spelling is
  kept for writeback). A UTF-8 BOM is tolerated.
- **Column split** (`csv_io.read_csv_mappings_with_axes`,
  `config_generator`): a column is a **parametric (`out`) axis** when its
  uppercased name matches a source axis tag that has master coverage;
  every other column is a **user (`in`) axis** — the avar2 mapping input.
  `normalize_in_axis_name` maps the registered ones to their lowercase
  tags (`WGHT`→`wght`, `CONTRAST`/`CNTR`→`cntr`, a legacy `-E` suffix
  stripped); anything else is lowercased as-is.
- A row with user-axis values is an avar2 mapping; a row with empty user
  cells is a plain parametric instance. A blank parametric cell is an
  error (it aborts the avar2 build); a blank user cell is skipped for
  that axis. Rows resolving to the same input location are deduplicated,
  first wins; `opsz` drops out of the mapping when every row shares one
  value (`skip_opsz_if_no_variation`).
- **`SPAC`** is the one transform-injected axis with a CSV contract: the
  per-style spacing coordinate. The column does not exist at bootstrap;
  `update_instance` **mints it on first use** and backfills the other rows
  blank (= 0). The config generator excludes it from `out:` everywhere;
  the spacing transforms read it themselves (`_instance_spac_overrides`)
  when pinning named instances. Control-axis values never become
  columns (decision 0008); `_non_mapping_axis_tags` is the exclusion set.
- **Bootstrap** (`_initialize_preview_csv_from_glyphs`): when the file is
  missing, one column per source axis (uppercased, master-backed or not)
  and one row per source instance, missing cells filled with 0.
- **Sync from the source** (`csv_io.update_csv_from_glyphs`, run by the
  watcher and before every staged build): adds rows for new source
  instances and updates coordinates for existing ones; rows being edited
  (`EDITING_INSTANCES`) are shielded; a new source axis becomes a column
  backfilled with the axis **default** on every row (never blank); rows
  absent from the source are **never removed** (studio-only instances
  live only here). The studio records its own write times so an
  external edit (`_check_csv_external_edit`, mtime) can be refused.
- The legacy name `avar2-mappings.csv` beside the source is still
  recognised and staged.

## `<stem>-control.json`

Secondary (control) axes: declarations, brace-layer locations, drawn
outlines, per-instance values. `control_axes/` (`schema.py`) is the owner.

```jsonc
{
  "version": 1,
  "axes": [
    {
      "tag": "LCWD",                   // authored case preserved (decision 0012)
      "display_name": "Lowercase width",
      "default": 0, "min": 0, "max": 100,
      "layers": [                      // flat list, one entry per brace layer
        { "glyph": "a", "location": { "LCWD": 100, "XTRA": 1715, "XOPQ": 1462, "YOPQ": 275 } },
        { "glyph": "a",
          "location": { "LCWD": 100, "XTRA": 47, "XOPQ": 1462, "YOPQ": 275 },
          "target": { "XOPQ": 1240 } },                       // a correction: computed "as if at"
        { "glyph": "e",
          "location": { "LCWD": 100, "XTRA": 1715, "XOPQ": 1462, "YOPQ": 275 },
          "outline": {                                         // a captured drawing (model α)
            "width": 1180,
            "paths": [ { "closed": true, "nodes": [[120, 0, "line"], [310, 0, "line"], [310, 660, "curve"]] } ],
            "components": [ { "name": "dotaccent", "transform": [1, 0, 0, 1, 0, 0] } ],
            "anchors": [ { "name": "top", "x": 420, "y": 1200 } ]
          },
          "source_sig": "3f9c2a7e0b1d4c55" }                 // the masters this was drawn over
      ]
    }
  ],
  "instance_values": { "LCWD": { "Ultra Wide Heavy 144": 100 } }
}
```

- `axes[]`: `tag` (4 printable ASCII chars; duplicates are rejected
  case-insensitively), `display_name`, `default`, `min`, `max`
  (`min < max`, `min ≤ default ≤ max`; narrowing a range past an existing
  layer is refused), `layers[]`.
- `layers[]`: `glyph`; `location` — **sparse**, keyed by axis tag, only the
  pinned axes (the rest take the default master's value at regen);
  optional `target` — parametric overrides naming the point the outline is
  computed *as if at* (a key on the control axis itself is ignored); such a
  layer is recomputed on every regeneration and never captured; optional
  `outline` — a value dump of the drawn geometry (`width`, `paths[]` with
  `closed` and `nodes` as `[x, y, type]`, `components[]` with `name` and a
  6-value `transform`, `anchors[]`), deliberately JSON rather than glif so
  a config bundle carries it verbatim; optional `source_sig` — a hash of
  the glyph's master geometry when the drawing was captured, so the UI can
  report "source changed since". Entries are deduplicated on
  `(glyph, location)`, **first wins**. Coverage ("applicable glyphs") is
  derived from the unique glyph names and never stored.
- `instance_values`: `{tag: {instance name: value}}`, the per-row values
  of secondary axes (decision 0008). Carried through normalisation so a
  layer edit never wipes it; renamed and removed with the instance.
- Legacy keys `coverage` (implicit min/max seeds) and `extra_locations`
  are migrated into `layers` on load and never re-emitted.
- The seed signature (`xyz.avar2studio.seed-sig`) and the Fontra source
  label (`xyz.fontra.source-name`) are written on the brace layer in the
  **shadow**, not in this file.
- The list endpoint serves this file **without** `outline` and
  `source_sig` (they are large and the endpoint is polled), replacing them
  with `has_outline` and `source_changed`.

## `<stem>-grade.json`

```jsonc
{
  "version": 1,
  "enabled": true,              // the Transforms "Grade" toggle
  "default_pct": 0.25,          // seeds newly graded instances; never changes the built font
  "intensity": 1.0,             // multiplies every instance's pct; 0 disables without forgetting
  "clamp_to_headroom": true,    // limit the stem move to the counter room available
  "instances": [ { "name": "Bold Condensed", "pct": 0.25 } ]   // sparse anchors; GRAD interpolates between them
}
```

Percentages are non-negative floats (`0.25` = 25 %). Duplicate names
keep the first entry. Per-instance grades **persist when the toggle is
off** — `enabled` only gates whether the GRAD axis is built. Owner:
`grade.py`; the mechanics are in [guides/grade.md](guides/grade.md).

## `<stem>-transforms.json`

```jsonc
{
  "version": 1,
  "transforms": [
    { "type": "spac_widthaware", "enabled": true, "params": { "min": -20, "max": 40, "bias": 1.0, "scale": 1.25 } },
    { "type": "round_corners",   "enabled": false, "params": { "default_pct": 0, "style_pcts": {}, "master_overrides": {} } }
  ]
}
```

An **ordered** list of `{type, enabled, params}`; the build applies the
enabled entries in this order (source-stage ones re-sorted by their
priority). `type` is a transform id from the registry; a saved entry
whose type is not registered (an uninstalled user script) is dropped when
listed. Params are coerced against each transform's `ParamSpec` schema and
cross-validated before saving; at most one enabled entry may inject a
given axis tag. Owner: `transforms/config.py`.

## `<stem>-kinetikern.json`

```jsonc
{
  "version": 1,
  "enabled": true,
  "instances": [
    { "name": "Narrow 400 MinOpsz", "looseness": 0.0,
      "deltas": { "H": [83, 83], "n": [65, 66] } },             // [left, right], whole units, source glyph names
    { "name": "Custom", "looseness": 0.3,
      "location": { "XTRA": 663.1, "XOPQ": 202.5, "YOPQ": 121.8 },   // a spot that is not a named style
      "deltas": { "n": [12, 9] } }
  ]
}
```

Deltas are integers relative to what the masters interpolate to at that
instance; zero pairs are dropped; `location` keys are uppercased. Without
`location` the bake resolves the instance's current parametric
coordinates, so the correction follows the style if it moves. Owner:
`kinetikern/sidecar.py`; written atomically.

## `.avar2-studio/axis-metadata.json`

Display names and ranges of the axes the studio knows about, keyed by
**uppercase** tag:

```jsonc
{
  "XTRA": { "display_name": "X-Transparency", "registered_tag": "xtra", "min": 94.0, "max": 3330.0, "is_parametric": true },
  "WGHT": { "display_name": "Weight", "registered_tag": "wght", "is_parametric": false, "min": 100, "default": 100, "max": 900 }
}
```

- Parametric entries are auto-populated from the source's axes
  (`_load_axis_metadata`) and their `is_parametric` flag re-synced on
  every read (coverage can change as masters come and go).
- User (traditional) axes are declared in the AVAR2 MAPPINGS modal; their
  `default` is what makes the ORIGIN row decidable (decision 0001) and
  what the gen-avar2 shim receives as `[min, default, max]`. Without a
  `default` the registered convention applies (`TRADITIONAL_AXIS_DEFAULTS`:
  wght 400, wdth 100, opsz 72, slnt/ital/grad/spac/cntr 0), clamped into
  range. `avar2-studio build` warns when the file is missing (named styles
  then land off their mapped values — measured at 62 units on Crispy).
- A placeholder extent (`-1000 / 1000`, or a degenerate `[0, 0]` as
  transform-injected axes report) is repaired on read from the enabled
  transform's params, the GRAD extent or the CSV column's spread
  (`_repair_placeholder_ranges`); hand-edited ranges are never clobbered.
- Travels in the workspace zip (`.avar2-studio/axis-metadata.json` or a
  `*-axis-metadata.json` beside the source) and, in the static demo, as an
  optional `axis_metadata` section of the config bundle.

## `.avar2-studio/instance-renames.json`

`{"<original instance name>": "<studio name>"}`. Renaming a
source-defined instance cannot touch the original, and the shadow is
re-derived from it on every regeneration, so the rename is recorded here
and re-applied by `regenerate_shadow` every time. Chains collapse
(`Def → Default → Final` becomes `Def → Final`); renaming back drops the
entry.

## `.avar2-studio/config.yaml`

The gftools-builder configuration the build consumes, keyed by the
built-font filename `<Family>[<tags>].ttf` (tag case preserved: registered
lowercase, foundry uppercase):

```yaml
sources: [/abs/path/to/Crispy.glyphs]        # the staged job copy after a build (see below)
familyName: Crispy
fvarInstances:
  Crispy[XOPQ,XTRA,YOPQ,wght].ttf:
    - {name: Regular, coordinates: {xopq: 202.5, xtra: 663.1, yopq: 121.8}}
stat:                                         # regenerated from the CSV on every build
  Crispy[XOPQ,XTRA,YOPQ,wght].ttf:
    - {name: Weight, tag: wght, values: [{name: Thin, value: 100}, …]}
avar2:                                        # one entry per mapping row: user-axis in → parametric out
  Crispy[XOPQ,XTRA,YOPQ,wght].ttf:
    - {in: {wght: 100, wdth: 20, opsz: 144}, out: {XTRA: 94, XOPQ: 6, YOPQ: 6}}
```

Created once from the source (`sources`, `familyName`, `fvarInstances` —
omitted when the source has no instances, because an empty list fails the
builder's schema), then `stat` and `avar2` are regenerated from the CSV by
`config_generator.update_config` inside each build job and the job's copy
is promoted over this one. Consequence: between builds `sources:` points
at the (deleted) job directory; the next build's `update_config` rewrites
it for its staged source, and the export's row lookup reads the `avar2`
section only.

## The `.avar2-studio/` directory

```
.avar2-studio/
  axis-metadata.json, instance-renames.json, config.yaml
  shadow/<name>.glyphs                 the canonical shadow (control + grade braces)
  shadow/transformed/<name>.glyphs     the build copy after source-stage transforms
  shadow/<stem>.designspace            designspace shadow …
  shadow/<stem>-studio-<slug>.ufo        … with one pooled sparse UFO per unique brace location
  build/<Family>[tags].ttf             promoted builds (newest wins; the directory accumulates — T2.14)
  build/<font>.woff2, <font>.woff2.json     the WOFF2 cache and its key {ttf_size, ttf_mtime, woff2_bytes, source_bytes}
  exports/<job-id>-<artifact>          finished exports, the last 5 (KEEP_EXPORTS)
  jobs/<job-id>/                       one per running job, removed when it finishes:
    src/                                 the snapshot (source, sidecars, .avar2-studio/{axis-metadata,instance-renames,config}.json/yaml, shadow/<name>, UFOs)
    spec.json, progress.jsonl, result.json, out/, build/
  backups/<file>.<YYYYMMDD-HHMMSS>     the last 20 copies of each rewritten sidecar
  archive/<stamp>-<source>             upload workspaces only: the previous source, last 5
```

Job ids are `<kind>-<epoch ms>-<seq>`; `progress.jsonl` holds
`{"phase": …}` / `{"log": …}` lines with `elapsed`; `result.json` carries
`success`, `built_font` / `artifact`, `duration`, the `last_*` build-state
snapshot and, on failure, `error` and `details`.

Outside the project, under `~/.avar2-studio/`: `workspace/<example-id>/`
(staged bundled examples), `workspace/uploads/<slug>/` (one per uploaded
font), `transforms/` (user transform scripts and a README template),
`glyphs-plugin.json` and `glyphs-plugin.log` (the Glyphs launcher).

## The configuration bundle

`GET /api/config/export` — a single JSON file, `<Family>-avar2studio.json`:

```jsonc
{
  "format": "avar2-studio-config", "format_version": 1,
  "exported_at": "2026-10-16T18:02:11+00:00", "studio_version": "0.1.0",
  "source": { "family_name": "Crispy",
              "axes": [ { "tag": "XOPQ", "min": 2, "default": 202.5, "max": 1016, "has_master_coverage": true } ],
              "avar2_out_columns": ["XTRA", "XOPQ", "YOPQ"] },
  "control_axes": { …the control sidecar… },     // drawings captured first; pins on transform-injected axes dropped
  "avar2_csv": "Instance Name,XTRA,…\n…",         // the CSV text, or "" for "leave the target's CSV alone"
  "transforms": { …the transforms sidecar… },
  "grade": { …the grade sidecar… }
}
```

Export runs `capture_outlines` first so the bundle is the whole session;
pins on axes the target source cannot have (SPAC, GRAD — they exist only
in compiled fonts) are dropped so the bundle stays portable. Import
(`POST /api/config/import {bundle, dry_run}`) is **all-or-nothing**:
`validate_bundle` checks the format version, that every control-axis tag
is valid and collides with nothing, that every layer's glyph and pinned
axes exist in the target, that the CSV's parametric columns are the
target's, and refuses control axes on a `.designspace` target; only a
clean report lets `apply_bundle` write — control axes, transforms and
grade **replaced wholesale**, the CSV only when the bundle carries one —
after which the shadow is regenerated and a build queued. The static
demo reads the same format and additionally honours optional
`axis_metadata` and `corner_pins` sections.

## The workspace zip

**Config ▾ → Download workspace (.zip)…** / **Load Font ▾ → Upload …**
(`server._load_project_zip`, `frontend/src/zip-workspace.js`):

```
<stem>.glyphs                          # or: <stem>.designspace + *.ufo/ directories
<stem>-avar.csv
<stem>-control.json                    # if any
<stem>-transforms.json                 # if any
<stem>-grade.json                      # if any
<stem>-kinetikern.json                 # desktop only; rides along as a plain file
<stem>-cornerpins.json                 # static only
.avar2-studio/axis-metadata.json       # if any (or <stem>-axis-metadata.json beside the source)
.avar2-studio/build/<stem>-VF.ttf      # the current build; required for a .designspace in the browser
```

Exactly one source per archive, anywhere in its folder structure;
`__MACOSX`, `._*`, absolute and `..` paths are rejected; the rest of
`.avar2-studio/` (build, shadow, jobs, archive) is tool-managed and never
imported. The desktop loader backs up and replaces `-avar.csv`,
`-control.json`, `-transforms.json` and the axis metadata and copies every
other project file (feature files, notes, `-grade.json`,
`-kinetikern.json`) verbatim next to the source; the static writer emits
the sidecars from live state (the CSV is the source of truth) plus the
current font bytes.
