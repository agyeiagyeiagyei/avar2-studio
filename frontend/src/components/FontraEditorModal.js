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
          Editing <code>{editor.tag}{editor.controlValue !== null && editor.controlValue !== undefined ? `=${editor.controlValue}` : ''}</code>
          {editor.glyphName ? <> — glyph <code>{editor.glyphName}</code></> : null}
          <span className="fontra-editor-subtitle">
            Draw the {editor.axisName || editor.tag} change here. Close to update the preview.
          </span>
        </div>
        <div className="fontra-editor-actions">
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
      {/* Iframe loads Fontra through avar2-studio's same-origin
          proxy at /fontra/*. The proxy now also forwards Fontra's
          runtime root-path fetches (/lang, /data, /images,
          /webfonts, /projectlist, /serverinfo) plus an /api/*
          catch-all that falls through after specific avar2-studio
          /api routes match. Same-origin lets us inject the
          focused-UI stylesheet that hides irrelevant Fontra
          panels and tools. editor.directUrl is exposed as the
          "Open in new tab" escape, bypassing the focused overlay. */}
      <iframe
        className="fontra-editor-iframe"
        src={editor.url}
        title="Fontra editor"
      />
    </div>
  );
}

export default FontraEditorModal;
