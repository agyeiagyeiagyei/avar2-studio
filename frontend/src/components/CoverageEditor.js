import React, { useEffect, useState } from 'react';

/**
 * Inline editor for a control axis's coverage list.
 *
 * The designer enters glyph names — one per line, with optional
 * ``# group comments`` and blank lines for grouping. The parser
 * strips the noise on save so the canonical sidecar shape is a
 * clean array.
 *
 * Props:
 *   tag        — axis tag (lowercase)
 *   coverage   — array<string> of glyph names currently in the
 *                sidecar (canonical truth)
 *   onSave     — async (tag, coverageArray) => void; controlled by
 *                App.js, calls the API and refreshes coverage data
 */
function CoverageEditor({ tag, coverage, onSave }) {
  // Local draft state — the textarea is uncontrolled relative to
  // the prop until the user saves. Reset whenever the canonical
  // ``coverage`` changes (e.g. after a save round-trip).
  const [draft, setDraft] = useState(() => (coverage || []).join('\n'));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDraft((coverage || []).join('\n'));
  }, [coverage]);

  const parsedGlyphs = parseGlyphList(draft);
  const dirty = !sameArrays(parsedGlyphs, coverage || []);

  const handleSave = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(tag, parsedGlyphs);
    } catch (e) {
      setError(e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="coverage-editor">
      <div className="coverage-editor-label">
        Coverage glyphs
        <span className="coverage-editor-hint">
          one per line · <code>#</code> starts a comment · blank lines OK
        </span>
      </div>
      <textarea
        className="coverage-editor-textarea"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={Math.max(4, Math.min(10, (draft.match(/\n/g) || []).length + 2))}
        placeholder={'# letters with crossbars\nA\nE\nF\nH\ne\nf\nt'}
        spellCheck={false}
        autoComplete="off"
      />
      <div className="coverage-editor-actions">
        <span className="coverage-editor-count">
          {parsedGlyphs.length} glyph{parsedGlyphs.length === 1 ? '' : 's'}
          {dirty && ' (unsaved)'}
        </span>
        <button
          className="coverage-editor-save"
          onClick={handleSave}
          disabled={!dirty || saving}
        >
          {saving ? 'Saving…' : 'Save coverage'}
        </button>
      </div>
      {error && <div className="coverage-editor-error">{error}</div>}
    </div>
  );
}

function parseGlyphList(text) {
  if (!text) return [];
  const out = [];
  const seen = new Set();
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    if (seen.has(line)) continue;
    seen.add(line);
    out.push(line);
  }
  return out;
}

function sameArrays(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

export default CoverageEditor;
