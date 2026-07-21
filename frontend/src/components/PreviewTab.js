import React, { useState, useEffect, useMemo, useRef } from 'react';
import './PreviewTab.css';
import AxisControl from './AxisControl';
import { api } from '../api';

// Font size stays in rem internally; the slider presents it in pt
// (12pt = 1rem at the default 16px root — same convention as Sidebar).
const remToPt = (rem) => rem * 12;
const ptToRem = (pt) => pt / 12;
const roundHalf = (v) => Math.round(v * 2) / 2;
const formatPt = (rem) => {
  const pt = roundHalf(remToPt(rem));
  return Number.isInteger(pt) ? String(pt) : pt.toFixed(1);
};

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
  avar2Error,
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

  // A parametric-only font (avar2 mappings whose in: axes ARE the
  // parametric axes) has no dedicated user axes — the avar2 table
  // remaps the parametric axes themselves. In that world the
  // parametric group IS the avar2 surface, so promote it to a primary
  // always-open section (transform-injected axes like SPAC live there
  // too) instead of hiding everything behind the "advanced" toggle
  // under a misleading "no avar2-mapped axes" empty state.
  const parametricPromoted = userAxes.length === 0 && parametricAxes.length > 0;

  const [coords, setCoords] = useState({});
  const [showParametric, setShowParametric] = useState(false);
  // Post-mapping axis values from /api/mapped-location: the effective
  // location after the built font's avar table is applied to the
  // current inputs. Non-overridden parametric sliders DISPLAY these,
  // so they visibly follow the avar2 mappings as wght/opsz move.
  // Display-only — the font applies the real mapping while rendering;
  // `coords` (the inputs) is what feeds font-variation-settings.
  const [mappedParams, setMappedParams] = useState({});
  // Parametric axes the designer has dragged directly. An override
  // stops that axis reflecting the mapping (its input value shows
  // instead) until Reset.
  const [overrides, setOverrides] = useState(() => new Set());
  const mapTimer = useRef(null);

  const parametricTagSet = useMemo(
    () => new Set(parametricAxes.map(a => a.tag)),
    [parametricAxes]
  );

  useEffect(() => {
    if (!fontLoaded) return undefined;
    clearTimeout(mapTimer.current);
    mapTimer.current = setTimeout(async () => {
      try {
        const res = await api.getMappedLocation(coords);
        setMappedParams(res.mapped || {});
      } catch {
        // No built font / transient error — sliders fall back to inputs.
        setMappedParams({});
      }
    }, 120);
    return () => clearTimeout(mapTimer.current);
  }, [coords, fontLoaded]);

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

  const setAxis = (tag, value) => {
    // Dragging a parametric slider is an explicit override: it stops
    // following the mapping and becomes a direct input.
    if (parametricTagSet.has(tag)) {
      setOverrides(prev => (prev.has(tag) ? prev : new Set(prev).add(tag)));
    }
    setCoords(prev => ({ ...prev, [tag]: value }));
  };

  const resetAll = () => {
    const next = {};
    for (const a of (axes || [])) next[a.tag] = a.default;
    setCoords(next);
    setOverrides(new Set());
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

  // Export options: axes to flag HIDDEN in the exported fvar, and an
  // optional "open here" default captured from the current user-axis
  // sliders (the export is rebuilt so that combination becomes the
  // compiled origin — ranges stay intact).
  const [exportHidden, setExportHidden] = useState(() => new Set());
  const [exportUseDefault, setExportUseDefault] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(null);

  const toggleExportHidden = (tag) => setExportHidden(prev => {
    const next = new Set(prev);
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    return next;
  });

  const currentUserLocation = useMemo(
    () => Object.fromEntries(userAxes.map(a => [a.tag, coords[a.tag] ?? a.default])),
    [userAxes, coords]
  );

  const handleDownload = async () => {
    if (!fontUrl) return;
    const plainDownload = exportHidden.size === 0 && !exportUseDefault;
    if (plainDownload) {
      const downloadUrl = `${fontUrl}${fontUrl.includes('?') ? '&' : '?'}download=1`;
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = builtFontFilename || `${vfFamilyId || 'avar2-font'}.ttf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      return;
    }
    setExporting(true);
    setExportError(null);
    try {
      const blob = await api.exportFont({
        hidden_axes: [...exportHidden],
        default_location: exportUseDefault ? currentUserLocation : null,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      const base = (builtFontFilename || `${vfFamilyId || 'avar2-font'}.ttf`).replace(/\.ttf$/i, '');
      const suffix = exportUseDefault
        ? '-at-' + Object.entries(currentUserLocation).map(([t, v]) => `${t}${v}`).join('-')
        : '';
      link.download = `${base}${suffix}.ttf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err.message || 'Export failed');
    } finally {
      setExporting(false);
    }
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

  // Parametric sliders reflect the avar2 mapping unless overridden:
  // the displayed value is the post-mapping location, so they move as
  // the user drags wght/opsz above.
  const renderParametricAxis = (a) => {
    const reflected = !overrides.has(a.tag);
    const value = reflected
      ? (mappedParams[a.tag] ?? coords[a.tag] ?? a.default)
      : (coords[a.tag] ?? a.default);
    return (
      <div
        key={a.tag}
        className={reflected ? 'preview-axis-reflected' : 'preview-axis-overridden'}
        title={reflected
          ? 'Following the avar2 mapping — drag to override this axis directly.'
          : 'Manually overridden — Reset to follow the mapping again.'}
      >
        <AxisControl
          axis={a}
          value={value}
          onChange={(v) => setAxis(a.tag, v)}
          treatEmptyAsActive
        />
      </div>
    );
  };

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
          <label className="axis-name">Font Size: {formatPt(fontSize)}pt</label>
          <input
            type="range"
            min="9"
            max="144"
            step="0.5"
            value={roundHalf(remToPt(fontSize))}
            onChange={(e) => onFontSizeChange(ptToRem(parseFloat(e.target.value)))}
            className="axis-slider"
          />
        </div>

        {avar2Error && (
          <div
            className="preview-avar2-warning"
            title={avar2Error}
          >
            ⚠ The avar2 build is failing, so this preview is the plain
            build — mapped axes are missing. Hover for the error.
          </div>
        )}

        {!parametricPromoted && (
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
        )}

        {controlAxes.length > 0 && (
          <section className="preview-axis-group">
            <div className="preview-axis-group-head">
              <h3>Secondary parametric axes</h3>
              <span className="preview-axis-group-sub">glyph-scoped</span>
            </div>
            {controlAxes.map(renderAxis)}
          </section>
        )}

        {parametricAxes.length > 0 && (parametricPromoted ? (
          <section className="preview-axis-group">
            <div className="preview-axis-group-head">
              <h3>Parametric axes</h3>
              <span className="preview-axis-group-sub">avar2-mapped — your mappings drive these</span>
            </div>
            {parametricAxes.map(renderParametricAxis)}
          </section>
        ) : (
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
                {parametricAxes.map(renderParametricAxis)}
              </div>
            )}
          </section>
        ))}

        <div className="preview-tab-download">
          <div className="preview-export-options">
            <div className="preview-export-row">
              <span
                className="preview-export-label"
                title="Hidden axes keep working via font-variation-settings but don't appear in font pickers or design apps."
              >
                Hide on export
              </span>
              <div className="preview-export-chips">
                {(axes || []).map(a => (
                  <button
                    key={a.tag}
                    type="button"
                    className={`preview-export-chip${exportHidden.has(a.tag) ? ' on' : ''}`}
                    onClick={() => toggleExportHidden(a.tag)}
                    title={exportHidden.has(a.tag)
                      ? `${a.tag} will be flagged hidden in the exported font. Click to unhide.`
                      : `Flag ${a.tag} as hidden in the exported font.`}
                  >
                    {a.tag}
                  </button>
                ))}
              </div>
            </div>
            {userAxes.length > 0 && (
              <label
                className="preview-export-default"
                title="Rebuilds the export so its resting state IS this style: parametric defaults move to the mapped location and the avar2 table is regenerated around it. Every axis range stays intact."
              >
                <input
                  type="checkbox"
                  checked={exportUseDefault}
                  onChange={(e) => setExportUseDefault(e.target.checked)}
                />
                <span>
                  Open at current location{' '}
                  <span className="preview-export-loc">
                    {Object.entries(currentUserLocation).map(([t, v]) => `${t} ${v}`).join(' · ')}
                  </span>
                </span>
              </label>
            )}
            {exportError && <div className="preview-export-error">{exportError}</div>}
          </div>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleDownload}
            disabled={!fontLoaded || exporting}
            title={exportHidden.size === 0 && !exportUseDefault
              ? "Download the built variable font (.ttf)"
              : "Export with the options above (a relocated default rebuilds the font — takes a few seconds)"}
          >
            {exporting ? 'Exporting…' : 'Download font'}
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
