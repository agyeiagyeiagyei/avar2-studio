/**
 * Promotion of a studio-only row into the source file.
 *
 * A slider drag on a studio row is persisted by an 800 ms debounced
 * auto-save that only runs while the row is `origin: 'studio'`
 * (useInstanceEditing). The flyout's "Save to source file (promote)"
 * used to POST /add-to-source straight away: inside that window the
 * promotion flipped the row to `origin: 'source'`, the auto-save effect
 * returned early by design, and the draft was dropped from every save
 * path — the mappings file, the promoted source instance and the screen
 * all disagreed and the row stayed red (findings.md, "Designer Part B
 * finding", F1).
 *
 * This module owns the ordering so it can be tested without React:
 *
 *   1. strip preview-only axes (they never persist),
 *   2. flush the draft with the same csv-only PUT the timer would have
 *      sent, serialized behind any pending auto-save (`pending`),
 *   3. only then promote,
 *   4. hand back the refreshed instance list so the caller can reseed.
 *
 * A failed flush throws before the promote call: promoting values that
 * were never written is exactly the bug this closes.
 */

/** Drop the axes that carry live preview state only (SPAC stays). */
export function previewFreeCoords(coords, previewOnlyTags = new Set()) {
  const kept = {};
  for (const [tag, value] of Object.entries(coords || {})) {
    if (previewOnlyTags.has(String(tag).toLowerCase())) continue;
    kept[tag] = value;
  }
  return kept;
}

/**
 * Flush `coords`, promote `name`, return the refreshed rows.
 *
 * ``api`` needs updateInstance / addInstanceToSource / getInstances.
 * ``pending`` is the auto-save promise chain; the flush queues behind it
 * because each commit rewrites the whole mappings file.
 */
export async function promoteStudioRow(name, {
  api,
  coords,
  previewOnlyTags = new Set(),
  pending = Promise.resolve(),
} = {}) {
  if (!name) throw new Error('promoteStudioRow needs a row name');
  const draft = previewFreeCoords(coords, previewOnlyTags);
  const flushed = Object.keys(draft).length > 0;
  if (flushed) {
    await pending;
    await api.updateInstance(name, draft, { csvOnly: true });
  }
  await api.addInstanceToSource(name);
  const refreshed = await api.getInstances();
  return { draft, flushed, instances: (refreshed && refreshed.instances) || [] };
}
