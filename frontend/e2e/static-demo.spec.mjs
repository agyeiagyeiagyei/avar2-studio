/**
 * End-to-end tests for the static demo (GitHub Pages bundle, no backend).
 *
 * Covers the full surface:
 *   1. static boot (banner, header actions, CrispyMini rows, no Rebuild)
 *   2. Transforms SPAC toggle (baked variant swap)
 *   3. Load Font dataset switch (Roboto Delta Mini)
 *   4. .glyphs upload compiled in-browser (fontc-wasm worker)
 *   5. Rebuild on an uploaded source
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

await browser.close();
console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
