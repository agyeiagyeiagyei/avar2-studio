/**
 * Extract the built font bytes + axes from a live session (via the
 * blob: resource the FontFace loaded), so avar2-eval.js can be run on
 * the exact bytes in node. Also captures ALL console traffic.
 */
import { chromium } from 'playwright-core';
import { writeFileSync } from 'node:fs';

const BASE = process.env.STATIC_URL || 'https://agyeiagyeiagyei.github.io/avar2-studio/';
const GLYPHS = '/Users/agyei/Documents/Crispy/sources/crispy-demo-14aug.glyphs';
const CONFIG = '/Users/agyei/Downloads/crispy-demo-14aug-avar2studio-fixed.json';
const OUT = process.env.OUT_TTF || '/tmp/crispy-demo-live.ttf';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('console', m => console.log(`[${m.type()}]`, m.text()));
page.on('pageerror', e => console.error('[pageerror]', e.message));
// Capture every blob handed to createObjectURL — the built font rides one.
await page.addInitScript(() => {
  window.__blobs = [];
  const orig = URL.createObjectURL.bind(URL);
  URL.createObjectURL = (blob) => {
    const url = orig(blob);
    window.__blobs.push({ url, blob });
    return url;
  };
});

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

// Trigger the reflection path so any warn fires while we listen.
await page.click('button:text-is("Preview")');
await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
await page.evaluate(() => {
  const groups = [...document.querySelectorAll('.axis-control')];
  const g = groups.find(el => [...el.querySelectorAll('.axis-tag')].some(x => x.textContent.trim() === 'wght'));
  const input = g && g.querySelector('input[type=range]');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, '900');
  input.dispatchEvent(new Event('input', { bubbles: true }));
});
await page.waitForTimeout(1200);

// Pull the newest captured blob — the built font's object URL.
const fontB64 = await page.evaluate(async () => {
  const entries = (window.__blobs || []).filter(e => e.blob && e.blob.size > 1000);
  if (!entries.length) return null;
  const buf = await entries[entries.length - 1].blob.arrayBuffer();
  let s = '';
  const u8 = new Uint8Array(buf);
  for (let i = 0; i < u8.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  }
  return btoa(s);
});
await browser.close();
if (!fontB64) { console.error('NO blob resource found'); process.exit(1); }
writeFileSync(OUT, Buffer.from(fontB64, 'base64'));
console.log(`\nwrote ${OUT} (${Buffer.from(fontB64, 'base64').length} bytes)`);
