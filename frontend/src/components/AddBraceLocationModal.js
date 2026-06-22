import React, { useEffect, useState } from 'react';
import './AddBraceLocationModal.css';

/**
 * Modal for pinning a brace layer at a custom multi-axis location
 * for one coverage glyph.
 *
 * Each axis has a pin-toggle + value input. Unpinned axes interpolate
 * from masters; the resulting brace layer carries a sparse
 * ``location`` dict containing only the pinned axes. The control axis
 * (whose row this belongs to) is required non-default.
 *
 * Props:
 *   isOpen           — render gate
 *   onClose          — () => void
 *   onCreate         — async ({glyph, location}) => void
 *   axisTag          — the control axis this layer is being added to
 *                      (its slider is highlighted + required non-default)
 *   axisDefault      — control axis default value, for the "required
 *                      non-default" check
 *   coverageGlyphs   — Array<string> — restrict the glyph picker to
 *                      glyphs already in the axis's coverage list
 *   allAxes          — Array<{tag, name, min, max, default}> — every
 *                      axis in the source/sidecar, used to render
 *                      pin inputs
 */
function AddBraceLocationModal({ isOpen, onClose, onCreate, axisTag, axisDefault, coverageGlyphs, allAxes, prefillGlyph }) {
  const [glyph, setGlyph] = useState('');
  const [pins, setPins] = useState({});
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    // prefillGlyph wins if the modal opened from a per-glyph "+ Add
    // mapping" button; else default to the first coverage glyph.
    setGlyph(prefillGlyph || (coverageGlyphs && coverageGlyphs[0]) || '');
    // Seed the control axis pin at axis-min as a useful starting
    // point; user adjusts from there. Other axes start un-pinned.
    const controlAxis = (allAxes || []).find(a => a.tag === axisTag);
    setPins(controlAxis ? { [axisTag]: controlAxis.min } : {});
    setError(null);
    setSubmitting(false);
  }, [isOpen, coverageGlyphs, axisTag, allAxes, prefillGlyph]);

  if (!isOpen) return null;

  const togglePin = (tag, axisInfo) => {
    setPins(prev => {
      const next = { ...prev };
      if (tag in next) {
        delete next[tag];
      } else {
        next[tag] = axisInfo?.default ?? 0;
      }
      return next;
    });
  };

  const setPinValue = (tag, value) => {
    setPins(prev => ({ ...prev, [tag]: parseFloat(value) }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    if (!glyph) {
      setError('Pick a glyph');
      return;
    }
    if (!(axisTag in pins)) {
      setError(`Pin ${axisTag} — the control axis must be non-default`);
      return;
    }
    if (Number(pins[axisTag]) === Number(axisDefault)) {
      setError(`Pin ${axisTag} to something other than its default (${axisDefault})`);
      return;
    }
    // Filter to numeric pins only; drop NaN.
    const cleanPins = {};
    for (const [tag, value] of Object.entries(pins)) {
      const n = Number(value);
      if (Number.isFinite(n)) cleanPins[tag] = n;
    }
    setSubmitting(true);
    try {
      await onCreate({ glyph, location: cleanPins });
      onClose();
    } catch (err) {
      setError(err.message || 'Failed to add layer');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="add-brace-location-modal" onClick={e => e.stopPropagation()}>
        <h3>Add brace layer location</h3>
        <p className="modal-help">
          Pin a brace layer for one glyph at a specific point in axis
          space. Unpinned axes interpolate from masters. The control
          axis (<code>{axisTag}</code>) must be pinned non-default.
        </p>
        <form onSubmit={handleSubmit}>
          <div className="form-row">
            <label htmlFor="brace-glyph">Glyph</label>
            <select
              id="brace-glyph"
              value={glyph}
              onChange={e => setGlyph(e.target.value)}
            >
              {(coverageGlyphs || []).map(g => (
                <option key={g} value={g}>{g}</option>
              ))}
            </select>
          </div>

          <div className="location-pins">
            <div className="location-pins-header">Axis pins</div>
            {(allAxes || []).map(axis => {
              const isPinned = axis.tag in pins;
              const isControlAxis = axis.tag === axisTag;
              return (
                <div
                  key={axis.tag}
                  className={`location-pin-row ${isPinned ? 'pinned' : ''} ${isControlAxis ? 'control-axis' : ''}`}
                >
                  <label className="pin-toggle">
                    <input
                      type="checkbox"
                      checked={isPinned}
                      onChange={() => togglePin(axis.tag, axis)}
                      disabled={isControlAxis}  /* always pinned for the control axis */
                      title={isControlAxis ? 'Required — pinning the control axis is what makes this a brace layer.' : ''}
                    />
                    <span className="pin-tag">{axis.tag}</span>
                  </label>
                  <span className="pin-range">{axis.min} … {axis.max}</span>
                  <input
                    type="number"
                    className="pin-value"
                    disabled={!isPinned}
                    min={axis.min}
                    max={axis.max}
                    step={0.1}
                    value={isPinned ? pins[axis.tag] : axis.default}
                    onChange={e => setPinValue(axis.tag, e.target.value)}
                  />
                </div>
              );
            })}
          </div>

          <div className="location-preview">
            <strong>Layer:</strong> <code>{glyph} @ {`{${Object.entries(pins).map(([t, v]) => `${t}=${v}`).join(', ')}}`}</code>
          </div>

          {error && <div className="submit-error">{error}</div>}

          <div className="modal-buttons">
            <button type="button" className="btn btn-cancel" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn-confirm" disabled={submitting}>
              {submitting ? 'Adding…' : 'Add layer'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AddBraceLocationModal;
