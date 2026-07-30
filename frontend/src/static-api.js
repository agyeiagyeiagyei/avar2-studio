/**
 * Static-demo data provider — lets the built bundle run with NO backend
 * (GitHub Pages). docs/migration-github-pages.md has the plan.
 *
 * How it works: before the app renders, selectApiMode() probes
 * /api/health. A real server answers 200 → nothing changes. On a static
 * host the probe 404s → we swap api's methods for static ones that read
 * the build-time snapshot in public/static-demo/ (captured API
 * responses — shapes match by construction) and reject write operations
 * with a clear message.
 *
 * Known static-mode limitations (tracked in the migration doc):
 *   - getMappedLocation returns the input coordinates (no avar2
 *     evaluation yet) — parametric sliders show inputs, not the mapping
 *   - no builds, saving, transforms, grade, control axes, import/export
 *   - text width comes from the instances snapshot (server-computed)
 */

import { api } from './api';

const DATA = 'static-demo'; // relative — resolves under any --base

let staticMode = false;
export const isStaticMode = () => staticMode;

let healthPromise = null;
const staticHealth = () => {
  if (!healthPromise) {
    healthPromise = fetch(`${DATA}/health.json`).then(r => {
      if (!r.ok) throw new Error('static demo data missing (health.json)');
      return r.json();
    });
  }
  return healthPromise;
};

const fetchJSON = (name) => async () => {
  const r = await fetch(`${DATA}/${name}`);
  if (!r.ok) throw new Error(`static demo data missing (${name})`);
  return r.json();
};

const unavailable = (what) => async () => {
  throw new Error(`${what} needs the full app — this static demo is read-only.`);
};

const staticOverrides = {
  health: staticHealth,
  glyphsFileStatus: async () => ({ has_unsaved_changes: false }),
  getInstances: fetchJSON('instances.json'),
  getMasters: fetchJSON('masters.json'),
  getAxes: fetchJSON('axes.json'),
  getAvar2Instances: fetchJSON('avar2-instances.json'),
  getAvar2Axes: fetchJSON('avar2-axes.json'),
  getTransforms: fetchJSON('transforms.json'),
  getGrade: fetchJSON('grade.json'),
  listControlAxes: fetchJSON('control-axes.json'),
  getGlyphCoverage: fetchJSON('glyph-coverage.json'),
  listExamples: async () => ({ examples: [] }),
  checkSyncStatus: async () => ({ synced: true, message: 'Static demo snapshot' }),
  getFontUrl: () => `${DATA}/demo.ttf`,
  getAvar2FontUrl: () => `${DATA}/demo.ttf`,
  exportConfigUrl: () => '#',
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
  updateTransforms: unavailable('Editing transforms'),
  setGrade: unavailable('Editing grade'),
  setInstanceGrade: unavailable('Editing grade'),
  removeInstanceGrade: unavailable('Editing grade'),
  createControlAxis: unavailable('Creating control axes'),
  updateControlAxis: unavailable('Editing control axes'),
  deleteControlAxis: unavailable('Deleting control axes'),
  controlAxisLayerDelta: unavailable('Editing layers'),
  setControlAxisLayers: unavailable('Editing layers'),
  openControlAxisInEditor: unavailable('The glyph editor'),
  loadExample: unavailable('Loading fonts'),
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
