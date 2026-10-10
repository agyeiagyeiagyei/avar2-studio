import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import './TesterTab.css';
import AxisControl from './AxisControl';

// ASCII-only default text: the bundled examples subset to A-Z + a-z +
// 0-9 + space (same reason as App's DEFAULT_SAMPLE_TEXT).
const DEFAULT_TEXT = 'The Quick Brown Fox Jumps Over The Lazy Dog 0123456789';

// The default batch covers sans/serif/mono on macOS; elsewhere missing
// families simply fall back and get the "not installed" hint. The batch
// is a default, not a guarantee — the user can add/remove names.
const DEFAULT_SYSTEM_FONT_NAMES = [
  'system-ui', 'Helvetica Neue', 'Arial', 'SF Pro Text', 'Georgia',
  'Times New Roman', 'New York', 'SF Mono', 'Menlo', 'Courier New',
];

const LINE_HEIGHTS = [1.1, 1.25, 1.4, 1.6, 1.8, 2.0];

const DEFAULT_STYLE = { font: { kind: 'preview' }, bold: false, italic: false, tracking: 0 };

const DEFAULT_STATE = {
  doc: { blocks: [{ align: 'left', lineHeight: 1.4, runs: [{ text: DEFAULT_TEXT, ...DEFAULT_STYLE }] }] },
  typingStyle: DEFAULT_STYLE,
  systemFonts: DEFAULT_SYSTEM_FONT_NAMES.map(name => ({ name, label: name })),
};

const fontInstalled = (name) => {
  try {
    return document.fonts.check(`12px "${name}"`);
  } catch {
    return false;
  }
};

const blockText = (blk) => blk.runs.map(r => r.text).join('');

const styleOf = (run) => ({ font: run.font, bold: run.bold, italic: run.italic, tracking: run.tracking });

const sameStyle = (a, b) =>
  a.bold === b.bold && a.italic === b.italic && a.tracking === b.tracking &&
  a.font.kind === b.font.kind && (a.font.kind === 'preview' || a.font.name === b.font.name);

// Split the run covering (b, off) at that offset. No-op at boundaries.
const splitRunAt = (blocks, b, off) => blocks.map((blk, bi) => {
  if (bi !== b) return blk;
  let cum = 0;
  const runs = [];
  for (const run of blk.runs) {
    const len = run.text.length;
    if (off > cum && off < cum + len) {
      runs.push({ ...run, text: run.text.slice(0, off - cum) },
        { ...run, text: run.text.slice(off - cum) });
    } else {
      runs.push(run);
    }
    cum += len;
  }
  return { ...blk, runs };
});

// Merge adjacent runs with identical style (compacts browser splits).
const mergeRuns = (blocks) => blocks.map(blk => {
  const runs = [];
  for (const r of blk.runs) {
    const p = runs[runs.length - 1];
    if (p && sameStyle(p, r)) p.text += r.text;
    else runs.push({ ...r });
  }
  return { ...blk, runs: runs.length ? runs : [{ text: '', ...DEFAULT_STYLE }] };
});

// Apply a run-style patch to every run intersecting [s, e) (model
// coordinates: block index + char offset within the block).
const patchRange = (blocks, s, e, patch) => {
  let next = splitRunAt(blocks, s.b, s.off);
  next = splitRunAt(next, e.b, e.off);
  return next.map((blk, bi) => {
    if (bi < s.b || bi > e.b) return blk;
    const sOff = bi === s.b ? s.off : 0;
    const eOff = bi === e.b ? e.off : blockText(blk).length;
    let cum = 0;
    const runs = blk.runs.map(run => {
      const rs = cum;
      cum += run.text.length;
      return cum > sOff && rs < eOff ? { ...run, ...patch } : run;
    });
    return { ...blk, runs };
  });
};

// Insert a zero-length "marker" run carrying `style` at (b, off), so the
// next typed text takes the new style (the word-processor typing-style
// behavior for a collapsed caret mid-run).
const insertMarker = (doc, b, off, style) => {
  const blocks = splitRunAt(doc.blocks, b, off);
  const block = blocks[b];
  let idx = 0;
  if (off > 0) {
    let cum = 0;
    idx = block.runs.length;
    for (let i = 0; i < block.runs.length; i++) {
      cum += block.runs[i].text.length;
      if (cum === off) { idx = i + 1; break; }
    }
  }
  const runs = [...block.runs.slice(0, idx), { text: '', ...style }, ...block.runs.slice(idx)];
  return { ...doc, blocks: blocks.map((blk, i) => (i === b ? { ...blk, runs } : blk)) };
};

