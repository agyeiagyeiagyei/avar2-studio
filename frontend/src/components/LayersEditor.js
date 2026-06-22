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
function LayersEditor({ tag, axis, layers, onChangeLayers, onOpenInEditor, onRequestAddModal }) {
  const [collapsed, setCollapsed] = useState({});

  // Group layers by glyph, preserving first-seen order so the
  // designer's mental ordering carries through.
  const byGlyph = new Map();
  for (const entry of (layers || [])) {
    if (!byGlyph.has(entry.glyph)) byGlyph.set(entry.glyph, []);
    byGlyph.get(entry.glyph).push(entry);
  }
  const orderedGlyphs = Array.from(byGlyph.keys());

  // For an axis to truly deform a glyph in both directions from
  // its default, the glyph needs brace layers on BOTH sides of the
  // axis default — one below, one above. With only one side,
  // moving the slider in the other direction extrapolates from
  // the single delta, which is usually nonsense.
  //
  // We check for the CONTROL AXIS only (the one this LayersEditor
  // is bound to). Custom multi-axis pins are extra; coverage of
  // both directions on the control axis is what makes the axis
  // "active" for that glyph.
  const axisDefault = axis ? axis.default : 0;
  const classifyGlyphCoverage = (entries) => {
    let below = false;
    let above = false;
    for (const e of entries) {
      const v = e.location ? e.location[tag] : undefined;
      if (v === undefined) continue;
      if (v < axisDefault) below = true;
      else if (v > axisDefault) above = true;
    }
    if (below && above) return { kind: 'ok' };
    if (below) return { kind: 'one-sided', side: 'below' };
    if (above) return { kind: 'one-sided', side: 'above' };
    return { kind: 'none' };
  };

  const toggleCollapsed = (glyph) => {
    setCollapsed(prev => ({ ...prev, [glyph]: !prev[glyph] }));
  };

  const removeLayer = async (entry) => {
    const next = (layers || []).filter(e => !sameEntry(e, entry));
    await onChangeLayers(tag, next);
  };

  const formatLocation = (loc) => Object.entries(loc || {})
    .map(([t, v]) => `${t} = ${v}`)
    .join(', ');

  return (
    <div className="layers-editor">
      <div className="layers-editor-header">
        <span className="layers-editor-label">
          Brace layers
          <span className="layers-editor-hint">
            every layer is explicit · click a row to open in Fontra
          </span>
        </span>
      </div>

      {orderedGlyphs.length === 0 && (
        <div className="layers-editor-empty">
          No brace layers yet. <strong>+ Add brace layer</strong> below to
          declare a layer at a specific axis location for one or more glyphs.
        </div>
      )}

      {orderedGlyphs.map(glyphName => {
        const glyphLayers = byGlyph.get(glyphName) || [];
        const isCollapsed = !!collapsed[glyphName];
        const coverage = classifyGlyphCoverage(glyphLayers);
        const needsMoreLayers = coverage.kind !== 'ok';
        return (
          <div key={glyphName} className={`layers-glyph-block ${needsMoreLayers ? 'needs-more' : ''}`}>
            <div
              className="layers-glyph-header"
              onClick={() => toggleCollapsed(glyphName)}
            >
              <span className="layers-glyph-caret">{isCollapsed ? '▸' : '▾'}</span>
              <span className="layers-glyph-name">{glyphName}</span>
              <span className="layers-glyph-count">
                {glyphLayers.length} layer{glyphLayers.length === 1 ? '' : 's'}
              </span>
              {needsMoreLayers && (
                <span
                  className="layers-glyph-warning"
                  title={
                    coverage.kind === 'none'
                      ? `No brace layers vary along ${tag} (only multi-axis pins). ${glyphName} won't deform as the slider moves.`
                      : coverage.side === 'below'
                        ? `Only layers below ${tag} default (${axisDefault}). Add a layer above ${axisDefault} so the slider works in both directions.`
                        : `Only layers above ${tag} default (${axisDefault}). Add a layer below ${axisDefault} so the slider works in both directions.`
                  }
                >
                  ⚠ needs another
                </span>
              )}
            </div>
            {!isCollapsed && (
              <>
                <ul className="layers-glyph-list">
                  {glyphLayers.map((entry, i) => (
                    <li
                      key={i}
                      className="layer-row"
                      onClick={() => onOpenInEditor && onOpenInEditor(tag, glyphName)}
                      title="Click to open this glyph in Fontra."
                    >
                      <span className="layer-coords">{formatLocation(entry.location)}</span>
                      <button
                        type="button"
                        className="layer-remove"
                        title="Remove this brace layer. If it's the last one for this glyph, the glyph drops out of coverage."
                        onClick={(e) => {
                          e.stopPropagation();
                          removeLayer(entry);
                        }}
                      >
                        ✕
                      </button>
                    </li>
                  ))}
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
          + Add brace layer
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
