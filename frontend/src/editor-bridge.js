/**
 * editor-bridge.js — parent side of the fontra-embed glyph editor.
 *
 * The static demo can't run Fontra's Python backend, so the editor (the
 * fontra-embed bundle) is iframed from a static host and talks to this
 * bridge over postMessage: the bridge answers Fontra's font RPC from an
 * in-memory VariableGlyph model built from `glyph_model` (fontc-web wasm,
 * source-level) plus the control-axis sidecar, and applies the editor's
 * editFinal changes back into the sidecar (a layer's stored `outline`).
 *
 * Scoping and guards (mirroring the desktop):
 *   - the session is one (control axis, glyph, location) triple;
 *   - master layers are read-only (they belong to the source file);
 *   - correction layers (a `target`) are read-only (they are recomputed
 *     on every build, so an edit would be discarded);
 *   - structural edits (point/contour add/remove) on a drawn layer are
 *     rejected — the outline must stay compatible with the masters, same
 *     reason the desktop hides the pen/knife tools.
 * Rejected edits are reverted in the editor via a reloadData push and
 * surfaced through onError.
 *
 * The Fontra space is keyed by axis DISPLAY NAME (the desktop contract —
 * App.jsx builds the location fragment the same way): getAxes/sources/
 * locations all use names; the sidecar stays tag-keyed; the bridge maps
 * between them.
 */

import { glyphModel } from './fontc-compile';
import { VariationModel, normalizeValue } from './var-model';

// The embed bundle's URL. Production default: the fontra-embed Pages
// deploy. Dev default: the fontra-embed dev server (`npx serve dist
// -l 8099` there). Overrides: VITE_FONTRA_EMBED_URL at build time,
// window.AVAR2_EMBED_URL at runtime (tests/tools).
export const embedEditorUrl = () =>
  (typeof window !== 'undefined' && window.AVAR2_EMBED_URL)
  || import.meta.env.VITE_FONTRA_EMBED_URL
  || (import.meta.env.DEV
    ? 'http://localhost:8099/editor.html'
    : 'https://agyeiagyeiagyei.github.io/fontra-embed/editor.html');

const MESSAGE_KEY = 'avar2-embed';
// Wire protocol version. fontra-embed stamps every message with it; a
// mismatch means one side is stale — fail loudly instead of parsing
// the wrong shape.
const PROTOCOL_VERSION = 0;

// ---------------------------------------------------------------------------
// Outline schema conversions (sidecar <-> Fontra PackedPath)
// ---------------------------------------------------------------------------

// VarPackedPath point type bits (fontra-core var-path.js).
const PT_ON = 0x00;
const PT_OFF_QUAD = 0x01;
const PT_OFF_CUBIC = 0x02;

// An off-curve run is quadratic when the segment it belongs to ends at a
// "qcurve" on-curve node (glyphsLib shares one "offcurve" type). The run
// wraps the contour end for closed paths.
function sidecarPathToPacked(path) {
  const nodes = path.nodes || [];
  const n = nodes.length;
  const onCurveType = (i) => nodes[i][2] || 'line';
  const coordinates = [];
  const pointTypes = [];
  for (let i = 0; i < n; i++) {
    const [x, y, t] = nodes[i];
    coordinates.push(x, y);
    if ((t || 'line') !== 'offcurve') {
      pointTypes.push(PT_ON);
      continue;
    }
    // Next on-curve node (wrapping within the contour when closed).
    let quad = false;
    for (let step = 1; step <= n; step++) {
      const j = (i + step) % n;
      const jt = onCurveType(j);
      if (jt !== 'offcurve') { quad = jt === 'qcurve'; break; }
      if (!path.closed && (i + step) >= n) break; // open: no wrap
    }
    pointTypes.push(quad ? PT_OFF_QUAD : PT_OFF_CUBIC);
  }
  return { coordinates, pointTypes };
}

function outlineToStaticGlyph(outline) {
  const coordinates = [];
  const pointTypes = [];
  const contourInfo = [];
  for (const path of outline.paths || []) {
    const packed = sidecarPathToPacked(path);
    const base = pointTypes.length;
    coordinates.push(...packed.coordinates);
    pointTypes.push(...packed.pointTypes);
    if (packed.pointTypes.length) {
      contourInfo.push({ endPoint: base + packed.pointTypes.length - 1, isClosed: !!path.closed });
    }
  }
  return {
    path: { coordinates, pointTypes, contourInfo },
    components: (outline.components || []).map(c => ({
      name: c.name,
      transformation: decomposeTransform(c.transform || [1, 0, 0, 1, 0, 0]),
      location: {},
    })),
    xAdvance: outline.width ?? 0,
    anchors: (outline.anchors || []).map(a => ({ name: a.name, x: a.x, y: a.y })),
  };
}

