import React, { useEffect, useRef, useState } from 'react';
import './Header.css';
import { api } from '../api';

// "Load Font" dropdown sitting in the top bar. The dropdown is the
// single entry point for swapping the active source at runtime:
//
//   - Built-in examples come from GET /api/examples. Selecting one
//     POSTs { example: id } to /api/load-source; the backend stages
//     a per-example workspace under ~/.avar2-studio/workspace/ so
//     the shipped fixture stays clean.
//   - "Upload .glyphs file…" opens a hidden file picker. The chosen
//     file is sent as multipart/form-data to the same endpoint.
function Header({ onBuildFont, building, fontLoaded, familyName, onSourceLoaded, busy }) {
  const [examples, setExamples] = useState([]);
  const [open, setOpen] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState(null);
  const dropdownRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    api.listExamples()
      .then(d => setExamples(d.examples || []))
      .catch(() => setExamples([]));
  }, []);

  // Click-outside closes the menu.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const handleLoadExample = async (id, name) => {
    setOpen(false);
    setLoadingMsg(`Loading ${name}…`);
    try {
      await api.loadExample(id);
      onSourceLoaded && onSourceLoaded();
    } catch (err) {
      setLoadingMsg(`Failed: ${err.message || err}`);
      setTimeout(() => setLoadingMsg(null), 4000);
      return;
    }
    setLoadingMsg(null);
  };

  const handleUploadClick = () => {
    setOpen(false);
    fileInputRef.current && fileInputRef.current.click();
  };

  const handleFileChange = async (e) => {
    const files = e.target.files;
    e.target.value = '';
    if (!files || files.length === 0) return;
    const names = Array.from(files).map(f => f.name).join(', ');
    setLoadingMsg(`Uploading ${names}…`);
    try {
      const result = await api.uploadSource(files);
      if (result.ignored_files && result.ignored_files.length > 0) {
        // Surface the silently-rejected files so the user knows their
        // CSV / metadata picks didn't all land.
        console.warn('Ignored files:', result.ignored_files);
      }
      onSourceLoaded && onSourceLoaded();
    } catch (err) {
      setLoadingMsg(`Failed: ${err.message || err}`);
      setTimeout(() => setLoadingMsg(null), 4000);
      return;
    }
    setLoadingMsg(null);
  };

  return (
    <header className="header">
      <div className="header-title">
        {familyName ? <h1>{familyName}</h1> : <h1 className="header-title-empty">avar2-studio</h1>}
        {loadingMsg && <span className="header-loading-msg">{loadingMsg}</span>}
      </div>
      <div className="header-actions">
        <div className="load-font-dropdown" ref={dropdownRef}>
          <button
            className="btn btn-load-font"
            onClick={() => setOpen(o => !o)}
            disabled={busy}
            title="Load a different source: a built-in example fixture or your own .glyphs file"
          >
            Load Font ▾
          </button>
          {open && (
            <div className="load-font-menu">
              {examples.length > 0 && (
                <>
                  <div className="load-font-section-label">Examples</div>
                  {examples.map(ex => (
                    <button
                      key={ex.id}
                      className="load-font-item"
                      onClick={() => handleLoadExample(ex.id, ex.name)}
                    >
                      <div className="load-font-item-name">{ex.name}</div>
                      {ex.subtitle && <div className="load-font-item-subtitle">{ex.subtitle}</div>}
                    </button>
                  ))}
                  <div className="load-font-divider" />
                </>
              )}
              <button
                className="load-font-item load-font-item-upload"
                onClick={handleUploadClick}
              >
                <div className="load-font-item-name">Upload .glyphs file…</div>
                <div className="load-font-item-subtitle">
                  Optionally select the sibling -avar.csv and avar2-axis-metadata.json in the same picker.
                </div>
              </button>
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".glyphs,.csv,.json"
            multiple
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
        </div>
        {fontLoaded !== undefined && familyName && (
          <button
            onClick={onBuildFont}
            disabled={building}
            className="btn btn-primary"
          >
            {building ? 'Building...' : fontLoaded ? 'Rebuild Font' : 'Build Font'}
          </button>
        )}
      </div>
    </header>
  );
}

export default Header;
