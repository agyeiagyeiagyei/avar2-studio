/**
 * Provider parity: src/api.js (live server) vs src/static-api.js (browser,
 * swapped in by Object.assign on a static host) — sprint T2.7.
 *
 * Because the swap is an Object.assign, any live method the browser
 * provider does NOT override stays callable there and would hit /api/… and
 * 404. So every live-only method must be declared below with the
 * `capabilities` flag the UI gates it on (false in the browser), or as
 * internal to a live-only path. Browser-only methods are declared too, so
 * neither side can drift silently. The two `capabilities` objects must
 * carry the same keys.
 *
 * static-api.js cannot be imported under node (Vite `?worker` import), so
 * both providers are read as text: top-level keys of the object literal,
 * their parameter lists, and the capabilities literal.
 *
 * Usage: node e2e/api-parity.spec.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { api } from '../src/api.js';

const HERE = dirname(fileURLToPath(import.meta.url));

// Live-only methods → the capability whose `false` keeps the browser UI
// from calling them, or 'internal' when only another live-only path uses
// them.
const DESKTOP_ONLY = {
  getMasters: 'masters',
  getBuildStatus: 'woff2',       // export sizes for the format chips
  getBuildJob: 'internal',       // the live exportFont's own poll
  cancelBuild: 'cancel',
  getWoff2DownloadUrl: 'woff2',
  getWoff2PreviewUrl: 'woff2',
  getKinetikern: 'kinetikern',
  proposeKinetikern: 'kinetikern',
  applyKinetikern: 'kinetikern',
  revertKinetikern: 'kinetikern',
  applyKinetikernAll: 'kinetikern',
  setKinetikernEnabled: 'kinetikern',
};

// Browser-only methods (the Header/App call them only in static mode).
// checkSyncStatus, getCoverage, addMappingRow, pinCorner and clampOutOfRange
// have no UI caller left — pre-existing dead code, listed so the test
// stays honest until the dead-code item removes them.
const BROWSER_ONLY = new Set([
  'flushEditorRebuilds', 'forgetSession', 'exportWorkspaceUrl',
  'checkSyncStatus', 'getCoverage', 'addMappingRow', 'pinCorner', 'clampOutOfRange',
]);

// Shared methods whose parameter COUNT legitimately differs, with the reason.
const SIGNATURE_EXCEPTIONS = {
  updateInstance: 'live takes {csvOnly}: the browser has no source writeback, every save is studio-only',
  deleteInstance: 'live takes {csvOnly|sourceOnly}: same reason',
  exportFont: 'live takes {signal}: the browser export is synchronous wasm work, nothing to cancel',
  registerEditingInstance: 'no-op in the browser',
  unregisterEditingInstance: 'no-op in the browser',
  addInstanceToSource: 'browser: unavailable(…) thrower with guidance, takes no arguments',
  reseedControlAxisLayers: 'browser: unavailable(…) thrower with guidance, takes no arguments',
};

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};
const section = (t) => console.log(`\n${t}`);

/** Top-level keys (and parameter lists) of the object literal that starts at `marker`. */
function methods(file, marker) {
  const src = readFileSync(join(HERE, '..', 'src', file), 'utf8');
  const start = src.indexOf(marker);
  if (start < 0) throw new Error(`${file}: marker not found: ${marker}`);
  const body = src.slice(start);
  const end = body.search(/^\};/m);
  const out = new Map();
  for (const line of body.slice(0, end).split('\n')) {
    // exactly two spaces of indentation = a top-level key; bodies are deeper
    const m = line.match(/^ {2}(?:async )?([A-Za-z_$][\w$]*)\s*(?::\s*(?:async\s*)?)?(?:\(([^)]*(?:\)\s*=>)?)|:)/);
    if (!m) continue;
    const params = m[2] == null ? null : splitParams(m[2].replace(/\)\s*=>$/, ''));
    out.set(m[1], params);
  }
  return out;
}

/** Split a parameter list on top-level commas (destructured params count as one). */
function splitParams(text) {
  const params = [];
  let depth = 0, cur = '';
  for (const ch of text) {
    if ('{[('.includes(ch)) depth++;
    if ('}])'.includes(ch)) depth--;
    if (ch === ',' && depth === 0) { params.push(cur.trim()); cur = ''; } else cur += ch;
  }
  if (cur.trim()) params.push(cur.trim());
  return params;
}

