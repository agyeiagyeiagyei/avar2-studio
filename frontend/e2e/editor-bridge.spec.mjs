/**
 * End-to-end tests for the static-demo glyph editor bridge (Phases 2+3):
 * the fontra-embed editor iframed into the static studio, edits flowing
 * back into the control-axis sidecar, coalesced rebuilds, guards, and
 * persistence (session reload + workspace zip round-trip).
 *
 * Covers:
 *   0. seed interpolation parity (var-model.js vs a fontTools fixture)
 *   1. open the editor on a control-axis layer (glyph + location)
 *   1b. focused studio UI: drawing tools/sidebar panels hidden inside the
 *       editor iframe, sources list narrowed to the session's brace layers
 *       (the shim now lives in the fontra-embed bundle, Phase 4a)
 *   2. a node nudge reaches the parent as editFinal and is stored as the
 *      layer's drawn outline; the rebuilt font renders the drawing
 *   3. guards: master-layer edit rejected, computed (target) layer
 *      rejected, structural edit rejected
 *   4. persistence: reload keeps the drawing; workspace zip carries it;
 *      loading the zip back keeps it
 *   4b. metrics HUD: reference numbers match a fontTools oracle on the
 *       pristine compile; the live row tracks a nudge; refs stay fixed
 *   5. two-tab lock: a second tab shows the banner
 *
 * Usage:
 *   node e2e/editor-bridge.spec.mjs
 *
 * Self-contained: serves frontend/dist-pages (build first:
 * `npx vite build --base=./ --outDir dist-pages`) and the fontra-embed
 * bundle (~/Documents/fontra-embed/dist — `npm run build` there) on
 * ephemeral localhost ports, drives system Chrome (playwright-core).
 */

import { chromium } from 'playwright-core';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const STUDIO_DIST = join(HERE, '..', 'dist-pages');
const EMBED_DIST = process.env.FONTRA_EMBED_DIST
  || resolve(process.env.HOME, 'Documents/fontra-embed/dist');
const CRISPY_GLYPHS = join(HERE, '../../examples/crispy-mini/sources/CrispyMini.glyphs');

const CONTENT_TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.ico': 'image/x-icon', '.json': 'application/json', '.csv': 'text/csv',
  '.woff2': 'font/woff2', '.txt': 'text/plain', '.wasm': 'application/wasm',
  '.zip': 'application/zip', '.ttf': 'font/ttf',
};

