/**
 * End-to-end tests for the static demo (GitHub Pages bundle, no backend).
 *
 * Covers the full surface:
 *   1. static boot (banner, header actions, CrispyMini rows, no Rebuild)
 *   2. Transforms SPAC toggle (baked variant swap)
 *   3. Load Font dataset switch (Roboto Delta Mini)
 *   4. .glyphs upload compiled in-browser (fontc-wasm worker)
 *   5. Rebuild on an uploaded source
 *   6. avar2 mappings upload (user axes + mapped reflection)
 *   7. config import onto an uploaded source (avar2 mappings apply)
 *   8. control axes + GRAD apply from a config bundle (computed braces;
 *      fixture built by e2e/fixtures/build-test-bundle.mjs)
 *   9. width-aware SPAC applies from the same bundle (Transforms menu
 *      state, parametric-group slider, specimen tracks SPAC)
 *  15. zip workspace (project zip upload incl. designspace, download)
 *
 * Usage:
 *   STATIC_URL=http://localhost:8123 node e2e/static-demo.spec.mjs
 *
 * Requires: dist-pages built (npx vite build --base=./ --outDir
 * dist-pages) and served statically, plus system Chrome (channel:
 * 'chrome' — same as the repo's screenshot harness).
 */

import { chromium } from 'playwright-core';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { readFileSync, writeFileSync } from 'node:fs';

const BASE = process.env.STATIC_URL || 'http://localhost:8123';
const FIXTURE = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'WasmTest.glyphs');
const FIXTURE_CSV = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'WasmTest-avar.csv');

let failures = 0;
const ok = (cond, label) => {
  console.log(`${cond ? '  ✓' : '  ✗ FAIL'} ${label}`);
  if (!cond) failures++;
};

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('pageerror', e => console.error('[pageerror]', e.message));
page.on('crash', () => console.error('[PAGE CRASHED]'));

const sleep = (ms) => page.waitForTimeout(ms);

// ---- 1. static boot -------------------------------------------------------
console.log('1. static boot');
await page.goto(BASE, { waitUntil: 'load', timeout: 30000 });
await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
ok(!(await page.$('.static-demo-banner')), 'no demo banner');
ok(await page.isVisible('button:has-text("Load Font")'), 'Load Font present');
ok(await page.isVisible('button:has-text("Transforms")'), 'Transforms present');
ok(await page.isVisible('button:has-text("Config")'), 'Config present');
ok(!(await page.$('header .btn-3d')), 'no Rebuild for snapshot datasets');
await page.waitForSelector('.sidebar h2', { timeout: 20000 });
ok((await page.textContent('.sidebar h2')) === 'CrispyMini', 'CrispyMini in sidebar');
await page.waitForSelector('.instance-row', { timeout: 20000 }).catch(() => null);
ok((await page.$$('.instance-row')).length > 0, 'instance rows render');

// ---- 2. SPAC toggle --------------------------------------------------------
console.log('2. SPAC toggle (baked variant)');
const spacVisible = () => page.evaluate(() =>
  [...document.querySelectorAll('.sidebar *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'SPAC'));
ok(await spacVisible(), 'SPAC axis visible in sidebar (baked default)');
await page.click('button:has-text("Transforms")');
await page.click('label:has-text("Spacing — width-aware") input[type=checkbox]');
await page.keyboard.press('Escape');
await sleep(2500);
ok(!(await spacVisible()), 'SPAC axis gone after toggle off (variant swap)');
ok(!(await page.$('.count-flag')), 'transforms count badge cleared');
await page.click('button:has-text("Transforms")');
await page.click('label:has-text("Spacing — width-aware") input[type=checkbox]');
await page.keyboard.press('Escape');
await sleep(2500);
ok(await spacVisible(), 'SPAC axis back after toggle on');

// ---- 3. dataset switch -----------------------------------------------------
console.log('3. Load Font dataset switch');
await page.click('button:has-text("Load Font")');
await page.click('text=Roboto Delta Mini');
await sleep(4000);
ok((await page.textContent('.sidebar h2')) === 'RobotoDeltaMini', 'Roboto family in sidebar');
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.sidebar *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'XOUC')), 'case-split axes (XOUC) present');

// ---- 4. upload (fontc-wasm compile) ---------------------------------------
console.log('4. .glyphs upload compiles in-browser');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', FIXTURE);
await page.waitForFunction(
  () => document.querySelector('.sidebar h2')?.textContent === 'WasmTest',
  { timeout: 60000 }
);
ok(true, 'WasmTest family in sidebar after upload compile');
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.sidebar *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'wght')), 'wght axis from compiled fvar');
ok(await page.isVisible('header .btn-3d'), 'Rebuild appears for uploaded sources');
// The fixture declares no source instances (studio rows are normally
// synthesized server-side) — the Preview tab renders regardless.
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 15000 });
ok(true, 'preview specimen renders for uploaded font');
// avar2 mappings state is per-dataset: the crispy-mini example's CSV
// columns (OPSZ/WGHT/WDTH) must not leak into a CSV-less upload.
await page.click('button:text-is("Instances")');
await page.waitForSelector('.sidebar', { timeout: 15000 });
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.sidebar .avar2-empty-hint')].some(el =>
    el.textContent.includes('No mapping axes yet'))),
  'no stale avar2 columns from the previous dataset');

// ---- 5. rebuild on upload --------------------------------------------------
console.log('5. Rebuild on uploaded source');
await page.click('header .btn-3d');
await page.waitForFunction(
  () => !document.querySelector('header .btn-3d')?.textContent.includes('Building'),
  { timeout: 60000 }
);
await page.click('button:text-is("Instances")');
await page.waitForSelector('.sidebar h2', { timeout: 15000 });
ok((await page.textContent('.sidebar h2')) === 'WasmTest', 'still WasmTest after rebuild');

