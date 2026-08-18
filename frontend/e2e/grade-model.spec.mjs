/**
 * Unit tests for src/grade-model.js — the JS port of grade.py's
 * max_pct_for (per-instance grade slider caps).
 *
 * Oracle values generated from the Python module itself:
 *   .venv/bin/python -c "from avar2_studio import grade; grade.max_pct_for(...)"
 * (regenerate against src/avar2_studio/grade.py if the model changes).
 *
 * Usage: node e2e/grade-model.spec.mjs
 */

import { maxPctFor, K_YOPQ, COMP_RATIO } from '../src/grade-model.js';

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};
const close = (a, b) => Math.abs(a - b) < 1e-9;

console.log('model constants match grade.py');
check('K_YOPQ', K_YOPQ === 1.0);
check('COMP_RATIO', COMP_RATIO === 2.0);

console.log('\nmax_pct_for vs the Python oracle');
const CASES = [
  ['CrispyMini Narrow Heavy 12',
    { XTRA: 222.8, XOPQ: 279.6, YOPQ: 250.2 },
    { XTRA: [94.0, 3330.0], XOPQ: [2.0, 1016.0], YOPQ: [2.0, 462.0] },
    0.46065808297567956],
  ['crispy-demo Control Test (near the box ceiling)',
    { XTRA: 1665.0, XOPQ: 700.0, YOPQ: 275.0 },
    { XTRA: [47.0, 1665.0], XOPQ: [1.0, 700.0], YOPQ: [1.0, 275.0] },
    1.9927272727272727],
  ['crispy-demo BC (XTRA at the floor binds)',
    { XTRA: 47.0, XOPQ: 143.5, YOPQ: 193.6 },
    { XTRA: [47.0, 1665.0], XOPQ: [1.0, 700.0], YOPQ: [1.0, 275.0] },
    0.8409090909090909],
];
for (const [name, base, ranges, expected] of CASES) {
  const got = maxPctFor(base, ranges);
  check(name, close(got, expected), `got ${got}, want ${expected}`);
}

console.log('\ndegenerate bases (generous 2.0 cap)');
check('no XOPQ', maxPctFor({ XTRA: 100.0 }, { XTRA: [47, 1665] }) === 2.0);
check('zero XOPQ', maxPctFor({ XOPQ: 0.0 }, { XOPQ: [1, 700] }) === 2.0);
check('null base', maxPctFor(null, {}) === 2.0);
check('missing ranges never bind', maxPctFor({ XOPQ: 100, YOPQ: 50, XTRA: 200 }, {}) === 2.0);

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
