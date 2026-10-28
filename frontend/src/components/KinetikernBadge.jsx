import React, { useState, useRef, useEffect } from 'react';
import './KinetikernBadge.css';
import { badgeLabel } from '../kinetikern-copy';

/**
 * Per-row Kinetikern marker, in the Grade badge's pattern (B7 K1).
 *
 *   - Spaced style → "K −0.60" badge. Click opens the two things a designer
 *     wants from the row: jump to the Kinetikern tab with this style
 *     selected, or Remove (the sidecar entry, one rebuild).
 *   - Unspaced style → nothing at all. Spacing is authored in the tab (it
 *     needs a looseness and a proposal), so a row must not offer to add one.
 *
 * Only rendered while the family switch is on: with Kinetikern off the build
 * ignores every entry, so a badge would claim spacing the font does not have.
 */
function KinetikernBadge({ instanceName, looseness, onOpen, onRemove }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const lo = Number(looseness) || 0;

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span className="kinetikern-badge-wrap" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className="kinetikern-badge"
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        title={`Kinetikern: spaced at ${lo.toFixed(2)}. Click for the Kinetikern tab or to remove it.`}
      >
        {badgeLabel(lo)}
      </button>
      {open && (
        <div className="kinetikern-popover" onClick={(e) => e.stopPropagation()}>
          <div className="kinetikern-popover-hint">
            Spaced by Kinetikern at {lo.toFixed(2)} — deltas live in the
            Kinetikern sidecar, not in this row.
          </div>
          <div className="kinetikern-popover-actions">
            <button
              type="button"
              className="kinetikern-popover-open"
              onClick={() => { setOpen(false); onOpen && onOpen(instanceName); }}
            >
              Open in Kinetikern
            </button>
            <button
              type="button"
              className="kinetikern-popover-remove"
              onClick={() => { setOpen(false); onRemove && onRemove(instanceName); }}
            >
              Remove
            </button>
          </div>
        </div>
      )}
    </span>
  );
}

export default KinetikernBadge;