// ---- 6. avar2 mappings upload (user axes + mapped reflection) --------------
console.log('6. avar2 mappings upload');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', [FIXTURE, FIXTURE_CSV]);
await page.waitForFunction(
  () => document.querySelector('.sidebar h2')?.textContent === 'WasmTest',
  { timeout: 90000 }
);
ok(true, 'WasmTest family after mappings upload');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.preview-tab *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'wght')),
  'USER AXES shows the CSV-derived wght axis (registered → lowercase)');
// The parametric (compiled) wght follows the mapping: with WGHT at its
// default (100 → wght 400), the reflected parametric wght reads 400.
await page.waitForFunction(() => {
  const rows = [...document.querySelectorAll('.preview-tab *')];
  return rows.some(el => el.children.length === 0 && el.textContent.trim() === '400');
}, { timeout: 20000 });
ok(true, 'parametric wght reflects mapping (400 at WGHT default)');
// Capture the specimen pixels — the next section uploads a DIFFERENT
// font, and the specimen must visibly change with it (staleness guard).
const specimenBefore = await page.screenshot({
  clip: await (await page.$('.preview-tab-sample')).boundingBox(),
});

// ---- 7. config import onto an uploaded source -------------------------------
console.log('7. config import onto an uploaded source');
const CRISPY_GLYPHS = join(dirname(fileURLToPath(import.meta.url)), '../../examples/crispy-mini/sources/CrispyMini.glyphs');
const CRISPY_BUNDLE = join(dirname(fileURLToPath(import.meta.url)), '../public/static-demo/crispy-mini/config-export.json');
await page.click('button:text-is("Instances")');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY_GLYPHS);
await page.waitForFunction(
  () => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'),
  { timeout: 90000 }
);
ok(true, 'CrispyMini.glyphs compiles on upload');
// The specimen must show the NEW font, not the previous upload's blob.
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
await page.waitForTimeout(1000);
const specimenAfter = await page.screenshot({
  clip: await (await page.$('.preview-tab-sample')).boundingBox(),
});
ok(!specimenBefore.equals(specimenAfter), 'specimen renders the newly uploaded font (not the stale blob)');
await page.click('button:text-is("Instances")');
await page.click('button:has-text("Config")');
await page.click('text=Import configuration…');
await page.setInputFiles('.config-dropdown input[type=file]', CRISPY_BUNDLE);
await page.waitForSelector('.import-config-confirm:not([disabled])', { timeout: 30000 });
ok(true, 'bundle validates clean against the uploaded font');
await page.click('.import-config-confirm');
await page.waitForFunction(() => !document.querySelector('.import-config-confirm'), { timeout: 90000 });
ok(true, 'bundle applied');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.preview-tab *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'opsz')),
  'opsz user axis appears after mappings import (registered → lowercase)');

// ---- 8. control axes + GRAD apply from a config bundle ----------------------
console.log('8. control axes + GRAD apply from bundle');
const TEST_BUNDLE = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'crispy-mini-test-bundle.json');
await page.click('button:text-is("Instances")');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY_GLYPHS);
await page.waitForFunction(
  () => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'),
  { timeout: 90000 }
);
ok(true, 'CrispyMini.glyphs compiles on upload (section 8)');
await page.click('button:has-text("Config")');
await page.click('text=Import configuration…');
await page.setInputFiles('.config-dropdown input[type=file]', TEST_BUNDLE);
await page.waitForSelector('.import-config-confirm:not([disabled])', { timeout: 30000 });
ok(true, 'control+grade bundle validates clean');
await page.click('.import-config-confirm');
await page.waitForFunction(() => !document.querySelector('.import-config-confirm'), { timeout: 90000 });
ok(true, 'control+grade bundle applied');
// The sidebar's SECONDARY PARAMETRIC AXES section lists the applied axis.
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.sidebar *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'SECONDARY PARAMETRIC AXES')),
  'SECONDARY PARAMETRIC AXES section shows in sidebar');
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.control-axes *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'crbr')),
  'crbr row in SECONDARY PARAMETRIC AXES');
