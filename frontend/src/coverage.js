/**
 * Coverage audit — layer A (structural), a port of
 * spike/corner_audit.py to run on the compiled font in-browser.
 *
 * Diagnoses "missing corner" design-space coverage: axis-extreme
 * corners no source's tent reaches, and sources sitting outside the
 * axis box (out-of-range braces/masters). These never error at build
 * time — they only surface in axis usage (extrapolation collapse).
 *
 * Findings: {severity, type, location, reach?, detail}
 *   - uncovered-corner: location in USER coords, reach = per-axis
 *     edge coverage (0..1)
 *   - out-of-range-source: peak (normalized), glyphs affected
 */

import { parseFont } from './fvar.js';
import { gvarRegions } from './gvar.js';

const EPS = 1e-3;

// fontTools models.supportScalar, OT mode (peak 0 = axis doesn't
// participate; out-of-tent locations → 0).
function supportScalar(location, mins, peaks, maxs) {
  let scalar = 1;
  for (let a = 0; a < peaks.length; a++) {
    const peak = peaks[a];
    if (peak === 0) continue;
    const lower = mins[a];
    const upper = maxs[a];
    if (lower > peak || peak > upper) continue;
    if (lower < 0 && upper > 0) continue;
    const v = location[a] ?? 0;
    if (v === peak) continue;
    if (v <= lower || upper <= v) return 0;
    scalar *= v < peak ? (v - lower) / (peak - lower) : (v - upper) / (peak - upper);
  }
  return scalar;
}

function tables(view) {
  const map = {};
  const count = view.getUint16(4);
  for (let i = 0; i < count; i++) {
    const r = 12 + i * 16;
    map[String.fromCharCode(view.getUint8(r), view.getUint8(r + 1), view.getUint8(r + 2), view.getUint8(r + 3))] = {
      offset: view.getUint32(r + 8),
      length: view.getUint32(r + 12),
    };
  }
  return map;
}

export function auditCoverage(bytes) {
  const meta = parseFont(bytes);
  if (!meta.axes.length) return { findings: [], axes: [] };
  const tags = meta.axes.map(a => a.tag);
  const n = tags.length;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const gvarRec = tables(view).gvar;
  if (!gvarRec) return { findings: [], axes: meta.axes };

  const tuples = gvarRegions(view, gvarRec, n);
  const findings = [];

  // --- out-of-range sources (peaks beyond the axis box) ---
  const outGroups = new Map(); // rounded peak key -> {peak, glyphs:Set}
  for (const t of tuples) {
    if (!t.peaks.some(p => Math.abs(p) > 1 + EPS)) continue;
    const key = t.peaks.join(',');
    if (!outGroups.has(key)) outGroups.set(key, { peak: t.peaks, glyphs: new Set() });
    outGroups.get(key).glyphs.add(t.glyph);
  }
  for (const { peak, glyphs } of outGroups.values()) {
    const over = tags
      .map((tag, i) => (Math.abs(peak[i]) > 1 + EPS ? `${tag} ${peak[i] >= 0 ? '+' : ''}${peak[i].toFixed(2)}` : null))
      .filter(Boolean)
      .join(', ');
    findings.push({
      severity: 'fail',
      type: 'out-of-range-source',
      location: null,
      detail: `source outside the axis box (${over}) used by ${glyphs.size} glyph(s)`,
    });
  }

  // --- uncovered corners ---
  // The default master is a source (zero delta → no gvar tuple).
  const peaksWithOrigin = [[...Array(n).fill(0)], ...tuples.map(t => t.peaks)];
  const normDefault = meta.axes.map(a =>
    a.default === a.min ? -1 : a.default === a.max ? 1 : 0
  );
  const userCoord = (corner, i) => {
    const a = meta.axes[i];
    return corner[i] < 0 ? a.min : corner[i] > 0 ? a.max : a.default;
  };
  const cornerCount = 1 << n;
  for (let c = 0; c < cornerCount; c++) {
    const corner = Array.from({ length: n }, (_, i) => ((c >> i) & 1 ? 1.0 : -1.0));
    if (corner.every((v, i) => v === normDefault[i])) continue;
    const covered = tuples.some(t => supportScalar(corner, t.mins, t.peaks, t.maxs) > EPS);
    if (covered) continue;
    const reach = tags.map((_, i) => {
      const best = Math.max(0, ...peaksWithOrigin.map(p => p[i] * corner[i]));
      return Math.min(1, best);
    });
    const cornerTxt = tags.map((tag, i) => `${tag}${corner[i] > 0 ? '▲' : '▼'}`).join(' ');
    const reachTxt = tags.map((tag, i) => `${tag} ${Math.round(reach[i] * 100)}%`).join(', ');
    findings.push({
      severity: 'fail',
      type: 'uncovered-corner',
      location: Object.fromEntries(tags.map((tag, i) => [tag, userCoord(corner, i)])),
      reach,
      detail: `corner (${cornerTxt}): no source reaches it (edge coverage ${reachTxt})`,
    });
  }

  return { findings, axes: meta.axes };
}
