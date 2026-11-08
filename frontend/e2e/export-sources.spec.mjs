/**
 * The export dialog's sources format (B12, 2026-11-08).
 *
 * The designer ticked "Set default at" for a TTF export, then picked
 * "UFO + designspace (zip)" — where that box is disabled and shown unticked —
 * and got the backend's refusal ("sources rest at the compiled origin …"):
 * the dialog still sent the default location. A sources download that did go
 * through was saved under the font's `.ttf` name. And the browser build can't
 * write sources at all, so it must not offer them.
 *
 * Usage: node e2e/export-sources.spec.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { exportDefaultLocation, exportFilename, exportFormats } from '../src/export-formats.js';
import { api } from '../src/api.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const source = (...p) => readFileSync(join(HERE, '..', 'src', ...p), 'utf8');

let failures = 0;
const check = (name, cond, extra = '') => {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`FAIL  ${name}${extra ? ` — ${extra}` : ''}`); }
};

// 1. The request: a default location only for the fonts.
const loc = { opsz: 144, wdth: 20, wght: 100 };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
check('sources send no default location, even with the box left ticked',
  exportDefaultLocation('sources', true, loc) === null);
check('sources with the box unticked send none',
  exportDefaultLocation('sources', false, loc) === null);
check('TTF with the box ticked sends the location', same(exportDefaultLocation('ttf', true, loc), loc));
check('WOFF2 with the box ticked sends the location', same(exportDefaultLocation('woff2', true, loc), loc));
check('TTF with the box unticked sends none', exportDefaultLocation('ttf', false, loc) === null);
check('TTF with the box ticked but no location sends none', exportDefaultLocation('ttf', true, undefined) === null);

// 2. The file name: the zip as the backend and the CLI name it.
const built = 'Crispy[GRAD,LCWD,ROND,SPAC,XOPQ,XTRA,YMOD,YOPQ,opsz,slnt,wdth,wght].ttf';
check('the sources zip is <Family>-sources.zip',
  exportFilename('sources', built, 'Crispy') === 'Crispy-sources.zip', exportFilename('sources', built, 'Crispy'));
check('spaces dropped, as the backend does',
  exportFilename('sources', built, 'Crispy Mini Test') === 'CrispyMiniTest-sources.zip');
check('no family: font-sources.zip, the backend fallback',
  exportFilename('sources', built, null) === 'font-sources.zip');
check('TTF keeps the built name', exportFilename('ttf', built, 'Crispy') === built);
check('WOFF2 swaps the extension',
  exportFilename('woff2', built, 'Crispy') === built.replace(/\.ttf$/, '.woff2'));

// 3. The chips follow the provider: the live studio makes sources, the browser can't.
const ids = (o) => exportFormats(o).map(f => f.id).join(',');
check('all three formats by default', ids() === 'ttf,woff2,sources', ids());
check('no sources chip when the provider has none', ids({ sources: false }) === 'ttf,woff2', ids({ sources: false }));
check('live studio: sources on', api.capabilities.sources === true);
check('browser: sources off', /^ {4}sources: false,/m.test(source('static-api.js')));

// 4. The dialog is wired to all three (source text, like export-warning.spec).
const preview = source('components', 'PreviewTab.jsx');
check('the request takes its default location from exportDefaultLocation',
  /default_location: exportDefaultLocation\(exportFormat, exportUseDefault, currentUserLocation\)/.test(preview));
check('no other default_location in the dialog',
  (preview.match(/default_location:/g) || []).length === 1);
check('the download is named by exportFilename', /exportFilename\(exportFormat,/.test(preview));
check('the chips follow api.capabilities.sources',
  /exportFormats\(\{ woff2: api\.capabilities\.woff2, sources: api\.capabilities\.sources \}\)/.test(preview));

if (failures) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log('\nall export-sources checks passed');
