/**
 * compileFont / addAvar2 / applyControlAxes / applyGrade — font
 * compilation and bundle application in a Web Worker (fontc → WASM;
 * see wasm/fontc-web and docs/migration-github-pages.md).
 *
 * One worker for the app's lifetime; calls serialize (each message
 * replaces the pending resolver — callers await before sending the
 * next; static demo uploads/rebuilds do exactly that).
 */

import FontcWorker from './fontc-worker.js?worker';

let worker = null;
let pending = null;

function getWorker() {
  if (!worker) {
    worker = new FontcWorker();
    worker.onmessage = (e) => {
      const p = pending;
      pending = null;
      if (!p) return;
      if (e.data.ok) p.resolve(e.data.ttf);
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
  if (pending) {
    return Promise.reject(new Error('a fontc job is already running'));
  }
  return new Promise((resolve, reject) => {
    pending = { resolve, reject };
    getWorker().postMessage(message);
  });
}

/** .glyphs source string → TTF bytes. */
export function compileFont(source) {
  return send({ kind: 'compile', source });
}

/** TTF bytes + mappings CSV → TTF bytes with user axes + avar v2 table. */
export function addAvar2(fontBytes, mappingsCsv) {
  return send({ kind: 'avar2', fontBytes, csv: mappingsCsv });
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
