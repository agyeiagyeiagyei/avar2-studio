/**
 * Contract test: every key a consumer destructures from `useTransforms()`
 * must exist in `TransformsProvider`'s `value` object.
 *
 * Why this exists: the provider shipped without `transforms` and `grade`
 * (they live in `useStudioData`), so Header's Transforms menu and the rows'
 * G badges silently never rendered — `transforms = []`-style defaults and an
 * untyped context hid it, and `npm run typecheck` cannot see `.jsx` at all
 * (tsconfig has `allowJs` but no `checkJs`). This spec reads the three files
 * and fails when a consumer reads a key the provider does not expose.
 *
 * Usage: node e2e/transforms-context.spec.mjs
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const hookSrc = readFileSync(join(root, 'src/hooks/useTransforms.jsx'), 'utf8');
const consumerFiles = [
  'src/components/Header.tsx',
  'src/components/InstanceRows.jsx',
  'src/components/InstancesTab.jsx',
  'src/hooks/useInstanceEditing.jsx',
];
const consumers = consumerFiles.filter(p => {
  try { readFileSync(join(root, p)); return true; } catch { return false; }
});

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};

// `const value = { a, b, fn: ..., ... }` — take the top-level keys.
const valueStart = hookSrc.indexOf('const value = {');
if (valueStart < 0) {
  console.error('FAIL  TransformsProvider has no `const value = {` block');
  process.exit(1);
}
let depth = 0, end = valueStart;
for (let i = hookSrc.indexOf('{', valueStart); i < hookSrc.length; i++) {
  if (hookSrc[i] === '{') depth++;
  else if (hookSrc[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
}
const valueBody = hookSrc.slice(hookSrc.indexOf('{', valueStart) + 1, end);
const provided = new Set();
for (const line of valueBody.split('\n')) {
  const m = line.match(/^\s{4}(?:\.\.\.)?([A-Za-z_$][\w$]*)\s*(?::|,|$)/);
  if (m) provided.add(m[1]);
}

// Each consumer's `useTransforms()` destructure list. The call is written as
//   const { a, b: alias, c = 1,
//   } = useTransforms();
// so match back from the closing brace to the matching opening one.
const consumed = new Map();
for (const file of consumers) {
  const src = readFileSync(join(root, file), 'utf8');
  const re = /\}\s*=\s*useTransforms\(\)/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    const close = m.index;                     // index of the closing brace
    let depth = 0, open = -1;
    for (let i = close; i >= 0; i--) {
      if (src[i] === '}') depth++;
      else if (src[i] === '{') { depth--; if (depth === 0) { open = i; break; } }
      if (close - i > 2000) break;             // not a destructure
    }
    if (open < 0) continue;
    for (const part of src.slice(open + 1, close).split(',')) {
      const key = part.split(':')[0].split('=')[0].trim();
      if (/^[A-Za-z_$][\w$]*$/.test(key)) consumed.set(`${file}:${key}`, key);
    }
  }
}

check('consumers were found', consumed.size > 0, [...consumed.keys()].join(', '));
for (const [where, key] of consumed) {
  check(`${where} is provided by TransformsProvider`, provided.has(key),
        `provider exposes: ${[...provided].sort().join(', ')}`);
}
// The two that were actually missing (A10) — pin them by name.
check('provider exposes transforms', provided.has('transforms'));
check('provider exposes grade', provided.has('grade'));

if (failures) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log('\ntransforms-context: all passed');