// PackedPath → sidecar nodes. An on-curve node's type comes from the
// off-curve run of the segment ENDING at it (wrapping for closed
// contours): none → "line", cubic → "curve", quad → "qcurve".
function packedPathToOutline(path) {
  const { coordinates, pointTypes, contourInfo } = path;
  const paths = [];
  let start = 0;
  for (const contour of contourInfo || []) {
    const n = contour.endPoint - start + 1;
    const types = [];
    for (let i = 0; i < n; i++) {
      types.push(pointTypes[start + i] & 0x07);
    }
    const runTypeBefore = (i) => {
      // Walk backwards from point i over off-curve points (wrapping for
      // closed contours); null = no off-curve run precedes it.
      let quad = null;
      for (let step = 1; step < n; step++) {
        const j = i - step;
        if (j < 0 && !contour.isClosed) break;
        const t = types[(j + n) % n];
        if (t === PT_OFF_CUBIC) quad = false;
        else if (t === PT_OFF_QUAD) quad = true;
        else break;
      }
      return quad;
    };
    const nodes = [];
    for (let i = 0; i < n; i++) {
      const x = coordinates[2 * (start + i)];
      const y = coordinates[2 * (start + i) + 1];
      const t = types[i];
      let type;
      if (t === PT_OFF_CUBIC || t === PT_OFF_QUAD) {
        type = 'offcurve';
      } else {
        const quad = runTypeBefore(i);
        type = quad === null ? 'line' : quad ? 'qcurve' : 'curve';
      }
      nodes.push([x, y, type]);
    }
    paths.push({ closed: !!contour.isClosed, nodes });
    start = contour.endPoint + 1;
  }
  return paths;
}

function staticGlyphToOutline(glyph) {
  return {
    width: glyph.xAdvance ?? 0,
    paths: packedPathToOutline(glyph.path || { coordinates: [], pointTypes: [], contourInfo: [] }),
    components: (glyph.components || []).map(c => ({
      name: c.name,
      transform: composeTransform(c.transformation || {}),
    })),
    anchors: (glyph.anchors || []).map(a => ({ name: a.name, x: a.x, y: a.y })),
  };
}

// fontTools-order 2x3 [xx, xy, yx, yy, dx, dy] <-> Fontra's
// DecomposedTransform (fontra-core transform.js, same math).
function composeTransform(t) {
  const rad = (d) => (d * Math.PI) / 180;
  const mul = (a, b) => [
    a[0] * b[0] + a[2] * b[1],
    a[1] * b[0] + a[3] * b[1],
    a[0] * b[2] + a[2] * b[3],
    a[1] * b[2] + a[3] * b[3],
    a[0] * b[4] + a[2] * b[5] + a[4],
    a[1] * b[4] + a[3] * b[5] + a[5],
  ];
  const tx = t.translateX || 0; const ty = t.translateY || 0;
  const cx = t.tCenterX || 0; const cy = t.tCenterY || 0;
  const rot = rad(t.rotation || 0);
  const cos = Math.cos(rot); const sin = Math.sin(rot);
  const sx = t.scaleX ?? 1; const sy = t.scaleY ?? 1;
  const skx = rad(t.skewX || 0); const sky = rad(t.skewY || 0);
  let m = [1, 0, 0, 1, tx + cx, ty + cy];
  m = mul(m, [cos, sin, -sin, cos, 0, 0]);
  m = mul(m, [sx, 0, 0, sy, 0, 0]);
  m = mul(m, [1, Math.tan(sky), Math.tan(skx), 1, 0, 0]);
  m = mul(m, [1, 0, 0, 1, -cx, -cy]);
  return m;
}

