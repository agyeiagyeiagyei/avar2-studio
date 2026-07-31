/**
 * compileFont / addAvar2 — font compilation and avar2 generation in a
 * Web Worker (fontc → WASM; see wasm/fontc-web and
 * docs/migration-github-pages.md).
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
