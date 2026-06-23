import React, { useEffect, useRef, useState } from 'react';
import './FontraEditorModal.css';

/**
 * Right-side drawer that iframes the Fontra editor pointed at the
 * studio's shadow source file. The designer edits brace-layer
 * outlines for control-axis coverage glyphs; on close, the parent
 * triggers a font rebuild so the preview reflects the edits.
 *
 * Drawer width is user-resizable via the left-edge handle and
 * persists in localStorage across sessions.
 *
 * Props:
 *   editor    — { url, tag } or null. When null, drawer is hidden.
 *   onClose   — called when the user dismisses. Parent should also
 *               trigger a font rebuild so the preview catches up.
 */
function FontraEditorModal({ editor, onClose }) {
  // Drawer width — persisted across sessions. Default 60vw, min 600px
  // (Fontra needs room to render a meaningful canvas + sidebars).
  const [width, setWidth] = useState(() => {
    const saved = localStorage.getItem('avar2.fontraDrawerWidth');
    return saved && parseInt(saved, 10) > 0
      ? parseInt(saved, 10) + 'px'
      : '60vw';
  });
  const dragStateRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  // Escape closes the drawer. Bound at the document level so it
  // works even when the iframe has focus.
  useEffect(() => {
    if (!editor) return;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [editor, onClose]);

  const startResize = (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = window.innerWidth - e.target.parentElement.getBoundingClientRect().left;
    dragStateRef.current = { startX, startWidth };
    setDragging(true);
    const onMove = (ev) => {
      const { startX, startWidth } = dragStateRef.current;
      const next = Math.max(600, Math.min(window.innerWidth - 80, startWidth + (startX - ev.clientX)));
      setWidth(next + 'px');
    };
    const onUp = () => {
      setDragging(false);
      // Persist the final width.
      const px = parseInt(width, 10);
      if (!isNaN(px)) localStorage.setItem('avar2.fontraDrawerWidth', String(px));
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // Capture the LATEST width into the persisted store on unmount /
  // close — the onUp handler captures a stale ``width`` from
  // closure scope and would otherwise save the first-drag value.
  useEffect(() => {
    return () => {
      const px = parseInt(width, 10);
      if (!isNaN(px)) localStorage.setItem('avar2.fontraDrawerWidth', String(px));
    };
  }, [width]);

  if (!editor) return null;

  return (
    <div
      className="fontra-editor-overlay"
      style={{ '--fontra-drawer-width': width }}
    >
      <div
        className={`fontra-editor-resize ${dragging ? 'dragging' : ''}`}
        onMouseDown={startResize}
        title="Drag to resize the Fontra drawer"
      />
      <div className="fontra-editor-header">
        <div className="fontra-editor-title">
          Editing <code>{editor.tag}</code> coverage in Fontra
          <span className="fontra-editor-subtitle">
            edits land in the shadow .glyphs · close to refresh preview
          </span>
        </div>
        <div className="fontra-editor-actions">
          <a
            href={editor.directUrl || editor.url}
            target="_blank"
            rel="noreferrer"
            className="fontra-editor-popout"
            title="Open Fontra in a new tab (raw URL, no focused-UI overlay)"
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
      {/* Iframe loads Fontra directly at port 8001 (cross-origin)
          rather than through avar2-studio's /fontra/* reverse
          proxy. The proxy works for the HTML/CSS/JS rewrite but
          Fontra also makes runtime fetches for translations
          (/lang/*), images, and its /api/* routes — those bypass
          the HTML rewriter and 404 against avar2-studio's root.
          Symptom: menubar shows literal keys (menubar.file etc),
          sidebar tab icons blank, canvas empty.
          Cross-origin loses CSS injection / focused-UI overlay
          but gives the designer a working editor. Re-enable the
          proxied URL once we extend the proxy with root-level
          routes for /lang, /images, /api, etc. */}
      <iframe
        className="fontra-editor-iframe"
        src={editor.directUrl || editor.url}
        title="Fontra editor"
      />
    </div>
  );
}

export default FontraEditorModal;
