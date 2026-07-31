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
 *   - Load Font: switch between the snapshotted examples (uploads need
 *     a build → disabled)
 *   - Transforms SPAC toggle: swaps the pre-baked spac-on/spac-off font
 *     variants (params edits and Rebuild need a real build → hidden)
 *   - Config export: a static file download (import needs a rebuild →
 *     hidden)
 *
 * Known limitations (tracked in the migration doc):
 *   - getMappedLocation returns the input coordinates (no avar2
 *     evaluation yet) — parametric sliders show inputs, not the mapping
 *   - anything that writes or builds throws "needs the full app"
 */

import { api } from './api';
import { compileFont } from './fontc-compile';
import { parseFont } from './fvar';

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

const buildUploadDataset = async (file) => {
  const sourceText = await file.text();
  const ttf = await compileFont(sourceText);
  const meta = parseFont(ttf);
  const fontUrl = URL.createObjectURL(new Blob([ttf], { type: 'font/ttf' }));
  return {
    sourceText,
    fontUrl,
    axes: {
      axes: meta.axes.map(a => ({
        tag: a.tag, name: a.name,
        min: a.min, default: a.default, max: a.max,
        has_master_coverage: true, is_control_axis: false,
      })),
    },
    instances: {
      instances: meta.instances.map(i => ({
        name: i.name, coordinates: i.coordinates, origin: 'source',
      })),
    },
    health: {
      static: true, demo: false, building: false,
      font_built: true, font_loaded: true,
      glyphs_path: `upload:${file.name}:${Date.now()}`,
      original_path: `upload:${file.name}`,
      source_format: 'glyphs',
      family_name: meta.familyName,
      vf_family_id: `${meta.familyName}-VF`,
      built_font_filename: `${meta.familyName}.ttf`,
      last_build_status: 'ok', last_build_error: null,
      avar2_error: null, build_stale: false,
      upm: meta.upm,
    },
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

const staticOverrides = {
  health: async () => (uploadDataset ? uploadDataset.health : staticHealth()),
  glyphsFileStatus: async () => ({ has_unsaved_changes: false }),
  getInstances: async () => (uploadDataset ? uploadDataset.instances : fetchJSON(await variantFile('instances.json'))),
  getMasters: async () => (uploadDataset ? { masters: [] } : endpoint('masters.json')()),
  getAxes: async () => (uploadDataset ? uploadDataset.axes : fetchJSON(await variantFile('axes.json'))),
  getAvar2Instances: async () => (uploadDataset ? { instances: [] } : endpoint('avar2-instances.json')()),
  getAvar2Axes: async () => (uploadDataset ? { axes: [] } : endpoint('avar2-axes.json')()),
  getTransforms: async () => (uploadDataset ? { transforms: [] } : { transforms: await transformsList() }),
  getGrade: async () => (uploadDataset
    ? { enabled: false, default_pct: 0.25, instances: [], max_pct: {} }
    : endpoint('grade.json')()),
  listControlAxes: async () => (uploadDataset ? { axes: [] } : endpoint('control-axes.json')()),
  getGlyphCoverage: async () => (uploadDataset ? {} : endpoint('glyph-coverage.json')()),
  listExamples: examplesIndex,
  checkSyncStatus: async () => ({ synced: true, message: 'Static demo snapshot' }),
  getFontUrl: () => (uploadDataset ? uploadDataset.fontUrl : currentFontPath()),
  getAvar2FontUrl: () => (uploadDataset ? uploadDataset.fontUrl : currentFontPath()),
  exportConfigUrl: () => `${datasetPath}/config-export.json`,

  // Uploads: compile the source in a Web Worker (fontc-wasm) and switch
  // the app to the resulting in-memory dataset. This is the Phase 2
  // path — no server anywhere.
  uploadSource: async (files) => {
    const list = Array.from(files || []);
    const glyphsFile = list.find(f => f.name.toLowerCase().endsWith('.glyphs'));
    if (!glyphsFile) {
      throw new Error('No .glyphs file in the upload (sources must be .glyphs for now)');
    }
    const ignored = list.filter(f => f !== glyphsFile).map(f => f.name);
    uploadDataset = await buildUploadDataset(glyphsFile);
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

  // The parametric-slider reflection falls back to input coordinates —
  // the avar2 evaluator hasn't been ported (Phase 2 in the plan).
  getMappedLocation: async (coordinates) => ({ mapped: coordinates || {} }),

  // Editing-registration is best-effort on the real server; no-op here.
  registerEditingInstance: async () => ({}),
  unregisterEditingInstance: async () => ({}),

  // Everything that writes is unavailable. (Building and uploads are
  // implemented above via fontc-wasm — don't shadow them here.)
  buildAvar2Font: unavailable('Building'),
  createInstance: unavailable('Creating instances'),
  updateInstance: unavailable('Saving instances'),
  renameInstance: unavailable('Renaming instances'),
  deleteInstance: unavailable('Deleting instances'),
  addInstanceToSource: unavailable('Saving to source'),
  addAvar2Axis: unavailable('Adding mapping axes'),
  updateAvar2Axis: unavailable('Editing mapping axes'),
  updateAvar2Mapping: unavailable('Editing mappings'),
  setGrade: unavailable('Editing grade'),
  setInstanceGrade: unavailable('Editing grade'),
  removeInstanceGrade: unavailable('Editing grade'),
  createControlAxis: unavailable('Creating control axes'),
  updateControlAxis: unavailable('Editing control axes'),
  deleteControlAxis: unavailable('Deleting control axes'),
  controlAxisLayerDelta: unavailable('Editing layers'),
  setControlAxisLayers: unavailable('Editing layers'),
  openControlAxisInEditor: unavailable('The glyph editor'),
  exportFont: unavailable('Exporting'),
  importConfig: unavailable('Importing configurations'),
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
