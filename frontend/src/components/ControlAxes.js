import React, { useState } from 'react';
import './ControlAxes.css';
import CoverageEditor from './CoverageEditor';
import AddBraceLocationModal from './AddBraceLocationModal';

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
function ControlAxes({ axes, disabledAxes, onToggleDisable, onAddClick, onDeleteAxis, onSetCoverage, onOpenInEditor, onSetExtraLocations, allAxes }) {
  const [expandedTag, setExpandedTag] = useState(null);
  // {tag, axisDefault, coverageGlyphs, prefillGlyph} when the
  // add-location modal is open; null otherwise.
  const [addLocationFor, setAddLocationFor] = useState(null);

  const handleAddExtraLocation = async (ax, entry) => {
    if (typeof onSetExtraLocations !== 'function') return;
    const merged = [...(ax.extra_locations || []), entry];
    await onSetExtraLocations(ax.tag, merged);
  };

  const handleRemoveExtraLocation = async (ax, location) => {
    if (typeof onSetExtraLocations !== 'function') return;
    // Match by structural equality (glyph + location dict) so the
    // remove survives a refetch reordering entries.
    const next = (ax.extra_locations || []).filter(e => !sameEntry(e, location));
    await onSetExtraLocations(ax.tag, next);
  };

  // Render an N-D location dict as ``{axis=value, …}``. Used both
  // for display and to identify entries.
  const formatLocation = (loc) => Object.entries(loc || {})
    .map(([t, v]) => `${t}=${v}`)
    .join(', ');
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

  // Section is rendered whenever there are control-like axes to show
  // OR the user has the ability to declare new ones (v2). Otherwise
  // collapse to null so pure-parametric fonts with no +Add affordance
  // (none right now — v2 always allows declaration) don't show an
  // empty section.
  const canDeclare = typeof onAddClick === 'function';
  if (controlLikeAxes.length === 0 && !canDeclare) {
    return null;
  }

  const disabledCount = controlLikeAxes.reduce(
    (n, ax) => n + (disabledAxes.has(ax.tag) ? 1 : 0),
    0
  );

  return (
    <div className="control-axes">
      <div className="control-axes-header-row">
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
            glyph-scoped
          </span>
        </button>
        {canDeclare && (
          <button
            type="button"
            className="control-axes-add-btn"
            onClick={(e) => {
              e.stopPropagation();
              onAddClick();
              // Open the section so the new axis is visible after creation.
              setSectionOpen(true);
            }}
            title="Declare a new control axis (designer-named axis with min/max). Coverage glyphs and brace-layer authoring arrive in later v2 slices."
          >
            + Add
          </button>
        )}
      </div>
      {sectionOpen && (
      <div className="control-axes-list">
        {controlLikeAxes.map(ax => {
          const isExpanded = expandedTag === ax.tag;
          const isDisabled = disabledAxes.has(ax.tag);
          const isStudio = ax.source === 'studio';
          const kindBadge = ax.kind === 'partial'
            ? <span className="kind-badge kind-partial" title="Most glyphs but not all — usually means the designer forgot to author a few glyphs at the alternate master.">partial</span>
            : <span className="kind-badge kind-scoped" title={isStudio ? 'Designer-declared control axis. Coverage glyphs land in a later v2 slice.' : 'Glyph-scoped variation. Only the listed glyphs change as this axis moves.'}>scoped</span>;
          const originBadge = isStudio
            ? <span className="kind-badge kind-studio" title="Declared in the studio (lives in <basename>-control.json). Not yet in the source file.">studio</span>
            : null;

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
                {originBadge}
                {isStudio && ax.min !== undefined && ax.max !== undefined && (
                  <span
                    className="control-axis-range"
                    title={`Range ${ax.min} to ${ax.max}, default ${ax.default}`}
                  >
                    {ax.min}…{ax.max}
                  </span>
                )}
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
                {isStudio && typeof onDeleteAxis === 'function' && (
                  <button
                    className="control-axis-delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (window.confirm(`Delete control axis "${ax.tag}"? The sidecar entry is removed; the source file is untouched.`)) {
                        onDeleteAxis(ax.tag);
                      }
                    }}
                    title="Delete this studio-declared control axis from the sidecar."
                  >
                    🗑
                  </button>
                )}
              </div>
              {isExpanded && (
                <div className="control-axis-body">
                  {isStudio && (
                    <div className="control-axis-meta">
                      <span><strong>Range:</strong> {ax.min} … {ax.max}</span>
                      <span><strong>Default:</strong> {ax.default}</span>
                    </div>
                  )}
                  {isStudio && typeof onSetCoverage === 'function' ? (
                    <>
                      <CoverageEditor
                        tag={ax.tag}
                        coverage={ax.covers}
                        onSave={onSetCoverage}
                        onOpenInEditor={onOpenInEditor}
                      />
                      {typeof onSetExtraLocations === 'function' && (ax.covers || []).length > 0 && (
                        <div className="brace-layers">
                          <div className="brace-layers-header">
                            Brace layers per glyph
                            <span className="brace-layers-hint">
                              auto seeds at {ax.min} / {ax.max} for every covered glyph · custom locations editable below
                            </span>
                          </div>
                          {(ax.covers || []).map(glyphName => {
                            const customForGlyph = (ax.extra_locations || []).filter(e => e.glyph === glyphName);
                            return (
                              <div key={glyphName} className="brace-layers-glyph">
                                <div className="brace-layers-glyph-name">{glyphName}</div>
                                <ul className="brace-layers-list">
                                  {/* Auto seeds at axis-min and axis-max. Read-only — every
                                      coverage glyph gets these from regenerate_shadow. */}
                                  <li
                                    className="brace-layer-row brace-layer-auto"
                                    onClick={() => onOpenInEditor && onOpenInEditor(ax.tag, glyphName)}
                                    title="Auto-seeded brace layer at the axis minimum. Click to open in Fontra."
                                  >
                                    <span className="brace-layer-coords">
                                      {ax.tag} = {ax.min}
                                    </span>
                                    <span className="brace-layer-tag">auto</span>
                                  </li>
                                  <li
                                    className="brace-layer-row brace-layer-auto"
                                    onClick={() => onOpenInEditor && onOpenInEditor(ax.tag, glyphName)}
                                    title="Auto-seeded brace layer at the axis maximum. Click to open in Fontra."
                                  >
                                    <span className="brace-layer-coords">
                                      {ax.tag} = {ax.max}
                                    </span>
                                    <span className="brace-layer-tag">auto</span>
                                  </li>
                                  {customForGlyph.map((entry, i) => (
                                    <li
                                      key={`custom-${i}`}
                                      className="brace-layer-row brace-layer-custom"
                                      onClick={() => onOpenInEditor && onOpenInEditor(ax.tag, glyphName)}
                                      title="Click to open in Fontra."
                                    >
                                      <span className="brace-layer-coords">
                                        {formatLocation(entry.location)}
                                      </span>
                                      <button
                                        type="button"
                                        className="brace-layer-remove"
                                        title="Remove this brace-layer location."
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          handleRemoveExtraLocation(ax, entry);
                                        }}
                                      >
                                        ✕
                                      </button>
                                    </li>
                                  ))}
                                  <li className="brace-layer-add-row">
                                    <button
                                      type="button"
                                      className="brace-layer-add"
                                      onClick={() => setAddLocationFor({
                                        tag: ax.tag,
                                        axisDefault: ax.default,
                                        coverage: [glyphName],  // restrict the picker to this glyph
                                        prefillGlyph: glyphName,
                                      })}
                                    >
                                      + Add mapping
                                    </button>
                                  </li>
                                </ul>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </>
                  ) : (
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
                  )}
                  {isStudio && typeof onDeleteAxis === 'function' && (
                    <button
                      className="control-axis-delete-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (window.confirm(`Delete control axis "${ax.tag}"? The sidecar entry is removed; the source file is untouched.`)) {
                          onDeleteAxis(ax.tag);
                        }
                      }}
                    >
                      Delete control axis
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      )}

      {addLocationFor && (
        <AddBraceLocationModal
          isOpen={!!addLocationFor}
          onClose={() => setAddLocationFor(null)}
          axisTag={addLocationFor.tag}
          axisDefault={addLocationFor.axisDefault}
          coverageGlyphs={addLocationFor.coverage}
          prefillGlyph={addLocationFor.prefillGlyph}
          allAxes={allAxes || []}
          onCreate={async (entry) => {
            const ax = controlLikeAxes.find(a => a.tag === addLocationFor.tag);
            if (ax) await handleAddExtraLocation(ax, entry);
          }}
        />
      )}
    </div>
  );
}

/**
 * Strict equality of {glyph, location} brace-layer entries.
 */
function sameEntry(a, b) {
  if (!a || !b) return false;
  if (a.glyph !== b.glyph) return false;
  const la = a.location || {};
  const lb = b.location || {};
  const ka = Object.keys(la);
  const kb = Object.keys(lb);
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    if (Number(la[k]) !== Number(lb[k])) return false;
  }
  return true;
}

export default ControlAxes;
