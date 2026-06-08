import React, { useEffect, useRef } from 'react';
import './InstanceFlyout.css';

/**
 * Flyout offering two update paths for an instance:
 *
 *   - Update in avar2-studio — persists the row to the sibling
 *     ``-avar.csv`` only. Source files are untouched. Works for both
 *     studio-only and source-defined instances; for source instances
 *     it's a "tweak the avar2 mapping without changing the source"
 *     escape hatch.
 *   - Update source file / Add to source — for a source instance this
 *     writes back to .glyphs / .designspace (the historical Update
 *     Instance behavior). For a studio-only instance this promotes the
 *     row into the source file's instance list.
 */
function InstanceFlyout({ isOpen, onClose, onUpdateStudio, onUpdateSource, instanceOrigin = 'source', position }) {
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

  const sourceLabel = instanceOrigin === 'studio'
    ? 'Save to source file (promote)'
    : 'Save to source file';
  const sourceTitle = instanceOrigin === 'studio'
    ? 'Promote this studio-only instance into the source file (.glyphs / .designspace). The row gains the SRC badge afterwards.'
    : 'Write the edited coordinates back to the source file (.glyphs / .designspace). The SRC instance is permanently modified.';

  return (
    <div
      className="instance-flyout"
      ref={flyoutRef}
      style={style}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        className="flyout-item"
        onClick={() => {
          onUpdateStudio();
          onClose();
        }}
        title="Persist these coordinates to the sibling -avar.csv (the avar2 mapping file) only. Your .glyphs / .designspace source file stays untouched."
      >
        Save to avar2-studio (CSV)
      </button>
      {onUpdateSource && (
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
    </div>
  );
}

export default InstanceFlyout;
