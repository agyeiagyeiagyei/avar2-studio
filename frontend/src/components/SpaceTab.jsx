import React, { useEffect, useMemo, useRef, useState } from 'react';
import './SpaceTab.css';
import { parseFont, parseGlyphNames } from '../fvar.js';
import { gvarRegions } from '../gvar.js';
import { measureAt } from '../fontc-compile';

const PROBE_TEXT = 'adhesion';
const CHIP_GLYPH = 'a';

// N-D → 2D orbit projection (hand-rolled, no deps). The first three
// axes get the familiar 3D orbit (drag). Every dimension beyond that
// is rotated in its (z, x_d) plane — ⇧drag spins the 4th — and
// perspective-collapsed into z before the 3D orbit, so a 4-axis font
// shows a real tesseract, not a truncated cube.
function projectors(ranges, az, el, wAngle, W, H) {
  const norm = (i, v) => (v - ranges[i][0]) / (ranges[i][1] - ranges[i][0]) - 0.5;
  const ca = Math.cos(az), sa = Math.sin(az), ce = Math.cos(el), se = Math.sin(el);
  const cw = Math.cos(wAngle), sw = Math.sin(wAngle);
  return (loc) => {
    let x = norm(0, loc[0]), y = norm(1, loc[1]), z = norm(2, loc[2]);
    for (let d = 3; d < loc.length; d++) {
      const w = norm(d, loc[d]);
      const z2 = z * cw - w * sw, w2 = z * sw + w * cw;
      const s4 = 1.35 / (w2 + 2.1);
      x *= s4; y *= s4; z = z2 * s4;
    }
    // The collapse shrinks the figure toward the center — grow it back.
    const grow = loc.length > 3 ? 1.35 : 1;
    x *= grow; y *= grow; z *= grow;
    const X = x * ca - y * sa, Y1 = x * sa + y * ca;
    const Y = Y1 * ce - z * se, Z = Y1 * se + z * ce;
    const s = 1.9 / (Z + 2.6);
    return [W / 2 + X * s * W * 0.55, H / 2 - Y * s * H * 0.62, Z];
  };
}

const fmtLoc = (tags, loc) => tags.map((t, i) => `${t} ${Math.round(loc[i] * 10) / 10}`).join(' · ');
// Single-letter glyph names render as themselves; longer names are
// shown as text (no reliable name→unicode path without the full AGL).
const probeFor = (glyphName) =>
  glyphName && /^[A-Za-z]$/.test(glyphName) ? glyphName : PROBE_TEXT;

const FINDING_LABELS = {
  'uncovered-corner': 'Missing corner',
  'out-of-range-source': 'Out-of-range source',
  'collapse': 'Collapse',
  'inert-sweep': 'Inert axis',
};

/**
 * The Noordzij cube: the font's design space as an orbitable wireframe
 * — a cube for 3 parametric axes, a tesseract for 4 (extra dimensions
 * rotate in their z–w plane and collapse into the 3D projection;
 * ⇧drag spins them). Masters are
 * blue dots, brace layers grey dots (hover = glyph preview), named
 * instances teal diamonds, the default a ring. Corners carry live
 * glyph specimens at their exact variation settings — red when the
 * audit flags the corner as uncovered (a ghost), labelled "pinned"
 * once held by a corner pin. Clicking any marker applies that
 * location to the probe text on the right — no navigation.
 * The side panel also carries the coverage findings rail: every audit
 * finding with a click-to-probe shortcut, plus the Pin and Drop
 * actions (the header keeps only a count badge that lands here).
 */
