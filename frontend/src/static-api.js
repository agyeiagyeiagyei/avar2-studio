/**
 * Static-demo data provider — lets the built bundle run with NO backend
 * (GitHub Pages). docs/migration-github-pages.md has the history.
 *
 * How it works: before the app renders, selectApiMode() probes
 * /api/health. A real server answers 200 → nothing changes. On a static
 * host the probe 404s → we swap api's methods for in-browser ones.
 *
 * Every project is a live, in-memory workspace — the bundled examples
 * included. An example ships as the project zip a designer would upload
 * (scripts/snapshot_static_demo.py stages it) plus a pristine fontc
 * compile of the source, so it shows instantly; from then on it is an
 * upload like any other: instance and mapping edits regenerate avar2,
 * secondary-axis / grade / transform edits and Rebuild recompile the
 * source in the fontc-wasm worker, the session persists in IndexedDB,
 * and "Forget this project" reloads the pristine copy.
 *
 * Not available in the browser (throws with guidance): writing back to
 * the source file, re-seeding layers from source, the outline editor,
 * and recompiling .designspace projects (fontc-wasm can't read UFOs off
 * a filesystem — those load from the pristine build inside their zip).
 */

import { api } from './api';
import { compileFont, compileWithOverlays, addAvar2, measureAt, pinCorner as pinCornerWasm, regenStat, clampOutOfRange as clampOutOfRangeWasm, applyTransforms, applyControlAxes, applyGrade } from './fontc-compile';
import { parseFont } from './fvar';
import { mappedLocation } from './avar2-eval';
import * as mappingsCsv from './mappings-csv';
import { readWorkspaceZip, buildWorkspaceZip } from './zip-workspace';
import { saveSession, loadSession, clearSession, SESSION_VERSION } from './session';
import { auditCoverage, probeSweeps, PROBE_GLYPHS } from './coverage.js';
import { lintAvar2Mappings } from './avar2-lint.js';
import { maxPctFor, gradeDiagnostics } from './grade-model.js';
import { createEditorBridge, embedEditorUrl } from './editor-bridge.js';

const DATA = 'static-demo'; // relative — resolves under any --base

let staticMode = false;
export const isStaticMode = () => staticMode;
// True while a project (a bundled example or an upload) is loaded —
// editing, Rebuild and session persistence exist for these.
export const isUploadDataset = () => !!uploadDataset;

// Sample text persistence — stored on the dataset so it survives reload.
export const getSampleText = () => uploadDataset?.sampleText || null;
export const setSampleText = (text) => {
  if (uploadDataset) {
    uploadDataset.sampleText = text;
    persistSoon();
  }
};

// ---- bundled examples index --------------------------------------------------

let examplesPromise = null;
let loadError = null; // why no project is loaded (boot failure), if any

const fetchJSON = async (path) => {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`static demo data missing (${path})`);
  return r.json();
};

const fetchBytes = async (path) => {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`static demo data missing (${path})`);
  return new Uint8Array(await r.arrayBuffer());
};

const examplesIndex = () => {
  if (!examplesPromise) examplesPromise = fetchJSON(`${DATA}/examples.json`);
  return examplesPromise;
};

const defaultExampleId = async () =>
  (await examplesIndex()).examples?.[0]?.id || 'crispy-mini';

// ---- project state (fontc-wasm compiled in a Worker) ------------------------
//
// A .glyphs project compiles in-browser; everything the studio shows
// comes from the compiled font itself (fvar axes + named instances +
// name table — see fvar.js) plus the sidecars that travelled with it
// (avar2 mappings, transforms, grade, control axes). A source uploaded
// without sidecars gets empty studio surfaces, exactly like a
// blind-launched source on the real server.

let uploadDataset = null; // {health, axes, instances, fontUrl, sourceText, origin, exampleId}

// Axes the behavioral sweep probe may cover: only those gvar actually
// varies. User (avar2-input) axes act entirely through the mapping —
// which measure_at does not evaluate — and transform/grade axes are
// injected post-build (SPAC moves advances, not outlines): probing any
// of them reports a false "inert axis" per axis.
const gvarSweepAxes = (fontBytes, axesMeta) => {
  const flags = new Map((axesMeta || []).map(a => [a.tag, a]));
  return parseFont(fontBytes).axes.filter(a => {
    const f = flags.get(a.tag);
    return !f || (f.has_master_coverage && !f.transform_injected && !f.is_grade_axis);
  });
};

// sourceFormat is 'glyphs' (sourceText set — compiles/rebuilds in-browser)
// or 'designspace' (fontBytes = baked preview TTF from the project zip —
// fontc can't compile UFO sources off the filesystem). csvText/metadataText
// are the harvested sidecars; workspaceEntries/sourceDir are the raw zip
// contents for verbatim re-export (null for picker uploads).
const buildUploadDataset = async ({
  sourceName, sourceFormat, sourceText = null, fontBytes = null,
  csvText = null, metadataText = null, workspaceEntries = null, sourceDir = '',
  origin = 'upload', exampleId = null,
}) => {
  let bytes = fontBytes ?? await compileFont(sourceText);
  let mappingsText = csvText;
  // A header-only CSV (a project with no instance rows — e.g. a workspace
  // zip of a plain upload) is no mappings: add_avar2 aborts on it
  // ("mappings CSV has no instance rows").
  if (mappingsText && mappingsCsv.parseMappingsCsv(mappingsText).rows.length === 0) {
    mappingsText = null;
  }
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
    origin,      // 'example' | 'upload'
    exampleId,   // for 'example': the bundled project it started from
    axes: {
      axes: meta.axes.map(a => ({
        tag: a.tag, name: a.name,
        min: a.min, default: a.default, max: a.max,
        has_master_coverage: !userAxisTags.has(a.tag), is_control_axis: false,
      })),
    },
    instances: { instances: [] },
    coverage: [], // auditDataset below, once the axes metadata exists
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
  dataset.coverage = await auditDataset(dataset);
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
  origin: dataset.origin || 'upload',
  exampleId: dataset.exampleId || null,
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
      origin: rec.origin || 'upload',
      exampleId: rec.exampleId || null,
      workspaceEntries: rec.workspaceEntries || null,
      controlAxes: rec.controlAxes || [],
      transforms: rec.transforms || [],
      grade: rec.grade || null,
      cornerPins: rec.cornerPins || [],
      clampOutOfRange: rec.clampOutOfRange || false,
      sampleText: rec.sampleText || null,
      axes: rec.axes,
      instances: { instances: [] },
      coverage: [],
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
    uploadDataset.coverage = await auditDataset(uploadDataset);
    return true;
  } catch (err) {
    console.warn('Stored session was unreadable — starting fresh:', err);
    await clearSession().catch(() => {});
    uploadDataset = null;
    return false;
  }
};

