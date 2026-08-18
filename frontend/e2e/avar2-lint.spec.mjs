/**
 * Unit tests for src/avar2-lint.js — the mappings-CSV guard.
 *
 * Grounded in the crispy-demo-14aug regression (Aug 2026): four
 * extreme-corner rows with wght/wdth defaults at (400, 100) produced a
 * dead default cross (sliders inert until past the default), and a
 * "Regular" row added at the default location was silently discarded
 * by add_avar2 while skewing every authored corner.
 *
 * Usage: node e2e/avar2-lint.spec.mjs
 */

import { lintAvar2Mappings, deriveInputRanges, normalize } from '../src/avar2-lint.js';
import { parseMappingsCsv } from '../src/mappings-csv.js';

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};
const section = (t) => console.log(`\n${t}`);

const OUTPUT_RANGES = {
  XTRA: { min: 47, default: 47, max: 1665 },
  XOPQ: { min: 1, default: 1, max: 700 },
  YOPQ: { min: 1, default: 1, max: 275 },
};
const PARAMETRIC = ['XTRA', 'XOPQ', 'YOPQ'];

const CRISPY_4CORNERS = `Instance Name,XTRA,XOPQ,YOPQ,WGHT,WDTH
Control Test,1665.0,700.0,275.0,900.0,200.0
Min,47.0,1.0,1.0,100.0,5.0
BC,47.0,143.5,193.6,900.0,5.0
WL,1665.0,1.0,1.0,100.0,200.0
`;

const CRISPY_8ROWS = `Instance Name,XTRA,XOPQ,YOPQ,WGHT,WDTH
Min,47.0,1.0,1.0,100.0,5.0
Normal Narrow,47.0,54.4,73.2,400.0,5.0
BC,47.0,143.5,193.6,900.0,5.0
Thin Normal,835.3,1.0,1.0,100.0,100.0
Bold Normal,835.3,414.6,233.3,900.0,100.0
WL,1665.0,1.0,1.0,100.0,200.0
Normal Wide,1665.0,263.1,103.8,400.0,200.0
Control Test,1665.0,700.0,275.0,900.0,200.0
`;

const MID_DEFAULTS = {
  WGHT: { min: 100, default: 400, max: 900 },
  WDTH: { min: 5, default: 100, max: 200 },
};

section('normalize (fontTools normalizeValue semantics)');
check('default -> 0', normalize(400, 100, 400, 900) === 0);
check('max -> 1', normalize(900, 100, 400, 900) === 1);
check('min -> -1', normalize(100, 100, 400, 900) === -1);
check('default==min: below-default side is 0-width', normalize(47, 47, 47, 1665) === 0);
check('clamps above max', normalize(2000, 100, 400, 900) === 1);

section('deriveInputRanges (lib.rs gen_fvar_axes semantics)');
{
  const parsed = parseMappingsCsv(CRISPY_4CORNERS);
  const r = deriveInputRanges(parsed, ['WGHT', 'WDTH']);
  check('span from CSV, default=min', r.WGHT.min === 100 && r.WGHT.default === 100 && r.WGHT.max === 900);
  const m = deriveInputRanges(parsed, ['WGHT', 'WDTH'], { WGHT: { default: 400 } });
  check('metadata default override', m.WGHT.default === 400 && m.WGHT.min === 100);
}

section('crispy-demo-14aug regression: 4 corners, mid defaults -> dead cross');
{
  const { findings } = lintAvar2Mappings(parseMappingsCsv(CRISPY_4CORNERS), {
    parametricTags: PARAMETRIC,
    outputRanges: OUTPUT_RANGES,
    inputRanges: MID_DEFAULTS,
  });
  const dead = findings.filter(f => f.type === 'unmapped-mapping-point');
  check('4 unmapped grid points (the cross arms)', dead.length === 4,
    `got ${dead.length}: ${dead.map(f => JSON.stringify(f.location)).join(' ')}`);
  const locs = dead.map(f => `${f.location.WGHT},${f.location.WDTH}`).sort();
  check('exactly the default-line anchors',
    JSON.stringify(locs) === JSON.stringify(['100,100', '400,200', '400,5', '900,100']));
  check('no default-row finding (no row at 400,100)', !findings.some(f => f.type === 'avar2-default-row'));
  check('all findings are fails', findings.every(f => f.severity === 'fail'));
}