function SpaceTab({ axes, coverageFindings = [], coveragePins, fontUrl, vfFamilyId, onPinCorner, onClampOutOfRange }) {
  const [bytes, setBytes] = useState(null);
  const [health, setHealth] = useState({});
  const [probe, setProbe] = useState(null); // {loc, glyphName?, label?}
  const [az, setAz] = useState(-0.62);
  const [el, setEl] = useState(0.42);
  const [wAngle, setWAngle] = useState(0.6);
  const [pinning, setPinning] = useState(null);
  const [dropping, setDropping] = useState(false);
  const dragRef = useRef(null);
  const canvasRef = useRef(null);

  const W = 760, H = 520;
  const paramAxes = (axes || []).filter(a => a.has_master_coverage !== false);
  const tags = paramAxes.map(a => a.tag);
  const ranges = paramAxes.map(a => [a.min, a.max]);
  const locKey = (loc) => loc.map(v => Math.round(v * 100) / 100).join(',');
  const proj = projectors(ranges, az, el, wAngle, W, H);

  const corners = useMemo(() => {
    const out = [];
    for (let i = 0; i < (1 << tags.length); i++) {
      out.push(tags.map((_, j) => ((i >> j) & 1 ? ranges[j][1] : ranges[j][0])));
    }
    return out;
  }, [axes]);

  const ghostLocs = useMemo(() =>
    (coverageFindings || [])
      .filter(f => f.type === 'uncovered-corner' && f.location)
      .map(f => f.location),
  [coverageFindings]);
  const pinLocs = useMemo(() => (coveragePins || []).map(p => p.corner), [coveragePins]);
  // A corner is ghost/pinned when a finding/pin matches it on every
  // axis the finding pins — extra dimensions (e.g. SPAC) don't
  // disqualify: the corner is uncovered at every value of those axes.
  const locationMatches = (loc) => (L) =>
    Object.entries(L).every(([t, v]) => Math.abs((loc[tags.indexOf(t)] ?? NaN) - v) < 0.01);
  const isGhost = (loc) => ghostLocs.some(locationMatches(loc));
  const isPinned = (loc) => pinLocs.some(locationMatches(loc));

  // Fetch the font bytes once (same URL the preview renders from).
  useEffect(() => {
    let live = true;
    if (!fontUrl) return;
    fetch(fontUrl)
      .then(r => r.arrayBuffer())
      .then(b => live && setBytes(new Uint8Array(b)))
      .catch(() => {});
    return () => { live = false; };
  }, [fontUrl]);

  const meta = useMemo(() => (bytes ? parseFont(bytes) : null), [bytes]);
  const glyphNames = useMemo(() => (bytes ? parseGlyphNames(bytes) : []), [bytes]);

  // Masters + braces from the gvar regions (peaks shared by most
  // glyphs are masters; the rest are brace/intermediate sources,
  // tracked with their glyph names for the hover preview).
  const { masters, braces } = useMemo(() => {
    if (!bytes || tags.length < 3) return { masters: [], braces: [] };
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const dir = {};
    const count = view.getUint16(4);
    for (let i = 0; i < count; i++) {
      const r = 12 + i * 16;
      dir[String.fromCharCode(view.getUint8(r), view.getUint8(r + 1), view.getUint8(r + 2), view.getUint8(r + 3))] =
        { offset: view.getUint32(r + 8) };
    }
    if (!dir.gvar) return { masters: [], braces: [] };
    const tuples = gvarRegions(view, dir.gvar, tags.length);
    const usage = new Map(); // peakKey -> Set of glyph names
    for (const t of tuples) {
      const key = [...t.peaks].map(v => v.toFixed(3)).join(',');
      if (!usage.has(key)) usage.set(key, new Set());
      if (glyphNames[t.glyph]) usage.get(key).add(glyphNames[t.glyph]);
    }
    const glyphCount = new Set(tuples.map(t => t.glyph)).size || 1;
    const masters = [], braces = [];
    for (const [key, glyphs] of usage) {
      const peak = key.split(',').map(Number);
      const loc = peak.map((v, i) => {
        const a = paramAxes[i];
        return v >= 0 ? a.default + v * (a.max - a.default) : a.default + v * (a.default - a.min);
      });
      const outOfRange = loc.some((v, i) => v < ranges[i][0] || v > ranges[i][1]);
      (glyphs.size >= glyphCount * 0.5 ? masters : braces).push({ loc, glyphs: [...glyphs], outOfRange });
    }
    return { masters, braces };
  }, [bytes, axes, glyphNames]);

  // Health: outline area at corners + masters (one worker round-trip).
  useEffect(() => {
    if (!bytes || !tags.length) return;
    const locs = [...corners, ...masters.map(m => m.loc)].map(loc =>
      Object.fromEntries(tags.map((t, i) => [t, loc[i]])));
    measureAt(bytes, ['a', 'e', 'o', 'g'], locs)
      .then(areas => {
        const map = {};
        locs.forEach((loc, i) => { map[locKey(loc)] = areas[i]; });
        setHealth(map);
      })
      .catch(() => {});
  }, [bytes, corners, masters]);

  const healthOf = (loc) => health[locKey(loc)];

  // ---- cube rendering (canvas: box, hull, markers; chips are DOM) ---
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || !tags.length) return;
    const ctx = cv.getContext('2d');
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = '#d4d4d8';
    ctx.lineWidth = 1.5;
    // Hypercube edges: corner bitmasks differing in exactly one bit.
    const EDGES = [];
    for (let i = 0; i < (1 << tags.length); i++) {
      for (let j = 0; j < tags.length; j++) {
        const k = i ^ (1 << j);
        if (i < k) EDGES.push([i, k]);
      }
    }
    for (const [u, v] of EDGES) {
      const p = proj(corners[u]), q = proj(corners[v]);
      ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
    }
    if (masters.length) {
      ctx.strokeStyle = 'rgba(29,78,216,.5)';
      ctx.lineWidth = 1.2;
      const M = masters.map(m => proj(m.loc));
      for (let i = 0; i < masters.length; i++) {
        for (let j = i + 1; j < masters.length; j++) {
          const shared = masters[i].loc.filter((v, k) => v === masters[j].loc[k]).length;
          if (shared >= tags.length - 1) {
            ctx.beginPath(); ctx.moveTo(M[i][0], M[i][1]); ctx.lineTo(M[j][0], M[j][1]); ctx.stroke();
          }
        }
      }
    }
    const items = [];
    masters.forEach(m => items.push({ p: proj(m.loc), kind: 'master' }));
    const dflt = paramAxes.map(a => a.default);
    items.push({ p: proj(dflt), kind: 'default' });
    items.sort((a, b) => a.p[2] - b.p[2]);
    for (const it of items) {
      const [x, y] = it.p;
      if (it.kind === 'master') {
        ctx.fillStyle = '#1d4ed8';
        ctx.beginPath(); ctx.arc(x, y, 6, 0, 7); ctx.fill();
      } else if (it.kind === 'default') {
        ctx.strokeStyle = '#18181b'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 8, 0, 7); ctx.stroke();
      }
    }
  });

  if (tags.length < 3) {
    return (
      <div className="space-tab-empty">
        The Space view needs a font with three parametric axes (this one has {tags.length}).
      </div>
    );
  }

  const onCubeDrag = (e) => {
    if (e.buttons !== 1) return;
    const last = dragRef.current;
    if (last) {
      if (e.shiftKey && tags.length > 3) {
        // ⇧drag spins the extra dimensions (the z–w plane rotation).
        setWAngle(wAngle + (e.clientX - last[0]) * 0.008);
      } else {
        setAz(az + (e.clientX - last[0]) * 0.008);
        setEl(Math.max(-1.2, Math.min(1.2, el + (e.clientY - last[1]) * 0.008)));
      }
    }
    dragRef.current = [e.clientX, e.clientY];
  };

  const fvs = (loc) => tags.map((t, i) => `"${t}" ${loc[i]}`).join(', ');
  const chipFvs = probe ? fvs(probe.loc) : undefined;

  return (
    <div className="space-tab">
      <div className="space-cube-wrap">
        <canvas
          ref={canvasRef}
          width={W}
          height={H}
          className="space-cube"
          onMouseMove={onCubeDrag}
          onMouseLeave={() => { dragRef.current = null; }}
        />
        {/* named instances (teal diamonds) */}
        {(meta?.instances || []).map((inst, i) => {
          const loc = tags.map(t => inst.coordinates[t] ?? paramAxes[tags.indexOf(t)].default);
          const [x, y] = proj(loc);
          return (
            <div
              key={`inst-${i}`}
              className="space-instance"
              style={{ left: x, top: y }}
              title={`${inst.name} — ${fmtLoc(tags, loc)}`}
              onClick={() => setProbe({ loc, glyphName: CHIP_GLYPH, label: inst.name })}
            />
          );
        })}
        {/* brace layers (grey dots; hover previews the glyph). Sources
            outside the axis box get a hollow amber marker instead. */}
        {braces.map((b, i) => {
          const [x, y] = proj(b.loc);
          return (
            <div
              key={`brace-${i}`}
              className={`space-brace-dot${b.outOfRange ? ' out-of-range' : ''}`}
              style={{ left: x, top: y }}
              title={`brace layer${b.outOfRange ? ' (out of range)' : ''} — ${b.glyphs.join(', ')} — ${fmtLoc(tags, b.loc)}`}
              onMouseEnter={() => setProbe({ loc: b.loc, glyphName: probeFor(b.glyphs[0]), label: b.glyphs.join(', ') })}
            />
          );
        })}
        {/* axis labels — DOM so they sit above the chips at any orbit */}
        {tags.map((t, i) => {
          const ext = tags.map((_, j) =>
            i === j ? ranges[j][1] + 0.14 * (ranges[j][1] - ranges[j][0]) : ranges[j][0]);
          const [x, y] = proj(ext);
          return (
            <span key={t} className="space-axis-label" style={{ left: x, top: y }}>{t} →</span>
          );
        })}
        {/* corner chips (live specimens; ghost = red, pinned = labelled) */}
        {corners.map((loc) => {
          const [x, y] = proj(loc);
          const key = locKey(loc);
          const ghost = isGhost(loc);
          const pinnedChip = isPinned(loc);
          const h = healthOf(loc);
          return (
            <div
              key={key}
              className={`space-chip${ghost ? ' ghost' : ''}${pinnedChip ? ' pinned' : ''}`}
              style={{ left: x, top: y }}
              title={fmtLoc(tags, loc) + (h !== undefined ? ` · area ${Math.round(h)}` : '')}
              onClick={() => setProbe({ loc, glyphName: CHIP_GLYPH })}
            >
              <span className="space-chip-glyph" style={{
                fontFamily: vfFamilyId ? `"${vfFamilyId}", sans-serif` : 'sans-serif',
                fontVariationSettings: fvs(loc),
              }}>{CHIP_GLYPH}</span>
              {pinnedChip && <span className="space-chip-pinned-label">pinned</span>}
              {ghost && !pinnedChip && onPinCorner && (
                <button
                  className="space-chip-pin"
                  disabled={pinning === key}
                  title="Pin this corner (healthy-edge scaffold)"
                  onClick={(e) => {
                    e.stopPropagation();
                    setPinning(key);
                    Promise.resolve(onPinCorner(Object.fromEntries(tags.map((t, i) => [t, loc[i]]))))
                      .finally(() => setPinning(null));
                  }}
                >
                  {pinning === key ? '…' : 'Pin'}
                </button>
              )}
            </div>
          );
        })}
      </div>
      <div className="space-side">
        <div className="space-side-label">
          {probe
            ? `${probe.label ? `${probe.label} — ` : ''}${fmtLoc(tags, probe.loc)}`
            : 'Click a corner, instance, or brace layer dot'}
        </div>
        <div
          className="space-probe"
          style={chipFvs ? {
            fontFamily: vfFamilyId ? `"${vfFamilyId}", sans-serif` : 'sans-serif',
            fontVariationSettings: chipFvs,
          } : undefined}
        >
          {probe ? probeFor(probe.glyphName) : PROBE_TEXT}
        </div>
        <div className="space-legend">
          <span><i className="dot master" /> master</span>
          <span><i className="dot brace" /> brace layer</span>
          <span><i className="dot out-of-range" /> out of range</span>
          <span><i className="dot instance" /> instance</span>
          <span><i className="dot default" /> default</span>
          <span><i className="dot ghost" /> ghost corner</span>
          <span className="dim">drag to orbit{tags.length > 3 ? ' · ⇧drag spins the 4th axis' : ''} · hover braces for their glyph</span>
        </div>
        {coverageFindings.length > 0 && (
          <div className="space-findings">
            <div className="space-findings-title">
              <span>Coverage findings</span>
              {onClampOutOfRange && coverageFindings.some(f => f.type === 'out-of-range-source') && (
                <button
                  className="coverage-pin-btn"
                  disabled={dropping}
                  title="Drop every source outside the axis box (zero its deltas, rebuild HVAR) — the Glyphs.app/fontmake semantics"
                  onClick={async () => {
                    setDropping(true);
                    try {
                      await onClampOutOfRange();
                    } finally {
                      setDropping(false);
                    }
                  }}
                >
                  {dropping ? 'Dropping…' : 'Drop out-of-range sources'}
                </button>
              )}
            </div>
            {coverageFindings.map((f, i) => (
              <div key={i} className="coverage-finding-row">
                <button
                  className="coverage-finding-jump"
                  disabled={!f.location}
                  onClick={() => f.location && setProbe({
                    loc: tags.map(t => f.location[t] ?? 0),
                    glyphName: CHIP_GLYPH,
                    label: FINDING_LABELS[f.type] || f.type,
                  })}
                >
                  <div className="load-font-item-name">
                    {f.severity === 'fail' ? '✗' : 'i'} {FINDING_LABELS[f.type] || f.type}
                  </div>
                  <div className="load-font-item-subtitle">{f.detail}</div>
                </button>
                {f.type === 'uncovered-corner' && f.location && onPinCorner && (
                  <button
                    className="coverage-pin-btn"
                    title="Hold this corner up with the nearest healthy shape (master semantics)"
                    disabled={pinning === locKey(tags.map(t => f.location[t] ?? 0))}
                    onClick={() => {
                      const key = locKey(tags.map(t => f.location[t] ?? 0));
                      setPinning(key);
                      Promise.resolve(onPinCorner(f.location)).finally(() => setPinning(null));
                    }}
                  >
                    {pinning === locKey(tags.map(t => f.location[t] ?? 0)) ? '…' : 'Pin'}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default SpaceTab;
