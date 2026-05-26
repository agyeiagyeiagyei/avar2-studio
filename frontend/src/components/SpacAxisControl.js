import React, { useState, useRef, useEffect } from 'react';
import './AxisControl.css'; // Use same styles as parametric axes
import { formatAxisValue } from '../utils/formatNumber';

function SpacAxisControl({ axis, value, onChange, disabled }) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const inputRef = useRef(null);

  const handleSliderChange = (e) => {
    const newValue = parseFloat(e.target.value);
    onChange(newValue);
  };

  const handleValueClick = () => {
    if (disabled) return;
    setIsEditing(true);
    // Show current value with up to 3 decimal places when editing
    setEditValue(value.toFixed(3));
  };

  const handleInputChange = (e) => {
    setEditValue(e.target.value);
  };

  const validateAndSave = () => {
    const numValue = parseFloat(editValue);
    
    if (isNaN(numValue)) {
      // Invalid input, revert to current value
      setIsEditing(false);
      return;
    }

    // Clamp to valid range
    const clampedValue = Math.max(axis.min, Math.min(axis.max, numValue));
    
    // Round to 3 decimal places (allow precision up to 0.001)
    const roundedValue = Math.round(clampedValue * 1000) / 1000;
    
    onChange(roundedValue);
    setIsEditing(false);
  };

  const handleInputBlur = () => {
    validateAndSave();
  };

  const handleInputKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      validateAndSave();
    } else if (e.key === 'Escape') {
      setIsEditing(false);
      setEditValue('');
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const numValue = parseFloat(editValue) || value;
      const newValue = Math.min(axis.max, numValue + 0.1);
      const roundedValue = Math.round(newValue * 1000) / 1000;
      setEditValue(roundedValue.toFixed(3));
      onChange(roundedValue);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const numValue = parseFloat(editValue) || value;
      const newValue = Math.max(axis.min, numValue - 0.1);
      const roundedValue = Math.round(newValue * 1000) / 1000;
      setEditValue(roundedValue.toFixed(3));
      onChange(roundedValue);
    }
  };

  // Focus input when entering edit mode
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  // Update editValue when value changes externally (e.g., from slider)
  useEffect(() => {
    if (!isEditing) {
      setEditValue('');
    }
  }, [value, isEditing]);

  return (
    <div className="axis-control">
      <div className="axis-header">
        <label className="axis-name">{axis.name}</label>
        <span className="axis-tag">{axis.tag}</span>
      </div>
      <div className="axis-slider-container">
        <input
          type="range"
          min={axis.min}
          max={axis.max}
          step={0.1}
          value={value}
          onChange={handleSliderChange}
          disabled={disabled}
          className="axis-slider"
        />
        <div className="axis-values">
          <span className="axis-min">{axis.min}</span>
          {isEditing ? (
            <input
              ref={inputRef}
              type="text"
              className="axis-current axis-current-editing"
              value={editValue}
              onChange={handleInputChange}
              onBlur={handleInputBlur}
              onKeyDown={handleInputKeyDown}
              disabled={disabled}
            />
          ) : (
            <span 
              className="axis-current axis-current-clickable"
              onClick={handleValueClick}
              title="Click to edit value"
            >
              {formatAxisValue(value)}
            </span>
          )}
          <span className="axis-max">{axis.max}</span>
        </div>
      </div>
    </div>
  );
}

export default SpacAxisControl;
