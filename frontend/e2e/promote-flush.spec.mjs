/**
 * Unit tests for src/promote-flush.js — the F1 fix from findings.md
 * ("Designer Part B finding", 2026-10-21): promoting a studio row into
 * the source file must flush the pending debounced draft FIRST, because
 * the promotion flips the row to origin:"source" and switches the studio
 * auto-save off.
 *
 * Usage: node e2e/promote-flush.spec.mjs
 */

import { promoteStudioRow, previewFreeCoords } from '../src/promote-flush.js';

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};
const section = (t) => console.log(`\n${t}`);

const PREVIEW_ONLY = new Set(['grad', 'rond']);

/** API double that records the call order. */
const recorder = ({ failOn } = {}) => {
  const calls = [];
  return {
    calls,
    order: () => calls.map(c => c.call).join(' → '),
    async updateInstance(name, coords, opts) {
      calls.push({ call: 'updateInstance', name, coords, opts });
      if (failOn === 'updateInstance') throw new Error('save failed');
    },
    async addInstanceToSource(name) {
      calls.push({ call: 'addInstanceToSource', name });
      if (failOn === 'addInstanceToSource') throw new Error('promote failed');
    },
    async getInstances() {
      calls.push({ call: 'getInstances' });
      return { instances: [{ name: 'Row A', origin: 'source', coordinates: {} }] };
    },
  };
};

section('the draft is written before the promotion, preview axes stripped');
{
  const api = recorder();
  const result = await promoteStudioRow('Row A', {
    api,
    coords: { wght: 900, SPAC: 51.2, GRAD: 12, ROND: 4 },
    previewOnlyTags: PREVIEW_ONLY,
  });
  check('order: flush → promote → refresh',
    api.order() === 'updateInstance → addInstanceToSource → getInstances', api.order());
  const put = api.calls[0];
  check('flush is the csv-only path', put && put.opts && put.opts.csvOnly === true);
  check('flush carries the on-screen values',
    put && put.coords.wght === 900 && put.coords.SPAC === 51.2, JSON.stringify(put && put.coords));
  check('preview-only axes are not persisted',
    put && !('GRAD' in put.coords) && !('ROND' in put.coords), JSON.stringify(put && put.coords));
  check('reports the flush', result.flushed === true);
  check('hands back the refreshed rows',
    result.instances.length === 1 && result.instances[0].origin === 'source');
}

section('the flush queues behind the pending auto-save chain');
{
  const api = recorder();
  let chainDone = false;
  const pending = new Promise(resolve => setTimeout(() => { chainDone = true; resolve(); }, 25));
  const started = Date.now();
  await promoteStudioRow('Row A', {
    api, coords: { wght: 900 }, previewOnlyTags: PREVIEW_ONLY, pending,
  });
  check('waited for the chain', chainDone === true);
  check('took at least the chain delay', Date.now() - started >= 20, `${Date.now() - started} ms`);
  check('still ordered correctly',
    api.order() === 'updateInstance → addInstanceToSource → getInstances', api.order());
}

section('nothing to flush → no empty PUT, promotion still happens');
{
  const api = recorder();
  const result = await promoteStudioRow('Row A', {
    api, coords: { GRAD: 12 }, previewOnlyTags: PREVIEW_ONLY,
  });
  check('no updateInstance call', api.order() === 'addInstanceToSource → getInstances', api.order());
  check('reports no flush', result.flushed === false);
}

section('a failed flush does NOT promote');
{
  const api = recorder({ failOn: 'updateInstance' });
  let threw = false;
  try {
    await promoteStudioRow('Row A', { api, coords: { wght: 900 }, previewOnlyTags: PREVIEW_ONLY });
  } catch (err) {
    threw = /save failed/.test(err.message);
  }
  check('the save error propagates', threw);
  check('addInstanceToSource never ran', !api.calls.some(c => c.call === 'addInstanceToSource'), api.order());
}

section('previewFreeCoords keeps SPAC and never mutates the input');
{
  const input = { WGHT: 900, SPAC: 51.2, grad: 1 };
  const kept = previewFreeCoords(input, PREVIEW_ONLY);
  check('SPAC survives', kept.SPAC === 51.2);
  check('preview axis dropped (case-insensitive)', !('grad' in kept));
  check('input untouched', 'grad' in input && kept !== input);
  check('missing coords → empty', Object.keys(previewFreeCoords(undefined, PREVIEW_ONLY)).length === 0);
}

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
