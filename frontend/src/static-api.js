/**
 * Static-demo data provider — lets the built bundle run with NO backend
 * (GitHub Pages). docs/migration-github-pages.md has the plan.
 *
 * How it works: before the app renders, selectApiMode() probes
 * /api/health. A real server answers 200 → nothing changes. On a static
 * host the probe 404s → we swap api's methods for static ones that read
 * the build-time snapshot in public/static-demo/ (captured API
 * responses — shapes match by construction).
 *
 * What works statically:
 *   - Load Font: snapshotted examples, or upload a .glyphs (compiles
 *     in-browser via the fontc-wasm worker)
 *   - Transforms SPAC toggle: swaps the pre-baked spac-on/spac-off font
 *     variants (params edits and Rebuild need a real build → hidden)
 *   - Config export: a static file download
 *   - Config import onto an uploaded source: avar2 mappings, control
 *     axes, grade and SPAC transforms apply in-browser (wasm)
 *
 * Known limitations (tracked in the migration doc):
 *   - snapshot datasets: getMappedLocation returns the input
 *     coordinates (their avar2 isn't parsed); uploads with a mappings
 *     CSV get a real client-side evaluation (avar2-eval.js)
 *   - anything else that writes or builds throws "needs the full app"
 */

import { api } from './api';
import { compileFont, addAvar2 } from './fontc-compile';
import { parseFont } from './fvar';
import { mappedLocation } from './avar2-eval';
import * as mappingsCsv from './mappings-csv';

const DATA = 'static-demo'; // relative — resolves under any --base

let staticMode = false;
export const isStaticMode = () => staticMode;
// True while the app is showing an uploaded (fontc-wasm compiled)
// source rather than a baked snapshot — Rebuild exists for these.
export const isUploadDataset = () => !!uploadDataset;

// ---- dataset (example) state ------------------------------------------------

let dataset = null;               // example id, e.g. 'crispy-mini'
let datasetPath = DATA;           // sync mirror for URL-builder methods
let examplesPromise = null;

const fetchJSON = async (path) => {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`static demo data missing (${path})`);
  return r.json();
};

const examplesIndex = () => {
  if (!examplesPromise) examplesPromise = fetchJSON(`${DATA}/examples.json`);
  return examplesPromise;
};

const datasetDir = async () => {
  if (!dataset) {
    const idx = await examplesIndex();
    dataset = (idx.examples || [])[0]?.id || 'crispy-mini';
  }
  datasetPath = `${DATA}/${dataset}`;
  return dataset;
};

// ---- uploaded source state (fontc-wasm compiled in a Worker) -----------------
//
// An uploaded .glyphs compiles in-browser; everything the studio shows
// comes from the compiled font itself (fvar axes + named instances +
// name table — see fvar.js). Sidecar-backed features (avar2 mappings,
// transforms, grade, control axes) don't exist for an upload, so those
// surfaces stay empty/unavailable, exactly like a blind-launched source
// on the real server.

let uploadDataset = null; // {health, axes, instances, fontUrl, sourceText}

const buildUploadDataset = async (glyphsFile, csvFile, metadataFile = null) => {
  const sourceText = await glyphsFile.text();
  let fontBytes = await compileFont(sourceText);
  let mappingsText = null;
  let userAxisTags = new Set();
  const compiledTags = new Set(parseFont(fontBytes).axes.map(a => a.tag));
  const axisMetadata = metadataFile ? JSON.parse(await metadataFile.text()) : {};
  if (csvFile) {
    // avar2 generation in-browser: CSV mappings → avar v2 store in the
    // compiled font (fontc-web wasm). The user axes are the CSV columns
    // that aren't already fvar axes in the compiled font.
    mappingsText = await csvFile.text();
    fontBytes = await addAvar2(fontBytes, mappingsText);
    const header = mappingsText.split('\n', 1)[0].replace(/^﻿/, '');
    // Registered columns normalize to lowercase fvar tags (wght etc.);
    // user-tag matching must use the normalized form.
    userAxisTags = new Set(
      header.split(',').slice(1).map(s => s.trim()).filter(t => t && !compiledTags.has(t))
        .map(t => mappingsCsv.normalizeInAxisName(t))
    );
  }
  const meta = parseFont(fontBytes);
  const fontUrl = URL.createObjectURL(new Blob([fontBytes], { type: 'font/ttf' }));
  // The mappings CSV is the authoring source of truth (instances +
  // mappings), exactly as in the studio's workspace: uploaded CSV if
  // present, otherwise synthesized parametric-only from the font.
  const csvParsed = mappingsText
    ? mappingsCsv.parseMappingsCsv(mappingsText)
    : mappingsCsv.synthesizeFromFont(
        [...compiledTags].map(tag => ({ tag, has_master_coverage: true })),
        meta.instances
      );
  const dataset = {
    sourceText,
    fontBytes,
    mappingsCsv: mappingsText,
    instancesCsv: csvParsed,
    parametricTags: new Set(compiledTags),
    axisRanges: axisMetadata || {},
    fontUrl,
    axes: {
      axes: meta.axes.map(a => ({
        tag: a.tag, name: a.name,
        min: a.min, default: a.default, max: a.max,
        has_master_coverage: !userAxisTags.has(a.tag), is_control_axis: false,
      })),
    },
    instances: { instances: [] },
    health: {
      static: true, demo: false, building: false,
      font_built: true, font_loaded: true,
      glyphs_path: `upload:${glyphsFile.name}:${Date.now()}`,
      original_path: `upload:${glyphsFile.name}`,
      source_format: 'glyphs',
      family_name: meta.familyName,
      vf_family_id: `${meta.familyName}-VF`,
      built_font_filename: `${meta.familyName}.ttf`,
      last_build_status: 'ok', last_build_error: null,
      avar2_error: null, build_stale: false,
      upm: meta.upm,
    },
  };
  syncInstancesFromCsv(dataset, meta.instances.map(i => i.name));
  return dataset;
};

