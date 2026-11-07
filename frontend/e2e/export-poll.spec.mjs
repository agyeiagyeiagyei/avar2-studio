/**
 * Unit tests for api.exportFont — the export poll on the per-job endpoint
 * (GET /api/build/<job_id>) with AbortSignal support (sprint T2.5).
 *
 * Before: the poll keyed on GET /api/build/status → status.last, which names
 * only the NEWEST finished job; an export finishing inside one poll window
 * was lost and the client spun for 20 minutes with Cancel disabled.
 *
 * fetch is replaced with a scripted router; no server, no browser.
 *
 * Usage: node e2e/export-poll.spec.mjs
 */

import { api } from '../src/api.js';

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};
const section = (t) => console.log(`\n${t}`);

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

/**
 * Scripted backend: `jobs` maps job id → a queue of status answers (the
 * last one repeats); each POST /api/export-font hands out the next id.
 */
function fakeServer({ ids, jobs }) {
  const log = [];
  let next = 0;
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method || 'GET';
    const path = String(url).replace(/^.*?\/api/, '/api');
    log.push(`${method} ${path}`);
    if (init.signal && init.signal.aborted) {
      throw new DOMException('aborted', 'AbortError');
    }
    if (method === 'POST' && path === '/api/export-font') {
      return json({ job_id: ids[next++], status: 'queued' }, 202);
    }
    let m = path.match(/^\/api\/build\/([^/]+)$/);
    if (m && method === 'GET') {
      const q = jobs[m[1]];
      if (!q) return json({ error: 'no such job' }, 404);
      const st = q.length > 1 ? q.shift() : q[0];
      return json({ job_id: m[1], kind: 'export', ...st });
    }
    if (m && method === 'DELETE') {
      return json({ cancelled: true });
    }
    m = path.match(/^\/api\/export\/([^/]+)\/download$/);
    if (m && method === 'GET') {
      return new Response(`bytes-of-${m[1]}`, { status: 200 });
    }
    return json({ error: `unrouted ${method} ${path}` }, 500);
  };
  return log;
}
const count = (log, prefix) => log.filter(l => l.startsWith(prefix)).length;

section('two exports finishing inside one poll window both resolve by id');
{
  const finished = { state: 'finished', success: true, cancelled: false, error: null, artifact_ready: true };
  const log = fakeServer({
    ids: ['export-1', 'export-2'],
    jobs: { 'export-1': [finished], 'export-2': [finished] },
  });
  const [a, b] = await Promise.all([
    api.exportFont({ hidden_axes: [], default_location: null, format: 'ttf' }),
    api.exportFont({ hidden_axes: ['XOPQ'], default_location: null, format: 'ttf' }),
  ]);
  check('first export downloads ITS artifact', await a.text() === 'bytes-of-export-1');
  check('second export downloads ITS artifact', await b.text() === 'bytes-of-export-2');
  check('each polled its own job', count(log, 'GET /api/build/export-1') === 1 && count(log, 'GET /api/build/export-2') === 1);
  check('the whole-queue status endpoint is no longer consulted', count(log, 'GET /api/build/status') === 0, log.join(', '));
}

section('a running job is polled until finished; a failed job rejects with the worker error');
{
  const log = fakeServer({
    ids: ['export-3'],
    jobs: { 'export-3': [
      { state: 'queued' },
      { state: 'running', elapsed: 1.0, phase: 'fontc' },
      { state: 'finished', success: false, cancelled: false, error: 'fontc: boom', artifact_ready: false },
    ] },
  });
  let err = null;
  try { await api.exportFont({ hidden_axes: [], default_location: null, format: 'ttf' }); } catch (e) { err = e; }
  check('rejects with the worker error', err && err.message === 'fontc: boom', err && err.message);
  check('polled three times', count(log, 'GET /api/build/export-3') === 3, log.join(', '));
  check('no download attempted', count(log, 'GET /api/export/') === 0);
  check('no cancel sent for a job that ended by itself', count(log, 'DELETE') === 0);
}

section('abort mid-poll: stops polling, cancels the job, rejects with an AbortError');
{
  const log = fakeServer({
    ids: ['export-4'],
    jobs: { 'export-4': [{ state: 'running', elapsed: 2.0, phase: 'fontc' }] },
  });
  const controller = new AbortController();
  const started = Date.now();
  const pending = api.exportFont({ hidden_axes: [], default_location: null, format: 'ttf' }, { signal: controller.signal });
  // let the POST + first poll go through, then cancel during the 1 s sleep
  await new Promise(r => setTimeout(r, 150));
  controller.abort();
  let err = null;
  try { await pending; } catch (e) { err = e; }
  const elapsed = Date.now() - started;
  check('rejects with an AbortError', err && err.name === 'AbortError', err && `${err.name}: ${err.message}`);
  check('cancelled the job server-side', count(log, 'DELETE /api/build/export-4') === 1, log.join(', '));
  check('woke from the poll sleep at once (not after the 1 s interval)', elapsed < 800, `${elapsed} ms`);
  const pollsAtAbort = count(log, 'GET /api/build/export-4');
  await new Promise(r => setTimeout(r, 1200));
  check('no further polls after the abort', count(log, 'GET /api/build/export-4') === pollsAtAbort);
}

section('abort before the POST: nothing is submitted');
{
  const log = fakeServer({ ids: ['export-5'], jobs: {} });
  const controller = new AbortController();
  controller.abort();
  let err = null;
  try { await api.exportFont({ hidden_axes: [], default_location: null, format: 'ttf' }, { signal: controller.signal }); } catch (e) { err = e; }
  check('rejects with an AbortError', err && err.name === 'AbortError');
  check('no request made', log.length === 0, log.join(', '));
}

section('a job cancelled elsewhere (veil Cancel, source swap) is a plain error, not an abort');
{
  fakeServer({
    ids: ['export-6'],
    jobs: { 'export-6': [{ state: 'finished', success: false, cancelled: true, error: 'cancelled', artifact_ready: false }] },
  });
  let err = null;
  try { await api.exportFont({ hidden_axes: [], default_location: null, format: 'ttf' }); } catch (e) { err = e; }
  check('rejects with a readable message', err && err.name !== 'AbortError' && /cancelled/.test(err.message), err && err.message);
}

section('an unknown job id (studio restarted) fails clearly');
{
  fakeServer({ ids: ['export-7'], jobs: {} });
  let err = null;
  try { await api.exportFont({ hidden_axes: [], default_location: null, format: 'ttf' }); } catch (e) { err = e; }
  check('404 → clear message', err && /no longer knows this export/.test(err.message), err && err.message);
}

section('timeout: gives up with a clear message and drops the job');
{
  const log = fakeServer({
    ids: ['export-8'],
    jobs: { 'export-8': [{ state: 'running', elapsed: 1.0, phase: 'fontc' }] },
  });
  const realNow = Date.now;
  let calls = 0;
  // the deadline is taken on the first Date.now(); every later reading is
  // 21 minutes on
  Date.now = () => (calls++ === 0 ? realNow() : realNow() + 21 * 60 * 1000);
  let err = null;
  try { await api.exportFont({ hidden_axes: [], default_location: null, format: 'ttf' }); } catch (e) { err = e; }
  Date.now = realNow;
  check('rejects with the timeout message', err && /timed out after 20 minutes/.test(err.message), err && err.message);
  check('cancelled the job it will never download', count(log, 'DELETE /api/build/export-8') === 1, log.join(', '));
}

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