/** The `capabilities: { … }` literal of a provider as {key: boolean}. */
function capabilitiesLiteral(file) {
  const src = readFileSync(join(HERE, '..', 'src', file), 'utf8');
  const m = src.match(/^ {2}capabilities: \{([^}]*)\}/m);
  if (!m) throw new Error(`${file}: no capabilities literal`);
  const out = {};
  for (const line of m[1].split('\n')) {
    const kv = line.match(/^\s*([A-Za-z_$][\w$]*):\s*(true|false)\s*,/);
    if (kv) out[kv[1]] = kv[2] === 'true';
  }
  return out;
}

const live = methods('api.js', 'export const api = {');
const stat = methods('static-api.js', 'const staticOverrides = {');

section('both providers parsed');
check('live provider has methods', live.size > 40, String(live.size));
check('browser provider has methods', stat.size > 40, String(stat.size));
check('text extraction matches the real live object', [...live.keys()].every(k => k in api) && Object.keys(api).every(k => live.has(k)),
  `missing: ${Object.keys(api).filter(k => !live.has(k)).join(', ')} / extra: ${[...live.keys()].filter(k => !(k in api)).join(', ')}`);

section('every live method has a browser counterpart or is declared DESKTOP_ONLY');
for (const name of live.keys()) {
  if (name === 'capabilities') continue;
  check(name, stat.has(name) || name in DESKTOP_ONLY, 'live-only and not in DESKTOP_ONLY');
}

section('every browser method has a live counterpart or is declared BROWSER_ONLY');
for (const name of stat.keys()) {
  if (name === 'capabilities') continue;
  check(name, live.has(name) || BROWSER_ONLY.has(name), 'browser-only and not in BROWSER_ONLY');
}

section('the declared sets are exact (no stale entries)');
for (const name of Object.keys(DESKTOP_ONLY)) {
  check(`${name} is live-only`, live.has(name) && !stat.has(name), stat.has(name) ? 'the browser provider has it now — drop it from DESKTOP_ONLY' : 'not a live method');
}
for (const name of BROWSER_ONLY) {
  check(`${name} is browser-only`, stat.has(name) && !live.has(name), live.has(name) ? 'the live provider has it now — drop it from BROWSER_ONLY' : 'not a browser method');
}

section('capabilities: same keys, and every DESKTOP_ONLY gate is false in the browser / true live');
const liveCaps = capabilitiesLiteral('api.js');
const statCaps = capabilitiesLiteral('static-api.js');
check('live literal matches the imported object', JSON.stringify(liveCaps) === JSON.stringify(api.capabilities), JSON.stringify([liveCaps, api.capabilities]));
check('same capability keys', JSON.stringify(Object.keys(liveCaps).sort()) === JSON.stringify(Object.keys(statCaps).sort()),
  `${Object.keys(liveCaps)} vs ${Object.keys(statCaps)}`);
for (const [name, cap] of Object.entries(DESKTOP_ONLY)) {
  if (cap === 'internal') continue;
  check(`${name} ← capabilities.${cap}`, liveCaps[cap] === true && statCaps[cap] === false, `live ${liveCaps[cap]}, browser ${statCaps[cap]}`);
}
check('sourceWriteback: live true, browser false', liveCaps.sourceWriteback === true && statCaps.sourceWriteback === false);
check('fontraEditor: both providers open the editor', liveCaps.fontraEditor === true && statCaps.fontraEditor === true);

section('shared methods agree on their parameters');
for (const name of live.keys()) {
  if (!stat.has(name) || name === 'capabilities') continue;
  const a = live.get(name) || [], b = stat.get(name) || [];
  if (a.length === b.length) continue;
  check(`${name}(${a.join(', ')}) vs (${b.join(', ')})`, name in SIGNATURE_EXCEPTIONS, 'parameter count differs and no SIGNATURE_EXCEPTIONS entry explains it');
}
for (const name of Object.keys(SIGNATURE_EXCEPTIONS)) {
  const a = live.get(name) || [], b = stat.get(name) || [];
  check(`exception for ${name} is still needed`, live.has(name) && stat.has(name) && a.length !== b.length, 'signatures agree now — drop the exception');
}
// The one the audit named: identical parameter names, not just counts.
check('openControlAxisInEditor takes the same arguments on both providers',
  JSON.stringify((live.get('openControlAxisInEditor') || []).map(p => p.split('=')[0].trim()))
  === JSON.stringify((stat.get('openControlAxisInEditor') || []).map(p => p.split('=')[0].trim())),
  `${live.get('openControlAxisInEditor')} vs ${stat.get('openControlAxisInEditor')}`);

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