/**
 * Tester: ONE editable document (contentEditable) set in the built font
 * and/or a batch of locally installed system fonts.
 *
 * Model: doc.blocks[] → { align, lineHeight, runs[] }; a run is
 * { text, font, bold, italic, tracking } with font = { kind: 'preview' }
 * | { kind: 'system', name }. A block is a paragraph (Enter splits).
 *
 * Rendering/editing: the editable's interior is synced IMPERATIVELY
 * (React never reconciles inside the contentEditable — the browser
 * mutates those nodes on every edit, and diffing a browser-mutated
 * subtree crashes on removeChild). Input commits come from the DOM
 * itself, so their re-render skips the re-sync; every other model change
 * re-renders the interior wholesale, then restores the saved selection.
 * On input the model is rebuilt FROM the DOM (run styles looked up via
 * the data-b / data-r attributes — Chrome's Enter/Backspace node splits
 * preserve them), with the selection kept in (block, char-offset)
 * coordinates, which survive the run renumbering that rebuilds and
 * merges cause.
 *
 * Toolbar: selection-driven. With a range it patches the covered runs
 * (splitting at the boundaries); collapsed, it restyles a zero-length
 * marker run inserted at the caret so the next typed text takes the new
 * style — runIndexAt/modelPointToDom let an empty run AT a boundary win,
 * which is what keeps typing inside the marker.
 *
 * B/I are plain CSS font-weight: 700 / font-style: italic on the run —
 * for the preview font that is SYNTHETIC styling, and CSS gives
 * font-variation-settings wght precedence over font-weight (B is inert
 * there by design): the wght slider is the precise control. The axis
 * sliders (local state, seeded at defaults — PreviewTab's slider state
 * is component-local and deliberately not shared) feed
 * font-variation-settings on preview-font runs ONLY; system-font runs
 * never see them.
 *
 * Props:
 *   axes                            — full fvar axis list from /api/axes
 *   vfFamilyId                      — FontFace family id (registered by App)
 *   fontLoaded                      — has the built font loaded?
 *   fontSize                        — shared with the other tabs (rem)
 *   testerState / onTesterStateChange — { doc, typingStyle, systemFonts },
 *                                     lifted to App (static sessions
 *                                     persist it; in-memory in server mode)
 */
