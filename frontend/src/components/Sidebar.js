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

function Sidebar({ axes, coordinates, onAxisChange, disabled, sampleText, onSampleTextChange, selectedInstance, onUpdateInstance, onResetCoordinates, originalCoordinates, fontSize, onFontSizeChange, onDuplicateInstance, onCreateNewInstance, avar2Mode, avar2Instances, avar2Axes, onAddAvar2Axis, onUpdateAvar2Axis, onUpdateAvar2Mapping, onReloadAvar2Data, glyphsFileHasUnsavedChanges, getInstanceSyncStatus, instances, building = false, glyphCoverageAxes = [], disabledControlAxes, onToggleDisableControlAxis, onCreateControlAxis, onDeleteControlAxis, onSetControlAxisLayers, onOpenControlAxisInEditor }) {
  // CONTROL AXES — modal for declaring a new axis. State + render
  // live in Sidebar because the +Add button does too; the App-level
  // handler does the actual POST + refetch and surfaces the result.
  const [showAddControlAxisModal, setShowAddControlAxisModal] = useState(false);
  const [showDuplicateModal, setShowDuplicateModal] = useState(false);
  const [showNewInstanceModal, setShowNewInstanceModal] = useState(false);
  const [showAddAxisModal, setShowAddAxisModal] = useState(false);
  const [showEditAxisModal, setShowEditAxisModal] = useState(false);
  const [editingAxisName, setEditingAxisName] = useState(null);
  const [editingValue, setEditingValue] = useState({ instance: null, axis: null, value: null });
  const [savingValue, setSavingValue] = useState(false);
  const [valueError, setValueError] = useState(null);
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
        <h2>Select a style on the right</h2>
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
          <label className="axis-name">Font Size: {formatAxisValue(fontSize)}rem</label>
        </div>
        <div className="axis-slider-container">
          <input
            type="range"
            min="0.5"
            max="12"
            step="0.1"
            value={fontSize}
            onChange={(e) => onFontSizeChange(parseFloat(e.target.value))}
            className="axis-slider"
          />
          <div className="axis-values">
            <span className="axis-min">0.5</span>
            {fontSizeEditing ? (
              <input
                ref={fontSizeInputRef}
                type="text"
                className="axis-current axis-current-editing"
                value={fontSizeEditValue}
                onChange={(e) => setFontSizeEditValue(e.target.value)}
                onBlur={() => {
                  const numValue = parseFloat(fontSizeEditValue);
                  if (isNaN(numValue)) {
                    setFontSizeEditing(false);
                    return;
                  }
                  const clampedValue = Math.max(0.5, Math.min(12, numValue));
                  const roundedValue = Math.round(clampedValue * 1000) / 1000;
                  onFontSizeChange(roundedValue);
                  setFontSizeEditing(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    const numValue = parseFloat(fontSizeEditValue);
                    if (isNaN(numValue)) {
                      setFontSizeEditing(false);
                      return;
                    }
                    const clampedValue = Math.max(0.5, Math.min(12, numValue));
                    const roundedValue = Math.round(clampedValue * 1000) / 1000;
                    onFontSizeChange(roundedValue);
                    setFontSizeEditing(false);
                  } else if (e.key === 'Escape') {
                    setFontSizeEditing(false);
                    setFontSizeEditValue('');
                  } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    const numValue = parseFloat(fontSizeEditValue) || fontSize;
                    const newValue = Math.min(12, numValue + 0.1);
                    const roundedValue = Math.round(newValue * 1000) / 1000;
                    setFontSizeEditValue(roundedValue.toFixed(3));
                    onFontSizeChange(roundedValue);
                  } else if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    const numValue = parseFloat(fontSizeEditValue) || fontSize;
                    const newValue = Math.max(0.5, numValue - 0.1);
                    const roundedValue = Math.round(newValue * 1000) / 1000;
                    setFontSizeEditValue(roundedValue.toFixed(3));
                    onFontSizeChange(roundedValue);
                  }
                }}
              />
            ) : (
              <span 
                className="axis-current axis-current-clickable"
                onClick={() => {
                  setFontSizeEditing(true);
                  setFontSizeEditValue(fontSize.toFixed(3));
                }}
                title="Click to edit value"
              >
                {formatAxisValue(fontSize)}
              </span>
            )}
            <span className="axis-max">12.0</span>
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
          // (has_master_coverage=true). Empty axes — the avar2 mapping
          // targets like ``wght`` — live in the AVAR2 MAPPINGS section
          // below where they belong conceptually (with the mapping rows
          // that drive them), not duplicated up here.
          const coreAxes = axes.filter(a => a.has_master_coverage !== false);

          return (
            <>
              {coreAxes.length > 0 && (
                <div className="axis-group">
                  <h3 className="axis-group-heading">Core / parametric axes</h3>
                  {coreAxes.map(renderAxis)}
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
        allAxes={axes}
        disabledAxes={disabledControlAxes || new Set()}
        onToggleDisable={onToggleDisableControlAxis || (() => {})}
        onAddClick={onCreateControlAxis ? () => setShowAddControlAxisModal(true) : undefined}
        onDeleteAxis={onDeleteControlAxis}
        onSetLayers={onSetControlAxisLayers}
        onOpenInEditor={onOpenControlAxisInEditor}
      />
      {onCreateControlAxis && (
        <AddControlAxisModal
          isOpen={showAddControlAxisModal}
          onClose={() => setShowAddControlAxisModal(false)}
          onCreate={onCreateControlAxis}
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
            // without committing to a row.
            if (!selectedInstance) {
              const columns = avar2Axes?.traditional_axes?.columns || [];
              return (
                <div className="traditional-axes-list">
                  {columns.filter(col => col.toUpperCase() !== 'SPAC').map(col => (
                    <div key={col} className="traditional-axis-item">
                      <div className="traditional-axis-tag">{col}</div>
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
                    Mapping rows live in the sibling -avar.csv. This instance doesn't have one yet.
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
                      const isSaving = savingValue && editingValue.instance === selectedInstance.name && editingValue.axis === axisColumn;
                      
                      // Check if this is a parametric axis (exists in Glyphs file) - cannot edit
                      // Use is_parametric flag from metadata, fallback to checking parametric_axes array
                      const isParametricAxis = axisMeta?.is_parametric === true || avar2Axes?.parametric_axes?.includes(axisColumn) || false;
                      
                      // Get min/max from metadata or use defaults
                      const axisMin = axisMeta?.min ?? -1000;
                      const axisMax = axisMeta?.max ?? 1000;
                      
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
                          {!hasValue && !isEditing ? (
                            <div
                              className="traditional-axis-value traditional-axis-value-placeholder"
                              title={`No avar2 mapping for "${axisColumn}" on this instance. SmallOpsz / internal-only rows in Glyphs aren't declared in the avar2 mapping CSV.`}
                            >
                              —
                            </div>
                          ) : isEditing && !isParametricAxis ? (
                            <input
                              type="number"
                              className="traditional-axis-value-input"
                              defaultValue={value}
                              step="0.1"
                              autoFocus
                              ref={(input) => {
                                // Select only on first mount. An inline
                                // ref callback runs on every render, so
                                // unconditionally calling .select() here
                                // re-selected the typed text on every
                                // keystroke and the next character
                                // replaced what was already there —
                                // typing "700" only ever showed "0".
                                if (input && !input.dataset.selected) {
                                  input.dataset.selected = "1";
                                  input.select();
                                }
                              }}
                              onBlur={async (e) => {
                                const newValue = parseFloat(e.target.value);
                                if (isNaN(newValue)) {
                                  setEditingValue({ instance: null, axis: null, value: null });
                                  return;
                                }
                                
                                // Validate range
                                if (newValue < axisMin || newValue > axisMax) {
                                  setValueError(`Value must be between ${axisMin} and ${axisMax}`);
                                  setTimeout(() => setValueError(null), 3000);
                                  setEditingValue({ instance: null, axis: null, value: null });
                                  return;
                                }
                                
                                setSavingValue(true);
                                setValueError(null);
                                try {
                                  await onUpdateAvar2Mapping(selectedInstance.name, axisColumn, newValue);
                                  setEditingValue({ instance: null, axis: null, value: null });
                                } catch (err) {
                                  setValueError(err.message || 'Failed to save value');
                                  setTimeout(() => setValueError(null), 3000);
                                  // Revert to original value
                                  setEditingValue({ instance: null, axis: null, value: null });
                                } finally {
                                  setSavingValue(false);
                                }
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.target.blur();
                                } else if (e.key === 'Escape') {
                                  setEditingValue({ instance: null, axis: null, value: null });
                                }
                              }}
                            />
                          ) : (
                            <div 
                              className={`traditional-axis-value ${isSaving ? 'saving' : (isParametricAxis ? '' : 'clickable')}`}
                              onClick={() => {
                                if (!isSaving && !isParametricAxis) {
                                  setEditingValue({ 
                                    instance: selectedInstance.name, 
                                    axis: axisColumn, 
                                    value: value 
                                  });
                                }
                              }}
                              title={isParametricAxis ? "Parametric axis (from Glyphs file) - cannot edit" : `Click to edit (range: ${axisMin} to ${axisMax})`}
                            >
                              {isSaving ? '...' : value.toFixed(1)}
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
      />
    </aside>
  );
}

export default Sidebar;
