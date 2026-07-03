import React, { useState } from 'react';
import './ControlAxes.css';
import LayersEditor from './LayersEditor';
import AddBraceLocationModal from './AddBraceLocationModal';

/**
 * CONTROL AXES panel.
 *
 * Renders the axes the backend's /api/glyph-coverage endpoint
 * classified as ``scoped`` (glyph-scoped variation, not
 * universal-via-masters). Source-derived scoped axes are read-only;
 * studio-declared ones get add/edit/delete + brace-layer authoring.
 * Each row expands to show its applicable glyphs and a per-axis
 * preview-disable toggle.
 *
 * Hidden entirely when there are no scoped axes and no declare
 * affordance — pure-parametric fonts (Crispy etc.) shouldn't see
 * this section at all.
 *
 * Props:
 *   axes            — full array from /api/glyph-coverage
 *   disabledAxes    — Set<string> of tags currently disabled (pinned
 *                     to axis default at preview render time)
 *   onToggleDisable — (tag) => void; flips the disabled state for an
 *                     axis. State + persistence lives in App.js.
 */
function ControlAxes({ axes, disabledAxes, onToggleDisable, onAddClick, addDisabledReason, onEditAxis, onDeleteAxis, onOpenInEditor, onSetLayers, allAxes, allMasters, vfFamilyId, fontLoaded }) {
  // Set of axis tags currently expanded. All axes default collapsed
  // — matches the per-glyph-block treatment one level down. Designer
  // clicks an axis row to drill in.
  const [expandedTags, setExpandedTags] = useState(() => new Set());
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
  // Section opens by default — control axes are the primary editing
  // surface in v1, and per-axis rows are now collapsible themselves,
  // so even with 9 axes (Roboto Delta) the section stays scannable.
  // Designer can still fold the whole section if they want it out
  // of the way.
  const [sectionOpen, setSectionOpen] = useState(true);

  // Filter to scoped. Universal axes are master-driven and belong
  // under AVAR2 MAPPINGS / parametric, not here.
  const controlLikeAxes = (axes || []).filter(ax => ax.kind === 'scoped');

  const resolvedExpandedTags = expandedTags;

  const toggleAxisExpanded = (tag) => {
    setExpandedTags(prev => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
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
          {disabledCount > 0 && (
            <span className="control-axes-disabled-count" title={`${disabledCount} disabled in preview`}>
              {disabledCount} off
            </span>
          )}
        </button>
        {canDeclare && (
          <button
            type="button"
            className="control-axes-add-btn"
            disabled={!!addDisabledReason}
            onClick={(e) => {
              e.stopPropagation();
              if (addDisabledReason) return;
              onAddClick();
              // Open the section so the new axis is visible after creation.
              setSectionOpen(true);
            }}
            title={addDisabledReason || 'Declare a new control axis (designer-named axis with a chosen min/max/default). Author its applicable glyphs + brace layers after creating it.'}
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
          // Source-derived axes keep the "scoped" badge — the
          // meaningful "does it vary all glyphs or a subset?" signal.
          // Studio axes show no kind badge: by construction they're
          // glyph-scoped, and the "studio" origin was redundant.
          const kindBadge = isStudio
            ? null
            : <span className="kind-badge kind-scoped" title="Glyph-scoped variation. Only some glyphs change as this axis moves.">scoped</span>;

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
                {isStudio && typeof onEditAxis === 'function' && (
                  <button
                    className="control-axis-edit"
                    onClick={(e) => {
                      e.stopPropagation();
                      onEditAxis(ax);
                    }}
                    title="Edit this axis's display name, range, or default. Tag stays the same."
                  >
                    ✎
                  </button>
                )}
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
                  {isStudio && typeof onSetLayers === 'function' ? (
                    <LayersEditor
                      tag={ax.tag}
                      axis={ax}
                      layers={ax.layers || []}
                      allAxes={allAxes || []}
                      onChangeLayers={onSetLayers}
                      onOpenInEditor={onOpenInEditor}
                      onRequestAddModal={setAddLocationFor}
                      vfFamilyId={vfFamilyId}
                      fontLoaded={fontLoaded}
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
          allMasters={allMasters || []}
          vfFamilyId={vfFamilyId}
          fontLoaded={fontLoaded}
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

export default ControlAxes;
