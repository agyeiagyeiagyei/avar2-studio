/**
 * Unit tests for src/instance-sync.js — the subset comparison behind the
 * instance rows' sync dot (A6: a source row could never turn green).
 *
 * The real case: `Default 400 MaxOpsz` on the Crispy copy reports
 * `avar2_mapping.out == {XOPQ 202.5, XTRA 663.1, YOPQ 121.8}` and the same
 * values in `glyphs_coordinates`, while `source_coordinates` also carries
 * GRAD/LCWD/ROND/slnt/YMOD at their defaults.
 *
 * Usage: node e2e/instance-sync.spec.mjs
 */

import { sameCoords, csvDivergesFromSource, editsAreDirty, persistedValue } from '../src/instance-sync.js';

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};

const csv = { XOPQ: 202.5, XTRA: 663.1000061035156, YOPQ: 121.8 };
const source = {
  XOPQ: 202.5, XTRA: 663.1, YOPQ: 121.8,
  GRAD: 0, LCWD: 0, ROND: 0, slnt: 0, YMOD: 0,
};

check('a matching source row is in sync despite the extra axes',
      csvDivergesFromSource(csv, source) === false);
check('float32 noise (663.1000061035156 vs 663.1) is inside tolerance',
      csvDivergesFromSource({ XTRA: 663.1000061035156 }, { XTRA: 663.1 }) === false);
check('a real divergence still shows',
      csvDivergesFromSource({ XOPQ: 275.3, XTRA: 662.2, YOPQ: 4 },
                            { XOPQ: 5, XTRA: 662.2, YOPQ: 4 }) === true);
check('a 0.02 difference is a divergence',
      csvDivergesFromSource({ XOPQ: 202.52 }, { XOPQ: 202.5 }) === true);
check('no comparable axis means in sync, not a permanent orange',
      csvDivergesFromSource({ SPAC: 1 }, source) === false);
check('missing inputs are not a divergence',
      csvDivergesFromSource(null, source) === false);

check('sameCoords ignores key order', sameCoords({ a: 1, b: 2 }, { b: 2, a: 1 }) === true);
check('sameCoords rejects a different key count', sameCoords({ a: 1 }, { a: 1, b: 0 }) === false);
check('sameCoords compares non-numbers literally',
      sameCoords({ a: 'x' }, { a: 'y' }) === false);

// F3 (findings.md, verifier re-run at 72271f4): the dot compared SPAC by
// exact key case. The mappings CSV lowercases the column (`in: {spac: …}`)
// while the row, the sliders and /api/instances use the fvar tag (`SPAC`),
// so every SPAC draft read as "no persisted home" and the row stayed red
// for good whenever the SPAC transform was on.
console.log('\nsync dot: persisted keys compare case-insensitively (F3)');
check('edits {SPAC} against in {spac} is CLEAN',
      editsAreDirty({ SPAC: 70.6 }, { wght: 400, spac: 70.6 }) === false);
check('the lowercase draft reads the same way',
      editsAreDirty({ spac: 70.6 }, { SPAC: 70.6, wght: 400 }) === false);
check('a real SPAC difference is still dirty',
      editsAreDirty({ SPAC: 70.6 }, { spac: 51.2 }) === true);
check('a key with genuinely no persisted home is dirty',
      editsAreDirty({ SPAC: 70.6 }, { wght: 400 }) === true);
check('preview-only axes never make a row dirty',
      editsAreDirty({ GRAD: 12 }, { wght: 400 }, new Set(['grad'])) === false);
check('persistedValue finds the exact key first, then case-insensitively',
      persistedValue({ wght: 1, SPAC: 2 }, 'wght').value === 1
      && persistedValue({ spac: 3 }, 'SPAC').value === 3
      && persistedValue({ wght: 1 }, 'SPAC').found === false);
check('editsAreDirty tolerates float32 noise',
      editsAreDirty({ XTRA: 663.1000061035156 }, { XTRA: 663.1 }) === false);

if (failures) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log('\ninstance-sync: all passed');
