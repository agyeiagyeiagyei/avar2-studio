/**
 * Sync-status comparison for instance rows — the pure part of
 * `useInstanceEditing`'s `getInstanceSyncStatus`.
 *
 * A row's dot turns orange when the avar2 CSV says something different from
 * the source. The CSV only stores the mapping's parametric outputs (XTRA /
 * XOPQ / YOPQ on Crispy), while `source_coordinates` carries every axis —
 * control (LCWD, YMOD) and transform (GRAD, slnt, ROND) axes included at
 * their defaults. Comparing the two dictionaries whole always failed on the
 * key count, so a source row that matched its CSV read orange forever and
 * never went green after a source push (A6). Compare only the axes the CSV
 * actually carries.
 *
 * Kept here (rather than inline in the hook) so `e2e/instance-sync.spec.mjs`
 * can test it under `npm run test:unit`.
 */

/** Coordinate-dict equality: numbers within 0.01, anything else literally. */
export const sameCoords = (a, b) => {
  if (!a || !b) return false;
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  for (const k of ka) {
    const va = Number(a[k]);
    const vb = Number(b[k]);
    if (!Number.isFinite(va) || !Number.isFinite(vb)) {
      if (a[k] !== b[k]) return false;
    } else if (Math.abs(va - vb) > 0.01) {
      return false;
    }
  }
  return true;
};

/** True when the CSV's output values disagree with the source's coords. */
export const csvDivergesFromSource = (csvCoords, sourceCoords) => {
  if (!csvCoords || !sourceCoords) return false;
  const subset = {};
  for (const k of Object.keys(csvCoords)) {
    if (k in sourceCoords) subset[k] = sourceCoords[k];
  }
  // Nothing comparable (the CSV pins axes this source does not report):
  // say "in sync" rather than flashing a permanent orange.
  if (Object.keys(subset).length === 0) return false;
  return !sameCoords(csvCoords, subset);
};

/**
 * Look a draft axis up in the persisted union, IGNORING KEY CASE.
 *
 * The mappings CSV lowercases every column it does not recognise
 * (`csv_io.normalize_in_axis_name` → `spac`), while the app, the sliders and
 * `/api/instances` use the fvar tag (`SPAC`). An exact-case `k in persisted`
 * therefore called a persisted SPAC "a value with no persisted home" and the
 * dot stayed red for good whenever the SPAC transform was on (findings.md,
 * F3). Returns `{ found, value }`.
 */
export const persistedValue = (persisted, key) => {
  if (!persisted || key === undefined || key === null) return { found: false };
  if (key in persisted) return { found: true, value: persisted[key] };
  const want = String(key).toUpperCase();
  for (const k of Object.keys(persisted)) {
    if (String(k).toUpperCase() === want) return { found: true, value: persisted[k] };
  }
  return { found: false };
};

/**
 * True when the row's local draft disagrees with anything persisted.
 * `previewOnlyTags` never persist, so they never make a row dirty.
 */
export const editsAreDirty = (edits, persisted, previewOnlyTags = new Set()) => {
  for (const k of Object.keys(edits || {})) {
    if (previewOnlyTags.has(String(k).toLowerCase())) continue;
    const home = persistedValue(persisted, k);
    if (!home.found) return true;            // value with no persisted home
    const ve = Number(edits[k]);
    const vp = Number(home.value);
    if (!Number.isFinite(ve) || !Number.isFinite(vp)) {
      if (edits[k] !== home.value) return true;
    } else if (Math.abs(ve - vp) > 0.01) {
      return true;
    }
  }
  return false;
};
