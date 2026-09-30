/**
 * compileFont / addAvar2 / applyControlAxes / applyGrade — font
 * compilation and bundle application in a Web Worker (fontc → WASM;
 * see wasm/fontc-web and docs/migration-github-pages.md).
 *
 * One worker for the app's lifetime; calls serialize through a queue (the
 * worker resolves one job at a time — see send()).
 */

import FontcWorker from './fontc-worker.js?worker';

let worker = null;
let pending = null;
// Jobs serialize through this chain: the worker holds one pending resolver,
// and callers (rebuild pipeline, editor-bridge model pulls) are NOT
// centrally coordinated — without the queue an unlucky overlap rejected
// with "a fontc job is already running".
let queue = Promise.resolve();

function getWorker() {
  if (!worker) {
    worker = new FontcWorker();
    worker.onmessage = (e) => {
      const p = pending;
      pending = null;
      if (!p) return;
      if (e.data.ok) p.resolve(e.data.ttf ?? e.data.areas ?? e.data.model);
      else p.reject(new Error(e.data.error || 'fontc worker failed'));
    };
    worker.onerror = (e) => {
      const p = pending;
      pending = null;
      if (p) p.reject(new Error(e.message || 'fontc worker error'));
    };
  }
  return worker;
}

function send(message) {
  const result = queue.then(() => new Promise((resolve, reject) => {
    pending = { resolve, reject };
    getWorker().postMessage(message);
  }));
  // Keep the chain alive past a failed job.
  queue = result.catch(() => {});
  return result;
}

/** .glyphs source string → TTF bytes. */
export function compileFont(source) {
  return send({ kind: 'compile', source });
}

/** .glyphs source + one glyph name → the source-level glyph model JSON
 *  (axes, masters, layers with sidecar-schema outlines) for the glyph
 *  editor bridge. Returns the parsed object. */
export async function glyphModel(source, glyph) {
  return JSON.parse(await send({ kind: 'glyph-model', source, glyph }));
}

/** .glyphs source + an overlay request ({axes, overlays}) → TTF bytes with
 *  the axes declared and the drawn brace layers spliced in at the Plist
 *  level (the desktop shadow-build path, source-side). */
export function compileWithOverlays(source, request) {
  return send({ kind: 'compile-overlays', source, request: JSON.stringify(request) });
}

/** TTF bytes + mappings CSV → TTF bytes with user axes + avar v2 table.
 *  Optional axisMetadata: {TAG: {min, default, max}} range overrides
 *  for newly declared user axes (axis-metadata.json semantics), and
 *  parametricTags for the in/out split when regenerating onto a font
 *  that already carries user axes (default-location rebuild). */
export function addAvar2(fontBytes, mappingsCsv, axisMetadata = null, parametricTags = null) {
  return send({ kind: 'avar2', fontBytes, csv: mappingsCsv, metadata: axisMetadata, parametricTags });
}

/** Export option: rebuild with fvar defaults moved to the location
 *  (user values + mapped parametrics), avar2 regenerated around it. */
export function exportFontSetDefault(fontBytes, defaultLocation, mappingsCsv, axisMetadata, parametricTags) {
  return send({
    kind: 'set-default',
    fontBytes,
    location: JSON.stringify(defaultLocation),
    csv: mappingsCsv,
    metadata: axisMetadata,
    parametricTags: parametricTags ? JSON.stringify(parametricTags) : null,
  });
}

/** Export option: flag fvar axes as hidden in the exported font. */
export function exportFontHiddenAxes(fontBytes, hiddenTags) {
  return send({ kind: 'hide-axes', fontBytes, tags: JSON.stringify(hiddenTags) });
}

/** Rebuild the STAT table from the font's fvar (Google-Fonts-ready). */
export function regenStat(fontBytes) {
  return send({ kind: 'stat', fontBytes });
}

/** Coverage probe: outline area (stem-darkness proxy) across `glyphs`
 *  for each location in `locations` (user coords, fvar tags) — one
 *  entry per location. Batched in one worker round-trip. */
export function measureAt(fontBytes, glyphs, locations) {
  return send({ kind: 'measure', fontBytes, request: JSON.stringify({ glyphs, locations }) });
}

/** Pin a ghost corner: hold it up with the scaffold location's shape
 *  (a model-computed gvar tuple — master semantics, no bleed onto
 *  other corners). Locations in user coords. */
export function pinCorner(fontBytes, corner, scaffold) {
  return send({ kind: 'pin', fontBytes, request: JSON.stringify({ corner, scaffold }) });
}

/** TTF bytes + control-axes JSON array → TTF bytes with the new fvar
 * axes and their computed gvar brace tuples. */
export function applyControlAxes(fontBytes, controlJson) {
  return send({ kind: 'control', fontBytes, json: controlJson });
}

/** TTF bytes + grade JSON + instance-coords JSON → TTF bytes with the
 * GRAD fvar axis and equalised light/dark brace tuples. */
export function applyGrade(fontBytes, gradeJson, coordsJson) {
  return send({ kind: 'grade', fontBytes, json: gradeJson, coords: coordsJson });
}

/** TTF bytes + transforms JSON array + avar2 CSV → TTF bytes with the
 * enabled SPAC transform applied (SPAC fvar axis + gvar phantom tuples
 * + rebuilt HVAR — the axis stays a live slider). */
export function applyTransforms(fontBytes, transformsJson, avar2Csv) {
  return send({ kind: 'transforms', fontBytes, json: transformsJson, csv: avar2Csv });
}

/** Drop out-of-range (stranded) sources: their gvar deltas are zeroed
 * and HVAR rebuilt — the Glyphs.app/fontmake semantics. */
export function clampOutOfRange(fontBytes) {
  return send({ kind: 'clamp', fontBytes });
}
