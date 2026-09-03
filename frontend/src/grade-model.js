/**
 * Grade model constants + the per-instance slider cap — a port of the
 * pure maths in src/avar2_studio/grade.py (the wasm carries the same
 * constants in braces.rs; keep all three in sync).
 *
 * The pure-weight model: XOPQ (stems) + YOPQ (horizontals) drive, XTRA
 * (counters) follows at COMP_RATIO × the stem move to hold width.
 */

export const K_YOPQ = 1.0;
export const COMP_RATIO = 2.0;
export const PARAM_TAGS = ['XTRA', 'XOPQ', 'YOPQ'];

/**
 * grade.py `max_pct_for`: the largest grade% before any parametric axis
 * would clamp at `base` — the UI bounds the slider with it so a grade
 * the parametric space can't deliver is simply unreachable.
 *
 * A cap of exactly 0 is a REAL answer, not a missing one: an instance on
 * the XTRA floor has no counter headroom, so no grade% is deliverable
 * there. Filtering zeros out advertised the next-loosest axis's cap
 * instead, which let the widest/heaviest instances offer grades that could
 * only bleed into the counters.
 *
 * @param base   {XTRA, XOPQ, YOPQ} user-space coords of the instance
 * @param ranges {TAG: [min, max]} parametric axis extents
 * @returns the cap, or a generous 2.0 when nothing binds
 */
export function maxPctFor(base, ranges) {
  const o = base?.XOPQ;
  if (o === undefined || o === null || o <= 0) return 2.0;
  const caps = [];
  const bound = (tag, v, halfPerPct) => {
    const r = ranges[tag];
    if (!r || halfPerPct <= 0) return;
    caps.push((v - r[0]) / halfPerPct); // room to the axis floor
    caps.push((r[1] - v) / halfPerPct); // room to the axis ceiling
  };
  // XOPQ (driver): half-move per pct = o/2
  bound('XOPQ', o, o / 2.0);
  // YOPQ (driver): half-move = K_YOPQ*y/2
  const y = base.YOPQ;
  if (y && y > 0) bound('YOPQ', y, (K_YOPQ * y) / 2.0);
  // XTRA (follower): half-move scales with the STEM move, not XTRA
  const x = base.XTRA;
  if (x && x > 0) bound('XTRA', x, (COMP_RATIO * o) / 2.0);
  return Math.min(...caps.filter(c => c >= 0), 2.0);
}

/**
 * grade.py `grade_coords`: (light, dark) parametric coords for a grade at
 * `base`. The stem move (the driver) is limited to what BOTH the driver's
 * own range and the follower's headroom can absorb, then the horizontals
 * are scaled by the fraction actually achieved — so an instance pinned on
 * the XTRA floor does not thicken into counters that cannot open.
 *
 * @param base   {XTRA, XOPQ, YOPQ} user-space coords
 * @param pct    effective grade% (already scaled by intensity)
 * @param ranges {TAG: [min, max]}
 * @param clampToHeadroom  false restores uncompensated darkening
 */
export function gradeCoords(base, pct, ranges, clampToHeadroom = true) {
  const g = (t) => base?.[t] ?? 0;
  const [x, o, y] = [g('XTRA'), g('XOPQ'), g('YOPQ')];
  const dOHalf = (pct * o) / 2;
  const dYHalf = (pct * K_YOPQ * y) / 2;
  const range = (t) => ranges[t] || [-Infinity, Infinity];
  const clamp = (t, v) => {
    const [lo, hi] = range(t);
    return Math.max(lo, Math.min(hi, v));
  };
  const [loO, hiO] = range('XOPQ');
  const [loX, hiX] = range('XTRA');

  let darkRoom = Math.max(0, hiO - o);
  let lightRoom = Math.max(0, o - loO);
  if (clampToHeadroom && COMP_RATIO > 0) {
    darkRoom = Math.min(darkRoom, Math.max(0, (x - loX) / COMP_RATIO));
    lightRoom = Math.min(lightRoom, Math.max(0, (hiX - x) / COMP_RATIO));
  }
  const darkDO = Math.min(dOHalf, darkRoom);
  const lightDO = Math.min(dOHalf, lightRoom);
  const sDark = dOHalf > 0 ? darkDO / dOHalf : 0;
  const sLight = dOHalf > 0 ? lightDO / dOHalf : 0;

  return [
    {
      XTRA: clamp('XTRA', x + COMP_RATIO * lightDO),
      XOPQ: clamp('XOPQ', o - lightDO),
      YOPQ: clamp('YOPQ', y - sLight * dYHalf),
    },
    {
      XTRA: clamp('XTRA', x - COMP_RATIO * darkDO),
      XOPQ: clamp('XOPQ', o + darkDO),
      YOPQ: clamp('YOPQ', y + sDark * dYHalf),
    },
  ];
}