function decomposeTransform(m) {
  // fontra-core's decomposedFromTransform (QR-like).
  let [a, b, c, d] = [m[0], m[1], m[2], m[3]];
  const signA = Math.sign(a) || 1;
  if (signA < 0) { a *= signA; b *= signA; }
  const delta = a * d - b * c;
  let rotation = 0; let scaleX = 0; let scaleY = 0; let skewX = 0; let skewY = 0;
  if (a !== 0 || b !== 0) {
    const r = Math.sqrt(a * a + b * b);
    rotation = b >= 0 ? Math.acos(a / r) : -Math.acos(a / r);
    scaleX = r;
    scaleY = delta / r;
    skewX = Math.atan((a * c + b * d) / (r * r));
  } else if (c !== 0 || d !== 0) {
    const s = Math.sqrt(c * c + d * d);
    rotation = Math.PI / 2 - (d >= 0 ? Math.acos(-c / s) : -Math.acos(c / s));
    scaleX = delta / s;
    scaleY = s;
    skewY = Math.atan((a * c + b * d) / (s * s));
  }
  const deg = (r) => (r * 180) / Math.PI;
  return {
    translateX: m[4],
    translateY: m[5],
    rotation: deg(rotation),
    scaleX,
    scaleY,
    skewX: deg(skewX),
    skewY: deg(skewY),
    tCenterX: 0,
    tCenterY: 0,
  };
}

// ---------------------------------------------------------------------------
// Fontra change application (plain-data subset of fontra-core changes.js)
// ---------------------------------------------------------------------------

// Structural ops (pen/knife/delete-point territory) are rejected by name;
// everything else applies to plain data and is verified by the
// post-application structure compare.
const STRUCTURAL_OPS = new Set([
  'appendPath', 'deleteNTrailingContours', 'insertContour', 'deleteContour',
  'deletePoint', 'insertPoint',
]);

class ChangeRejection extends Error {}

function setPointPosition(path, pointIndex, x, y) {
  if (!path || !path.coordinates || pointIndex * 2 + 1 >= path.coordinates.length) {
    throw new ChangeRejection(`=xy point index ${pointIndex} out of range`);
  }
  path.coordinates[pointIndex * 2] = x;
  path.coordinates[pointIndex * 2 + 1] = y;
}

function moveAllWithFirstPoint(path, x, y) {
  const dx = x - path.coordinates[0];
  const dy = y - path.coordinates[1];
  for (let i = 0; i < path.coordinates.length; i += 2) {
    path.coordinates[i] += dx;
    path.coordinates[i + 1] += dy;
  }
}

function applyChangeNode(subject, change) {
  const path = change.p || [];
  for (const el of path) {
    if (subject == null || typeof subject !== 'object' || !(el in Object(subject))) {
      throw new ChangeRejection(`invalid change path: ${JSON.stringify(path)}`);
    }
    subject = subject[el];
  }
  const f = change.f;
  if (f) {
    const a = change.a || [];
    switch (f) {
      case '=': subject[a[0]] = a[1]; break;
      case 'd': delete subject[a[0]]; break;
      case '-': subject.splice(a[0], a[1] ?? 1); break;
      case '+': subject.splice(a[0], 0, ...a.slice(1)); break;
      case ':': subject.splice(a[0], a[1], ...a.slice(2)); break;
      case '=xy': setPointPosition(subject, a[0], a[1], a[2]); break;
      case 'moveAllWithFirstPoint': moveAllWithFirstPoint(subject, a[0], a[1]); break;
      default:
        if (STRUCTURAL_OPS.has(f)) {
          throw new ChangeRejection('structural'); // classified by the caller
        }
        throw new ChangeRejection(`unsupported edit operation '${f}'`);
    }
  }
  for (const child of change.c || []) {
    applyChangeNode(subject, child);
  }
}

// Structural signature of a StaticGlyph's path: contour count, per-contour
// point counts and the on/off-curve pattern. Drawn layers must keep the
// seed's structure (desktop: pen/knife tools are hidden for these).
function pathSignature(glyphPath) {
  const p = glyphPath || { coordinates: [], pointTypes: [], contourInfo: [] };
  return JSON.stringify({
    contours: (p.contourInfo || []).map(c => c.endPoint),
    types: (p.pointTypes || []).map(t => t & 0x07),
  });
}

// ---------------------------------------------------------------------------
// Source-model interpolation + reference stroke measurement
// ---------------------------------------------------------------------------

