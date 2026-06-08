import React, { useState } from 'react';
import './DeleteInstanceModal.css';

function DeleteInstanceModal({ isOpen, onClose, instanceName, onConfirm, glyphsFileHasUnsavedChanges }) {
  // Default to deleting from source AND CSV — that's almost always
  // what the user wants when they click the trash icon. The unchecked
  // path keeps the source instance but removes its avar2 mapping row
  // (useful when "unmapping" without losing the source-side instance).
  const [deleteFromGlyphs, setDeleteFromGlyphs] = useState(true);

  if (!isOpen) return null;

  const handleConfirm = () => {
    onConfirm(deleteFromGlyphs);
    setDeleteFromGlyphs(true); // Reset for next open
  };

  const handleCancel = () => {
    setDeleteFromGlyphs(true);
    onClose();
  };

  const isDisabled = deleteFromGlyphs && glyphsFileHasUnsavedChanges;

  return (
    <div className="modal-overlay" onClick={handleCancel}>
      <div className="delete-instance-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Delete instance</h3>
        <p className="delete-instance-message">
          {deleteFromGlyphs
            ? `Delete "${instanceName}" from the source file and the avar2 mapping CSV.`
            : `Remove "${instanceName}" from the avar2 mapping CSV. The source-file instance stays.`}
        </p>
        {deleteFromGlyphs && (
          <p className="delete-instance-warning">
            ⚠️ This rewrites your .glyphs / .designspace. Can't be undone from here.
          </p>
        )}
        {isDisabled && (
          <p className="delete-instance-error">
            ⚠️ Glyphs file has unsaved changes. Save the file first, or uncheck the box below to keep the source instance.
          </p>
        )}
        <div className="delete-instance-checkbox">
          <label>
            <input
              type="checkbox"
              checked={deleteFromGlyphs}
              onChange={(e) => setDeleteFromGlyphs(e.target.checked)}
              disabled={glyphsFileHasUnsavedChanges}
            />
            <span>Also delete from the source file (.glyphs / .designspace)</span>
          </label>
        </div>
        <div className="delete-instance-buttons">
          <button
            className="delete-instance-cancel"
            onClick={handleCancel}
          >
            Cancel
          </button>
          <button
            className="delete-instance-confirm"
            onClick={handleConfirm}
            disabled={isDisabled}
          >
            {deleteFromGlyphs ? 'Delete from source + CSV' : 'Remove from CSV only'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default DeleteInstanceModal;
