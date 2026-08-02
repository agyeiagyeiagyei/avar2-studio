/**
 * Minimal gvar reader for the coverage audit: extracts ONLY the
 * variation regions (min/peak/max per axis per tuple) — the de-facto
 * source positions of every glyph's variation data. The serialized
 * point data is never read.
 *
 * Layout (OpenType spec): gvar header → per-glyph offsets → per-glyph
 * GlyphVariationData (tupleVariationCount + headers). Each header
 * carries variationDataSize, a tupleIndex word (0x8000 = embedded
 * peak, 0x4000 = intermediate region, 0x0FFF = shared-tuple index),
 * an optional embedded peak tuple and optional intermediate start/end
 * tuples, all F2DOT14 int16. Non-intermediate regions are
 * (min(0, peak), peak, max(0, peak)).
 */

const F2DOT14 = 1 / 16384;

export function gvarRegions(view, gvarRec, axisCount) {
  const base = gvarRec.offset;
  const sharedCount = view.getUint16(base + 6);
  const sharedOff = base + view.getUint32(base + 8);
  const glyphCount = view.getUint16(base + 12);
  const longOffsets = (view.getUint16(base + 14) & 1) === 1;
  const dataOff = base + view.getUint32(base + 16);

  const sharedPeak = (idx) => {
    const p = sharedOff + idx * axisCount * 2;
    const out = new Float64Array(axisCount);
    for (let a = 0; a < axisCount; a++) out[a] = view.getInt16(p + a * 2) * F2DOT14;
    return out;
  };

  const offsetsAt = base + 20;
  const glyphOffset = (i) =>
    longOffsets
      ? view.getUint32(offsetsAt + i * 4)
      : view.getUint16(offsetsAt + i * 2) * 2;

  const tuples = [];
  for (let g = 0; g < glyphCount; g++) {
    const start = glyphOffset(g);
    const end = glyphOffset(g + 1);
    if (end <= start) continue;
    const gd = dataOff + start;
    // tupleVariationCount: low 12 bits are the count; 0x8000 means the
    // tuples share point numbers (TUPLES_SHARE_POINT_NUMBERS).
    const count = view.getUint16(gd) & 0x0fff;
    let h = gd + 4;
    for (let t = 0; t < count; t++) {
      const tupleIndex = view.getUint16(h + 2);
      h += 4;
      let peaks;
      if (tupleIndex & 0x8000) {
        peaks = new Float64Array(axisCount);
        for (let a = 0; a < axisCount; a++) peaks[a] = view.getInt16(h + a * 2) * F2DOT14;
        h += axisCount * 2;
      } else {
        const idx = tupleIndex & 0x0fff;
        peaks = idx < sharedCount ? sharedPeak(idx) : new Float64Array(axisCount);
      }
      const mins = new Float64Array(axisCount);
      const maxs = new Float64Array(axisCount);
      if (tupleIndex & 0x4000) {
        for (let a = 0; a < axisCount; a++) mins[a] = view.getInt16(h + a * 2) * F2DOT14;
        h += axisCount * 2;
        for (let a = 0; a < axisCount; a++) maxs[a] = view.getInt16(h + a * 2) * F2DOT14;
        h += axisCount * 2;
      } else {
        for (let a = 0; a < axisCount; a++) {
          mins[a] = Math.min(0, peaks[a]);
          maxs[a] = Math.max(0, peaks[a]);
        }
      }
      tuples.push({ glyph: g, mins, peaks, maxs });
    }
  }
  return tuples;
}