// Instance list derives from the CSV rows (every column is a coordinate);
// rows matching an fvar named instance are 'source', others 'studio'.
const syncInstancesFromCsv = (dataset, fvarInstanceNames = null) => {
  const fvarNames = new Set(
    fvarInstanceNames ?? parseFont(dataset.fontBytes).instances.map(i => i.name)
  );
  dataset.instances = {
    instances: dataset.instancesCsv.rows.map(row => ({
      name: row.name,
      coordinates: Object.fromEntries(
        Object.entries(row.values)
          .filter(([, v]) => v !== '')
          .map(([tag, v]) => [tag, parseFloat(v)])
      ),
      origin: fvarNames.has(row.name) ? 'source' : 'studio',
    })),
  };
};

// Every authoring mutation funnels here: if the CSV grew user columns
// the avar2 store regenerates; parametric-only edits only move browser
// state (identity mapping — a no-op table fontc would omit anyway).
// axisRanges (axis-metadata semantics) overrides the CSV-derived range
// for newly declared user axes.
const regenerateFont = async (dataset) => {
  if (mappingsCsv.userColumns(dataset.instancesCsv, [...dataset.parametricTags]).length === 0) {
    return;
  }
  const ranges = Object.keys(dataset.axisRanges || {}).length
    ? JSON.stringify(dataset.axisRanges)
    : null;
  dataset.fontBytes = await addAvar2(
    dataset.fontBytes,
    mappingsCsv.serializeMappingsCsv(dataset.instancesCsv),
    ranges
  );
  URL.revokeObjectURL(dataset.fontUrl);
  dataset.fontUrl = URL.createObjectURL(new Blob([dataset.fontBytes], { type: 'font/ttf' }));
  dataset.health.last_build_time = Date.now();
  // The fvar grew (new user axes): re-read so USER AXES sliders appear.
  // Parametric tags keep master coverage; user columns don't; anything
  // else the fvar carries (SPAC/GRAD/control axes from a bundle) is
  // treated as non-master here too — bundle flags get refreshed on the
  // next import, and the authoring flow never mixes with snapshots.
  const meta = parseFont(dataset.fontBytes);
  const userTags = new Set(
    mappingsCsv.userColumns(dataset.instancesCsv, [...dataset.parametricTags])
      .map(t => mappingsCsv.normalizeInAxisName(t))
  );
  dataset.axes = {
    axes: meta.axes.map(a => ({
      tag: a.tag, name: a.name,
      min: a.min, default: a.default, max: a.max,
      has_master_coverage: dataset.parametricTags.has(a.tag) && !userTags.has(a.tag),
      is_control_axis: false,
    })),
  };
};

// ---- transforms state (toggles → pre-baked variants) ------------------------
//
// The snapshot bakes TWO builds of the default example: the transform set
// as-captured, and all-transforms-off. Toggles are allowed only between
// those two states — anything else needs a real build (full app).

let transformsState = null;   // id-shaped list, mirroring GET /api/transforms
let bakedEnabledIds = null;   // enabled ids in the snapshot ('default' files)

const enabledIds = (list) => list.filter(t => t.enabled).map(t => t.id).sort();
const sameSet = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

const transformsList = async () => {
  if (!transformsState) {
    const dir = await datasetDir();
    transformsState = (await fetchJSON(`${DATA}/${dir}/transforms.json`)).transforms || [];
    bakedEnabledIds = enabledIds(transformsState);
  }
  return transformsState;
};