// avar2 mapping lint (authoring layer): default-location rows the wasm
// builder silently discards (skewing every other row), and unmapped
// {min, default, max}^n grid points — the "dead cross" where an input
// axis at its default zeroes every authored tent. Invisible to
// auditCoverage, which audits gvar master coverage over the ±1 corners
// and never probes a coordinate AT an axis default. Ranges come from
// the patched font's fvar (post-addAvar2, so metadata is baked in);
// axisRanges fills in for columns fvar doesn't carry yet.
const lintMappingsFindings = (dataset) => {
  const parsed = dataset.instancesCsv;
  if (!parsed?.rows?.length || !parsed?.columns?.length) return [];
  const byTag = new Map(parseFont(dataset.fontBytes).axes.map(a => [a.tag, a]));
  const outputRanges = {};
  const inputRanges = {};
  for (const col of parsed.columns) {
    const axis = dataset.parametricTags.has(col)
      ? byTag.get(col)
      : byTag.get(mappingsCsv.normalizeInAxisName(col));
    if (!axis) continue;
    const triple = { min: axis.min, default: axis.default, max: axis.max };
    if (dataset.parametricTags.has(col)) outputRanges[col] = triple;
    else inputRanges[col] = triple;
  }
  return lintAvar2Mappings(parsed, {
    parametricTags: [...dataset.parametricTags],
    outputRanges,
    inputRanges: Object.keys(inputRanges).length ? inputRanges : undefined,
    metadata: dataset.axisRanges || {},
  }).findings;
};

