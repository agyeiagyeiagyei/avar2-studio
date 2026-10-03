import React, { useState, useRef, useEffect } from 'react';
import './GradeBadge.css';

/**
 * Per-instance rounding control — the grade badge's sibling, same chip
 * convention on the row:
 *
 *   - Own percent set → "R 40%" badge (active). Click to edit.
 *   - Following the broad default → "+ Round" badge (muted). Click to
 *     give this style its own percent.
 *
 * A style's percent is just its ROND coordinate, so Save is a fast
 * rebuild (no re-rounding of the source). Remove returns the style to
 * the default percent from the Transforms menu.
 */
function RoundingBadge({ instanceName, pct = null, defaultPct = 0, onSave, onRemove }) {
  const [open, setOpen] = useState(false);
  const set = pct != null;
  const [draft, setDraft] = useState(Math.round(set ? pct : defaultPct));
  const ref = useRef(null);

  useEffect(() => {
    if (open) setDraft(Math.round(pct ?? defaultPct));
  }, [open, pct, defaultPct]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const save = () => {
    const v = Math.max(0, Math.min(100, Math.round(Number(draft) || 0)));
    onSave(instanceName, v);
    setOpen(false);
  };

  return (
    <span className="grade-badge-wrap" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className={`grade-badge ${set ? 'grade-badge-on' : 'grade-badge-off'}`}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        title={set
          ? `Rounding ${Math.round(pct)}%. Click to edit.`
          : `Follows the default rounding (${Math.round(defaultPct)}%) — click to set this style's own`}
      >
        {set ? `R ${Math.round(pct)}%` : '+ Round'}
      </button>
      {open && (
        <div className="grade-popover" onClick={(e) => e.stopPropagation()}>
          <div className="grade-popover-field">
            <span className="grade-popover-label">Rounding</span>
            <input
              type="number"
              className="grade-popover-input"
              value={draft}
              min={0}
              max={100}
              step={1}
              autoFocus
              onChange={(e) => setDraft(e.target.value === '' ? '' : parseFloat(e.target.value))}
              onKeyDown={(e) => { if (e.key === 'Enter') save(); }}
            />
            <span className="grade-popover-unit">%</span>
          </div>
          <div className="grade-popover-hint">
            100% = the dialed radii; 0% = as drawn
          </div>
          <div className="grade-popover-actions">
            {set && (
              <button type="button" className="grade-popover-remove" onClick={() => { onRemove(instanceName); setOpen(false); }}>
                Remove
              </button>
            )}
            <button type="button" className="grade-popover-save" onClick={save}>
              Save
            </button>
          </div>
        </div>
      )}
    </span>
  );
}

export default RoundingBadge;