// The Preview tab gets sliders for both new axes, in their own groups.
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
const sliderFor = (tag) => page.evaluateHandle((t) => {
  const groups = [...document.querySelectorAll('.axis-control')];
  const g = groups.find(el => [...el.querySelectorAll('.axis-tag')].some(x => x.textContent.trim() === t));
  return g ? g.querySelector('input[type=range]') : null;
}, tag);
const setSlider = async (tag, value) => {
  await page.evaluate(([t, v]) => {
    const groups = [...document.querySelectorAll('.axis-control')];
    const g = groups.find(el => [...el.querySelectorAll('.axis-tag')].some(x => x.textContent.trim() === t));
    const input = g && g.querySelector('input[type=range]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, v);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, [tag, String(value)]);
};
ok(await (await sliderFor('crbr')).evaluate(el => !!el), 'crbr slider in Preview (secondary parametric axes)');
ok(await (await sliderFor('GRAD')).evaluate(el => !!el), 'GRAD slider in Preview (Grade group)');
// Specimen shows 'e' only, so the pixel diff is the brace-layer effect.
const setSpecimen = (text) => page.evaluate((t) => {
  const el = document.querySelector('.preview-tab-sample');
  el.textContent = t;
  el.dispatchEvent(new Event('input', { bubbles: true }));
}, text);
await setSpecimen('eeee');
await sleep(600);
const specimenShot = () => page.locator('.preview-tab-sample').screenshot();
const shotDefault = await specimenShot();
await setSlider('crbr', 100);
await sleep(600);
const shotCrbr = await specimenShot();
ok(!shotDefault.equals(shotCrbr), 'moving crbr changes the rendered specimen for e');
await setSlider('crbr', 0);
await sleep(600);
const shotReset = await specimenShot();
ok(shotDefault.equals(shotReset), 'crbr back to default restores the specimen');
await setSlider('GRAD', 10);
await sleep(600);
const shotGrad = await specimenShot();
ok(!shotDefault.equals(shotGrad), 'GRAD +10 darkens the specimen');
await setSlider('GRAD', 0);

// ---- 9. SPAC transform applies from the bundle (width-aware) ----------------
// The test bundle imported in section 8 has spac_widthaware ENABLED —
// it applied in-browser with the rest of the bundle, so this page state
// already carries the injected SPAC axis (params min -20, max 40).
console.log('9. SPAC transform applies from bundle (width-aware)');
// The Transforms menu reflects the bundle's set: width-aware enabled.
await page.click('button:has-text("Transforms")');
const waRowChecked = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('.transform-row')];
  const row = rows.find(r => r.querySelector('.transform-name')?.textContent.trim() === 'Spacing — width-aware');
  return row ? !!row.querySelector('input[type=checkbox]')?.checked : null;
});
ok(waRowChecked === true, 'Transforms menu shows width-aware SPAC enabled');
await page.keyboard.press('Escape');
// SPAC is transform-injected: it sits in the parametric group with
// XTRA/XOPQ/YOPQ, a live slider on the built font.
const spacInParametric = await page.evaluate(() => {
  const groups = [...document.querySelectorAll('.preview-axis-group')];
  const g = groups.find(el =>
    [...el.querySelectorAll('.preview-axis-group-title')].some(x => x.textContent.trim() === 'Parametric axes'));
  return !!g && [...g.querySelectorAll('.axis-tag')].some(x => x.textContent.trim() === 'SPAC');
});
ok(spacInParametric, 'SPAC slider exists in the parametric group');
// Pixel-width check on the specimen text run: SPAC tracks the advances
// (width-aware: 'n' is narrower than the glyph mean, so it tracks less
// than an average glyph — but still monotonically).
const specimenWidth = () => page.evaluate(() => {
  const el = document.querySelector('.preview-tab-sample');
  const range = document.createRange();
  range.selectNodeContents(el);
  return range.getBoundingClientRect().width;
});
await setSpecimen('eeee');
await sleep(600);
const widthDefault = await specimenWidth();
await setSlider('SPAC', 40);
await sleep(600);
const widthWide = await specimenWidth();
await setSlider('SPAC', -20);
await sleep(600);
const widthTight = await specimenWidth();
ok(widthWide > widthTight, `specimen wider at SPAC +40 (${widthWide.toFixed(1)}px) than at -20 (${widthTight.toFixed(1)}px)`);
await setSlider('SPAC', 0);
await sleep(600);
const widthReset = await specimenWidth();
ok(Math.abs(widthReset - widthDefault) < 0.01, `specimen width at SPAC 0 matches baseline (${widthReset.toFixed(2)}px ≈ ${widthDefault.toFixed(2)}px)`);

// ---- 10. instance lifecycle on an uploaded source (authoring S1) ------------
console.log('10. instance lifecycle (create → edit → delete)');
await page.click('button:text-is("Instances")');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY_GLYPHS);
await page.waitForFunction(
  () => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'),
  { timeout: 90000 }
);
ok(true, 'fresh CrispyMini upload for authoring');
// The fresh upload clears the previous dataset's rows, but the rows
// refresh lags the family-name update — wait for it before counting.
await page.waitForFunction(
  () => document.querySelectorAll('.instance-row').length === 0,
  { timeout: 30000 }
);
const rowCount = async () => (await page.$$('.instance-row')).length;
const initialRows = await rowCount();
await page.click('.btn-new-instance');
await page.waitForSelector('.modal-input', { timeout: 10000 });
await page.fill('.modal-input', 'Test Wide 96');
await page.click('button:has-text("Create")');
await page.waitForFunction(
  (n) => document.querySelectorAll('.instance-row').length > n,
  initialRows,
  { timeout: 15000 }
);
ok(true, 'instance created via + New Instance');
await page.click('.instance-row:has-text("Test Wide 96")');
await page.waitForSelector('.sidebar input[type=range]', { timeout: 10000 });
const rowEl = await page.$('.instance-row');
const shotBefore = await rowEl.screenshot();
await page.evaluate(() => {
  const s = document.querySelector('.sidebar input[type=range]');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(s, 2000);
  s.dispatchEvent(new Event('input', { bubbles: true }));
});
await page.waitForTimeout(1200);
const shotAfter = await rowEl.screenshot();
ok(!shotBefore.equals(shotAfter), 'editing coordinates re-renders the specimen');
await page.click('.instance-row:has-text("Test Wide 96")');
await page.click('.delete-instance-btn');
// Studio-origin rows delete directly (no confirmation modal — that's
// reserved for source-defined rows).
await page.waitForFunction(
  (n) => document.querySelectorAll('.instance-row').length === n,
  initialRows,
  { timeout: 15000 }
);
ok(true, 'instance deleted');

