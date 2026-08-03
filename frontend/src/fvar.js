/**
 * Minimal TTF reader for the static demo's upload path: pulls exactly
 * what the studio needs out of the compiled font — family name (name
 * table), axes (fvar), named instances (fvar), and upm (head). No
 * shaping, no outlines — the browser's own variable-font rendering
 * does all of that.
 *
 * Usage: const meta = parseFont(bytes /* Uint8Array *\/)
 */

const TAG = (view, off) => String.fromCharCode(view.getUint8(off), view.getUint8(off + 1), view.getUint8(off + 2), view.getUint8(off + 3));

function tables(view) {
  const map = {};
  const count = view.getUint16(4);
  for (let i = 0; i < count; i++) {
    const rec = 12 + i * 16;
    map[TAG(view, rec)] = {
      offset: view.getUint32(rec + 8),
      length: view.getUint32(rec + 12),
    };
  }
  return map;
}

// name table: nameID → best string (UTF-16BE Windows first, then Mac).
// No nameID range filter — axis/instance names live at IDs ≥ 256.
function parseNames(view, rec) {
  const out = {};
  if (!rec) return out;
  const base = rec.offset;
  const count = view.getUint16(base + 2);
  const stringOff = view.getUint16(base + 4);
  for (let i = 0; i < count; i++) {
    const r = base + 6 + i * 12;
    const platform = view.getUint16(r);
    const nameID = view.getUint16(r + 6);
    const length = view.getUint16(r + 8);
    const offset = view.getUint16(r + 10);
    const start = base + stringOff + offset;
    let value = null;
    if (platform === 3 || platform === 0) {
      const bytes = new Uint8Array(view.buffer, view.byteOffset + start, length);
      value = new TextDecoder('utf-16be').decode(bytes);
    } else if (platform === 1) {
      const bytes = new Uint8Array(view.buffer, view.byteOffset + start, length);
      value = new TextDecoder('macintosh').decode(bytes);
    }
    if (value && !(nameID in out)) out[nameID] = value;
  }
  return out;
}

export function parseFont(bytes) {  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const dir = tables(view);

  const names = parseNames(view, dir.name);
  const familyName = names[16] || names[1] || 'Uploaded font';

  let upm = 1000;
  if (dir.head) upm = view.getUint16(dir.head.offset + 18);

  const axes = [];
  const instances = [];
  if (dir.fvar) {
    const base = dir.fvar.offset;
    const axisCount = view.getUint16(base + 8);
    const axisSize = view.getUint16(base + 10);
    const instanceCount = view.getUint16(base + 12);
    const instanceSize = view.getUint16(base + 14);
    for (let i = 0; i < axisCount; i++) {
      const a = base + 16 + i * axisSize;
      axes.push({
        tag: TAG(view, a),
        min: view.getInt32(a + 4) / 65536,
        default: view.getInt32(a + 8) / 65536,
        max: view.getInt32(a + 12) / 65536,
        flags: view.getUint16(a + 16),
        name: names[view.getUint16(a + 18)] || TAG(view, a),
      });
    }
    const instBase = base + 16 + axisCount * axisSize;
    for (let i = 0; i < instanceCount; i++) {
      const r = instBase + i * instanceSize;
      const nameID = view.getUint16(r);
      const coordinates = {};
      axes.forEach((axis, j) => {
        coordinates[axis.tag] = view.getInt32(r + 4 + j * 4) / 65536;
      });
      instances.push({ name: names[nameID] || `Instance ${i + 1}`, coordinates });
    }
  }

  return { familyName, upm, axes, instances };
}

// post table (format 2.0): glyph order as names — glyphNameIndex plus
// Pascal strings for custom names (index ≥ 258). Used by the Space tab
// to name brace layers' glyphs.
import { MAC_GLYPH_NAMES } from './mac-glyph-names.js';

