// fontc worker entry — font compilation and avar2 generation off the
// main thread. Protocol:
//   {kind: 'compile', source: string}     → {ok, ttf|error}
//   {kind: 'avar2', fontBytes, csv}       → {ok, ttf|error}
// The ttf is transferred (zero-copy) back to the caller.

import init, { compile_glyphs, add_avar2 } from './wasm/fontc-web/fontc_web.js';

const ready = init();

self.onmessage = async (e) => {
  try {
    await ready;
    if (e.data && e.data.kind === 'avar2') {
      const ttf = add_avar2(e.data.fontBytes, e.data.csv);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else {
      const ttf = compile_glyphs(e.data.source ?? e.data);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    }
  } catch (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  }
};
