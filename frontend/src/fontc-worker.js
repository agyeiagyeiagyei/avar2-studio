// fontc worker entry — font compilation, avar2 generation and bundle
// application (control axes, grade, SPAC transforms) off the main
// thread. Protocol:
//   {kind: 'compile', source: string}     → {ok, ttf|error}
//   {kind: 'avar2', fontBytes, csv}       → {ok, ttf|error}
//   {kind: 'control', fontBytes, json}    → {ok, ttf|error}
//   {kind: 'grade', fontBytes, json, coords} → {ok, ttf|error}
//   {kind: 'transforms', fontBytes, json, csv} → {ok, ttf|error}
// The ttf is transferred (zero-copy) back to the caller.

import init, { compile_glyphs, add_avar2, apply_control_axes, apply_grade, apply_transforms } from './wasm/fontc-web/fontc_web.js';

const ready = init();

self.onmessage = async (e) => {
  try {
    await ready;
    if (e.data && e.data.kind === 'avar2') {
      const ttf = add_avar2(e.data.fontBytes, e.data.csv, e.data.metadata ?? undefined);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else if (e.data && e.data.kind === 'control') {
      const ttf = apply_control_axes(e.data.fontBytes, e.data.json);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else if (e.data && e.data.kind === 'grade') {
      const ttf = apply_grade(e.data.fontBytes, e.data.json, e.data.coords);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else if (e.data && e.data.kind === 'transforms') {
      const ttf = apply_transforms(e.data.fontBytes, e.data.json, e.data.csv);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else {
      const ttf = compile_glyphs(e.data.source ?? e.data);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    }
  } catch (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  }
};
