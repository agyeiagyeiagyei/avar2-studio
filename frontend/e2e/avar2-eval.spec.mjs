/**
 * Oracle test for src/avar2-eval.js against a REAL wasm-built font.
 *
 * Fixture: /tmp/crispy-demo-live.ttf — crispy-demo-14aug.glyphs compiled
 * in-browser + the fixed 8-row avar2 config applied (captured from a
 * live session by e2e/reflection-extract.mjs; regenerate with that
 * script if missing). Expected values are the fontTools VariationModel
 * evaluation of the same 8-row CSV (the model add_avar2 ports).
 *
 * Usage: node e2e/avar2-eval.spec.mjs [path-to-ttf]
 */
import { readFileSync } from 'node:fs';
import { parseAvar2, mappedLocation } from '../src/avar2-eval.js';
import { parseFont } from '../src/fvar.js';

const TTF = process.argv[2] || '/tmp/crispy-demo-live.ttf';
let bytes;
try {
  bytes = new Uint8Array(readFileSync(TTF));
} catch {
  console.log(`fixture ${TTF} missing — run e2e/reflection-extract.mjs first (SKIP)`);
  process.exit(0);
}

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};
const close = (a, b, tol = 1.5) => Math.abs(a - b) < tol;

console.log('parse');
const parsed = parseAvar2(bytes);
check('parses without throwing, non-null', !!parsed);
check('axisCount matches fvar', parsed?.axisCount === 5);
check('regions present', (parsed?.regions?.length ?? 0) > 0);

console.log('\nmapped locations vs the fontTools model');
const axes = parseFont(bytes).axes;
const at = (wght, wdth) => mappedLocation(bytes, axes, { wght, wdth });
const CASES = [
  // [wght, wdth, XTRA, XOPQ, YOPQ] — probe values from the 8-row grid
  [100, 5, 47.0, 1.0, 1.0],        // origin corner
  [900, 5, 47.0, 143.5, 193.6],    // BC
  [100, 200, 1665.0, 1.0, 1.0],    // WL
  [900, 200, 1665.0, 700.0, 275.0],// Control Test
  [400, 100, 835.3, 257.7, 103.0], // grid interior (authored mids)
  [400, 5, 47.0, 54.4, 73.2],      // Normal Narrow
];
for (const [wght, wdth, xtra, xopq, yopq] of CASES) {
  const m = at(wght, wdth);
  check(`(${wght}, ${wdth}) -> ${xtra}/${xopq}/${yopq}`,
    close(m.XTRA, xtra) && close(m.XOPQ, xopq) && close(m.YOPQ, yopq),
    `got ${m.XTRA}/${m.XOPQ}/${m.YOPQ}`);
}

console.log('\ninputs reflect unchanged');
const m = at(900, 5);
check('wght passes through', m.wght === 900);
check('wdth passes through', m.wdth === 5);

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
