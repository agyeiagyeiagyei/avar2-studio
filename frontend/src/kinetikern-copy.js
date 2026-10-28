/**
 * Kinetikern tab copy + the "apply to all" contract (B7 findings K2, K3).
 *
 * "Apply to all…" really means all: every style is re-spaced at the slider's
 * value, replacing any looseness it had tuned. The confirm used to claim "each
 * gets its own correction", which is true of the deltas and false of the
 * looseness, and the button said nothing about either. Kept here so the node
 * spec can pin the exact strings.
 */

/** `-0.6` → `−0.60`, `0` → `0.00`, `0.25` → `+0.25` (the slider's own format). */
export function fmtLooseness(value) {
  const n = Number(value);
  const v = Number.isFinite(n) ? n : 0;
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}`;
}

/** The button: it carries the value it will write to every style. */
export function applyAllLabel(looseness) {
  return `Apply ${fmtLooseness(looseness)} to all…`;
}

/** Styles that already carry their own looseness, from the GET payload. */
export function spacedStyles(stored) {
  return (stored?.instances || [])
    .filter(e => e && e.name)
    .map(e => ({ name: e.name, looseness: Number(e.looseness) }));
}

/**
 * The confirm text. With nothing tuned it stays the plain one; with tuned
 * styles it names each of them and the value they will become, so the
 * overwrite is visible before it happens (user decision: "all means all",
 * make it visible rather than skipping the tuned styles).
 */
export function applyAllConfirm({ instances = [], stored = null, looseness = 0 } = {}) {
  const target = fmtLooseness(looseness);
  const tuned = spacedStyles(stored);
  const head = `Space all ${instances.length} instances at ${target}? ` +
    'Each gets its own correction; the font rebuilds once.';
  if (!tuned.length) return head;
  const named = tuned.map(e => `${e.name} (${fmtLooseness(e.looseness)})`).join(', ');
  const verb = tuned.length === 1
    ? 'already has its own looseness and will'
    : 'already have their own looseness and will';
  return `${head} ${named} ${verb} be re-spaced at ${target}.`;
}

/** One dropdown option's text: the marker is a word, not a bare "●". */
export function instanceOptionLabel(name, entry) {
  if (!entry) return name;
  return `${name} · spaced ${fmtLooseness(entry.looseness)}`;
}

/** The Revert button names what it reverts. */
export function revertLabel(name) {
  return name ? `Revert ${name}` : 'Revert';
}

/**
 * K1 — Kinetikern has to be visible outside its own tab, so an Instances row
 * that is spaced carries a badge in the Grade badge's pattern. Absence means
 * "not spaced": spacing is authored in the tab, so a row never offers to add
 * one.
 */
export function badgeLabel(looseness) {
  return `K ${fmtLooseness(looseness)}`;
}

/**
 * K1 — the Transforms menu's Kinetikern row. The count is the number of
 * spaced styles; the copy has to say what "off" does, because the switch
 * looks like it deletes the work and does not (entries are kept).
 */
export function kinetikernMenuDesc(enabled, count = 0) {
  const n = Number(count) || 0;
  const styled = n === 1 ? '1 style spaced' : `${n} styles spaced`;
  return enabled
    ? `Per-style spacing from the Kinetikern tab. ${styled}. Off builds without it and keeps the entries.`
    : `Off — the build uses the source's own spacing. ${styled}; turn it back on to restore them.`;
}

/**
 * K4 — the size a style was drawn for: its `opsz` coordinate, clamped to the
 * Font Size slider's range. `null` when the source has no opsz axis, so the
 * caller leaves the (shared) size alone.
 */
export function fontSizeForStyle(instance, coordOf, min = 9, max = 144) {
  if (!instance || typeof coordOf !== 'function') return null;
  const opsz = Number(coordOf(instance, 'opsz'));
  if (!Number.isFinite(opsz) || opsz <= 0) return null;
  return Math.min(max, Math.max(min, opsz));
}
