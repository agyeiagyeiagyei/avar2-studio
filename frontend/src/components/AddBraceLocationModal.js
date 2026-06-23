import React, { useEffect, useState } from 'react';
import './AddBraceLocationModal.css';

/**
 * Modal for declaring brace layers at a multi-axis location for one
 * or more glyphs in a single submit.
 *
 * Glyph field accepts a Fontra-style string:
 *   - Single characters (one glyph each):  "AEFH" → ["A","E","F","H"]
 *   - Slash-named glyphs:                  "/idotless" → ["idotless"]
 *   - Mixed:                               "A/idotless/B" → ["A","idotless","B"]
 *   - Whitespace / comma separators:       "A E F H" or "A, idotless"
 *
 * On submit, one brace layer is created per parsed glyph at the
 * same location. The control axis (whose row this belongs to) is
 * required pinned non-default; other axes optional.
 *
 * Props:
 *   isOpen             — render gate
 *   onClose            — () => void
 *   onCreate           — async ([{glyph, location}, ...]) => void
 *   axisTag            — control axis tag
 *   axisDefault        — its default value (used for non-default check)
 *   allAxes            — every axis (for pin inputs)
 *   prefillGlyphs      — optional initial value for the glyph field
 *   lockGlyphs         — if true, the glyph field is read-only
 *                        (per-glyph "+ Add layer for X" sets this)
 */
