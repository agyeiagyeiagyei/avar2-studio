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
import { compileFont, addAvar2, measureAt, pinCorner as pinCornerWasm, regenStat, clampOutOfRange as clampOutOfRangeWasm, applyTransforms, applyControlAxes, applyGrade } from './fontc-compile';
import { parseFont } from './fvar';
import { mappedLocation } from './avar2-eval';
import * as mappingsCsv from './mappings-csv';
import { readWorkspaceZip, buildWorkspaceZip } from './zip-workspace';
import { saveSession, loadSession, clearSession, SESSION_VERSION } from './session';
import { auditCoverage, probeSweeps, PROBE_GLYPHS } from './coverage.js';

const DATA = 'static-demo'; // relative — resolves under any --base

let staticMode = false;
export const isStaticMode = () => staticMode;
// True while the app is showing an uploaded (fontc-wasm compiled)
// source rather than a baked snapshot — Rebuild exists for these.
export const isUploadDataset = () => !!uploadDataset;

// Sample text persistence — stored on the dataset so it survives reload.
export const getSampleText = () => uploadDataset?.sampleText || null;
export const setSampleText = (text) => {
  if (uploadDataset) {
    uploadDataset.sampleText = text;
    persistSoon();
  }
};

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

// sourceFormat is 'glyphs' (sourceText set — compiles/rebuilds in-browser)
// or 'designspace' (fontBytes = baked preview TTF from the project zip —
// fontc can't compile UFO sources off the filesystem). csvText/metadataText
// are the harvested sidecars; workspaceEntries/sourceDir are the raw zip
// contents for verbatim re-export (null for picker uploads).
const buildUploadDataset = async ({
  sourceName, sourceFormat, sourceText = null, fontBytes = null,
  csvText = null, metadataText = null, workspaceEntries = null, sourceDir = '',
}) => {
  let bytes = fontBytes ?? await compileFont(sourceText);
  let mappingsText = csvText;
  let userAxisTags = new Set();
  const compiledTags = new Set(parseFont(bytes).axes.map(a => a.tag));
  const axisMetadata = metadataText ? JSON.parse(metadataText) : {};
  if (mappingsText) {
    // avar2 generation in-browser: CSV mappings → avar v2 store in the
    // compiled font (fontc-web wasm). The user axes are the CSV columns
    // that aren't already fvar axes in the compiled font.
    const ranges = Object.keys(axisMetadata || {}).length
      ? JSON.stringify(axisMetadata)
      : null;
    bytes = await addAvar2(bytes, mappingsText, ranges, [...compiledTags]);
    const header = mappingsText.split('\n', 1)[0].replace(/^﻿/, '');
    // Registered columns normalize to lowercase fvar tags (wght etc.);
    // user-tag matching must use the normalized form.
    userAxisTags = new Set(
      header.split(',').slice(1).map(s => s.trim()).filter(t => t && !compiledTags.has(t))
        .map(t => mappingsCsv.normalizeInAxisName(t))
    );
  }
  const meta = parseFont(bytes);
  const fontUrl = URL.createObjectURL(new Blob([bytes], { type: 'font/ttf' }));
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
    fontBytes: bytes,
    mappingsCsv: mappingsText,
    instancesCsv: csvParsed,
    parametricTags: new Set(compiledTags),
    axisRanges: axisMetadata || {},
    fontUrl,
    sourceName,
    stem: sourceName.replace(/\.[^.]+$/, ''),
    sourceDir,
    workspaceEntries,
    axes: {
      axes: meta.axes.map(a => ({
        tag: a.tag, name: a.name,
        min: a.min, default: a.default, max: a.max,
        has_master_coverage: !userAxisTags.has(a.tag), is_control_axis: false,
      })),
    },
    instances: { instances: [] },
    // Coverage audit (structural gvar regions + behavioral sweeps via
    // the measure_at probe): missing corners, out-of-range sources,
    // collapses and inert regions, found at load time.
    coverage: [
      ...auditCoverage(bytes).findings,
      ...await probeSweeps(bytes, meta.axes, (b, g, l) => measureAt(b, g, l)),
    ],
    cornerPins: [],
    health: {
      static: true, demo: false, building: false,
      font_built: true, font_loaded: true,
      // Stamp the build time: the App only re-reads fontUrl when it
      // changes — without this the specimen keeps the PREVIOUS
      // dataset's font after an upload.
      last_build_time: Date.now(),
      glyphs_path: `upload:${sourceName}:${Date.now()}`,
      original_path: `upload:${sourceName}`,
      source_format: sourceFormat,
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

// ---- session persistence (IndexedDB; see session.js) -------------------------
//
// The stored record mirrors the dataset's serializable state; fontBytes is
// the key asset — restoring it skips the recompile. Uploads persist
// immediately (awaited); authoring mutations debounce.

const serializeDataset = (dataset) => ({
  version: SESSION_VERSION,
  savedAt: new Date().toISOString(),
  sourceName: dataset.sourceName,
  sourceFormat: dataset.health.source_format,
  stem: dataset.stem,
  sourceDir: dataset.sourceDir,
  sourceText: dataset.sourceText,
  fontBytes: dataset.fontBytes,
  workspaceEntries: dataset.workspaceEntries || null,
  csvText: mappingsCsv.serializeMappingsCsv(dataset.instancesCsv),
  parametricTags: [...dataset.parametricTags],
  axes: dataset.axes,
  axisRanges: dataset.axisRanges || {},
  controlAxes: dataset.controlAxes || [],
  transforms: dataset.transforms || [],
  grade: dataset.grade || null,
  cornerPins: dataset.cornerPins || [],
  clampOutOfRange: dataset.clampOutOfRange || false,
  familyName: dataset.health.family_name,
  upm: dataset.health.upm,
  sampleText: dataset.sampleText || null,
});

let persistWarned = false;
const persistSession = async () => {
  if (!uploadDataset) return;
  try {
    await saveSession(serializeDataset(uploadDataset));
  } catch (err) {
    // Quota/IDB failure: persistence is best-effort, editing continues.
    if (!persistWarned) console.warn('Session could not be persisted:', err);
    persistWarned = true;
  }
};

let persistTimer = null;
const persistSoon = () => {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(persistSession, 500);
};

// Rehydrate a stored session as the live dataset WITHOUT recompiling.
// Deliberately not buildUploadDataset: the stored fontBytes already carry
// the avar2 table. Returns true when a session was restored.
const restoreSession = async () => {
  const rec = await loadSession().catch(() => null);
  if (!rec) return false;
  if (rec.version !== SESSION_VERSION) {
    await clearSession().catch(() => {});
    return false;
  }
  try {
    const meta = parseFont(rec.fontBytes); // sanity: the bytes still parse
    const familyName = rec.familyName || meta.familyName;
    uploadDataset = {
      sourceText: rec.sourceText,
      fontBytes: rec.fontBytes,
      mappingsCsv: rec.csvText,
      instancesCsv: mappingsCsv.parseMappingsCsv(rec.csvText),
      parametricTags: new Set(rec.parametricTags),
      axisRanges: rec.axisRanges || {},
      fontUrl: URL.createObjectURL(new Blob([rec.fontBytes], { type: 'font/ttf' })),
      sourceName: rec.sourceName,
      stem: rec.stem,
      sourceDir: rec.sourceDir,
      workspaceEntries: rec.workspaceEntries || null,
      controlAxes: rec.controlAxes || [],
      transforms: rec.transforms || [],
      grade: rec.grade || null,
      cornerPins: rec.cornerPins || [],
      clampOutOfRange: rec.clampOutOfRange || false,
      sampleText: rec.sampleText || null,
      axes: rec.axes,
      instances: { instances: [] },
      coverage: [
        ...auditCoverage(rec.fontBytes).findings,
        ...await probeSweeps(rec.fontBytes, parseFont(rec.fontBytes).axes, (b, g, l) => measureAt(b, g, l)),
      ],
      health: {
        static: true, demo: false, building: false,
        font_built: true, font_loaded: true,
        last_build_time: Date.now(), // see buildUploadDataset
        glyphs_path: `upload:${rec.sourceName}:${Date.now()}`,
        original_path: `upload:${rec.sourceName}`,
        source_format: rec.sourceFormat,
        family_name: familyName,
        vf_family_id: `${familyName}-VF`,
        built_font_filename: `${familyName}.ttf`,
        last_build_status: 'ok', last_build_error: null,
        avar2_error: null, build_stale: false,
        upm: rec.upm || meta.upm,
      },
    };
    syncInstancesFromCsv(uploadDataset);
    transformsState = [];
    bakedEnabledIds = [];
    return true;
  } catch (err) {
    console.warn('Stored session was unreadable — starting fresh:', err);
    await clearSession().catch(() => {});
    uploadDataset = null;
    return false;
  }
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
  console.log('[static-api] regenerateFont called from:', new Error().stack?.split('\n')[2]?.trim());
  persistSoon(); // the CSV changed even when no avar2 regen is needed
  if (mappingsCsv.userColumns(dataset.instancesCsv, [...dataset.parametricTags]).length === 0) {
    return;
  }  const ranges = Object.keys(dataset.axisRanges || {}).length
    ? JSON.stringify(dataset.axisRanges)
    : null;
  console.log('[regenerateFont] axisRanges:', dataset.axisRanges);
  console.log('[regenerateFont] ranges JSON:', ranges);
  dataset.fontBytes = await addAvar2(
    dataset.fontBytes,
    mappingsCsv.serializeMappingsCsv(dataset.instancesCsv),
    ranges,
    [...dataset.parametricTags]
  );
  URL.revokeObjectURL(dataset.fontUrl);
  dataset.fontUrl = URL.createObjectURL(new Blob([dataset.fontBytes], { type: 'font/ttf' }));
  dataset.health.last_build_time = Date.now();
  // Re-read axes from the patched font — use refreshAxesFromFont so
  // GRAD/SPAC/control-axis flags are preserved (the inline mapping here
  // used to drop them, making those axes disappear after avar2 edits).
  refreshAxesFromFont(dataset);
};

// ---- corner pinning ---------------------------------------------------------
//
// A pin holds an uncovered corner up with the scaffold location's
// shape (healthy edge — never the ghost itself). Pins are workspace
// state: they ride sessions, the workspace zip, and rebuilds.
//
// Scaffold choice: sweep from the default location toward the corner
// (every differing axis interpolated together) and take the measured
// peak — the last healthy point before the collapse. When the peak IS
// the default (the sweep only collapses), nothing reusable exists on
// the path: return null and pin_corner synthesizes the corner shape by
// extrapolating the model's master trends — and refuses outright when
// no trend reaches the corner either.

const chooseScaffold = async (dataset, corner) => {
  const defaults = Object.fromEntries(
    dataset.axes.axes.filter(a => a.has_master_coverage).map(a => [a.tag, a.default])
  );
  const steps = [];
  for (let i = 0; i <= 6; i++) {
    const t = i / 6;
    steps.push(Object.fromEntries(
      Object.keys(defaults).map(tag => [tag, defaults[tag] + ((corner[tag] ?? defaults[tag]) - defaults[tag]) * t])
    ));
  }
  const areas = await measureAt(dataset.fontBytes, PROBE_GLYPHS, steps);
  const peakI = areas.indexOf(Math.max(...areas));
  if (peakI === 0) return null;
  return steps[peakI];
};

const applyPins = async (dataset) => {
  for (const pin of dataset.cornerPins || []) {
    dataset.fontBytes = await pinCornerWasm(dataset.fontBytes, pin.corner, pin.scaffold);
  }
};

const refreshAfterPin = async (dataset) => {
  URL.revokeObjectURL(dataset.fontUrl);
  dataset.fontUrl = URL.createObjectURL(new Blob([dataset.fontBytes], { type: 'font/ttf' }));
  dataset.health.last_build_time = Date.now();
  const structural = auditCoverage(dataset.fontBytes).findings;
  const behavioral = await probeSweeps(dataset.fontBytes, parseFont(dataset.fontBytes).axes, (b, g, l) => measureAt(b, g, l));
  dataset.coverage = [...structural, ...behavioral];
  persistSoon();
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
  fix_instances: {
    id: 'fix_instances',
    name: 'Clean fvar instances',
    description: "Regenerate the font's named instances (fix-instances) so they match the current axes.",
  },
  gen_stat: {
    id: 'gen_stat',
    name: 'Rebuild STAT table',
    description: 'Generate the STAT table from the Google Fonts axis registry. Registered axes (wght/wdth/opsz) only — custom axes need a STAT config.',
  },
  fix_unhinted: {
    id: 'fix_unhinted',
    name: 'Smooth unhinted rendering',
    description: 'Add gasp + prep tables so an unhinted variable font rasterizes with grayscale anti-aliasing at all sizes.',
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
  corner_pins: { version: 1, pins: dataset.cornerPins || [] },
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
    dataset.fontBytes = await addAvar2(dataset.fontBytes, avar2Csv, null, [...dataset.parametricTags]);
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
    dataset.grade = grade; // kept so rebuilds re-apply it
    report.applied.push('grade');
  }
  // SPAC transforms apply last (they rebuild HVAR from the gvar the
  // earlier sections produced). Disabled and unknown-type entries never
  // reach the font — the wasm side applies only enabled known ones.
  if (transforms.some(t => t.enabled && KNOWN_TRANSFORMS[t.type])) {
    dataset.fontBytes = await applyTransforms(dataset.fontBytes, JSON.stringify(transforms), avar2Csv);
    report.applied.push('transforms (SPAC)');
  }
  // Corner pins: replace the set and re-apply onto the font the
  // earlier sections produced.
  const cornerPins = bundle.corner_pins?.pins || [];
  if (cornerPins.length) {
    dataset.cornerPins = cornerPins;
    await applyPins(dataset);
    report.applied.push('corner pins');
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
  refreshAxesFromFont(dataset);
  return report;
};

// Re-derive dataset.axes from the font's fvar after a mutation that
// changed the axis set (bundle import, transforms toggle, rebuild).
// User axes (CSV in-columns), control axes, GRAD and transform-injected
// axes don't come from compiled masters — SPAC rides the fvar as a
// live-preview parametric slider.
const refreshAxesFromFont = (dataset) => {
  const csv = dataset.mappingsCsv || '';
  const compiledTags = new Set(dataset.parametricTags);
  const userTags = csv.trim()
    ? new Set(csvHeaderTags(csv).filter(t => !compiledTags.has(t) && t !== 'SPAC').map(t => mappingsCsv.normalizeInAxisName(t)))
    : new Set();
  const controlTags = new Set((dataset.controlAxes || []).map(a => a.tag));
  const injectedTags = new Set(
    (dataset.transforms || [])
      .filter(t => t.enabled)
      .map(t => KNOWN_TRANSFORMS[t.type || t.id]?.injected_axis_tag)
      .filter(Boolean)
  );
  console.log('[refreshAxesFromFont] enabled transforms:', dataset.transforms?.filter(t => t.enabled));
  console.log('[refreshAxesFromFont] injectedTags:', [...injectedTags]);
  console.log('[refreshAxesFromFont] userTags (normalized):', [...userTags]);
  console.log('[refreshAxesFromFont] compiledTags:', [...compiledTags]);
  console.log('[refreshAxesFromFont] userTags (normalized):', [...userTags]);
  const meta = parseFont(dataset.fontBytes);
  console.log('[refreshAxesFromFont] font axes with ranges:', meta.axes.map(a => ({ tag: a.tag, min: a.min, default: a.default, max: a.max })));
  console.log('[refreshAxesFromFont] font axes:', meta.axes.map(a => a.tag));
  // Use display_name from axisRanges (user-defined metadata) when
  // available; the font's name table often lacks entries for avar2 axes.
  const axisRanges = dataset.axisRanges || {};
  dataset.axes = {
    axes: meta.axes.map(a => {
      const rangeOverride = axisRanges[a.tag] || {};
      const hasCoverage = !userTags.has(a.tag) && !controlTags.has(a.tag) && a.tag !== 'GRAD';
      console.log(`[refreshAxesFromFont] ${a.tag}: has_master_coverage=${hasCoverage}, in userTags=${userTags.has(a.tag)}`);
      return {
        tag: a.tag,
        name: rangeOverride.display_name || a.name || a.tag,
        min: a.min, default: a.default, max: a.max,
        has_master_coverage: hasCoverage,
        is_control_axis: controlTags.has(a.tag),
        is_grade_axis: a.tag === 'GRAD',
        transform_injected: injectedTags.has(a.tag),
      };
    }),
  };
};

// The Transforms menu on an uploaded source: the known built-ins with
// the dataset's enabled/params state overlaid (bundle imports and
// toggles set it), plus any unknown imported entries (the wasm skips
// those — they render so the menu reflects the bundle).
const transformsMenu = (dataset) => {
  const state = new Map((dataset.transforms || []).map(t => [t.type || t.id, t]));
  const known = Object.values(KNOWN_TRANSFORMS).map(k => {
    const s = state.get(k.id) || {};
    return { ...k, enabled: !!s.enabled, params: { ...(s.params || {}) } };
  });
  const knownIds = new Set(Object.keys(KNOWN_TRANSFORMS));
  const unknown = (dataset.transforms || [])
    .filter(t => !knownIds.has(t.type || t.id))
    .map(t => ({ id: t.type || t.id, name: t.name || t.type || t.id, enabled: !!t.enabled, params: t.params || {} }));
  return [...known, ...unknown];
};

// The full rebuild pipeline for an uploaded .glyphs source: compile,
// then re-apply the studio state in bundle-import order — avar2
// mappings, control axes, grade, SPAC transforms, corner pins — and
// finally the out-of-range drop. Each stage's HVAR rebuild reads the
// current gvar, so the final font carries every stage's advances.
// Shared by buildFont and updateTransforms (the only way to change the
// transform set — applied transforms can't be un-baked).
let rebuildCount = 0;
const rebuildUploadFont = async (dataset) => {
  rebuildCount++;
  console.log(`[static-api] rebuildUploadFont called (#${rebuildCount}) from:`, new Error().stack?.split('\n')[2]?.trim());
  if (dataset.sourceText == null) {
    throw new Error("This needs the full app — the browser can't compile UFO sources yet.");
  }
  let ttf = await compileFont(dataset.sourceText);
  if (dataset.mappingsCsv) {
    const ranges = Object.keys(dataset.axisRanges || {}).length
      ? JSON.stringify(dataset.axisRanges)
      : null;
    ttf = await addAvar2(ttf, dataset.mappingsCsv, ranges, [...dataset.parametricTags]);
  }
  dataset.fontBytes = ttf;
  if ((dataset.controlAxes || []).length) {
    dataset.fontBytes = await applyControlAxes(dataset.fontBytes, JSON.stringify(dataset.controlAxes));
  }
  const grade = dataset.grade || {};
  const gradeInstances = grade.enabled ? (grade.instances || []) : [];
  // Apply grade whenever enabled — even with no per-instance grades, the
  // GRAD axis needs to exist so the slider shows. applyGrade with an
  // empty instances list adds the axis at default (no visual change).
  if (grade.enabled) {
    const coords = resolveGradeCoords(dataset, dataset.mappingsCsv || '');
    dataset.fontBytes = await applyGrade(
      dataset.fontBytes, JSON.stringify(grade), JSON.stringify(coords)
    );
    console.log('[rebuildUploadFont] after applyGrade, font axes:', parseFont(dataset.fontBytes).axes.map(a => a.tag));
  }
  const transforms = (dataset.transforms || [])
    .map(t => ({ type: t.type || t.id, enabled: !!t.enabled, params: t.params || {} }));
  if (transforms.some(t => t.enabled && KNOWN_TRANSFORMS[t.type])) {
    dataset.fontBytes = await applyTransforms(
      dataset.fontBytes, JSON.stringify(transforms), dataset.mappingsCsv || ''
    );
    console.log('[rebuildUploadFont] after applyTransforms, font axes:', parseFont(dataset.fontBytes).axes.map(a => a.tag));
  }
  await applyPins(dataset);
  if (dataset.clampOutOfRange) {
    dataset.fontBytes = await clampOutOfRangeWasm(dataset.fontBytes);
  }
  console.log('[rebuildUploadFont] before refreshAxesFromFont, transforms:', dataset.transforms);
  console.log('[rebuildUploadFont] before refreshAxesFromFont, grade:', dataset.grade);
  refreshAxesFromFont(dataset);
  console.log('[rebuildUploadFont] after refreshAxesFromFont, axes:', dataset.axes.axes.map(a => ({ tag: a.tag, transform_injected: a.transform_injected, is_grade_axis: a.is_grade_axis })));
};

// After any rebuild-from-source: swap the object URL, stamp the build
// (the App re-reads fontUrl only when last_build_time changes) and
// persist the session.
const commitRebuiltFont = (dataset) => {
  console.log('[static-api] commitRebuiltFont called from:', new Error().stack?.split('\n')[2]?.trim());
  URL.revokeObjectURL(dataset.fontUrl);
  dataset.fontUrl = URL.createObjectURL(new Blob([dataset.fontBytes], { type: 'font/ttf' }));
  dataset.health.last_build_time = Date.now();
  persistSoon();
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
  getTransforms: async () => (uploadDataset ? { transforms: transformsMenu(uploadDataset) } : { transforms: await transformsList() }),
  getCoverage: async () => ({
    findings: uploadDataset ? uploadDataset.coverage || [] : [],
    // Fresh array every call: dataset.cornerPins is mutated in place
    // by pinCorner, and the same reference through getCoverage left
    // React's Object.is state check blind to the update.
    pins: [...(uploadDataset ? uploadDataset.cornerPins || [] : [])],
  }),
  getGrade: async () => (uploadDataset
    ? (uploadDataset.grade || { enabled: false, default_pct: 0.25, instances: [], max_pct: {} })
    : endpoint('grade.json')()),
  listControlAxes: async () => (uploadDataset
    ? { axes: uploadDataset.controlAxes || [] }
    : endpoint('control-axes.json')()),
  getGlyphCoverage: async () => {
    if (!uploadDataset) return endpoint('glyph-coverage.json')();
    // Synthesize coverage rows for the upload's control axes so the
    // sidebar's SECONDARY PARAMETRIC AXES section shows them. Marked
    // source: 'studio' — the rows get the edit affordances (add/remove
    // layers); the braces are computed by the wasm on rebuild.
    const axes = (uploadDataset.controlAxes || []).map(a => {
      const covers = [...new Set((a.layers || []).map(l => l.glyph))];
      return {
        tag: a.tag,
        name: a.name || a.tag,
        min: a.min,
        default: a.default,
        max: a.max,
        kind: 'scoped',
        source: 'studio',
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
  // Whole project as one zip (sources + studio sidecars + preview build)
  // — loads back here or in the full app. Uploads only; snapshots have
  // no workspace to export.
  exportWorkspaceUrl: () => {
    if (!uploadDataset) {
      throw new Error('Workspace export needs an uploaded source — snapshots are read-only demos.');
    }
    return URL.createObjectURL(
      new Blob([buildWorkspaceZip(uploadDataset)], { type: 'application/zip' })
    );
  },

  // Uploads: compile the source in a Web Worker (fontc-wasm) and switch
  // the app to the resulting in-memory dataset. This is the Phase 2
  // path — no server anywhere. A project .zip travels as one archive
  // (the only way a .designspace + its UFOs can arrive).
  uploadSource: async (files) => {
    const list = Array.from(files || []);
    const zipFile = list.find(f => f.name.toLowerCase().endsWith('.zip'));
    if (zipFile) {
      if (list.length > 1) {
        throw new Error('Upload the .zip on its own — it carries the whole project.');
      }
      const ws = readWorkspaceZip(new Uint8Array(await zipFile.arrayBuffer()));
      uploadDataset = await buildUploadDataset({
        sourceName: ws.sourceName,
        sourceFormat: ws.sourceExt,
        sourceText: ws.sourceText,
        fontBytes: ws.previewTtf,
        csvText: ws.csvText,
        metadataText: ws.metadataText,
        workspaceEntries: ws.entries,
        sourceDir: ws.sourceDir,
      });
      transformsState = [];
      bakedEnabledIds = [];
      // Harvested control axes / transforms apply through the bundle
      // machinery (same wasm steps, same warnings); the CSV was already
      // applied by the dataset build above. Corner pins apply after.
      if (ws.controlText || ws.transformsText) {
        await applyBundle({
          format: 'avar2-studio-config', format_version: 1,
          source: { avar2_out_columns: [...uploadDataset.parametricTags] },
          control_axes: ws.controlText ? JSON.parse(ws.controlText) : { version: 1, axes: [] },
          avar2_csv: '',
          transforms: ws.transformsText ? JSON.parse(ws.transformsText) : { version: 1, transforms: [] },
          grade: { version: 1, enabled: false, default_pct: 0.25, instances: [] },
        }, uploadDataset);
      }
      if (ws.cornerPinsText) {
        uploadDataset.cornerPins = JSON.parse(ws.cornerPinsText).pins || [];
        await applyPins(uploadDataset);
      }
      await persistSession();
      return { ok: true, ignored_files: [] };
    }
    const glyphsFile = list.find(f => f.name.toLowerCase().endsWith('.glyphs'));
    if (!glyphsFile) {
      throw new Error('No .glyphs or project .zip in the upload (.designspace projects need the zip)');
    }
    const csvFile = list.find(f => f.name.toLowerCase().endsWith('-avar.csv')) || null;
    const metadataFile = list.find(f => f.name.toLowerCase().endsWith('axis-metadata.json')) || null;
    const ignored = list.filter(f => f !== glyphsFile && f !== csvFile && f !== metadataFile).map(f => f.name);
    uploadDataset = await buildUploadDataset({
      sourceName: glyphsFile.name,
      sourceFormat: 'glyphs',
      sourceText: await glyphsFile.text(),
      csvText: csvFile ? await csvFile.text() : null,
      metadataText: metadataFile ? await metadataFile.text() : null,
    });
    transformsState = [];
    bakedEnabledIds = [];
    await persistSession();
    return { ok: true, ignored_files: ignored };
  },

  // Rebuild only exists for uploaded .glyphs sources: the full pipeline
  // re-runs (compile → avar2 → control axes → grade → transforms →
  // pins → out-of-range drop) —
  // the rebuilt fontBytes stay the dataset's truth.
  buildFont: async () => {
    if (!uploadDataset) {
      throw new Error('Building needs the full app — this static demo is read-only.');
    }
    if (uploadDataset.sourceText == null) {
      throw new Error("Rebuilding a .designspace needs the full app — the browser can't compile UFO sources yet.");
    }
    await rebuildUploadFont(uploadDataset);
    URL.revokeObjectURL(uploadDataset.fontUrl);
    uploadDataset = {
      ...uploadDataset,
      fontUrl: URL.createObjectURL(new Blob([uploadDataset.fontBytes], { type: 'font/ttf' })),
    };
    uploadDataset.health.last_build_time = Date.now();
    persistSoon();
    return { ok: true };
  },

  // Load Font: swap the dataset; App's loadData() re-reads the new
  // health (different glyphs_path) and treats it as a source swap.
  // Switching to an example abandons the uploaded project — drop the
  // stored session with it (snapshots are never persisted).
  loadExample: async (id) => {
    const idx = await examplesIndex();
    if (!(idx.examples || []).some(e => e.id === id)) {
      throw new Error(`Unknown example: ${id}`);
    }
    dataset = id;
    uploadDataset = null;
    transformsState = null;
    bakedEnabledIds = null;
    clearSession().catch(() => {});
    return { ok: true };
  },

  // "Forget this project": drop the stored session AND unload back to
  // the default example — auto-restore brings nothing back after this.
  forgetSession: async () => {
    await clearSession().catch(() => {});
    uploadDataset = null;
    transformsState = null;
    bakedEnabledIds = null;
    dataset = (await examplesIndex()).examples?.[0]?.id || 'crispy-mini';
    return { ok: true };
  },

  // Transforms toggles: on SNAPSHOT datasets, allowed only between the
  // two baked states (the snapshot's enabled set ↔ all-off). Anything
  // else isn't baked and throws — App reverts the toggle and shows the
  // message. On UPLOADED .glyphs sources the set is real: applied
  // transforms can't be un-baked, so the font rebuilds from source with
  // the new set applied. Enabled/params merge OVER the stored list so
  // name/description/schema metadata survives (the App renders the menu
  // from our return value).
  updateTransforms: async (entries) => {
    if (uploadDataset) {
      if (uploadDataset.sourceText == null) {
        throw new Error("Transform toggles on a .designspace project need the full app — the browser can't rebuild UFO sources yet.");
      }
      const list = (entries || []).map(e => ({
        type: e.type || e.id, enabled: !!e.enabled, params: e.params || {},
      }));
      // The registry's one-injector-per-axis rule (same as the bundle
      // validation): two enabled SPAC transforms would produce a font
      // with two SPAC axes.
      const spacInjectors = list
        .filter(t => t.enabled && KNOWN_TRANSFORMS[t.type]?.injected_axis_tag === 'SPAC')
        .map(t => t.type);
      if (spacInjectors.length > 1) {
        throw new Error(`Only one transform can add the SPAC axis at a time ('${spacInjectors.join("' and '")}' both do)`);
      }
      uploadDataset.transforms = list;
      await rebuildUploadFont(uploadDataset);
      commitRebuiltFont(uploadDataset);
      return { transforms: transformsMenu(uploadDataset) };
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
    // Migrate grade state keyed by instance name
    const grade = uploadDataset.grade;
    if (grade) {
      if (grade.instances) {
        for (const entry of grade.instances) {
          if (entry.name === instanceName) entry.name = newName;
        }
      }
      if (grade.max_pct && instanceName in grade.max_pct) {
        grade.max_pct[newName] = grade.max_pct[instanceName];
        delete grade.max_pct[instanceName];
      }
    }
    persistSoon();
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
    // EditAxisModal payload: {display_name, registered_tag, min,
    // default, max} — merge all of it into the axis-metadata entry
    // (name/tag edits included; earlier this dropped them silently).
    uploadDataset.axisRanges[axisName] = {
      ...(uploadDataset.axisRanges[axisName] || {}),
      ...(axisData.display_name !== undefined ? { display_name: axisData.display_name } : {}),
      ...(axisData.registered_tag !== undefined ? { registered_tag: axisData.registered_tag } : {}),
      ...(axisData.min !== undefined ? { min: axisData.min } : {}),
      ...(axisData.default !== undefined ? { default: axisData.default } : {}),
      ...(axisData.default_value !== undefined ? { default: axisData.default_value } : {}),
      ...(axisData.max !== undefined ? { max: axisData.max } : {}),
    };
    await regenerateFont(uploadDataset);
    return {};
  },
  deleteAvar2Axis: async (axisName) => {
    requireUpload();
    mappingsCsv.removeColumn(uploadDataset.instancesCsv, axisName);
    delete uploadDataset.axisRanges[axisName];
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  updateAvar2Mapping: async (instanceName, axisName, value) => {
    requireUpload();
    mappingsCsv.upsertRow(uploadDataset.instancesCsv, instanceName, { [axisName]: value });
    console.log('[updateAvar2Mapping] CSV after update:', mappingsCsv.serializeMappingsCsv(uploadDataset.instancesCsv));
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  setGrade: async (patch) => {
    console.log('[static-api] setGrade called with:', patch);
    requireUpload();
    uploadDataset.grade = { ...(uploadDataset.grade || {}), ...patch };
    console.log('[static-api] uploadDataset.grade after update:', uploadDataset.grade);
    try {
      await rebuildUploadFont(uploadDataset);
      commitRebuiltFont(uploadDataset);
      console.log('[static-api] setGrade completed, returning:', uploadDataset.grade);
      return uploadDataset.grade;
    } catch (err) {
      console.error('[static-api] setGrade failed:', err);
      throw err;
    }
  },
  setInstanceGrade: async (instanceName, pct) => {
    requireUpload();
    const grade = uploadDataset.grade ||= { version: 1, enabled: false, default_pct: 0.25, instances: [] };
    grade.instances = (grade.instances || []).filter(e => e.name !== instanceName);
    if (pct !== undefined && pct !== null) {
      grade.instances.push({ name: instanceName, pct });
    }
    await rebuildUploadFont(uploadDataset);
    commitRebuiltFont(uploadDataset);
    return grade;
  },
  removeInstanceGrade: async (instanceName) => {
    requireUpload();
    const grade = uploadDataset.grade;
    if (grade) {
      grade.instances = (grade.instances || []).filter(e => e.name !== instanceName);
    }
    await rebuildUploadFont(uploadDataset);
    commitRebuiltFont(uploadDataset);
    return grade;
  },
  // Control axes (secondary parametric axes) on uploads: declarations
  // live in dataset.controlAxes using the sidecar shape ({tag, name,
  // min, default, max, layers: [{glyph, location}]}); every mutation
  // rebuilds from source through the shared pipeline — the wasm
  // computes the brace tuples (drawn outlines stay a full-app feature).
  createControlAxis: async (axis) => {    requireUpload();
    // Modal payload: {tag, display_name, default, min, max}.
    (uploadDataset.controlAxes ||= []).push({
      tag: axis.tag,
      name: axis.display_name || axis.tag,
      min: axis.min, default: axis.default, max: axis.max,
      layers: [],
    });
    await rebuildUploadFont(uploadDataset);
    commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  updateControlAxis: async (tag, updates) => {
    requireUpload();
    const ax = (uploadDataset.controlAxes || []).find(a => a.tag === tag);
    if (!ax) throw new Error(`No control axis '${tag}'`);
    if (updates.display_name !== undefined) ax.name = updates.display_name;
    for (const k of ['min', 'default', 'max']) {
      if (updates[k] !== undefined) ax[k] = updates[k];
    }
    await rebuildUploadFont(uploadDataset);
    commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  deleteControlAxis: async (tag) => {
    requireUpload();
    uploadDataset.controlAxes = (uploadDataset.controlAxes || []).filter(a => a.tag !== tag);
    await rebuildUploadFont(uploadDataset);
    commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  // Layer locations from the UI pin every axis (including control,
  // grade and transform-injected ones at their defaults); the wasm
  // only knows the compiled parametric axes plus CSV user columns and
  // rejects anything else — strip the rest before storing.
  controlAxisLayerDelta: async (tag, delta) => {
    requireUpload();
    const ax = (uploadDataset.controlAxes || []).find(a => a.tag === tag);
    if (!ax) throw new Error(`No control axis '${tag}'`);
    ax.layers ||= [];
    const allowed = new Set([...uploadDataset.parametricTags]);
    for (const t of csvHeaderTags(uploadDataset.mappingsCsv || '')) allowed.add(t);
    const clean = (l) => ({
      glyph: l.glyph,
      location: Object.fromEntries(Object.entries(l.location || {}).filter(([k]) => allowed.has(k))),
    });
    const sameLayer = (a, b) =>
      a.glyph === b.glyph &&
      JSON.stringify(Object.entries(a.location || {}).sort()) ===
        JSON.stringify(Object.entries(b.location || {}).sort());
    for (const rm of delta.remove || []) {
      ax.layers = ax.layers.filter(l => !sameLayer(l, clean(rm)));
    }
    for (const add of delta.add || []) {
      const c = clean(add);
      if (!ax.layers.some(l => sameLayer(l, c))) ax.layers.push(c);
    }
    await rebuildUploadFont(uploadDataset);
    commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  setControlAxisLayers: async (tag, layers) => {
    requireUpload();
    const ax = (uploadDataset.controlAxes || []).find(a => a.tag === tag);
    if (!ax) throw new Error(`No control axis '${tag}'`);
    const allowed = new Set([...uploadDataset.parametricTags]);
    for (const t of csvHeaderTags(uploadDataset.mappingsCsv || '')) allowed.add(t);
    ax.layers = (layers || []).map(l => ({
      glyph: l.glyph,
      location: Object.fromEntries(Object.entries(l.location || {}).filter(([k]) => allowed.has(k))),
    }));
    await rebuildUploadFont(uploadDataset);
    commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
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
    const applied = await applyBundle(bundle, uploadDataset);
    persistSoon();
    return applied;
  },

  // Pin a ghost corner: scaffold from the measured healthy edge, hold
  // the corner up (model-computed tuple), then regenerate the stack
  // (avar2 from the CSV if present, STAT always) and re-audit.
  pinCorner: async (corner) => {
    requireUpload();
    const scaffold = await chooseScaffold(uploadDataset, corner);
    uploadDataset.fontBytes = await pinCornerWasm(uploadDataset.fontBytes, corner, scaffold);
    (uploadDataset.cornerPins ||= []).push({ corner, scaffold });
    if (uploadDataset.mappingsCsv) {
      uploadDataset.fontBytes = await addAvar2(
        uploadDataset.fontBytes,
        mappingsCsv.serializeMappingsCsv(uploadDataset.instancesCsv),
        null,
        [...uploadDataset.parametricTags]
      );
    }
    uploadDataset.fontBytes = await regenStat(uploadDataset.fontBytes);
    await refreshAfterPin(uploadDataset);
    return { ok: true, scaffold, synthesized: scaffold == null };
  },

  // Drop out-of-range (stranded) sources — the Glyphs.app/fontmake
  // semantics: their gvar deltas are zeroed and HVAR rebuilt. Sticks
  // to the dataset: rebuilds re-apply it, sessions carry it.
  clampOutOfRange: async () => {
    requireUpload();
    uploadDataset.fontBytes = await clampOutOfRangeWasm(uploadDataset.fontBytes);
    uploadDataset.clampOutOfRange = true;
    await refreshAfterPin(uploadDataset);
    return { ok: true };
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
  // Debug/introspection hook for the static provider (used by e2e and
  // manual diagnosis).
  if (typeof window !== 'undefined') window.__avar2api = api;
  // Auto-restore the stored session before the app renders — health()
  // then answers for the uploaded dataset and the app boots into it.
  await restoreSession();
  return true;
}
