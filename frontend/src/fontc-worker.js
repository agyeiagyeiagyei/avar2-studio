// fontc worker entry — compiles .glyphs sources off the main thread.
// Protocol: postMessage(string source) → postMessage({ok, ttf|error}).
// The ttf is transferred (zero-copy) back to the caller.

import init, { compile_glyphs } from './wasm/fontc-web/fontc_web.js';

const ready = init();

self.onmessage = async (e) => {
  try {
    await ready;
    const ttf = compile_glyphs(e.data);
    self.postMessage({ ok: true, ttf }, [ttf.buffer]);
  } catch (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  }
};
