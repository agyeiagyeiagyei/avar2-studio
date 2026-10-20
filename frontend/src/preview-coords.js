/**
 * How the Preview canvas maps axis inputs to font-variation-settings.
 *
 * Healthy build: the inputs go straight through — HarfBuzz applies the font's
 * own avar2 table, which is the whole point of the tab.
 *
 * Degraded build (`/api/health` carries `origin_relocation_error`): the font's
 * default could not be moved onto the ORIGIN row, so its avar2 table no longer
 * anchors the resting state and driving the user axes would render the
 * compiled origin instead of the authored style. Then the parametric axes are
 * PINNED at the mapping's post-mapping values (`/api/mapped-location`, the
 * oracle the tab already fetches) and the user axes are held at their
 * defaults, so nothing fights the pins (S1(c)).
 *
 * An axis the designer dragged directly is an explicit override and keeps its
 * slider value: with the user axes at their defaults the avar2 delta is zero,
 * so the override renders exactly — the one mode where a direct parametric
 * input is exact (round-11 review).
 */
export function previewRenderCoords({
  degraded = false,
  effectiveCoords = {},
  userAxes = [],
  parametricAxes = [],
  mappedParams = {},
  overrides = new Set(),
} = {}) {
  if (!degraded) return effectiveCoords;

  const pinned = { ...effectiveCoords };
  for (const axis of userAxes) pinned[axis.tag] = axis.default;
  for (const axis of parametricAxes) {
    const oracle = mappedParams[axis.tag];
    pinned[axis.tag] = overrides.has(axis.tag) || typeof oracle !== 'number'
      ? effectiveCoords[axis.tag]
      : oracle;
  }
  return pinned;
}

/** The `font-variation-settings` string for a coordinate map. */
export function variationSettings(axes = [], coords = {}) {
  return axes
    .map(a => `"${a.tag}" ${coords[a.tag] ?? a.default}`)
    .join(', ');
}