// 'default' files vs the all-off 'variants/spac-off' bake.
const activeVariant = async () => {
  const cur = enabledIds(await transformsList());
  return cur.length === 0 && !sameSet(cur, bakedEnabledIds) ? 'spac-off' : 'default';
};

// Files that differ between the baked variants.
const variantFile = async (name) => {
  const dir = await datasetDir();
  const variant = await activeVariant();
  return variant === 'spac-off'
    ? `${DATA}/${dir}/variants/spac-off/${name}`
    : `${DATA}/${dir}/${name}`;
};

// getFontUrl() must stay SYNCHRONOUS (App calls it directly). Both
// inputs are already set synchronously by the time it can be called:
// `dataset` by loadExample, transformsState by updateTransforms (or by
// the first health() via activeVariant).
const syncVariant = () => {
  if (!transformsState) return 'default';
  const cur = enabledIds(transformsState);
  return cur.length === 0 && !sameSet(cur, bakedEnabledIds) ? 'spac-off' : 'default';
};

const currentFontPath = () => {
  const dir = dataset || 'crispy-mini';
  return syncVariant() === 'spac-off'
    ? `${DATA}/${dir}/variants/spac-off/demo.ttf`
    : `${DATA}/${dir}/demo.ttf`;
};

const endpoint = (name) => async () => {
  const dir = await datasetDir();
  return fetchJSON(`${DATA}/${dir}/${name}`);
};

let healthCache = {};
const staticHealth = async () => {
  const path = await variantFile('health.json');
  if (!healthCache[path]) healthCache[path] = fetchJSON(path);
  return healthCache[path];
};

const unavailable = (what) => async () => {
  throw new Error(`${what} needs the full app — this static demo is read-only.`);
};

const requireUpload = () => {
  if (!uploadDataset) {
    throw new Error('Editing instances needs an uploaded source — snapshots are read-only demos.');
  }
};

// ---- config bundle import (uploaded sources only) ---------------------------
//
// Validation gates on the target font supplying the bundle's core axis
// data, mirroring config_port.validate_bundle: the parametric axes the
// avar2 CSV maps onto, and the source axes brace-layer locations
// reference. Apply is per-section: avar2 mappings, control axes, grade
// and the SPAC transforms are real (wasm add_avar2 /
// apply_control_axes / apply_grade / apply_transforms). Enabled
// transforms of a type the wasm port doesn't know are skipped with a
// warning, never silently dropped.

// Menu metadata for the built-in SPAC transforms, mirroring the studio's
// transform registry (transforms/builtin_spac*.py — the bundle carries
// only {type, enabled, params}; the Transforms menu also renders
// name/description/params_schema).
const KNOWN_TRANSFORMS = {
  spac: {
    id: 'spac',
    name: 'Spacing — uniform (gftools)',
    description: 'Inject a SPAC axis via gftools-gen-spac; every glyph tracks by the same amount, outlines unchanged.',
    injected_axis_tag: 'SPAC',
    params_schema: [
      { key: 'min', label: 'Min', type: 'int', default: -20 },
      { key: 'max', label: 'Max', type: 'int', default: 40 },
    ],
  },
  spac_widthaware: {
    id: 'spac_widthaware',
    name: 'Spacing — width-aware',
    description: 'Inject a SPAC axis that loosens every glyph by a consistent proportion of its width (wider glyphs get more), including composites.',
    injected_axis_tag: 'SPAC',
    params_schema: [
      { key: 'min', label: 'Min', type: 'int', default: -20 },
      { key: 'max', label: 'Max', type: 'int', default: 40 },
      { key: 'bias', label: 'Wide bias', type: 'float', default: 1.0, min: 1.0, max: 2.5 },
      { key: 'scale', label: 'Scale', type: 'float', default: 1.25, min: 0.1, max: 10.0 },
    ],
  },
};

const csvRowCount = (text) =>
  text.split('\n').filter(l => l.trim() && !l.startsWith('﻿')).length - 1;

const csvHeaderTags = (csv) =>
  csv.split('\n', 1)[0].replace(/^﻿/, '').split(',').slice(1).map(s => s.trim()).filter(Boolean);

const PARAM_TAGS = ['XTRA', 'XOPQ', 'YOPQ'];

