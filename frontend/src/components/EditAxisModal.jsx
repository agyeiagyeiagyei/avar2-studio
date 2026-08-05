import React, { useState, useEffect, useRef } from 'react';
import './EditAxisModal.css';

function EditAxisModal({ isOpen, onClose, onConfirm, axisName, axisMetadata, existingAxes = [], isParametricAxis = false, onDelete = null }) {
  const [displayName, setDisplayName] = useState('');
  const [registeredTag, setRegisteredTag] = useState('');
  const [minValue, setMinValue] = useState('');
  const [defaultValue, setDefaultValue] = useState('');
  const [maxValue, setMaxValue] = useState('');
  const [errors, setErrors] = useState({});
  const displayNameRef = useRef(null);

  useEffect(() => {
    if (isOpen && axisMetadata) {
      setDisplayName(axisMetadata.display_name || '');
      setRegisteredTag(axisMetadata.registered_tag || '');
      setMinValue(String(axisMetadata.min ?? -1000));
      setDefaultValue(String(axisMetadata.default ?? axisMetadata.min ?? 0));
      setMaxValue(String(axisMetadata.max ?? 1000));
      setErrors({});
      // Focus input after a brief delay
      setTimeout(() => {
        if (displayNameRef.current) {
          displayNameRef.current.focus();
        }
      }, 100);
    }
  }, [isOpen, axisMetadata]);

  const validate = () => {
    const newErrors = {};
    
    if (!displayName.trim()) {
      newErrors.displayName = 'Display name is required';
    }
    
    if (!registeredTag.trim()) {
      newErrors.registeredTag = 'Registered tag is required';
    } else if (registeredTag.length !== 4) {
      newErrors.registeredTag = 'Tag must be exactly 4 characters';
    } else if (!registeredTag.match(/^[a-z0-9]{4}$/)) {
      newErrors.registeredTag = 'Tag must be 4 lowercase alphanumeric characters';
    } else {
      // Check for duplicate tags (excluding current axis)
      const existingTags = existingAxes
        .filter(ax => ax.axisName !== axisName)
        .map(ax => ax.registeredTag)
        .filter(Boolean);
      if (existingTags.includes(registeredTag.toLowerCase())) {
        newErrors.registeredTag = 'Registered tag already used by another axis';
      }
    }
    
    const min = parseFloat(minValue);
    const max = parseFloat(maxValue);
    if (isNaN(min) || min < -1000 || min > 1000) {
      newErrors.minValue = 'Min must be between -1000 and 1000';
    }
    if (isNaN(max) || max < -1000 || max > 1000) {
      newErrors.maxValue = 'Max must be between -1000 and 1000';
    }
    if (!isNaN(min) && !isNaN(max) && min >= max) {
      newErrors.maxValue = 'Max must be greater than min';
    }
    const def = parseFloat(defaultValue);
    if (isNaN(def)) {
      newErrors.defaultValue = 'Default must be a number';
    } else if (!isNaN(min) && !isNaN(max) && (def < min || def > max)) {
      newErrors.defaultValue = 'Default must be between min and max';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (validate()) {
      onConfirm({
        display_name: displayName.trim(),
        registered_tag: registeredTag.toLowerCase().trim(),
        min: parseFloat(minValue),
        default: parseFloat(defaultValue),
        max: parseFloat(maxValue),
      });
    }
  };

  const handleCancel = () => {
    setErrors({});
    onClose();
  };

  if (!isOpen || !axisMetadata) return null;

  if (isParametricAxis) {
    return (
      <div className="modal-overlay">
        <div className="modal-content edit-axis-modal" onClick={(e) => e.stopPropagation()}>
          <h3>Cannot Edit Parametric Axis</h3>
          <p>Axis "{axisName}" exists in the Glyphs file as a parametric axis. Parametric axes are managed in the Glyphs file and cannot be edited through avar2 mappings.</p>
          <div className="modal-buttons">
            <button type="button" onClick={handleCancel} className="btn btn-confirm">
              OK
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-overlay">
      <div className="modal-content edit-axis-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Edit Axis: {axisName}</h3>
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>
              Display Name <span className="required">*</span>
            </label>
            <input
              ref={displayNameRef}
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g., Weight"
              className={errors.displayName ? 'error' : ''}
            />
            {errors.displayName && <span className="error-message">{errors.displayName}</span>}
          </div>
          
          <div className="form-group">
            <label>
              Registered Tag (OpenType) <span className="required">*</span>
            </label>
            <input
              type="text"
              value={registeredTag}
              onChange={(e) => setRegisteredTag(e.target.value.toLowerCase().slice(0, 4))}
              placeholder="e.g., wght"
              maxLength={4}
              className={errors.registeredTag ? 'error' : ''}
            />
            {errors.registeredTag && <span className="error-message">{errors.registeredTag}</span>}
            <small>4 lowercase alphanumeric characters</small>
          </div>
          
          <div className="form-row">
            <div className="form-group">
              <label>
                Min Value <span className="required">*</span>
              </label>
              <input
                type="number"
                value={minValue}
                onChange={(e) => setMinValue(e.target.value)}
                min="-1000"
                max="1000"
                step="1"
                className={`axis-number-input ${errors.minValue ? 'error' : ''}`}
              />
              {errors.minValue && <span className="error-message">{errors.minValue}</span>}
            </div>

            <div className="form-group">
              <label>
                Default <span className="required">*</span>
              </label>
              <input
                type="number"
                value={defaultValue}
                onChange={(e) => setDefaultValue(e.target.value)}
                min="-1000"
                max="1000"
                step="1"
                className={`axis-number-input ${errors.defaultValue ? 'error' : ''}`}
                title="Where the axis rests. This compiles into the font's fvar — avar2 cannot remap the default location, so put it where the SOURCE's default outlines live (or author a mapping row structure that surrounds it)."
              />
              {errors.defaultValue && <span className="error-message">{errors.defaultValue}</span>}
            </div>

            <div className="form-group">
              <label>
                Max Value <span className="required">*</span>
              </label>
              <input
                type="number"
                value={maxValue}
                onChange={(e) => setMaxValue(e.target.value)}
                min="-1000"
                max="1000"
                step="1"
                className={`axis-number-input ${errors.maxValue ? 'error' : ''}`}
              />
              {errors.maxValue && <span className="error-message">{errors.maxValue}</span>}
            </div>
          </div>
          
          <div className="modal-buttons">
            {onDelete && (
              <button
                type="button"
                className="btn btn-cancel"
                onClick={() => {
                  if (window.confirm(`Delete mapping axis "${axisName}"? Its column and every instance's value for it are removed from the mappings.`)) {
                    onDelete(axisName);
                  }
                }}
              >
                Delete axis
              </button>
            )}
            <button type="button" onClick={handleCancel} className="btn btn-cancel">
              Cancel
            </button>
            <button type="submit" className="btn btn-confirm">
              Save
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default EditAxisModal;
