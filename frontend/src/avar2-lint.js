/**
 * avar2 mapping lint — the authoring-time guard for the two ways a
 * mappings CSV silently produces a dead or corrupted avar2 table.
 *
 * Both failures are invisible today: `add_avar2` builds a valid font
 * either way, and coverage.js can't see them (it audits gvar master
 * coverage over the 2^n axis-box corners, never the mapping rows and
 * never a coordinate sitting AT an axis default).
 *
 * The physics, in one paragraph. An avar2 table is an
 * ItemVariationStore: deltas from the default instance, weighted by
 * tents whose peaks are the CSV rows' normalized input locations. A
 * tent for a master at coordinate c != 0 on axis i never crosses zero
 * — its near edge is 0 — so at any location where axis i sits at its
 * default, that master scores exactly 0. Two consequences:
 *
 *   1. DEAD POINTS. A grid point is reachable only by a row sitting
 *      exactly on it: a row must be 0 wherever the probe is 0 (or its
 *      tent scores 0 there) and +-1 wherever the probe is +-1 (or its
 *      tent has already run out). So with only the 2^n extreme corners
 *      authored, every location where ANY input axis is at its default
 *      — the whole default cross — falls back to the output axes'
 *      fvar defaults. Fix: author the {min, default, max}^n grid.
 *
 *   2. DISCARDED ROWS. A row at the all-default input location becomes
 *      the model's BASE master. A VarStore has no base-value slot, so
 *      lib.rs:481 (`if region.is_default() { continue }`) drops it —
 *      and because every other row's delta is computed relative to
 *      that base, all of them are skewed by the discarded amount. The
 *      default instance is whatever the source compiled to; only
 *      re-origining the source can move it.
 *
 * Findings use coverage.js's shape: {severity, type, location, detail},
 * with `location` in USER coordinates so the Space tab can fly to it.
 */

const EPS = 1e-6;

/** fontTools normalizeValue: piecewise-linear, clamped to [-1, 1]. */
export function normalize(v, min, dflt, max) {
  if (!(v > dflt) && !(v < dflt)) return 0;
  if (v < dflt) return dflt === min ? 0 : -Math.min(1, (dflt - v) / (dflt - min));
  return dflt === max ? 0 : Math.min(1, (v - dflt) / (max - dflt));
}

/**
 * Input-axis triples derived the way lib.rs:286-352 does: min/max span
 * the column's non-empty cells, default = min, then axis metadata (when
 * the caller has any) overrides. Mirrors gftools gen_fvar_axes.
 */
export function deriveInputRanges(parsed, inputTags, metadata = {}) {
  const ranges = {};
  for (const tag of inputTags) {
    const vals = parsed.rows
      .map(r => Number.parseFloat(r.values[tag]))
      .filter(v => Number.isFinite(v));
    if (!vals.length) continue;
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const m = metadata[tag] || {};
    ranges[tag] = {
      min: Number.isFinite(m.min) ? m.min : min,
      default: Number.isFinite(m.default) ? m.default : (Number.isFinite(m.min) ? m.min : min),
      max: Number.isFinite(m.max) ? m.max : max,
    };
  }
  return ranges;
}

/** A blank cell means "absent" == normalized 0 == the axis default. */
const cellValue = (row, tag, range) => {
  const raw = (row.values[tag] ?? '').trim();
  if (!raw) return range.default;
  const v = Number.parseFloat(raw);
  return Number.isFinite(v) ? v : range.default;
};

const fmt = (n) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100));

/**
 * Lint a parsed mappings CSV.
 *
 * @param parsed          parseMappingsCsv() output
 * @param opts.parametricTags  output (parametric) column tags — everything
 *                             else in the CSV is an avar2 input axis
 * @param opts.outputRanges    {TAG: {min, default, max}} for the output axes,
 *                             read from the compiled font's fvar
 * @param opts.inputRanges     optional explicit input triples; derived from
 *                             the CSV + metadata when omitted
 * @param opts.metadata        optional axis-metadata overrides
 * @returns {{findings: Array}}
 */
