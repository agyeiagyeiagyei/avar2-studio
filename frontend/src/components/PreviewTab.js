import React, { useState, useEffect, useMemo } from 'react';
import './PreviewTab.css';
import AxisControl from './AxisControl';
import { formatAxisValue } from '../utils/formatNumber';

const DEFAULT_SAMPLE = 'Adhesion';

/**
 * Free-form preview of the *actual built font*. Unlike the Instances
 * view — which pins each row to fixed parametric coordinates — this tab
 * hands the designer live sliders for the user-facing avar2-mapped axes
 * (wght/wdth) and the control axes (crbr), and renders sample text with
 * CSS font-variation-settings. HarfBuzz resolves the avar2 table and the
 * control-axis deltas, so moving `wght` drives the parametric axes
 * automatically — no JS-side interpolation. This is the "shipped font"
 * experience the way an end user would feel it.
 *
 * Axis classification comes straight off /api/axes (the built font's
 * fvar surface):
 *   - is_control_axis            → Control axes
 *   - has_master_coverage false  → User axes (avar2-mapped target)
 *   - otherwise                  → Parametric axes (advanced)
 *
 * Props:
 *   axes              — full fvar axis list from /api/axes
 *   vfFamilyId        — FontFace family id (already registered by App)
 *   fontLoaded        — has the built font loaded?
 *   fontUrl           — /api/font url (for download)
 *   builtFontFilename — filename to save the download as
 *   sampleText / onSampleTextChange — shared with the Instances view
 *   fontSize   / onFontSizeChange   — shared with the Instances view
 */