// Interpolate a glyph model's masters at a source-axis (tag-keyed) location
// — the natural-shape outline the brace seeds also use. Mirrors
// control_axes.py `_interpolated_seed`: per-axis triples are (min, FIRST
// master's value, max) over the masters, and an outline-incompatible glyph
// falls back to the first master's outline.
function interpolateGlyphOutline(model, locationByTag) {
  const masterLayers = model.masters.map(m =>
    model.glyph.layers.find(l => l.layerId === m.id && !(l.coordinates || []).length));
  const firstLayer = masterLayers[0];
  if (!firstLayer) throw new Error(`glyph '${model.glyph.name}' has no master layer`);
  const refPaths = (firstLayer.outline.paths || []).map(p => (p.nodes || []).length);
  const compatible = masterLayers.every(l =>
    l && (l.outline.paths || []).length === refPaths.length &&
    (l.outline.paths || []).every((p, i) => (p.nodes || []).length === refPaths[i]));
  if (!compatible) return firstLayer.outline;

  const tags = model.axes.map(a => a.tag);
  const triples = tags.map((t, i) => {
    const vals = model.masters.map(m => m.axesValues[i] ?? 0);
    return [Math.min(...vals), model.masters[0].axesValues[i] ?? 0, Math.max(...vals)];
  });
  const norm = (loc, i) => normalizeValue(loc, ...triples[i]);
  const masterLocs = model.masters.map(m =>
    Object.fromEntries(tags.map((t, i) => [t, norm(m.axesValues[i] ?? 0, i)])));
  const target = Object.fromEntries(
    tags.map((t, i) => [t, norm(locationByTag[t] ?? triples[i][1], i)]));
  const vm = new VariationModel(masterLocs);

  const paths = (firstLayer.outline.paths || []).map((p, pi) => ({
    closed: p.closed,
    nodes: (p.nodes || []).map((node, ni) => {
      const xs = masterLayers.map(l => l.outline.paths[pi].nodes[ni][0]);
      const ys = masterLayers.map(l => l.outline.paths[pi].nodes[ni][1]);
      return [vm.interpolateFromMasters(target, xs), vm.interpolateFromMasters(target, ys), node[2] || 'line'];
    }),
  }));
  const width = vm.interpolateFromMasters(
    target, masterLayers.map(l => l.outline.width ?? 0));
  // Components/anchors are not interpolated — the desktop copies the
  // first (default) master's.
  return {
    width,
    paths,
    components: firstLayer.outline.components || [],
    anchors: firstLayer.outline.anchors || [],
  };
}

// Vertical-stem and horizontal-bar thickness for one interpolated outline —
// a faithful port of the server's _measure_strokes (server.py): a horizontal
// scanline at 25% of the glyph's own ink height reads its vertical stems, a
// vertical scanline at mid-advance reads its horizontal bars, and the
// THINNEST ink run over 5 units is the stroke weight. Off-curve points are
// polygon vertices, matching both the server's DecomposingRecordingPen
// output and the HUD's live scan of Fontra's in-memory path. Components are
// not decomposed (the live scan doesn't see them either). Rounding matches
// the server (0.1 for lengths, 0.01 for contrast).
function measureStrokes(contours, advance) {
  const runs = (coord, vertical) => {
    const vals = [];
    for (const c of contours) {
      for (let i = 0; i < c.length; i++) {
        const p = c[i]; const q = c[(i + 1) % c.length];
        const a1 = vertical ? p[0] : p[1]; const b1 = vertical ? p[1] : p[0];
        const a2 = vertical ? q[0] : q[1]; const b2 = vertical ? q[1] : q[0];
        if ((a1 <= coord && coord < a2) || (a2 <= coord && coord < a1)) {
          vals.push(b1 + ((coord - a1) * (b2 - b1)) / (a2 - a1));
        }
      }
    }
    vals.sort((x, y) => x - y);
    const out = [];
    for (let i = 0; i + 1 < vals.length; i += 2) out.push(vals[i + 1] - vals[i]);
    return out.filter(v => v > 5);
  };
  const ys = [];
  for (const c of contours) for (const pt of c) ys.push(pt[1]);
  if (!ys.length) return null;
  const yMin = Math.min(...ys); const yMax = Math.max(...ys);
  const stems = runs(yMin + (yMax - yMin) * 0.25, false);
  const bars = runs(advance * 0.5, true);
  const out = { advance: Math.round(advance * 10) / 10 };
  if (stems.length) out.stem = Math.round(Math.min(...stems) * 10) / 10;
  if (bars.length) out.bar = Math.round(Math.min(...bars) * 10) / 10;
  if (stems.length && bars.length) {
    out.contrast = Math.round((Math.min(...stems) / Math.min(...bars)) * 100) / 100;
  }
  return out;
}

// ---------------------------------------------------------------------------
// The bridge
// ---------------------------------------------------------------------------

/**
 * Create a bridge for one editing session.
 *
 * dataset        — the live uploadDataset (sourceText, controlAxes, health)
 * tag            — control axis tag (session scope)
 * glyphName      — the glyph being edited
 * layerLocation  — sparse tag-keyed pins of the clicked layer (may be null)
 * studioAxes     — the app's axes list (display names + defaults)
 * onError        — (message) => void, surfaced in the studio UI
 * onOutlineEdited — (layerEntry, outline) => void; static-api stores it
 */