function serve(root) {
  const server = createServer(async (req, res) => {
    try {
      let urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      if (urlPath.endsWith('/')) urlPath += 'index.html';
      const filePath = join(root, urlPath);
      if (!filePath.startsWith(root)) throw new Error('path escape');
      const data = readFileSync(filePath);
      res.writeHead(200, {
        'content-type': CONTENT_TYPES[filePath.slice(filePath.lastIndexOf('.'))] || 'application/octet-stream',
        // The studio page iframes the embed cross-origin.
        'access-control-allow-origin': '*',
      });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end('not found');
    }
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

let failures = 0;
const ok = (cond, label) => {
  console.log(`${cond ? '  ✓' : '  ✗ FAIL'} ${label}`);
  if (!cond) failures++;
};

// ---- 0. seed interpolation parity (Node-side, fontTools fixture) ----------
console.log('0. seed interpolation parity (var-model.js vs fontTools fixture)');
{
  const { VariationModel, normalizeValue } = await import('../src/var-model.js');
  const fx = JSON.parse(readFileSync(join(HERE, 'fixtures', 'seed-oracle.json'), 'utf8'));
  const norm = (loc) => Object.fromEntries(Object.entries(fx.triples).map(([ax, [lo, dflt, hi]]) =>
    [ax, normalizeValue(loc[ax] ?? dflt, lo, dflt, hi)]));
  const vm = new VariationModel(fx.masters.map(norm));
  let worst = 0;
  for (const c of fx.cases) {
    worst = Math.max(worst, Math.abs(vm.interpolateFromMasters(norm(c.location), fx.values) - c.expected));
  }
  ok(worst === 0, `seed model matches fontTools on ${fx.cases.length} locations (max diff ${worst})`);
}

// ---- servers + browser ------------------------------------------------------
const studioServer = await serve(STUDIO_DIST);
const embedServer = await serve(EMBED_DIST);
const studioBase = `http://127.0.0.1:${studioServer.address().port}`;
const embedBase = `http://127.0.0.1:${embedServer.address().port}`;
console.log(`serving studio on ${studioBase}, embed on ${embedBase}`);

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await context.newPage();
page.on('pageerror', e => console.error('[pageerror]', e.message));
page.on('console', m => {
  const t = m.text();
  if (/error|fail|invalid|zip|upload/i.test(t)) console.log('[console]', m.type(), t.slice(0, 250));
});
page.on('console', m => {
  if (m.type() === 'error' || m.type() === 'warning') {
    console.log(`[page.${m.type()}]`, m.text().slice(0, 400));
  }
});
await context.addInitScript((embedUrl) => {
  window.AVAR2_EMBED_URL = embedUrl;
}, `${embedBase}/editor.html`);

const sleep = (ms) => page.waitForTimeout(ms);
// waitForFunction does NOT await async predicates in this playwright-core
// (a returned Promise is truthy → instant false pass) — poll page-side
// async conditions with an explicit Node loop instead.
const waitForAsync = async (fn, { timeout = 120000, every = 250 } = {}) => {
  for (const deadline = Date.now() + timeout; ;) {
    if (await fn()) return;
    if (Date.now() > deadline) throw new Error('waitForAsync timed out');
    await sleep(every);
  }
};
const buildStamp = () => page.evaluate(async () => (await window.__avar2api.health()).last_build_time);
const waitForBuildAdvance = async (before, timeout = 300000) => {
  for (const deadline = Date.now() + timeout; ;) {
    const stamp = await buildStamp();
    if (stamp !== before) return stamp;
    if (Date.now() > deadline) throw new Error('timed out waiting for a rebuild');
    await sleep(250);
  }
};
const controlAxes = () => page.evaluate(async () => (await window.__avar2api.listControlAxes()).axes);

try {
  // ---- 1. open the editor on a control-axis layer -------------------------
  console.log('1. open editor on a control-axis layer');
  await page.goto(studioBase, { waitUntil: 'load', timeout: 30000 });
  await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent === 'Crispy Mini', null, { timeout: 120000 });

  // Upload CrispyMini.glyphs so the dataset is a plain, CSV-less project,
  // then declare a control axis with one layer through the same API the
  // sidebar uses. NOTE: the family name doesn't change (the example IS
  // Crispy Mini) — wait on glyphs_path (stamped per upload) instead.
  const glyphsPathBefore = await page.evaluate(async () => (await window.__avar2api.health()).glyphs_path);
  await page.click('button:has-text("Load Font")');
  await page.setInputFiles('.load-font-dropdown input[type=file]', CRISPY_GLYPHS);
  await waitForAsync(async () =>
    (await page.evaluate(async () => (await window.__avar2api.health()).glyphs_path)) !== glyphsPathBefore);
  await page.evaluate(async () => {
    await window.__avar2api.createControlAxis({ tag: 'crbr', display_name: 'Crossbar', min: 0, default: 0, max: 100 });
    await window.__avar2api.controlAxisLayerDelta('crbr', {
      add: [{ glyph: 'e', location: { XTRA: 1000, crbr: 100 } }],
    });
    // A second, COMPUTED layer (correction target) — the guard test edits it.
    await window.__avar2api.controlAxisLayerDelta('crbr', {
      add: [{ glyph: 'e', location: { XTRA: 3330, crbr: 100 }, target: { XTRA: 94 } }],
    });
  });
  let axes = await controlAxes();
  const layerE = axes.find(a => a.tag === 'crbr')?.layers?.find(l => l.glyph === 'e' && l.location?.crbr === 100 && l.location?.XTRA === 1000);
  ok(!!layerE, 'layer stored WITH its crbr pin (control tag survives the delta endpoint)');

  // Reload so the sidebar picks up the new axis (also proves the new
  // layer persisted), then open the editor from the layer row's ↗ button.
  await page.reload({ waitUntil: 'load' });
  await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'), null, { timeout: 120000 });
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.control-axes *')].some(el =>
      el.children.length === 0 && el.textContent.trim() === 'crbr'), null, { timeout: 30000 });
  ok(true, 'crbr axis in the sidebar after reload (layer persisted)');

  // Expand the axis row, then the glyph block, then click ↗.
  const expandAxis = async () => {
    await page.evaluate(() => {
      const row = [...document.querySelectorAll('.control-axis-row')]
        .find(r => r.querySelector('.control-axis-tag')?.textContent.trim() === 'crbr');
      row.querySelector('.control-axis-header').click();
    });
    await page.waitForSelector('.layers-glyph-header', { timeout: 10000 });
    await page.click('.layers-glyph-header');
    await page.waitForSelector('.layer-open-fontra', { timeout: 10000 });
  };
  await expandAxis();
  // The first ↗ is the plain layer {XTRA:1000, crbr:100}.
  await page.click('.layer-open-fontra');

  await page.waitForSelector('.fontra-editor-iframe', { timeout: 15000 });
  ok(true, 'editor drawer opens with the iframe');
  // The bridge session initializes once the embed loads (heavy bundle).
  await page.waitForFunction(() => !!window.__avar2EditorBridge, null, { timeout: 30000 });
  // The iframe element mounts before the frame attaches and loads — poll
  // page.frames() rather than assuming it is already there.
  let editorFrame = null;
  await waitForAsync(async () => {
    editorFrame = page.frames().find(f => f.url().startsWith(embedBase)) || null;
    return !!editorFrame;
  }, { timeout: 30000 });
  ok(true, 'embed iframe frame found');
  await editorFrame.waitForFunction(() => !!window.editorController?.fontController, null, { timeout: 60000 });
  ok(true, 'editor controller started in the iframe');

  // Navigated to the right glyph at the session location?
  await editorFrame.waitForFunction(() =>
    window.editorController.sceneSettings.selectedGlyphName === 'e', null, { timeout: 30000 });
  ok(true, "editor sits on glyph 'e' in edit mode");
  const loc = await editorFrame.evaluate(() => window.editorController.sceneSettings.fontLocationUser);
  const expected = await page.evaluate(() => window.__avar2EditorBridge._state().sessionLocation);
  const locMatch = Object.entries(expected).every(([k, v]) => Math.abs((loc[k] ?? NaN) - v) < 1e-9);
  ok(locMatch, `editor location matches the session (${JSON.stringify(loc)})`);

  // ---- 1b. focused studio UI (the shim lives inside the embed bundle) ------
  console.log('1b. focused studio UI (ported from the server-side injection)');
  // The session config rides the bridge init; the embed bundle applies the
  // focused UI itself — no server-side page injection in this topology.
  const embedSession = await editorFrame.evaluate(() => window.embedSession);
  ok(embedSession?.studio === true && embedSession?.tag === 'crbr' && embedSession?.glyph === 'e',
    `studio session config arrived over the bridge init (${JSON.stringify(embedSession)})`);
  ok(await editorFrame.evaluate(() => !!document.getElementById('fontra-embed-focus')),
    'the embed injected the focused-UI stylesheet');

  const focusDOM = await editorFrame.evaluate(() => {
    const display = (sel) => {
      const el = document.querySelector(sel);
      return el ? getComputedStyle(el).display : null;
    };
    const tool = (t) => display(`#edit-tools > .tool-button[data-tool="${t}"]`);
    const tab = (n) => display(`.sidebar-tab[data-sidebar-name="${n}"]`);
    return {
      hiddenTools: ['pen-tool', 'knife-tool', 'shape-tool'].map(tool),
      keptTools: ['pointer-tools', 'power-ruler-tool', 'metrics-tool', 'hand-tool'].map(tool),
      topBar: display('.top-bar-container'),
      hiddenTabs: ['text-entry', 'selection-info', 'reference-font', 'glyph-search',
        'selection-transformation', 'glyph-note', 'related-glyphs', 'characters-glyphs'].map(tab),
      navTab: tab('designspace-navigation'),
    };
  });
  ok(focusDOM.hiddenTools.every((d) => d === 'none'),
    `drawing tools hidden (pen/knife/shape: ${focusDOM.hiddenTools})`);
  ok(focusDOM.keptTools.every((d) => d && d !== 'none'),
    `pointer/ruler/metrics/hand tools kept (${focusDOM.keptTools})`);
  ok(focusDOM.topBar === 'none', 'top bar and menu bar hidden');
  ok(focusDOM.hiddenTabs.every((d) => d === 'none' || d === null)
    && focusDOM.hiddenTabs.filter((d) => d === 'none').length >= 7,
    `named sidebar panels hidden (${focusDOM.hiddenTabs})`);
  ok(focusDOM.navTab && focusDOM.navTab !== 'none', 'designspace-navigation tab kept visible');

  // The sources sweep polls (every 500 ms) until Fontra's panel populates,
  // then trims the accordion + rows and enables editing on the studio rows.
  // Wait for the CONVERGED state, not just any editing flag: Fontra's own
  // init can mark a master editing before the first sweep strips it.
  // Brace rows stay visible; editing is enabled on the session layer only —
  // the second brace row here is a COMPUTED correction target, which the
  // bridge rejects edits to, so the shim must never enable it.
  const editableLayer = await page.evaluate(() => {
    const s = window.__avar2EditorBridge._state();
    return s.layers.find((ln) => s.editable[ln]);
  });
  await editorFrame.waitForFunction(() => {
    const session = window.embedSession;
    const panel = document.querySelector(
      '.sidebar-content[data-sidebar-name="designspace-navigation"]')?.children[0];
    const list = panel?.sourcesList;
    const items = list?.items || [];
    if (!list?.shadowRoot || !items.length) return false;
    const isStudio = (item) => !item.isFontSource && item.denseLocation
      && (session.axis_name in item.denseLocation)
      && Number(item.denseLocation[session.axis_name]) !== Number(session.axis_default);
    const isComputed = (item) => / → computed$/.test(String(item.name || ''));
    if (!items.some(isStudio)) return false;
    for (const row of list.shadowRoot.querySelectorAll('.contents > .row')) {
      const item = items[Number(row.dataset.rowIndex)];
      if (!item) return false;
      if (isStudio(item)) {
        if (row.style.display === 'none') return false;
        if (!isComputed(item) && !item.editing) return false;
        if (isComputed(item) && item.editing) return false;
      } else if (item.editing || row.style.display !== 'none') {
        return false;
      }
    }
    return true;
  }, null, { timeout: 20000 });

  const sourcesUI = await editorFrame.evaluate(() => {
    const session = window.embedSession;
    const panel = document.querySelector(
      '.sidebar-content[data-sidebar-name="designspace-navigation"]').children[0];
    const tab = document.querySelector(
      '.sidebar-tab[data-sidebar-name="designspace-navigation"]');
    const accordion = panel.shadowRoot.querySelector('ui-accordion');
    const trimmed = ['font-axes-accordion-item', 'glyph-axes-accordion-item',
      'glyph-layers-accordion-item', 'sources-list-add-remove-buttons']
      .map((id) => {
        const el = accordion.shadowRoot.querySelector('#' + id);
        return el ? getComputedStyle(el).display : 'absent';
      });
    const list = panel.sourcesList;
    const rows = [...list.shadowRoot.querySelectorAll('.contents > .row')]
      .map((row) => {
        const item = list.items[Number(row.dataset.rowIndex)];
        const studio = !item.isFontSource && item.denseLocation
          && (session.axis_name in item.denseLocation)
          && Number(item.denseLocation[session.axis_name]) !== Number(session.axis_default);
        return {
          layerName: item.layerName, studio: !!studio,
          computed: / → computed$/.test(String(item.name || '')),
          editing: !!item.editing, hidden: getComputedStyle(row).display === 'none',
        };
      });
    return { navOpen: tab.classList.contains('selected'), trimmed, rows };
  });
  ok(sourcesUI.navOpen, 'designspace-navigation tab auto-opened by the shim');
  ok(sourcesUI.trimmed.every((d) => d === 'none'),
    `nav panel trimmed to the sources list (${sourcesUI.trimmed})`);
  const studioRows = sourcesUI.rows.filter((r) => r.studio);
  const otherRows = sourcesUI.rows.filter((r) => !r.studio);
  ok(studioRows.length === 2 && otherRows.length >= 2,
    `sources list has 2 brace rows + master/font rows (${sourcesUI.rows.length} total)`);
  ok(studioRows.every((r) => !r.hidden), 'brace-layer rows stay visible');
  const editingRows = sourcesUI.rows.filter((r) => r.editing);
  ok(editingRows.length === 1 && editingRows[0].layerName === editableLayer,
    'only the session layer row is editing-enabled');
  ok(studioRows.some((r) => r.computed && !r.editing),
    'the computed correction-target row stays visible but non-editable');
  ok(otherRows.every((r) => r.hidden && !r.editing),
    'master/font-source rows hidden and non-editable');

  // ---- 2. nudge -> editFinal -> stored outline -> rebuild ------------------
  console.log('2. node nudge flows to the sidecar and the rebuild renders it');
  const state = await page.evaluate(() => window.__avar2EditorBridge._state());
  const editableLayers = Object.keys(state.editable).filter(ln => state.editable[ln]);
  ok(editableLayers.length === 1, `exactly one editable (session) layer (${editableLayers})`);

  // Specimen baselines BEFORE the drawing exists: the layer sits at
  // XTRA=1000, so the brace (computed for now) only shows with the
  // preview's XTRA slider there. crbr=0 is the no-brace reference.
  await page.click('button:text-is("Preview")');
  await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
  const setSpecimen = (text) => page.evaluate((t) => {
    const el = document.querySelector('.preview-tab-sample');
    el.textContent = t;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, text);
  const setSlider = (tag, value) => page.evaluate(([t, v]) => {
    const groups = [...document.querySelectorAll('.axis-control')];
    const g = groups.find(el => [...el.querySelectorAll('.axis-tag')].some(x => x.textContent.trim() === t));
    const input = g && g.querySelector('input[type=range]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, v);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, [tag, String(value)]);
  await setSpecimen('eeee');
  await setSlider('XTRA', 1000);
  await setSlider('crbr', 100);
  await sleep(900);
  const shotComputed = await page.locator('.preview-tab-sample').screenshot();
  await setSlider('crbr', 0);
  await sleep(900);
  const shotNoBrace = await page.locator('.preview-tab-sample').screenshot();
  ok(!shotComputed.equals(shotNoBrace), 'baseline: the computed brace shows at XTRA=1000 × crbr=100');
  // Baseline font bytes for the post-draw crbr=0 check: pixel equality
  // across a page reload is layout-fragile, so the no-leak assertion
  // measures rendered advances in a canvas instead (deterministic).
  const fontBytesBefore = await page.evaluate(async () => {
    const r = await fetch(window.__avar2api.getFontUrl());
    const u8 = new Uint8Array(await r.arrayBuffer());
    let s = '';
    for (let i = 0; i < u8.length; i += 32768) {
      s += String.fromCharCode(...u8.subarray(i, i + 32768));
    }
    return btoa(s);
  });

  // Nudge: select the first on-curve point of the session glyph and
  // shift-arrow it (5 × 10 = 50 units — visible on the specimen).
  // Capture the build stamp BEFORE the edit: the editFinal triggers the
  // coalesced rebuild, and the stamp must advance past this value.
  const stamp = await buildStamp();
  await editorFrame.evaluate(async () => {
    const ed = window.editorController;
    ed.sceneController.selection = new Set(['point/0']);
    for (let i = 0; i < 5; i++) {
      await ed.sceneController.handleArrowKeys({
        key: 'ArrowRight', preventDefault() {}, metaKey: false, ctrlKey: false, shiftKey: true, altKey: false,
      });
    }
  });
  // The sidecar gains the drawn outline (editFinal flowed back).
  await waitForAsync(async () => page.evaluate(async () => {
    const axs = (await window.__avar2api.listControlAxes()).axes;
    const l = axs.find(a => a.tag === 'crbr')?.layers?.find(l =>
      l.glyph === 'e' && l.location?.crbr === 100 && l.location?.XTRA === 1000);
    return !!l?.outline;
  }), { timeout: 15000 });
  ok(true, 'editFinal stored the drawn outline on the layer');
  const drawn = (await controlAxes()).find(a => a.tag === 'crbr').layers
    .find(l => l.glyph === 'e' && l.location?.crbr === 100 && l.location?.XTRA === 1000);
  ok(drawn.outline.paths.length === 2 && drawn.outline.paths.map(p => p.nodes.length).join(',') === '27,17',
    'stored outline keeps the seed structure (2 contours, 27+17 nodes)');
  // The nudge moved node 0 by +50 on x (5 shift-arrows), everything else
  // untouched. The expected seed is computed Node-side from the same wasm
  // model + the var-model port (verified against fontTools in section 0).
  const { VariationModel, normalizeValue } = await import('../src/var-model.js');
  const wasmInit = (await import('../src/wasm/fontc-web/fontc_web.js')).default;
  const { glyph_model } = await import('../src/wasm/fontc-web/fontc_web.js');
  await wasmInit(readFileSync(join(HERE, '../src/wasm/fontc-web/fontc_web_bg.wasm')));
  const model = JSON.parse(glyph_model(readFileSync(CRISPY_GLYPHS, 'utf8'), 'e'));
  const tags = model.axes.map(a => a.tag);
  const triples = tags.map((t, i) => {
    const vals = model.masters.map(m => m.axesValues[i] ?? 0);
    return [Math.min(...vals), model.masters[0].axesValues[i] ?? 0, Math.max(...vals)];
  });
  const norm = (v, i) => normalizeValue(v, ...triples[i]);
  const vm = new VariationModel(model.masters.map(m =>
    Object.fromEntries(tags.map((t, i) => [t, norm(m.axesValues[i] ?? 0, i)]))));
  const seedTarget = Object.fromEntries(tags.map((t, i) =>
    [t, norm(t === 'XTRA' ? 1000 : model.masters[0].axesValues[i] ?? 0, i)]));
  const masterLayers = model.masters.map(m => model.glyph.layers.find(l => l.layerId === m.id));
  const expectedSeed0 = [
    vm.interpolateFromMasters(seedTarget, masterLayers.map(l => l.outline.paths[0].nodes[0][0])),
    vm.interpolateFromMasters(seedTarget, masterLayers.map(l => l.outline.paths[0].nodes[0][1])),
  ];
  const node0 = drawn.outline.paths[0].nodes[0];
  ok(Math.abs(node0[0] - (expectedSeed0[0] + 50)) < 0.5 && Math.abs(node0[1] - expectedSeed0[1]) < 0.5,
    `stored node0 = seed + (50, 0) — got [${node0[0]}, ${node0[1]}], seed [${expectedSeed0[0].toFixed(1)}, ${expectedSeed0[1].toFixed(1)}]`);
  // Untouched nodes equal the seed exactly (the seed IS the desktop's).
  const node5 = drawn.outline.paths[0].nodes[5];
  const expectedNode5 = [
    vm.interpolateFromMasters(seedTarget, masterLayers.map(l => l.outline.paths[0].nodes[5][0])),
    vm.interpolateFromMasters(seedTarget, masterLayers.map(l => l.outline.paths[0].nodes[5][1])),
  ];
  ok(Math.abs(node5[0] - expectedNode5[0]) < 1e-6 && Math.abs(node5[1] - expectedNode5[1]) < 1e-6,
    'an untouched node keeps the exact seed position');

  // The coalesced rebuild lands: the preview at crbr=100 changes.
  // (If it failed, the bridge surfaces the error in the drawer — read it
  // for a diagnosis before timing out.)
  try {
    await waitForBuildAdvance(stamp, 120000);
  } catch (err) {
    const bridgeErr = await page.evaluate(() =>
      document.querySelector('.fontra-editor-bridge-error')?.textContent || null);
    console.error('  bridge error at rebuild timeout:', bridgeErr);
    throw err;
  }
  await page.reload({ waitUntil: 'load' }); // pick up the persisted session cheaply
  await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'), null, { timeout: 120000 });
  await page.click('button:text-is("Preview")');
  await page.waitForSelector('.preview-tab-sample', { timeout: 20000 });
  await setSpecimen('eeee');
  await setSlider('XTRA', 1000);
  await setSlider('crbr', 100);
  await sleep(900);
  // The drawn brace peaks at its full N-D location (desktop/varLib
  // semantics): at XTRA=1000 × crbr=100 the render shows the drawing;
  // at crbr=0 the layer contributes nothing.
  const shotDrawn = await page.locator('.preview-tab-sample').screenshot();
  ok(!shotDrawn.equals(shotNoBrace), 'the rebuilt font renders the drawing at the layer location');
  ok(!shotDrawn.equals(shotComputed), 'the drawn outline replaced the computed brace');
  await setSlider('crbr', 0);
  await sleep(900);
  // No-leak + drawing-visible checks at font level: render 'eeee' in the
  // pre-draw font and the current build as hidden divs in ONE page (no
  // reload boundary, DOM honors font-variation-settings), screenshot each
  // div, compare pixels. crbr=0 must be identical; crbr=100 must differ —
  // the nudge moved a node (shape, not advance), so width probes can't
  // see it. (Font-level equivalent verified in Node: measure_at on both
  // builds is bit-identical at crbr=0.)
  await page.evaluate(async (b64) => {
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const before = new FontFace('before-font', bytes);
    await before.load();
    document.fonts.add(before);
    const mk = (family, fvs, id, top) => {
      const el = document.createElement('div');
      el.id = id;
      el.style.cssText = `position:absolute;left:0;top:${top}px;width:400px;height:80px;`
        + 'background:#fff;color:#000;pointer-events:none;'
        + `font-size:64px;line-height:80px;font-family:${family};font-variation-settings:${fvs};`;
      el.textContent = 'eeee';
      document.body.appendChild(el);
    };
    mk('before-font', '"XTRA" 1000, "crbr" 0', 'e2e-b0', 0);
    mk('"Crispy Mini-VF"', '"XTRA" 1000, "crbr" 0', 'e2e-a0', 100);
    mk('before-font', '"XTRA" 1000, "crbr" 100', 'e2e-b100', 200);
    mk('"Crispy Mini-VF"', '"XTRA" 1000, "crbr" 100', 'e2e-a100', 300);
  }, fontBytesBefore);
  const shot = (id) => page.locator(`#${id}`).screenshot();
  const [sb0, sa0, sb100, sa100] = [await shot('e2e-b0'), await shot('e2e-a0'),
    await shot('e2e-b100'), await shot('e2e-a100')];
  if (!sb0.equals(sa0)) {
    const { writeFileSync: wr } = await import('node:fs');
    wr('/tmp/e2e-b0.png', sb0); wr('/tmp/e2e-a0.png', sa0);
  }
  ok(sb0.equals(sa0),
    'crbr=0 is untouched by the drawn brace (pixel-identical to the pre-draw font)');
  ok(!sb100.equals(sa100),
    'the drawing shows at crbr=100');
  await page.evaluate(() => ['e2e-b0', 'e2e-a0', 'e2e-b100', 'e2e-a100']
    .forEach(id => document.getElementById(id)?.remove()));

  // ---- 3. guards ------------------------------------------------------------
  console.log('3. guards (master / computed / structural)');
  // Reopen the editor on the drawn layer.
  await page.click('button:text-is("Instances")');
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.control-axes *')].some(el =>
      el.children.length === 0 && el.textContent.trim() === 'crbr'), null, { timeout: 30000 });
  await expandAxis();
  await page.click('.layer-open-fontra');
  await page.waitForFunction(() => !!window.__avar2EditorBridge, null, { timeout: 30000 });
  let editorFrame2 = null;
  await waitForAsync(async () => {
    editorFrame2 = page.frames().find(f => f.url().startsWith(embedBase)) || null;
    return !!editorFrame2;
  }, { timeout: 30000 });
  await editorFrame2.waitForFunction(() => !!window.editorController?.fontController, null, { timeout: 60000 });

  const bridgeLayers = await page.evaluate(() => window.__avar2EditorBridge._state());
  const masterLayer = bridgeLayers.layers.find(ln => ln.startsWith('master:'));
  const targetLayer = bridgeLayers.layers
    .find(ln => ln.startsWith('brace:') && !bridgeLayers.editable[ln]);
  const sessionLayer = bridgeLayers.layers.find(ln => bridgeLayers.editable[ln]);
  ok(!!masterLayer && !!targetLayer && !!sessionLayer, 'bridge model has master, computed and editable layers');

  const sendFakeEdit = (layerName) => editorFrame2.evaluate((ln) => {
    window.parent.postMessage({
      'avar2-embed': true, v: 0, type: 'editFinal',
      change: { p: ['glyphs', 'e', 'layers', ln, 'glyph', 'path'], f: '=xy', a: [0, 1, 2] },
      rollbackChange: { p: ['glyphs', 'e', 'layers', ln, 'glyph', 'path'], f: '=xy', a: [0, 0, 0] },
      label: 'e2e probe',
    }, '*');
  }, layerName);

  await sendFakeEdit(masterLayer);
  await page.waitForSelector('.fontra-editor-bridge-error', { timeout: 10000 });
  const masterErr = await page.textContent('.fontra-editor-bridge-error');
  ok(/Master layers/.test(masterErr), `master-layer edit rejected (${masterErr.trim().slice(0, 60)}…)`);

  await sendFakeEdit(targetLayer);
  await page.waitForFunction(
    (prev) => document.querySelector('.fontra-editor-bridge-error')?.textContent !== prev
      && /computed/.test(document.querySelector('.fontra-editor-bridge-error')?.textContent || ''),
    masterErr, { timeout: 10000 });
  ok(true, 'computed (target) layer edit rejected');

  // Structural: deleteContour on the session layer.
  await editorFrame2.evaluate((ln) => {
    window.parent.postMessage({
      'avar2-embed': true, v: 0, type: 'editFinal',
      change: { p: ['glyphs', 'e', 'layers', ln, 'glyph', 'path'], f: 'deleteContour', a: [0] },
      label: 'e2e structural probe',
    }, '*');
  }, sessionLayer);
  await page.waitForFunction(() =>
    /structure/.test(document.querySelector('.fontra-editor-bridge-error')?.textContent || ''),
    null, { timeout: 10000 });
  ok(true, 'structural edit rejected with a readable error');

  // Nothing landed on the sidecar for any of the rejected edits.
  const axesAfter = await controlAxes();
  const drawnAfter = axesAfter.find(a => a.tag === 'crbr').layers
    .find(l => l.glyph === 'e' && l.location?.XTRA === 1000);
  ok(drawnAfter.outline.paths[0].nodes.length === 27, 'rejected edits left the stored outline intact');

  // Close the drawer.
  await page.click('.fontra-editor-close');
  await page.waitForFunction(() => !document.querySelector('.fontra-editor-iframe'), null, { timeout: 10000 });
  ok(true, 'drawer closes (Done editing)');

  // ---- 4. persistence: reload + workspace zip -------------------------------
  console.log('4. persistence (reload + zip round-trip)');
  await page.reload({ waitUntil: 'load' });
  await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'), null, { timeout: 120000 });
  const axesReloaded = await controlAxes();
  ok(axesReloaded.find(a => a.tag === 'crbr')?.layers?.some(l => l.outline),
    'the drawn outline survives a page reload (session)');
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.control-axes *')].some(el =>
      el.children.length === 0 && el.textContent.trim() === 'crbr'), null, { timeout: 30000 });
  await expandAxis();
  ok(await page.waitForSelector('.layer-coords-drawn-badge', { timeout: 10000 }).then(() => true).catch(() => false),
    'the layer row shows the drawn badge');

  // Download the workspace zip; the control sidecar must carry the outline.
  const { unzipSync } = await import('fflate');
  await page.click('button:has-text("Config")');
  const [wsDownload] = await Promise.all([
    page.waitForEvent('download', { timeout: 20000 }),
    page.click('button:has-text("Download workspace")'),
  ]);
  const zipBytes = readFileSync(await wsDownload.path());
  const zipEntries = unzipSync(new Uint8Array(zipBytes));
  const controlEntry = Object.keys(zipEntries).find(n => n.endsWith('-control.json'));
  ok(!!controlEntry, 'workspace zip carries the control sidecar');
  const sidecar = JSON.parse(new TextDecoder().decode(zipEntries[controlEntry]));
  const zipLayer = sidecar.axes.find(a => a.tag === 'crbr')?.layers
    ?.find(l => l.glyph === 'e' && l.location?.XTRA === 1000);
  ok(zipLayer?.outline?.paths?.length === 2 && zipLayer.outline.paths[0].nodes.length === 27,
    'the zipped sidecar carries the drawn outline');

  // Load the zip back as a new project: the drawing survives. (Family
  // name AND glyphs_path are unchanged — same source inside the zip —
  // so wait on the font URL, which is re-minted per load.)
  const fontUrlBeforeZip = await page.evaluate(() => window.__avar2api.getFontUrl());
  await page.click('button:has-text("Load Font")');
  // The upload handler branches on the file NAME (.zip vs .glyphs), and
  // Playwright's download.path() is extensionless — give it a .zip name.
  const { writeFileSync: writeZip } = await import('node:fs');
  writeZip('/tmp/e2e-workspace.zip', zipBytes);
  await page.setInputFiles('.load-font-dropdown input[type=file]', '/tmp/e2e-workspace.zip');
  try {
    await waitForAsync(async () =>
      (await page.evaluate(() => window.__avar2api.getFontUrl())) !== fontUrlBeforeZip,
    { timeout: 45000 });
  } catch (e) {
    const zipB64 = zipBytes.toString('base64');
    console.log('  zip upload diagnostics:',
      await page.evaluate(async (b64) => {
        const msgs = document.body.innerText.split('\n')
          .filter(l => /upload|fail|zip/i.test(l)).slice(0, 6);
        let direct;
        try {
          const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
          const f = new File([bytes], 'workspace.zip', { type: 'application/zip' });
          const r = await window.__avar2api.uploadSource([f]);
          direct = 'ok: ' + JSON.stringify(r).slice(0, 150);
        } catch (err) { direct = 'threw: ' + (err.message || String(err)).slice(0, 200); }
        return JSON.stringify({
          health: await window.__avar2api.health(), uiMessages: msgs, directUpload: direct,
        });
      }, zipB64).catch(err => `evaluate failed: ${err}`));
    throw e;
  }
  const axesFromZip = await controlAxes();
  const zipLoadedLayer = axesFromZip.find(a => a.tag === 'crbr')?.layers
    ?.find(l => l.glyph === 'e' && l.location?.XTRA === 1000);
  ok(!!zipLoadedLayer?.outline, 'the outline survives the zip round-trip');
  const zipNode0 = zipLoadedLayer.outline.paths[0].nodes[0];
  ok(zipNode0[0] === drawnAfter.outline.paths[0].nodes[0][0] && zipNode0[1] === drawnAfter.outline.paths[0].nodes[0][1],
    'zip round-trip keeps the nudged coordinates verbatim');

  // ---- 4b. metrics HUD (Phase 4b) ------------------------------------------
  // The HUD measures the PRISTINE source at the session layer's location with
  // the control axis pinned to default. The section-1 session sits at the
  // parametric default (XOPQ=2), where this skeleton toy font's strokes are
  // ~2 units — below the server rule's 5-unit noise floor — so a numeric
  // check needs a fatter location: a second session on 'H' at XOPQ=1016 ×
  // YOPQ=462. Oracle: fixtures/reference-metrics-oracle.json, generated by
  // running the server's _measure_strokes verbatim (fontTools) on the
  // pristine wasm compile of CrispyMini.glyphs instantiated there.
  console.log('4b. metrics HUD (reference numbers vs fontTools oracle, live tracking)');
  const oracle = JSON.parse(readFileSync(join(HERE, 'fixtures', 'reference-metrics-oracle.json'), 'utf8'));
  await page.evaluate(async () => {
    await window.__avar2api.controlAxisLayerDelta('crbr', {
      add: [{ glyph: 'H', location: { XTRA: 1000, XOPQ: 1016, YOPQ: 462, crbr: 100 } }],
    });
  });
  await page.reload({ waitUntil: 'load' });
  await page.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
  await page.waitForFunction(() => document.querySelector('.sidebar h2')?.textContent?.startsWith('Crispy'), null, { timeout: 120000 });
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.control-axes *')].some(el =>
      el.children.length === 0 && el.textContent.trim() === 'crbr'), null, { timeout: 30000 });
  await expandAxis();
  await page.evaluate(() => {
    const block = [...document.querySelectorAll('.layers-glyph-block')]
      .find(b => b.querySelector('.layers-glyph-name')?.textContent.trim() === 'H');
    block.querySelector('.layers-glyph-header').click();
  });
  await page.evaluate(() => {
    const block = [...document.querySelectorAll('.layers-glyph-block')]
      .find(b => b.querySelector('.layers-glyph-name')?.textContent.trim() === 'H');
    block.querySelector('.layer-open-fontra').click();
  });

  let hudFrame = null;
  await waitForAsync(async () => {
    hudFrame = page.frames().find(f => f.url().startsWith(embedBase)) || null;
    return !!hudFrame;
  }, { timeout: 30000 });
  await hudFrame.waitForFunction(() => !!window.editorController?.fontController, null, { timeout: 60000 });
  await hudFrame.waitForFunction(() =>
    window.editorController.sceneSettings.selectedGlyphName === 'H', null, { timeout: 30000 });
  ok(true, 'HUD session open on H at XOPQ=1016 × YOPQ=462');

  await hudFrame.waitForSelector('#avar2-studio-metrics', { timeout: 15000 });
  ok(true, 'metrics HUD appears inside the editor iframe');
  // The reference RPC round-trips through the bridge; wait for H's stem.
  await hudFrame.waitForFunction(() =>
    document.querySelector('#avar2-studio-metrics tr.ref td.s')?.textContent === '1016.0',
    null, { timeout: 15000 });
  const hudRead = () => hudFrame.evaluate(() => {
    const txt = (sel) => document.querySelector(`#avar2-studio-metrics ${sel}`)?.textContent;
    return {
      header: txt('.hd'),
      ref: { bar: txt('tr.ref td.b'), stem: txt('tr.ref td.s'), contrast: txt('tr.ref td.c') },
      live: { glyph: txt('tr.live td.g'), bar: txt('tr.live td.b'), stem: txt('tr.live td.s') },
    };
  });
  const hud1 = await hudRead();
  const fmt = (v, dp) => Number(v).toFixed(dp);
  ok(hud1.ref.bar === fmt(oracle.metrics.H.bar, 1)
    && hud1.ref.stem === fmt(oracle.metrics.H.stem, 1)
    && hud1.ref.contrast === fmt(oracle.metrics.H.contrast, 2),
    `reference H matches the fontTools oracle (bar ${hud1.ref.bar}, stem ${hud1.ref.stem}, s/b ${hud1.ref.contrast})`);
  ok(['XTRA 1000', 'XOPQ 1016', 'YOPQ 462'].every(p => hud1.header.includes(p)) && !/crbr/i.test(hud1.header),
    `HUD header shows the layer location with the control axis pinned out (${hud1.header})`);

  // The reference input round-trips through the bridge: type 'e'.
  await hudFrame.evaluate(() => {
    const input = document.querySelector('#avar2-ref-glyph');
    input.value = 'e';
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await hudFrame.waitForFunction((expected) =>
    document.querySelector('#avar2-studio-metrics tr.ref td.s')?.textContent === expected,
    fmt(oracle.metrics.e.stem, 1), { timeout: 15000 });
  const hud2 = await hudRead();
  ok(hud2.ref.bar === fmt(oracle.metrics.e.bar, 1)
    && hud2.ref.stem === fmt(oracle.metrics.e.stem, 1)
    && hud2.ref.contrast === fmt(oracle.metrics.e.contrast, 2),
    `reference e matches the oracle after typing it (bar ${hud2.ref.bar}, stem ${hud2.ref.stem}, s/b ${hud2.ref.contrast})`);

  // Live row: the drawing itself, tracking the canvas. Pre-nudge it equals
  // the seed (H's own figures); the nudge moves node2 (inner right-stem
  // edge at the bar level) +50 on x, narrowing that stem 1016 → 980.
  await hudFrame.waitForFunction(() =>
    document.querySelector('#avar2-studio-metrics tr.live td.s')?.textContent === '1016.0',
    null, { timeout: 15000 });
  const hudPre = await hudRead();
  ok(hudPre.live.glyph === 'H' && hudPre.live.stem === '1016.0' && hudPre.live.bar === '462.7',
    `live row measures the in-memory drawing (stem ${hudPre.live.stem}, bar ${hudPre.live.bar})`);
  await hudFrame.evaluate(async () => {
    const ed = window.editorController;
    ed.sceneController.selection = new Set(['point/2']);
    for (let i = 0; i < 5; i++) {
      await ed.sceneController.handleArrowKeys({
        key: 'ArrowRight', preventDefault() {}, metaKey: false, ctrlKey: false, shiftKey: true, altKey: false,
      });
    }
  });
  // The edit landed (guard passed, sidecar holds H's drawn outline)…
  await waitForAsync(async () => page.evaluate(async () => {
    const axs = (await window.__avar2api.listControlAxes()).axes;
    const l = axs.find(a => a.tag === 'crbr')?.layers?.find(l =>
      l.glyph === 'H' && l.location?.XOPQ === 1016 && l.location?.crbr === 100);
    return !!l?.outline;
  }), { timeout: 15000 });
  ok(true, 'the H nudge landed on the sidecar (guard passed)');
  // …and the HUD's live figure tracks it while the reference stays fixed.
  // (The editor's re-interpolated path carries sub-unit float dust — offline
  // the move gives exactly 980.0/462.7 — so assert on parse with tolerance.)
  await hudFrame.waitForFunction(() =>
    document.querySelector('#avar2-studio-metrics tr.live td.s')?.textContent !== '1016.0',
    null, { timeout: 15000 });
  const hudPost = await hudRead();
  const postStem = parseFloat(hudPost.live.stem);
  const postBar = parseFloat(hudPost.live.bar);
  ok(Math.abs(postStem - 980) <= 0.5 && Math.abs(postBar - 462.7) <= 0.5,
    `live stem tracks the nudge (1016.0 → ${hudPost.live.stem}), bar unchanged (${hudPost.live.bar})`);
  ok(hudPost.ref.stem === hud2.ref.stem && hudPost.ref.bar === hud2.ref.bar
    && hudPost.ref.contrast === hud2.ref.contrast,
    'reference row unchanged by the edit (pristine-source figures are constant)');
} catch (err) {
  ok(false, `spec crashed: ${err.message || err}`);
  console.error(err);
}

// ---- 5. two-tab lock ---------------------------------------------------------
console.log('5. two-tab session lock');
try {
  const page2 = await context.newPage();
  await page2.goto(studioBase, { waitUntil: 'load', timeout: 30000 });
  await page2.waitForSelector('button:has-text("Load Font")', { timeout: 20000 });
  await page2.waitForSelector('text=/open in another tab/', { timeout: 15000 });
  ok(true, 'second tab shows the session-lock banner');
  const page1Banner = await page.$('text=/open in another tab/');
  ok(!page1Banner, 'the holding tab shows no banner');
  await page2.close();
} catch (err) {
  ok(false, `two-tab check crashed: ${err.message || err}`);
}

await browser.close();
studioServer.close();
embedServer.close();
console.log(failures ? `\n${failures} FAILURES` : '\nALL PASS');
process.exit(failures ? 1 : 0);
