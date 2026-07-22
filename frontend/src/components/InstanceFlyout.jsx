import React, { useEffect, useRef } from 'react';
import './InstanceFlyout.css';

/**
 * Three-option save / demote flyout. Which buttons render depends on
 * the row's tri-state sync status:
 *
 *   - Save to avar2-studio (CSV) — persists local edits to the sibling
 *     ``-avar.csv`` only. Source files are untouched. Shown whenever
 *     there's something to save (red state) — hidden in orange/green
 *     where there are no pending edits.
 *   - Save to source file — for a source instance this writes back to
 *     .glyphs / .designspace. For a studio-only instance this promotes
 *     the row into the source file's instance list. Shown in red and
 *     orange. Hidden in green (the row already matches the source).
 *   - Remove from source file — DEMOTES a source row to studio-only:
 *     deletes the source declaration, KEEPS the CSV row so the avar2
 *     mapping is preserved. Shown only for source rows (the operation
 *     is meaningless on a row that isn't in source).
 */
function InstanceFlyout({ isOpen, onClose, onUpdateStudio, onUpdateSource, onDemoteFromSource, instanceOrigin = 'source', syncStatus = 'green', position }) {
  const flyoutRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (flyoutRef.current && !flyoutRef.current.contains(event.target)) {
        onClose();
      }
    };
    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEscape);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const style = position
    ? {
        position: 'absolute',
        top: `${position.top}px`,
        ...(position.right !== undefined && position.right !== null
          ? { right: `${position.right}px`, left: 'auto' }
          : { left: `${position.left || 0}px`, right: 'auto' }),
        zIndex: 1000,
      }
    : {};

  const isStudioOnly = instanceOrigin === 'studio';
  const sourceLabel = isStudioOnly ? 'Save to source file (promote)' : 'Save to source file';
  const sourceTitle = isStudioOnly
    ? 'Promote this studio-only instance into the source file (.glyphs / .designspace). The row gains the SRC badge afterwards.'
    : 'Write the edited coordinates back to the source file (.glyphs / .designspace). The SRC instance is permanently modified.';

  // Only show "Save to avar2-studio" when there's something to save —
  // i.e. local edits are pending (red state). Hiding it in green/orange
  // keeps the menu small once everything is persisted.
  const showSaveToStudio = syncStatus === 'red' && typeof onUpdateStudio === 'function';

  // "Save to source" is the orange→green push. Show in red (user might
  // jump straight to source) and orange (CSV needs syncing up). Hide
  // in green (already in source).
  const showSaveToSource = syncStatus !== 'green' && typeof onUpdateSource === 'function';

  // Demote: only meaningful for rows that ARE in source — i.e. not
  // studio-only. Shown in any state for source rows.
  const showDemote = !isStudioOnly && typeof onDemoteFromSource === 'function';

  return (
    <div
      className="instance-flyout"
      ref={flyoutRef}
      style={style}
      onClick={(e) => e.stopPropagation()}
    >
      {showSaveToStudio && (
        <button
          className="flyout-item"
          onClick={() => {
            onUpdateStudio();
            onClose();
          }}
          title="Save these coordinates in the studio (the avar2 mapping data) only. Your .glyphs / .designspace source file stays untouched."
        >
          Save to avar2-studio (CSV)
        </button>
      )}
      {showSaveToSource && (
        <button
          className="flyout-item flyout-item-source"
          onClick={() => {
            onUpdateSource();
            onClose();
          }}
          title={sourceTitle}
        >
          {sourceLabel}
        </button>
      )}
      {showDemote && (
        <button
          className="flyout-item flyout-item-demote"
          onClick={() => {
            onDemoteFromSource();
            onClose();
          }}
          title="Remove this instance from the source file (.glyphs / .designspace). It stays in the studio with its avar2 mapping preserved; the row loses its SRC badge."
        >
          Remove from source file
        </button>
      )}
    </div>
  );
}

export default InstanceFlyout;
