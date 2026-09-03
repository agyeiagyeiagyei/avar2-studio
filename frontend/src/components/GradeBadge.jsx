import React, { useState, useRef, useEffect } from 'react';
import './GradeBadge.css';
import GradeDiagnostics from './GradeDiagnostics';

/**
 * Per-instance grade control, anchored to a clickable badge on the row.
 *
 *   - Graded style   → "G 25%" badge (active). Click to edit.
 *   - Ungraded style → "+ Grade" badge (muted). Click to add.
 *
 * Editing is LOCAL until Save — nothing is persisted or rebuilt while you
 * type. Save commits (one rebuild); Remove clears the grade. The grade%
 * lives here, not in the row's save/demote flyout.
 */
function GradeBadge({ instanceName, pct = null, maxPct, defaultPct = 0.25, diagnostics, onSave, onRemove }) {
  const [open, setOpen] = useState(false);
  const graded = pct != null;
  // A cap of 0 is a REAL answer — this style has no counter headroom, so no
  // grade is deliverable here. Testing `maxPct ?` treated that as "unknown"
  // and silently offered the full 1–100 range instead.
  const hasCap = maxPct != null && Number.isFinite(maxPct);
  const capPercent = hasCap ? Math.floor(maxPct * 100) : null;
  const noHeadroom = capPercent === 0;
  // Only bound the input by a cap that leaves something to author. At zero we
  // let the value through and warn instead, because the grade still lands if
  // "limit grade to counter headroom" is switched off.
  const maxPercent = hasCap && capPercent > 0 ? capPercent : 100;
  // Worst diagnostic for THIS style, so the badge itself can show that
  // something is wrong — a grade that silently delivers nothing would
  // otherwise look identical to one that works.
  const mine = (Array.isArray(diagnostics) ? diagnostics : [])
    .filter(d => d.instance === instanceName);
  const flag = mine.some(d => d.level === 'error') ? 'error'
    : mine.some(d => d.level === 'warning') ? 'warning' : null;
  const [draft, setDraft] = useState(Math.round((pct ?? defaultPct) * 100));
  const ref = useRef(null);

  // Reset the draft to the saved value whenever the popover (re)opens.
  useEffect(() => {
    if (open) setDraft(Math.round((pct ?? defaultPct) * 100));
  }, [open, pct, defaultPct]);

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

  const save = () => {
    const v = Math.max(0, Math.min(maxPercent, Math.round(Number(draft) || 0)));
    onSave(instanceName, v / 100);
    setOpen(false);
  };

  return (
    <span className="grade-badge-wrap" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className={`grade-badge ${graded ? 'grade-badge-on' : 'grade-badge-off'}${flag ? ` grade-badge-${flag}` : ''}`}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        title={flag ? mine[0].message
          : graded ? `Graded — ${Math.round(pct * 100)}%. Click to edit.`
          : 'Add a grade to this style'}
      >
        {graded ? `G ${Math.round(pct * 100)}%` : '+ Grade'}
        {flag && <span className="grade-badge-flag" aria-hidden="true">!</span>}
      </button>
      {open && (
        <div className="grade-popover" onClick={(e) => e.stopPropagation()}>
          <div className="grade-popover-field">
            <span className="grade-popover-label">Grade</span>
            <input
              type="number"
              className="grade-popover-input"
              value={draft}
              min={0}
              max={maxPercent}
              step={1}
              autoFocus
              onChange={(e) => setDraft(e.target.value === '' ? '' : parseFloat(e.target.value))}
              onKeyDown={(e) => { if (e.key === 'Enter') save(); }}
            />
            <span className="grade-popover-unit">%</span>
          </div>
          <div className="grade-popover-hint">
            {noHeadroom
              ? 'no counter headroom at this style'
              : hasCap ? `max ${maxPercent}% here` : 'higher = darker'}
          </div>
          <GradeDiagnostics diagnostics={diagnostics} scope={instanceName} compact />
          <div className="grade-popover-actions">
            {graded && (
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

export default GradeBadge;
