/**
 * Production smoke: the LIVE Pages demo + the LIVE fontra-embed bundle.
 * Boots the demo, uploads CrispyMini.glyphs, declares a control axis,
 * opens the glyph editor, asserts the iframe is the production embed URL
 * and the editor controller starts inside it. Not committed — run after
 * deploys:  node e2e/.prod-smoke.mjs
 */
import { chromium } from 'playwright-core';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const DEMO = 'https://agyeiagyeiagyei.github.io/avar2-studio/';
const EMBED = 'https://agyeiagyeiagyei.github.io/fontra-embed/editor.html';
const CRISPY = join(HERE, '../../examples/crispy-mini/sources/CrispyMini.glyphs');

let failures = 0;
// waitForFunction does NOT await async predicates (a returned Promise is
// truthy → instant false pass) — poll page-side async conditions Node-side.
const waitForAsync = async (fn, { timeout = 120000, every = 250 } = {}) => {
  for (const deadline = Date.now() + timeout; ;) {
    if (await fn()) return;
    if (Date.now() > deadline) throw new Error('waitForAsync timed out');
    await page.waitForTimeout(every);
  }
};
const ok = (cond, label) => { console.log(`${cond ? '  ✓' : '  ✗ FAIL'} ${label}`); if (!cond) failures++; };

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('pageerror', e => console.error('[pageerror]', (e.stack || e.message).slice(0, 500)));

await page.goto(DEMO, { waitUntil: 'load', timeout: 30000 });
await page.waitForSelector('button:has-text("Load Font")', { timeout: 30000 });
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'), null, { timeout: 120000 });
ok(true, 'live demo boots on Crispy Mini');

await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY);
// EXACTLY the e2e's wait: glyphs_path change only.
const gpBefore = await page.evaluate(async () => (await window.__avar2api.health()).glyphs_path);
await waitForAsync(async () =>
  (await page.evaluate(async () => (await window.__avar2api.health()).glyphs_path)) !== gpBefore);
ok(true, 'source uploaded and built in-browser');

await page.evaluate(async () => {
  await window.__avar2api.createControlAxis({ tag: 'crbr', display_name: 'Crossbar', min: 0, default: 0, max: 100 });
  await window.__avar2api.controlAxisLayerDelta('crbr', {
    add: [{ glyph: 'e', location: { XTRA: 1000, crbr: 100 } }],
  });
});
await page.reload({ waitUntil: 'load' });
await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'), null, { timeout: 120000 });
await page.waitForFunction(() =>
  [...document.querySelectorAll('.control-axes *')].some(el =>
    el.children.length === 0 && el.textContent.trim() === 'crbr'), null, { timeout: 30000 });
ok(true, 'control axis persisted (session)');

await page.evaluate(() => {
  const row = [...document.querySelectorAll('.control-axis-row')]
    .find(r => r.querySelector('.control-axis-tag')?.textContent.trim() === 'crbr');
  row.querySelector('.control-axis-header').click();
});
await page.waitForSelector('.layers-glyph-header', { timeout: 10000 });
await page.click('.layers-glyph-header');
await page.waitForSelector('.layer-open-fontra', { timeout: 10000 });
await page.click('.layer-open-fontra');

await page.waitForSelector('.fontra-editor-iframe', { timeout: 15000 });
const src = await page.locator('.fontra-editor-iframe').getAttribute('src');
ok(src.startsWith(EMBED), `iframe src is the production embed (${src.slice(0, 60)}…)`);
await page.waitForFunction(() => !!window.__avar2EditorBridge, null, { timeout: 30000 });
let frame = null;
for (let i = 0; i < 120 && !frame; i++) {
  frame = page.frames().find(f => f.url().startsWith(EMBED)) || null;
  if (!frame) await page.waitForTimeout(250);
}
ok(!!frame, 'embed frame attached');
await frame.waitForFunction(() => !!window.editorController?.fontController, null, { timeout: 60000 });
ok(true, 'editor controller started in the LIVE embed');
await frame.waitForFunction(() => window.editorController?.sceneSettings?.selectedGlyphName === 'e', null, { timeout: 30000 });
ok(true, "editor sits on glyph 'e' in edit mode");

await browser.close();
console.log(failures ? `\n${failures} FAILURES` : '\nPRODUCTION SMOKE: ALL PASS');
process.exit(failures ? 1 : 0);
