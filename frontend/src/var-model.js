/**
 * var-model.js — a minimal port of fontTools.varLib.models.VariationModel
 * (the subset the editor bridge seeds with: getDeltas /
 * interpolateFromMasters), f64 throughout so results match the desktop's
 * Python math bit-for-bit. Fontra's fontra-core var-model.js is the same
 * port; this copy keeps the studio bundle free of fontra imports.
 *
 * Locations are sparse normalized dicts ({axis: value}); zero values are
 * stripped on construction (fontTools does the same to every input).
 */

export function normalizeValue(v, lo, dflt, hi) {
  // fontTools models.normalizeValue, including its clamp.
  v = Math.min(Math.max(v, lo), hi);
  if (v === dflt) return 0;
  return v < dflt ? (v - dflt) / (dflt - lo) : (v - dflt) / (hi - dflt);
}

const locKey = (loc) => JSON.stringify(Object.fromEntries(
  Object.keys(loc).sort().map(k => [k, loc[k]])
));

export function supportScalar(loc, support) {
  // fontTools supportScalar(ot=True): a peak of 0 means the axis does not
  // participate; inverted or origin-crossing regions are ignored.
  let scalar = 1.0;
  for (const [axis, [lower, peak, upper]] of Object.entries(support)) {
    if (peak === 0 || lower > peak || peak > upper || (lower < 0 && upper > 0)) continue;
    const v = loc[axis] || 0.0;
    if (v === peak) continue;
    if (v <= lower || upper <= v) return 0.0;
    scalar *= v < peak ? (v - lower) / (peak - lower) : (v - upper) / (peak - upper);
  }
  return scalar;
}

