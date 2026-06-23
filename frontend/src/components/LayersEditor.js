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
 *   onChangeLayers     — async (tag, layers) => void
 *   onOpenInEditor     — (tag, glyphName?) => void
 *   onRequestAddModal  — ({tag, axisDefault, prefillGlyphs?}) => void
 */
function LayersEditor({ tag, axis, layers, allAxes, onChangeLayers, onOpenInEditor, onRequestAddModal }) {
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
    const issues = [];
    if (!hasBelow) issues.push({ kind: 'no-below' });
    else if (!reachesMin) issues.push({ kind: 'extrapolates-below', at: belowVal });
    if (!hasAbove) issues.push({ kind: 'no-above' });
    else if (!reachesMax) issues.push({ kind: 'extrapolates-above', at: aboveVal });
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

  // "Pin layers to axis extremes" — when a glyph has layers that
  // don't reach axis-min / axis-max, move the lowest layer to
  // axis-min and the highest to axis-max. Outlines come along
  // with the location change; intermediate layers untouched.
  const pinExtremesForGlyph = async (glyphName) => {
    if (typeof onChangeLayers !== 'function') return;
    const cov = classifyGlyphCoverage(byGlyph.get(glyphName) || []);
    const next = (layers || []).map(entry => {
      if (entry.glyph !== glyphName) return entry;
      const v = entry.location[tag];
      if (v === undefined) return entry;
      // Lowest layer on the below side → axis.min.
      if (cov.belowVal !== null && v === cov.belowVal && v > axisMin) {
        return { ...entry, location: { ...entry.location, [tag]: axisMin } };
      }
      // Highest layer on the above side → axis.max.
      if (cov.aboveVal !== null && v === cov.aboveVal && v < axisMax) {
        return { ...entry, location: { ...entry.location, [tag]: axisMax } };
      }
      return entry;
    });
    await onChangeLayers(tag, next);
  };

  const toggleCollapsed = (glyph) => {
    setExpandedSet(prev => {
      const next = new Set(prev);
      if (next.has(glyph)) next.delete(glyph);
      else next.add(glyph);
      return next;
    });
  };

  const removeLayer = async (entry) => {
    const next = (layers || []).filter(e => !sameEntry(e, entry));
    await onChangeLayers(tag, next);
  };

  const replaceLayer = async (oldEntry, newEntry) => {
    const next = (layers || []).map(e => sameEntry(e, oldEntry) ? newEntry : e);
    await onChangeLayers(tag, next);
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

      {orderedGlyphs.length === 0 && (
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
        // The "pin to extremes" affordance only helps when there's a
        // layer to push — i.e. one or both sides authored but not at
        // the extreme. A missing side entirely can't be pinned (no
        // layer to move); designer adds a new one instead.
        const canPinExtremes = (
          (coverage.belowVal !== null && coverage.belowVal > axisMin) ||
          (coverage.aboveVal !== null && coverage.aboveVal < axisMax)
        );
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
                {needsAttention && (
                  <div className="layers-glyph-diagnostic">
                    <div className="diagnostic-text">{describeIssues(coverage)}</div>
                    {canPinExtremes && (
                      <button
                        type="button"
                        className="diagnostic-pin"
                        onClick={() => pinExtremesForGlyph(glyphName)}
                        title={`Push the lowest layer to ${tag}=${axisMin}${coverage.aboveVal !== null && coverage.aboveVal < axisMax ? ` and the highest to ${tag}=${axisMax}` : ''}. Outline data carries over; only the location changes.`}
                      >
                        Pin layers to axis extremes
                      </button>
                    )}
                  </div>
                )}
                <ul className="layers-glyph-list">
                  {glyphLayers.map((entry, i) => {
                    const fullLoc = buildFullLocation(entry.location);
                    return (
                      <li
                        key={i}
                        className="layer-row"
                        onClick={() => onRequestAddModal && onRequestAddModal({
                          tag,
                          axisDefault: axis.default,
                          editLayer: entry,
                          replaceLayer: (newEntry) => replaceLayer(entry, newEntry),
                        })}
                        title="Click to edit this brace layer's axis values."
                      >
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
                            <span className="layer-coords-studio-badge" title="Brace layer authored through avar2-studio. Lives in the sidecar; written to the shadow .glyphs on save.">studio</span>
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
                            title="Open this glyph in Fontra at this brace-layer location, in edit mode."
                            onClick={() => onOpenInEditor && onOpenInEditor(tag, glyphName, entry.location)}
                          >
                            ↗
                          </button>
                          <button
                            type="button"
                            className="layer-remove"
                            title="Remove this brace layer. If it's the last one for this glyph, the glyph drops out of coverage."
                            onClick={() => removeLayer(entry)}
                          >
                            ✕
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
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
              </>
            )}
          </div>
        );
      })}

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
    </div>
  );
}

function sameEntry(a, b) {
  if (!a || !b || a.glyph !== b.glyph) return false;
  const la = a.location || {};
  const lb = b.location || {};
  const ka = Object.keys(la);
  if (ka.length !== Object.keys(lb).length) return false;
  for (const k of ka) {
    if (Number(la[k]) !== Number(lb[k])) return false;
  }
  return true;
}

export default LayersEditor;
