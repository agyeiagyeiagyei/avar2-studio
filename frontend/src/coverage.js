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

// ---- layer B (behavioral probe) ---------------------------------------------
//
// Sweep each axis (at default, then with each other axis pinned at min
// and max) and measure outline area (the stem-darkness proxy from
// fontc-web's measure_at). Flags:
//   - collapse: real weight appears, then dies below half its peak —
//     the extrapolation-collapse signature.
//   - inert (default sweeps only): the sweep moves nothing — weight in
//     this design needs more than one axis.
// Thresholds are scale-free (ratios), since outline area is
// font-dependent.

export const PROBE_GLYPHS = ['a', 'e', 'o', 'g', 'A', 'H', 'n', 'x'];
const SWEEP_STEPS = 7;
const COLLAPSE_RATIO = 0.5;   // tail drops below this share of the peak
const MOVE_RATIO = 1.5;       // peak must clear baseline×this to count as movement
const INERT_RATIO = 0.05;     // sweep moves less than this share of its peak

export async function probeSweeps(bytes, axes, measureFn) {
  const defaultLoc = Object.fromEntries(axes.map(a => [a.tag, a.default]));
  const findings = [];
  for (const axis of axes) {
    const pins = [[null, null]];
    for (const other of axes) {
      if (other.tag !== axis.tag) pins.push([other.tag, other.min], [other.tag, other.max]);
    }
    for (const [other, pos] of pins) {
      const steps = [];
      for (let i = 0; i < SWEEP_STEPS; i++) {
        const loc = { ...defaultLoc, [axis.tag]: axis.min + (axis.max - axis.min) * i / (SWEEP_STEPS - 1) };
        if (other) loc[other] = pos;
        steps.push(loc);
      }
      const vals = await measureFn(bytes, PROBE_GLYPHS, steps);
      const peak = Math.max(...vals);
      const floor = Math.min(...vals);
      if (peak === 0) continue; // no probe glyphs in this font — nothing to measure
      const peakI = vals.indexOf(peak);
      const tail = vals.slice(peakI + 1);
      const label = `${axis.tag} sweep` + (other === null ? ' (others at default)' : ` with ${other} at ${pos}`);
      if (peak > MOVE_RATIO * floor && tail.length && Math.min(...tail) < COLLAPSE_RATIO * peak) {
        const end = steps[steps.length - 1];
        const at = axes.map(a => `${a.tag} ${end[a.tag]}`).join(', ');
        findings.push({
          severity: 'fail',
          type: 'collapse',
          location: end,
          detail: `${label}: collapses — stems vanish toward (${at})`,
        });
      } else if (other === null && peak > 0 && (peak - floor) < INERT_RATIO * peak) {
        findings.push({
          severity: 'info',
          type: 'inert-sweep',
          location: null,
          detail: `${label}: inert — weight here needs more than one axis`,
        });
      }
    }
  }
  return findings;
}