function AddBraceLocationModal({ isOpen, onClose, onCreate, axisTag, axisDefault, allAxes, allInstances, prefillGlyphs, lockGlyphs, editLayer }) {
  const [glyphsInput, setGlyphsInput] = useState('');
  const [pins, setPins] = useState({});
  const [baseInstance, setBaseInstance] = useState('');  // instance name or '' (no baseline)
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    if (editLayer) {
      setGlyphsInput(editLayer.glyph || '');
      setPins({ ...(editLayer.location || {}) });
    } else {
      setGlyphsInput(prefillGlyphs || '');
      const controlAxis = (allAxes || []).find(a => a.tag === axisTag);
      setPins(controlAxis ? { [axisTag]: controlAxis.min } : {});
    }
    setBaseInstance('');
    setError(null);
    setSubmitting(false);
  }, [isOpen, prefillGlyphs, axisTag, allAxes, editLayer]);

  // Pick an instance as the baseline location. Fills in pins for
  // every parametric axis the instance declares; control axis stays
  // at whatever the designer set (axis-min by default). User-friendly
  // mental model: "give me a brace AT THE BOLD INSTANCE, with crbr=-100"
  // instead of "give me a brace at (180, 300, 200, -100)."
  const handlePickInstance = (instanceName) => {
    setBaseInstance(instanceName);
    if (!instanceName) return;
    const inst = (allInstances || []).find(i => i.name === instanceName);
    if (!inst || !inst.coordinates) return;
    setPins(prev => {
      const next = { ...prev };
      for (const [tag, value] of Object.entries(inst.coordinates)) {
        // Don't override the control axis from an instance — its
        // value comes from the designer's intent for this brace.
        if (tag === axisTag) continue;
        next[tag] = Number(value);
      }
      return next;
    });
  };

  if (!isOpen) return null;

  const parsedGlyphs = parseGlyphString(glyphsInput);

  const togglePin = (axisInfo) => {
    setPins(prev => {
      const next = { ...prev };
      if (axisInfo.tag in next) {
        delete next[axisInfo.tag];
      } else {
        next[axisInfo.tag] = axisInfo.default ?? 0;
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
    if (parsedGlyphs.length === 0) {
      setError('Enter at least one glyph');
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
    const cleanPins = {};
    for (const [t, v] of Object.entries(pins)) {
      const n = Number(v);
      if (Number.isFinite(n)) cleanPins[t] = n;
    }
    const entries = parsedGlyphs.map(g => ({ glyph: g, location: cleanPins }));
    setSubmitting(true);
    try {
      await onCreate(entries);
      onClose();
    } catch (err) {
      setError(err.message || (editLayer ? 'Failed to update layer' : 'Failed to add layers'));
    } finally {
      setSubmitting(false);
    }
  };

  const isEdit = !!editLayer;
  const effectiveLockGlyphs = !!lockGlyphs || isEdit;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="add-brace-location-modal" onClick={e => e.stopPropagation()}>
        <h3>{isEdit ? 'Edit brace layer location' : `Add brace layer${parsedGlyphs.length > 1 ? 's' : ''}`}</h3>
        <p className="modal-help">
          {isEdit
            ? 'Change the axis values for this brace layer. The outline data carries over — only the location changes.'
            : <>One brace layer per glyph, all at the same axis location.
              Type plain characters (<code>AEFH</code>) or
              slash-named glyphs (<code>/idotless</code>). Mix and match
              allowed. Whitespace / commas are optional separators.</>}
        </p>
        <form onSubmit={handleSubmit}>
          <div className="form-row">
            <label htmlFor="brace-glyphs">
              Glyph{parsedGlyphs.length > 1 ? 's' : ''}
              {parsedGlyphs.length > 0 && (
                <span className="glyphs-preview">
                  → {parsedGlyphs.join(', ')}
                </span>
              )}
            </label>
            <input
              id="brace-glyphs"
              type="text"
              value={glyphsInput}
              onChange={e => setGlyphsInput(e.target.value)}
              readOnly={effectiveLockGlyphs}
              autoComplete="off"
              spellCheck={false}
              placeholder="e.g. AEFH or /idotless or A E F H"
            />
          </div>

          {(allInstances || []).length > 0 && (
            <div className="form-row">
              <label htmlFor="base-instance">
                Base on existing instance
                <span className="form-row-hint">
                  optional · pre-fills parametric axes from an instance's coordinates
                </span>
              </label>
              <select
                id="base-instance"
                value={baseInstance}
                onChange={e => handlePickInstance(e.target.value)}
              >
                <option value="">— none (set axes manually) —</option>
                {(allInstances || []).map(inst => (
                  <option key={inst.name} value={inst.name}>
                    {inst.name}
                    {inst.origin === 'studio' ? ' · studio' : ''}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="location-pins">
            <div className="location-pins-header">Axis pins</div>
            {(allAxes || []).map(axis => {
              const isPinned = axis.tag in pins;
              const isControlAxis = axis.tag === axisTag;
              const currentValue = isPinned ? pins[axis.tag] : axis.default;
              const step = (axis.max - axis.min) / 1000;
              return (
                <div
                  key={axis.tag}
                  className={`location-pin-row ${isPinned ? 'pinned' : ''} ${isControlAxis ? 'control-axis' : ''}`}
                >
                  <label className="pin-toggle">
                    <input
                      type="checkbox"
                      checked={isPinned}
                      onChange={() => togglePin(axis)}
                      disabled={isControlAxis}
                      title={isControlAxis ? 'Required — pinning the control axis is what makes this a brace layer.' : ''}
                    />
                    <span className="pin-tag">{axis.tag}</span>
                  </label>
                  <div className="pin-slider-wrap">
                    <input
                      type="range"
                      className="pin-slider"
                      disabled={!isPinned}
                      min={axis.min}
                      max={axis.max}
                      step={step > 0 ? step : 0.1}
                      value={currentValue}
                      onChange={e => setPinValue(axis.tag, e.target.value)}
                    />
                    <div className="pin-slider-ticks">
                      <span>{axis.min}</span>
                      <span>{axis.max}</span>
                    </div>
                  </div>
                  <input
                    type="number"
                    className="pin-value"
                    disabled={!isPinned}
                    min={axis.min}
                    max={axis.max}
                    step={0.1}
                    value={currentValue}
                    onChange={e => setPinValue(axis.tag, e.target.value)}
                  />
                </div>
              );
            })}
          </div>

          <div className="location-preview">
            <strong>{isEdit ? 'New location:' : 'Will create:'}</strong>{' '}
            <code>
              {isEdit
                ? `${parsedGlyphs[0] || ''} @ {${Object.entries(pins).map(([t, v]) => `${t}=${v}`).join(', ')}}`
                : parsedGlyphs.length === 0
                  ? '(no glyphs)'
                  : `${parsedGlyphs.length} brace layer${parsedGlyphs.length === 1 ? '' : 's'} @ {${Object.entries(pins).map(([t, v]) => `${t}=${v}`).join(', ')}}`}
            </code>
          </div>

          {error && <div className="submit-error">{error}</div>}

          <div className="modal-buttons">
            <button type="button" className="btn btn-cancel" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn-confirm" disabled={submitting}>
              {submitting
                ? (isEdit ? 'Saving…' : 'Adding…')
                : isEdit
                  ? 'Save changes'
                  : `Add ${parsedGlyphs.length || ''} layer${parsedGlyphs.length === 1 ? '' : 's'}`.trim()}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * Parse a glyph-input string per Fontra convention.
 *
 *   "AEFH"          → ["A", "E", "F", "H"]
 *   "/idotless"     → ["idotless"]
 *   "A/idotless/B"  → ["A", "idotless", "B"]
 *   "A E F H"       → ["A", "E", "F", "H"]
 *   "A, E, F, H"    → ["A", "E", "F", "H"]
 *   "  "            → []
 *
 * Tokens with no leading "/" are treated as raw text — each
 * character is one glyph. Tokens starting with "/" are a named
 * glyph (the name continues until the next "/" or whitespace).
 */
function parseGlyphString(input) {
  if (!input) return [];
  const out = [];
  const seen = new Set();
  const push = (g) => {
    if (g && !seen.has(g)) {
      seen.add(g);
      out.push(g);
    }
  };

  // Split into tokens by whitespace / commas first.
  const tokens = input.split(/[\s,]+/).filter(Boolean);
  for (const token of tokens) {
    let i = 0;
    while (i < token.length) {
      if (token[i] === '/') {
        // Named glyph; consume until next '/' or end-of-token.
        let j = i + 1;
        while (j < token.length && token[j] !== '/') j++;
        const name = token.slice(i + 1, j);
        if (name) push(name);
        i = j;
      } else {
        // Single character glyph.
        push(token[i]);
        i++;
      }
    }
  }
  return out;
}

export default AddBraceLocationModal;