// ---- 11. mapping authoring (declare axis + metadata range) -----------------
console.log('11. mapping authoring (declare axis + metadata range)');
await page.click('button:text-is("Instances")');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', [
  CRISPY_GLYPHS,
  join(dirname(fileURLToPath(import.meta.url)), '../../examples/crispy-mini/sources/CrispyMini-avar.csv'),
]);
await page.waitForFunction(
  () => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'),
  { timeout: 90000 }
);
await page.waitForFunction(
  () => document.querySelectorAll('.instance-row').length === 8,
  { timeout: 30000 }
);
ok(true, 'upload with mappings CSV (8 rows)');
await page.click('.btn-add-axis-inline');
await page.waitForSelector('.add-axis-modal input', { timeout: 10000 });
const axisInputs = await page.$$('.add-axis-modal input');
await axisInputs[0].fill('Roundness');
await axisInputs[1].fill('rond');
const numInputs = await page.$$('.add-axis-modal .axis-number-input');
await numInputs[0].fill('-100');
await numInputs[1].fill('100');
// Default Value stays at its initial 0 (its input lacks the number class).
await page.click('.add-axis-modal button[type="submit"]');
await page.waitForFunction(() =>
  [...document.querySelectorAll('.sidebar *')].some(
    el => el.children.length === 0 && el.textContent.trim() === 'ROND'
  ), { timeout: 60000 });
ok(true, 'new user axis ROND declared and visible in mappings');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
// Custom unregistered axis: the tag stays verbatim uppercase (studio
// semantics — only registered axes normalize to lowercase).
await page.waitForFunction(() =>
  [...document.querySelectorAll('.preview-tab *')].some(
    el => el.children.length === 0 && el.textContent.trim() === 'ROND'
  ), { timeout: 20000 });
ok(true, 'ROND user-axis slider in Preview');
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.preview-tab *')].some(
    el => el.children.length === 0 && el.textContent.trim() === '-100'
  )), 'ROND range from axis metadata (-100 … 100), not CSV-derived');

// ---- 12. downloads: config bundle + avar2-ready font -------------------------
console.log('12. downloads (config bundle + font)');
// Continues on section 11's dataset (crispy upload + CSV + authored ROND).
await page.click('button:text-is("Instances")');
await page.click('button:has-text("Config")');
const [cfgDownload] = await Promise.all([
  page.waitForEvent('download', { timeout: 20000 }),
  page.click('text=Export configuration…'),
]);
const bundle = JSON.parse(readFileSync(await cfgDownload.path(), 'utf8'));
ok(bundle.format === 'avar2-studio-config'
  && bundle.avar2_csv.includes('ROND')
  && bundle.source.avar2_out_columns.includes('XTRA'),
  'config bundle downloads with authored axis in the CSV');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-download', { timeout: 20000 });
await page.click('.preview-tab-download button');
await page.waitForSelector('.preview-export-modal', { timeout: 10000 });
const [fontDownload] = await Promise.all([
  page.waitForEvent('download', { timeout: 30000 }),
  page.click('button:has-text("Download")'),
]);
const fontBytes = readFileSync(await fontDownload.path());
ok(fontBytes.length > 10000
  && fontBytes[0] === 0 && fontBytes[1] === 1 && fontBytes[2] === 0 && fontBytes[3] === 0,
  `avar2-ready font downloads as valid TTF (${fontBytes.length} bytes)`);
// The plain download path leaves the export modal open — close it.
await page.click('.preview-export-modal button:has-text("Cancel")');
await page.waitForTimeout(500);

// ---- 13. export options (default location + hidden axes) -------------------
console.log('13. export options (default location + hidden axes)');
// Continues on section 11/12's dataset (crispy + CSV + authored ROND).
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
// Move wght off default so the default-location rebuild is observable.
await page.evaluate(() => {
  const rows = [...document.querySelectorAll('.preview-tab input[type=range]')];
  const wght = rows.find(s => {
    let p = s.parentElement;
    for (let i = 0; i < 5 && p; i++) {
      if (p.querySelectorAll('input[type=range]').length === 1 && p.textContent.includes('wght')) return true;
      p = p.parentElement;
    }
    return false;
  });
  if (!wght) throw new Error('wght slider not found');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(wght, 500);
  wght.dispatchEvent(new Event('input', { bubbles: true }));
});
await page.waitForTimeout(1000);
await page.click('.preview-tab-download button');
await page.waitForSelector('.preview-export-modal', { timeout: 10000 });
await page.click('.preview-export-default input[type=checkbox]');
await page.click('.preview-export-chips button:has-text("XTRA")');
const [optDownload] = await Promise.all([
  page.waitForEvent('download', { timeout: 90000 }),
  page.click('.preview-export-modal button:has-text("Download")'),
]);
const { parseFont } = await import('../src/fvar.js');
const optMeta = parseFont(new Uint8Array(readFileSync(await optDownload.path())));
const wghtAxis = optMeta.axes.find(a => a.tag === 'wght');
ok(wghtAxis && Math.abs(wghtAxis.default - 500) < 0.01,
  `default-location rebuild: wght default is the slider position (${wghtAxis?.default})`);
const xtraAxis = optMeta.axes.find(a => a.tag === 'XTRA');
ok(xtraAxis && (xtraAxis.flags & 0x0001) === 0x0001,
  'hidden-axes flag set on XTRA in the exported fvar');
ok(optMeta.axes.every(a => (a.flags & 0x0001) === 0 || a.tag === 'XTRA'),
  'only XTRA is hidden');

// ---- 14. STAT regeneration -------------------------------------------------
console.log('14. STAT regeneration in the exported font');
// Continues on section 13's downloaded font (default-location + XTRA hidden).
const { parseStat } = await import('../src/fvar.js');
const stat = parseStat(new Uint8Array(readFileSync(await optDownload.path())));
ok(stat !== null, 'STAT table present in the exported font');
const statTags = stat ? stat.axes.map(a => a.tag) : [];
ok(['wght', 'ROND'].every(t => statTags.includes(t)),
  `STAT axis records include normalized wght and custom ROND (${statTags})`);
ok(stat && stat.elidedFallbackNameID === 2,
  `elidedFallbackNameID = 2 (Regular), got ${stat?.elidedFallbackNameID}`);
