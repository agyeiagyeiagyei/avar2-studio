import React, { useEffect, useRef, useState } from 'react';
import './AddControlAxisModal.css';

/**
 * Modal for declaring a new CONTROL AXIS.
 *
 * Captures tag (4-char OT tag), display name, min/max/default. The
 * declaration persists to the sibling ``<basename>-control.json``
 * via POST /api/control-axes. v2 slice 1 stops here — coverage list
 * + brace-layer authoring lands in later slices.
 *
 * Props:
 *   isOpen     — render gate
 *   onClose    — () => void
 *   onCreate   — async (axis) => Promise; throws on validation
 *                error so this component can surface it inline
 *   existingTags — Array<string> of axis tags (any source) the user
 *                  shouldn't collide with — pre-validates the tag
 *                  before the API call so the user gets immediate
 *                  feedback instead of a round-trip 400.
 */
function AddControlAxisModal({ isOpen, onClose, onCreate, onUpdate, editAxis, existingTags = [] }) {
  const isEdit = !!editAxis;
  const [tag, setTag] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [defaultValue, setDefaultValue] = useState('0');
  const [minValue, setMinValue] = useState('-100');
  const [maxValue, setMaxValue] = useState('100');
  const [errors, setErrors] = useState({});
  const [submitError, setSubmitError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const tagInputRef = useRef(null);
  const nameInputRef = useRef(null);

  useEffect(() => {
    if (isOpen) {
      if (isEdit) {
        setTag(editAxis.tag || '');
        setDisplayName(editAxis.name || editAxis.display_name || '');
        setDefaultValue(String(editAxis.default ?? 0));
        setMinValue(String(editAxis.min ?? -100));
        setMaxValue(String(editAxis.max ?? 100));
      } else {
        setTag('');
        setDisplayName('');
        setDefaultValue('0');
        setMinValue('-100');
        setMaxValue('100');
      }
      setErrors({});
      setSubmitError(null);
      setSubmitting(false);
      // Focus the first editable input after mount — tag in create,
      // display name in edit (since tag is locked).
      setTimeout(() => {
        if (isEdit) {
          nameInputRef.current && nameInputRef.current.focus();
        } else {
          tagInputRef.current && tagInputRef.current.focus();
        }
      }, 50);
    }
  }, [isOpen, isEdit, editAxis]);

  if (!isOpen) return null;

  const validate = () => {
    const next = {};
    const tagTrim = tag.trim().toLowerCase();
    // In edit mode the tag is locked so we don't re-validate it; only
    // create mode enforces uniqueness + shape.
    if (!isEdit) {
      if (!tagTrim) {
        next.tag = 'Required';
      } else if (tagTrim.length !== 4) {
        next.tag = 'Must be exactly 4 characters';
      } else if (!/^[a-z0-9_-]{4}$/.test(tagTrim)) {
        next.tag = 'Use lowercase letters, digits, _ or -';
      } else if (existingTags.some(t => String(t).toLowerCase() === tagTrim)) {
        next.tag = 'Tag already used by another axis';
      }
    }
    if (!displayName.trim()) {
      next.displayName = 'Required';
    }
    const minN = parseFloat(minValue);
    const maxN = parseFloat(maxValue);
    const defN = parseFloat(defaultValue);
    if (isNaN(minN)) next.minValue = 'Must be numeric';
    if (isNaN(maxN)) next.maxValue = 'Must be numeric';
    if (isNaN(defN)) next.defaultValue = 'Must be numeric';
    if (!isNaN(minN) && !isNaN(maxN) && minN >= maxN) {
      next.maxValue = 'Max must be greater than min';
    }
    if (!isNaN(defN) && !isNaN(minN) && !isNaN(maxN) && (defN < minN || defN > maxN)) {
      next.defaultValue = `Must be within [${minN}, ${maxN}]`;
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!validate() || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      if (isEdit) {
        await onUpdate({
          tag: editAxis.tag,
          display_name: displayName.trim(),
          default: parseFloat(defaultValue),
          min: parseFloat(minValue),
          max: parseFloat(maxValue),
        });
      } else {
        await onCreate({
          tag: tag.trim().toLowerCase(),
          display_name: displayName.trim(),
          default: parseFloat(defaultValue),
          min: parseFloat(minValue),
          max: parseFloat(maxValue),
        });
      }
      onClose();
    } catch (err) {
      setSubmitError(err.message || (isEdit ? 'Failed to update secondary parametric axis' : 'Failed to create secondary parametric axis'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="add-control-axis-modal">
        <h3>{isEdit ? `Edit secondary parametric axis · ${editAxis.tag}` : 'New secondary parametric axis'}</h3>
        <p className="modal-help">
          {isEdit
            ? 'Change the display name, range, or default. Tag is immutable — every applicable glyph\'s brace-layer location is keyed by it. Narrowing min/max is refused if any existing layer would fall outside the new range.'
            : 'Declares an axis the designer controls — separate from AVAR2 MAPPINGS. After creating it, add applicable glyphs and draw their brace layers in the embedded editor. Your source file is never modified.'}
        </p>
        <form onSubmit={handleSubmit}>
          <div className="form-row">
            <label htmlFor="ctl-tag">
              Tag <span className="hint">{isEdit ? '(immutable)' : '(4 chars, e.g. '}{!isEdit && <code>crbr</code>}{!isEdit && ')'}</span>
            </label>
            <input
              ref={tagInputRef}
              id="ctl-tag"
              type="text"
              maxLength={4}
              value={tag}
              onChange={e => setTag(e.target.value)}
              className={errors.tag ? 'has-error' : ''}
              autoComplete="off"
              spellCheck={false}
              readOnly={isEdit}
            />
            {errors.tag && <span className="field-error">{errors.tag}</span>}
          </div>
          <div className="form-row">
            <label htmlFor="ctl-name">Display name</label>
            <input
              ref={nameInputRef}
              id="ctl-name"
              type="text"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              className={errors.displayName ? 'has-error' : ''}
              placeholder="e.g. Crossbar"
            />
            {errors.displayName && <span className="field-error">{errors.displayName}</span>}
          </div>
          <div className="form-row form-row-triple">
            <div>
              <label htmlFor="ctl-min">Min</label>
              <input
                id="ctl-min"
                type="number"
                value={minValue}
                onChange={e => setMinValue(e.target.value)}
                className={errors.minValue ? 'has-error' : ''}
              />
              {errors.minValue && <span className="field-error">{errors.minValue}</span>}
            </div>
            <div>
              <label htmlFor="ctl-default">Default</label>
              <input
                id="ctl-default"
                type="number"
                value={defaultValue}
                onChange={e => setDefaultValue(e.target.value)}
                className={errors.defaultValue ? 'has-error' : ''}
              />
              {errors.defaultValue && <span className="field-error">{errors.defaultValue}</span>}
            </div>
            <div>
              <label htmlFor="ctl-max">Max</label>
              <input
                id="ctl-max"
                type="number"
                value={maxValue}
                onChange={e => setMaxValue(e.target.value)}
                className={errors.maxValue ? 'has-error' : ''}
              />
              {errors.maxValue && <span className="field-error">{errors.maxValue}</span>}
            </div>
          </div>
          {submitError && (
            <div className="submit-error">{submitError}</div>
          )}
          <div className="modal-buttons">
            <button type="button" className="btn btn-cancel" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn-confirm" disabled={submitting}>
              {submitting
                ? (isEdit ? 'Saving…' : 'Creating…')
                : (isEdit ? 'Save changes' : 'Create')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AddControlAxisModal;
