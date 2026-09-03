import React from 'react';
import './GradeDiagnostics.css';

/**
 * Grade warnings/errors, as reported by the server in
 * GET /api/transforms/grade → `diagnostics`.
 *
 * Each entry is {level, code, instance, message, detail}. The failure modes
 * this surfaces are invisible in the preview until the slider is dragged to
 * an extreme — an instance pinned on the XTRA floor has no counters left to
 * open, so every stem unit the grade adds bleeds straight into them — which
 * is exactly why they are worth stating in words at the point of authoring.
 *
 * `scope`:
 *   "axis"     — only entries with no instance (does the GRAD axis exist?)
 *   <name>     — only entries for that instance
 */
function GradeDiagnostics({ diagnostics, scope = 'axis', compact = false }) {
  if (!Array.isArray(diagnostics) || diagnostics.length === 0) return null;
  const rows = diagnostics.filter(d =>
    scope === 'axis' ? !d.instance : d.instance === scope
  );
  if (rows.length === 0) return null;

  const icon = { error: '!', warning: '!', info: 'i' };
  return (
    <ul className={`grade-diags${compact ? ' grade-diags-compact' : ''}`}>
      {rows.map((d, i) => (
        <li key={d.code ? `${d.code}-${i}` : i} className={`grade-diag grade-diag-${d.level}`}>
          <span className="grade-diag-icon" aria-hidden="true">{icon[d.level] || 'i'}</span>
          <span className="grade-diag-body">
            <span className="grade-diag-message">{d.message}</span>
            {d.detail && !compact && <span className="grade-diag-detail">{d.detail}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default GradeDiagnostics;