// Instance name → base parametric coords {XTRA, XOPQ, YOPQ} for grade
// application. Sources: the bundle's avar2 CSV rows (the studio
// instances' mapped parametric locations), then the compiled font's
// fvar named instances (font truth — overrides where they overlap).
const resolveGradeCoords = (dataset, avar2Csv) => {
  const coords = {};
  if (avar2Csv && avar2Csv.trim()) {
    const lines = avar2Csv.trim().split('\n').filter(l => l.trim());
    const header = lines[0].replace(/^﻿/, '').split(',').map(s => s.trim());
    for (const line of lines.slice(1)) {
      const cells = line.split(',');
      const row = {};
      for (const t of PARAM_TAGS) {
        const i = header.indexOf(t);
        if (i > 0 && cells[i] && cells[i].trim()) row[t] = parseFloat(cells[i]);
      }
      if (Object.keys(row).length && cells[0]) coords[cells[0].trim()] = row;
    }
  }
  for (const inst of parseFont(dataset.fontBytes).instances) {
    const row = {};
    for (const t of PARAM_TAGS) {
      if (t in inst.coordinates) row[t] = inst.coordinates[t];
    }
    if (Object.keys(row).length) coords[inst.name] = row;
  }
  return coords;
};

const validateBundle = (bundle, dataset) => {
  const errors = [];
  const warnings = [];
  if (!bundle || bundle.format !== 'avar2-studio-config') {
    errors.push('Not an avar2-studio config bundle (missing format marker)');
  } else if (bundle.format_version !== 1) {
    errors.push(`Unsupported bundle version ${bundle.format_version} (expected 1)`);
  }
  const controlAxes = bundle?.control_axes?.axes || [];
  const transforms = bundle?.transforms?.transforms || [];
  const grade = bundle?.grade || {};
  const avar2Csv = bundle?.avar2_csv || '';

  const targetTags = new Set(
    dataset.axes.axes.filter(a => a.has_master_coverage).map(a => a.tag)
  );
  if (!errors.length) {
    for (const col of bundle.source?.avar2_out_columns || []) {
      if (!targetTags.has(col)) {
        errors.push(`Core axis '${col}' (avar2 out) is missing in the loaded font`);
      }
    }
    for (const axis of controlAxes) {
      for (const layer of axis.layers || []) {
        for (const tag of Object.keys(layer.location || {})) {
          if (!targetTags.has(tag)) {
            errors.push(`Brace layer on '${layer.glyph}' references missing axis '${tag}'`);
          }
        }
      }
    }
    // The registry's one-injector-per-axis rule: two enabled SPAC
    // transforms would produce a font with two SPAC axes.
    const spacInjectors = transforms
      .filter(t => t.enabled && KNOWN_TRANSFORMS[t.type]?.injected_axis_tag === 'SPAC')
      .map(t => t.type);
    if (spacInjectors.length > 1) {
      errors.push(`Only one transform can add the SPAC axis at a time ('${spacInjectors.join("' and '")}' both do)`);
    }
  }
  if (controlAxes.length) warnings.push('Control axes: applied as computed brace tuples (drawn outlines are a full-app feature)');
  for (const t of transforms) {
    if (t.enabled && !KNOWN_TRANSFORMS[t.type]) {
      warnings.push(`Transform '${t.type}': unknown type — skipped by the static demo`);
    }
  }

  return {
    ok: errors.length === 0,
    errors,
    warnings,
    summary: {
      axes: controlAxes.length,
      layers: controlAxes.reduce((n, a) => n + (a.layers || []).length, 0),
      mapping_rows: avar2Csv ? csvRowCount(avar2Csv) : 0,
      transforms: transforms.filter(t => t.enabled).length,
      grades: (grade.instances || []).length,
    },
  };
};

// ---- config bundle export from browser state (S3) ---------------------------
//
// Assembles the same bundle shape the server's config_port.build_export
// emits, from the in-memory dataset: the edited CSV is the mappings, the
// rest are recorded section states. Downloads via a blob URL the Header
// anchors to (same mechanism as the server's Content-Disposition trick).

const buildConfigBundle = (dataset) => ({
  format: 'avar2-studio-config',
  format_version: 1,
  exported_at: new Date().toISOString(),
  studio_version: 'static-demo',
  source: {
    family_name: dataset.health.family_name,
    axes: (dataset.axes?.axes || []).map(a => ({
      tag: a.tag, min: a.min, default: a.default, max: a.max,
      has_master_coverage: a.has_master_coverage,
    })),
    avar2_out_columns: [...dataset.parametricTags],
  },
  control_axes: { version: 1, axes: dataset.controlAxes || [] },
  avar2_csv: mappingsCsv.serializeMappingsCsv(dataset.instancesCsv),
  transforms: { version: 1, transforms: dataset.transforms || [] },
  grade: dataset.grade || { version: 1, enabled: false, default_pct: 0.25, instances: [] },
});