export function lintAvar2Mappings(parsed, opts = {}) {
  const { parametricTags = [], outputRanges = {}, metadata = {} } = opts;
  const outSet = new Set(parametricTags);
  const inputTags = (parsed.columns || []).filter(t => !outSet.has(t));
  const outputTags = (parsed.columns || []).filter(t => outSet.has(t));
  const findings = [];
  if (!inputTags.length || !parsed.rows?.length) return { findings };

  const inputRanges = opts.inputRanges || deriveInputRanges(parsed, inputTags, metadata);
  const usable = inputTags.filter(t => inputRanges[t]);
  if (!usable.length) return { findings };

  const inputLoc = (row) =>
    Object.fromEntries(usable.map(t => [t, cellValue(row, t, inputRanges[t])]));
  const atDefault = (row) =>
    usable.every(t => Math.abs(cellValue(row, t, inputRanges[t]) - inputRanges[t].default) < EPS);

  // --- 1. rows at the default location (silently discarded + skewing) ---
  for (const row of parsed.rows) {
    if (!atDefault(row)) continue;
    const moved = [];
    for (const tag of outputTags) {
      const r = outputRanges[tag];
      const raw = (row.values[tag] ?? '').trim();
      if (!r || !raw) continue;
      const v = Number.parseFloat(raw);
      if (!Number.isFinite(v)) continue;
      if (Math.abs(normalize(v, r.min, r.default, r.max)) > EPS) {
        moved.push(`${tag} ${fmt(v)} (default ${fmt(r.default)})`);
      }
    }
    if (!moved.length) continue; // an identity base row is legitimate
    findings.push({
      severity: 'fail',
      type: 'avar2-default-row',
      location: inputLoc(row),
      detail:
        `"${row.name}" sits at the default location (${usable.map(t => `${t} ${fmt(inputRanges[t].default)}`).join(', ')}), ` +
        `where avar2 cannot apply deltas: ${moved.join(', ')} ` +
        `${moved.length === 1 ? 'is' : 'are'} discarded, and every other row is skewed by that amount. ` +
        `Move the origin in the source instead.`,
    });
  }

  // --- 2. unmapped {min, default, max}^n grid points (the dead cross) ---
  const axisGrid = usable.map(t => {
    const r = inputRanges[t];
    return [...new Set([r.min, r.default, r.max])].sort((a, b) => a - b);
  });
  const authored = parsed.rows.map(inputLoc);
  const covered = (point) =>
    authored.some(loc => usable.every(t => Math.abs(loc[t] - point[t]) < EPS));

  const total = axisGrid.reduce((n, g) => n * g.length, 1);
  for (let i = 0; i < total; i++) {
    let rem = i;
    const point = {};
    for (let a = 0; a < usable.length; a++) {
      point[usable[a]] = axisGrid[a][rem % axisGrid[a].length];
      rem = Math.floor(rem / axisGrid[a].length);
    }
    // the all-default centre is the origin — it is never authorable
    if (usable.every(t => Math.abs(point[t] - inputRanges[t].default) < EPS)) continue;
    if (covered(point)) continue;
    const where = usable.map(t => `${t} ${fmt(point[t])}`).join(', ');
    const onDefaultLine = usable.filter(t => Math.abs(point[t] - inputRanges[t].default) < EPS);
    findings.push({
      severity: 'fail',
      type: 'unmapped-mapping-point',
      location: point,
      detail: onDefaultLine.length
        ? `(${where}): no row sits here, and ${onDefaultLine.join('/')} at ${onDefaultLine.length === 1 ? 'its' : 'their'} default ` +
          `blocks every other row from reaching it — the mapping falls back to the output defaults. Add a row at this location.`
        : `(${where}): no row sits here — the mapping falls back to the output defaults. Add a row at this location.`,
    });
  }

  return { findings };
}
