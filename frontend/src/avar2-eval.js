/**
 * avar v2 evaluation for the static demo — drives the Preview tab's
 * "parametric sliders follow the mapping" reflection without a server.
 *
 * avar v2 (current OT spec) = an optional DeltaSetIndexMap mapping each
 * fvar axis to a (outer, inner) delta-set index, plus an
 * ItemVariationStore of region tents + per-item delta values. Evaluating
 * at a location: for each axis, sum (region factor × delta) over its
 * subtable's regions, add to the axis's normalized value, then
 * denormalize back to user units.
 *
 * The build side (computing the store itself) lives in the fontc-web
 * wasm crate; this is only the eval, which is mechanical.
 */

import { parseFont } from './fvar.js';

const F2DOT14 = 16384;

function tentFactor(start, peak, end, v) {
  if (v < start || v > end) return 0;
  if (v === peak) return 1;
  if (start === peak && peak === end) return 0;
  if (v < peak) return (v - start) / (peak - start);
  return (end - v) / (end - peak);
}

/**
 * Parse the avar table (v2 pieces) from a font's bytes.
 * Returns null when the font has no avar2 store.
 */
export function parseAvar2(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  // table directory
  const count = view.getUint16(4);
  let avarRec = null;
  for (let i = 0; i < count; i++) {
    const rec = 12 + i * 16;
    if (String.fromCharCode(view.getUint8(rec), view.getUint8(rec + 1), view.getUint8(rec + 2), view.getUint8(rec + 3)) === 'avar') {
      avarRec = { offset: view.getUint32(rec + 8), length: view.getUint32(rec + 12) };
      break;
    }
  }
  if (!avarRec) return null;

  const base = avarRec.offset;
  const major = view.getUint16(base);
  if (major !== 2) return null; // v1 unsupported here (rare in our pipeline)

  let p = base + 8; // skip version(4) + reserved(2) + axisCount(2)
  const axisCount = view.getUint16(base + 6);
  // v1 segment maps area (usually empty for a pure v2 table)
  for (let i = 0; i < axisCount; i++) {
    const mapCount = view.getUint16(p);
    p += 2 + mapCount * 4;
  }

  // Optional DeltaSetIndexMap
  let dsim = null;
  const maybeFormat = view.getUint16(p);
  if (maybeFormat === 0 || maybeFormat === 1) {
    const entries = [];
    if (maybeFormat === 0) {
      const mapCount = view.getUint16(p + 2);
      for (let i = 0; i < mapCount; i++) {
        const e = view.getUint8(p + 4 + i);
        entries.push([e >> 4, e & 0xF]);
      }
      p += 4 + mapCount;
    } else {
      const mapCount = view.getUint32(p + 2);
      for (let i = 0; i < mapCount; i++) {
        const e = view.getUint16(p + 6 + i * 2);
        entries.push([e >> 8, e & 0xFF]);
      }
      p += 6 + mapCount * 2;
    }
    dsim = entries;
  }

  // ItemVariationStore
  const storeBase = p;
  const regionListOffset = view.getUint32(storeBase + 2);
  const dataCount = view.getUint16(storeBase + 6);
  const dataOffsets = [];
  for (let i = 0; i < dataCount; i++) {
    dataOffsets.push(view.getUint32(storeBase + 8 + i * 4));
  }

  const rlBase = storeBase + regionListOffset;
  const regionAxisCount = view.getUint16(rlBase);
  const regionCount = view.getUint16(rlBase + 2);
  const regions = [];
  for (let r = 0; r < regionCount; r++) {
    const rBase = rlBase + 4 + r * regionAxisCount * 6;
    const tents = [];
    for (let a = 0; a < regionAxisCount; a++) {
      tents.push({
        start: view.getInt16(rBase + a * 6) / F2DOT14,
        peak: view.getInt16(rBase + a * 6 + 2) / F2DOT14,
        end: view.getInt16(rBase + a * 6 + 4) / F2DOT14,
      });
    }
    regions.push(tents);
  }

  const subtables = dataOffsets.map((off) => {
    const dBase = storeBase + off;
    const itemCount = view.getUint16(dBase);
    const shortDeltaCount = view.getUint16(dBase + 2);
    const regionIndexCount = view.getUint16(dBase + 4);
    const regionIndexes = [];
    for (let i = 0; i < regionIndexCount; i++) {
      regionIndexes.push(view.getUint16(dBase + 6 + i * 2));
    }
    const itemsBase = dBase + 6 + regionIndexCount * 2;
    const items = [];
    for (let i = 0; i < itemCount; i++) {
      const deltas = [];
      for (let j = 0; j < shortDeltaCount; j++) {
        deltas.push(view.getInt16(itemsBase + i * (shortDeltaCount * 2 + (regionIndexCount - shortDeltaCount)) + j * 2));
      }
      for (let j = shortDeltaCount; j < regionIndexCount; j++) {
        deltas.push(view.getInt8(itemsBase + i * (shortDeltaCount * 2 + (regionIndexCount - shortDeltaCount)) + shortDeltaCount * 2 + (j - shortDeltaCount)));
      }
      items.push(deltas);
    }
    return { regionIndexes, items };
  });

  return { axisCount, dsim, regions, subtables };
}

/**
 * Evaluate the avar2 store at a normalized location.
 * coords: array of normalized (-1..1) values in FINAL fvar axis order.
 * Returns a new array with the mapped (deltas applied, clamped) values.
 */
export function evalAvar2(parsed, coords) {
  if (!parsed) return coords.slice();
  const { axisCount, dsim, regions, subtables } = parsed;
  const out = coords.slice(0, axisCount);
  for (let i = 0; i < axisCount; i++) {
    let outer = 0;
    let inner = i;
    if (dsim && dsim[i]) [outer, inner] = dsim[i];
    const sub = subtables[outer];
    if (!sub) continue;
    const items = sub.items[inner];
    if (!items) continue;
    let delta = 0;
    for (let j = 0; j < sub.regionIndexes.length; j++) {
      const tents = regions[sub.regionIndexes[j]];
      let factor = 1;
      for (let a = 0; a < tents.length; a++) {
        const t = tents[a];
        factor *= tentFactor(t.start, t.peak, t.end, coords[a] ?? 0);
        if (factor === 0) break;
      }
      delta += factor * items[j];
    }
    out[i] = Math.max(-1, Math.min(1, (coords[i] ?? 0) + delta / F2DOT14));
  }
  return out;
}

/**
 * Convenience for the Preview tab: given the font bytes (parsed via
 * fvar.js for tags/ranges) and a user-space coords object {tag: value},
 * return the mapped coords object in user units.
 */
export function mappedLocation(fontBytes, axes, coords) {
  const parsed = parseAvar2(fontBytes);
  if (!parsed) return { ...coords };
  const normalize = (v, a) => {
    if (v === a.default) return 0;
    if (v < a.default) return -(a.default - v) / (a.default - a.min);
    return (v - a.default) / (a.max - a.default);
  };
  const denormalize = (n, a) => {
    if (n === 0) return a.default;
    if (n < 0) return a.default + n * (a.default - a.min);
    return a.default + n * (a.max - a.default);
  };
  const normalized = axes.map(a => normalize(coords[a.tag] ?? a.default, a));
  const mapped = evalAvar2(parsed, normalized);
  const result = {};
  axes.forEach((a, i) => {
    result[a.tag] = Math.round(denormalize(mapped[i], a) * 1000) / 1000;
  });
  return result;
}

// Re-export for one-stop imports in PreviewTab.
export { parseFont };
