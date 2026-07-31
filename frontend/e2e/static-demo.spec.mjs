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
 *
 * Usage:
 *   STATIC_URL=http://localhost:8123 node e2e/static-demo.spec.mjs
 *
 * Requires: dist-pages built (npx vite build --base=./ --outDir
 * dist-pages) and served statically, plus system Chrome (channel:
 * 'chrome' — same as the repo's screenshot harness).
 */

import { chromium } from 'playwright-core';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

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

const sleep = (ms) => page.waitForTimeout(ms);

// ---- 1. static boot -------------------------------------------------------
console.log('1. static boot');
await page.goto(BASE, { waitUntil: 'load', timeout: 30000 });
await page.waitForSelector('.static-demo-banner', { timeout: 20000 });
ok(await page.isVisible('.static-demo-banner'), 'static banner shows');
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
    el.children.length === 0 && el.textContent.trim() === 'WGHT')),
  'USER AXES shows the CSV-derived WGHT axis');
// The parametric (compiled) wght follows the mapping: with WGHT at its
// default (100 → wght 400), the reflected parametric wght reads 400.
await page.waitForFunction(() => {
  const rows = [...document.querySelectorAll('.preview-tab *')];
  return rows.some(el => el.children.length === 0 && el.textContent.trim() === '400');
}, { timeout: 20000 });
ok(true, 'parametric wght reflects mapping (400 at WGHT default)');

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
    el.children.length === 0 && el.textContent.trim() === 'OPSZ')),
  'OPSZ user axis appears after mappings import');

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

await browser.close();
console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