/**
 * Port of the server's `_grade_diagnostics`: what is wrong with the current
 * grade declaration, worst first. Same {level, code, instance, message,
 * detail} shape the UI renders in GradeDiagnostics.
 */
export function gradeDiagnostics(grade, coords, ranges) {
  const out = [];
  const strength = grade?.intensity ?? 1;
  const capped = grade?.clamp_to_headroom !== false;
  const entries = grade?.instances || [];
  const eff = (p) => Math.max(0, p || 0) * Math.max(0, strength);
  const live = entries.filter((e) => eff(e.pct) > 0);

  if (!grade?.enabled) {
    out.push({ level: 'info', code: 'grade_disabled', instance: null,
      message: 'Grade is switched off, so the GRAD axis is not built.',
      detail: 'Per-instance grades are remembered and return when you switch it back on.' });
  } else if (entries.length === 0) {
    out.push({ level: 'error', code: 'no_graded_instances', instance: null,
      message: 'No instance is graded, so the GRAD axis is not built.',
      detail: 'Grade at least one instance to bring the axis into the font.' });
  } else if (strength <= 0) {
    out.push({ level: 'error', code: 'intensity_zero', instance: null,
      message: 'Grade intensity is 0, so every grade is cancelled and the GRAD axis is not built.',
      detail: `${entries.length} instance(s) are graded. Raise intensity above 0 to bring them back.` });
  } else if (live.length === 0) {
    out.push({ level: 'error', code: 'all_grades_zero', instance: null,
      message: 'Every graded instance sits at 0%, so the GRAD axis is not built.',
      detail: 'Give at least one instance a grade above 0.' });
  } else if (live.length === 1) {
    out.push({ level: 'info', code: 'single_anchor', instance: live[0].name,
      message: `Only “${live[0].name}” is graded, so GRAD fades to nothing away from it.`,
      detail: 'Grade more instances to carry the axis across the space.' });
  }

  if (!ranges || Object.keys(ranges).length === 0) return out;

  for (const entry of entries) {
    const name = entry.name;
    const authored = Number(entry.pct) || 0;
    const pct = eff(authored);
    const base = coords?.[name];
    if (!base) {
      out.push({ level: 'error', code: 'unknown_instance', instance: name,
        message: `“${name}” is graded but no longer exists.`,
        detail: 'It was renamed or deleted. Remove the grade or re-add the instance.' });
      continue;
    }
    if (pct <= 0) continue;

    const cap = maxPctFor(base, ranges);
    const loX = ranges.XTRA ? ranges.XTRA[0] : null;
    const room = loX == null || base.XTRA == null ? null : Math.max(0, base.XTRA - loX);
    const [light, dark] = gradeCoords(base, pct, ranges, capped);
    const o = base.XOPQ ?? 0;
    const want = (pct * o) / 2;
    const gotDark = (dark.XOPQ ?? o) - o;
    const gotLight = o - (light.XOPQ ?? o);

    if (room === 0) {
      if (capped) {
        out.push({ level: 'error', code: 'no_headroom_capped', instance: name,
          message: `“${name}” cannot darken: its counters are already as tight as the design allows.`,
          detail: 'XTRA is at its minimum, so there is no counter room to absorb a thicker stem. '
            + 'The darkening half of the grade is held at 0 here; the lightening half still works. '
            + 'Move the instance off the XTRA floor, or turn off “limit grade to counter headroom”.' });
      } else {
        out.push({ level: 'warning', code: 'no_headroom_uncompensated', instance: name,
          message: `“${name}” darkens straight into its counters.`,
          detail: `XTRA is at its minimum, so none of the ${Math.round(2 * want)} units of counter relief `
            + `this grade needs are available: all ${Math.round(want)} units of added stem fill the counters. `
            + 'Lower this instance’s grade%, or turn on “limit grade to counter headroom”.' });
      }
    } else if (authored > cap + 1e-9) {
      out.push({ level: 'warning', code: 'pct_over_cap', instance: name,
        message: `“${name}” is graded beyond what its parametric space can deliver.`,
        detail: `Grade is ${Math.round(authored * 100)}% but only ${Math.round(cap * 100)}% fits before an `
          + 'axis hits its limit; the excess is discarded, so raising it further changes nothing.' });
    } else if (want > 0 && (gotDark < want - 0.5 || gotLight < want - 0.5)) {
      const worst = Math.min(gotDark, gotLight);
      out.push({ level: 'warning', code: 'partially_clamped', instance: name,
        message: `“${name}” only reaches part of its grade.`,
        detail: `${Math.round(worst)} of ${Math.round(want)} stem units are delivered before an axis hits `
          + 'its limit, so the two directions are no longer symmetric.' });
    }
  }

  const order = { error: 0, warning: 1, info: 2 };
  out.sort((a, b) => (order[a.level] ?? 3) - (order[b.level] ?? 3));
  return out;
}
