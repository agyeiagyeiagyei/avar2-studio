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
    if (nameID === 0 || nameID > 25) continue;
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

export function parseFont(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
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
