/**
 * Unit tests for src/kinetikern-copy.js — the B7 findings K1, K2, K3, K4.
 *
 * K1: a spaced style is visible outside its tab — the row badge reads
 * "K -0.60" and the Transforms menu reports the count and says what "off"
 * does. K2: "Apply to all..." overwrites every style's looseness; the button
 * carries the value it writes and the confirm names the styles it will
 * re-space. K3: the dropdown marks a spaced style with words, and Revert names
 * its style. K4: picking a style moves the Font Size to its opsz.
 *
 * Usage: node e2e/kinetikern-copy.spec.mjs
 */

import {
  applyAllConfirm, applyAllLabel, badgeHint, badgeLabel, fmtLooseness, fontSizeForStyle,
  instanceOptionLabel, kinetikernMenuDesc, revertLabel, spacedStyles,
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

section('K1 — the row badge and the Transforms menu');
check('badge reads K -0.60', badgeLabel(-0.6) === 'K -0.60', badgeLabel(-0.6));
check('badge reads K 0.00 at zero', badgeLabel(0) === 'K 0.00');
check('badge reads K +0.25 when loose', badgeLabel(0.25) === 'K +0.25');
check('badge tolerates a missing value', badgeLabel(undefined) === 'K 0.00');
const onOne = kinetikernMenuDesc(true, 1);
check('menu says the count', onOne.includes('1 style spaced'), onOne);
check('menu says off keeps the entries', onOne.includes('Off builds without it and keeps the entries.'), onOne);
const onTwo = kinetikernMenuDesc(true, 2);
check('menu pluralises', onTwo.includes('2 styles spaced'), onTwo);
const off = kinetikernMenuDesc(false, 2);
check('off menu says the build ignores it', off.startsWith('Off'), off);
check('off menu says the entries are kept', off.includes('2 styles spaced'), off);
check('off menu says how to restore', off.includes('turn it back on'), off);

section('K1b — the popover hint names the tab, never a file');
const hint = badgeHint(-0.6);
check('hint carries the value', hint.includes('-0.60'), hint);
check('hint points at the Kinetikern tab', hint.includes('Kinetikern tab'), hint);
check('hint says the row does not own it', hint.includes('not in this row'), hint);
for (const word of ['sidecar', 'JSON', 'CSV', '.json', '.csv']) {
  check(`hint never says "${word}"`, !hint.toLowerCase().includes(word.toLowerCase()), hint);
}
check('menu copy never says "sidecar"',
      !onOne.toLowerCase().includes('sidecar') && !off.toLowerCase().includes('sidecar'));

console.log(failures ? `\n${failures} FAILURE(S)` : '\nkinetikern-copy: all passed');
process.exit(failures ? 1 : 0);