const applyBundle = async (bundle, dataset) => {
  const { addAvar2, applyControlAxes, applyGrade, applyTransforms } = await import('./fontc-compile');
  const report = { ok: true, applied: [], warnings: validateBundle(bundle, dataset).warnings };
  const avar2Csv = bundle.avar2_csv || '';
  const controlAxes = bundle.control_axes?.axes || [];
  const transforms = bundle.transforms?.transforms || [];
  const grade = bundle.grade || {};
  const gradeInstances = grade.enabled ? (grade.instances || []) : [];

  // The compiled font's own (parametric) axes — captured BEFORE the
  // mappings apply grows the fvar; used to tell user axes apart.
  let compiledTags = null;
  if (avar2Csv.trim()) {
    compiledTags = new Set(
      dataset.axes.axes.filter(a => a.has_master_coverage).map(a => a.tag)
    );
    dataset.fontBytes = await addAvar2(dataset.fontBytes, avar2Csv);
    dataset.mappingsCsv = avar2Csv;
    // The bundle's CSV becomes the authoring source of truth.
    dataset.instancesCsv = mappingsCsv.parseMappingsCsv(avar2Csv);
    syncInstancesFromCsv(dataset);
    report.applied.push('avar2 mappings');
  }
  if (controlAxes.length) {
    dataset.fontBytes = await applyControlAxes(dataset.fontBytes, JSON.stringify(controlAxes));
    dataset.controlAxes = controlAxes;
    report.applied.push('control axes');
  }
  if (gradeInstances.length) {
    const coords = resolveGradeCoords(dataset, avar2Csv);
    dataset.fontBytes = await applyGrade(
      dataset.fontBytes, JSON.stringify(grade), JSON.stringify(coords)
    );
    report.applied.push('grade');
  }
  // SPAC transforms apply last (they rebuild HVAR from the gvar the
  // earlier sections produced). Disabled and unknown-type entries never
  // reach the font — the wasm side applies only enabled known ones.
  if (transforms.some(t => t.enabled && KNOWN_TRANSFORMS[t.type])) {
    dataset.fontBytes = await applyTransforms(dataset.fontBytes, JSON.stringify(transforms), avar2Csv);
    report.applied.push('transforms (SPAC)');
  }
  // The Transforms menu reflects the bundle's set from now on: enabled
  // entries show enabled, the rest available but off.
  dataset.transforms = transforms.map(t => ({
    ...(KNOWN_TRANSFORMS[t.type] || { id: t.type, name: t.type }),
    enabled: !!t.enabled,
    params: t.params || {},
  }));
  if (!report.applied.length) return report;

  URL.revokeObjectURL(dataset.fontUrl);
  dataset.fontUrl = URL.createObjectURL(new Blob([dataset.fontBytes], { type: 'font/ttf' }));
  // The App only re-reads fontUrl when last_build_time changes — the
  // import IS a rebuild of the in-memory font, so stamp it (the real
  // server bumps last_build_time on every build too).
  dataset.health = { ...dataset.health, last_build_time: new Date().toISOString() };
  // Re-read axes from the patched font: user axes (CSV in-columns),
  // control axes and GRAD all appear in the fvar now. None of them has
  // master coverage — the compiled masters only span the parametrics.
  // SPAC is transform-injected (a live-preview parametric slider, like
  // the server's built-font overlay marks it).
  const userTags = avar2Csv.trim() && compiledTags
    ? new Set(csvHeaderTags(avar2Csv).filter(t => !compiledTags.has(t) && t !== 'SPAC'))
    : new Set();
  const controlTags = new Set(controlAxes.map(a => a.tag));
  const injectedTags = new Set(
    transforms
      .filter(t => t.enabled)
      .map(t => KNOWN_TRANSFORMS[t.type]?.injected_axis_tag)
      .filter(Boolean)
  );
  const meta = parseFont(dataset.fontBytes);
  dataset.axes = {
    axes: meta.axes.map(a => ({
      tag: a.tag, name: a.name,
      min: a.min, default: a.default, max: a.max,
      has_master_coverage: !userTags.has(a.tag) && !controlTags.has(a.tag) && a.tag !== 'GRAD',
      is_control_axis: controlTags.has(a.tag),
      is_grade_axis: a.tag === 'GRAD',
      transform_injected: injectedTags.has(a.tag),
    })),
  };
  return report;
};

