import React, { useState } from 'react';

/**
 * Unified brace-layer editor for a control axis. Replaces the old
 * "Coverage glyphs" textarea + per-glyph extra-locations list — every
 * brace layer is explicit, coverage is derived from unique glyph
 * names.
 *
 * Per-glyph blocks are collapsible (caret toggle). Each row is
 * click-to-edit-in-Fontra. ✕ removes a single brace layer; remove
 * the last one and the glyph drops out of coverage.
 *
 * Props:
 *   tag                — axis tag
 *   axis               — full axis dict (range, default)
 *   layers             — Array<{glyph, location}> from sidecar
 *   allAxes            — full axes list from /api/axes (for the modal)
 *   onLayerDelta       — async (tag, {add, remove}) => void; sends only
 *                        what changed so concurrent edits can't clobber
 *   onOpenInEditor     — (tag, glyphName?) => void
 *   onRequestAddModal  — ({tag, axisDefault, prefillGlyphs?}) => void
 *   readOnly           — source-derived axes: the layers live in the
 *                        source file itself (brace layers / alternate
 *                        masters), so add/remove/edit affordances are
 *                        hidden. Thumbnails, coverage warnings, and
 *                        the open-in-Fontra flyout stay.
 */
function LayersEditor({ tag, axis, layers, allAxes, onLayerDelta, onOpenInEditor, onRequestAddModal, vfFamilyId, fontLoaded, readOnly = false, glyphChars = {} }) {
  // Per-glyph block expansion. Tracks the set of EXPLICITLY
  // EXPANDED glyphs — anything else is collapsed (showing just
  // the glyph name + layer count). Designer clicks the caret to
  // drill into a specific glyph's layers; the axis-row body stays
  // readable at a glance regardless of how many glyphs have braces.
  const [expandedSet, setExpandedSet] = useState(() => new Set());

  // Group layers by glyph, preserving first-seen order so the
  // designer's mental ordering carries through.
  const byGlyph = new Map();
  for (const entry of (layers || [])) {
    if (!byGlyph.has(entry.glyph)) byGlyph.set(entry.glyph, []);
    byGlyph.get(entry.glyph).push(entry);
  }
  const orderedGlyphs = Array.from(byGlyph.keys());

  // Per-glyph coverage diagnostics on the CONTROL axis. We want to
  // warn the designer when the rendered slider will extrapolate
  // beyond the authored range:
  //   1. No layers below the axis default → slider toward min does
  //      nothing for this glyph.
  //   2. No layers above the axis default → mirror.
  //   3. Lowest authored layer doesn't reach axis-min — slider
  //      travel between the lowest layer and axis-min linearly
  //      extrapolates the delta (usually broken).
  //   4. Highest authored layer doesn't reach axis-max — mirror.
  const axisMin = axis ? axis.min : 0;
  const axisMax = axis ? axis.max : 0;
  const axisDefault = axis ? axis.default : 0;

  const classifyGlyphCoverage = (entries) => {
    let belowVal = null;     // most-negative axis value among entries
    let aboveVal = null;     // most-positive axis value among entries
    for (const e of entries) {
      const v = e.location ? e.location[tag] : undefined;
      if (v === undefined) continue;
      if (v < axisDefault && (belowVal === null || v < belowVal)) belowVal = v;
      if (v > axisDefault && (aboveVal === null || v > aboveVal)) aboveVal = v;
    }
    const hasBelow = belowVal !== null;
    const hasAbove = aboveVal !== null;
    const reachesMin = hasBelow && belowVal <= axisMin;
    const reachesMax = hasAbove && aboveVal >= axisMax;
    // Only the sides of the default that actually have axis travel can
    // have a coverage problem. When the default sits ON an extreme
    // (e.g. a 0…40 axis with default 0), there's no "below default"
    // region — the master already defines that endpoint — so a missing
    // below-layer is not a defect. Guarding on these prevents the
    // nonsensical "no layer below default" warning when there's no room
    // below it in the first place.
    const hasBelowRoom = axisMin < axisDefault;
    const hasAboveRoom = axisMax > axisDefault;
    const issues = [];
    if (hasBelowRoom) {
      if (!hasBelow) issues.push({ kind: 'no-below' });
      else if (!reachesMin) issues.push({ kind: 'extrapolates-below', at: belowVal });
    }
    if (hasAboveRoom) {
      if (!hasAbove) issues.push({ kind: 'no-above' });
      else if (!reachesMax) issues.push({ kind: 'extrapolates-above', at: aboveVal });
    }
    return {
      ok: issues.length === 0,
      issues,
      belowVal,
      aboveVal,
      reachesMin,
      reachesMax,
    };
  };

  const describeIssues = (cov) => {
    if (cov.ok) return '';
    return cov.issues.map(i => {
      if (i.kind === 'no-below') return `No layer below default — slider toward ${axisMin} won't deform this glyph.`;
      if (i.kind === 'no-above') return `No layer above default — slider toward ${axisMax} won't deform this glyph.`;
      if (i.kind === 'extrapolates-below') return `Lowest layer is at ${tag}=${i.at}; the axis goes to ${axisMin}. Slider between ${i.at} and ${axisMin} will extrapolate from your authored outline (usually overshoot / broken).`;
      if (i.kind === 'extrapolates-above') return `Highest layer is at ${tag}=${i.at}; the axis goes to ${axisMax}. Slider between ${i.at} and ${axisMax} will extrapolate.`;
      return '';
    }).filter(Boolean).join(' ');
  };

  const toggleCollapsed = (glyph) => {
    setExpandedSet(prev => {
      const next = new Set(prev);
      if (next.has(glyph)) next.delete(glyph);
      else next.add(glyph);
      return next;
    });
  };

  // Both send a DELTA, never the whole list: rebuilding the list from our
  // cached `layers` would silently wipe anything added since the last refetch.
  const removeLayer = async (entry) => {
    await onLayerDelta(tag, { remove: [entry] });
  };

  const replaceLayer = async (oldEntry, newEntry) => {
    await onLayerDelta(tag, { remove: [oldEntry], add: [newEntry] });
  };

  // Build the full N-D location for a brace: sparse pins from
  // the sidecar overlaid on each declared axis's default. This
  // matches what Fontra computes when rendering the source list
  // (master + extraLocation), so the studio's row label and
  // Fontra's source label are 1:1.
  const axisByTag = new Map((allAxes || []).map(a => [a.tag, a]));
  const buildFullLocation = (sparseLoc) => {
    const out = [];
    for (const ax of (allAxes || [])) {
      const v = (sparseLoc && sparseLoc[ax.tag] !== undefined)
        ? Number(sparseLoc[ax.tag])
        : Number(ax.default);
      out.push({ tag: ax.tag, name: ax.name || ax.tag, value: v, isControl: ax.tag === tag });
    }
    return out;
  };

  // Pretty-print the control-axis value as a quick header label
  // (the "primary" handle the designer pinned this brace to).
  const formatControl = (sparseLoc) => {
    const v = sparseLoc?.[tag];
    if (v === undefined) return `${tag} unset`;
    return `${tag} = ${v}`;
  };

  return (
    <div className="layers-editor">
      <div className="layers-editor-header">
        <span className="layers-editor-label">
          Applicable glyphs
          <span className="layers-editor-hint">
            glyphs this axis changes · click one to see its layers
          </span>
        </span>
      </div>

      {orderedGlyphs.length === 0 && !readOnly && (
        <div className="layers-editor-empty">
          No glyphs yet. <strong>+ Add applicable glyphs</strong> below to
          declare which glyphs this axis changes, and at what axis extreme.
        </div>
      )}

      {orderedGlyphs.map(glyphName => {
        const glyphLayers = byGlyph.get(glyphName) || [];
        const isCollapsed = !expandedSet.has(glyphName);
        const coverage = classifyGlyphCoverage(glyphLayers);
        const needsAttention = !coverage.ok;
        return (
          <div key={glyphName} className={`layers-glyph-block ${needsAttention ? 'needs-more' : ''}`}>
            <div
              className="layers-glyph-header"
              onClick={() => toggleCollapsed(glyphName)}
            >
              <span className="layers-glyph-caret">{isCollapsed ? '▸' : '▾'}</span>
              <span className="layers-glyph-name">{glyphName}</span>
              <span className="layers-glyph-count">
                {glyphLayers.length} layer{glyphLayers.length === 1 ? '' : 's'}
              </span>
              {needsAttention && (
                <span
                  className="layers-glyph-warning"
                  title={describeIssues(coverage)}
                >
                  ⚠ extrapolates
                </span>
              )}
            </div>
            {!isCollapsed && (
              <>
                <ul className="layers-glyph-list">
                  {glyphLayers.map((entry, i) => {
                    // Display coords stay in DESIGN space (what the
                    // source authors). Anything that drives the
                    // compiled font or Fontra — the thumbnail's
                    // fontVariationSettings, the flyout's location —
                    // needs USER space: the backend inverts designspace
                    // axis maps into `location_user` (== location for
                    // identity maps; sidecar entries omit the field).
                    // An explicit null means the map couldn't be
                    // inverted — render/navigate WITHOUT a location
                    // rather than at a wrong one.
                    const fullLoc = buildFullLocation(entry.location);
                    const navLocation = entry.location_user === null
                      ? null
                      : (entry.location_user || entry.location);
                    const thumbLoc = navLocation ? buildFullLocation(navLocation) : null;
                    // Thumbnails typeset TEXT in the built font, so a
                    // multi-char glyph name ("eight") must be mapped to
                    // its actual character via the source's unicode
                    // data. No codepoint → no thumbnail, rather than
                    // rendering the name as a word.
                    const thumbChar = glyphChars[glyphName]
                      || (glyphName.length === 1 ? glyphName : null);
                    return (
                      <li
                        key={i}
                        className={`layer-row${readOnly ? ' layer-row-readonly' : ''}`}
                        onClick={readOnly ? undefined : () => onRequestAddModal && onRequestAddModal({
                          tag,
                          axisDefault: axis.default,
                          editLayer: entry,
                          replaceLayer: (newEntry) => replaceLayer(entry, newEntry),
                        })}
                        title={readOnly
                          ? "Source-derived layer — authored in your source file, not the studio sidecar."
                          : "Click to edit this brace layer's axis values."}
                      >
                        {fontLoaded && vfFamilyId && thumbLoc && thumbChar && (
                          <span
                            className="layer-thumb"
                            style={{
                              fontFamily: `"${vfFamilyId}", sans-serif`,
                              fontVariationSettings: thumbLoc.map(a => `"${a.tag}" ${a.value}`).join(', '),
                            }}
                          >
                            {thumbChar}
                          </span>
                        )}
                        <div className="layer-coords">
                          <div className="layer-coords-control">
                            <span
                              className="layer-coords-control-tag"
                              title={(axisByTag.get(tag)?.name) || tag}
                            >
                              {tag}
                            </span>
                            <span className="layer-coords-control-eq">=</span>
                            <span className="layer-coords-control-val">{entry.location?.[tag] ?? ''}</span>
                            {readOnly ? (
                              <span className="layer-coords-source-badge" title="Layer read from your source file — a brace layer (.glyphs) or an alternate master (.designspace).">source</span>
                            ) : (
                              <span className="layer-coords-studio-badge" title="Brace layer authored through avar2-studio. Lives in the sidecar; written to the shadow .glyphs on save.">studio</span>
                            )}
                          </div>
                          <div className="layer-coords-context">
                            <span className="layer-coords-context-prefix">at</span>
                            {fullLoc.filter(a => !a.isControl).map(a => (
                              <span key={a.tag} className="layer-coords-axis-pair">
                                <span
                                  className="layer-coords-axis-tag"
                                  title={a.name}
                                >
                                  {a.tag}
                                </span>
                                <span className="layer-coords-axis-val">{a.value}</span>
                              </span>
                            ))}
                          </div>
                        </div>
                        <div className="layer-actions" onClick={e => e.stopPropagation()}>
                          <button
                            type="button"
                            className="layer-open-fontra"
                            title={readOnly
                              ? "Open this glyph in Fontra at this layer's location. This axis lives in your source file, so Fontra edits the real source."
                              : "Open this glyph in Fontra at this brace-layer location, in edit mode."}
                            onClick={() => onOpenInEditor && onOpenInEditor(tag, glyphName, navLocation)}
                          >
                            ↗
                          </button>
                          {!readOnly && (
                            <>
                              <button
                                type="button"
                                className="layer-duplicate"
                                title="Duplicate this layer — opens at the same coordinates so you only change what differs. Edit the glyph field to reuse this location on other glyphs."
                                onClick={() => onRequestAddModal && onRequestAddModal({
                                  tag,
                                  axisDefault: axis.default,
                                  duplicateFrom: entry,
                                })}
                              >
                                ⧉
                              </button>
                              <button
                                type="button"
                                className="layer-remove"
                                title="Remove this brace layer. If it's the last one for this glyph, the glyph drops out of coverage."
                                onClick={() => removeLayer(entry)}
                              >
                                ✕
                              </button>
                            </>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
                {!readOnly && (
                  <div className="layers-glyph-add-row">
                    <button
                      type="button"
                      className="layer-add-for-glyph"
                      onClick={() => onRequestAddModal && onRequestAddModal({
                        tag,
                        axisDefault: axis.default,
                        prefillGlyphs: glyphName,
                        lockGlyphs: true,
                      })}
                    >
                      + Add layer for {glyphName}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        );
      })}

      {!readOnly && (
        <div className="layers-editor-toplevel-add">
          <button
            type="button"
            className="layer-add-toplevel"
            onClick={() => onRequestAddModal && onRequestAddModal({
              tag,
              axisDefault: axis.default,
            })}
          >
            + Add applicable glyphs
          </button>
        </div>
      )}
    </div>
  );
}

export default LayersEditor;