export function parseGlyphNames(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const dir = tables(view);
  const rec = dir.post;
  if (!rec) return [];
  const base = rec.offset;
  if (view.getUint32(base) !== 0x00020000) return [];
  // fontc writes 0 in post's own numGlyphs field; fontTools (and we)
  // read the real count from maxp. post 2.0's full header is 34
  // bytes; glyphNameIndex follows it, then Pascal strings.
  const numGlyphs = dir.maxp ? view.getUint16(dir.maxp.offset + 4) : view.getUint16(base + 4);
  const idx = [];
  for (let i = 0; i < numGlyphs; i++) idx.push(view.getUint16(base + 34 + i * 2));
  const pascal = [];
  let p = base + 34 + numGlyphs * 2;
  const end = base + rec.length;
  while (p < end) {
    const len = view.getUint8(p);
    pascal.push(new TextDecoder().decode(new Uint8Array(view.buffer, view.byteOffset + p + 1, len)));
    p += 1 + len;
  }
  return idx.map(gi => (gi < 258 ? MAC_GLYPH_NAMES[gi] : pascal[gi - 258]) || `glyph${gi}`);
}

/**
 * Minimal STAT reader for export verification: axis records (tag,
 * nameID, ordering), format-aware axis value records, and
 * elidedFallbackNameID. Value records are reached via the uint16
 * offset array at AxisValueArray (offsets relative to that array's
 * start); each record's own uint16 Format field selects the layout.
 */
export function parseStat(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const count = view.getUint16(4);
  let rec = null;
  for (let i = 0; i < count; i++) {
    const r = 12 + i * 16;
    if (String.fromCharCode(view.getUint8(r), view.getUint8(r + 1), view.getUint8(r + 2), view.getUint8(r + 3)) === 'STAT') {
      rec = { offset: view.getUint32(r + 8), length: view.getUint32(r + 12) };
      break;
    }
  }
  if (!rec) return null;
  const base = rec.offset;
  const result = {
    version: `${view.getUint16(base)}.${view.getUint16(base + 2)}`,
    elidedFallbackNameID: view.getUint16(base + 18),
    axes: [],
    values: [],
  };
  const axisCount = view.getUint16(base + 6);
  const designAxesOffset = view.getUint32(base + 8);
  const axisValueCount = view.getUint16(base + 12);
  const axisValueArrayOffset = view.getUint32(base + 14);
  for (let i = 0; i < axisCount; i++) {
    const a = base + designAxesOffset + i * 8;
    result.axes.push({
      tag: TAG(view, a),
      nameID: view.getUint16(a + 4),
      ordering: view.getUint16(a + 6),
    });
  }
  const arrBase = base + axisValueArrayOffset;
  for (let i = 0; i < axisValueCount; i++) {
    const v = arrBase + view.getUint16(arrBase + i * 2);
    const format = view.getUint16(v);
    if (format === 4) {
      const n = view.getUint16(v + 2);
      const value = { format, flags: view.getUint16(v + 4), nameID: view.getUint16(v + 6), values: [] };
      for (let j = 0; j < n; j++) {
        const r = v + 8 + j * 6;
        value.values.push({ axisIndex: view.getUint16(r), value: view.getInt32(r + 2) / 65536 });
      }
      result.values.push(value);
      continue;
    }
    const value = {
      format,
      axisIndex: view.getUint16(v + 2),
      flags: view.getUint16(v + 4),
      nameID: view.getUint16(v + 6),
    };
    if (format === 1) {
      value.value = view.getInt32(v + 8) / 65536;
    } else if (format === 2) {
      value.nominalValue = view.getInt32(v + 8) / 65536;
      value.rangeMinValue = view.getInt32(v + 12) / 65536;
      value.rangeMaxValue = view.getInt32(v + 16) / 65536;
    } else if (format === 3) {
      value.value = view.getInt32(v + 8) / 65536;
      value.linkedValue = view.getInt32(v + 12) / 65536;
    }
    result.values.push(value);
  }
  return result;
}