export async function createEditorBridge({ dataset, tag, glyphName, layerLocation, studioAxes, onError, onOutlineEdited }) {
  const axisSpec = (dataset.controlAxes || []).find(a => a.tag === tag);
  if (!axisSpec) throw new Error(`No control axis '${tag}'`);
  const model = await glyphModel(dataset.sourceText, glyphName);

  // ---- axes and name<->tag maps -----------------------------------------
  // Editor font axes: the source's axes (from the model) plus the session
  // control axis (from the sidecar). Display names come from the studio's
  // axes list when present (the same names App's location fragment uses).
  const studioNameByTag = new Map((studioAxes || []).map(a => [a.tag, a.name || a.tag]));
  const fontAxes = [
    ...model.axes.map(a => ({
      name: studioNameByTag.get(a.tag) || a.name || a.tag,
      tag: a.tag,
      minValue: a.min,
      defaultValue: a.default,
      maxValue: a.max,
    })),
    {
      name: studioNameByTag.get(tag) || axisSpec.name || tag,
      tag,
      minValue: axisSpec.min,
      defaultValue: axisSpec.default,
      maxValue: axisSpec.max,
    },
  ];
  const defaultLocationByName = Object.fromEntries(fontAxes.map(a => [a.name, a.defaultValue]));

  // A sidecar layer entry's full source-axis location (tag-keyed): its
  // sparse pins overlaid on the FIRST master's coordinates, then the
  // control axis's own value. (Desktop parity: regenerate_shadow fills
  // from font.masters[0] — not the Variable-Font-Origin master.) Used to
  // key editor sources, to navigate the session, and to find the entry an
  // edit belongs to.
  const masters0Fill = {};
  model.axes.forEach((a, i) => { masters0Fill[a.tag] = model.masters[0].axesValues[i] ?? 0; });
  const entryFullLocation = (entry) => {
    const full = { ...masters0Fill };
    Object.assign(full, entry.location || {});
    full[tag] = (entry.location || {})[tag] ?? axisSpec.default;
    return full;
  };
  const entryKey = (entry) => JSON.stringify(entryFullLocation(entry));

  // Full name-keyed location for the session: the clicked entry's own
  // full location when it exists (so the editor lands exactly on its
  // source), else the sparse pins overlaid on the fill. Keyed by axis
  // DISPLAY NAME (the desktop contract — App.jsx builds the location
  // fragment the same way).
  const sessionEntry = (axisSpec.layers || []).find(l =>
    l.glyph === glyphName && layerLocation &&
    JSON.stringify(Object.entries(l.location || {}).sort()) ===
      JSON.stringify(Object.entries(layerLocation).sort()));
  const fullSession = sessionEntry
    ? entryFullLocation(sessionEntry)
    : { ...masters0Fill, ...(layerLocation || {}) };
  const sessionLocation = { ...defaultLocationByName };
  for (const axis of fontAxes) {
    if (fullSession[axis.tag] !== undefined) {
      sessionLocation[axis.name] = Number(fullSession[axis.tag]);
    }
  }

  // ---- layers: masters + this axis's brace layers for the glyph ---------
  const masterIds = new Map(model.masters.map(m => [m.id, m]));
  const isMasterLayer = (l) => masterIds.has(l.layerId) && !(l.coordinates || []).length;

  const layers = {};   // layerName -> {glyph: StaticGlyph}
  const sources = [];  // VariableGlyph sources
  const layerInfo = {}; // layerName -> {kind: 'master'|'brace', entry?, editable, reason}

  for (const l of model.glyph.layers) {
    if (!isMasterLayer(l)) continue;
    const master = masterIds.get(l.layerId);
    const location = { ...defaultLocationByName };
    model.axes.forEach((a, i) => { location[studioNameByTag.get(a.tag) || a.name || a.tag] = master.axesValues[i]; });
    const layerName = `master:${l.layerId}`;
    layers[layerName] = { glyph: outlineToStaticGlyph(l.outline) };
    sources.push({ name: master.name, layerName, location });
    layerInfo[layerName] = {
      kind: 'master',
      editable: false,
      reason: 'Master layers come from the source file — edit the masters in the source (or the desktop app).',
    };
  }

  for (const entry of axisSpec.layers || []) {
    if (entry.glyph !== glyphName) continue;
    const isComputed = entry.target && Object.keys(entry.target).length > 0;
    const full = entryFullLocation(entry);
    const layerName = `brace:${entryKey(entry)}`;
    // The shown outline: a stored drawing wins; otherwise seed from the
    // masters at the layer's location (corrections: with the target's
    // parametric overrides) — the desktop's seeding rules.
    let outline;
    if (entry.outline && !isComputed) {
      outline = entry.outline;
    } else {
      const seedLoc = { ...full, ...(isComputed ? entry.target : {}) };
      outline = seedOutline(seedLoc);
    }
    const location = { ...defaultLocationByName };
    for (const axis of fontAxes) location[axis.name] = full[axis.tag];
    layers[layerName] = { glyph: outlineToStaticGlyph(outline) };
    sources.push({ name: sourceLabel(full, model, axisSpec, tag, isComputed), layerName, location });
    layerInfo[layerName] = {
      kind: 'brace',
      entry,
      editable: !isComputed,
      reason: isComputed
        ? 'This layer is computed (it has a correction target) and re-derived on every build — edits would be discarded. Remove the correction to hand-draw it.'
        : null,
    };
  }

  // The VariableGlyph as served to the editor (canonical bridge copy).
  const variableGlyph = {
    name: glyphName,
    axes: [],
    sources,
    layers,
  };

  const fontData = {
    glyphMap: { [glyphName]: model.glyph.codepoints || [] },
    axes: { axes: fontAxes.map(a => ({ ...a, label: a.name, hidden: false, mapping: [] })), mappings: [] },
    sources: Object.fromEntries(model.masters.map((m) => {
      const location = { ...defaultLocationByName };
      model.axes.forEach((a, j) => {
        location[studioNameByTag.get(a.tag) || a.name || a.tag] = m.axesValues[j];
      });
      const lineMetrics = {};
      if (m.ascender !== null && m.ascender !== undefined) lineMetrics.ascender = { value: m.ascender };
      if (m.descender !== null && m.descender !== undefined) lineMetrics.descender = { value: m.descender };
      return [m.id, {
        name: m.name,
        isSparse: false,
        location,
        lineMetricsHorizontalLayout: lineMetrics,
        italicAngle: 0,
        guidelines: [],
      }];
    })),
    unitsPerEm: dataset.health?.upm || 1000,
    customData: {},
    backEndInfo: { features: {}, projectManagerFeatures: {} },
    readOnly: false,
  };

  // Natural-shape seed of a brace layer: interpolate the masters' outlines
  // at the location (source axes only — the control axis is not spanned by
  // any master). Same rule as the desktop's `_interpolated_seed`.
  function seedOutline(locationByTag) {
    return interpolateGlyphOutline(model, locationByTag);
  }

  // Reference stroke metrics for the editor HUD — the desktop server's
  // /api/control-axes/<tag>/reference-metrics (server.py
  // control_axis_reference_metrics) ported to the static topology. The
  // server instantiates the last BUILD; here the figures come from the
  // PRISTINE source model (glyph_model masters, interpolated — no control
  // /grade/SPAC effect can leak in, and no rebuild invalidates them, so no
  // cache refresh is wired). The session's control axis drops out of the
  // location entirely — the pristine source doesn't span it, which IS the
  // server's "pinned to default". The edited glyph's own figures are its
  // pre-edit state, same as the desktop.
  const metricsModelCache = new Map([[glyphName, model]]);
  async function referenceMetrics(refsParam) {
    const refs = String(refsParam || 'H,N,O').split(',')
      .map(g => g.trim()).filter(Boolean);
    const location = { ...fullSession };
    delete location[tag];
    const metrics = {};
    for (const name of [glyphName, ...refs.filter(g => g !== glyphName)]) {
      let glyphModel_ = metricsModelCache.get(name);
      if (!glyphModel_) {
        try {
          glyphModel_ = await glyphModel(dataset.sourceText, name);
        } catch {
          continue; // server parity: glyphs missing from the font are skipped
        }
        metricsModelCache.set(name, glyphModel_);
      }
      const outline = interpolateGlyphOutline(glyphModel_, location);
      const contours = (outline.paths || [])
        .map(p => (p.nodes || []).map(n => [n[0], n[1]]))
        .filter(c => c.length > 2);
      const measured = measureStrokes(contours, outline.width ?? 0);
      if (measured) metrics[name] = measured;
    }
    return { tag, glyph: glyphName, location, metrics };
  }

  // ---- RPC surface --------------------------------------------------------

  const rpc = {
    getGlyphMap: () => fontData.glyphMap,
    getAxes: () => fontData.axes,
    getSources: () => fontData.sources,
    getUnitsPerEm: () => fontData.unitsPerEm,
    getCustomData: () => fontData.customData,
    getBackEndInfo: () => fontData.backEndInfo,
    isReadOnly: () => fontData.readOnly,
    getGlyph: (name) => {
      if (name === glyphName) return structuredClone(variableGlyph);
      // Referenced-but-not-loaded glyphs (e.g. component bases): an empty
      // glyph keeps the scene alive without pretending to be editable.
      console.warn(`editor-bridge: glyph '${name}' is not part of this session`);
      return { name, axes: [], sources: [], layers: {} };
    },
    getKerning: () => ({}),
    getFeatures: () => ({}),
    getFontInfo: () => ({}),
    findGlyphsThatUseGlyph: () => [],
    // Studio extra (no Fontra-backend counterpart): the metrics HUD's
    // reference figures. The embed bundle only calls it in studio sessions.
    getReferenceMetrics: (refs) => referenceMetrics(refs),
  };

  // ---- edit guards + application ------------------------------------------

  // Classify a change tree's scope; returns the set of layerNames it
  // touches, or throws ChangeRejection with a readable message.
  function guardChange(change) {
    const touched = new Set();
    const walk = (node, prefix) => {
      const full = prefix.concat(node.p || []);
      if (node.f || node.a !== undefined) {
        if (full[0] !== 'glyphs') {
          throw new ChangeRejection('Only glyph edits are supported in this session.');
        }
        if (full[1] !== glyphName) {
          throw new ChangeRejection(`This session edits '${glyphName}' only — '${full[1]}' is out of scope.`);
        }
        if (full[2] !== 'layers' || typeof full[3] !== 'string') {
          throw new ChangeRejection('Only layer outline edits are supported (no source list or glyph metadata edits).');
        }
        const layerName = full[3];
        const info = layerInfo[layerName];
        if (!info) {
          throw new ChangeRejection('The edited layer is not part of this session.');
        }
        if (!info.editable) {
          throw new ChangeRejection(info.reason);
        }
        if (node.f && STRUCTURAL_OPS.has(node.f)) {
          throw new ChangeRejection('structural');
        }
        touched.add(layerName);
      }
      for (const child of node.c || []) walk(child, full);
    };
    walk(change, []);
    return touched;
  }

  function rejectEdit(message) {
    const readable = message === 'structural'
      ? 'Points or contours were added/removed — a drawn brace layer must keep its structure (same reason the desktop hides the pen and knife tools here). Move existing points instead.'
      : message;
    bridge.onError?.(readable);
    send({ type: 'message', headline: 'Edit not kept', message: readable });
    // Revert the editor's optimistic local application.
    send({ type: 'reloadData', pattern: { glyphs: { [glyphName]: null } } });
  }

  function handleEditFinal(change) {
    let touched;
    try {
      touched = guardChange(change);
    } catch (err) {
      rejectEdit(err instanceof ChangeRejection ? err.message : String(err));
      return;
    }
    if (!touched.size) return;
    // Apply over snapshots: a failed application (bad path, or a
    // structure change caught by the post-apply compare) restores the
    // pre-edit layers so the canonical model never drifts from the
    // editor's state after a rejection (the editor is reverted via
    // reloadData either way).
    const snapshots = new Map(
      [...touched].map(ln => [ln, structuredClone(variableGlyph.layers[ln])]));
    const before = Object.fromEntries(
      [...touched].map(ln => [ln, pathSignature(variableGlyph.layers[ln].glyph.path)]));
    try {
      applyChangeNode({ glyphs: { [glyphName]: variableGlyph } }, change);
      for (const ln of touched) {
        if (pathSignature(variableGlyph.layers[ln].glyph.path) !== before[ln]) {
          throw new ChangeRejection('structural');
        }
      }
    } catch (err) {
      for (const [ln, snap] of snapshots) variableGlyph.layers[ln] = snap;
      rejectEdit(err instanceof ChangeRejection ? err.message : `The edit could not be applied (${err.message || err}).`);
      return;
    }
    for (const ln of touched) {
      const info = layerInfo[ln];
      if (info?.entry) {
        onOutlineEdited?.(info.entry, staticGlyphToOutline(variableGlyph.layers[ln].glyph));
      }
    }
  }

  // ---- messaging ------------------------------------------------------------

  let targetWindow = null;
  let disposed = false;
  let readySeen = false;
  const listeners = new Map(); // event -> Set<fn>  ('dirty', 'error', 'close-request')

  function send(message) {
    targetWindow?.postMessage({ [MESSAGE_KEY]: true, v: 0, ...message }, '*');
  }

  const onMessage = (event) => {
    if (disposed) return;
    const message = event.data;
    if (!message?.[MESSAGE_KEY]) return;
    if (targetWindow && event.source !== targetWindow) return;
    if (message.v !== undefined && message.v !== PROTOCOL_VERSION) {
      emit('error', new Error(
        `The glyph editor at ${embedEditorUrl()} speaks protocol v${message.v}, `
        + `this studio expects v${PROTOCOL_VERSION} — update one of them.`));
      return;
    }
    switch (message.type) {
      case 'ready':
        if (!readySeen) {
          readySeen = true;
          targetWindow = targetWindow || event.source;
          send({
            type: 'init',
            font: { title: `${glyphName} — ${axisSpec.name || tag}` },
            session: {
              studio: true,
              tag,
              axis_name: axisSpec.name || tag,
              axis_default: axisSpec.default,
              glyph: glyphName,
            },
            glyphName,
            location: sessionLocation,
          });
        }
        break;
      case 'editor-ready':
        emit('ready');
        break;
      case 'rpc': {
        const handler = rpc[message.method];
        const reply = (payload) =>
          send({ type: 'rpc-result', id: message.id, ...payload });
        if (!handler) {
          reply({ error: `unknown method ${message.method}` });
          break;
        }
        Promise.resolve()
          .then(() => handler(...(message.args || [])))
          .then((value) => reply({ value: value === undefined ? null : value }))
          .catch((error) => reply({ error: String(error && error.message || error) }));
        break;
      }
      case 'editFinal':
        handleEditFinal(message.change);
        break;
      case 'editIncremental':
        // Live drags: the final edit is guarded and applied on release;
        // rejecting mid-drag would spam errors, so drops are silent here.
        try { guardChange(message.change); } catch { /* dropped */ }
        break;
      case 'dirty':
        emit('dirty', !!message.value);
        break;
      case 'close-request':
        emit('close-request');
        break;
      case 'error':
        onError?.(String(message.error));
        break;
    }
  };

  function emit(event, ...args) {
    for (const fn of listeners.get(event) || []) fn(...args);
  }

  const bridge = {
    glyphName,
    tag,
    onError: onError || null, // reassignable: the modal subscribes directly
    on: (event, fn) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(fn);
    },
    attach(window_) {
      targetWindow = window_;
      window.addEventListener('message', onMessage);
    },
    detach() {
      window.removeEventListener('message', onMessage);
      targetWindow = null;
    },
    dispose() {
      disposed = true;
      bridge.detach();
      listeners.clear();
    },
    // Debug/e2e introspection (mirrors the __avar2api hook's spirit).
    _state: () => ({
      sessionLocation,
      layers: Object.keys(layers),
      editable: Object.fromEntries(
        Object.entries(layerInfo).map(([ln, i]) => [ln, i.editable])),
    }),
  };

  // Reachability: the embed posts ready once it's up (and reposts for
  // ~10s); nothing within 15s means the bundle isn't being served.
  const watchdog = setTimeout(() => {
    if (!readySeen) {
      const url = embedEditorUrl();
      bridge.onError?.(
        `The glyph editor didn't answer at ${url} — is the fontra-embed bundle being served there? ` +
        `(in the fontra-embed checkout: npm run build, then npx serve dist -l 8099)`);
    }
  }, 15000);
  bridge.on('ready', () => clearTimeout(watchdog));
  // Also clear on dispose to avoid a stale fire after close.
  const origDispose = bridge.dispose;
  bridge.dispose = () => { clearTimeout(watchdog); origDispose(); };

  return bridge;
}

// Source list label, desktop flavor ("<corner> · <tag> <value>"): the
// parametric part of the location named by the master corner when it
// matches one, plus the control value (and the correction's target).
function sourceLabel(full, model, axisSpec, controlTag, isComputed) {
  const corner = model.masters.find(m =>
    model.axes.every((a, i) => {
      const v = full[a.tag] ?? a.default;
      return Math.abs(v - (m.axesValues[i] ?? 0)) < 1e-9;
    }));
  const ctrlValue = full[controlTag] ?? axisSpec.default;
  const parts = [];
  if (corner) parts.push(corner.name);
  parts.push(`${controlTag} ${ctrlValue}`);
  return parts.join(' · ') + (isComputed ? ' → computed' : '');
}
