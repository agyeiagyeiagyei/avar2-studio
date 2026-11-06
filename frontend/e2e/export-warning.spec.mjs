/**
 * E2l: the export dialog's size warning — the copy and its silence.
 *
 * The file is larger whenever the resting location is not the source's default
 * master, because the studio rebuilds the variations around the resting style;
 * on a twin-heavy source the preview's spacing is then approximate between
 * masters (measured on Crispy: up to ~397 u at some styles). The warning says
 * both and how to fix them, and it stays silent at the default master — a
 * false warning would teach designers to ignore it.
 *
 * Usage: node e2e/export-warning.spec.mjs
 */

import { sizeWarningCopy } from '../src/export-formats.js';

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};

const text = (report) => sizeWarningCopy(report) || '';

const other = { status: 'other_master', name: 'Black', default_master: 'Thin',
  max_delta: 274 };
const none = { status: 'no_master', name: 'Thin', default_master: 'Thin',
  max_delta: 616.1 };

check('silent at the default master',
  sizeWarningCopy({ status: 'default_master', name: 'Thin', default_master: 'Thin' }) === null);
check('silent when the source could not be read (no guessing)',
  sizeWarningCopy({ status: 'unknown' }) === null && sizeWarningCopy(null) === null);

check('another master: says the file is larger', /larger/.test(text(other)));
check('another master: names it and the default master',
  /“Black”/.test(text(other)) && /“Thin”/.test(text(other)));
check('no master: says so and how far away it is',
  /not a master/.test(text(none)) && /616 units/.test(text(none)));
check('both: says the preview spacing is approximate between masters',
  /approximate between masters/.test(text(other)) && /approximate between masters/.test(text(none)));
check('both: says how to fix it (default master / Variable Font Origin)',
  /make that master the default/.test(text(other)) && /Variable Font Origin/.test(text(other)));
check('never names an internal file',
  !/(\.json|\.csv|sidecar|shadow)/i.test(text(other) + text(none)));

if (failures) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log('\nall export-warning checks passed');
