import React, { useState } from 'react';
import './ControlAxes.css';

/**
 * CONTROL AXES panel — v1 read-only.
 *
 * Renders the axes the backend's /api/glyph-coverage endpoint
 * classified as ``scoped`` or ``partial`` (glyph-scoped variation,
 * not universal-via-masters). Each row is expandable to show the
 * coverage glyph list and per-axis preview-disable toggle.
 *
 * Hidden entirely when there are no scoped/partial axes — most
 * fonts (pure-parametric Crispy etc.) shouldn't see this section
 * at all.
 *
 * Props:
 *   axes            — full array from /api/glyph-coverage
 *   disabledAxes    — Set<string> of tags currently disabled (pinned
 *                     to axis default at preview render time)
 *   onToggleDisable — (tag) => void; flips the disabled state for an
 *                     axis. State + persistence lives in App.js.
 */
function ControlAxes({ axes, disabledAxes, onToggleDisable }) {
  const [expandedTag, setExpandedTag] = useState(null);
  // Section folds closed by default — Roboto Delta has 9 control axes
  // and that's a lot of vertical space if always-open. The header
  // shows a count so the user knows how many are hiding behind the
  // toggle.
  const [sectionOpen, setSectionOpen] = useState(false);

  // Filter to scoped + partial. Universal axes are master-driven
  // and belong under AVAR2 MAPPINGS / parametric, not here.
  const controlLikeAxes = (axes || []).filter(
    ax => ax.kind === 'scoped' || ax.kind === 'partial'
  );

  if (controlLikeAxes.length === 0) {
    return null;
  }

  const disabledCount = controlLikeAxes.reduce(
    (n, ax) => n + (disabledAxes.has(ax.tag) ? 1 : 0),
    0
  );

  return (
    <div className="control-axes">
      <button
        type="button"
        className="control-axes-header"
        onClick={() => setSectionOpen(o => !o)}
        aria-expanded={sectionOpen}
      >
        <span className="control-axes-section-caret">{sectionOpen ? '▾' : '▸'}</span>
        <h3 className="control-axes-title">CONTROL AXES</h3>
        <span className="control-axes-count">{controlLikeAxes.length}</span>
        {disabledCount > 0 && (
          <span className="control-axes-disabled-count" title={`${disabledCount} disabled in preview`}>
            {disabledCount} off
          </span>
        )}
        <span className="control-axes-subtitle">
          glyph-scoped — read-only
        </span>
      </button>
      {sectionOpen && (
      <div className="control-axes-list">
        {controlLikeAxes.map(ax => {
          const isExpanded = expandedTag === ax.tag;
          const isDisabled = disabledAxes.has(ax.tag);
          const kindBadge = ax.kind === 'partial'
            ? <span className="kind-badge kind-partial" title="Most glyphs but not all — usually means the designer forgot to author a few glyphs at the alternate master.">partial</span>
            : <span className="kind-badge kind-scoped" title="Glyph-scoped variation. Only the listed glyphs change as this axis moves.">scoped</span>;

          return (
            <div key={ax.tag} className={`control-axis-row ${isDisabled ? 'disabled' : ''}`}>
              <div
                className="control-axis-header"
                onClick={() => setExpandedTag(isExpanded ? null : ax.tag)}
              >
                <span className="control-axis-caret">{isExpanded ? '▾' : '▸'}</span>
                <span className="control-axis-tag">{ax.tag}</span>
                <span className="control-axis-name">{ax.name}</span>
                {kindBadge}
                <span className="control-axis-count">
                  {ax.covers_count}/{ax.total_glyphs}
                </span>
                <button
                  className={`control-axis-disable ${isDisabled ? 'on' : ''}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleDisable(ax.tag);
                  }}
                  title={
                    isDisabled
                      ? `Re-enable ${ax.tag} in preview. Slider value is honoured again.`
                      : `Disable ${ax.tag} in preview. The slider is pinned to the axis default while rendering — useful for comparing "with vs without" this control axis.`
                  }
                >
                  {isDisabled ? '👁‍🗨' : '👁'}
                </button>
              </div>
              {isExpanded && (
                <div className="control-axis-body">
                  <div className="control-axis-glyphs">
                    {ax.covers.length > 0 ? (
                      ax.covers.map(g => (
                        <span key={g} className="control-axis-glyph">{g}</span>
                      ))
                    ) : (
                      <span className="control-axis-glyphs-empty">
                        Declared in the source but no glyphs vary along it yet.
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      )}
    </div>
  );
}

export default ControlAxes;
