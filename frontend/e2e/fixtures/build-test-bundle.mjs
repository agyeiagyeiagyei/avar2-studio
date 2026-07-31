#!/usr/bin/env node
/**
 * Build the e2e test bundle for control axes + grade (section 8 of
 * e2e/static-demo.spec.mjs).
 *
 * Takes the baked CrispyMini config export
 * (frontend/public/static-demo/crispy-mini/config-export.json) and adds:
 *   - one control axis `crbr` (0..100, default 0) with a single layer
 *     {glyph: "e", location: {"XTRA": 1000}} — the wasm port computes
 *     the brace as e's interpolated shape at XTRA=1000;
 *   - a grade (enabled, default_pct 0.25) with one graded instance at
 *     pct 0.3 — "Narrow Heavy 144" from the baked instances.json (a
 *     heavy instance, so the grade effect is clearly visible).
 *
 * Usage: node e2e/fixtures/build-test-bundle.mjs
 * Writes: e2e/fixtures/crispy-mini-test-bundle.json
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const EXPORT = join(HERE, '../../public/static-demo/crispy-mini/config-export.json');
const INSTANCES = join(HERE, '../../public/static-demo/crispy-mini/instances.json');
const OUT = join(HERE, 'crispy-mini-test-bundle.json');

const bundle = JSON.parse(readFileSync(EXPORT, 'utf8'));
const instances = JSON.parse(readFileSync(INSTANCES, 'utf8')).instances || [];

const GRADED = 'Narrow Heavy 144';
if (!instances.some(i => i.name === GRADED)) {
  throw new Error(`instance '${GRADED}' not in ${INSTANCES}`);
}

bundle.control_axes = {
  version: 1,
  axes: [
    {
      tag: 'crbr',
      name: 'Crossbar',
      min: 0,
      default: 0,
      max: 100,
      layers: [{ glyph: 'e', location: { XTRA: 1000 } }],
    },
  ],
};

bundle.grade = {
  version: 1,
  enabled: true,
  default_pct: 0.25,
  instances: [{ name: GRADED, pct: 0.3 }],
};

writeFileSync(OUT, JSON.stringify(bundle, null, 2) + '\n');
console.log(`wrote ${OUT}`);
