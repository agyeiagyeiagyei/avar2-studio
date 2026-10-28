import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import './KinetikernTab.css';
import AxisControl from './AxisControl';
import { api } from '../api';
import { applyAllConfirm, applyAllLabel, fontSizeForStyle, instanceOptionLabel, revertLabel } from '../kinetikern-copy';
import { useBuildStatus } from '../hooks/useBuildStatus';

// Kinetikern — per-instance spacing from Kinetikern2's engine (Mirko
// Velimirovic, MIT). Pick an instance from the dropdown the way the plugin
// picks a master, or use the sliders (the Preview tab's model: user axes
// drive the parametric location through the avar2 mapping, a dragged
// parametric slider overrides it); set the looseness; compare the built
// font (before) with the engine's proposal (after) over the shared sample
// text. Apply bakes the proposal into that instance or location, Revert
// drops it. The engine always spaces the PARAMETRIC location with every
// other axis at its default; the secondary and transform axes only change
// how the panes render.

const PARAM_TAGS = ['XTRA', 'XOPQ', 'YOPQ'];
const CUSTOM = '__custom__';
const roundHalf = v => Math.round(v * 2) / 2;
const remToPt = rem => rem * 12;
const ptToRem = pt => pt / 12;
const formatPt = rem => {
  const pt = roundHalf(remToPt(rem));
  return Number.isInteger(pt) ? String(pt) : pt.toFixed(1);
};

function coordOf(instance, tag) {
  const c = instance?.coordinates || {};
  const v = c[tag] ?? c[tag.toUpperCase()] ?? c[tag.toLowerCase()];
  return v === undefined || v === null || v === '' ? undefined : Number(v);
}

