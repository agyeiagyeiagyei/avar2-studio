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

const DATA = 'static-demo'; // relative — resolves under any --base

let staticMode = false;
export const isStaticMode = () => staticMode;

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

// Files that differ between the baked variants. fontPath is tracked
// synchronously so getFontUrl() can stay sync (App calls it directly,
// but always after an awaited health()).
let fontPath = null;
const variantFile = async (name) => {
  const dir = await datasetDir();
  const variant = await activeVariant();
  const path = variant === 'spac-off'
    ? `${DATA}/${dir}/variants/spac-off/${name}`
    : `${DATA}/${dir}/${name}`;
  if (name === 'demo.ttf') fontPath = path;
  return path;
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
  health: staticHealth,
  glyphsFileStatus: async () => ({ has_unsaved_changes: false }),
  getInstances: async () => fetchJSON(await variantFile('instances.json')),
  getMasters: endpoint('masters.json'),
  getAxes: async () => fetchJSON(await variantFile('axes.json')),
  getAvar2Instances: endpoint('avar2-instances.json'),
  getAvar2Axes: endpoint('avar2-axes.json'),
  getTransforms: async () => ({ transforms: await transformsList() }),
  getGrade: endpoint('grade.json'),
  listControlAxes: endpoint('control-axes.json'),
  getGlyphCoverage: endpoint('glyph-coverage.json'),
  listExamples: examplesIndex,
  checkSyncStatus: async () => ({ synced: true, message: 'Static demo snapshot' }),
  getFontUrl: () => fontPath || `${DATA}/crispy-mini/demo.ttf`,
  getAvar2FontUrl: () => fontPath || `${DATA}/crispy-mini/demo.ttf`,
  exportConfigUrl: () => `${datasetPath}/config-export.json`,

  // Load Font: swap the dataset; App's loadData() re-reads the new
  // health (different glyphs_path) and treats it as a source swap.
  loadExample: async (id) => {
    const idx = await examplesIndex();
    if (!(idx.examples || []).some(e => e.id === id)) {
      throw new Error(`Unknown example: ${id}`);
    }
    dataset = id;
    transformsState = null;
    bakedEnabledIds = null;
    return { ok: true };
  },

  // Transforms toggles: allowed only between the two baked states (the
  // snapshot's enabled set ↔ all-off). Anything else isn't baked and
  // throws — App reverts the toggle and shows the message.
  updateTransforms: async (entries) => {
    const next = (entries || []).map(e => ({
      id: e.type || e.id,
      enabled: !!e.enabled,
      params: { ...(e.params || {}) },
    }));
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

  // Everything that writes or builds is unavailable.
  buildFont: unavailable('Building'),
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
  uploadSource: unavailable('Uploading sources'),
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