ok(stat && stat.axes.every(a => optMeta.axes.some(f => f.tag === a.tag)),
  'every STAT axis record matches an fvar axis');
ok(stat && stat.values.every(v =>
  [1, 2, 3, 4].includes(v.format) &&
  (v.format === 4 ? v.values.every(r => r.axisIndex < stat.axes.length)
                  : v.axisIndex < stat.axes.length)),
  'all STAT value records are well-formed with valid axis indexes');
ok(stat && stat.values.some(v => (v.flags & 0x0002) === 0x0002),
  'at least one elidable (0x0002) STAT value record');
// (No modal close needed here — the options path closes the export
// modal itself on success, unlike section 12's plain download.)

// ---- 15. zip workspace (project zips in and out) ---------------------------
console.log('15. zip workspace (project zips in and out)');
const { zipSync, unzipSync, strToU8 } = await import('fflate');
const FX = (n) => join(dirname(fileURLToPath(import.meta.url)), 'fixtures', n);

// 15a: a .glyphs project zip uploads, and the workspace download round-trips.
writeFileSync('/tmp/e2e-glyphs-project.zip', zipSync({
  'WasmTest.glyphs': readFileSync(FIXTURE),
  'WasmTest-avar.csv': readFileSync(FIXTURE_CSV),
  'avar2-axis-metadata.json': strToU8(JSON.stringify({
    WGHT: { display_name: 'Weight', registered_tag: 'wght', min: 100, default: 100, max: 900, is_parametric: false },
  })),
}));
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', '/tmp/e2e-glyphs-project.zip');
// A source swap keeps the current tab; the family name only renders in
// the Instances tab's sidebar h2 — go there before waiting on it.
await page.click('button:text-is("Instances")');
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'WasmTest');
ok(true, 'glyphs project zip uploads and compiles');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.preview-tab *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'wght')),
  'CSV-derived wght user axis from the zipped -avar.csv');
await page.click('button:has-text("Config")');
const [wsDownload] = await Promise.all([
  page.waitForEvent('download', { timeout: 20000 }),
  page.click('button:has-text("Download workspace")'),
]);
const wsZip = unzipSync(new Uint8Array(readFileSync(await wsDownload.path())));
const wsNames = Object.keys(wsZip);
ok(wsNames.includes('WasmTest-avar.csv'), 'workspace zip carries the mappings CSV');
ok(wsNames.includes('.avar2-studio/axis-metadata.json'), 'workspace zip carries axis metadata');
const buildEntry = wsNames.find(n => n.startsWith('.avar2-studio/build/') && n.endsWith('.ttf'));
ok(!!buildEntry, 'workspace zip carries the current build as preview TTF');
ok(parseFont(new Uint8Array(wsZip[buildEntry])).axes.some(a => a.tag === 'wght'),
  'preview TTF parses with the wght axis');
ok(new TextDecoder().decode(wsZip['WasmTest-avar.csv']).split('\n', 1)[0].includes('WGHT'),
  'round-tripped CSV keeps the WGHT column');

// 15b: a designspace project zip loads from its baked preview TTF; the
// source itself can't recompile in the browser and says so honestly.
writeFileSync('/tmp/e2e-designspace-project.zip', zipSync({
  'WasmTest.designspace': readFileSync(FX('WasmTest.designspace')),
  'WasmTest-Regular.ufo/metainfo.plist': readFileSync(FX('WasmTest-Regular.ufo/metainfo.plist')),
  '.avar2-studio/build/WasmTest-VF.ttf': readFileSync(FX('WasmTest-VF.ttf')),
  'WasmTest-avar.csv': readFileSync(FIXTURE_CSV),
}));
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', '/tmp/e2e-designspace-project.zip');
await page.click('button:text-is("Instances")');
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'WasmTest');
ok(true, 'designspace project zip loads from its baked preview TTF');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
ok(true, 'preview specimen renders for the designspace project');
await page.click('header .btn-3d');
await page.waitForSelector('text=needs the full app', { timeout: 15000 });
ok(true, 'Rebuild on a designspace project fails with guidance, not a crash');

// 15c: a designspace zip WITHOUT a preview TTF is rejected with guidance.
writeFileSync('/tmp/e2e-designspace-no-ttf.zip', zipSync({
  'WasmTest.designspace': readFileSync(FX('WasmTest.designspace')),
  'WasmTest-Regular.ufo/metainfo.plist': readFileSync(FX('WasmTest-Regular.ufo/metainfo.plist')),
}));
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', '/tmp/e2e-designspace-no-ttf.zip');
await page.waitForFunction(() =>
  document.querySelector('.header-loading-msg')?.textContent.includes("can't be compiled in the browser"),
  { timeout: 20000 }
);
ok(true, 'designspace zip without a preview TTF fails with guidance');

// ---- 16. session persistence (auto-restore + forget) -----------------------
console.log('16. session persistence (auto-restore + forget)');
// Deterministic uploaded session to restore.
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', [FIXTURE, FIXTURE_CSV]);
await page.click('button:text-is("Instances")');
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'WasmTest');
ok(true, 'fresh upload to persist');
await page.reload({ waitUntil: 'load' });
await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'WasmTest');
ok(true, 'reload auto-restores the uploaded session (no re-upload)');
// The source text survived too: Rebuild still works on the restored session.
await page.click('header .btn-3d');
await page.waitForFunction(() =>
  !document.querySelector('header .btn-3d')?.textContent.includes('Building'));