function KinetikernTab({ instances, axes, vfFamilyId, fontLoaded, sampleText, onSampleTextChange, fontSize, onFontSizeChange }) {
  const { building } = useBuildStatus();
  const { userAxes, controlAxes, transformAxes, parametricAxes } = useMemo(() => {
    const user = [];
    const control = [];
    const transform = [];
    const param = [];
    for (const a of (axes || [])) {
      if (a.is_control_axis) control.push(a);
      else if (a.transform_injected) transform.push(a);
      else if (a.is_grade_axis) transform.push(a);
      else if (a.has_master_coverage === false) user.push(a);
      else param.push(a);
    }
    return { userAxes: user, controlAxes: control, transformAxes: transform, parametricAxes: param };
  }, [axes]);
  const parametricTagSet = useMemo(() => new Set(parametricAxes.map(a => a.tag)), [parametricAxes]);
  const userTagSet = useMemo(() => new Set(userAxes.map(a => a.tag)), [userAxes]);
  const defaults = useMemo(() => Object.fromEntries((axes || []).map(a => [a.tag, Number(a.default)])), [axes]);

  const [coords, setCoords] = useState({});          // every axis's input, as the Preview tab keeps it
  const [overrides, setOverrides] = useState(() => new Set());
  const [mappedParams, setMappedParams] = useState({});
  const [showParametric, setShowParametric] = useState(true);
  const [selected, setSelected] = useState(null);    // instance name, or CUSTOM
  const [anchor, setAnchor] = useState(null);        // the last instance picked: Reset returns to it
  const [customName, setCustomName] = useState('Custom');
  const [looseness, setLooseness] = useState(0);
  const [proposal, setProposal] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [stored, setStored] = useState({ instances: [] });
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const solveTimer = useRef(null);
  const mapTimer = useRef(null);

  // seed each axis at its default the first time it appears
  useEffect(() => {
    setCoords(prev => {
      const next = { ...prev };
      let changed = false;
      for (const a of (axes || [])) {
        if (next[a.tag] === undefined) { next[a.tag] = Number(a.default); changed = true; }
      }
      return changed ? next : prev;
    });
  }, [axes]);

  const instance = useMemo(() => instances.find(i => i.name === selected), [instances, selected]);
  const anchorInstance = useMemo(() => instances.find(i => i.name === anchor), [instances, anchor]);

  const pickInstance = useCallback((name) => {
    const inst = instances.find(i => i.name === name);
    setSelected(name);
    if (!inst) return;
    setAnchor(name);
    // K4: a style has an intended size — its opsz coordinate. Picking one moves
    // the (shared) Font Size control onto it, clamped to the slider, so the
    // panes show the style at the size it was drawn for. No opsz → leave the
    // size alone; a manual slider move afterwards wins until the next pick.
    const pt = fontSizeForStyle(inst, coordOf);
    if (pt !== null) onFontSizeChange(ptToRem(pt));
    setCoords(prev => {
      const next = { ...prev };
      for (const a of userAxes) next[a.tag] = coordOf(inst, a.tag) ?? Number(a.default);
      for (const a of parametricAxes) next[a.tag] = Number(a.default);
      return next;
    });
    setOverrides(new Set());
  }, [instances, userAxes, parametricAxes, onFontSizeChange]);

  useEffect(() => {
    if (!selected && instances.length && userAxes.length + parametricAxes.length > 0) pickInstance(instances[0].name);
  }, [instances, selected, userAxes, parametricAxes, pickInstance]);

  // the parametric values the mapping gives the current user-axis inputs
  const userInputs = useMemo(() => {
    const out = {};
    for (const a of userAxes) out[a.tag] = coords[a.tag] ?? Number(a.default);
    return out;
  }, [userAxes, coords]);
  useEffect(() => {
    if (!fontLoaded || userAxes.length === 0) return undefined;
    clearTimeout(mapTimer.current);
    mapTimer.current = setTimeout(async () => {
      try {
        const res = await api.getMappedLocation(userInputs);
        setMappedParams(res.mapped || {});
      } catch {
        setMappedParams({});
      }
    }, 120);
    return () => clearTimeout(mapTimer.current);
  }, [userInputs, fontLoaded, userAxes.length]);

  const parametric = useMemo(() => {
    const out = {};
    for (const a of parametricAxes) {
      const upper = a.tag.toUpperCase();
      out[upper] = overrides.has(a.tag)
        ? Number(coords[a.tag])
        : Number(mappedParams[a.tag] ?? mappedParams[upper] ?? coords[a.tag] ?? a.default);
    }
    return out;
  }, [parametricAxes, overrides, coords, mappedParams]);
  const complete = PARAM_TAGS.every(t => Number.isFinite(parametric[t]));

  const corrected = useMemo(() => new Map(stored.instances.map(e => [e.name, e])), [stored]);
  const applyName = selected === CUSTOM ? customName.trim() : selected;
  const entry = applyName ? corrected.get(applyName) : undefined;

  const reloadStored = useCallback(() => {
    api.getKinetikern().then(setStored).catch(() => {});
  }, []);
  useEffect(() => { reloadStored(); }, [reloadStored]);
  useEffect(() => { if (!building) reloadStored(); }, [building, reloadStored]);
  useEffect(() => {
    if (entry) setLooseness(entry.looseness);
    // K3: `entry` comes from `stored`, which arrives async — keying this on the
    // selection alone left the slider at 0 when the initially selected style
    // was the spaced one.
  }, [selected, entry?.looseness]); // eslint-disable-line react-hooks/exhaustive-deps

  const atAnchor = (nextCoords, nextOverrides) => {
    if (!anchorInstance || nextOverrides.size) return false;
    return userAxes.every(a => Math.abs((nextCoords[a.tag] ?? Number(a.default)) - (coordOf(anchorInstance, a.tag) ?? Number(a.default))) < 1e-6);
  };

  const setAxis = (tag, value) => {
    const nextOverrides = parametricTagSet.has(tag) && !overrides.has(tag) ? new Set(overrides).add(tag) : overrides;
    if (nextOverrides !== overrides) setOverrides(nextOverrides);
    const next = { ...coords, [tag]: value };
    setCoords(next);
    // a user or parametric axis moves the location: it leaves the instance
    // unless it lands back on it; secondary and transform axes only change the view
    if (userTagSet.has(tag) || parametricTagSet.has(tag)) {
      setSelected(atAnchor(next, nextOverrides) && anchor ? anchor : CUSTOM);
    }
  };
  const resetAll = () => {
    setCoords(prev => {
      const next = { ...prev };
      for (const a of [...controlAxes, ...transformAxes, ...parametricAxes]) next[a.tag] = Number(a.default);
      for (const a of userAxes) next[a.tag] = coordOf(anchorInstance, a.tag) ?? Number(a.default);
      return next;
    });
    setOverrides(new Set());
    if (anchor) setSelected(anchor);
  };
  const atRest = useMemo(
    () => atAnchor(coords, overrides) && [...controlAxes, ...transformAxes].every(a => (coords[a.tag] ?? Number(a.default)) === Number(a.default)),
    [coords, overrides, controlAxes, transformAxes, anchorInstance, userAxes], // eslint-disable-line react-hooks/exhaustive-deps
  );

  useEffect(() => {
    if (!fontLoaded || building || !complete) { if (!complete) setProposal(null); return undefined; }
    const target = selected && selected !== CUSTOM ? { instance: selected } : { coordinates: parametric };
    clearTimeout(solveTimer.current);
    solveTimer.current = setTimeout(async () => {
      setLoading(true);
      try {
        setProposal(await api.proposeKinetikern({ ...target, looseness }));
        setError(null);
      } catch (err) {
        setError(err.message);
        setProposal(null);
      } finally {
        setLoading(false);
      }
    }, 150);
    return () => clearTimeout(solveTimer.current);
  }, [selected, parametric, complete, looseness, fontLoaded, building]);

  async function run(action, message) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setStatus(message);
      reloadStored();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  const apply = () => run(
    () => api.applyKinetikern(applyName, looseness, selected === CUSTOM ? parametric : undefined),
    `Applied to ${applyName} — rebuilding`,
  );
  const revert = () => run(() => api.revertKinetikern(applyName), `Reverted ${applyName} — rebuilding`);
  const applyAll = () => {
    if (!window.confirm(applyAllConfirm({ instances, stored, looseness }))) return;
    run(() => api.applyKinetikernAll(looseness), 'Applied to all instances — rebuilding');
  };

  // The panes render exactly where the engine measured: the parametric
  // location pinned, user axes at default so the mapping adds nothing,
  // secondary and transform axes as the sliders have them.
  const fvs = useMemo(() => {
    const parts = [];
    for (const a of (axes || [])) {
      const upper = a.tag.toUpperCase();
      let v = Number(a.default);
      if (parametricTagSet.has(a.tag) && Number.isFinite(parametric[upper])) v = parametric[upper];
      else if (!userTagSet.has(a.tag) && !parametricTagSet.has(a.tag)) v = Number(coords[a.tag] ?? a.default);
      parts.push(`"${a.tag}" ${v}`);
    }
    return parts.join(', ');
  }, [axes, parametricTagSet, userTagSet, parametric, coords]);
  const byCodePoint = useMemo(() => {
    const m = new Map();
    (proposal?.glyphs || []).forEach(g => m.set(g.unicode, g));
    return m;
  }, [proposal]);
  const upm = proposal?.upm || 1000;

  function renderText(side) {
    return (sampleText || '').split('\n').map((line, li) => (
      <div key={li} className="kk-line">
        {line.split(/(\s+)/).map((tok, ti) => (
          tok.trim() === '' ? <span key={ti}>{tok}</span> : (
            <span key={ti} className="kk-word">
              {Array.from(tok).map((ch, ci) => {
                const g = byCodePoint.get(ch.codePointAt(0));
                let dl = 0;
                let dr = 0;
                if (g && g.spaced) {
                  if (side === 'after') {
                    dl = g.new.lsb - g.cur.lsb;
                    dr = g.new.rsb - g.cur.rsb;
                  } else if (g.applied) {
                    dl = -g.applied[0];
                    dr = -g.applied[1];
                  }
                }
                const style = (dl || dr) ? { marginLeft: `${dl / upm}em`, marginRight: `${dr / upm}em` } : undefined;
                return <span key={ci} style={style}>{ch}</span>;
              })}
            </span>
          )
        ))}
      </div>
    ));
  }

  const renderAxis = (a) => (
    <AxisControl
      key={a.tag}
      axis={a}
      value={Number(coords[a.tag] ?? a.default)}
      onChange={(v) => setAxis(a.tag, v)}
      treatEmptyAsActive
    />
  );
  const renderParametricAxis = (a) => {
    const reflected = !overrides.has(a.tag);
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
          value={Number(parametric[a.tag.toUpperCase()] ?? a.default)}
          onChange={(v) => setAxis(a.tag, v)}
          treatEmptyAsActive
        />
      </div>
    );
  };

  const stats = proposal?.stats;
  const fmt = v => (v === undefined || v === null || !Number.isFinite(v) ? '–' : Math.round(v * 10) / 10);
  const textStyle = {
    fontFamily: fontLoaded && vfFamilyId ? `"${vfFamilyId}", sans-serif` : 'sans-serif',
    fontVariationSettings: fontLoaded && fvs ? fvs : undefined,
    fontSize: `${fontSize}rem`,
  };
  const canApply = !!proposal && !busy && !building && !!applyName && complete;
  const parametricPromoted = userAxes.length === 0 && parametricAxes.length > 0;

  return (
    <div className="kinetikern-tab">
      <div className="preview-tab-panel">
        <div className="footer-controls">
          <button
            type="button"
            className="preview-reset"
            onClick={resetAll}
            disabled={atRest}
            title={anchor ? `Back to ${anchor}, every other axis at its default` : 'Reset every axis to its default value'}
          >
            Reset
          </button>
        </div>
        {!parametricPromoted && (
          <section className="preview-axis-group">
            <div className="preview-axis-group-head">
              <h3>User axes</h3>
              <span className="preview-axis-group-sub">avar2-mapped</span>
            </div>
            {userAxes.length === 0 ? (
              <p className="preview-axis-empty">No avar2-mapped axes declared yet.</p>
            ) : (
              userAxes.map(renderAxis)
            )}
          </section>
        )}
        {controlAxes.length > 0 && (
          <section className="preview-axis-group">
            <div className="preview-axis-group-head">
              <h3>Secondary parametric axes</h3>
              <span className="preview-axis-group-sub">preview only</span>
            </div>
            {controlAxes.map(renderAxis)}
          </section>
        )}
        {transformAxes.length > 0 && (
          <section className="preview-axis-group">
            <div className="preview-axis-group-head">
              <h3>Transform axes</h3>
              <span className="preview-axis-group-sub">preview only</span>
            </div>
            {transformAxes.map(renderAxis)}
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
              <span className="preview-axis-group-sub">{parametricPromoted ? 'avar2-mapped' : 'what the engine spaces'}</span>
            </button>
            {showParametric && (
              <div className="preview-axis-group-body">
                {parametricAxes.map(renderParametricAxis)}
              </div>
            )}
          </section>
        )}
      </div>

      <div className="kk-main">
        <div className="kk-head">
          <select
            className="kk-instance"
            value={selected || ''}
            onChange={e => (e.target.value === CUSTOM ? setSelected(CUSTOM) : pickInstance(e.target.value))}
            aria-label="Instance"
          >
            {instances.map(i => (
              <option key={i.name} value={i.name}>{instanceOptionLabel(i.name, corrected.get(i.name))}</option>
            ))}
            <option value={CUSTOM}>{corrected.has(customName.trim()) ? instanceOptionLabel('Custom location', corrected.get(customName.trim())) : 'Custom location'}</option>
          </select>
          {selected === CUSTOM && (
            <input
              className="kk-custom-name"
              value={customName}
              onChange={e => setCustomName(e.target.value)}
              placeholder="Name for this location"
              aria-label="Name for this location"
            />
          )}
          <span className="kk-coords">
            {userAxes.map(a => `${a.tag} ${fmt(Number(coords[a.tag] ?? a.default))}`).join(' · ')}
            {userAxes.length > 0 ? '  ·  ' : ''}
            {PARAM_TAGS.map(t => `${t} ${fmt(parametric[t])}`).join(' · ')}
          </span>
        </div>

        <div className="kk-controls">
          <label className="kk-looseness">
            <span className="kk-looseness-label">Looseness</span>
            <span className="kk-looseness-end">tight</span>
            <input
              type="range"
              aria-label="Looseness"
              min="-1"
              max="1"
              step="0.05"
              value={looseness}
              onChange={e => setLooseness(parseFloat(e.target.value))}
            />
            <span className="kk-looseness-end">loose</span>
            <span className="kk-looseness-value">{looseness > 0 ? '+' : ''}{looseness.toFixed(2)}</span>
          </label>
          <button type="button" className="kk-btn primary" disabled={!canApply} onClick={apply}>Apply</button>
          <button type="button" className="kk-btn" disabled={!entry || busy || building} onClick={revert}>{revertLabel(applyName)}</button>
          <button type="button" className="kk-btn" disabled={busy || building || !instances.length} onClick={applyAll}>{applyAllLabel(looseness)}</button>
          <textarea
            className="kk-sample"
            value={sampleText}
            onChange={e => onSampleTextChange(e.target.value)}
            rows={1}
            spellCheck={false}
            aria-label="Sample text"
          />
        </div>

        <div className="kk-status" aria-live="polite">
          {error ? <span className="kk-error">{error}</span> : (
            <>
              {stats && (
                <span>
                  {stats.spaced} of {stats.glyphs} glyphs spaced · mean {stats.mean_lsb_delta_per_1000 >= 0 ? '+' : ''}{stats.mean_lsb_delta_per_1000} / {stats.mean_rsb_delta_per_1000 >= 0 ? '+' : ''}{stats.mean_rsb_delta_per_1000} per 1000 em · {stats.ms} ms
                </span>
              )}
              {entry && <span> · applied at looseness {entry.looseness > 0 ? '+' : ''}{entry.looseness.toFixed(2)}</span>}
              {!entry && proposal && <span> · not applied</span>}
              {loading && <span> · solving…</span>}
              {building && <span> · rebuilding…</span>}
              {status && <span> · {status}</span>}
            </>
          )}
        </div>

        {/* K4: the size control belongs with the sample text it sizes, not in
            the left column where the designer never found it. */}
        <div className="font-size-control">
          <label className="axis-name">Font Size: {formatPt(fontSize)}pt</label>
          <input
            type="range"
            aria-label="Font size"
            min="9"
            max="144"
            step="0.5"
            value={roundHalf(remToPt(fontSize))}
            onChange={(e) => onFontSizeChange(ptToRem(parseFloat(e.target.value)))}
            className="axis-slider"
          />
        </div>

        <div className="kk-panes">
          <section className="kk-pane">
            <div className="kk-pane-label">Before · built font</div>
            <div className="kk-text" style={textStyle}>{renderText('before')}</div>
          </section>
          <section className="kk-pane">
            <div className="kk-pane-label">After · {entry && proposal && stats && Math.abs(stats.mean_lsb_delta_per_1000) < 0.5 ? 'as applied' : 'proposal'}</div>
            <div className="kk-text" style={textStyle}>{renderText('after')}</div>
          </section>
        </div>
      </div>
    </div>
  );
}

export default KinetikernTab;
