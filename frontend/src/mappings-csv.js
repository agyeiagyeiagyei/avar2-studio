/**
 * Mappings CSV model for the static demo's authoring flow — the CSV is
 * the single source of truth for instances + avar2 mappings on uploaded
 * fonts, exactly as in the studio's workspace.
 *
 * Format: first column is the instance name; remaining columns are axis
 * tags — parametric (out) axes first by studio convention, then user
 * (in) axes. Rows are instance rows; every mutation serializes back to
 * text for the avar2 regen (wasm add_avar2).
 *
 * Numbers round-trip as raw strings from the CSV; writes use plain
 * String(value) like the studio's writer.
 */

const stripBom = (t) => t.replace(/^﻿/, '');

export function parseMappingsCsv(text) {
  const lines = stripBom(text).split('\n').filter(l => l.trim());
  if (!lines.length) return { nameCol: 'Instance Name', columns: [], rows: [] };
  const header = lines[0].split(',').map(s => s.trim());
  const nameCol = header[0] || 'Instance Name';
  const columns = header.slice(1);
  const rows = lines.slice(1).map(line => {
    const cells = line.split(',');
    const values = {};
    columns.forEach((tag, i) => { values[tag] = (cells[i + 1] ?? '').trim(); });
    return { name: (cells[0] ?? '').trim(), values };
  }).filter(r => r.name);
  return { nameCol, columns, rows };
}

export function serializeMappingsCsv(parsed) {
  const lines = [[parsed.nameCol, ...parsed.columns].join(',')];
  for (const row of parsed.rows) {
    lines.push([row.name, ...parsed.columns.map(tag => row.values[tag] ?? '')].join(','));
  }
  return lines.join('\n') + '\n';
}

/** Columns that are NOT fvar axes of the compiled font = user axes. */
export function userColumns(parsed, fvarTags) {
  const set = new Set(fvarTags);
  return parsed.columns.filter(t => !set.has(t));
}

const coordsForColumns = (columns, coords) => {
  const values = {};
  for (const tag of columns) {
    const v = coords?.[tag];
    values[tag] = v === undefined || v === null ? '' : String(v);
  }
  return values;
};

/** Insert or replace a row by name; insertAfter positions a new row.
 *  Only updates the values present in ``coords`` — other columns keep
 *  their existing values (previously every column was overwritten,
 *  wiping sibling slider values on a single-axis edit). */
export function upsertRow(parsed, name, coords, insertAfter = null) {
  const existing = parsed.rows.find(r => r.name === name);
  if (existing) {
    // Only assign the columns present in coords — leave the rest.
    for (const [tag, v] of Object.entries(coords || {})) {
      if (parsed.columns.includes(tag)) {
        existing.values[tag] = v === undefined || v === null ? '' : String(v);
      }
    }
    return;
  }
  const row = { name, values: coordsForColumns(parsed.columns, coords) };
  const at = insertAfter ? parsed.rows.findIndex(r => r.name === insertAfter) : -1;
  if (at >= 0) parsed.rows.splice(at + 1, 0, row);
  else parsed.rows.push(row);
}

export function renameRow(parsed, oldName, newName) {
  const row = parsed.rows.find(r => r.name === oldName);
  if (!row) throw new Error(`Instance "${oldName}" not found`);
  if (parsed.rows.some(r => r.name === newName)) {
    throw new Error(`An instance named "${newName}" already exists`);
  }
  row.name = newName;
}

export function deleteRow(parsed, name) {
  const at = parsed.rows.findIndex(r => r.name === name);
  if (at < 0) throw new Error(`Instance "${name}" not found`);
  parsed.rows.splice(at, 1);
}

/** Synthesize a CSV structure from the compiled font (parametric-only). */
export function synthesizeFromFont(metaAxes, metaInstances) {
  const parametric = metaAxes.filter(a => a.has_master_coverage !== false).map(a => a.tag);
  return {
    nameCol: 'Instance Name',
    columns: parametric,
    rows: (metaInstances || []).map(i => ({
      name: i.name,
      values: coordsForColumns(parametric, i.coordinates),
    })),
  };
}

/** Append a user-axis column with a default value in every row. */
export function addColumn(parsed, tag, defaultValue) {
  if (parsed.columns.includes(tag)) {
    throw new Error(`Axis column "${tag}" already exists`);
  }
  parsed.columns.push(tag);
  for (const row of parsed.rows) row.values[tag] = defaultValue === undefined ? '' : String(defaultValue);
}

/** Remove a user-axis column and every row's value for it. */
export function removeColumn(parsed, tag) {
  if (!parsed.columns.includes(tag)) {
    throw new Error(`Axis column "${tag}" does not exist`);
  }
  parsed.columns = parsed.columns.filter(c => c !== tag);
  for (const row of parsed.rows) delete row.values[tag];
}

// Column-name → registered tag (mirrors csv_io.normalize_in_axis_name).
const AXIS_NAME_MAP = { WGHT: 'wght', WDTH: 'wdth', OPSZ: 'opsz', CONTRAST: 'cntr', CNTR: 'cntr' };
export const normalizeInAxisName = (col) => AXIS_NAME_MAP[col.toUpperCase()] || col;

/** Derived min/default/max of a column's non-empty values (gen_fvar_axes). */
export function columnRange(parsed, tag) {
  const vals = parsed.rows.map(r => r.values[tag]).filter(v => v !== '').map(parseFloat);
  if (!vals.length) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  return { min, default: min, max };
}