ok((await page.textContent('.sidebar h2')) === 'WasmTest', 'Rebuild works on the restored session');
// Authoring state (the CSV-derived user axis) rehydrated, not just the font.
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
ok(await page.evaluate(() =>
  [...document.querySelectorAll('.preview-tab *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'wght')),
  'restored session keeps the CSV-derived wght user axis');
// Forget: unloads to the default example and stays forgotten after reload.
await page.click('button:has-text("Load Font")');
await page.click('button:has-text("Forget this project")');
await page.click('button:text-is("Instances")');
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'CrispyMini');
ok(true, 'forget returns to the default example');
await page.reload({ waitUntil: 'load' });
await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'CrispyMini');
ok(true, 'forget survives reload (stored session cleared)');

// ---- 17. coverage audit (missing corners on upload) -------------------------
console.log('17. coverage audit (missing corners on upload)');
const CRISPY_TEST = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'Crispy-test.glyphs');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY_TEST);
await page.click('button:text-is("Instances")');
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'Crispy', null, { timeout: 120000 });
const coverageBtn = await page.waitForSelector('button:has-text("Coverage")', { timeout: 20000 });
const badgeText = await coverageBtn.textContent();
ok(/\d+/.test(badgeText) && parseInt(badgeText.match(/\d+/)[0], 10) >= 3,
  `Coverage badge shows findings (${badgeText.trim()})`);
await coverageBtn.click();
const missingCorners = await page.$$eval('.load-font-menu .load-font-item-name',
  els => els.filter(e => e.textContent.includes('Missing corner')).length);
ok(missingCorners === 3, `findings list shows 3 Missing corner entries (${missingCorners})`);
const collapseItems = await page.$$eval('.load-font-menu .load-font-item',
  els => els.filter(e => e.textContent.includes('collapses')).length);
ok(collapseItems > 0, `findings include sweep collapse(s) from the probe (${collapseItems})`);
// First finding: (XTRA 47, XOPQ 700, YOPQ 1) — clicking jumps the preview.
await page.click('.load-font-menu .load-font-item');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
await page.waitForTimeout(800);
ok(await page.evaluate(() =>
  getComputedStyle(document.querySelector('.preview-tab-sample')).fontVariationSettings.includes('"XOPQ" 700')),
  'clicking a finding jumps the preview sliders to the corner');

// ---- 18. corner pinning ------------------------------------------------------
console.log('18. corner pinning');
// Continues on section 17's crispy-test upload. The real ghost is the
// (XOPQ▲, YOPQ▲) corner (47,700,300) — the one with a collapse finding.
const cornerFindings = async () => {
  const n = await page.$$eval('.load-font-menu .load-font-item-name',
    els => els.filter(e => e.textContent.includes('Missing corner')).length);
  await page.click('button:has-text("Coverage")'); // toggle closed
  return n;
};
const pinGhost = async () => {
  await page.click('button:has-text("Coverage")');
  await page.waitForSelector('.coverage-pin-btn', { timeout: 15000 });
  await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.coverage-finding-row')];
    const row = rows.find(r => r.textContent.includes('XOPQ▲') && r.textContent.includes('YOPQ▲'));
    if (!row) throw new Error('ghost corner finding not found');
    row.querySelector('.coverage-pin-btn').click();
  });
  await page.waitForFunction(() =>
    document.querySelector('.header-loading-msg')?.textContent === 'Corner pinned.',
    { timeout: 90000 }
  );
  await page.waitForTimeout(1500);
};
await pinGhost();
ok(true, 'ghost corner pinned (scaffold from the healthy edge)');
await page.click('button:has-text("Coverage")');
ok(await cornerFindings() < 3, 'pin cleared the corners it covers');
// Shape-level verification: the exported font renders real stem
// darkness at the pinned corner (it was 0 before — the ghost).
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-download button', { timeout: 20000 });
await page.click('.preview-tab-download button');
await page.waitForSelector('.preview-export-modal', { timeout: 10000 });
const [pinFontDl] = await Promise.all([
  page.waitForEvent('download', { timeout: 60000 }),
  page.click('.preview-export-modal button:has-text("Download")'),
]);
const pinFontPath = await pinFontDl.path();
const pinDarkness = Number(execFileSync(
  '/Users/agyei/Documents/avar2-studio/.venv/bin/python',
  ['-c', `
import sys
from PIL import Image, ImageDraw, ImageFont
f = ImageFont.truetype(sys.argv[1], 72)
f.set_variation_by_axes([47, 700, 300])
bbox = f.getbbox('a')
img = Image.new('L', (bbox[2]-bbox[0]+20, bbox[3]-bbox[1]+20), 255)
ImageDraw.Draw(img).text((10-bbox[0], 10-bbox[1]), 'a', font=f, fill=0)
print(sum(1 for p in img.get_flattened_data() if p < 128))
`, pinFontPath]
).toString().trim());
ok(pinDarkness > 1000, `pinned ghost corner renders with weight (darkness ${pinDarkness} at (47,700,300))`);
await page.click('.preview-export-modal button:has-text("Cancel")');
// Pin the rest (the intent-hairline corners — covered explicitly).
const pinRemaining = async () => {
  await page.click('button:has-text("Coverage")');
  await page.waitForSelector('.coverage-pin-btn', { timeout: 15000 });
  await page.click('.coverage-pin-btn');
  await page.waitForFunction(() =>
    document.querySelector('.header-loading-msg')?.textContent === 'Corner pinned.',
    { timeout: 90000 }
  );
  await page.waitForTimeout(1500);
};
await pinRemaining();
await pinRemaining();
await page.click('button:has-text("Coverage")');
ok(await cornerFindings() === 0, 'all corner findings pinned');

// ---- 19. rebuild keeps pins ---------------------------------------------------
console.log('19. rebuild keeps pins');
await page.click('header .btn-3d');
await page.waitForFunction(() =>
  !document.querySelector('header .btn-3d')?.textContent.includes('Building'),
  { timeout: 90000 }
);
await page.click('button:has-text("Coverage")');
ok(await cornerFindings() === 0, 'pins re-applied on rebuild (still no corner findings)');

