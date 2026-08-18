/**
 * Grade behavior test on the user's own font (crispy-demo-14aug + the
 * fixed 8-row config), driving the real UI:
 *
 *  A. Grade a MID-BOX instance (Bold Normal, 835.3/414.6/233.3):
 *     GRAD +10 must visibly darken the specimen AND hold its advance
 *     width (the whole point of a grade — no reflow).
 *  B. Grade the CEILING instance (Control Test, 1665/700/275) at the
 *     slider max: XOPQ/YOPQ have zero dark headroom, so GRAD +10 must
 *     now be a clean NO-OP — regression for the clamped-driver bug
 *     where XTRA still moved and the "grade" was a pure condense that
 *     deformed the spacing.
 *
 * Usage: [STATIC_URL=...] node e2e/grade-live.spec.mjs
 */
import { chromium } from 'playwright-core';

const BASE = process.env.STATIC_URL || 'http://localhost:8123';
const GLYPHS = process.env.GRADE_GLYPHS || '/Users/agyei/Documents/Crispy/sources/crispy-demo-14aug.glyphs';
const CONFIG = process.env.GRADE_CONFIG || '/Users/agyei/Downloads/crispy-demo-14aug-avar2studio-fixed.json';

let failures = 0;
const ok = (cond, label) => {
  console.log(`${cond ? '  ✓' : '  ✗ FAIL'} ${label}`);
  if (!cond) failures++;
};

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('pageerror', e => console.error('[pageerror]', e.message));
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
const specimenShot = async () =>
  page.screenshot({ clip: await (await page.$('.preview-tab-sample')).boundingBox() });
const specimenWidth = () => page.evaluate(() => {
  const el = document.querySelector('.preview-tab-sample');
  const range = document.createRange();
  range.selectNodeContents(el);
  return range.getBoundingClientRect().width;
});
const waitSettled = async () => {
  await page.waitForFunction(() => !document.querySelector('.building-indicator'), { timeout: 120000 }).catch(() => {});
  await sleep(2000);
};
// Grade one instance via its row badge; pct in percent. Assumes the
// Instances tab is active.
const gradeInstance = async (name, pct) => {
  await page.evaluate(async ([n, v]) => {
    const rows = [...document.querySelectorAll('.instance-row')];
    const row = rows.find(r => r.textContent.includes(n));
    row.querySelector('.grade-badge').click();
    await new Promise(r => setTimeout(r, 250));
    const input = row.querySelector('.grade-popover-input');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, v);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 100));
    row.querySelector('.grade-popover-save').click();
  }, [name, String(pct)]);
  await waitSettled();
};
const removeGrade = async (name) => {
  await page.evaluate(async (n) => {
    const rows = [...document.querySelectorAll('.instance-row')];
    const row = rows.find(r => r.textContent.includes(n));
    row.querySelector('.grade-badge').click();
    await new Promise(r => setTimeout(r, 250));
    row.querySelector('.grade-popover-remove').click();
  }, name);
  await waitSettled();
};

console.log(`target: ${BASE}`);
await page.goto(BASE, { waitUntil: 'load', timeout: 30000 });
await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
await page.click('button:has-text("Load Font")');
await page.setInputFiles('.load-font-dropdown input[type=file]', GLYPHS);
await page.waitForFunction(
  () => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'),
  { timeout: 120000 }
);
await page.click('button:has-text("Config")');
await page.click('text=Import configuration…');
await page.setInputFiles('.config-dropdown input[type=file]', CONFIG);
await page.waitForSelector('.import-config-confirm:not([disabled])', { timeout: 30000 });
await page.click('.import-config-confirm');
await page.waitForFunction(() => !document.querySelector('.import-config-confirm'), { timeout: 90000 });
console.log('setup: font + config loaded');

// Enable the Grade transform.
await page.click('button:has-text("Transforms")');
await page.evaluate(() => {
  const rows = [...document.querySelectorAll('.transform-row')];
  const row = rows.find(r => r.querySelector('.transform-name')?.textContent.trim() === 'Grade');
  row?.querySelector('input[type=checkbox]')?.click();
});
await sleep(400);
await page.keyboard.press('Escape');
await waitSettled();

// Row-render regression: a graded row's outline must be IDENTICAL to
// its ungraded self (rows render at GRAD 0 — the chip says 0; an
// earlier revision injected GRAD max into row previews, which showed
// closed counters under a chip claiming 0).
const rowShot = async (name) => {
  const handle = await page.evaluateHandle((n) => {
    const rows = [...document.querySelectorAll('.instance-row')];
    const el = rows.find(r => r.textContent.includes(n))?.querySelector('.preview-text');
    el?.scrollIntoView({ block: 'center' });
    return el;
  }, name);
  await sleep(400);
  return page.screenshot({ clip: await handle.asElement().boundingBox() });
};
const rowUngraded = await rowShot('Bold Normal');

console.log('A. mid-box instance (Bold Normal) at 30%');
await gradeInstance('Bold Normal', 30);
const rowGraded = await rowShot('Bold Normal');
ok(rowUngraded.equals(rowGraded), 'graded row renders identical to ungraded (GRAD 0)');
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
await sleep(800);
const midAt0 = await specimenShot();
const widthAt0 = await specimenWidth();
await setSlider('GRAD', 10);
await sleep(800);
const midAt10 = await specimenShot();
const widthAt10 = await specimenWidth();
ok(!midAt0.equals(midAt10), 'GRAD +10 visibly darkens the specimen (mid-box grade)');
ok(Math.abs(widthAt10 - widthAt0) < 1.5, `advance held: width ${widthAt0.toFixed(1)} -> ${widthAt10.toFixed(1)}`);
await setSlider('GRAD', 0);
await sleep(500);

console.log('B. ceiling instance (Control Test) at the slider max');
await page.click('button:text-is("Instances")');
await sleep(400);
await removeGrade('Bold Normal');
await gradeInstance('Control Test', 200);
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
await sleep(800);
const ceilAt0 = await specimenShot();
const ceilWidth0 = await specimenWidth();
await setSlider('GRAD', 10);
await sleep(800);
const ceilAt10 = await specimenShot();
const ceilWidth10 = await specimenWidth();
ok(ceilAt0.equals(ceilAt10), 'GRAD +10 on a zero-headroom grade is a clean no-op (no condense/deform)');
ok(Math.abs(ceilWidth10 - ceilWidth0) < 1.5, `spacing intact: width ${ceilWidth0.toFixed(1)} -> ${ceilWidth10.toFixed(1)}`);

await browser.close();
console.log(failures ? `\n${failures} FAILURE(S)` : '\nall passed');
process.exit(failures ? 1 : 0);
