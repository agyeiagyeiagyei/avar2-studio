/**
 * Unit tests for src/kinetikern-copy.js — the B7 findings K2 and K3.
 *
 * K2: "Apply to all…" overwrites every style's looseness; the button carries
 * the value it writes and the confirm names the styles it will re-space.
 * K3: the dropdown marks a spaced style with words, and Revert names its style.
 *
 * Usage: node e2e/kinetikern-copy.spec.mjs
 */

import {
  applyAllConfirm, applyAllLabel, fmtLooseness, fontSizeForStyle, instanceOptionLabel,
  revertLabel, spacedStyles,
} from '../src/kinetikern-copy.js';

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};
const section = (t) => console.log(`\n${t}`);

section('K2 — the button carries the value it will write');
check('negative looseness', applyAllLabel(-0.6) === 'Apply -0.60 to all…', applyAllLabel(-0.6));
check('zero shows 0.00', applyAllLabel(0) === 'Apply 0.00 to all…', applyAllLabel(0));
check('positive gets a plus', applyAllLabel(0.25) === 'Apply +0.25 to all…', applyAllLabel(0.25));
check('garbage reads as 0.00', applyAllLabel(undefined) === 'Apply 0.00 to all…');

section('K2 — the confirm names the styles it will overwrite');
const stored = { instances: [{ name: 'Default 400 MinOpsz', looseness: -0.6 }] };
const instances = [{ name: 'Default 400 MinOpsz' }, { name: 'Narrow 1 MinOpsz' }];

const withTuned = applyAllConfirm({ instances, stored, looseness: 0 });
check('names the tuned style and its current value',
      withTuned.includes('Default 400 MinOpsz (-0.60)'), withTuned);
check('says what it becomes', withTuned.includes('will be re-spaced at 0.00'), withTuned);
check('singular verb for one style', withTuned.includes('already has its own looseness'), withTuned);
check('still says all instances are re-spaced', withTuned.startsWith('Space all 2 instances at 0.00?'), withTuned);

const twoTuned = applyAllConfirm({
  instances,
  stored: { instances: [{ name: 'A', looseness: -0.6 }, { name: 'B', looseness: 0.4 }] },
  looseness: 0,
});
check('plural verb for two styles', twoTuned.includes('already have their own looseness'), twoTuned);
check('both named with their values', twoTuned.includes('A (-0.60)') && twoTuned.includes('B (+0.40)'), twoTuned);

const plain = applyAllConfirm({ instances, stored: { instances: [] }, looseness: -0.2 });
check('nothing tuned → the plain confirm',
      plain === 'Space all 2 instances at -0.20? Each gets its own correction; the font rebuilds once.',
      plain);
check('spacedStyles tolerates a missing payload', spacedStyles(null).length === 0);
check('spacedStyles keeps name and value',
      spacedStyles({ instances: [{ name: 'A', looseness: -0.6 }] })[0].looseness === -0.6);

section('K3 — the dropdown and Revert say what they mean');
check('unspaced option is just the name', instanceOptionLabel('Narrow 1') === 'Narrow 1');
check('spaced option carries the word and the value',
      instanceOptionLabel('Default 400 MinOpsz', { looseness: -0.6 })
      === 'Default 400 MinOpsz · spaced -0.60',
      instanceOptionLabel('Default 400 MinOpsz', { looseness: -0.6 }));
check('no bare dot marker left', !instanceOptionLabel('X', { looseness: 0 }).includes('●'));
check('Revert names its style', revertLabel('Default 400 MinOpsz') === 'Revert Default 400 MinOpsz');
check('Revert without a style stays plain', revertLabel('') === 'Revert');

section('K4 — picking a style moves the size to its opsz');
const coord = (inst, tag) => (inst.coords || {})[tag];
check('opsz 72 → 72pt', fontSizeForStyle({ coords: { opsz: 72 } }, coord) === 72);
check('opsz 144 → 144pt', fontSizeForStyle({ coords: { opsz: 144 } }, coord) === 144);
check('below the slider range clamps to 9', fontSizeForStyle({ coords: { opsz: 4 } }, coord) === 9);
check('no opsz leaves the size alone', fontSizeForStyle({ coords: { wght: 400 } }, coord) === null);
check('missing instance is safe', fontSizeForStyle(null, coord) === null);

section('the slider format');
check('fmtLooseness(-0.6)', fmtLooseness(-0.6) === '-0.60');
check('fmtLooseness(0)', fmtLooseness(0) === '0.00');
check('fmtLooseness(1)', fmtLooseness(1) === '+1.00');

console.log(failures ? `\n${failures} FAILURE(S)` : '\nkinetikern-copy: all passed');
process.exit(failures ? 1 : 0);