// ---- 20. Space tab (Noordzij cube) ------------------------------------------
console.log('20. Space tab (Noordzij cube)');
// Fresh upload so ghost corners are present again.
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY_TEST);
await page.click('button:text-is("Instances")');
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'Crispy', null, { timeout: 120000 });
await page.click('button:text-is("Space")');
await page.waitForSelector('.space-chip', { timeout: 30000 });
ok((await page.$$('.space-chip')).length === 8, '8 corner chips render');
ok((await page.$$('.space-chip.ghost')).length === 3, '3 ghost chips (the missing corners)');
ok((await page.$$('.space-instance')).length === 2, 'named instances render as diamonds');
ok((await page.$$('.space-brace-dot')).length === 0, 'no brace dots on a brace-less font (correct classification)');
// Orbit by drag: chips move with the cube (before any chip click).
const chipLeftBefore = await page.evaluate(() => document.querySelector('.space-chip').style.left);
await page.evaluate(() => {
  const cv = document.querySelector('.space-cube');
  const r = cv.getBoundingClientRect();
  cv.dispatchEvent(new MouseEvent('mousemove', { clientX: r.x + 200, clientY: r.y + 200, buttons: 1, bubbles: true }));
  cv.dispatchEvent(new MouseEvent('mousemove', { clientX: r.x + 260, clientY: r.y + 220, buttons: 1, bubbles: true }));
});
const chipLeftAfter = await page.evaluate(() => document.querySelector('.space-chip').style.left);
ok(chipLeftBefore !== chipLeftAfter, 'drag orbits the cube (chips move)');
// Chip click applies the location to the in-tab probe (no navigation).
await page.evaluate(() => {
  const chips = [...document.querySelectorAll('.space-chip:not(.ghost)')];
  chips[chips.length - 1].click();
});
await page.waitForTimeout(600);
ok(await page.$('.space-cube') !== null, 'chip click stays in the Space tab');
ok((await page.textContent('.space-side-label')).includes('XTRA'), 'probe shows the clicked location');
// Pin a ghost from its chip → the chip turns into a red "pinned" chip.
await page.evaluate(() => {
  const chips = [...document.querySelectorAll('.space-chip.ghost')];
  chips[0].querySelector('.space-chip-pin').click();
});
await page.waitForFunction(() => document.querySelectorAll('.space-chip.pinned').length > 0, { timeout: 90000 });
ok(true, 'pin from a ghost chip completes');
ok((await page.textContent('.space-chip.pinned')).includes('pinned'), 'pinned chip shows the red pinned label');

// ---- 21. drop out-of-range sources ----------------------------------------
console.log('21. drop out-of-range sources');
// The full braced Crispy has brace sources OUTSIDE the axis box
// (XTRA +2.00 etc.) — fontc extrapolates them, Glyphs.app/fontmake drop
// them. The Coverage action applies the drop semantics in-browser.
const CRISPY_BRACED = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'Crispy-braced.glyphs');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY_BRACED);
await page.click('button:text-is("Instances")');
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'CrispyBraced', null, { timeout: 240000 });
await page.click('button:has-text("Coverage")');
await page.waitForSelector('.load-font-menu', { timeout: 20000 });
const oorBefore = await page.$$eval('.load-font-menu .load-font-item-name',
  els => els.filter(e => e.textContent.includes('Out-of-range source')).length);
ok(oorBefore > 0, `out-of-range findings present after upload (${oorBefore})`);
await page.click('button:has-text("Drop out-of-range sources")');
await page.waitForFunction(() =>
  document.querySelector('.header-loading-msg')?.textContent === 'Out-of-range sources dropped.',
  { timeout: 120000 }
);
await page.waitForTimeout(1500);
await page.click('button:has-text("Coverage")');
await page.waitForSelector('.load-font-menu', { timeout: 20000 });
const oorAfter = await page.$$eval('.load-font-menu .load-font-item-name',
  els => els.filter(e => e.textContent.includes('Out-of-range source')).length);
ok(oorAfter === 0, `drop cleared the out-of-range findings (${oorAfter} left)`);
await page.click('button:has-text("Coverage")'); // close
// The default instance must be intact: download the font and check the
// default 'a' metrics/outline — the peak-zeroing failure mode gave a
// default advance of 2154 (vs 166). (A Pillow render can't be used
// here: Crispy's default is a 1-unit hairline — zero dark pixels at
// any size.)
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-download button', { timeout: 20000 });
await page.click('.preview-tab-download button');
await page.waitForSelector('.preview-export-modal', { timeout: 10000 });
const [clampFontDl] = await Promise.all([
  page.waitForEvent('download', { timeout: 60000 }),
  page.click('.preview-export-modal button:has-text("Download")'),
]);
const clampFontPath = await clampFontDl.path();
const clampMetrics = execFileSync(
  '/Users/agyei/Documents/avar2-studio/.venv/bin/python',
  ['-c', `
import sys
from fontTools.ttLib import TTFont
f = TTFont(sys.argv[1])
g = f['glyf']['a']
g.recalcBounds(f['glyf'])
print(f['hmtx'].metrics['a'], (g.xMin, g.yMin, g.xMax, g.yMax))
`, clampFontPath]
).toString().trim();
ok(clampMetrics === '(166, 34) (34, 0, 129, 1200)',
  `default instance intact after the drop (${clampMetrics})`);
await page.click('.preview-export-modal button:has-text("Cancel")');

