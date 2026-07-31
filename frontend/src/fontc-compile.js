/**
 * compileFont — compile a .glyphs source string to TTF bytes in a Web
 * Worker (fontc → WASM; see wasm/fontc-web and docs/migration-github-pages.md).
 *
 * One worker for the app's lifetime; compiles queue in order (each
 * message replaces the pending resolver — compiles are serialized by
 * the worker's own message loop, so callers must await before sending
 * the next; static demo uploads/rebuilds do exactly that).
 */

import FontcWorker from './fontc-worker.js?worker';

let worker = null;
let pending = null;

export function compileFont(source) {
  if (!worker) {
    worker = new FontcWorker();
    worker.onmessage = (e) => {
      const p = pending;
      pending = null;
      if (!p) return;
      if (e.data.ok) p.resolve(e.data.ttf);
      else p.reject(new Error(e.data.error || 'compile failed'));
    };
    worker.onerror = (e) => {
      const p = pending;
      pending = null;
      if (p) p.reject(new Error(e.message || 'fontc worker error'));
    };
  }
  if (pending) {
    return Promise.reject(new Error('a compile is already running'));
  }
  return new Promise((resolve, reject) => {
    pending = { resolve, reject };
    worker.postMessage(source);
  });
}