const staticOverrides = {
  health: async () => (uploadDataset ? uploadDataset.health : staticHealth()),
  glyphsFileStatus: async () => ({ has_unsaved_changes: false }),
  getInstances: async () => (uploadDataset ? uploadDataset.instances : fetchJSON(await variantFile('instances.json'))),
  getMasters: async () => (uploadDataset ? { masters: [] } : endpoint('masters.json')()),
  getAxes: async () => (uploadDataset ? uploadDataset.axes : fetchJSON(await variantFile('axes.json'))),
  getAvar2Instances: async () => {
    if (!uploadDataset) return endpoint('avar2-instances.json')();
    return {
      instances: uploadDataset.instancesCsv.rows.map(row => ({
        instance_name: row.name,
        avar2_mapping: {
          in: Object.fromEntries(
            Object.entries(row.values)
              .filter(([tag, v]) => v !== '' && !uploadDataset.parametricTags.has(tag))
              .map(([tag, v]) => [mappingsCsv.normalizeInAxisName(tag), parseFloat(v)])
          ),
          out: Object.fromEntries(
            Object.entries(row.values)
              .filter(([tag, v]) => v !== '' && uploadDataset.parametricTags.has(tag))
              .map(([tag, v]) => [tag, parseFloat(v)])
          ),
        },
      })),
    };
  },
  getAvar2Axes: async () => {
    if (!uploadDataset) return endpoint('avar2-axes.json')();
    const parsed = uploadDataset.instancesCsv;
    const userCols = mappingsCsv.userColumns(parsed, [...uploadDataset.parametricTags]);
    const metadata = {};
    for (const col of userCols) {
      const override = uploadDataset.axisRanges[col] || {};
      const derived = mappingsCsv.columnRange(parsed, col) || { min: 0, default: 0, max: 0 };
      metadata[col] = {
        registered_tag: override.registered_tag || mappingsCsv.normalizeInAxisName(col),
        display_name: override.display_name || col,
        is_parametric: false,
        min: override.min ?? derived.min,
        default: override.default ?? derived.default,
        max: override.max ?? derived.max,
      };
    }
    // Parametric axes also carry metadata (display + ranges from the font).
    for (const a of uploadDataset.axes.axes.filter(x => x.has_master_coverage)) {
      metadata[a.tag] = {
        registered_tag: mappingsCsv.normalizeInAxisName(a.tag),
        display_name: a.name || a.tag,
        is_parametric: true,
        min: a.min, default: a.default, max: a.max,
      };
    }
    return {
      traditional_axes: { columns: userCols },
      metadata,
      parametric_axes: [...uploadDataset.parametricTags],
    };
  },
  getTransforms: async () => (uploadDataset ? { transforms: uploadDataset.transforms || [] } : { transforms: await transformsList() }),
  getGrade: async () => (uploadDataset
    ? { enabled: false, default_pct: 0.25, instances: [], max_pct: {} }
    : endpoint('grade.json')()),
  listControlAxes: async () => (uploadDataset
    ? { axes: uploadDataset.controlAxes || [] }
    : endpoint('control-axes.json')()),
  getGlyphCoverage: async () => {
    if (!uploadDataset) return endpoint('glyph-coverage.json')();
    // Synthesize coverage rows for bundle-applied control axes so the
    // sidebar's SECONDARY PARAMETRIC AXES section shows them. Marked
    // source: 'source' — that renders the layers read-only (studio rows
    // would offer edit affordances that only exist on the real server).
    const axes = (uploadDataset.controlAxes || []).map(a => {
      const covers = [...new Set((a.layers || []).map(l => l.glyph))];
      return {
        tag: a.tag,
        name: a.name || a.tag,
        min: a.min,
        default: a.default,
        max: a.max,
        kind: 'scoped',
        source: 'source',
        covers,
        covers_count: covers.length,
        layers: (a.layers || []).map(l => ({
          glyph: l.glyph,
          location: l.location || {},
          location_user: l.location || {},
        })),
      };
    });
    return { axes, glyph_chars: {} };
  },
  listExamples: examplesIndex,
  checkSyncStatus: async () => ({ synced: true, message: 'Static demo snapshot' }),
  getFontUrl: () => (uploadDataset ? uploadDataset.fontUrl : currentFontPath()),
  getAvar2FontUrl: () => (uploadDataset ? uploadDataset.fontUrl : currentFontPath()),
  exportConfigUrl: () => {
    if (!uploadDataset) return `${datasetPath}/config-export.json`;
    const bundle = buildConfigBundle(uploadDataset);
    return URL.createObjectURL(
      new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })
    );
  },

  // Uploads: compile the source in a Web Worker (fontc-wasm) and switch
  // the app to the resulting in-memory dataset. This is the Phase 2
  // path — no server anywhere.
  uploadSource: async (files) => {
    const list = Array.from(files || []);
    const glyphsFile = list.find(f => f.name.toLowerCase().endsWith('.glyphs'));
    if (!glyphsFile) {
      throw new Error('No .glyphs file in the upload (sources must be .glyphs for now)');
    }
    const csvFile = list.find(f => f.name.toLowerCase().endsWith('-avar.csv')) || null;
    const metadataFile = list.find(f => f.name.toLowerCase().endsWith('axis-metadata.json')) || null;
    const ignored = list.filter(f => f !== glyphsFile && f !== csvFile && f !== metadataFile).map(f => f.name);
    uploadDataset = await buildUploadDataset(glyphsFile, csvFile, metadataFile);
    transformsState = [];
    bakedEnabledIds = [];
    return { ok: true, ignored_files: ignored };
  },

  // Rebuild only exists for uploaded sources: recompile the same source
  // text in the Worker and swap the font bytes. Snapshot datasets have
  // nothing to rebuild (pre-baked).
  buildFont: async () => {
    if (!uploadDataset) {
      throw new Error('Building needs the full app — this static demo is read-only.');
    }
    const ttf = await compileFont(uploadDataset.sourceText);
    URL.revokeObjectURL(uploadDataset.fontUrl);
    uploadDataset = {
      ...uploadDataset,
      fontUrl: URL.createObjectURL(new Blob([ttf], { type: 'font/ttf' })),
    };
    return { ok: true };
  },

  // Load Font: swap the dataset; App's loadData() re-reads the new
  // health (different glyphs_path) and treats it as a source swap.
  loadExample: async (id) => {
    const idx = await examplesIndex();
    if (!(idx.examples || []).some(e => e.id === id)) {
      throw new Error(`Unknown example: ${id}`);
    }
    dataset = id;
    uploadDataset = null;
    transformsState = null;
    bakedEnabledIds = null;
    return { ok: true };
  },

  // Transforms toggles: allowed only between the two baked states (the
  // snapshot's enabled set ↔ all-off). Anything else isn't baked and
  // throws — App reverts the toggle and shows the message. Enabled/params
  // merge OVER the stored list so name/description/schema metadata
  // survives (the App renders the menu from our return value).
  updateTransforms: async (entries) => {
    if (uploadDataset) {
      // An imported bundle's transforms were applied onto the compiled
      // bytes one-shot; re-toggling would mean re-running the whole
      // import pipeline.
      throw new Error("Transform toggles on an uploaded source need the full app — the static demo applies an imported bundle's transforms as-is.");
    }
    const base = await transformsList();
    const next = base.map(t => {
      const e = (entries || []).find(x => (x.type || x.id) === t.id);
      return e ? { ...t, enabled: !!e.enabled, params: { ...(e.params || {}) } } : t;
    });
    const cur = enabledIds(next);
    if (!sameSet(cur, bakedEnabledIds) && cur.length !== 0) {
      throw new Error("That transform combination isn't baked into the static demo — the full app rebuilds on demand.");
    }
    transformsState = next;
    return { transforms: transformsState };
  },

  // The parametric-slider reflection falls back to input coordinates for
  // snapshot datasets (their avar2 isn't parsed); uploads with a
  // mappings CSV get a real client-side avar2 evaluation (avar2-eval.js).
  getMappedLocation: async (coordinates) => {
    if (uploadDataset) {
      if (uploadDataset.mappingsCsv) {
        return {
          mapped: mappedLocation(
            uploadDataset.fontBytes,
            uploadDataset.axes.axes,
            coordinates || {}
          ),
        };
      }
      return { mapped: coordinates || {} };
    }
    return { mapped: coordinates || {} };
  },

  // Editing-registration is best-effort on the real server; no-op here.
  registerEditingInstance: async () => ({}),
  unregisterEditingInstance: async () => ({}),

  // Everything that writes is unavailable on SNAPSHOT datasets (they are
  // read-only demos). On uploaded sources the instance lifecycle is real:
  // the CSV is the source of truth, mutations regenerate the avar2 store.
  buildAvar2Font: unavailable('Building'),
  createInstance: async (instanceName, coordinates, insertAfter = null) => {
    requireUpload();
    mappingsCsv.upsertRow(uploadDataset.instancesCsv, instanceName, coordinates, insertAfter);
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  updateInstance: async (instanceName, coordinates) => {
    requireUpload();
    mappingsCsv.upsertRow(uploadDataset.instancesCsv, instanceName, coordinates);
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  renameInstance: async (instanceName, newName) => {
    requireUpload();
    mappingsCsv.renameRow(uploadDataset.instancesCsv, instanceName, newName);
    syncInstancesFromCsv(uploadDataset);
    return {};
  },
  deleteInstance: async (instanceName) => {
    requireUpload();
    mappingsCsv.deleteRow(uploadDataset.instancesCsv, instanceName);
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  addInstanceToSource: unavailable('Saving to source'),
  addAvar2Axis: async (axisData) => {
    requireUpload();
    // AddAxisModal payload: {axis_name (uppercase CSV column),
    // display_name, registered_tag, default_value, min, max}.
    const column = axisData.axis_name;
    mappingsCsv.addColumn(uploadDataset.instancesCsv, column, axisData.default_value);
    uploadDataset.axisRanges[column] = {
      display_name: axisData.display_name,
      registered_tag: axisData.registered_tag,
      min: axisData.min,
      default: axisData.default_value,
      max: axisData.max,
      is_parametric: false,
    };
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  updateAvar2Axis: async (axisName, axisData) => {
    requireUpload();
    // Range edit (EditAxisModal): merge into the axis-metadata entry.
    uploadDataset.axisRanges[axisName] = {
      ...(uploadDataset.axisRanges[axisName] || {}),
      ...(axisData.min !== undefined ? { min: axisData.min } : {}),
      ...(axisData.default_value !== undefined ? { default: axisData.default_value } : {}),
      ...(axisData.max !== undefined ? { max: axisData.max } : {}),
    };
    await regenerateFont(uploadDataset);
    return {};
  },
  updateAvar2Mapping: async (instanceName, axisName, value) => {
    requireUpload();
    mappingsCsv.upsertRow(uploadDataset.instancesCsv, instanceName, { [axisName]: value });
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  setGrade: unavailable('Editing grade'),
  setInstanceGrade: unavailable('Editing grade'),
  removeInstanceGrade: unavailable('Editing grade'),
  createControlAxis: unavailable('Creating control axes'),
  updateControlAxis: unavailable('Editing control axes'),
  deleteControlAxis: unavailable('Deleting control axes'),
  controlAxisLayerDelta: unavailable('Editing layers'),
  setControlAxisLayers: unavailable('Editing layers'),
  openControlAxisInEditor: unavailable('The glyph editor'),
  exportFont: async (options) => {
    const { hidden_axes = [], default_location } = options || {};
    if (!uploadDataset) {
      if (hidden_axes.length || default_location) {
        throw new Error('Export options need an uploaded source');
      }
      const r = await fetch(await variantFile('demo.ttf'));
      if (!r.ok) throw new Error('Font download failed');
      return r.blob();
    }
    const { exportFontSetDefault, exportFontHiddenAxes, regenStat } = await import('./fontc-compile');
    let bytes = uploadDataset.fontBytes;
    if (default_location) {
      // Resting state IS the current location: defaults move to the user
      // values, parametric defaults to the mapped location (avar2-eval).
      const mapped = mappedLocation(bytes, uploadDataset.axes.axes, default_location);
      const defaults = { ...default_location };
      for (const tag of uploadDataset.parametricTags) {
        if (mapped[tag] !== undefined) defaults[tag] = mapped[tag];
      }
      const metadata = Object.fromEntries(
        Object.entries(uploadDataset.axisRanges || {}).map(([col, entry]) => {
          const tag = mappingsCsv.normalizeInAxisName(col);
          return [col, { ...entry, default: defaults[tag] ?? entry.default }];
        })
      );
      bytes = await exportFontSetDefault(
        bytes,
        defaults,
        mappingsCsv.serializeMappingsCsv(uploadDataset.instancesCsv),
        Object.keys(metadata).length ? JSON.stringify(metadata) : null,
        [...uploadDataset.parametricTags]
      );
    }
    if (hidden_axes.length) {
      bytes = await exportFontHiddenAxes(bytes, hidden_axes);
    }
    // Every export is Google-Fonts-ready: STAT regenerated from the fvar.
    bytes = await regenStat(bytes);
    return new Blob([bytes], { type: 'font/ttf' });
  },
  importConfig: async (bundle, dryRun) => {
    if (!uploadDataset) {
      throw new Error('Import needs an uploaded source — snapshots come pre-configured.');
    }
    const report = validateBundle(bundle, uploadDataset);
    if (dryRun) return report;
    if (!report.ok) {
      const err = new Error(report.errors[0] || 'Invalid bundle');
      err.report = report;
      throw err;
    }
    return applyBundle(bundle, uploadDataset);
  },
};

/**
 * Probe for a live backend; if there is none, swap in the static
 * provider. Must resolve before the app renders (see index.jsx).
 * Returns true when static mode was selected.
 */
export async function selectApiMode() {
  try {
    const r = await fetch('/api/health', { signal: AbortSignal.timeout(1500) });
    if (r.ok) return false;
  } catch {
    // fall through — no backend
  }
  staticMode = true;
  Object.assign(api, staticOverrides);
  return true;
}
