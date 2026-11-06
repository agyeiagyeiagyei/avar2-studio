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

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { sizeWarningCopy, restingQuery } from '../src/export-formats.js';

const HERE = dirname(fileURLToPath(import.meta.url));

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
check('no master: says so and names the nearest',
  /not a master/.test(text(none)) && /“Thin”/.test(text(none)));
check('both: says the preview spacing is approximate between masters',
  /approximate between masters/.test(text(other)) && /approximate between masters/.test(text(none)));
check('both: says the exported fonts are exact',
  /the exported fonts are exact/.test(text(other)) && /the exported fonts are exact/.test(text(none)));
check('both: names no internal file',
  !/(\.json|\.csv|sidecar|shadow)/i.test(text(other) + text(none)));
// Verifier, 2026-11-06 15:49: a mid-range default master has no neighbours
// along the axis lines through it, so advising "add a master there and make it
// the default" makes the design flat at up to 3,205 u. The note must not say it.
check('neither: advises neither adding a master nor moving the default',
  !/add a master/i.test(text(other) + text(none))
  && !/make (that|it) (master )?the default/i.test(text(other) + text(none))
  && !/Variable Font Origin/i.test(text(other) + text(none)));
check('neither: keeps the axis distance out of the copy',
  !/units away/.test(text(other) + text(none)));

if (failures) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log('\nall export-warning checks passed');

// E2l wiring: the dialog follows the "Set default at" checkbox. OFF asks about
// the ORIGIN row (no location — the backend reads the mappings file); ON asks
// about the preview's own style.
check('checkbox off asks about the ORIGIN row',
  restingQuery(false, { wght: 400 }) === null);
check('checkbox on asks about the preview location',
  JSON.stringify(restingQuery(true, { wght: 400 })) === '{"wght":400}');
check('checkbox on with no location still asks about the ORIGIN row',
  restingQuery(true, undefined) === null);

// And the dialog is wired to it: the API call takes the query, and the note
// renders only while the copy says something.
const preview = readFileSync(join(HERE, '..', 'src', 'components', 'PreviewTab.jsx'),
  'utf8');
check('PreviewTab asks through restingQuery',
  /api\.restingMaster\(restingQuery\(exportUseDefault, currentUserLocation\)\)/
    .test(preview));
check('PreviewTab renders the note only when the copy is non-empty',
  /exportFormat !== 'sources' && sizeWarning &&/.test(preview));