function PreviewTab({
  axes,
  vfFamilyId,
  fontLoaded,
  fontUrl,
  builtFontFilename,
  sampleText,
  onSampleTextChange,
  fontSize,
  onFontSizeChange,
}) {
  const { userAxes, controlAxes, parametricAxes } = useMemo(() => {
    const user = [];
    const control = [];
    const param = [];
    for (const a of (axes || [])) {
      if (a.is_control_axis) control.push(a);
      else if (a.has_master_coverage === false) user.push(a);
      else param.push(a);
    }
    return { userAxes: user, controlAxes: control, parametricAxes: param };
  }, [axes]);

  const [coords, setCoords] = useState({});
  const [showParametric, setShowParametric] = useState(false);

  // Seed each axis at its default the first time it appears; leave
  // designer edits untouched on subsequent axis-list changes.
  useEffect(() => {
    setCoords(prev => {
      const next = { ...prev };
      let changed = false;
      for (const a of (axes || [])) {
        if (next[a.tag] === undefined) {
          next[a.tag] = a.default;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [axes]);

  // Register the built font ourselves so the canvas is correct even when
  // the Instances tab (which normally loads it) is unmounted — e.g. after
  // a rebuild triggered while this tab is active. Idempotent: same family,
  // same global document.fonts as InstanceRows.
  useEffect(() => {
    if (!(fontUrl && fontLoaded && typeof fontUrl === 'string' && vfFamilyId)) return;
    const oldFont = Array.from(document.fonts).find(f => f.family === vfFamilyId);
    if (oldFont) document.fonts.delete(oldFont);
    const fontFace = new FontFace(vfFamilyId, `url(${fontUrl})`);
    fontFace.load()
      .then((loaded) => { document.fonts.add(loaded); return document.fonts.ready; })
      .catch(err => console.error('Preview: failed to load font:', err));
  }, [fontUrl, fontLoaded, vfFamilyId]);

  const setAxis = (tag, value) => setCoords(prev => ({ ...prev, [tag]: value }));

  const resetAll = () => {
    const next = {};
    for (const a of (axes || [])) next[a.tag] = a.default;
    setCoords(next);
  };

  const atDefault = useMemo(
    () => (axes || []).every(a => (coords[a.tag] ?? a.default) === a.default),
    [axes, coords]
  );

  // font-variation-settings uses the exact fvar tag, case-sensitively —
  // XTRA/XOPQ/YOPQ stay uppercase, crbr/wght stay as declared. (Matches
  // the working layer thumbnails; do NOT lowercase.)
  const fvs = useMemo(
    () => (axes || []).map(a => `"${a.tag}" ${coords[a.tag] ?? a.default}`).join(', '),
    [axes, coords]
  );

  const handleDownload = () => {
    if (!fontUrl) return;
    const downloadUrl = `${fontUrl}${fontUrl.includes('?') ? '&' : '?'}download=1`;
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = builtFontFilename || `${vfFamilyId || 'avar2-font'}.ttf`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const renderAxis = (a) => (
    <AxisControl
      key={a.tag}
      axis={a}
      value={coords[a.tag] ?? a.default}
      onChange={(v) => setAxis(a.tag, v)}
      treatEmptyAsActive
    />
  );

  if (!fontLoaded) {
    return (
      <div className="preview-tab preview-tab-empty">
        <div>
          <h2>Nothing to preview yet</h2>
          <p>Build the font first — use <strong>Rebuild Font</strong> in the top bar — then this tab renders the live variable font.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="preview-tab">
      <div className="preview-tab-panel">
        <div className="preview-tab-panel-head">
          <h2>Preview</h2>
          <button
            type="button"
            className="preview-reset"
            onClick={resetAll}
            disabled={atDefault}
            title="Reset every axis to its default value"
          >
            Reset
          </button>
        </div>

        <div className="sample-text-section">
          <textarea
            value={sampleText}
            onChange={(e) => onSampleTextChange(e.target.value)}
            className="sample-text-input"
            placeholder="Enter sample text..."
            rows={2}
          />
        </div>

        <div className="font-size-control">
          <label className="axis-name">Font Size: {formatAxisValue(fontSize)}rem</label>
          <input
            type="range"
            min="1"
            max="12"
            step="0.1"
            value={fontSize}
            onChange={(e) => onFontSizeChange(parseFloat(e.target.value))}
            className="axis-slider"
          />
        </div>

        <section className="preview-axis-group">
          <div className="preview-axis-group-head">
            <h3>User axes</h3>
            <span className="preview-axis-group-sub">avar2-mapped</span>
          </div>
          {userAxes.length === 0 ? (
            <p className="preview-axis-empty">
              No avar2-mapped axes declared yet. Add a user-facing axis
              (e.g. Weight) in <strong>AVAR2 MAPPINGS</strong> on the
              Instances tab.
            </p>
          ) : (
            userAxes.map(renderAxis)
          )}
        </section>

        {controlAxes.length > 0 && (
          <section className="preview-axis-group">
            <div className="preview-axis-group-head">
              <h3>Control axes</h3>
              <span className="preview-axis-group-sub">glyph-scoped</span>
            </div>
            {controlAxes.map(renderAxis)}
          </section>
        )}

        {parametricAxes.length > 0 && (
          <section className="preview-axis-group">
            <button
              type="button"
              className="preview-axis-group-toggle"
              onClick={() => setShowParametric(s => !s)}
              aria-expanded={showParametric}
            >
              <span className="preview-axis-caret">{showParametric ? '▾' : '▸'}</span>
              <span className="preview-axis-group-title">Parametric axes</span>
              <span className="preview-axis-group-sub">advanced · overrides the mapping</span>
            </button>
            {showParametric && (
              <div className="preview-axis-group-body">
                {parametricAxes.map(renderAxis)}
              </div>
            )}
          </section>
        )}

        <div className="preview-tab-download">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleDownload}
            disabled={!fontLoaded}
            title="Download the built variable font (.ttf)"
          >
            Download font
          </button>
        </div>
      </div>

      <div className="preview-tab-canvas">
        <div
          className="preview-tab-sample"
          style={{
            fontFamily: `"${vfFamilyId}", sans-serif`,
            fontSize: `${fontSize}rem`,
            fontVariationSettings: fvs,
          }}
        >
          {sampleText || DEFAULT_SAMPLE}
        </div>
      </div>
    </div>
  );
}

export default PreviewTab;
