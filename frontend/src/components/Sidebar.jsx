import React, { useState, useEffect, useRef } from 'react';
import './Sidebar.css';
import AxisControl from './AxisControl';
// SPAC support is deferred from v1; the SpacAxisControl import was
// removed alongside the SPAC mode toggle and the spac-error-state UI.
import DuplicateModal from './DuplicateModal';
import AddAxisModal from './AddAxisModal';
import EditAxisModal from './EditAxisModal';
import ControlAxes from './ControlAxes';
import AddControlAxisModal from './AddControlAxisModal';
import { formatAxisValue } from '../utils/formatNumber';

// Font size stays in rem internally (CSS font-size + /api/text-width's
// font_size_rem); the slider presents it in pt. 12pt = 1rem at the
// default 16px root, so 9pt…144pt maps to 0.75rem…12rem.
const remToPt = (rem) => rem * 12;
const ptToRem = (pt) => pt / 12;
const roundHalf = (v) => Math.round(v * 2) / 2;
const formatPt = (rem) => {
  const pt = roundHalf(remToPt(rem));
  return Number.isInteger(pt) ? String(pt) : pt.toFixed(1);
};

function Sidebar({ axes, coordinates, onAxisChange, disabled, sampleText, onSampleTextChange, selectedInstance, familyName, onUpdateInstance, onResetCoordinates, originalCoordinates, fontSize, onFontSizeChange, onDuplicateInstance, onCreateNewInstance, avar2Mode, avar2Instances, avar2Axes, onAddAvar2Axis, onUpdateAvar2Axis, onDeleteAvar2Axis, onUpdateAvar2Mapping, onReloadAvar2Data, glyphsFileHasUnsavedChanges, getInstanceSyncStatus, instances, masters = [], vfFamilyId, fontLoaded, building = false, glyphCoverageAxes = [], glyphChars = {}, disabledControlAxes, onToggleDisableControlAxis, onCreateControlAxis, onUpdateControlAxis, onDeleteControlAxis, onSetControlAxisLayers, onControlAxisLayerDelta, onOpenControlAxisInEditor, controlAxisAuthoringDisabledReason }) {
  // CONTROL AXES — modal for declaring a new axis. State + render
  // live in Sidebar because the +Add button does too; the App-level
  // handler does the actual POST + refetch and surfaces the result.
  const [showAddControlAxisModal, setShowAddControlAxisModal] = useState(false);
  const [editingControlAxis, setEditingControlAxis] = useState(null);
  const [showDuplicateModal, setShowDuplicateModal] = useState(false);
  const [showNewInstanceModal, setShowNewInstanceModal] = useState(false);
  const [showAddAxisModal, setShowAddAxisModal] = useState(false);
  const [showEditAxisModal, setShowEditAxisModal] = useState(false);
  const [editingAxisName, setEditingAxisName] = useState(null);
  const [editingValue, setEditingValue] = useState({ instance: null, axis: null });
  const [valueError, setValueError] = useState(null);
  // Draft values for the AVAR2 MAPPINGS sliders, keyed by
  // "<instance>:<axisColumn>". Shown optimistically until the commit
  // lands and the refetched server state carries the value. Debounce
  // matters: a drag fires onChange per pixel and each commit is a CSV
  // write + full avar2 refetch — pointer-up flushes immediately, the
  // timer is the fallback for keyboard/wheel value changes.
  const [mappingDrafts, setMappingDrafts] = useState({});
  const mappingCommitTimers = useRef({});
  // All commits run through ONE promise chain: the server rewrites
  // the whole CSV per commit and each refetch must observe its own
  // write, so overlapping commits (two axes dragged back-to-back)
  // could land out of order or trip the CSV external-edit guard if
  // allowed to interleave.
  const mappingCommitChain = useRef(Promise.resolve());

  const fireMappingCommit = (key, instanceName, axisColumn, newValue) => {
    delete mappingCommitTimers.current[key];
    mappingCommitChain.current = mappingCommitChain.current.then(async () => {
      setValueError(null);
      try {
        await onUpdateAvar2Mapping(instanceName, axisColumn, newValue);
      } catch (err) {
        setValueError(err.message || 'Failed to save value');
        setTimeout(() => setValueError(null), 3000);
      } finally {
        // Drop the draft only if no newer change superseded it: on
        // success the refetched state now carries this value; on
        // failure the row snaps back to the last saved value (a
        // visible revert beats silently showing an unsaved number).
        setMappingDrafts(prev => {
          if (prev[key] !== newValue) return prev;
          const next = { ...prev };
          delete next[key];
          return next;
        });
      }
    });
  };

  const scheduleMappingCommit = (instanceName, axisColumn, newValue, delayMs = 450) => {
    const key = `${instanceName}:${axisColumn}`;
    setMappingDrafts(prev => ({ ...prev, [key]: newValue }));
    const pending = mappingCommitTimers.current[key];
    if (pending) clearTimeout(pending.timer);
    const timer = setTimeout(
      () => fireMappingCommit(key, instanceName, axisColumn, newValue),
      delayMs
    );
    mappingCommitTimers.current[key] = {
      timer,
      flush: () => {
        clearTimeout(timer);
        fireMappingCommit(key, instanceName, axisColumn, newValue);
      },
    };
  };

  // Commit one row's pending change right now (slider pointer-up)
  // instead of waiting out the debounce.
  const flushMappingCommit = (instanceName, axisColumn) => {
    const pending = mappingCommitTimers.current[`${instanceName}:${axisColumn}`];
    if (pending) pending.flush();
  };

  useEffect(() => {
    const timers = mappingCommitTimers.current;
    // FLUSH (not discard) anything still pending on unmount — the UI
    // already showed the dragged value as applied, so dropping the
    // timer here would silently lose the edit.
    return () => Object.values(timers).forEach(p => p.flush());
  }, []);
  const [fontSizeEditing, setFontSizeEditing] = useState(false);
  const [fontSizeEditValue, setFontSizeEditValue] = useState('');
  const fontSizeInputRef = useRef(null);

  // Focus input when entering edit mode for font size
  useEffect(() => {
    if (fontSizeEditing && fontSizeInputRef.current) {
      fontSizeInputRef.current.focus();
      fontSizeInputRef.current.select();
    }
  }, [fontSizeEditing]);

  // Commit/nudge helpers for the pt-typed font-size field: the user
  // works in pt, the state stays in rem (see module-level converters).
  const commitFontSizePt = (raw) => {
    const num = parseFloat(raw);
    if (!isNaN(num)) {
      const clamped = Math.max(9, Math.min(144, roundHalf(num)));
      onFontSizeChange(ptToRem(clamped));
    }
    setFontSizeEditing(false);
  };
  const nudgeFontSizePt = (delta) => {
    const current = parseFloat(fontSizeEditValue) || remToPt(fontSize);
    const next = Math.max(9, Math.min(144, roundHalf(current + delta)));
    setFontSizeEditValue(formatPt(ptToRem(next)));
    onFontSizeChange(ptToRem(next));
  };

  // Update editValue when fontSize changes externally (e.g., from slider)
  useEffect(() => {
    if (!fontSizeEditing) {
      setFontSizeEditValue('');
    }
  }, [fontSize, fontSizeEditing]);
  
  // Check if coordinates have been modified from original
  const coordinatesChanged = React.useMemo(() => {
    if (!selectedInstance || !originalCoordinates || Object.keys(originalCoordinates).length === 0) {
      return false;
    }
    // Check if any coordinate differs from original
    return Object.keys(originalCoordinates).some(
      key => {
        const current = coordinates[key] ?? 0;
        const original = originalCoordinates[key] ?? 0;
        return Math.abs(current - original) > 0.01;
      }
    );
  }, [selectedInstance, coordinates, originalCoordinates]);
  
  // Always show "Duplicate Instance" — this button literally opens the
  // Duplicate modal. The historical rename to "Add New Instance" when
  // the row had pending edits was misleading once the proper
  // "+ New Instance" button below AVAR2 MAPPINGS landed.
  const duplicateButtonText = "Duplicate Instance";
  
  return (
    <aside className="sidebar">
      {selectedInstance ? (
        <h2>{selectedInstance.name}</h2>
      ) : (
        <h2>{familyName || 'Select a style on the right'}</h2>
      )}
      
      <div className="sample-text-section">
        <textarea
          value={sampleText}
          onChange={(e) => onSampleTextChange(e.target.value)}
          className="sample-text-input"
          placeholder="Enter sample text..."
          rows={3}
        />
      </div>
      
      <div className="font-size-control">
        <div className="axis-header">
          <label className="axis-name">Font Size: {formatPt(fontSize)}pt</label>
        </div>
        <div className="axis-slider-container">
          <input
            type="range"
            min="9"
            max="144"
            step="0.5"
            value={roundHalf(remToPt(fontSize))}
            onChange={(e) => onFontSizeChange(ptToRem(parseFloat(e.target.value)))}
            className="axis-slider"
          />
          <div className="axis-values">
            <span className="axis-min">9</span>
            {fontSizeEditing ? (
              <input
                ref={fontSizeInputRef}
                type="text"
                className="axis-current axis-current-editing"
                value={fontSizeEditValue}
                onChange={(e) => setFontSizeEditValue(e.target.value)}
                onBlur={() => commitFontSizePt(fontSizeEditValue)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    commitFontSizePt(fontSizeEditValue);
                  } else if (e.key === 'Escape') {
                    setFontSizeEditing(false);
                    setFontSizeEditValue('');
                  } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    nudgeFontSizePt(0.5);
                  } else if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    nudgeFontSizePt(-0.5);
                  }
                }}
              />
            ) : (
              <span 
                className="axis-current axis-current-clickable"
                onClick={() => {
                  setFontSizeEditing(true);
                  setFontSizeEditValue(formatPt(fontSize));
                }}
                title="Click to edit value"
              >
                {formatPt(fontSize)}
              </span>
            )}
            <span className="axis-max">144</span>
          </div>
        </div>
      </div>
      
      <div className="axis-controls">
        {(() => {
          // Every axis renders as a plain AxisControl. SPAC support is
          // deferred; if a source happens to declare a SPAC axis it just
          // renders as a normal parametric/avar2 axis with no special UX.
          const renderAxis = (axis) => (
            <AxisControl
              key={axis.tag}
              axis={axis}
              value={coordinates[axis.tag] ?? axis.default}
              onChange={(value) => onAxisChange(axis.tag, value)}
              disabled={disabled}
            />
          );

          // Sidebar only renders the axes that actively deform the font
          // (has_master_coverage=true) plus the GRAD axis when grade is
          // enabled. Empty axes — the avar2 mapping targets like ``wght``
          // — live in the AVAR2 MAPPINGS section below where they belong
          // conceptually (with the mapping rows that drive them), not
          // duplicated up here.
          // Secondary parametric (control) axes get their own group: they
          // deform only their applicable glyphs, and their value is preview
          // state — saves strip it, like SPAC. The static app flags them
          // has_master_coverage=false (they'd vanish from Core), the server
          // flags true (they'd blend into Core); splitting on is_control_axis
          // gives both the same sidebar as the Preview tab.
          const secondaryAxes = axes.filter(a => a.is_control_axis);
          const coreAxes = axes.filter(a => !a.is_control_axis && (a.has_master_coverage !== false || a.is_grade_axis));

          return (
            <>
              {coreAxes.length > 0 && (
                <div className="axis-group">
                  <h3 className="axis-group-heading">Core / parametric axes</h3>
                  {coreAxes.map(renderAxis)}
                </div>
              )}

              {secondaryAxes.length > 0 && (
                <div className="axis-group axis-group-secondary">
                  <h3 className="axis-group-heading">
                    Secondary parametric axes
                    <span className="axis-group-sub">glyph-scoped · preview only</span>
                  </h3>
                  {secondaryAxes.map(axis => {
                    const off = !!(disabledControlAxes && disabledControlAxes.has(axis.tag));
                    return (
                      <div
                        key={axis.tag}
                        className={`axis-secondary${off ? ' axis-secondary-off' : ''}`}
                        title={off
                          ? `${axis.tag} is disabled in preview (eye icon in the section below) — rows render it at the axis default.`
                          : `Moves only the glyphs ${axis.tag} has layers for. Preview state: not saved with the instance.`}
                      >
                        <AxisControl
                          axis={axis}
                          value={coordinates[axis.tag] ?? axis.default}
                          onChange={(value) => onAxisChange(axis.tag, value)}
                          disabled={disabled || off}
                        />
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          );
        })()}
      </div>

      {/* CONTROL AXES — sits directly below the parametric axes
          since both are "axes that deform the font" (vs AVAR2
          MAPPINGS which is mapping-table state). Surfaces axes
          whose effect is constrained to a named subset of glyphs
          (case-split parametric axes like Roboto Delta's
          XOUC/XOLC/XOFI). Component returns null when there are
          no scoped/partial axes, so pure-parametric fonts like
          Crispy Mini won't see this section. */}
      <ControlAxes
        axes={glyphCoverageAxes}
        glyphChars={glyphChars}
        allAxes={axes}
        allMasters={masters}
        vfFamilyId={vfFamilyId}
        fontLoaded={fontLoaded}
        building={building}
        disabledAxes={disabledControlAxes || new Set()}
        onToggleDisable={onToggleDisableControlAxis || (() => {})}
        onAddClick={onCreateControlAxis ? () => setShowAddControlAxisModal(true) : undefined}
        addDisabledReason={controlAxisAuthoringDisabledReason}
        onEditAxis={onUpdateControlAxis ? (ax) => setEditingControlAxis(ax) : undefined}
        onDeleteAxis={onDeleteControlAxis}
        onLayerDelta={onControlAxisLayerDelta}
        onOpenInEditor={onOpenControlAxisInEditor}
      />
      {(onCreateControlAxis || onUpdateControlAxis) && (
        <AddControlAxisModal
          isOpen={showAddControlAxisModal || !!editingControlAxis}
          onClose={() => {
            setShowAddControlAxisModal(false);
            setEditingControlAxis(null);
          }}
          onCreate={onCreateControlAxis}
          onUpdate={onUpdateControlAxis}
          editAxis={editingControlAxis}
          existingTags={(glyphCoverageAxes || []).map(a => a.tag)}
        />
      )}

      {avar2Mode && (() => {
        // Check if there are any traditional axes in the CSV
        const hasTraditionalAxes = avar2Axes?.traditional_axes?.columns && avar2Axes.traditional_axes.columns.length > 0;

        return (
        <div className="avar2-traditional-axes">
          {/* Heading row: title left, compact "+ Add" button right. The
              button is the canonical entry point for declaring a new
              traditional/input axis (wdth, opsz, …). The section header
              is always visible — even without a selected instance —
              so the user knows where the mapping rows live. */}
          <div className="avar2-section-header">
            <h3 className="avar2-section-title">AVAR2 MAPPINGS</h3>
            <button
              className="btn-add-axis-inline"
              onClick={() => setShowAddAxisModal(true)}
              title="Declare a new traditional / avar2-input axis (e.g. wdth, opsz) that the mapping table will route into parametric coords"
            >
              + Add
            </button>
          </div>
          {(() => {
            // No loading state any more. ``avar2Axes`` is null both
            // BEFORE the first fetch lands AND after a fetch that
            // 404'd because the source has no sibling -avar.csv. The
            // previous code treated those identically and printed
            // "Loading mappings..." forever for the latter — the
            // "+ Add" hint below already covers both cases truthfully
            // (the user needs to declare an axis to populate it),
            // so we fall straight through to it. The momentary flash
            // on first fetch (≤ one render frame) is acceptable.
            if (!hasTraditionalAxes) {
              return (
                <div className="avar2-empty-hint">
                  No mapping axes yet. Use <strong>+ Add</strong> above to declare one (wdth, opsz, …).
                </div>
              );
            }

            // No instance selected: render the same axis grid with empty
            // value cells so the user sees what mapping inputs exist
            // without committing to a row. Tags stay clickable — axis
            // edit/delete (EditAxisModal) doesn't depend on a selection.
            if (!selectedInstance) {
              const columns = avar2Axes?.traditional_axes?.columns || [];
              return (
                <div className="traditional-axes-list">
                  {columns.filter(col => col.toUpperCase() !== 'SPAC').map(col => (
                    <div key={col} className="traditional-axis-item">
                      <div
                        className="traditional-axis-tag clickable"
                        onClick={() => {
                          setEditingAxisName(col);
                          setShowEditAxisModal(true);
                        }}
                        title="Click to edit axis metadata"
                      >
                        {col}
                      </div>
                      <div className="traditional-axis-value traditional-axis-value-placeholder">
                        —
                      </div>
                    </div>
                  ))}
                </div>
              );
            }
            
            const mapping = avar2Instances.find(
              inst => inst.instance_name === selectedInstance.name
            );
            // Iterate column-driven (instead of mapping-driven) so:
            //   1. SPAC stays filtered out — the deferred axis would
            //      otherwise sneak back in from the CSV's ``in:`` keys.
            //   2. Rows with no avar2 mapping at all (e.g. the
            //      ``…SmallOpsz`` instances Glyphs uses internally to
            //      derive an optical-size master) still render the
            //      column framework with ``—`` placeholders, instead
            //      of collapsing the whole section to nothing.
            {
              const traditionalAxes = (mapping && mapping.avar2_mapping && mapping.avar2_mapping.in) || {};
              const metadata = avar2Axes?.metadata || {};
              const axisColumns = (avar2Axes?.traditional_axes?.columns || [])
                .filter(col => col.toUpperCase() !== 'SPAC');

              if (axisColumns.length === 0) {
                return (
                  <div className="avar2-empty-hint">
                    This instance has no mapping values yet.
                  </div>
                );
              }

              // Column → normalized-tag map. Mirrors the inverse map
              // used previously when iteration was mapping-driven.
              const columnToTagMap = {
                'WGHT': 'wght',
                'WDTH': 'wdth',
                'OPSZ': 'opsz',
                'CNTR': 'cntr',
                'CONTRAST': 'cntr',
              };

              return (
                <>
                  <div className="traditional-axes-list">
                    {axisColumns.map((axisColumn) => {
                      const tag = columnToTagMap[axisColumn.toUpperCase()] || axisColumn.toLowerCase();
                      const value = traditionalAxes[tag];
                      const hasValue = value !== undefined && value !== null;
                      
                      // Use metadata from backend (which ensures all axes are in JSON)
                      // CSV column names (like "WGHT") are the keys in metadata
                      const axisMeta = metadata[axisColumn];
                      
                      // Ensure we have metadata - if not, something is wrong
                      if (!axisMeta) {
                        console.warn(`Missing metadata for axis column: ${axisColumn}`);
                      }
                      
                      // Use registered_tag for avar2 mapping labels (not display_name)
                      // Fallback to normalized tag if metadata is missing
                      const axisLabel = axisMeta?.registered_tag || tag;
                      
                      const isEditing = editingValue.instance === selectedInstance.name && editingValue.axis === axisColumn;

                      // Check if this is a parametric axis (exists in Glyphs file) - cannot edit
                      // Use is_parametric flag from metadata, fallback to checking parametric_axes array
                      const isParametricAxis = axisMeta?.is_parametric === true || avar2Axes?.parametric_axes?.includes(axisColumn) || false;

                      // Get min/max from metadata or use defaults
                      const axisMin = axisMeta?.min ?? -1000;
                      const axisMax = axisMeta?.max ?? 1000;

                      const draftKey = `${selectedInstance.name}:${axisColumn}`;
                      const draft = mappingDrafts[draftKey];
                      // Unset cells still get a LIVE slider. Fresh and
                      // duplicated instances start with blank mapping
                      // cells (deliberately — a stamped 0 becomes real
                      // avar2 data), but a dead "—" placeholder left no
                      // way to author the value. The slider rests at
                      // the axis default; the first drag or typed value
                      // commits it.
                      const axisDefault = axisMeta?.default ?? (axisMin + axisMax) / 2;
                      const unset = !hasValue && draft === undefined;
                      const shown = draft !== undefined ? draft : (hasValue ? value : axisDefault);
                      const sliderStep = (axisMax - axisMin) > 100 ? 1 : 0.1;

                      const commitTyped = (raw) => {
                        setEditingValue({ instance: null, axis: null });
                        const numValue = parseFloat(raw);
                        if (isNaN(numValue)) return;
                        // Clamp to range — same semantics as the
                        // AxisControl sliders above.
                        const clamped = Math.max(axisMin, Math.min(axisMax, numValue));
                        scheduleMappingCommit(selectedInstance.name, axisColumn, clamped, 0);
                      };

                      return (
                        <div key={axisColumn} className="traditional-axis-item">
                          <div
                            className={`traditional-axis-tag ${isParametricAxis ? '' : 'clickable'}`}
                            onClick={() => {
                              if (!isParametricAxis) {
                                setEditingAxisName(axisColumn);
                                setShowEditAxisModal(true);
                              }
                            }}
                            title={isParametricAxis ? "Parametric axis (from Glyphs file) - cannot edit" : "Click to edit axis metadata"}
                          >
                            {axisLabel}
                            {isParametricAxis && <span className="parametric-badge"> (Glyphs)</span>}
                          </div>
                          {isParametricAxis ? (
                            <div
                              className="traditional-axis-value"
                              title="Parametric axis (from Glyphs file) - cannot edit"
                            >
                              {hasValue ? formatAxisValue(Number(value)) : '—'}
                            </div>
                          ) : (
                            // Mapping value drives like any other axis: a
                            // slider spanning the axis's min…max with the
                            // current value centered beneath it — click the
                            // number to type an exact figure. Same layout
                            // as the parametric AxisControl sliders above,
                            // so the section reads as one system.
                            <div className="axis-slider-container traditional-axis-slider-container">
                              <input
                                type="range"
                                className="axis-slider"
                                min={axisMin}
                                max={axisMax}
                                step={sliderStep}
                                value={shown}
                                onChange={(e) => scheduleMappingCommit(selectedInstance.name, axisColumn, parseFloat(e.target.value))}
                                onPointerUp={() => flushMappingCommit(selectedInstance.name, axisColumn)}
                                onKeyUp={() => flushMappingCommit(selectedInstance.name, axisColumn)}
                              />
                              <div className="axis-values">
                                <span className="axis-min">{axisMin}</span>
                                {isEditing ? (
                                  <input
                                    type="number"
                                    className="traditional-axis-value-input"
                                    defaultValue={shown}
                                    step={sliderStep}
                                    autoFocus
                                    ref={(input) => {
                                      // Select only on first mount — an inline ref
                                      // callback runs on every render, and
                                      // re-selecting per keystroke eats the input.
                                      if (input && !input.dataset.selected) {
                                        input.dataset.selected = "1";
                                        input.select();
                                      }
                                    }}
                                    onBlur={(e) => commitTyped(e.target.value)}
                                    onKeyDown={(e) => {
                                      if (e.key === 'Enter') {
                                        e.target.blur();
                                      } else if (e.key === 'Escape') {
                                        setEditingValue({ instance: null, axis: null });
                                      }
                                    }}
                                  />
                                ) : (
                                  <span
                                    className={`axis-current axis-current-clickable${draft !== undefined ? ' traditional-axis-saving' : ''}${unset ? ' traditional-axis-unset' : ''}`}
                                    onClick={() => setEditingValue({ instance: selectedInstance.name, axis: axisColumn })}
                                    title={unset
                                      ? `No mapping value yet — drag the slider or click to type one. The slider rests at the axis default (${axisDefault}).`
                                      : `Click to type a value (range: ${axisMin} to ${axisMax})`}
                                  >
                                    {unset ? '—' : formatAxisValue(Number(shown))}
                                  </span>
                                )}
                                <span className="axis-max">{axisMax}</span>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {valueError && (
                    <div className="avar2-error-message">{valueError}</div>
                  )}
                </>
              );
            }
          })()}
        </div>
        );
      })()}

      {/* New-instance entry point — sits right below AVAR2 MAPPINGS as
          the primary "make a new row" action. Duplicate (below) is the
          alternative path when an existing instance is selected. */}
      {onCreateNewInstance && (
        <div className="new-instance-button-section">
          <button
            onClick={() => setShowNewInstanceModal(true)}
            className="btn btn-new-instance"
            title="Create a fresh studio-only instance with values you specify per axis"
          >
            + New Instance
          </button>
        </div>
      )}

      {/* Update All Instances was removed in favor of singular updates
          per row via the orange sync-dot flyout. Each instance saves
          itself when the user clicks Update Instance there. */}


      {/* Reset only renders when there's a row selected to reset.
          Previously the section rendered with the button greyed out
          whenever ``disabled`` was false, which still consumed
          vertical space in the empty state. */}
      {selectedInstance && !disabled && (
        <div className="reset-button-section">
          <button
            onClick={onResetCoordinates}
            className="btn btn-reset"
          >
            Reset to Original
          </button>
        </div>
      )}
      
      {selectedInstance && (
        <div className="duplicate-button-section">
          <button
            onClick={() => setShowDuplicateModal(true)}
            className="btn btn-duplicate"
          >
            {duplicateButtonText}
          </button>
        </div>
      )}
      
      <DuplicateModal
        isOpen={showDuplicateModal}
        onClose={() => setShowDuplicateModal(false)}
        onConfirm={(newName) => {
          setShowDuplicateModal(false);
          onDuplicateInstance(newName);
        }}
        instanceName={selectedInstance?.name || ''}
        existingNames={(instances || []).map(i => i.name)}
      />

      <DuplicateModal
        isOpen={showNewInstanceModal}
        onClose={() => setShowNewInstanceModal(false)}
        onConfirm={(newName, newCoords) => {
          setShowNewInstanceModal(false);
          onCreateNewInstance(newName, newCoords);
        }}
        instanceName=""
        mode="new"
        axes={axes || []}
      />
      
      <AddAxisModal
        isOpen={showAddAxisModal}
        onClose={() => setShowAddAxisModal(false)}
        onConfirm={async (axisData) => {
          try {
            await onAddAvar2Axis(axisData);
            setShowAddAxisModal(false);
            // Force reload after a brief delay to ensure backend has processed
            setTimeout(() => {
              if (onReloadAvar2Data) {
                onReloadAvar2Data();
              }
            }, 500);
          } catch (err) {
            // Error will be shown in modal or handled by parent
            console.error('Failed to add axis:', err);
            alert(err.message || 'Failed to add axis');
            // Don't close modal on error so user can see the error
          }
        }}
        existingAxes={avar2Axes?.traditional_axes?.columns || []}
        existingMetadata={avar2Axes?.metadata || {}}
        parametricAxes={avar2Axes?.parametric_axes || []}
      />
      
      <EditAxisModal
        isOpen={showEditAxisModal}
        onClose={() => {
          setShowEditAxisModal(false);
          setEditingAxisName(null);
        }}
        onConfirm={async (axisData) => {
          try {
            // Check if this is a parametric axis - should not be editable
            const axisMeta = avar2Axes?.metadata?.[editingAxisName];
            const isParametric = axisMeta?.is_parametric === true || avar2Axes?.parametric_axes?.includes(editingAxisName) || false;
            if (isParametric) {
              throw new Error("Cannot edit parametric axes - they are managed in the Glyphs file");
            }
            await onUpdateAvar2Axis(editingAxisName, axisData);
            setShowEditAxisModal(false);
            setEditingAxisName(null);
          } catch (err) {
            // Error will be shown in modal or handled by parent
            console.error('Failed to update axis:', err);
            alert(err.message || 'Failed to update axis');
          }
        }}
        axisName={editingAxisName}
        axisMetadata={editingAxisName && avar2Axes?.metadata?.[editingAxisName]}
        existingAxes={(avar2Axes?.traditional_axes?.columns || []).map(col => ({
          axisName: col,
          registeredTag: avar2Axes?.metadata?.[col]?.registered_tag || ''
        }))}
        isParametricAxis={editingAxisName && (avar2Axes?.parametric_axes?.includes(editingAxisName) || false)}
        onDelete={onDeleteAvar2Axis ? async (name) => {
          try {
            await onDeleteAvar2Axis(name);
            setShowEditAxisModal(false);
            setEditingAxisName(null);
          } catch (err) {
            alert(err.message || 'Failed to delete axis');
          }
        } : undefined}
      />
    </aside>
  );
}

export default Sidebar;
