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

// fontTools models.supportScalar, OT mode — the same semantics as
// coverage.js. A peak of 0 means the axis does not participate in the
// region (factor 1); without that rule a null tent (0,0,0) zeroes any
// region whenever its axis sits off-default, which silently killed
// every multi-axis contribution.
function tentFactor(start, peak, end, v) {
  if (peak === 0) return 1;
  if (start > peak || peak > end) return 1;
  if (start < 0 && end > 0) return 1; // OT forbids tents crossing zero
  if (v === peak) return 1;
  if (v <= start || end <= v) return 0;
  if (v < peak) return (v - start) / (peak - start);
  return (end - v) / (end - peak);
}

/**
 * Parse the avar table (v2 pieces) from a font's bytes.
 * Returns null when the font has no avar2 store.
 */
export function parseAvar2(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const safeGetUint16 = (off) => {
    if (off + 2 > view.byteLength) throw new Error(`parseAvar2: offset ${off} out of bounds (len=${view.byteLength})`);
    return view.getUint16(off);
  };
  const safeGetUint32 = (off) => {
    if (off + 4 > view.byteLength) throw new Error(`parseAvar2: offset ${off} out of bounds (len=${view.byteLength})`);
    return view.getUint32(off);
  };
  const safeGetUint8 = (off) => {
    if (off + 1 > view.byteLength) throw new Error(`parseAvar2: offset ${off} out of bounds (len=${view.byteLength})`);
    return view.getUint8(off);
  };
  // table directory
  const count = safeGetUint16(4);
  let avarRec = null;
  for (let i = 0; i < count; i++) {
    const rec = 12 + i * 16;
    if (String.fromCharCode(view.getUint8(rec), view.getUint8(rec + 1), view.getUint8(rec + 2), view.getUint8(rec + 3)) === 'avar') {
      avarRec = { offset: safeGetUint32(rec + 8), length: safeGetUint32(rec + 12) };
      break;
    }
  }
  if (!avarRec) return null;
  if (avarRec.offset + avarRec.length > view.byteLength) {
    console.warn('[parseAvar2] avar table extends beyond buffer — malformed font');
    return null;
  }

  const base = avarRec.offset;
  const major = safeGetUint16(base);
  if (major !== 2) return null; // v1 unsupported here (rare in our pipeline)

  let p = base + 8; // skip version(4) + reserved(2) + axisCount(2)
  const axisCount = safeGetUint16(base + 6);
  // v1 segment maps area (usually empty for a pure v2 table)
  for (let i = 0; i < axisCount; i++) {
    const mapCount = safeGetUint16(p);
    p += 2 + mapCount * 4;
  }

  // After the segment maps the v2 header has TWO Offset32 fields, both
  // relative to the TABLE start (0 = absent): axisIndexMapOffset, then
  // varStoreOffset. (An earlier revision read the structures inline at
  // `p` — which parsed the offset fields themselves as map data and
  // wandered off into garbage.)
  const axisIdxMapOffset = safeGetUint32(p);
  const varStoreOffset = safeGetUint32(p + 4);
  if (!varStoreOffset) return null; // v2 header but no store → nothing to eval

  // Optional DeltaSetIndexMap (spec layout: format, entryFormat, then
  // mapCount sized by format; entries are entrySize bytes, big-endian,
  // outer/inner split by innerBitCount — both from entryFormat).
  let dsim = null;
  if (axisIdxMapOffset) {
    const mBase = base + axisIdxMapOffset;
    const format = safeGetUint8(mBase);
    const entryFormat = safeGetUint8(mBase + 1);
    const entrySize = ((entryFormat & 0x30) >> 4) + 1; // MAP_ENTRY_SIZE_MASK
    const innerBits = (entryFormat & 0x0f) + 1;        // INNER_INDEX_BIT_COUNT_MASK
    const mapCount = format === 0 ? safeGetUint16(mBase + 2) : safeGetUint32(mBase + 2);
    const dataBase = mBase + (format === 0 ? 4 : 6);
    const entries = [];
    for (let i = 0; i < mapCount; i++) {
      let v = 0;
      for (let b = 0; b < entrySize; b++) {
        v = (v << 8) | safeGetUint8(dataBase + i * entrySize + b);
      }
      entries.push([v >>> innerBits, v & ((1 << innerBits) - 1)]);
    }
    dsim = entries;
  }

  // ItemVariationStore, at its offset from the table start
  const storeBase = base + varStoreOffset;
  const regionListOffset = safeGetUint32(storeBase + 2);
  const dataCount = safeGetUint16(storeBase + 6);
  const dataOffsets = [];
  for (let i = 0; i < dataCount; i++) {
    dataOffsets.push(safeGetUint32(storeBase + 8 + i * 4));
  }

  const rlBase = storeBase + regionListOffset;
  const regionAxisCount = safeGetUint16(rlBase);
  const regionCount = safeGetUint16(rlBase + 2);
  const regions = [];
  for (let r = 0; r < regionCount; r++) {
    const rBase = rlBase + 4 + r * regionAxisCount * 6;
    const tents = [];
    for (let a = 0; a < regionAxisCount; a++) {
      const off = rBase + a * 6;
      tents.push({
        start: (off + 2 <= view.byteLength ? view.getInt16(off) : 0) / F2DOT14,
        peak: (off + 4 <= view.byteLength ? view.getInt16(off + 2) : 0) / F2DOT14,
        end: (off + 6 <= view.byteLength ? view.getInt16(off + 4) : 0) / F2DOT14,
      });
    }
    regions.push(tents);
  }

  const subtables = dataOffsets.map((off) => {
    const dBase = storeBase + off;
    const itemCount = safeGetUint16(dBase);
    // Bit 0x8000 is the LONG_WORDS flag (32-bit deltas) — our writer
    // never sets it (deltas are F2Dot14-scale, they fit int16), but it
    // must not leak into the count.
    const shortDeltaCount = safeGetUint16(dBase + 2) & 0x7fff;
    const regionIndexCount = safeGetUint16(dBase + 4);
    const regionIndexes = [];
    for (let i = 0; i < regionIndexCount; i++) {
      regionIndexes.push(safeGetUint16(dBase + 6 + i * 2));
    }
    const itemsBase = dBase + 6 + regionIndexCount * 2;
    const items = [];
    for (let i = 0; i < itemCount; i++) {
      const deltas = [];
      const itemStride = shortDeltaCount * 2 + (regionIndexCount - shortDeltaCount);
      for (let j = 0; j < shortDeltaCount; j++) {
        const off = itemsBase + i * itemStride + j * 2;
        deltas.push(off + 2 <= view.byteLength ? view.getInt16(off) : 0);
      }
      for (let j = shortDeltaCount; j < regionIndexCount; j++) {
        const off = itemsBase + i * itemStride + shortDeltaCount * 2 + (j - shortDeltaCount);
        deltas.push(off + 1 <= view.byteLength ? view.getInt8(off) : 0);
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
    // DeltaSetIndexMap semantics: an axis index beyond mapCount uses
    // the LAST entry (write-fonts trims trailing duplicates).
    if (dsim && dsim.length) [outer, inner] = dsim[Math.min(i, dsim.length - 1)];
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
    // Axes appended AFTER the avar table was written (SPAC, GRAD,
    // control axes) sit beyond its axisCount — the mapping is identity
    // for them, so reflect the input instead of a NaN.
    const n = mapped[i];
    result[a.tag] = Number.isFinite(n)
      ? Math.round(denormalize(n, a) * 1000) / 1000
      : (coords[a.tag] ?? a.default);
  });
  return result;
}

// Re-export for one-stop imports in PreviewTab.
export { parseFont };
