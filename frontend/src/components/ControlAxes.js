import React, { useState } from 'react';
import './ControlAxes.css';
import LayersEditor from './LayersEditor';
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
function ControlAxes({ axes, disabledAxes, onToggleDisable, onAddClick, onDeleteAxis, onOpenInEditor, onSetLayers, allAxes, allInstances }) {
  // Set of axis tags that are currently expanded. Multiple axes can
  // be open at once. Defaults to "all studio-declared axes are
  // expanded" so opening the CONTROL AXES section drops the user
  // straight into the per-glyph layer lists — no extra click to
  // drill into each axis.
  const [expandedTags, setExpandedTags] = useState(null);
  // ``addLocationFor`` carries the props the AddBraceLocationModal
  // needs: which axis, what to pre-fill, whether the glyph field is
  // locked (per-glyph add) or open (top-level bulk add).
  const [addLocationFor, setAddLocationFor] = useState(null);

  // Append a batch of new {glyph, location} entries to an axis's
  // ``layers`` list and round-trip through the API.
  const handleAddLayers = async (ax, newEntries) => {
    if (typeof onSetLayers !== 'function') return;
    const merged = [...(ax.layers || []), ...newEntries];
    await onSetLayers(ax.tag, merged);
  };
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

  // Resolve which tags are expanded right now. Default: all studio
  // axes are expanded (designer just opened the section and wants
  // to see what's there). Source-derived axes stay collapsed by
  // default since their content is read-only.
  const studioTagSet = React.useMemo(
    () => new Set(controlLikeAxes.filter(ax => ax.source === 'studio').map(ax => ax.tag)),
    [controlLikeAxes],
  );
  const resolvedExpandedTags = expandedTags === null ? studioTagSet : expandedTags;

  const toggleAxisExpanded = (tag) => {
    setExpandedTags(prev => {
      const base = prev === null ? new Set(studioTagSet) : new Set(prev);
      if (base.has(tag)) base.delete(tag);
      else base.add(tag);
      return base;
    });
  };

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
          const isExpanded = resolvedExpandedTags.has(ax.tag);
          const isDisabled = disabledAxes.has(ax.tag);
          const isStudio = ax.source === 'studio';
          // Source-derived axes keep scoped/partial — that's the
          // meaningful "does it vary all glyphs or a subset?" signal.
          // Studio axes show no kind badge: by construction they're
          // glyph-scoped, and the "studio" origin was redundant.
          const kindBadge = isStudio
            ? null
            : ax.kind === 'partial'
              ? <span className="kind-badge kind-partial" title="Most glyphs but not all — usually means the designer forgot to author a few glyphs at the alternate master.">partial</span>
              : <span className="kind-badge kind-scoped" title="Glyph-scoped variation. Only the listed glyphs change as this axis moves.">scoped</span>;

          // Count glyphs whose layer set on THIS axis would
          // extrapolate at one or both extremes. Same logic as
          // LayersEditor's classifyGlyphCoverage; rolled up so
          // the warning is visible at the axis-row level even
          // when the section's collapsed or the per-glyph blocks
          // are out of view.
          const extrapolateCount = isStudio
            ? countExtrapolatingGlyphs(ax)
            : 0;

          return (
            <div key={ax.tag} className={`control-axis-row ${isDisabled ? 'disabled' : ''}`}>
              <div
                className="control-axis-header"
                onClick={() => toggleAxisExpanded(ax.tag)}
              >
                <span className="control-axis-tag" title={ax.name}>{ax.tag}</span>
                {kindBadge}
                {isStudio && ax.min !== undefined && ax.max !== undefined && (
                  <span
                    className="control-axis-range"
                    title={`Range ${ax.min} to ${ax.max}, default ${ax.default}`}
                  >
                    {ax.min}…{ax.max}
                  </span>
                )}
                {extrapolateCount > 0 && (
                  <span
                    className="control-axis-extrapolate"
                    title={extrapolateTooltip(ax)}
                  >
                    ⚠ {extrapolateCount} glyph{extrapolateCount === 1 ? '' : 's'} extrapolate{extrapolateCount === 1 ? 's' : ''}
                  </span>
                )}
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
                  {isStudio && typeof onSetLayers === 'function' ? (
                    <LayersEditor
                      tag={ax.tag}
                      axis={ax}
                      layers={ax.layers || []}
                      allAxes={allAxes || []}
                      onChangeLayers={onSetLayers}
                      onOpenInEditor={onOpenInEditor}
                      onRequestAddModal={setAddLocationFor}
                    />
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
          prefillGlyphs={addLocationFor.prefillGlyphs}
          lockGlyphs={addLocationFor.lockGlyphs}
          editLayer={addLocationFor.editLayer}
          allAxes={allAxes || []}
          allInstances={allInstances || []}
          onCreate={async (entries) => {
            // Edit flow: the LayersEditor row supplied a
            // ``replaceLayer`` callback that swaps the single
            // existing entry. Add flow: append to the axis's layers.
            if (addLocationFor.editLayer && typeof addLocationFor.replaceLayer === 'function') {
              if (entries.length) {
                await addLocationFor.replaceLayer(entries[0]);
              }
            } else {
              const ax = controlLikeAxes.find(a => a.tag === addLocationFor.tag);
              if (ax) await handleAddLayers(ax, entries);
            }
          }}
        />
      )}
    </div>
  );
}

/**
 * Roll up per-glyph extrapolation diagnostics into one number for
 * an axis. Mirrors LayersEditor's classifyGlyphCoverage logic.
 * A glyph counts as "extrapolating" if any of:
 *   - no layer below the axis default
 *   - no layer above the axis default
 *   - lowest below-default layer doesn't reach axis.min
 *   - highest above-default layer doesn't reach axis.max
 */
function countExtrapolatingGlyphs(ax) {
  if (!ax || !Array.isArray(ax.layers)) return 0;
  return collectExtrapolating(ax).length;
}

function collectExtrapolating(ax) {
  const tag = ax.tag;
  const byGlyph = new Map();
  for (const entry of ax.layers) {
    if (!entry || !entry.glyph) continue;
    if (!byGlyph.has(entry.glyph)) byGlyph.set(entry.glyph, []);
    byGlyph.get(entry.glyph).push(entry);
  }
  const offenders = [];
  for (const [glyph, entries] of byGlyph) {
    let belowVal = null, aboveVal = null;
    for (const e of entries) {
      const v = e.location?.[tag];
      if (v === undefined) continue;
      if (v < ax.default && (belowVal === null || v < belowVal)) belowVal = v;
      if (v > ax.default && (aboveVal === null || v > aboveVal)) aboveVal = v;
    }
    const hasBelow = belowVal !== null;
    const hasAbove = aboveVal !== null;
    const reachesMin = hasBelow && belowVal <= ax.min;
    const reachesMax = hasAbove && aboveVal >= ax.max;
    if (!hasBelow || !hasAbove || !reachesMin || !reachesMax) {
      offenders.push(glyph);
    }
  }
  return offenders;
}

function extrapolateTooltip(ax) {
  const offenders = collectExtrapolating(ax);
  if (offenders.length === 0) return '';
  const shown = offenders.slice(0, 8).join(', ');
  const more = offenders.length > 8 ? ` (+${offenders.length - 8} more)` : '';
  return `Glyphs whose layers don't cover the full ${ax.tag} axis range, so the slider extrapolates at one or both extremes: ${shown}${more}. Expand the axis to see specifics and pin layers to ${ax.min} / ${ax.max}.`;
}

export default ControlAxes;
