import React, { useState, useRef, useEffect } from 'react';
import './InstanceRow.css';
import InstanceFlyout from './InstanceFlyout';
import GradeBadge from './GradeBadge';
import RoundingBadge from './RoundingBadge';
import SlantBadge from './SlantBadge';
import KinetikernBadge from './KinetikernBadge';
import { formatAxisValue } from '../utils/formatNumber';

// S1(d): the ORIGIN tooltip says whether this row's parametric location is a
// point the source actually interpolates. A row that sits ON a master relocates
// for free; one that misses needs the instancer to synthesise the origin, which
// is what ends in the gvar header overflow on a twin-heavy source.
function originMasterSummary(master) {
  if (!master) return '';
  if (master.on_master) {
    return ` Its parametric location sits on the compiled master “${master.nearest}”, so the origin is a point the source already interpolates.`;
  }
  const parts = Object.entries(master.delta || {})
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, 4)
    .map(([tag, value]) => `${tag} ${value > 0 ? '+' : ''}${value}`);
  const gap = parts.length ? ` (${parts.join(', ')} font units)` : '';
  return ` It does NOT sit on a compiled master: the nearest is “${master.nearest}”, off by${gap}. The build has to synthesise this origin with the instancer, which densifies gvar.`;
}

function InstanceRow({ instance, isSelected, isMultiSelected, onSelect, editingCoordinates, instanceEditingCoordinates, sampleText, fontLoaded, fontSize, vfFamilyId, onDelete, onMove, allInstances, syncStatus = 'green', onRename, onUpdateInstanceStudio, onUpdateInstanceSource, onDemoteFromSource, disabledControlAxes, axisDefaults, gradeEnabled, gradePct, gradeMaxPct, gradeDefaultPct, gradeDiagnostics, onSaveInstanceGrade, onRemoveInstanceGrade, parametricTags, transformTags, gradeTag,
  roundingEnabled, roundingPct, roundingDefaultPct,
  onSaveInstanceRounding, onRemoveInstanceRounding,
  slantEnabled, slantPct, slantDefaultPct, slantAxisMin,
  onSaveInstanceSlant, onRemoveInstanceSlant,
  kinetikernEnabled = false, kinetikernLooseness = null,
  onRemoveInstanceKinetikern, onOpenKinetikern,
  isDefaultOrigin = false,
  originMaster = null,
}) {
  const isStudioOnly = instance.origin === 'studio';
  const [showMoveControls, setShowMoveControls] = useState(false);
  const [movePosition, setMovePosition] = useState('before');
  const [targetInstance, setTargetInstance] = useState(null);
  const [isEditingName, setIsEditingName] = useState(false);
  const [editingName, setEditingName] = useState(instance.name);
  const [showFlyout, setShowFlyout] = useState(false);
  const dotRef = useRef(null);
  const rowRef = useRef(null);
  
  // Build font-variation-settings CSS from coordinates
  // If this row is selected, use editing coordinates (from sliders)
  // Otherwise, use persisted editing coordinates if they exist, or instance coordinates
  const activeCoordinates = isSelected && Object.keys(editingCoordinates).length > 0
    ? editingCoordinates
    : (instanceEditingCoordinates[instance.name] || instance.coordinates);

  // Graded rows render at GRAD 0 — the badge marks the grade, the GRAD
  // chip shows 0, and the outline must agree with both (server-app
  // behavior). The grade itself is inspected with the Preview tab's
  // GRAD slider, or by dragging the sidebar's GRAD slider while the
  // row is selected (editingCoordinates carry the drag; preview only). (An earlier revision injected GRAD at max here so the
  // row showed "the instance at its grade" — near the grade cap that
  // renders closed counters under a chip claiming GRAD: 0.)

  // Apply the CONTROL AXES preview-disable: any axis the user has
  // toggled off via the eye icon renders at its axis default,
  // regardless of the slider / CSV value. Frontend-only — the
  // actual edit state is untouched, so re-enabling restores the
  // user's chosen value immediately.
  const previewCoordinates = React.useMemo(() => {
    const out = { ...activeCoordinates };
    if (!disabledControlAxes || disabledControlAxes.size === 0) {
      return out;
    }
    for (const tag of disabledControlAxes) {
      const fallback = axisDefaults && axisDefaults[tag];
      if (fallback !== undefined) {
        out[tag] = fallback;
      }
    }
    return out;
  }, [activeCoordinates, disabledControlAxes, axisDefaults]);

  // SPAC support is deferred — the coordinate dict is rendered as-is.

  // Pin EVERY fvar axis explicitly. Any axis left unpinned is free
  // for plain CSS to drive: the moment opsz/wght exist in the fvar,
  // font-optical-sizing:auto (the browser default) maps the font
  // SIZE onto opsz and font-weight maps 400 onto wght — routing every
  // row through the avar2 table at (opsz≈size, wght 400) and
  // fattening the hairlines. Rows render the authored PARAMETRIC
  // location: user axes rest at their defaults (where avar2
  // contributes nothing) and the row's coordinates overlay only
  // exact fvar tags — the uppercase OPSZ/WGHT mapping columns never
  // match and stay display-only chips.
  const pinnedAxes = { ...(axisDefaults || {}) };
  for (const [tag, value] of Object.entries(previewCoordinates)) {
    if (tag in pinnedAxes) pinnedAxes[tag] = value;
  }
  // Per-style rounding renders in the row: the style's own percent, or
  // the broad default — its ROND coordinate in the built font.
  if (roundingEnabled && 'ROND' in pinnedAxes) {
    pinnedAxes.ROND = roundingPct != null ? roundingPct : (roundingDefaultPct || 0);
  }
  // Per-style slant likewise: the percent of the axis minimum the
  // built font stamps as the style's slnt coordinate. Styles named
  // Italic/Slanted are never stamped (they keep their own slnt), so
  // they get no overlay and no S badge.
  const slantExempt = /italic|slanted/i.test(instance.name);
  if (slantEnabled && !slantExempt && 'slnt' in pinnedAxes && slantAxisMin != null) {
    const slantRowPct = slantPct != null ? slantPct : (slantDefaultPct || 0);
    pinnedAxes.slnt = slantAxisMin * slantRowPct / 100;
  }
  const axesForRender = Object.keys(pinnedAxes).length > 0 ? pinnedAxes : previewCoordinates;
  let fontVariationSettings = Object.entries(axesForRender)
    .map(([tag, value]) => `"${tag}" ${value}`)
    .join(', ');

  return (
    <div
      ref={rowRef}
      className={`instance-row ${isSelected ? 'selected' : ''}${isMultiSelected ? ' multi-selected' : ''}`}
      onMouseDown={(e) => {
        // Shift-click is range-select, not text-select.
        if (e.shiftKey) e.preventDefault();
      }}
      onClick={(e) => {
        // Don't select if clicking on the dot wrapper
        if (e.target.closest('.sync-status-dot-wrapper')) {
          return;
        }
        onSelect({ shift: e.shiftKey, toggle: e.metaKey || e.ctrlKey });
      }}
      data-instance-name={instance.name}
    >
      <div className="instance-row-header">
        <div className="instance-name-wrapper">
          {isEditingName ? (
            <input
              type="text"
              value={editingName}
              onChange={(e) => setEditingName(e.target.value)}
              onBlur={async () => {
                if (editingName.trim() && editingName.trim() !== instance.name && onRename) {
                  try {
                    await onRename(instance.name, editingName.trim());
                  } catch (err) {
                    // Revert on error
                    setEditingName(instance.name);
                    alert(err.message || 'Failed to rename instance');
                  }
                } else {
                  // Revert if empty or unchanged
                  setEditingName(instance.name);
                }
                setIsEditingName(false);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.target.blur();
                } else if (e.key === 'Escape') {
                  setEditingName(instance.name);
                  setIsEditingName(false);
                }
              }}
              onClick={(e) => e.stopPropagation()}
              className="instance-name-input"
              autoFocus
            />
          ) : (
            <h3
              className="instance-name clickable"
              onClick={(e) => {
                e.stopPropagation();
                if (isSelected && onRename) {
                  setIsEditingName(true);
                  setEditingName(instance.name);
                }
              }}
              title={isSelected && onRename ? "Click to edit name" : ""}
            >
              {/* Origin marker (SRC badge) is rendered next to the
                  sync-status dot in instance-header-right — see below.
                  Studio-only rows get no badge: "no badge" already
                  reads as studio-only since SRC is the affordance. */}
              {instance.name}
            </h3>
          )}
        </div>
        <div className="instance-header-right">
          {/* Always render icons but hide when not selected to prevent layout shift */}
          {onMove && (
            <button
              className="move-instance-btn"
              onClick={(e) => {
                e.stopPropagation();
                if (isSelected) {
                  setShowMoveControls(!showMoveControls);
                  if (!showMoveControls) {
                    setTargetInstance(null);
                    setMovePosition('before');
                  }
                }
              }}
              title="Move this instance"
              style={{ visibility: isSelected ? 'visible' : 'hidden' }}
            >
              ⇅
            </button>
          )}
          {onDelete && (
            <button
              className="delete-instance-btn"
              onClick={(e) => {
                e.stopPropagation();
                if (isSelected) {
                  onDelete(instance);
                }
              }}
              title="Remove this instance"
              style={{ visibility: isSelected ? 'visible' : 'hidden' }}
            >
              🗑️
            </button>
          )}
          {/* The standalone ＋src promote button was removed — the
              flyout's "Add to source file" option (visible whenever
              a studio-only row has a pending edit) is the single
              save path. Nudge any axis to surface the flyout. */}
          {/* SRC badge — shown only for source-defined rows. Sits
              immediately left of the sync-status dot so the user can
              read both the persistence-location and the
              edited/saved status as a single visual block. Studio-only
              rows get no badge (absence == studio). */}
          {isDefaultOrigin && (
            <span
              className="origin-badge origin-badge-default"
              title={`This instance's mapping inputs all sit at the axis defaults, so its parametric values define the font's RESTING state: the build interpolates a real master here and compiles with it as the origin (avar2 cannot remap the default location). Move an axis default, or this row's inputs, to hand the origin to another instance.${originMasterSummary(originMaster)}`}
              aria-label="default origin instance"
            >
              ORIGIN
            </span>
          )}
          {!isStudioOnly && (
            <span
              className="origin-badge origin-badge-source"
              title="Source-defined instance — this row is declared in the .glyphs / .designspace source file. Saving from the flyout's 'Save to source file' option writes any edited coordinates back into that file. Removing the SRC badge means the row exists only in the studio."
              aria-label="source-defined instance"
            >
              SRC
            </span>
          )}
          {gradeEnabled && (
            <GradeBadge
              instanceName={instance.name}
              pct={gradePct}
              maxPct={gradeMaxPct}
              defaultPct={gradeDefaultPct}
              diagnostics={gradeDiagnostics}
              onSave={onSaveInstanceGrade}
              onRemove={onRemoveInstanceGrade}
            />
          )}
          {roundingEnabled && (
            <RoundingBadge
              instanceName={instance.name}
              pct={roundingPct}
              defaultPct={roundingDefaultPct}
              onSave={onSaveInstanceRounding}
              onRemove={onRemoveInstanceRounding}
            />
          )}
          {slantEnabled && !slantExempt && (
            <SlantBadge
              instanceName={instance.name}
              pct={slantPct}
              defaultPct={slantDefaultPct}
              onSave={onSaveInstanceSlant}
              onRemove={onRemoveInstanceSlant}
            />
          )}
          {/* Absence means "not spaced" — there is no add affordance here. */}
          {kinetikernEnabled && kinetikernLooseness != null && (
            <KinetikernBadge
              instanceName={instance.name}
              looseness={kinetikernLooseness}
              onOpen={onOpenKinetikern}
              onRemove={onRemoveInstanceKinetikern}
            />
          )}
          {/* Coordinates moved below the sample text — see render-end. */}
          <div
            className="sync-status-dot-wrapper"
            onClick={(e) => {
              e.stopPropagation();
              e.preventDefault();
              e.nativeEvent?.stopImmediatePropagation();
              // Flyout opens from any state. Even green is actionable
              // for source-defined rows (the demote option lives there).
              if (onUpdateInstanceStudio) {
                setShowFlyout(true);
              }
            }}
            onMouseDown={(e) => {
              // Prevent row selection when clicking dot
              e.stopPropagation();
              e.preventDefault();
            }}
            onMouseUp={(e) => {
              // Also prevent on mouseup
              e.stopPropagation();
              e.preventDefault();
            }}
            style={{
              cursor: onUpdateInstanceStudio ? 'pointer' : 'default',
              display: 'inline-flex',
              alignItems: 'center',
              position: 'relative',
              zIndex: 10,
              userSelect: 'none'
            }}
          >
            <span
              ref={dotRef}
              className={`sync-status-dot sync-status-${syncStatus}`}
              title={
                syncStatus === 'green' ? 'Saved in the source file — click to demote (remove from source, keep in studio)' :
                syncStatus === 'orange' ? 'Saved in the studio — not in the source file. Click to push to source.' :
                syncStatus === 'red' ? 'Unsaved edits — saving shortly; click to choose where to save' :
                'Unknown status'
              }
              style={{
                pointerEvents: 'auto'
              }}
            ></span>
            {showFlyout && onUpdateInstanceStudio && dotRef.current && rowRef.current && (() => {
              // Calculate position relative to instance row bounds
              const dotRect = dotRef.current.getBoundingClientRect();
              const rowRect = rowRef.current.getBoundingClientRect();
              const wrapperRect = dotRef.current.closest('.sync-status-dot-wrapper')?.getBoundingClientRect();
              
              if (!wrapperRect) return null;
              
              const flyoutWidth = 150; // Approximate flyout width
              const flyoutHeight = 40; // Approximate flyout height
              const spacing = 4;
              
              // Calculate positions relative to instance row
              const wrapperTopInRow = wrapperRect.top - rowRect.top;
              const wrapperRightInRow = wrapperRect.right - rowRect.left;
              const dotHeight = dotRect.height;
              
              // Calculate available space within instance row
              const rowHeight = rowRect.height;
              
              // Calculate where flyout would be positioned relative to wrapper
              // Use RIGHT positioning - align flyout's right edge with wrapper's right edge (where dot is)
              // Default: below the dot, right edge aligned (right: 0px)
              let flyoutRightInWrapper = 0; // Align right edge with wrapper's right edge (where dot is)
              let flyoutTopInWrapper = dotHeight + spacing;
              
              // Calculate where flyout would be relative to row using right positioning
              // With right: 0, flyout's right edge aligns with wrapper's right edge
              const flyoutRightEdgeInRow = wrapperRightInRow; // Right edge of flyout in row (aligned with wrapper)
              const flyoutLeftEdgeInRow = flyoutRightEdgeInRow - flyoutWidth; // Left edge of flyout in row
              let flyoutTopInRow = wrapperTopInRow + flyoutTopInWrapper;
              let flyoutBottomInRow = flyoutTopInRow + flyoutHeight;
              
              // Check if flyout would overflow left edge of row
              // If so, we need to move the right edge inward (increase flyoutRightInWrapper)
              if (flyoutLeftEdgeInRow < 0) {
                // Would overflow left edge, move right edge inward
                // Calculate how much we need to move: the overflow amount
                const overflow = -flyoutLeftEdgeInRow;
                flyoutRightInWrapper = overflow + spacing; // Move right edge inward by overflow amount
              }
              
              // Adjust vertical position to stay within row bounds
              flyoutTopInRow = wrapperTopInRow + flyoutTopInWrapper;
              flyoutBottomInRow = flyoutTopInRow + flyoutHeight;
              
              if (flyoutBottomInRow > rowHeight) {
                // Would overflow bottom edge
                const spaceAbove = wrapperTopInRow;
                if (spaceAbove >= flyoutHeight) {
                  // Enough space above, position above wrapper
                  flyoutTopInWrapper = -flyoutHeight - spacing;
                } else {
                  // Not enough space, align to bottom edge of row
                  flyoutTopInWrapper = rowHeight - wrapperTopInRow - flyoutHeight - spacing;
                }
              }
              
              const minTop = -wrapperTopInRow; // Can't go past top edge of row
              const maxTop = rowHeight - wrapperTopInRow; // Can't go past bottom edge of row
              flyoutTopInWrapper = Math.max(minTop, Math.min(maxTop, flyoutTopInWrapper));
              
              const positionObj = {
                top: flyoutTopInWrapper,
                right: flyoutRightInWrapper
              };
              
              return (
                <InstanceFlyout
                  isOpen={showFlyout}
                  onClose={() => setShowFlyout(false)}
                  onUpdateStudio={onUpdateInstanceStudio}
                  onUpdateSource={onUpdateInstanceSource}
                  onDemoteFromSource={onDemoteFromSource}
                  instanceOrigin={instance.origin || 'source'}
                  syncStatus={syncStatus}
                  position={positionObj}
                />
              );
            })()}
          </div>
        </div>
      </div>
      
      {isSelected && showMoveControls && onMove && (
        <div className="move-controls" onClick={(e) => e.stopPropagation()}>
          <div className="move-controls-row">
            <select
              className="move-position-select"
              value={movePosition}
              onChange={(e) => setMovePosition(e.target.value)}
            >
              <option value="before">Before</option>
              <option value="after">After</option>
            </select>
            <select
              className="move-target-select"
              value={targetInstance?.name || ''}
              onChange={(e) => {
                const selected = allInstances.find(inst => inst.name === e.target.value);
                setTargetInstance(selected || null);
              }}
            >
              <option value="">Select instance...</option>
              {allInstances
                .filter(inst => inst.name !== instance.name)
                .map(inst => (
                  <option key={inst.name} value={inst.name}>
                    {inst.name}
                  </option>
                ))}
            </select>
            <button
              className="move-apply-btn"
              onClick={(e) => {
                e.stopPropagation();
                if (targetInstance) {
                  onMove(instance, targetInstance, movePosition);
                  setShowMoveControls(false);
                  setTargetInstance(null);
                }
              }}
              disabled={!targetInstance}
            >
              Apply
            </button>
            <button
              className="move-cancel-btn"
              onClick={(e) => {
                e.stopPropagation();
                setShowMoveControls(false);
                setTargetInstance(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      
      <div className="instance-row-content">
        <div
          className="preview-text"
          style={{
            fontFamily: fontLoaded && vfFamilyId ? `"${vfFamilyId}", sans-serif` : 'sans-serif',
            fontVariationSettings: fontLoaded ? fontVariationSettings : undefined,
            fontFeatureSettings: 'normal',
            // Belt to the pinning above: never let the browser steer
            // opsz from the font size or synthesize weight/slant.
            fontOpticalSizing: 'none',
            fontSynthesis: 'none',
            fontWeight: 'normal',
            fontSize: `${fontSize}rem`,
          }}
        >
          {sampleText}
        </div>
        <div className="instance-coordinates">
          {Object.entries(activeCoordinates)
            .filter(([tag]) => {
              if (!parametricTags && !transformTags) return true;
              return parametricTags?.has(tag) || transformTags?.has(tag) || tag === gradeTag;
            })
            .map(([tag, value]) => (
              <span key={tag} className="coordinate">
                {tag}: {formatAxisValue(value)}
              </span>
            ))}
        </div>
      </div>
    </div>
  );
}

export default InstanceRow;
