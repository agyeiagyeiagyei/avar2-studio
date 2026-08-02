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
 *   - is_grade_axis              → Grade (same-advance weight)
 *   - is_control_axis            → Secondary parametric axes
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
  familyName,
  vfFamilyId,
  fontLoaded,
  fontUrl,
  builtFontFilename,
  sampleText,
  onSampleTextChange,
  fontSize,
  onFontSizeChange,
  jumpLocation,
}) {
  const { userAxes, controlAxes, gradeAxes, parametricAxes } = useMemo(() => {
    const user = [];
    const control = [];
    const grade = [];
    const param = [];
    for (const a of (axes || [])) {
      if (a.is_grade_axis) grade.push(a);
      else if (a.is_control_axis) control.push(a);
      else if (a.has_master_coverage === false) user.push(a);
      else param.push(a);
    }
    return { userAxes: user, controlAxes: control, gradeAxes: grade, parametricAxes: param };
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
  const [showParametric, setShowParametric] = useState(true);

  // Coverage findings jump (Header panel, later the Space tab): merge
  // the finding's location into the preview coordinates. Deduped so a
  // re-render with the same object doesn't re-pin the sliders.
  const jumpRef = useRef(null);
  useEffect(() => {
    if (!jumpLocation || jumpLocation === jumpRef.current) return;
    jumpRef.current = jumpLocation;
    setCoords(prev => ({ ...prev, ...jumpLocation }));
  }, [jumpLocation]);

  // Auto optical size — mimics browsers'/Google Fonts'
  // font-optical-sizing:auto: opsz tracks the font size (in pt),
  // clamped to the axis range. On by default; the opsz slider is
  // disabled while linked and resumes from the tracked value when
  // unchecked. Only meaningful for fonts with an opsz user axis.
  const [autoOpsz, setAutoOpsz] = useState(true);
  const opszAxis = userAxes.find(a => a.tag === 'opsz');
  const linkedOpsz = opszAxis
    ? Math.min(opszAxis.max, Math.max(opszAxis.min, roundHalf(remToPt(fontSize))))
    : null;
  const effectiveCoords = useMemo(
    () => (autoOpsz && opszAxis ? { ...coords, opsz: linkedOpsz } : coords),
    [autoOpsz, opszAxis, coords, linkedOpsz]
  );

  // Start each font's preview at the bottom of its opsz range — with
  // auto-opsz on, the opening view is the axis's intended small-size
  // cut, and it matches what a browser would render at that size.
  // Once per font, so later manual font-size moves are never undone.
  const sizeInitRef = useRef(null);
  useEffect(() => {
    if (!opszAxis || sizeInitRef.current === vfFamilyId) return;
    sizeInitRef.current = vfFamilyId;
    onFontSizeChange(ptToRem(opszAxis.min));
  }, [opszAxis, vfFamilyId, onFontSizeChange]);

  // Editable specimen: the canvas IS the text input (the sidebar
  // textarea is gone on this tab). Uncontrolled contentEditable —
  // React must never render its children or the caret resets on
  // every keystroke; this effect pushes only EXTERNAL changes (edits
  // made on the Instances tab, initial load) into the DOM.
  const specimenRef = useRef(null);
  useEffect(() => {
    const el = specimenRef.current;
    if (el && el.textContent !== (sampleText || '')) {
      el.textContent = sampleText || DEFAULT_SAMPLE;
    }
  }, [sampleText]);
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
        const res = await api.getMappedLocation(effectiveCoords);
        setMappedParams(res.mapped || {});
      } catch {
        // No built font / transient error — sliders fall back to inputs.
        setMappedParams({});
      }
    }, 120);
    return () => clearTimeout(mapTimer.current);
  }, [effectiveCoords, fontLoaded]);

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
    () => (axes || []).map(a => `"${a.tag}" ${effectiveCoords[a.tag] ?? a.default}`).join(', '),
    [axes, effectiveCoords]
  );

  // Export options: axes to flag HIDDEN in the exported fvar, and an
  // optional "open here" default captured from the current user-axis
  // sliders (the export is rebuilt so that combination becomes the
  // compiled origin — ranges stay intact).
  const [exportHidden, setExportHidden] = useState(() => new Set());
  const [exportUseDefault, setExportUseDefault] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(null);
  const [showExportModal, setShowExportModal] = useState(false);

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
      // Blob URLs can't carry a query string — the ?download=1 flag is
      // for the server's fetch path only.
      const downloadUrl = fontUrl.startsWith('blob:')
        ? fontUrl
        : `${fontUrl}${fontUrl.includes('?') ? '&' : '?'}download=1`;
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = builtFontFilename || `${vfFamilyId || 'avar2-font'}.ttf`;
      document.body.appendChild(link);
      link.click();
      // Blob-URL downloads cancel if the anchor dies before the download starts.
      setTimeout(() => document.body.removeChild(link), 0);
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
      link.download = builtFontFilename || `${vfFamilyId || 'avar2-font'}.ttf`;
      document.body.appendChild(link);
      link.click();
      // Remove + revoke on the next tick — synchronously revoking the
      // blob can cancel the download before it starts.
      setTimeout(() => {
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
      }, 0);
      setShowExportModal(false);
    } catch (err) {
      setExportError(err.message || 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  const renderAxis = (a) => {
    // While auto-opsz is on, the opsz slider tracks the font size and
    // is display-only — manual input resumes from the tracked value
    // when the toggle is unchecked.
    if (autoOpsz && opszAxis && a.tag === 'opsz') {
      return (
        <div
          key={a.tag}
          className="preview-axis-reflected"
          title="Tracking font size (font-optical-sizing: auto, like Google Fonts) — uncheck Auto optical size to set it manually."
        >
          <AxisControl
            axis={a}
            value={linkedOpsz}
            onChange={(v) => setAxis(a.tag, v)}
            disabled
            treatEmptyAsActive
          />
        </div>
      );
    }
    return (
      <AxisControl
        key={a.tag}
        axis={a}
        value={coords[a.tag] ?? a.default}
        onChange={(v) => setAxis(a.tag, v)}
        treatEmptyAsActive
      />
    );
  };

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
          <label
            className="auto-opsz-toggle"
            title={opszAxis
              ? 'Mimic browsers/Google Fonts: optical size follows the font size (font-optical-sizing: auto)'
              : 'This font has no opsz axis to track'}
          >
            <input
              type="checkbox"
              checked={autoOpsz && !!opszAxis}
              disabled={!opszAxis}
              onChange={(e) => {
                const on = e.target.checked;
                // Resume manual control from the tracked value, not the
                // stale pre-link one.
                if (!on && opszAxis) setAxis('opsz', linkedOpsz);
                setAutoOpsz(on);
              }}
            />
            Auto optical size
          </label>
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

        {gradeAxes.length > 0 && (
          <section className="preview-axis-group">
            <div className="preview-axis-group-head">
              <h3>Grade</h3>
              <span className="preview-axis-group-sub">same advance — no reflow</span>
            </div>
            {gradeAxes.map(renderAxis)}
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
              {parametricPromoted && (
                <span className="preview-axis-group-sub">avar2-mapped — your mappings drive these</span>
              )}
            </button>
            {showParametric && (
              <div className="preview-axis-group-body">
                {parametricAxes.map(renderParametricAxis)}
              </div>
            )}
          </section>
        )}

        {showExportModal && (
          <div className="modal-overlay" onClick={() => !exporting && setShowExportModal(false)}>
            <div className="preview-export-modal" onClick={(e) => e.stopPropagation()}>
              <h3>Export font</h3>
              {/* Specimen: the family name set in the font itself at the
                  current preview location — what the export's resting
                  state will look like when "Set default" is on. */}
              <div
                className="preview-export-specimen"
                style={{
                  fontFamily: fontLoaded && vfFamilyId ? `"${vfFamilyId}", sans-serif` : 'sans-serif',
                  fontVariationSettings: fontLoaded ? fvs : undefined,
                }}
              >
                {familyName || vfFamilyId || 'Aa'}
              </div>
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
                      Set default at{' '}
                      <span className="preview-export-loc">
                        {Object.entries(currentUserLocation).map(([t, v]) => `${t} ${v}`).join(' · ')}
                      </span>
                    </span>
                  </label>
                )}
                {exportError && <div className="preview-export-error">{exportError}</div>}
              </div>
              <div className="preview-export-actions">
                <button
                  type="button"
                  className="btn btn-cancel"
                  onClick={() => setShowExportModal(false)}
                  disabled={exporting}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleDownload}
                  disabled={exporting}
                  title={exportUseDefault
                    ? "Rebuilds the font at the chosen location — takes a few seconds"
                    : "Download the built variable font (.ttf)"}
                >
                  {exporting ? 'Exporting…' : 'Download'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="preview-tab-canvas">
        <div
          ref={specimenRef}
          className="preview-tab-sample preview-tab-sample-editable"
          contentEditable
          suppressContentEditableWarning
          spellCheck={false}
          style={{
            fontFamily: `"${vfFamilyId}", sans-serif`,
            fontSize: `${fontSize}rem`,
            fontVariationSettings: fvs,
          }}
          onInput={(e) => onSampleTextChange(e.currentTarget.textContent)}
          onPaste={(e) => {
            // Plain text only — strip any formatting from pastes.
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text/plain');
            document.execCommand('insertText', false, text);
          }}
          title="Click and type to change the preview text"
        />
      </div>

      <div className="preview-tab-download">
        <button
          type="button"
          className="btn btn-3d"
          onClick={() => { setExportError(null); setShowExportModal(true); }}
          disabled={!fontLoaded}
          title="Download the built variable font — choose hidden axes and an optional opening location first"
        >
          Download font…
        </button>
      </div>
    </div>
  );
}

export default PreviewTab;
