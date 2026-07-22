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
 *   editLayer          — edit an existing layer in place (parent replaces it)
 *   duplicateFrom      — seed the location editor from an existing layer but
 *                        CREATE a new one (parent's onCreate appends). The
 *                        glyph stays editable so the same coordinates can be
 *                        duplicated onto other glyphs.
 */
function AddBraceLocationModal({ isOpen, onClose, onCreate, axisTag, axisDefault, allAxes, allMasters, prefillGlyphs, lockGlyphs, editLayer, duplicateFrom, vfFamilyId, fontLoaded, glyphChars = {} }) {
  const [glyphsInput, setGlyphsInput] = useState('');
  const [pins, setPins] = useState({});           // edit mode: the single location
  const [controlValue, setControlValue] = useState(0);  // add mode: the crbr value
  const [selectedCorners, setSelectedCorners] = useState(() => new Set()); // master names
  const [customOn, setCustomOn] = useState(false); // add a custom (non-corner) location
  const [customPins, setCustomPins] = useState({}); // parametric coords for the custom point
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // Parametric axes (everything except the control axis) — the corner
  // picker fixes these; the custom-location sliders edit them.
  const parametricAxes = (allAxes || []).filter(a => a.tag !== axisTag);

  useEffect(() => {
    if (!isOpen) return;
    const seedFrom = editLayer || duplicateFrom;
    if (seedFrom) {
      setGlyphsInput(seedFrom.glyph || '');
      // Edit/duplicate: prefill EVERY axis. The stored sidecar dict is
      // sparse (only non-default keys persist), but the brace layer
      // is a full N-D point — show all axes pinned with their
      // effective value (stored or axis-default) so the designer
      // can see/edit them all instead of seeing unrelated axes
      // greyed out.
      const fullPins = {};
      for (const ax of (allAxes || [])) {
        const stored = (seedFrom.location || {})[ax.tag];
        fullPins[ax.tag] = stored !== undefined ? Number(stored) : Number(ax.default);
      }
      setPins(fullPins);
    } else {
      setGlyphsInput(prefillGlyphs || '');
      const controlAxis = (allAxes || []).find(a => a.tag === axisTag);
      // Seed the control value to a NON-default value so the modal
      // opens valid (using axis.min breaks when min == default).
      setControlValue(controlAxis ? seedControlValue(controlAxis) : 0);
      setPins({});
    }
    setSelectedCorners(new Set());
    // Custom point starts at each parametric axis's default, then the
    // last custom location the designer added on this axis is layered on
    // top — so adding the same location for another glyph is a re-open,
    // not a re-type. Ticked on automatically when we have one to reuse.
    const cp = {};
    for (const a of (allAxes || [])) {
      if (a.tag !== axisTag) cp[a.tag] = Number(a.default);
    }
    const remembered = loadLastCustomLocation(axisTag);
    if (remembered) {
      for (const [t, v] of Object.entries(remembered)) {
        if (t in cp && Number.isFinite(Number(v))) cp[t] = Number(v);
      }
    }
    setCustomPins(cp);
    setCustomOn(!!remembered && !seedFrom);
    setError(null);
    setSubmitting(false);
  }, [isOpen, prefillGlyphs, axisTag, allAxes, editLayer, duplicateFrom]);

  // Clear any stale error the moment the designer changes an input.
  useEffect(() => {
    setError(null);
  }, [glyphsInput, controlValue, selectedCorners, customOn, customPins, pins]);

  const isEdit = !!editLayer;
  const isDuplicate = !!duplicateFrom;
  // Edit and duplicate both drive the single-location pin editor — they differ
  // only in what the parent does with the result (replace vs append), which it
  // decides from `editLayer`.
  const usePinEditor = isEdit || isDuplicate;

  // Add mode: toggle a master corner in/out of the selection.
  const toggleCorner = (name) => {
    setSelectedCorners(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };
  const setCustomPinValue = (tag, value) => {
    setCustomPins(prev => ({ ...prev, [tag]: round1(value) }));
  };

  // Add mode: the parametric locations to create a view at — each
  // ticked corner, plus the custom point if enabled. A brace layer is
  // a full point: this parametric location × the control-axis value.
  const buildLocations = () => {
    const locs = [];
    for (const m of (allMasters || [])) {
      if (!selectedCorners.has(m.name)) continue;
      const parametric = {};
      for (const [tag, v] of Object.entries(m.coordinates || {})) {
        if (tag === axisTag) continue;
        parametric[tag] = Number(v);
      }
      locs.push(parametric);
    }
    if (customOn) {
      const parametric = {};
      for (const [tag, v] of Object.entries(customPins)) {
        const n = Number(v);
        if (Number.isFinite(n)) parametric[tag] = n;
      }
      locs.push(parametric);
    }
    return locs;
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
    setPins(prev => ({ ...prev, [tag]: round1(value) }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    if (parsedGlyphs.length === 0) {
      setError('Enter at least one glyph');
      return;
    }

    if (usePinEditor) {
      // Edit / duplicate: one explicit location from the pin editor.
      if (!(axisTag in pins) || Number(pins[axisTag]) === Number(axisDefault)) {
        setError(`Set ${axisTag} to a non-default value (not ${axisDefault})`);
        return;
      }
      const loc = {};
      for (const [t, v] of Object.entries(pins)) {
        const n = Number(v);
        if (Number.isFinite(n)) loc[t] = n;
      }
      const entries = parsedGlyphs.map(g => ({ glyph: g, location: loc }));
      setSubmitting(true);
      try {
        await onCreate(entries);
        // A duplicate is a hand-placed location too — remember it so the
        // next add opens there.
        if (isDuplicate) {
          const parametric = {};
          for (const [t, v] of Object.entries(loc)) if (t !== axisTag) parametric[t] = v;
          saveLastCustomLocation(axisTag, parametric);
        }
        onClose();
      }
      catch (err) { setError(err.message || (isDuplicate ? 'Failed to duplicate layer' : 'Failed to update layer')); }
      finally { setSubmitting(false); }
      return;
    }

    // Add mode: control value must be non-default, and at least one
    // parametric location (corner or custom) is required.
    if (Number(controlValue) === Number(axisDefault)) {
      setError(`Set ${axisTag} to a non-default value (not ${axisDefault})`);
      return;
    }
    const locations = buildLocations();
    if (locations.length === 0) {
      setError('Pick at least one master corner (or add a custom location)');
      return;
    }
    // One brace layer per glyph × location: the parametric point + the
    // control-axis value. Stored as a full location so it lands exactly
    // at the chosen corner.
    const entries = [];
    for (const g of parsedGlyphs) {
      for (const parametric of locations) {
        entries.push({ glyph: g, location: { ...parametric, [axisTag]: Number(controlValue) } });
      }
    }
    setSubmitting(true);
    try {
      await onCreate(entries);
      // Remember a hand-placed custom location so the next open starts
      // there — that's what makes duplicating it onto another glyph a
      // two-click job instead of re-dialling every axis.
      if (customOn) saveLastCustomLocation(axisTag, customPins);
      onClose();
    }
    catch (err) { setError(err.message || 'Failed to add layers'); }
    finally { setSubmitting(false); }
  };

  // Duplicate keeps the glyph field open on purpose: the whole point is to
  // reuse one location across glyphs.
  const effectiveLockGlyphs = !!lockGlyphs || isEdit;
  // Three modes, three titles. Edit: editing one existing layer's
  // location. Per-glyph add (glyph locked): adding another extreme
  // for that one glyph. Top-level add (glyph field open): widening
  // which glyphs this axis is applicable to.
  const title = isEdit
    ? 'Edit layer location'
    : isDuplicate
      ? `Duplicate layer from ${duplicateFrom.glyph}`
      : effectiveLockGlyphs
        ? `Add layer for ${prefillGlyphs || ''}`.trim()
        : `Add applicable glyphs to ${axisTag}`;

  return (
    <div className="modal-overlay">
      <div className="add-brace-location-modal">
        <h3>{title}</h3>
        <p className="modal-help">
          {isEdit
            ? 'Change the axis values for this layer. The outline data carries over — only the location changes.'
            : isDuplicate
              ? <>Starts at the same coordinates — change what you need, then save as a NEW layer. Edit the glyph field to duplicate this location onto other glyphs.</>
            : effectiveLockGlyphs
              ? <>Add {axisTag} views for <code>{prefillGlyphs}</code> — one editable layer per master corner you tick.</>
              : <>Each view is one editable layer: a master corner × the {axisTag} value.
                Set the {axisTag} value, tick the corners to define it at, then edit
                each outline in Fontra. Glyphs: plain characters (<code>AEFH</code>) or
                slash-named (<code>/idotless</code>).</>}
        </p>
        <form onSubmit={handleSubmit}>
          {/* Glyph field only when it's editable — in the locked modes
              (per-glyph add, edit) the title already names the glyph. */}
          {!effectiveLockGlyphs && (
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
                autoComplete="off"
                spellCheck={false}
                placeholder="e.g. AEFH or /idotless or A E F H"
              />
            </div>
          )}

          {usePinEditor ? (
            /* Edit / duplicate: this one layer's exact axis values. */
            <div className="location-pins">
              <div className="location-pins-header">Axis values</div>
              {(allAxes || []).map(axis => {
                const isPinned = axis.tag in pins;
                const isControlAxis = axis.tag === axisTag;
                const currentValue = isPinned ? pins[axis.tag] : axis.default;
                return (
                  <div key={axis.tag} className={`location-pin-row ${isPinned ? 'pinned' : ''} ${isControlAxis ? 'control-axis' : ''}`}>
                    <label className="pin-toggle">
                      <input type="checkbox" checked={isPinned} onChange={() => togglePin(axis)} disabled={isControlAxis} />
                      <span className="pin-tag">{axis.tag}</span>
                    </label>
                    <div className="pin-slider-wrap">
                      <input type="range" className="pin-slider" disabled={!isPinned} min={axis.min} max={axis.max} step={0.1} value={currentValue} onChange={e => setPinValue(axis.tag, e.target.value)} />
                      <div className="pin-slider-ticks"><span>{axis.min}</span><span>{axis.max}</span></div>
                    </div>
                    <input type="number" className="pin-value" disabled={!isPinned} min={axis.min} max={axis.max} step={0.1} value={currentValue} onChange={e => setPinValue(axis.tag, e.target.value)} />
                  </div>
                );
              })}
            </div>
          ) : (
            <>
              {/* Add mode: control-axis value, then the master corners
                  to create a view at (each ticked corner + optional
                  custom location = one editable brace layer). */}
              {(() => {
                const ctrl = (allAxes || []).find(a => a.tag === axisTag) || { min: 0, max: 0 };
                return (
                  <div className="form-row">
                    <label>
                      {axisTag} value
                      <span className="form-row-hint">the control-axis position these views define</span>
                    </label>
                    <div className="control-value-row">
                      <input type="range" min={ctrl.min} max={ctrl.max} step={0.1} value={controlValue} onChange={e => setControlValue(round1(e.target.value))} />
                      <input type="number" className="pin-value" min={ctrl.min} max={ctrl.max} step={0.1} value={controlValue} onChange={e => setControlValue(round1(e.target.value))} />
                    </div>
                  </div>
                );
              })()}

              <div className="corner-picker">
                <div className="location-pins-header">Master corners · one view per corner</div>
                <div className="corner-list">
                  {(allMasters || []).map(m => (
                    <label key={m.name} className={`corner-item ${selectedCorners.has(m.name) ? 'selected' : ''}`}>
                      <input type="checkbox" checked={selectedCorners.has(m.name)} onChange={() => toggleCorner(m.name)} />
                      <span className="corner-name">{m.name}</span>
                    </label>
                  ))}
                  {(allMasters || []).length === 0 && (
                    <div className="corner-empty">No masters found — use a custom location below.</div>
                  )}
                </div>
                <label className={`corner-item corner-custom ${customOn ? 'selected' : ''}`}>
                  <input type="checkbox" checked={customOn} onChange={() => setCustomOn(v => !v)} />
                  <span className="corner-name">Custom location…</span>
                </label>
                {customOn && (
                  <div className="location-pins">
                    {parametricAxes.map(axis => {
                            const val = customPins[axis.tag] ?? axis.default;
                      return (
                        <div key={axis.tag} className="location-pin-row pinned">
                          <span className="pin-tag">{axis.tag}</span>
                          <div className="pin-slider-wrap">
                            <input type="range" className="pin-slider" min={axis.min} max={axis.max} step={0.1} value={val} onChange={e => setCustomPinValue(axis.tag, e.target.value)} />
                            <div className="pin-slider-ticks"><span>{axis.min}</span><span>{axis.max}</span></div>
                          </div>
                          <input type="number" className="pin-value" min={axis.min} max={axis.max} step={0.1} value={val} onChange={e => setCustomPinValue(axis.tag, e.target.value)} />
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Live preview — the glyph's starting shape at each
                  picked location, rendered from the built VF at crbr=0
                  (there's no brace yet, so crbr doesn't change it). This
                  is exactly what the seeded layer opens on in Fontra. */}
              {fontLoaded && vfFamilyId && parsedGlyphs[0] && (() => {
                // The preview typesets text — map the glyph NAME to
                // its character ("eight" → "8"); single-char names
                // pass through, unmappable names get no preview.
                const previewChar = glyphChars[parsedGlyphs[0]]
                  || (parsedGlyphs[0].length === 1 ? parsedGlyphs[0] : null);
                if (!previewChar) return null;
                const items = [];
                for (const m of (allMasters || [])) {
                  if (selectedCorners.has(m.name)) items.push({ label: m.name, coords: m.coordinates });
                }
                if (customOn) {
                  const coords = {};
                  for (const a of parametricAxes) coords[a.tag] = customPins[a.tag] ?? a.default;
                  items.push({ label: 'custom', coords });
                }
                if (items.length === 0) return null;
                const fvs = (coords) => Object.entries(coords || {})
                  .map(([t, v]) => `"${t}" ${t === axisTag ? 0 : Number(v)}`)
                  .join(', ');
                return (
                  <div className="corner-previews">
                    <div className="corner-preview-row">
                      {items.map((it, i) => (
                        <div key={i} className="corner-preview-item">
                          <span
                            className="corner-preview-glyph"
                            style={{
                              fontFamily: `"${vfFamilyId}", sans-serif`,
                              fontVariationSettings: fvs(it.coords),
                            }}
                          >
                            {previewChar}
                          </span>
                          <span className="corner-preview-label">{it.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}
            </>
          )}

          <div className="location-preview">
            <strong>{isEdit ? 'New location:' : 'Will create:'}</strong>{' '}
            <code>
              {usePinEditor
                ? `${parsedGlyphs.join(', ') || '(no glyphs)'} @ {${Object.entries(pins).map(([t, v]) => `${t}=${v}`).join(', ')}}`
                : (() => {
                    const nLoc = selectedCorners.size + (customOn ? 1 : 0);
                    const nViews = parsedGlyphs.length * nLoc;
                    if (parsedGlyphs.length === 0) return '(no glyphs)';
                    if (nLoc === 0) return '(pick a corner)';
                    return `${nViews} view${nViews === 1 ? '' : 's'} @ ${axisTag}=${controlValue} · ${nLoc} corner${nLoc === 1 ? '' : 's'} × ${parsedGlyphs.length} glyph${parsedGlyphs.length === 1 ? '' : 's'}`;
                  })()}
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
                  : isDuplicate
                    ? `Create ${parsedGlyphs.length || ''} layer${parsedGlyphs.length === 1 ? '' : 's'}`.replace(/\s+/g, ' ').trim()
                    : (() => {
                      const nViews = parsedGlyphs.length * (selectedCorners.size + (customOn ? 1 : 0));
                      return `Add ${nViews || ''} view${nViews === 1 ? '' : 's'}`.trim();
                    })()}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * Remember the last hand-placed (custom / duplicated) parametric location per
 * control axis, so adding the same location for another glyph doesn't mean
 * re-dialling every axis. Persisted so it survives a reload; keyed by axis tag
 * because each axis is authored at its own locations. Storage failures are
 * non-fatal — the modal just falls back to axis defaults.
 */
const LAST_CUSTOM_KEY = (axisTag) => `avar2-studio:lastCustomLocation:${axisTag}`;

function loadLastCustomLocation(axisTag) {
  try {
    const raw = window.localStorage.getItem(LAST_CUSTOM_KEY(axisTag));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && Object.keys(parsed).length ? parsed : null;
  } catch (e) {
    return null;
  }
}

function saveLastCustomLocation(axisTag, pins) {
  try {
    const clean = {};
    for (const [t, v] of Object.entries(pins || {})) {
      const n = Number(v);
      if (Number.isFinite(n)) clean[t] = n;
    }
    if (Object.keys(clean).length) {
      window.localStorage.setItem(LAST_CUSTOM_KEY(axisTag), JSON.stringify(clean));
    }
  } catch (e) {
    /* quota / private mode — remembering is a convenience, not a requirement */
  }
}

/**
 * Pick a non-default seed value for the control-axis pin so the modal
 * opens in a valid state. A brace layer at the axis default collides
 * with the master, so the default is forbidden — seed to whichever
 * extreme differs from the default. Prefer min; fall back to max when
 * the default sits at the min (e.g. a 0…40 axis with default 0).
 */
function seedControlValue(ax) {
  const def = Number(ax.default);
  const min = Number(ax.min);
  const max = Number(ax.max);
  if (min !== def) return min;
  if (max !== def) return max;
  return def; // degenerate axis (min == max == default) — no valid pin exists
}

// Axis coordinates are held to one decimal place — the sliders step in
// 0.1 and everything rounds here, so a drag never lands on a value like
// 390.362 that the number input (and save) would reject.
function round1(value) {
  const n = parseFloat(value);
  return Number.isFinite(n) ? Math.round(n * 10) / 10 : 0;
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
