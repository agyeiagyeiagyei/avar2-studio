import React, { useMemo } from 'react';
import './GradeComparison.css';

/**
 * Sidebar panel for grade-master calibration.
 *
 * Designing a grade master means matching advance widths per glyph
 * between a base instance and a candidate at a heavier (or lighter)
 * parametric configuration. The summed Width pill in the instance
 * rows isn't enough — two configurations can match on the whole
 * sample text and still differ per-glyph, which causes reflow.
 *
 * This panel shows per-glyph advance deltas between a pinned base
 * instance and the currently-selected/edited candidate, so the
 * designer can see exactly which glyphs need per-side sidebearing
 * fixes at the master level.
 */
function GradeComparison({
  baseSnapshot,             // { name, coordinates } | null
  basePerGlyph,             // [{ text, glyph_name, advance_font_units }] | null
  candidateName,            // string | null
  candidatePerGlyph,        // same shape as basePerGlyph | null
  onPinBase,
  onUnpinBase,
}) {
  const deltas = useMemo(() => {
    if (!baseSnapshot || !basePerGlyph || !candidatePerGlyph) return null;
    // The two arrays should align position-by-position because they're
    // shaped against the same sample text. If a different text reaches
    // each side (race condition) we fall back to the shorter length so
    // the table is at least consistent.
    const len = Math.min(basePerGlyph.length, candidatePerGlyph.length);
    const rows = [];
    for (let i = 0; i < len; i++) {
      const b = basePerGlyph[i];
      const c = candidatePerGlyph[i];
      rows.push({
        text: c.text || b.text,
        glyph_name: c.glyph_name || b.glyph_name,
        base: b.advance_font_units,
        candidate: c.advance_font_units,
        delta: c.advance_font_units - b.advance_font_units,
      });
    }
    return rows;
  }, [baseSnapshot, basePerGlyph, candidatePerGlyph]);

  const stats = useMemo(() => {
    if (!deltas || deltas.length === 0) return null;
    const baseSum = deltas.reduce((s, r) => s + r.base, 0);
    const candSum = deltas.reduce((s, r) => s + r.candidate, 0);
    const totalDelta = candSum - baseSum;
    const absDeltas = deltas.map(r => Math.abs(r.delta));
    return {
      baseSum,
      candSum,
      totalDelta,
      maxAbs: Math.max(...absDeltas),
      meanAbs: absDeltas.reduce((s, v) => s + v, 0) / absDeltas.length,
    };
  }, [deltas]);

  const formatDelta = (n) => {
    const rounded = Math.round(n);
    if (rounded === 0) return '0';
    return rounded > 0 ? `+${rounded}` : `${rounded}`;
  };

  return (
    <div className="grade-comparison">
      <h3>Grade comparison</h3>

      {!baseSnapshot && (
        <div className="grade-empty">
          <p>
            Pin an instance as the base, then select another to see
            per-glyph advance deltas. Useful for calibrating grade
            masters where every glyph needs to match advance width.
          </p>
          <button
            className="btn btn-pin-base"
            onClick={onPinBase}
            disabled={!candidateName}
            title={!candidateName ? 'Select an instance first' : `Pin "${candidateName}"`}
          >
            {candidateName ? `Pin "${candidateName}" as base` : 'Select an instance to pin'}
          </button>
        </div>
      )}

      {baseSnapshot && (
        <>
          <div className="grade-meta">
            <div className="grade-meta-row">
              <span className="grade-meta-label">Base</span>
              <span className="grade-meta-name">{baseSnapshot.name}</span>
              <button
                className="grade-unpin-button"
                onClick={onUnpinBase}
                title="Unpin base"
                aria-label="Unpin base"
              >
                ×
              </button>
            </div>
            <div className="grade-meta-row">
              <span className="grade-meta-label">Cand.</span>
              <span className="grade-meta-name">
                {candidateName || <em className="grade-empty-inline">(no selection)</em>}
              </span>
            </div>
          </div>

          {!candidateName && (
            <div className="grade-hint">
              Select an instance to compare against the base.
            </div>
          )}

          {candidateName && !deltas && (
            <div className="grade-loading">Computing…</div>
          )}

          {deltas && stats && (
            <>
              <table className="grade-table">
                <thead>
                  <tr>
                    <th>Glyph</th>
                    <th className="num">Base</th>
                    <th className="num">Cand.</th>
                    <th className="num">Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {deltas.map((row, i) => {
                    const rounded = Math.round(row.delta);
                    const cls =
                      rounded > 0 ? 'pos' : rounded < 0 ? 'neg' : 'zero';
                    const warn = Math.abs(rounded) >= 5;
                    return (
                      <tr key={i} className={warn ? 'grade-delta-warn' : ''}>
                        <td className="glyph-cell" title={row.glyph_name}>
                          {row.text === ' ' ? '␣' : (row.text || row.glyph_name)}
                        </td>
                        <td className="num">{Math.round(row.base)}</td>
                        <td className="num">{Math.round(row.candidate)}</td>
                        <td className={`num grade-delta ${cls}`}>
                          {formatDelta(row.delta)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              <div className="grade-stats">
                <div className="grade-stat">
                  <span className="grade-stat-label">Total Δ</span>
                  <span
                    className={`grade-stat-value num ${
                      stats.totalDelta > 0 ? 'pos' : stats.totalDelta < 0 ? 'neg' : ''
                    }`}
                  >
                    {formatDelta(stats.totalDelta)}
                  </span>
                </div>
                <div className="grade-stat">
                  <span className="grade-stat-label">Max |Δ|</span>
                  <span className="grade-stat-value num">{Math.round(stats.maxAbs)}</span>
                </div>
                <div className="grade-stat">
                  <span className="grade-stat-label">Mean |Δ|</span>
                  <span className="grade-stat-value num">{stats.meanAbs.toFixed(1)}</span>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

export default GradeComparison;
