// fontc worker entry — font compilation, avar2 generation, bundle
// application (control axes, grade, SPAC transforms) and export
// options (default location, hidden axes) off the main thread.
// Protocol:
//   {kind: 'compile', source: string}     → {ok, ttf|error}
//   {kind: 'avar2', fontBytes, csv, metadata?, parametricTags?} → {ok, ttf|error}
//   {kind: 'control', fontBytes, json}    → {ok, ttf|error}
//   {kind: 'grade', fontBytes, json, coords} → {ok, ttf|error}
//   {kind: 'transforms', fontBytes, json, csv} → {ok, ttf|error}
//   {kind: 'set-default', fontBytes, location, csv, metadata?, parametricTags?} → {ok, ttf|error}
//   {kind: 'hide-axes', fontBytes, tags}  → {ok, ttf|error}
//   {kind: 'measure', fontBytes, request} → {ok, areas|error}
//   {kind: 'pin', fontBytes, request}     → {ok, ttf|error}
//   {kind: 'clamp', fontBytes}            → {ok, ttf|error}
// The ttf is transferred (zero-copy) back to the caller.

import init, {
  compile_glyphs,
  add_avar2,
  apply_control_axes,
  apply_grade,
  apply_transforms,
  set_default_location,
  set_hidden_axes,
  regen_stat,
  measure_at,
  pin_corner,
  clamp_out_of_range,
} from './wasm/fontc-web/fontc_web.js';

const ready = init();

self.onmessage = async (e) => {
  try {
    await ready;
    if (e.data && e.data.kind === 'clamp') {
      const ttf = clamp_out_of_range(e.data.fontBytes);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else if (e.data && e.data.kind === 'pin') {
      const ttf = pin_corner(e.data.fontBytes, e.data.request);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else if (e.data && e.data.kind === 'measure') {
      const areas = measure_at(e.data.fontBytes, e.data.request);
      self.postMessage({ ok: true, areas: Array.from(areas) });
    } else if (e.data && e.data.kind === 'avar2') {
      // wasm expects parametricTags as a JSON string, not a JS array
      const parametricTagsJson = e.data.parametricTags ? JSON.stringify(e.data.parametricTags) : undefined;
      console.log('[worker] add_avar2 called, parametricTagsJson:', parametricTagsJson);
      console.log('[worker] CSV preview:', e.data.csv?.substring(0, 200));
      const ttf = add_avar2(e.data.fontBytes, e.data.csv, e.data.metadata ?? undefined, parametricTagsJson);
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
    } else if (e.data && e.data.kind === 'set-default') {
      const ttf = set_default_location(e.data.fontBytes, e.data.location, e.data.csv, e.data.metadata ?? undefined, e.data.parametricTags ?? undefined);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else if (e.data && e.data.kind === 'hide-axes') {
      const ttf = set_hidden_axes(e.data.fontBytes, e.data.tags);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else if (e.data && e.data.kind === 'stat') {
      const ttf = regen_stat(e.data.fontBytes);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    } else {
      const ttf = compile_glyphs(e.data.source ?? e.data);
      self.postMessage({ ok: true, ttf }, [ttf.buffer]);
    }
  } catch (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  }
};