section('the "Regular at the default location" trap is caught');
{
  const csv = CRISPY_4CORNERS + 'Regular,800.0,350.0,140.0,400.0,100.0\n';
  const { findings } = lintAvar2Mappings(parseMappingsCsv(csv), {
    parametricTags: PARAMETRIC,
    outputRanges: OUTPUT_RANGES,
    inputRanges: MID_DEFAULTS,
  });
  const row = findings.filter(f => f.type === 'avar2-default-row');
  check('flagged once', row.length === 1);
  check('names the row and the discarded outputs',
    row[0]?.detail.includes('"Regular"') && row[0]?.detail.includes('XTRA 800'));
  check('location is the input default', row[0]?.location.WGHT === 400 && row[0]?.location.WDTH === 100);
}

section('a base row with output values AT their defaults is legitimate');
{
  const csv = CRISPY_4CORNERS + 'Base,47.0,1.0,1.0,400.0,100.0\n';
  const { findings } = lintAvar2Mappings(parseMappingsCsv(csv), {
    parametricTags: PARAMETRIC,
    outputRanges: OUTPUT_RANGES,
    inputRanges: MID_DEFAULTS,
  });
  check('no default-row finding', !findings.some(f => f.type === 'avar2-default-row'));
}

section('the shipped fix: 8-row grid, origin defaults -> clean');
{
  const { findings } = lintAvar2Mappings(parseMappingsCsv(CRISPY_8ROWS), {
    parametricTags: PARAMETRIC,
    outputRanges: OUTPUT_RANGES,
    // reupload semantics: no metadata, defaults land on min
  });
  check('no findings at all', findings.length === 0,
    findings.map(f => `${f.type}: ${f.detail}`).join(' | '));
}

section('8-row grid but mid defaults re-set -> only the centre pothole remains');
{
  const { findings } = lintAvar2Mappings(parseMappingsCsv(CRISPY_8ROWS), {
    parametricTags: PARAMETRIC,
    outputRanges: OUTPUT_RANGES,
    inputRanges: MID_DEFAULTS,
  });
  // The all-default centre is the compiled origin — never authorable, so
  // never reported as unmapped; the 8 authored rows cover the rest.
  check('grid coverage is clean', !findings.some(f => f.type === 'unmapped-mapping-point'),
    findings.map(f => f.detail).join(' | '));
}

section('blank cells resolve to the axis default (CrispyMini shape)');
{
  // Ultra Wide Thin rows leave WGHT blank -> default (100 with metadata).
  // Row A lands at the all-default location; its XTRA is AT the output
  // default, so it is the legitimate base row, not a discarded one.
  const csv = `Instance Name,XTRA,WGHT,WDTH
A,94.0,,20.0
B,3330.0,,200.0
C,94.0,900.0,20.0
D,3330.0,900.0,200.0
`;
  const { findings } = lintAvar2Mappings(parseMappingsCsv(csv), {
    parametricTags: ['XTRA'],
    outputRanges: { XTRA: { min: 94, default: 94, max: 3330 } },
    metadata: { WGHT: { min: 100, default: 100, max: 900 }, WDTH: { min: 20, default: 20, max: 200 } },
  });
  check('blank WGHT = 100 = default: full {min,max}^2 grid, clean', findings.length === 0,
    findings.map(f => f.detail).join(' | '));
}

section('degenerate inputs');
{
  check('no input columns -> no findings',
    lintAvar2Mappings(parseMappingsCsv('Instance Name,XTRA\nA,100\n'), {
      parametricTags: ['XTRA'], outputRanges: OUTPUT_RANGES,
    }).findings.length === 0);
  check('empty CSV -> no findings',
    lintAvar2Mappings(parseMappingsCsv(''), { parametricTags: PARAMETRIC }).findings.length === 0);
}

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
