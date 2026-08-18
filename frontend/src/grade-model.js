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
  return Math.min(...caps.filter(c => c > 0), 2.0);
}
