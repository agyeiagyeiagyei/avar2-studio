/**
 * Parametric-reflection test against the LIVE deployed site, driving the
 * user's exact setup: crispy-demo-14aug.glyphs + the fixed 8-row config.
 *
 * Asserts: moving wght/wdth makes the (non-overridden) parametric
 * sliders' DISPLAYED values follow the avar2 mapping. Then re-checks
 * with width-aware SPAC enabled and with a graded instance (each grows
 * the fvar after the avar table was written — the two suspects).
 */
import { chromium } from 'playwright-core';

const BASE = process.env.STATIC_URL || 'https://agyeiagyeiagyei.github.io/avar2-studio/';
const GLYPHS = '/Users/agyei/Documents/Crispy/sources/crispy-demo-14aug.glyphs';
const CONFIG = '/Users/agyei/Downloads/crispy-demo-14aug-avar2studio-fixed.json';

let failures = 0;
const ok = (cond, label) => {
  console.log(`${cond ? '  ✓' : '  ✗ FAIL'} ${label}`);
  if (!cond) failures++;
};

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('pageerror', e => console.error('[pageerror]', e.message));
page.on('console', m => { if (m.type() === 'error') console.error('[console.error]', m.text()); });
const sleep = (ms) => page.waitForTimeout(ms);

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
// The displayed value of each parametric slider (range input value).
const paramValues = () => page.evaluate(() => {
  const out = {};
  for (const g of document.querySelectorAll('.axis-control')) {
    const tag = g.querySelector('.axis-tag')?.textContent.trim();
    const input = g.querySelector('input[type=range]');
    if (tag && input) out[tag] = parseFloat(input.value);
  }
  return out;
});

console.log(`target: ${BASE}`);
await page.goto(BASE, { waitUntil: 'load', timeout: 30000 });
await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });

console.log('1. upload crispy-demo-14aug.glyphs');
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', GLYPHS);
await page.waitForFunction(
  () => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'),
  { timeout: 120000 }
);
ok(true, 'compiled in-browser');

console.log('2. import the fixed 8-row config');
await page.click('button:has-text("Config")');
await page.click('text=Import configuration…');
await page.setInputFiles('.config-dropdown input[type=file]', CONFIG);
await page.waitForSelector('.import-config-confirm:not([disabled])', { timeout: 30000 });
await page.click('.import-config-confirm');
await page.waitForFunction(() => !document.querySelector('.import-config-confirm'), { timeout: 90000 });
ok(true, 'config applied');

console.log('3. reflection at defaults, then wght 900');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
await sleep(800);
const atDefault = await paramValues();
console.log('   sliders at defaults:', JSON.stringify(atDefault));
await setSlider('wght', 900);
await sleep(800); // 120ms debounce + evaluation
const atBold = await paramValues();
console.log('   sliders at wght 900:', JSON.stringify(atBold));
// wght 900 / wdth 5 maps to XOPQ 143.5, YOPQ 193.6 in the 8-row grid.
ok(atBold.XOPQ > atDefault.XOPQ + 50, `XOPQ reflects wght (${atDefault.XOPQ} -> ${atBold.XOPQ})`);
ok(atBold.YOPQ > atDefault.YOPQ + 50, `YOPQ reflects wght (${atDefault.YOPQ} -> ${atBold.YOPQ})`);

console.log('4. wdth 200 (bold wide corner)');
await setSlider('wdth', 200);
await sleep(800);
const atWide = await paramValues();
console.log('   sliders at 900/200:', JSON.stringify(atWide));
ok(atWide.XTRA > 1000, `XTRA reflects wdth (${atBold.XTRA} -> ${atWide.XTRA}, expect ~1665)`);
ok(atWide.XOPQ > 500, `XOPQ at bold-wide corner (${atWide.XOPQ}, expect ~700)`);

console.log('5. reset, enable width-aware SPAC, re-check reflection');
await page.click('button:has-text("Reset")').catch(() => {});
await sleep(400);
await page.click('button:has-text("Transforms")');
await page.evaluate(() => {
  const rows = [...document.querySelectorAll('.transform-row')];
  const row = rows.find(r => r.querySelector('.transform-name')?.textContent.trim() === 'Spacing — width-aware');
  row?.querySelector('input[type=checkbox]')?.click();
});
await sleep(500);
await page.keyboard.press('Escape');
// wait for the rebuild to settle
await page.waitForFunction(
  () => !document.querySelector('.building-indicator'),
  { timeout: 120000 }
).catch(() => {});
await sleep(2500);
await page.click('button:text-is("Preview")').catch(() => {});
await sleep(800);
const spacDefault = await paramValues();
console.log('   sliders after SPAC on:', JSON.stringify(spacDefault));
ok('SPAC' in spacDefault, 'SPAC slider present after toggle');
await setSlider('wght', 900);
await sleep(800);
const spacBold = await paramValues();
console.log('   sliders at wght 900 (SPAC on):', JSON.stringify(spacBold));
ok(spacBold.XOPQ > spacDefault.XOPQ + 50, `reflection still live with SPAC in fvar (XOPQ ${spacDefault.XOPQ} -> ${spacBold.XOPQ})`);

console.log('6. grade an instance, re-check reflection (GRAD in fvar)');
await page.click('button:text-is("Instances")');
await sleep(400);
// enable the Grade transform toggle
await page.click('button:has-text("Transforms")');
await page.evaluate(() => {
  const rows = [...document.querySelectorAll('.transform-row')];
  const row = rows.find(r => r.querySelector('.transform-name')?.textContent.trim() === 'Grade');
  row?.querySelector('input[type=checkbox]')?.click();
});
await sleep(500);
await page.keyboard.press('Escape');
await sleep(1500);
// grade the first instance via its badge
const badge = await page.$('.grade-badge');
if (badge) {
  await badge.click();
  await sleep(300);
  await page.click('.grade-popover-save').catch(() => {});
  await page.waitForFunction(
    () => !document.querySelector('.building-indicator'),
    { timeout: 120000 }
  ).catch(() => {});
  await sleep(2500);
  await page.click('button:text-is("Preview")');
  await sleep(800);
  const gradeDefault = await paramValues();
  console.log('   sliders after grade:', JSON.stringify(gradeDefault));
  ok('GRAD' in gradeDefault, 'GRAD slider present after grading');
  await setSlider('wght', 900);
  await sleep(800);
  const gradeBold = await paramValues();
  console.log('   sliders at wght 900 (GRAD on):', JSON.stringify(gradeBold));
  ok(gradeBold.XOPQ > gradeDefault.XOPQ + 50, `reflection still live with GRAD in fvar (XOPQ ${gradeDefault.XOPQ} -> ${gradeBold.XOPQ})`);
} else {
  console.log('   (no grade badge found — skipping grade leg)');
}

await browser.close();
console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