export class VariationModel {
  constructor(locations) {
    this.locations = locations.map(loc =>
      Object.fromEntries(Object.entries(loc).filter(([, v]) => v !== 0)));
    const keys = new Set(this.locations.map(locKey));
    if (!keys.has('{}')) throw new Error('variation model: no base master');
    if (keys.size !== this.locations.length) throw new Error('variation model: duplicate locations');
    // fontTools sortedLocations (no axisOrder): rank, then decreasing
    // on-point axes, then the axis-order indices, names, signs, values —
    // stable on the original order (JS sort is stable, like Python's).
    const axisPoints = {};
    for (const loc of this.locations) {
      const keys = Object.keys(loc);
      if (keys.length !== 1) continue;
      const axis = keys[0];
      if (axisPoints[axis] === undefined) axisPoints[axis] = new Set([0.0]);
      axisPoints[axis].add(loc[axis]);
    }
    const decorated = this.locations.map((loc, i) => {
      const axes = Object.keys(loc).sort();
      let onPoint = 0;
      for (const axis of axes) {
        if (axisPoints[axis] !== undefined && axisPoints[axis].has(loc[axis])) onPoint++;
      }
      return {
        key: [
          axes.length,
          -onPoint,
          axes.map(() => 0x10000),
          axes,
          axes.map(a => Math.sign(loc[a])),
          axes.map(a => Math.abs(loc[a])),
        ],
        loc,
        i,
      };
    });
    const cmp = (a, b) => {
      for (let i = 0; i < Math.max(a.length, b.length); i++) {
        const x = a[i]; const y = b[i];
        if (x === undefined) return -1;
        if (y === undefined) return 1;
        if (typeof x === 'number' || typeof x === 'string') {
          if (x !== y) return x < y ? -1 : 1;
        } else {
          const c = cmp(x, y);
          if (c) return c;
        }
      }
      return 0;
    };
    decorated.sort((a, b) => cmp(a.key, b.key) || (a.i - b.i));
    this.locations = decorated.map(d => d.loc);
    // mapping[originalIndex] = sortedIndex; sortedToUser[sortedIndex] =
    // originalIndex (fontTools' mapping / reverseMapping).
    this.mapping = new Array(locations.length);
    this.sortedToUser = new Array(locations.length);
    decorated.forEach((d, sortedI) => {
      this.mapping[d.i] = sortedI;
      this.sortedToUser[sortedI] = d.i;
    });

    // Supports (regions): each axis's region is (0, v, +1) / (-1, v, 0) —
    // the axis range is the full normalized span (current fontTools'
    // default axisRanges), then the box-splitting walk against previous
    // masters with the SAME axis set.
    this.supports = this.locations.map(loc => {
      const region = {};
      for (const [axis, v] of Object.entries(loc)) {
        region[axis] = v > 0 ? [0, v, 1] : [-1, v, 0];
      }
      return region;
    });
    for (let i = 0; i < this.supports.length; i++) {
      const region = this.supports[i];
      const locAxes = new Set(Object.keys(region));
      for (let j = 0; j < i; j++) {
        const prev = this.supports[j];
        const prevAxes = Object.keys(prev);
        // Masters with different axis sets do not participate.
        if (prevAxes.length !== locAxes.size || !prevAxes.every(a => locAxes.has(a))) continue;
        let relevant = true;
        for (const [axis, [lower, peak, upper]] of Object.entries(region)) {
          const prevPeak = prev[axis]?.[1];
          if (prevPeak === undefined || !(prevPeak === peak || (lower < prevPeak && prevPeak < upper))) {
            relevant = false;
            break;
          }
        }
        if (!relevant) continue;
        // Split the box where the previous master sits, in the
        // direction(s) with the largest range ratio.
        let bestRatio = -1;
        let best = {};
        for (const [axis, [, val]] of Object.entries(prev)) {
          const [lower, locV, upper] = region[axis];
          let newLower = lower; let newUpper = upper; let ratio;
          if (val < locV) {
            newLower = val;
            ratio = (val - locV) / (lower - locV);
          } else if (locV < val) {
            newUpper = val;
            ratio = (val - locV) / (upper - locV);
          } else {
            continue;
          }
          if (ratio > bestRatio) { best = {}; bestRatio = ratio; }
          if (ratio === bestRatio) best[axis] = [newLower, locV, newUpper];
        }
        Object.assign(region, best);
      }
    }
    // Delta weights.
    this.deltaWeights = this.locations.map((loc, i) => {
      const weights = new Map();
      for (let j = 0; j < i; j++) {
        const scalar = supportScalar(loc, this.supports[j]);
        if (scalar) weights.set(j, scalar);
      }
      return weights;
    });
  }

  // fontTools getDeltas: masterValues in the caller's (original) order,
  // deltas in the model's sorted order.
  getDeltas(masterValues) {
    const out = [];
    for (let i = 0; i < masterValues.length; i++) {
      let delta = masterValues[this.sortedToUser[i]];
      for (const [j, weight] of this.deltaWeights[i]) {
        delta -= weight === 1 ? out[j] : out[j] * weight;
      }
      out.push(delta);
    }
    return out;
  }

  getScalars(loc) {
    return this.supports.map(support => supportScalar(loc, support));
  }

  // fontTools getMasterScalars: per-MASTER multipliers for the location
  // (delta scalars folded back through the delta weights), in the
  // caller's original master order.
  getMasterScalars(loc) {
    const out = this.getScalars(loc);
    for (let i = this.deltaWeights.length - 1; i >= 0; i--) {
      for (const [j, weight] of this.deltaWeights[i]) {
        out[j] -= out[i] * weight;
      }
    }
    return this.mapping.map(sortedI => out[sortedI]);
  }

  interpolateFromMasters(loc, masterValues) {
    // fontTools interpolateFromValuesAndScalars: skips zero scalars.
    const scalars = this.getMasterScalars(loc);
    let v = 0;
    for (let i = 0; i < scalars.length; i++) {
      if (!scalars[i]) continue;
      v += masterValues[i] * scalars[i];
    }
    return v;
  }
}
