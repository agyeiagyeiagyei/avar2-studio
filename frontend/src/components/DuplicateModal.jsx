import React, { useState, useEffect, useRef } from 'react';
import './DuplicateModal.css';

function DuplicateModal({ isOpen, onClose, onConfirm, instanceName, mode = 'duplicate', axes = [] }) {
  const [newName, setNewName] = useState('');
  const [coords, setCoords] = useState({});
  const inputRef = useRef(null);

  // The same modal handles two flows. When ``mode==='new'`` the caller
  // is creating a fresh studio-only row from axis defaults — no source
  // instance to copy from — and we expose per-axis inputs so the user
  // can pick the starting coords up front instead of duplicating + tuning.
  const isNew = mode === 'new';
  const title = isNew ? 'New Instance' : 'Duplicate Instance';
  const prompt = isNew
    ? "Name your instance and set its starting coordinates. You can tune them further once the row appears."
    : 'Enter a name for the new instance:';
  const confirmLabel = isNew ? 'Create' : 'Duplicate';

  // Seed input + coords once on open. ``axes`` deliberately not in the
  // dependency list — App.js polls every 2s and hands us a fresh array
  // reference each time, which previously made this effect re-fire and
  // wipe whatever the user had typed. The axes value captured at open
  // time is sufficient since the modal is short-lived.
  useEffect(() => {
    if (isOpen) {
      setNewName(isNew ? '' : `${instanceName} Copy`);
      const initial = {};
      axes.forEach(a => { initial[a.tag] = a.default; });
      setCoords(initial);
      setTimeout(() => {
        if (inputRef.current) {
          inputRef.current.focus();
          inputRef.current.select();
        }
      }, 100);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, instanceName, isNew]);

  const handleSubmit = (e) => {
    e.preventDefault();
    const trimmedName = newName.trim();
    if (trimmedName) {
      onConfirm(trimmedName, isNew ? coords : undefined);
    }
  };

  const handleCancel = () => {
    setNewName('');
    setCoords({});
    onClose();
  };

  const setAxisValue = (tag, raw) => {
    const numeric = parseFloat(raw);
    setCoords(prev => ({ ...prev, [tag]: Number.isFinite(numeric) ? numeric : raw }));
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <h3>{title}</h3>
        <p>{prompt}</p>
        <form onSubmit={handleSubmit}>
          <input
            ref={inputRef}
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Instance name"
            className="modal-input"
            autoFocus
          />
          {isNew && axes.length > 0 && (
            <div className="modal-axis-inputs">
              {axes.map(axis => (
                <div key={axis.tag} className="modal-axis-row">
                  <label className="modal-axis-label">
                    <span className="modal-axis-tag">{axis.tag}</span>
                    <span className="modal-axis-range">
                      {axis.min} – {axis.max}
                    </span>
                  </label>
                  <input
                    type="number"
                    className="modal-axis-input"
                    value={coords[axis.tag] ?? axis.default}
                    min={axis.min}
                    max={axis.max}
                    step={0.1}
                    onChange={(e) => setAxisValue(axis.tag, e.target.value)}
                  />
                </div>
              ))}
            </div>
          )}
          <div className="modal-buttons">
            <button type="button" onClick={handleCancel} className="btn btn-cancel">
              Cancel
            </button>
            <button type="submit" className="btn btn-confirm" disabled={!newName.trim()}>
              {confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default DuplicateModal;