function TesterTab({ axes, vfFamilyId, fontLoaded, fontSize, testerState, onTesterStateChange }) {
  // Normalize to the known keys: a record written by an older shape
  // drops its orphans on the next persist.
  const state = {
    doc: testerState?.doc?.blocks?.length ? testerState.doc : DEFAULT_STATE.doc,
    typingStyle: testerState?.typingStyle || DEFAULT_STATE.typingStyle,
    systemFonts: testerState?.systemFonts || DEFAULT_STATE.systemFonts,
  };
  const { doc, typingStyle, systemFonts } = state;

  const editableRef = useRef(null);
  const docRef = useRef(doc);
  docRef.current = doc;
  const typingStyleRef = useRef(typingStyle);
  typingStyleRef.current = typingStyle;
  // Selection in model coordinates: savedSel is the last known one (what
  // toolbar actions apply to), pendingSel is restored after a commit's
  // re-render (the DOM is rebuilt from the model, so the Range must be
  // re-pointed).
  const savedSelRef = useRef(null);
  const pendingSelRef = useRef(null);

  const [coords, setCoords] = useState({});
  const [reflect, setReflect] = useState(() => ({ ...typingStyle, align: 'left', lineHeight: 1.4 }));
  const [newFontName, setNewFontName] = useState('');

  const commit = (patch) => onTesterStateChange({ ...state, ...patch });

  // Seed each axis slider at its default the first time it appears.
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

  // font-variation-settings uses the exact fvar tag, case-sensitively
  // (same rule as PreviewTab).
  const fvs = useMemo(
    () => (axes || []).map(a => `"${a.tag}" ${coords[a.tag] ?? a.default}`).join(', '),
    [axes, coords]
  );

  // ---- model <-> DOM selection mapping ---------------------------------------
  //
  // Model points are { b, off }: block index + char offset within the
  // block's full text — invariant under the run renumbering that rebuilds
  // and merges cause (run indices are not).

  const domPointToModel = (node, offset) => {
    const root = editableRef.current;
    if (!root) return { b: 0, off: 0 };
    if (node === root) {
      const b = Math.max(0, Math.min(offset, root.children.length - 1));
      return { b, off: 0 };
    }
    let el = node.nodeType === 3 ? node.parentElement : node;
    while (el && el.parentElement !== root) el = el.parentElement;
    if (!el) return { b: 0, off: 0 };
    const b = [...root.children].indexOf(el);
    // Char offset within the block: text length of the range up to the point.
    const r = document.createRange();
    r.selectNodeContents(el);
    try {
      r.setEnd(node, offset);
    } catch {
      return { b, off: 0 };
    }
    return { b: Math.max(0, b), off: r.toString().length };
  };

  const selectionToModel = () => {
    const root = editableRef.current;
    const sel = window.getSelection();
    if (!root || !sel.rangeCount) return null;
    if (!root.contains(sel.anchorNode) || !root.contains(sel.focusNode)) return null;
    return {
      anchor: domPointToModel(sel.anchorNode, sel.anchorOffset),
      focus: domPointToModel(sel.focusNode, sel.focusOffset),
    };
  };

  // Model point → DOM point. Affinity: an offset landing exactly on a
  // boundary resolves INTO the following run when that run is empty (the
  // marker run must win, or typing would escape it); otherwise it stays
  // at the end of the run before it (typing continues its style).
  const modelPointToDom = ({ b, off }) => {
    const root = editableRef.current;
    const blocks = root ? [...root.children] : [];
    const blockEl = blocks[Math.max(0, Math.min(b, blocks.length - 1))];
    if (!blockEl) return null;
    const spans = [...blockEl.childNodes].filter(n => n.nodeType === 1 && n.tagName !== 'BR');
    let cum = 0;
    for (let i = 0; i < spans.length; i++) {
      const len = (spans[i].textContent || '').length;
      if (off < cum + len) return { node: spans[i].firstChild || spans[i], off: off - cum };
      if (off === cum + len) {
        const next = spans[i + 1];
        if (next && (next.textContent || '').length === 0) return { node: next, off: 0 };
        return { node: spans[i].firstChild || spans[i], off: len };
      }
      cum += len;
    }
    return { node: blockEl, off: blockEl.childNodes.length };
  };

  // The run a collapsed caret at (block, off) would type into — same
  // affinity rule as modelPointToDom.
  const runIndexAt = (block, off) => {
    let cum = 0;
    for (let i = 0; i < block.runs.length; i++) {
      const len = block.runs[i].text.length;
      if (off < cum + len) return i;
      if (off === cum + len) {
        const next = block.runs[i + 1];
        if (next && next.text.length === 0) return i + 1;
        return i;
      }
      cum += len;
    }
    return block.runs.length - 1;
  };

  // Reflect the caret run's style in the toolbar — but never re-render
  // for an unchanged style: a re-render here is what used to re-enter the
  // sync effect and re-set the selection mid-gesture, which left Chrome's
  // editor confused about caret moves (End/Home ignored after a patch).
  const reflectAt = (sel) => {
    const d = docRef.current;
    const block = d.blocks[Math.min(sel.anchor.b, d.blocks.length - 1)];
    if (!block) return;
    const ri = runIndexAt(block, Math.min(sel.anchor.off, blockText(block).length));
    const run = block.runs[ri];
    if (!run) return;
    const next = { ...styleOf(run), align: block.align, lineHeight: block.lineHeight };
    setReflect(prev => (
      prev.align === next.align && prev.lineHeight === next.lineHeight && sameStyle(prev, next)
        ? prev
        : next
    ));
  };

  // Track the selection while it lives in the editable. Toolbar actions
  // re-read the LIVE selection first (a Range survives the focus move to
  // the toolbar); this listener + savedSel is the fallback for real
  // browsers, and the onKeyUp/onMouseUp refresh covers environments where
  // selectionchange under-reports (headless CDP input fires it barely).
  const refreshSelection = () => {
    const modelSel = selectionToModel();
    if (!modelSel) return;
    savedSelRef.current = modelSel;
    reflectAt(modelSel);
  };
  useEffect(() => {
    document.addEventListener('selectionchange', refreshSelection);
    return () => document.removeEventListener('selectionchange', refreshSelection);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The editable's interior is synced IMPERATIVELY, never via React
  // children: the browser mutates those nodes on every edit, and React
  // reconciling a browser-mutated subtree crashes (removeChild on a node
  // that is no longer there). handleInput commits come from the DOM
  // itself, so their re-render skips the re-sync (the DOM is already
  // exactly the rebuilt model); every other model change re-renders the
  // interior wholesale, then the saved selection is restored.
  const skipSyncRef = useRef(false);
  useLayoutEffect(() => {
    const root = editableRef.current;
    if (!root) return;
    const skipped = skipSyncRef.current;
    if (skipped) {
      skipSyncRef.current = false;
    } else {
      const frag = document.createDocumentFragment();
      doc.blocks.forEach((blk, bi) => {
        const div = document.createElement('div');
        div.className = 'tester-block';
        div.dataset.b = bi;
        div.style.textAlign = blk.align;
        div.style.lineHeight = String(blk.lineHeight);
        blk.runs.forEach((run, ri) => {
          const span = document.createElement('span');
          span.dataset.b = bi;
          span.dataset.r = ri;
          span.dataset.kind = run.font.kind;
          span.style.fontFamily = run.font.kind === 'preview' ? `"${vfFamilyId}", sans-serif` : `"${run.font.name}"`;
          if (run.font.kind === 'preview' && fvs) span.style.fontVariationSettings = fvs;
          span.style.fontWeight = run.bold ? '700' : '400';
          span.style.fontStyle = run.italic ? 'italic' : 'normal';
          if (run.tracking) span.style.letterSpacing = `${run.tracking}em`;
          span.textContent = run.text;
          div.appendChild(span);
        });
        if (blockText(blk) === '') div.appendChild(document.createElement('br'));
        frag.appendChild(div);
      });
      root.replaceChildren(frag);
    }
    const pending = pendingSelRef.current;
    pendingSelRef.current = null;
    // Toolbar commits restore (and refocus) the editable; slider-driven
    // re-syncs (focus on the slider) leave the page selection alone.
    const selToRestore = pending
      || (root.contains(document.activeElement) ? savedSelRef.current : null);
    if (!selToRestore) return;
    const a = modelPointToDom(selToRestore.anchor);
    const f = modelPointToDom(selToRestore.focus);
    if (!a || !f) return;
    if (pending && document.activeElement !== root) root.focus({ preventScroll: true });
    window.getSelection().setBaseAndExtent(a.node, a.off, f.node, f.off);
    savedSelRef.current = selToRestore;
    // Deps are exactly what the interior sync reads — a reflect-only
    // re-render must NOT re-run this (it would re-point the selection
    // mid-gesture).
  }, [doc, fvs, vfFamilyId]);

  // ---- editing ----------------------------------------------------------------

  // The model is authoritative for STYLES, the DOM for TEXT: rebuild the
  // document from the DOM after an input, looking each span's style up in
  // the pre-edit model via data-b/data-r (Chrome's node splits preserve
  // attributes, so a span split by Enter maps both halves to the same run).
  const rebuildFromDom = () => {
    const root = editableRef.current;
    const old = docRef.current;
    const blocks = [];
    [...root.childNodes].forEach((el, domIdx) => {
      if (el.nodeType === 3) { // stray text straight under the editable
        if (el.textContent) {
          blocks.push({ align: 'left', lineHeight: 1.4, runs: [{ ...typingStyleRef.current, text: el.textContent }] });
        }
        return;
      }
      if (el.tagName === 'BR') return;
      const oldBlockIdx = parseInt(el.dataset?.b, 10);
      const oldBlock = old.blocks[oldBlockIdx] || old.blocks[domIdx] || {};
      const runs = [];
      [...el.childNodes].forEach((span) => {
        if (span.nodeType === 3) {
          if (span.textContent) runs.push({ ...typingStyleRef.current, text: span.textContent });
          return;
        }
        if (span.tagName === 'BR') return;
        const oldRun = old.blocks[oldBlockIdx]?.runs?.[parseInt(span.dataset?.r, 10)];
        runs.push({ ...(oldRun ? styleOf(oldRun) : typingStyleRef.current), text: span.textContent || '' });
      });
      // Drop emptied runs — except a block's last remaining run, whose
      // style is the typing style for the next input there.
      let kept = runs.filter(r => r.text.length > 0);
      if (kept.length === 0) kept = [runs[0] || { ...typingStyleRef.current, text: '' }];
      const merged = [];
      for (const r of kept) {
        const p = merged[merged.length - 1];
        if (p && sameStyle(p, r)) p.text += r.text;
        else merged.push({ ...r });
      }
      blocks.push({
        align: oldBlock.align || 'left',
        lineHeight: oldBlock.lineHeight ?? 1.4,
        runs: merged,
      });
    });
    if (!blocks.length) {
      blocks.push({ align: 'left', lineHeight: 1.4, runs: [{ ...typingStyleRef.current, text: '' }] });
    }
    return { blocks };
  };

  const handleInput = () => {
    const modelSel = selectionToModel();
    if (modelSel) pendingSelRef.current = modelSel;
    skipSyncRef.current = true; // the DOM already IS the rebuilt model
    commit({ doc: rebuildFromDom() });
  };

  const handlePaste = (e) => {
    // Plain text only — strip formatting (and newlines: the blocks model
    // has no line-break-in-run, and paste is the only way they could
    // arrive).
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData('text/plain').replace(/\s*\n\s*/g, ' ');
    document.execCommand('insertText', false, text);
  };

  // ---- toolbar -----------------------------------------------------------------

  const orderPoints = (sel) => {
    const { anchor, focus } = sel;
    if (anchor.b !== focus.b) return anchor.b < focus.b ? [anchor, focus] : [focus, anchor];
    return anchor.off <= focus.off ? [anchor, focus] : [focus, anchor];
  };

  const clampSel = (sel, d) => {
    const clampPoint = (p) => {
      const b = Math.max(0, Math.min(p.b, d.blocks.length - 1));
      return { b, off: Math.max(0, Math.min(p.off, blockText(d.blocks[b]).length)) };
    };
    return { anchor: clampPoint(sel.anchor), focus: clampPoint(sel.focus) };
  };

  const applyRunPatch = (patch) => {
    const sel = selectionToModel() || savedSelRef.current;
    if (!sel) {
      // Nothing in the document has held the selection yet — restyle the
      // next input only.
      const merged = { ...typingStyleRef.current, ...patch };
      commit({ typingStyle: merged });
      setReflect(prev => ({ ...prev, ...patch }));
      return;
    }
    const d = docRef.current;
    const [s, e] = orderPoints(clampSel(sel, d));
    const collapsed = s.b === e.b && s.off === e.off;
    if (collapsed) {
      const block = d.blocks[s.b];
      const ri = runIndexAt(block, s.off);
      const run = block.runs[ri];
      const mergedStyle = { ...styleOf(run), ...patch };
      const next = run.text.length === 0
        // Caret already sits in a marker: restyle it in place.
        ? { ...d, blocks: d.blocks.map((blk, i) => (i === s.b ? { ...blk, runs: blk.runs.map((r, j) => (j === ri ? { ...r, ...patch } : r)) } : blk)) }
        : insertMarker(d, s.b, s.off, mergedStyle);
      pendingSelRef.current = sel;
      savedSelRef.current = sel;
      commit({ doc: next, typingStyle: mergedStyle });
      setReflect(prev => ({ ...prev, ...patch }));
      return;
    }
    const next = { ...d, blocks: mergeRuns(patchRange(d.blocks, s, e, patch)) };
    pendingSelRef.current = sel;
    commit({ doc: next });
    setReflect(prev => ({ ...prev, ...patch }));
  };

  // align / lineHeight apply to every block the selection touches.
  const applyBlockPatch = (patch) => {
    const sel = selectionToModel() || savedSelRef.current;
    if (!sel) return;
    const d = docRef.current;
    const [s, e] = orderPoints(clampSel(sel, d));
    pendingSelRef.current = sel;
    commit({
      doc: { ...d, blocks: d.blocks.map((blk, i) => (i >= s.b && i <= e.b ? { ...blk, ...patch } : blk)) },
    });
    setReflect(prev => ({ ...prev, ...patch }));
  };

  // Keep the editable's selection when toolbar buttons are pressed
  // (standard RTE mousedown trick; selects keep the document Range too).
  const keepSel = (e) => e.preventDefault();

  // ---- system font batch ---------------------------------------------------------

  const addSystemFont = () => {
    const name = newFontName.trim();
    if (!name) return;
    if (!systemFonts.some(f => f.name.toLowerCase() === name.toLowerCase())) {
      commit({ systemFonts: [...systemFonts, { name, label: name }] });
    }
    setNewFontName('');
  };
  const removeSystemFont = (name) => commit({
    systemFonts: systemFonts.filter(f => f.name !== name),
  });

  // The batch plus any name a run still references after a batch removal
  // (it renders by name regardless — dropping the option would lie).
  const fontOptions = useMemo(() => {
    const names = new Set(systemFonts.map(f => f.name));
    const extra = new Set();
    for (const blk of doc.blocks) {
      for (const r of blk.runs) {
        if (r.font?.kind === 'system' && r.font.name && !names.has(r.font.name)) extra.add(r.font.name);
      }
    }
    return [...systemFonts, ...[...extra].map(name => ({ name, label: name }))];
  }, [systemFonts, doc]);

  const fontSelectValue = reflect.font?.kind === 'system' ? reflect.font.name : 'preview';

  return (
    <div className="tester-tab">
      <div className="tester-tab-panel">
        <div className="tester-toolbar">
          <select
            className="tester-font-select"
            value={fontSelectValue}
            onChange={(e) => applyRunPatch({
              font: e.target.value === 'preview' ? { kind: 'preview' } : { kind: 'system', name: e.target.value },
            })}
            title="Font for the selection / next input"
          >
            <option value="preview">Preview font</option>
            {fontOptions.map(f => (
              <option key={f.name} value={f.name}>
                {f.label || f.name}{fontInstalled(f.name) ? '' : ' (not installed)'}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={`tester-tool tester-bold${reflect.bold ? ' on' : ''}`}
            onMouseDown={keepSel}
            onClick={() => applyRunPatch({ bold: !reflect.bold })}
            title="Bold — CSS font-weight 700 (synthetic on the preview font; use the wght slider for the real axis)"
          >
            <strong>B</strong>
          </button>
          <button
            type="button"
            className={`tester-tool tester-italic${reflect.italic ? ' on' : ''}`}
            onMouseDown={keepSel}
            onClick={() => applyRunPatch({ italic: !reflect.italic })}
            title="Italic — CSS font-style italic (synthetic on the preview font)"
          >
            <em>I</em>
          </button>
          <select
            className="tester-lh"
            value={reflect.lineHeight}
            onChange={(e) => applyBlockPatch({ lineHeight: parseFloat(e.target.value) })}
            title="Line height (paragraph)"
          >
            {LINE_HEIGHTS.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
          <div className="tester-align" role="group" aria-label="Alignment">
            {['left', 'center', 'right'].map(a => (
              <button
                key={a}
                type="button"
                className={`tester-tool${reflect.align === a ? ' on' : ''}`}
                onMouseDown={keepSel}
                onClick={() => applyBlockPatch({ align: a })}
              >
                {a[0].toUpperCase() + a.slice(1)}
              </button>
            ))}
          </div>
          <label className="tester-tracking" title="Letter-spacing, em">
            Tracking
            <input
              type="number"
              step="0.01"
              min="-0.05"
              max="0.3"
              value={reflect.tracking}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!Number.isNaN(v)) applyRunPatch({ tracking: Math.max(-0.05, Math.min(0.3, v)) });
              }}
            />
            em
          </label>
        </div>

        {/* The interior is synced imperatively (see the layout effect
            above) — React must never reconcile inside a contentEditable
            whose nodes the browser mutates on every edit. */}
        <div
          ref={editableRef}
          className="tester-editable"
          contentEditable
          suppressContentEditableWarning
          spellCheck={false}
          style={{ fontSize: `${fontSize}rem` }}
          onInput={handleInput}
          onPaste={handlePaste}
          onKeyUp={refreshSelection}
          onMouseUp={refreshSelection}
        />

        {(axes || []).length > 0 && (
          <section className="tester-axes">
            {(axes || []).map(a => (
              <AxisControl
                key={a.tag}
                axis={a}
                value={coords[a.tag] ?? a.default}
                onChange={(v) => setCoords(prev => ({ ...prev, [a.tag]: v }))}
                disabled={!fontLoaded}
                treatEmptyAsActive
              />
            ))}
          </section>
        )}

        <details className="tester-fonts-manage">
          <summary>System fonts</summary>
          <div className="tester-sysfonts">
            {systemFonts.map(f => (
              <div className="tester-sysfont" key={f.name}>
                <span className="tester-sysfont-name" style={{ fontFamily: `"${f.name}"` }}>
                  {f.label || f.name}
                </span>
                {fontInstalled(f.name)
                  ? <span className="tester-installed">installed</span>
                  : <span className="tester-not-installed">not installed</span>}
                <button
                  type="button"
                  className="tester-icon-btn"
                  onClick={() => removeSystemFont(f.name)}
                  title={`Remove ${f.label || f.name} from the batch`}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          <div className="tester-sysfont-add">
            <input
              type="text"
              value={newFontName}
              placeholder="Font family name…"
              onChange={(e) => setNewFontName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') addSystemFont(); }}
            />
            <button type="button" className="tester-add-btn" onClick={addSystemFont} disabled={!newFontName.trim()}>
              Add
            </button>
          </div>
        </details>
      </div>
    </div>
  );
}

export default TesterTab;