// ---- 22. SPAC transform toggles on an upload --------------------------------
console.log('22. SPAC transform toggles on an upload');
// Continues on section 21's CrispyBraced upload (its clamp flag is set,
// so the toggle also exercises the rebuild pipeline order: compile →
// pins → clamp → transforms). Uploads previously showed only Grade in
// this menu.
await page.click('button:has-text("Transforms")');
await page.waitForSelector('.transform-row', { timeout: 15000 });
const tNames = await page.$$eval('.transform-row .transform-name', els => els.map(e => e.textContent.trim()));
ok(tNames.includes('Spacing — uniform (gftools)') && tNames.includes('Spacing — width-aware'),
  `Transforms menu lists the SPAC transforms on an upload (${tNames.join(' | ')})`);
// Toggle width-aware on → the font rebuilds with SPAC injected.
await page.click('.transform-row:has-text("Spacing — width-aware") input[type=checkbox]');
await page.keyboard.press('Escape'); // close the Radix dropdown — outside clicks are swallowed by it
await page.click('button:text-is("Preview")');
await page.waitForFunction(() => {
  const groups = [...document.querySelectorAll('.preview-axis-group')];
  return groups.some(g => [...g.querySelectorAll('.axis-tag')].some(x => x.textContent.trim() === 'SPAC'));
}, { timeout: 240000 });
ok(true, 'SPAC slider appears after toggling width-aware on');
// The exported font carries the SPAC fvar axis.
await page.waitForSelector('.preview-tab-download button', { timeout: 20000 });
await page.click('.preview-tab-download button');
await page.waitForSelector('.preview-export-modal', { timeout: 10000 });
const [spacFontDl] = await Promise.all([
  page.waitForEvent('download', { timeout: 60000 }),
  page.click('.preview-export-modal button:has-text("Download")'),
]);
const spacFontPath = await spacFontDl.path();
const fontAxes = (p) => execFileSync(
  '/Users/agyei/Documents/avar2-studio/.venv/bin/python',
  ['-c', `
import sys
from fontTools.ttLib import TTFont
print(','.join(a.axisTag for a in TTFont(sys.argv[1])['fvar'].axes))
`, p]
).toString().trim();
ok(fontAxes(spacFontPath).includes('SPAC'), `exported font carries the SPAC fvar axis (${fontAxes(spacFontPath)})`);
await page.click('.preview-export-modal button:has-text("Cancel")');
// Toggle off → rebuild without SPAC → the fvar axis is gone again.
await page.click('button:has-text("Transforms")');
await page.waitForSelector('.transform-row', { timeout: 15000 });
await page.click('.transform-row:has-text("Spacing — width-aware") input[type=checkbox]');
await page.keyboard.press('Escape'); // close the Radix dropdown
await page.waitForFunction(() =>
  document.querySelector('header .btn-3d')?.textContent.includes('Building'),
  { timeout: 15000 }).catch(() => null);
await page.waitForFunction(() =>
  !document.querySelector('header .btn-3d')?.textContent.includes('Building'),
  { timeout: 240000 });
await page.waitForTimeout(1000);
await page.waitForSelector('.preview-tab-download button', { timeout: 20000 });
await page.click('.preview-tab-download button');
await page.waitForSelector('.preview-export-modal', { timeout: 10000 });
const [spacOffFontDl] = await Promise.all([
  page.waitForEvent('download', { timeout: 60000 }),
  page.click('.preview-export-modal button:has-text("Download")'),
]);
const spacOffFontPath = await spacOffFontDl.path();
ok(!fontAxes(spacOffFontPath).includes('SPAC'), `SPAC fvar axis removed after toggling off (${fontAxes(spacOffFontPath)})`);
await page.click('.preview-export-modal button:has-text("Cancel")');

// ---- 23. rebuild re-applies bundle state (control axes + grade + SPAC) ------
console.log('23. rebuild re-applies bundle state (control axes + grade + SPAC)');
// A bundle import bakes control axes / grade / transforms into the font
// BYTES; the studio state lives beside the source. Rebuild must re-apply
// that state — before this fix the recompiled font lost crbr/GRAD/SPAC.
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY_GLYPHS);
await page.click('button:text-is("Instances")');
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'Crispy Mini', null, { timeout: 240000 });
await page.click('button:has-text("Config")');
await page.click('text=Import configuration…');
await page.setInputFiles('.config-dropdown input[type=file]', TEST_BUNDLE);
await page.waitForSelector('.import-config-confirm:not([disabled])', { timeout: 30000 });
await page.click('.import-config-confirm');
await page.waitForFunction(() => !document.querySelector('.import-config-confirm'), { timeout: 120000 });
ok(true, 'bundle imported onto a fresh CrispyMini upload');
await page.click('header .btn-3d');
await page.waitForFunction(() =>
  document.querySelector('header .btn-3d')?.textContent.includes('Building'),
  { timeout: 15000 }).catch(() => null);
await page.waitForFunction(() =>
  !document.querySelector('header .btn-3d')?.textContent.includes('Building'),
  { timeout: 300000 });
ok(true, 'rebuild with full studio state completed');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-download button', { timeout: 20000 });
await page.click('.preview-tab-download button');
await page.waitForSelector('.preview-export-modal', { timeout: 10000 });
const [rebuiltFontDl] = await Promise.all([
  page.waitForEvent('download', { timeout: 60000 }),
  page.click('.preview-export-modal button:has-text("Download")'),
]);
const rebuiltAxes = fontAxes(await rebuiltFontDl.path());
ok(rebuiltAxes.includes('crbr'), `control axis crbr survives REBUILD (${rebuiltAxes})`);
ok(rebuiltAxes.includes('GRAD'), `GRAD axis survives REBUILD (${rebuiltAxes})`);
ok(rebuiltAxes.includes('SPAC'), `SPAC axis survives REBUILD (${rebuiltAxes})`);
await page.click('.preview-export-modal button:has-text("Cancel")');

await browser.close();
console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