// The coverage audit for a dataset — structural gvar corners, behavioral
// sweeps (measure_at) and the mapping lint — over the master-covered
// axes only, so first load, session restore and every rebuild agree no
// matter which post-build stages (transforms, grade, control axes) the
// bytes carry.
const auditDataset = async (dataset) => {
  const axes = gvarSweepAxes(dataset.fontBytes, dataset.axes?.axes);
  return [
    ...auditCoverage(dataset.fontBytes, { tags: axes.map(a => a.tag) }).findings,
    ...await probeSweeps(dataset.fontBytes, axes, (b, g, l) => measureAt(b, g, l)),
    ...lintMappingsFindings(dataset),
  ];
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
  persistSoon(); // the CSV changed even when no avar2 regen is needed
  if (mappingsCsv.userColumns(dataset.instancesCsv, [...dataset.parametricTags]).length === 0) {
    return;
  }
  const ranges = Object.keys(dataset.axisRanges || {}).length
    ? JSON.stringify(dataset.axisRanges)
    : null;
  // Keep dataset.mappingsCsv in step with the edited rows: full
  // rebuilds (transform toggles, grade edits, REBUILD) regenerate the
  // avar2 store from it, and refreshAxesFromFont categorizes user axes
  // from its header. When it went stale, a newly added axis column
  // survived only until the next full rebuild silently dropped it.
  dataset.mappingsCsv = mappingsCsv.serializeMappingsCsv(dataset.instancesCsv);
  dataset.fontBytes = await addAvar2(
    dataset.fontBytes,
    dataset.mappingsCsv,
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
  // The mapping lint tracks the CSV, so refresh it on every regen;
  // the gvar/sweep findings are untouched (masters didn't move).
  dataset.coverage = [
    ...(dataset.coverage || []).filter(f => !f.type.startsWith('avar2-') && f.type !== 'unmapped-mapping-point'),
    ...lintMappingsFindings(dataset),
  ];
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
  dataset.coverage = await auditDataset(dataset);
  persistSoon();
};

// ---- guards -----------------------------------------------------------------

const unavailable = (what) => async () => {
  throw new Error(`${what} isn't available in the browser demo yet — use the desktop app.`);
};

const requireProject = () => {
  if (!uploadDataset) {
    throw new Error('No project is loaded — open an example or upload a source first.');
  }
};

// Health while nothing is loaded: the boot could not fetch the bundled
// example (or a reload after "Forget this project" failed). The App
// shows "No font loaded" and the Load Font menu still works.
const noProjectHealth = () => ({
  static: true, demo: false, building: false,
  font_built: false, font_loaded: false,
  glyphs_path: null, original_path: null, family_name: null,
  last_build_status: loadError ? 'error' : 'ok',
  last_build_error: loadError ? String(loadError.message || loadError) : null,
  avar2_error: null, build_stale: false,
});

// ---- config bundle import (uploaded sources only) ---------------------------
//
// Validation gates on the target font supplying the bundle's core axis
// data, mirroring config_port.validate_bundle: the parametric axes the
// avar2 CSV maps onto, and the source axes brace-layer locations
// reference. Apply is per-section: avar2 mappings, control axes, grade
// and the SPAC transforms are real (wasm add_avar2 /
// apply_control_axes / apply_grade / apply_transforms), and
// round_corners runs at SOURCE level (round_corners_source before the
// compile — see compileUploadSource / rebuildFromSource). Enabled
// transforms of a type the wasm port doesn't know are skipped with a
// warning, never silently dropped.

// Menu metadata for the built-in SPAC transforms, mirroring the studio's
// transform registry (transforms/builtin_spac*.py — the bundle carries
// only {type, enabled, params}; the Transforms menu also renders
// name/description/params_schema).
const KNOWN_TRANSFORMS = {
  round_corners: {
    id: 'round_corners',
    name: 'Round corners',
    description: 'Round every corner before the compile; the radius blends each layer\'s stroke weight (XOPQ) with its width (XTRA), outer corners and counters separately.',
    stage: 'source',
    injected_axis_tag: 'ROND',
    params_schema: [
      { key: 'default_pct', label: 'Default rounding %', type: 'float', default: 0.0, min: 0.0, max: 100.0 },
      { key: 'outer_pct', label: 'Outer, % of stroke', type: 'float', default: 15.0, min: 0.0, max: 60.0 },
      { key: 'inner_pct', label: 'Counters, % of stroke', type: 'float', default: 5.0, min: 0.0, max: 60.0 },
      { key: 'outer_xtra_pct', label: 'Outer, % of width', type: 'float', default: 3.0, min: 0.0, max: 60.0 },
      { key: 'inner_xtra_pct', label: 'Counters, % of width', type: 'float', default: 1.0, min: 0.0, max: 60.0 },
      { key: 'outer_min', label: 'Outer floor (units)', type: 'float', default: 2.0, min: 0.0, max: 100.0 },
      { key: 'inner_min', label: 'Counter floor (units)', type: 'float', default: 1.0, min: 0.0, max: 100.0 },
      { key: 'style_pcts', label: 'Per-style rounding', type: 'table', default: {} },
      { key: 'master_overrides', label: 'Per-master overrides', type: 'table', default: {} },
    ],
  },
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
      { key: 'min', label: 'Min', type: 'int', default: -40 },
      { key: 'max', label: 'Max', type: 'int', default: 40 },
      { key: 'bias', label: 'Wide bias', type: 'float', default: 1.0, min: 1.0, max: 4.0 },
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
  const controlTags = new Set(controlAxes.map(a => a.tag));
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
          // The bundle's own control tags are valid pins: a layer's
          // location carries its control value (its identity).
          if (!targetTags.has(tag) && !controlTags.has(tag)) {
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
  if (controlAxes.length) {
    const hasDrawn = controlAxes.some(a => (a.layers || []).some(isDrawnLayer));
    if (hasDrawn && dataset.health?.source_format === 'designspace') {
      warnings.push('Control axes: drawn outlines skipped (the browser can\'t compile UFO sources) — those layers apply as computed braces');
    } else if (!hasDrawn) {
      warnings.push('Control axes: applied as computed brace tuples (no drawn outlines in the bundle)');
    }
  }
  for (const t of transforms) {
    if (t.enabled && !KNOWN_TRANSFORMS[t.type]) {
      warnings.push(`Transform '${t.type}': unknown type — skipped by the static demo`);
    }
  }
  // Mapping lint on the bundle's CSV, with the ranges the import will
  // actually use: applyBundle passes no axis metadata to addAvar2, so
  // input defaults land on min (the CSV column's smallest value).
  if (!errors.length && avar2Csv.trim()) {
    const parsed = mappingsCsv.parseMappingsCsv(avar2Csv);
    const outputRanges = {};
    for (const a of dataset.axes.axes) {
      if (targetTags.has(a.tag)) outputRanges[a.tag] = { min: a.min, default: a.default, max: a.max };
    }
    for (const f of lintAvar2Mappings(parsed, {
      parametricTags: [...targetTags],
      outputRanges,
    }).findings) {
      warnings.push(f.detail);
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
  // Axis-metadata overrides (min/default/max/display name per user-axis
  // column). Without this section a non-min default silently reverts to
  // the CSV-derived min on reimport — inverting the mapping's neutral
  // plane. Optional: pre-metadata bundles simply lack it.
  ...(Object.keys(dataset.axisRanges || {}).length
    ? { axis_metadata: dataset.axisRanges }
    : {}),
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
  // Drawn outlines are spliced at SOURCE level (compile_with_overlays), so
  // a bundle carrying them needs a from-source rebuild instead of the
  // incremental bytes patches below — every section still sets dataset
  // state, and the rebuild applies it all in one pass. Without a .glyphs
  // source (designspace projects) drawn layers apply as computed braces,
  // as the desktop's designspace shadow does.
  const rebuildFromSource = dataset.sourceText != null &&
    (controlAxes.some(a => (a.layers || []).some(isDrawnLayer))
      // round_corners runs on the SOURCE: a bundle enabling it needs the
      // full source rebuild, not the incremental bytes stages.
      || transforms.some(t => t.enabled && (t.type || t.id) === 'round_corners'));

  // Axis metadata rides the bundle (optional section): adopt it BEFORE
  // the mappings apply so the declared defaults/ranges shape the fvar,
  // and so later rebuilds keep using them.
  if (bundle.axis_metadata && typeof bundle.axis_metadata === 'object') {
    dataset.axisRanges = bundle.axis_metadata;
    report.applied.push('axis metadata');
  }
  if (avar2Csv.trim()) {
    const ranges = Object.keys(dataset.axisRanges || {}).length
      ? JSON.stringify(dataset.axisRanges)
      : null;
    if (!rebuildFromSource) {
      dataset.fontBytes = await addAvar2(dataset.fontBytes, avar2Csv, ranges, [...dataset.parametricTags]);
    }
    dataset.mappingsCsv = avar2Csv;
    // The bundle's CSV becomes the authoring source of truth.
    dataset.instancesCsv = mappingsCsv.parseMappingsCsv(avar2Csv);
    syncInstancesFromCsv(dataset);
    report.applied.push('avar2 mappings');
  }
  if (controlAxes.length) {
    dataset.controlAxes = controlAxes;
    if (!rebuildFromSource) {
      // Pure computed sidecar: bytes-level application, as before. Drawn
      // layers in the mix (designspace case) fall back to computed braces —
      // the outline is stripped for the wasm so it still gets a tuple.
      const forBytes = controlAxes.map(a => ({
        ...a,
        layers: (a.layers || []).map(l => {
          if (!isDrawnLayer(l)) return l;
          const stripped = { ...l };
          delete stripped.outline;
          return stripped;
        }),
      }));
      dataset.fontBytes = await applyControlAxes(dataset.fontBytes, JSON.stringify(controlAxesForBuild(forBytes)));
    }
    report.applied.push('control axes');
  }
  // The grade DECLARATION is retained even when it changes nothing in
  // the font (toggle off, or no graded instances): per-instance grades
  // persist across the toggle — the server's save_all semantics.
  if (bundle.grade) dataset.grade = grade;
  if (gradeInstances.length) {
    if (!rebuildFromSource) {
      const coords = resolveGradeCoords(dataset, avar2Csv);
      dataset.fontBytes = await applyGrade(
        dataset.fontBytes, JSON.stringify(grade), JSON.stringify(coords)
      );
    }
    report.applied.push('grade');
  }
  // SPAC transforms apply last (they rebuild HVAR from the gvar the
  // earlier sections produced). Disabled and unknown-type entries never
  // reach the font — the wasm side applies only enabled known ones.
  if (transforms.some(t => t.enabled && KNOWN_TRANSFORMS[t.type])) {
    if (!rebuildFromSource) {
      dataset.fontBytes = await applyTransforms(dataset.fontBytes, JSON.stringify(transforms), avar2Csv);
    }
    report.applied.push('transforms (SPAC)');
  }
  // Corner pins: replace the set and re-apply onto the font the
  // earlier sections produced.
  const cornerPins = bundle.corner_pins?.pins || [];
  if (cornerPins.length) {
    dataset.cornerPins = cornerPins;
    if (!rebuildFromSource) await applyPins(dataset);
    report.applied.push('corner pins');
  }
  // The Transforms menu reflects the bundle's set from now on: enabled
  // entries show enabled, the rest available but off.
  dataset.transforms = transforms.map(t => ({
    ...(KNOWN_TRANSFORMS[t.type] || { id: t.type, name: t.type }),
    enabled: !!t.enabled,
    params: t.params || {},
  }));
  if (rebuildFromSource) {
    await rebuildUploadFont(dataset);
  }
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
  const meta = parseFont(dataset.fontBytes);
  // Use display_name from axisRanges (user-defined metadata) when
  // available; the font's name table often lacks entries for avar2 axes.
  const axisRanges = dataset.axisRanges || {};
  dataset.axes = {
    axes: meta.axes.map(a => {
      const rangeOverride = axisRanges[a.tag] || {};
      const hasCoverage = !userTags.has(a.tag) && !controlTags.has(a.tag) && a.tag !== 'GRAD';
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
    .map(t => ({ id: t.type || t.id, name: t.name || t.type || t.id, enabled: !!t.enabled, params: { ...(t.params || {}) } }));
  return [...known, ...unknown];
};

// ---- control axes: split build (drawn at source level, computed on bytes) --
//
// DESIGN DECISION (Phase 3): a control-axis layer can be COMPUTED (plain:
// seeded from the masters at its location; or a correction: seeded from its
// `target`) or DRAWN (a hand-edited outline stored on the sidecar entry).
// Computed outlines are instancer math on font bytes (braces.rs); drawn
// outlines are source-level cubic nodes that only the compiler can turn into
// gvar (the font's quadratic point numbering doesn't line up with them).
//
// So the build SPLITS the sidecar:
//   - axes with ≥1 drawn layer go through compile_with_overlays (editor.rs):
//     ALL sidecar control axes are declared on the source (axis entries,
//     masters extended, Virtual Master pins — the same shapes the desktop's
//     regenerate_shadow writes) and each drawn layer is spliced as a real
//     brace layer carrying its stored outline. fontc then emits desktop-
//     identical gvar for them (the Phase 0 oracle proves the splice).
//   - apply_control_axes (braces.rs) then tolerates the already-declared
//     axes and adds computed tuples for the remaining layers only —
//     outline-bearing layers are skipped, so an axis is never
//     double-declared and a drawn layer never gets a second tuple.
// Declaring all axes at compile time whenever any drawn overlay exists also
// lets a drawn overlay's location pin a SIBLING control axis (the braces
// land in the same N-D space the desktop writes).
//
// For correction layers the two paths agree by construction (braces.rs
// pins the parametric peak, like varLib on the shadow); for plain layers
// the bytes-level tent peaks only at the control extreme where the desktop
// peaks at the full N-D location — a pre-existing static-demo
// approximation, unchanged by this split.

// A stored outline is drawn only when the layer is not a correction:
// regenerate_shadow ignores the outline of target-bearing layers.
const isDrawnLayer = (l) => !!l.outline && !(l.target && Object.keys(l.target).length);

// The control-axes JSON for the wasm: corrections never carry their
// (ignored) stored outline, so braces.rs's drawn-skip test (outline
// present) lines up exactly with the source-level drawn set.
const controlAxesForBuild = (axes) =>
  (axes || []).map(a => ({
    ...a,
    layers: (a.layers || []).map(l => {
      if (l.outline && l.target && Object.keys(l.target).length) {
        const stripped = { ...l };
        delete stripped.outline;
        return stripped;
      }
      return l;
    }),
  }));

// The round_corners request for a dataset: the enabled transform's
// params plus the control sidecar (correction targets), or null. The
// wasm engine runs it on the source before the compile — the oracle
// test proves it node-identical to the desktop engine.
const roundOption = (dataset) => {
  const t = (dataset.transforms || []).find(x => (x.type || x.id) === 'round_corners');
  if (!t || !t.enabled) return null;
  return {
    params: JSON.stringify(t.params || {}),
    control: (dataset.controlAxes || []).length
      ? JSON.stringify({ axes: dataset.controlAxes })
      : null,
  };
};

const compileUploadSource = (dataset) => {
  const round = roundOption(dataset);
  const drawnAxes = (dataset.controlAxes || [])
    .map(a => ({ ...a, drawn: (a.layers || []).filter(isDrawnLayer) }))
    .filter(a => a.drawn.length);
  if (!drawnAxes.length) return compileFont(dataset.sourceText, round);
  return compileWithOverlays(dataset.sourceText, {
    axes: (dataset.controlAxes || []).map(a => ({
      tag: a.tag, name: a.name, min: a.min, default: a.default, max: a.max,
    })),
    overlays: drawnAxes.flatMap(a =>
      a.drawn.map(l => ({ glyph: l.glyph, location: l.location, outline: l.outline }))),
  }, round);
};

// The full rebuild pipeline for an uploaded .glyphs source: compile,
// then re-apply the studio state in bundle-import order — avar2
// mappings, control axes, grade, SPAC transforms, corner pins — and
// finally the out-of-range drop. Each stage's HVAR rebuild reads the
// current gvar, so the final font carries every stage's advances.
// Shared by buildFont and updateTransforms (the only way to change the
// transform set — applied transforms can't be un-baked).
const rebuildUploadFont = async (dataset) => {
  if (dataset.sourceText == null) {
    throw new Error("This needs the full app — the browser can't compile UFO sources yet.");
  }
  let ttf = await compileUploadSource(dataset);
  // avar2 regen needs authored rows AND a user (non-parametric) column —
  // a restored CSV-less project carries a synthesized header-only CSV
  // (add_avar2 rejects it), and a parametric-only CSV is an identity
  // mapping (regenerateFont skips those for the same reason).
  if (dataset.mappingsCsv
      && dataset.instancesCsv?.rows?.length
      && mappingsCsv.userColumns(dataset.instancesCsv, [...dataset.parametricTags]).length) {
    const ranges = Object.keys(dataset.axisRanges || {}).length
      ? JSON.stringify(dataset.axisRanges)
      : null;
    ttf = await addAvar2(ttf, dataset.mappingsCsv, ranges, [...dataset.parametricTags]);
  }
  dataset.fontBytes = ttf;
  if ((dataset.controlAxes || []).length) {
    dataset.fontBytes = await applyControlAxes(dataset.fontBytes, JSON.stringify(controlAxesForBuild(dataset.controlAxes)));
  }
  const grade = dataset.grade || {};
  // The GRAD axis materialises only when enabled AND ≥1 instance is
  // graded (the wasm no-ops otherwise) — with no grades there are no
  // brace tuples and the axis would be inert, matching the server.
  if (grade.enabled) {
    const coords = resolveGradeCoords(dataset, dataset.mappingsCsv || '');
    dataset.fontBytes = await applyGrade(
      dataset.fontBytes, JSON.stringify(grade), JSON.stringify(coords)
    );
  }
  const transforms = (dataset.transforms || [])
    .map(t => ({ type: t.type || t.id, enabled: !!t.enabled, params: t.params || {} }));
  if (transforms.some(t => t.enabled && KNOWN_TRANSFORMS[t.type])) {
    dataset.fontBytes = await applyTransforms(
      dataset.fontBytes, JSON.stringify(transforms), dataset.mappingsCsv || ''
    );
  }
  await applyPins(dataset);
  if (dataset.clampOutOfRange) {
    dataset.fontBytes = await clampOutOfRangeWasm(dataset.fontBytes);
  }
  refreshAxesFromFont(dataset);
};

// After any rebuild-from-source: swap the object URL, stamp the build
// (the App re-reads fontUrl only when last_build_time changes) and
// persist the session — awaited, not debounced: a rebuild already took
// seconds, and a reload inside the debounce window would lose the edit.
const commitRebuiltFont = async (dataset) => {
  URL.revokeObjectURL(dataset.fontUrl);
  dataset.fontUrl = URL.createObjectURL(new Blob([dataset.fontBytes], { type: 'font/ttf' }));
  dataset.coverage = await auditDataset(dataset); // the font changed
  await persistSession();
  // Stamped after the write, so "the build time advanced" also means
  // "the session holds this build".
  dataset.health.last_build_time = Date.now();
};

// ---- glyph editor (fontra-embed bridge) -----------------------------------
//
// The studio-side half of the embedded editor (see editor-bridge.js for the
// message layer). openControlAxisInEditor creates a bridge for the session
// and returns the embed URL; the FontraEditorModal iframes it and attaches
// the bridge. Edits flow back per editFinal: the bridge guards and applies
// them to its model, hands the edited layer's outline here for the sidecar,
// and a coalesced rebuild (one in flight, one pending — trigger_build's
// spirit) recompiles the font so the preview follows.

let currentEditorBridge = null;
let editorRebuildRunning = false;
let editorRebuildPending = false;
let editorRebuildPromise = null;

const scheduleEditorRebuild = () => {
  if (!uploadDataset) return Promise.resolve();
  if (editorRebuildRunning) {
    editorRebuildPending = true;
    return editorRebuildPromise;
  }
  editorRebuildRunning = true;
  editorRebuildPromise = (async () => {
    try {
      do {
        editorRebuildPending = false;
        await rebuildUploadFont(uploadDataset);
        await commitRebuiltFont(uploadDataset);
      } while (editorRebuildPending);
    } catch (err) {
      currentEditorBridge?.onError?.(
        `The edit was saved to the layer, but the font rebuild failed: ${err.message || err}`);
    } finally {
      editorRebuildRunning = false;
    }
  })();
  return editorRebuildPromise;
};

const storeEditedOutline = (tag, glyph, entryLocation, outline) => {
  const ax = (uploadDataset?.controlAxes || []).find(a => a.tag === tag);
  if (!ax) return;
  // Re-find the entry in the CURRENT sidecar state — the sidebar's layer
  // edits replace ax.layers, so the session's held reference can be stale.
  const keyOf = (loc) => JSON.stringify(
    Object.entries(loc || {}).map(([k, v]) => [k, Number(v)]).sort());
  const entry = (ax.layers || []).find(l =>
    l.glyph === glyph && keyOf(l.location) === keyOf(entryLocation));
  if (!entry) return; // the layer was removed mid-session: the edit has no home
  entry.outline = outline;
  scheduleEditorRebuild();
};

// ---- two-tab session guard --------------------------------------------------
//
// The workspace persists to one IndexedDB record per origin, but storage
// events don't fire for IndexedDB writes — so two tabs on the same static
// deployment would silently clobber each other's session. A BroadcastChannel
// claim/heartbeat lock instead: the first tab holds the lock; another tab
// opening the studio is told it's the loser (the App shows a banner) until
// the holder's heartbeats stop (tab closed), at which point it takes over.
// Advisory only — the losing tab is not blocked from editing.

let sessionLockLost = false;
const sessionLockTabId = (crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`);

const startSessionLock = () => {
  if (typeof BroadcastChannel === 'undefined') return;
  const channel = new BroadcastChannel('avar2-studio-session-lock');
  let holderAlive = false;
  let lastHolderBeat = 0;
  let claiming = false;

  const claim = () => {
    claiming = true;
    holderAlive = false;
    channel.postMessage({ kind: 'claim', tabId: sessionLockTabId });
    setTimeout(() => {
      claiming = false;
      if (!holderAlive) sessionLockLost = false; // no holder answered: take it
    }, 300);
  };
  channel.onmessage = (e) => {
    const m = e.data || {};
    if (m.tabId === sessionLockTabId) return;
    if (m.kind === 'claim') {
      if (sessionLockLost || claiming) return; // losers and rival claimants stay out
      channel.postMessage({ kind: 'held', tabId: sessionLockTabId });
    } else if (m.kind === 'held' || m.kind === 'heartbeat') {
      holderAlive = true;
      lastHolderBeat = Date.now();
      if (claiming) {
        sessionLockLost = true; // a holder answered our claim: we lose
      } else if (!sessionLockLost && m.tabId < sessionLockTabId) {
        // Two holders (simultaneous open): the higher tabId yields.
        sessionLockLost = true;
      }
    }
  };
  claim();
  setInterval(() => {
    if (sessionLockLost) {
      // Holder gone for a while? Re-claim (takeover after its tab closed).
      if (Date.now() - lastHolderBeat > 6000) claim();
    } else {
      channel.postMessage({ kind: 'heartbeat', tabId: sessionLockTabId });
    }
  }, 2000);
};

// ---- projects: uploads and bundled examples ---------------------------------
//
// Both arrive as a workspace (zip-workspace.js): the source, its sidecars
// and — for .designspace projects — the pristine build the browser can't
// produce itself. An example is the same thing fetched from the static
// site, with its pristine base.ttf passed in so the first paint skips
// the compile. Either way the result is a live dataset that persists.

const loadWorkspace = async (ws, { fontBytes = null, origin = 'upload', exampleId = null } = {}) => {
  uploadDataset = await buildUploadDataset({
    sourceName: ws.sourceName,
    sourceFormat: ws.sourceExt,
    sourceText: ws.sourceText,
    fontBytes: fontBytes ?? ws.previewTtf,
    csvText: ws.csvText,
    metadataText: ws.metadataText,
    workspaceEntries: ws.entries,
    sourceDir: ws.sourceDir,
    origin,
    exampleId,
  });
  // Harvested control axes / transforms / grade apply through the bundle
  // machinery (same wasm steps, same warnings); the CSV was already
  // applied by the dataset build above. Corner pins apply after.
  if (ws.controlText || ws.transformsText || ws.gradeText) {
    await applyBundle({
      format: 'avar2-studio-config', format_version: 1,
      source: { avar2_out_columns: [...uploadDataset.parametricTags] },
      control_axes: ws.controlText ? JSON.parse(ws.controlText) : { version: 1, axes: [] },
      avar2_csv: '',
      transforms: ws.transformsText ? JSON.parse(ws.transformsText) : { version: 1, transforms: [] },
      grade: ws.gradeText
        ? JSON.parse(ws.gradeText)
        : { version: 1, enabled: false, default_pct: 0.25, instances: [] },
    }, uploadDataset);
  }
  if (ws.cornerPinsText) {
    uploadDataset.cornerPins = JSON.parse(ws.cornerPinsText).pins || [];
    await applyPins(uploadDataset);
  }
  await persistSession();
};

const loadExampleProject = async (id) => {
  const ex = ((await examplesIndex()).examples || []).find(e => e.id === id);
  if (!ex) throw new Error(`Unknown example: ${id}`);
  const [zip, base] = await Promise.all([
    fetchBytes(`${DATA}/${id}/project.zip`),
    ex.base_ttf ? fetchBytes(`${DATA}/${id}/${ex.base_ttf}`) : Promise.resolve(null),
  ]);
  await loadWorkspace(readWorkspaceZip(zip), { fontBytes: base, origin: 'example', exampleId: id });
  loadError = null;
};

const staticOverrides = {
  health: async () => (uploadDataset
    ? { ...uploadDataset.health, session_lock_lost: sessionLockLost }
    : { ...noProjectHealth(), session_lock_lost: sessionLockLost }),
  glyphsFileStatus: async () => ({ has_unsaved_changes: false }),
  getInstances: async () => (uploadDataset ? uploadDataset.instances : { instances: [] }),
  // STUB on purpose: the one consumer (the removed Rounding tab) is
  // gone, and implementing this via glyph_model turned out to PERTURB
  // later measure_at results in the shared wasm instance (pin synthesis
  // stopped triggering) — see HANDOVER §4 before resurrecting it.
  getMasters: async () => ({ masters: [] }),
  getAxes: async () => (uploadDataset ? uploadDataset.axes : { axes: [] }),
  getAvar2Instances: async () => {
    if (!uploadDataset) return { instances: [] };
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
    if (!uploadDataset) return { traditional_axes: { columns: [] }, metadata: {}, parametric_axes: [] };
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
  getTransforms: async () => ({ transforms: transformsMenu(uploadDataset || { transforms: [] }) }),
  getCoverage: async () => ({
    findings: uploadDataset ? uploadDataset.coverage || [] : [],
    // Fresh array every call: dataset.cornerPins is mutated in place
    // by pinCorner, and the same reference through getCoverage left
    // React's Object.is state check blind to the update.
    pins: [...(uploadDataset ? uploadDataset.cornerPins || [] : [])],
  }),
  getGrade: async () => {
    if (!uploadDataset) return { enabled: false, default_pct: 0.25, instances: [], max_pct: {}, diagnostics: [] };
    const grade = uploadDataset.grade || { enabled: false, default_pct: 0.25, instances: [] };
    // Per-instance slider caps, the server's _grade_state_payload
    // semantics: bound each instance's grade% by its own parametric
    // headroom so an undeliverable grade is unreachable in the UI.
    const ranges = {};
    for (const a of parseFont(uploadDataset.fontBytes).axes) {
      if (PARAM_TAGS.includes(a.tag)) ranges[a.tag] = [a.min, a.max];
    }
    const coords = resolveGradeCoords(uploadDataset, uploadDataset.mappingsCsv || '');
    const max_pct = {};
    if (Object.keys(ranges).length) {
      for (const name of Object.keys(coords)) max_pct[name] = maxPctFor(coords[name], ranges);
    }
    // Same warnings the server reports, so the hosted demo flags an
    // undeliverable grade exactly as the local studio does.
    return { ...grade, max_pct, diagnostics: gradeDiagnostics(grade, coords, ranges) };
  },
  listControlAxes: async () => ({ axes: uploadDataset?.controlAxes || [] }),
  getGlyphCoverage: async () => {
    if (!uploadDataset) return { axes: [], glyph_chars: {} };
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
          ...(l.target && Object.keys(l.target).length ? { target: l.target } : {}),
          // The drawn badge (and the reseed flow's confirm) read this.
          ...(isDrawnLayer(l) ? { has_outline: true } : {}),
        })),
      };
    });
    return { axes, glyph_chars: {} };
  },
  listExamples: examplesIndex,
  checkSyncStatus: async () => ({ synced: true, message: 'Browser workspace' }),
  getFontUrl: () => (uploadDataset ? uploadDataset.fontUrl : null),
  getAvar2FontUrl: () => (uploadDataset ? uploadDataset.fontUrl : null),
  exportConfigUrl: () => {
    requireProject();
    const bundle = buildConfigBundle(uploadDataset);
    return URL.createObjectURL(
      new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })
    );
  },
  // Whole project as one zip (sources + studio sidecars + preview build)
  // — loads back here or in the full app.
  exportWorkspaceUrl: () => {
    requireProject();
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
      await loadWorkspace(readWorkspaceZip(new Uint8Array(await zipFile.arrayBuffer())));
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
    await persistSession();
    return { ok: true, ignored_files: ignored };
  },

  // Rebuild: the full pipeline re-runs on the .glyphs source (compile →
  // avar2 → control axes → grade → transforms → pins → out-of-range
  // drop) — the rebuilt fontBytes stay the dataset's truth.
  buildFont: async () => {
    requireProject();
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
    await persistSession();
    return { ok: true };
  },

  // Load Font → Examples: the bundled project loads as a live workspace
  // (pristine copy, replacing whatever was loaded — the stored session
  // follows it). App's loadData() re-reads the new health (different
  // glyphs_path) and treats it as a source swap.
  loadExample: async (id) => {
    await loadExampleProject(id);
    return { ok: true };
  },

  // "Forget this project": drop the stored session and reload the
  // pristine default example (which persists in its turn, so a reload
  // comes back to the untouched example, never to the forgotten work).
  forgetSession: async () => {
    await clearSession().catch(() => {});
    uploadDataset = null;
    await loadExampleProject(await defaultExampleId());
    return { ok: true };
  },

  // Transforms toggles and parameter edits: applied transforms can't be
  // un-baked, so the font rebuilds from source with the new set applied.
  // Enabled/params merge OVER the known list so name/description/schema
  // metadata survives (the App renders the menu from our return value).
  updateTransforms: async (entries) => {
    requireProject();
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
    await commitRebuiltFont(uploadDataset);
    return { transforms: transformsMenu(uploadDataset) };
  },

  // The parametric-slider reflection: a project with a mappings CSV gets
  // a real client-side avar2 evaluation (avar2-eval.js); without one the
  // input coordinates pass through.
  // "Add row" on an unmapped-mapping-point finding: create the missing
  // grid row, outputs pre-filled with the surface's CURRENT value at
  // that location (the built font's own avar2 evaluation) — so adding
  // it is a behavior-pinning no-op the designer then edits into shape.
  addMappingRow: async (location) => {
    requireProject();
    // Finding locations are keyed by CSV column name (WGHT/OPSZ…);
    // the avar2 evaluator wants the fvar tags (wght/opsz…).
    const fvarLoc = Object.fromEntries(
      Object.entries(location).map(([k, v]) => [mappingsCsv.normalizeInAxisName(k), v])
    );
    let mapped = {};
    try {
      mapped = mappedLocation(uploadDataset.fontBytes, uploadDataset.axes.axes, fvarLoc) || {};
    } catch {
      // No avar2 table / parse failure — outputs stay blank (= defaults).
    }
    const coords = { ...location };
    for (const t of uploadDataset.parametricTags) {
      if (mapped[t] !== undefined) coords[t] = Math.round(mapped[t] * 10) / 10;
    }
    const name = Object.entries(location).map(([k, v]) => `${k} ${v}`).join(' ');
    mappingsCsv.upsertRow(uploadDataset.instancesCsv, name, coords);
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return { name };
  },
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

  // The instance lifecycle is real on every project: the CSV is the
  // source of truth, mutations regenerate the avar2 store.
  createInstance: async (instanceName, coordinates, insertAfter = null) => {
    requireProject();
    mappingsCsv.upsertRow(uploadDataset.instancesCsv, instanceName, coordinates, insertAfter);
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  updateInstance: async (instanceName, coordinates) => {
    requireProject();
    mappingsCsv.upsertRow(uploadDataset.instancesCsv, instanceName, coordinates);
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  renameInstance: async (instanceName, newName) => {
    requireProject();
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
    requireProject();
    mappingsCsv.deleteRow(uploadDataset.instancesCsv, instanceName);
    // A deleted instance takes its grade with it (server semantics for
    // a full delete; demote keeps the instance, so the grade stays).
    if (uploadDataset.grade?.instances?.length) {
      uploadDataset.grade.instances =
        uploadDataset.grade.instances.filter(e => e.name !== instanceName);
    }
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  addInstanceToSource: unavailable('Saving to source'),
  addAvar2Axis: async (axisData) => {
    requireProject();
    // AddAxisModal payload: {axis_name (uppercase CSV column),
    // display_name, registered_tag, default_value, min, max, scaffold}.
    const column = axisData.axis_name;
    // Snapshot BEFORE addColumn stamps the default into every row.
    const baseRows = uploadDataset.instancesCsv.rows.map(r => ({
      name: r.name, values: { ...r.values },
    }));
    mappingsCsv.addColumn(uploadDataset.instancesCsv, column, axisData.default_value);
    uploadDataset.axisRanges[column] = {
      display_name: axisData.display_name,
      registered_tag: axisData.registered_tag,
      min: axisData.min,
      default: axisData.default_value,
      max: axisData.max,
      is_parametric: false,
    };
    // Scaffold: duplicate every existing row at each non-default extreme
    // of the new axis, outputs copied. An avar2 tent never crosses an
    // axis default, so without rows on the far plane(s) the new axis
    // starts with dead zones (the "dead cross"); with them the space is
    // fully mapped from the first build — the axis is simply inert until
    // the designer edits the duplicated values.
    if (axisData.scaffold !== false && baseRows.length) {
      const extremes = [axisData.min, axisData.max]
        .filter(v => v !== axisData.default_value);
      const taken = new Set(uploadDataset.instancesCsv.rows.map(r => r.name));
      for (const extreme of extremes) {
        for (const row of baseRows) {
          const name = `${row.name} ${column} ${extreme}`;
          if (taken.has(name)) continue;
          taken.add(name);
          mappingsCsv.upsertRow(uploadDataset.instancesCsv, name, {
            ...row.values, [column]: extreme,
          });
        }
      }
    }
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  updateAvar2Axis: async (axisName, axisData) => {
    requireProject();
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
    requireProject();
    mappingsCsv.removeColumn(uploadDataset.instancesCsv, axisName);
    delete uploadDataset.axisRanges[axisName];
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  updateAvar2Mapping: async (instanceName, axisName, value) => {
    requireProject();
    mappingsCsv.upsertRow(uploadDataset.instancesCsv, instanceName, { [axisName]: value });
    syncInstancesFromCsv(uploadDataset);
    await regenerateFont(uploadDataset);
    return {};
  },
  setGrade: async (patch) => {
    requireProject();
    uploadDataset.grade = { ...(uploadDataset.grade || {}), ...patch };
    await rebuildUploadFont(uploadDataset);
    await commitRebuiltFont(uploadDataset);
    return uploadDataset.grade;
  },
  setInstanceGrade: async (instanceName, pct) => {
    requireProject();
    const grade = uploadDataset.grade ||= { version: 1, enabled: false, default_pct: 0.25, instances: [] };
    grade.instances = (grade.instances || []).filter(e => e.name !== instanceName);
    // pct omitted → seed with the global default (the server's
    // set_instance_grade(pct=None) semantics; removal is its own call).
    grade.instances.push({ name: instanceName, pct: pct ?? grade.default_pct ?? 0.25 });
    await rebuildUploadFont(uploadDataset);
    await commitRebuiltFont(uploadDataset);
    return grade;
  },
  removeInstanceGrade: async (instanceName) => {
    requireProject();
    const grade = uploadDataset.grade;
    if (grade) {
      grade.instances = (grade.instances || []).filter(e => e.name !== instanceName);
    }
    await rebuildUploadFont(uploadDataset);
    await commitRebuiltFont(uploadDataset);
    return grade;
  },
  // Control axes (secondary parametric axes) on uploads: declarations
  // live in dataset.controlAxes using the sidecar shape ({tag, name,
  // min, default, max, layers: [{glyph, location}]}); every mutation
  // rebuilds from source through the shared pipeline — the wasm
  // computes the brace tuples (drawn outlines stay a full-app feature).
  createControlAxis: async (axis) => {    requireProject();
    // Modal payload: {tag, display_name, default, min, max}.
    (uploadDataset.controlAxes ||= []).push({
      tag: axis.tag,
      name: axis.display_name || axis.tag,
      min: axis.min, default: axis.default, max: axis.max,
      layers: [],
    });
    await rebuildUploadFont(uploadDataset);
    await commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  updateControlAxis: async (tag, updates) => {
    requireProject();
    const ax = (uploadDataset.controlAxes || []).find(a => a.tag === tag);
    if (!ax) throw new Error(`No control axis '${tag}'`);
    if (updates.display_name !== undefined) ax.name = updates.display_name;
    for (const k of ['min', 'default', 'max']) {
      if (updates[k] !== undefined) ax[k] = updates[k];
    }
    await rebuildUploadFont(uploadDataset);
    await commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  deleteControlAxis: async (tag) => {
    requireProject();
    uploadDataset.controlAxes = (uploadDataset.controlAxes || []).filter(a => a.tag !== tag);
    await rebuildUploadFont(uploadDataset);
    await commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  // Layer locations from the UI pin every axis (including control,
  // grade and transform-injected ones at their defaults). Keep
  // parametric axes, CSV user columns and CONTROL-axis pins (the layer's
  // own control value is its identity — the wasm skips control pins when
  // instancing, and the source-level splice needs them); drop the rest
  // (GRAD/SPAC), which the wasm would reject.
  controlAxisLayerDelta: async (tag, delta) => {
    requireProject();
    const ax = (uploadDataset.controlAxes || []).find(a => a.tag === tag);
    if (!ax) throw new Error(`No control axis '${tag}'`);
    ax.layers ||= [];
    const allowed = new Set([...uploadDataset.parametricTags]);
    for (const t of csvHeaderTags(uploadDataset.mappingsCsv || '')) allowed.add(t);
    for (const a of uploadDataset.controlAxes || []) allowed.add(a.tag);
    const clean = (l) => {
      const out = {
        glyph: l.glyph,
        location: Object.fromEntries(Object.entries(l.location || {}).filter(([k]) => allowed.has(k))),
      };
      // Correction target (render "as if at" another parametric point):
      // kept so a bundle round-trips it to the full app. The static
      // wasm build ignores it for now (computed braces there are
      // deltas from the default master).
      const target = Object.fromEntries(Object.entries(l.target || {}).filter(([k]) => allowed.has(k)));
      if (Object.keys(target).length) out.target = target;
      // A stored drawing (from the editor bridge or an imported
      // bundle) round-trips untouched.
      if (l.outline && typeof l.outline === 'object') {
        out.outline = l.outline;
        if (l.source_sig) out.source_sig = l.source_sig;
      }
      return out;
    };
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
    await commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  setControlAxisLayers: async (tag, layers) => {
    requireProject();
    const ax = (uploadDataset.controlAxes || []).find(a => a.tag === tag);
    if (!ax) throw new Error(`No control axis '${tag}'`);
    const allowed = new Set([...uploadDataset.parametricTags]);
    for (const t of csvHeaderTags(uploadDataset.mappingsCsv || '')) allowed.add(t);
    for (const a of uploadDataset.controlAxes || []) allowed.add(a.tag);
    const sameLayer = (a, b) =>
      a.glyph === b.glyph &&
      JSON.stringify(Object.entries(a.location || {}).sort()) ===
        JSON.stringify(Object.entries(b.location || {}).sort());
    ax.layers = (layers || []).map(l => {
      const out = {
        glyph: l.glyph,
        location: Object.fromEntries(Object.entries(l.location || {}).filter(([k]) => allowed.has(k))),
      };
      const target = Object.fromEntries(Object.entries(l.target || {}).filter(([k]) => allowed.has(k)));
      if (Object.keys(target).length) out.target = target;
      // The UI's layer rows don't carry drawings: keep the existing
      // entry's outline when the row still identifies it (a whole-list
      // replace must not wipe drawings the caller never saw).
      if (l.outline && typeof l.outline === 'object') {
        out.outline = l.outline;
        if (l.source_sig) out.source_sig = l.source_sig;
      } else {
        const prev = (ax.layers || []).find(p => sameLayer(p, out));
        if (prev?.outline) {
          out.outline = prev.outline;
          if (prev.source_sig) out.source_sig = prev.source_sig;
        }
      }
      return out;
    });
    await rebuildUploadFont(uploadDataset);
    await commitRebuiltFont(uploadDataset);
    return { ok: true };
  },
  // Re-seeding rewrites the sidecar and re-derives the shadow, which the
  // static demo has no writable source for.
  reseedControlAxisLayers: unavailable('Re-seeding a layer from source'),
  // The glyph editor: the fontra-embed bundle (a static build of Fontra
  // with the font backend proxied over postMessage) iframed in the same
  // drawer the desktop uses. The bridge (editor-bridge.js) serves the
  // editor's RPC from the in-memory source model and writes accepted
  // edits back into the control-axis sidecar, then a coalesced rebuild
  // recompiles the font.
  openControlAxisInEditor: async (tag, glyphName, layerLocation, studioAxes) => {
    requireProject();
    if (uploadDataset.sourceText == null) {
      throw new Error("The glyph editor needs the project's .glyphs source — .designspace projects are read-only in the browser.");
    }
    const ax = (uploadDataset.controlAxes || []).find(a => a.tag === tag);
    if (!ax) throw new Error(`No control axis '${tag}'`);
    if (!glyphName) throw new Error('Pick a brace layer to edit first (the ↗ button on a layer row).');
    const url = embedEditorUrl();
    try {
      // Fast, honest failure when nothing serves the bundle (no-cors: an
      // opaque answer still means a server is there).
      await fetch(url, { mode: 'no-cors', signal: AbortSignal.timeout(3000) });
    } catch {
      throw new Error(
        `The glyph editor isn't reachable at ${url} — if you're developing, serve the fontra-embed bundle ` +
        `(in the fontra-embed checkout: npm run build, then npx serve dist -l 8099); ` +
        `the production demo expects it at agyeiagyeiagyei.github.io/fontra-embed/.`);
    }
    currentEditorBridge?.dispose();
    currentEditorBridge = await createEditorBridge({
      dataset: uploadDataset,
      tag,
      glyphName,
      layerLocation: layerLocation || null,
      studioAxes: studioAxes || uploadDataset.axes.axes,
      onOutlineEdited: (entry, outline) =>
        storeEditedOutline(tag, entry.glyph, entry.location, outline),
    });
    // Debug/e2e introspection, same spirit as window.__avar2api.
    if (typeof window !== 'undefined') window.__avar2EditorBridge = currentEditorBridge;
    return { url, direct_url: url, editing_original: false, bridge: currentEditorBridge };
  },
  // Drawer close: wait out the coalesced rebuild so the preview the user
  // returns to already shows the last edit.
  flushEditorRebuilds: async () => {
    if (editorRebuildPromise) await editorRebuildPromise.catch(() => {});
    currentEditorBridge = null;
    if (typeof window !== 'undefined') window.__avar2EditorBridge = null;
  },
  exportFont: async (options) => {
    const { hidden_axes = [], default_location } = options || {};
    requireProject();
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
    requireProject();
    const report = validateBundle(bundle, uploadDataset);
    if (dryRun) return report;
    if (!report.ok) {
      const err = new Error(report.errors[0] || 'Invalid bundle');
      err.report = report;
      throw err;
    }
    const applied = await applyBundle(bundle, uploadDataset);
    uploadDataset.coverage = await auditDataset(uploadDataset);
    persistSoon();
    return applied;
  },

  // Pin a ghost corner: scaffold from the measured healthy edge, hold
  // the corner up (model-computed tuple), then regenerate the stack
  // (avar2 from the CSV if present, STAT always) and re-audit.
  pinCorner: async (corner) => {
    requireProject();
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
    requireProject();
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
  startSessionLock();
  // Debug/introspection hook for the static provider (used by e2e and
  // manual diagnosis).
  if (typeof window !== 'undefined') window.__avar2api = api;
  // Auto-restore the stored session before the app renders — health()
  // then answers for that project and the app boots into it. With no
  // session, boot into the default bundled example.
  if (!(await restoreSession())) {
    try {
      await loadExampleProject(await defaultExampleId());
    } catch (err) {
      loadError = err;
      console.warn('The bundled example could not be loaded:', err);
    }
  }
  return true;
}
