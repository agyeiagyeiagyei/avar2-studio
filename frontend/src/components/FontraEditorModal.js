import React, { useEffect } from 'react';
import './FontraEditorModal.css';

/**
 * Full-screen overlay that iframes the Fontra editor pointed at the
 * studio's shadow source file. The designer edits brace-layer
 * outlines for control-axis coverage glyphs; on close, the parent
 * triggers a font rebuild so the preview reflects the edits.
 *
 * Props:
 *   editor    — { url, tag } or null. When null, modal is hidden.
 *   onClose   — called when the user dismisses. Parent should also
 *               trigger a font rebuild so the preview catches up.
 */
function FontraEditorModal({ editor, onClose }) {
  // Escape closes the modal. Bound at the document level so it
  // works even when the iframe has focus.
  useEffect(() => {
    if (!editor) return;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [editor, onClose]);

  if (!editor) return null;

  return (
    <div className="fontra-editor-overlay">
      <div className="fontra-editor-header">
        <div className="fontra-editor-title">
          Editing <code>{editor.tag}</code> coverage in Fontra
          <span className="fontra-editor-subtitle">
            edits land in the shadow .glyphs · close to refresh preview
          </span>
        </div>
        <div className="fontra-editor-actions">
          <a
            href={editor.url}
            target="_blank"
            rel="noreferrer"
            className="fontra-editor-popout"
            title="Open Fontra in a new tab (handy for a larger canvas)"
          >
            Open in new tab ↗
          </a>
          <button
            type="button"
            className="fontra-editor-close"
            onClick={onClose}
            title="Close Fontra. Studio rebuilds the preview from the shadow on close."
          >
            Done editing
          </button>
        </div>
      </div>
      <iframe
        className="fontra-editor-iframe"
        src={editor.url}
        title="Fontra editor"
      />
    </div>
  );
}

export default FontraEditorModal;
