import React, { useEffect, useRef, useState } from 'react';
import './Header.css';
import { api } from '../api';
import logoGif from '../assets/logo.gif';
import ImportConfigModal, { type ImportReport } from './ImportConfigModal';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

// Server shapes — see GET /api/examples and GET /api/transforms.
interface ExampleInfo {
  id: string;
  name: string;
  subtitle?: string;
}

interface TransformParamOption {
  value: string;
  label: string;
}

interface TransformParamSpec {
  key: string;
  label: string;
  type: string; // 'select' | 'bool' | 'float' | 'int' (int/float share the number input)
  default?: any; // server-defined; type depends on ``type``
  options?: TransformParamOption[];
  min?: number;
  max?: number;
}

interface TransformEntry {
  id: string;
  name: string;
  description?: string;
  enabled?: boolean;
  injected_axis_tag?: string | null;
  params_schema?: TransformParamSpec[];
  params?: Record<string, any>; // user values keyed by ParamSpec.key
}

interface ImportData {
  bundle: unknown;
  report: ImportReport;
}

interface HeaderProps {
  onBuildFont?: () => void;
  building?: boolean;
  fontLoaded?: boolean;
  familyName?: string;
  onSourceLoaded?: () => void;
  busy?: boolean;
  transforms?: TransformEntry[];
  onToggleTransform?: (id: string, enabled: boolean) => void;
  onTransformParam?: (id: string, key: string, value: any) => void;
  grade?: GradeState;
  onToggleGrade?: (enabled: boolean) => void;
  onGradeDefault?: (pct: number) => void;
  // Static demo (GitHub Pages): hide every action that needs the backend.
  staticMode?: boolean;
  // Static demo showing a baked snapshot: nothing exists to rebuild
  // (uploaded sources recompile in-browser, so they keep the button).
  hideRebuild?: boolean;
  // Static demo with an uploaded source: config import becomes
  // available (bundles apply in-browser via the static provider).
  allowImportInStatic?: boolean;
}

// Grade transform — source-level (adds a GRAD axis); toggle + global default
// here, per-instance grade% in each style's row menu.
interface GradeState {
  enabled?: boolean;
  default_pct?: number;
  instances?: { name: string; pct: number }[];
  max_pct?: Record<string, number>;
}

// "Load Font" dropdown sitting in the top bar. The dropdown is the
// single entry point for swapping the active source at runtime:
//
//   - Built-in examples come from GET /api/examples. Selecting one
//     POSTs { example: id } to /api/load-source; the backend stages
//     a per-example workspace under ~/.avar2-studio/workspace/ so
//     the shipped fixture stays clean.
//   - "Upload .glyphs file…" opens a hidden file picker. The chosen
//     file is sent as multipart/form-data to the same endpoint.
//
// The Transforms menu's open/close mechanics are the vendored shadcn
// DropdownMenu (Radix): trigger toggling, click-outside and Escape
// dismissal come from the primitive instead of a hand-rolled
// useEffect. Its content keeps the original load-font-menu /
// transforms-menu markup and styling hooks. The Load Font and Config
// dropdowns still use the old manual pattern.
function Header({ onBuildFont, building, fontLoaded, familyName, onSourceLoaded, busy,
                 transforms = [], onToggleTransform, onTransformParam,
                 grade, onToggleGrade, onGradeDefault, staticMode = false,
                 hideRebuild = false, allowImportInStatic = false }: HeaderProps) {
  const [examples, setExamples] = useState<ExampleInfo[]>([]);
  const [open, setOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState<string | null>(null);
  // Non-null while the import confirmation modal is open:
  // {bundle, report} from the dry-run POST.
  const [importData, setImportData] = useState<ImportData | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const configRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const configFileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.listExamples()
      .then(d => setExamples(d.examples || []))
      .catch(() => setExamples([]));
  }, []);

  // Click-outside closes the menu.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  // Click-outside closes the config menu.
  useEffect(() => {
    if (!configOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (configRef.current && !configRef.current.contains(e.target as Node)) {
        setConfigOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [configOpen]);

  const enabledCount = transforms.filter(t => t.enabled).length + (grade?.enabled ? 1 : 0);

  const handleLoadExample = async (id: string, name: string) => {
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
    // Static demo: uploads compile in-browser (fontc-wasm Worker).
    fileInputRef.current && fileInputRef.current.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    // COPY the FileList before clearing the input: ``input.files`` is
    // a LIVE list, and resetting ``value`` empties it in place — the
    // old grab-then-clear order left zero files and returned before
    // any message or request ("upload does nothing").
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (files.length === 0) return;
    const names = files.map(f => f.name).join(', ');
    setLoadingMsg(`Uploading ${names}…`);
    try {
      const result = await api.uploadSource(files);
      onSourceLoaded && onSourceLoaded();
      if (result.ignored_files && result.ignored_files.length > 0) {
        // Surface the rejected files in the header so the user knows
        // their CSV / metadata picks didn't all land.
        setLoadingMsg(`Loaded, but ignored: ${result.ignored_files.join(', ')}`);
        setTimeout(() => setLoadingMsg(null), 6000);
        return;
      }
    } catch (err) {
      setLoadingMsg(`Failed: ${err.message || err}`);
      setTimeout(() => setLoadingMsg(null), 6000);
      return;
    }
    setLoadingMsg(null);
  };

  const handleExportConfig = () => {
    setConfigOpen(false);
    // Same temporary-anchor trick as the font download in PreviewTab:
    // the server sets Content-Disposition: attachment with a
    // "<family>-avar2studio.json" filename, so the empty download
    // attr just forces navigation-free saving and the server's
    // filename wins.
    const link = document.createElement('a');
    link.href = api.exportConfigUrl();
    link.download = '';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleImportClick = () => {
    setConfigOpen(false);
    configFileInputRef.current && configFileInputRef.current.click();
  };

  const handleConfigFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    // Grab the File BEFORE clearing the input (``input.files`` is live —
    // see handleFileChange); the File object itself stays valid after.
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    setLoadingMsg(`Reading ${file.name}…`);
    const reader = new FileReader();
    reader.onload = async () => {
      let parsed;
      try {
        parsed = JSON.parse(reader.result as string); // readAsText → string
      } catch (parseErr) {
        setLoadingMsg(`Not valid JSON: ${parseErr.message}`);
        setTimeout(() => setLoadingMsg(null), 6000);
        return;
      }
      // Two-step import: this first POST is a DRY RUN — the server
      // validates the bundle and returns a report without applying
      // anything. The modal shows that report; only its Import
      // button POSTs again with dry_run=false.
      setLoadingMsg(`Validating ${file.name}…`);
      try {
        const report = await api.importConfig(parsed, true);
        setImportData({ bundle: parsed, report });
        setLoadingMsg(null);
      } catch (err) {
        setLoadingMsg(`Import check failed: ${err.message || err}`);
        setTimeout(() => setLoadingMsg(null), 6000);
      }
    };
    reader.onerror = () => {
      setLoadingMsg(`Could not read ${file.name}`);
      setTimeout(() => setLoadingMsg(null), 6000);
    };
    reader.readAsText(file);
  };

  const handleImported = () => {
    setImportData(null);
    // A successful import replaces axes / mappings / transforms
    // server-side, so it needs the same full refetch a source load gets.
    onSourceLoaded && onSourceLoaded();
    setLoadingMsg('Configuration imported.');
    setTimeout(() => setLoadingMsg(null), 4000);
  };

  return (
    <>
    <header className="header">
      <div className="header-title">
        <img className="header-logo" src={logoGif} alt="avar2 studio" />
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
                title={staticMode ? 'Uploads trigger a build — they need the full app' : undefined}
              >
                <div className="load-font-item-name">Upload .glyphs or project .zip…</div>
                <div className="load-font-item-subtitle">
                  Zip a project folder to continue where you left off — saved
                  studio data comes along (.designspace projects need the zip).
                </div>
              </button>
            </div>
          )}
          {/* No ``accept`` filter: Safari (and some macOS pickers) map
              accept extensions to registered document types, and an
              unregistered ".glyphs" grays the user's own source out —
              the picker opens but the file can't be chosen, which
              reads as "nothing happens". The server validates and
              reports anything that isn't a .glyphs / -avar.csv /
              axis-metadata.json. Likewise offscreen-not-display:none:
              some browsers ignore programmatic click() on a
              display:none file input. */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            style={{ position: 'fixed', left: '-9999px', width: 1, height: 1, opacity: 0 }}
            onChange={handleFileChange}
          />
        </div>
        {familyName && (transforms.length > 0 || grade) && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className="btn btn-load-font"
                disabled={busy}
                title="Transforms — Grade (adds a same-width darkening axis) and post-build steps like SPAC. Off by default."
              >
                Transforms{enabledCount > 0 && <span className="count-flag">{enabledCount}</span>} ▾
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="load-font-menu transforms-menu" align="end">
              {grade && (
                <>
                  <div className="load-font-section-label">Grade</div>
                  <div className="transform-row">
                    <label
                      className="transform-toggle"
                      title="Darken or lighten styles without changing advance widths. Adds a Grade axis; add a grade to individual styles from their row menu."
                    >
                      <input
                        type="checkbox"
                        checked={!!grade.enabled}
                        disabled={busy || staticMode}
                        onChange={(e) => onToggleGrade && onToggleGrade(e.target.checked)}
                      />
                      <span className="transform-name">Grade</span>
                    </label>
                    <div className="transform-desc">
                      Same-width darkening. Turn on, then add a grade to individual styles.
                    </div>
                    {grade.enabled && (
                      <div className="transform-params">
                        <label className="transform-param">
                          <span className="transform-param-label">Default grade %</span>
                          <input
                            type="number"
                            className="transform-param-input"
                            value={Math.round((grade.default_pct ?? 0.25) * 100)}
                            min={1}
                            max={100}
                            step={1}
                            disabled={busy || staticMode}
                            onChange={(e) => {
                              const v = parseFloat(e.target.value);
                              if (!Number.isNaN(v) && onGradeDefault) onGradeDefault(v / 100);
                            }}
                          />
                        </label>
                      </div>
                    )}
                  </div>
                </>
              )}
              {transforms.length > 0 && <div className="load-font-section-label">Post-build transforms</div>}
              {transforms.map(t => {
                // Client-side mirror of the server's one-injector-per-axis
                // rule: if another enabled transform already adds this
                // transform's fvar axis, disable this toggle (the server
                // would 400 anyway) and explain why.
                const owner = t.injected_axis_tag
                  ? transforms.find(o => o.id !== t.id && o.enabled && o.injected_axis_tag === t.injected_axis_tag)
                  : null;
                const conflictDisabled = !t.enabled && !!owner;
                return (
                <div key={t.id} className={`transform-row${conflictDisabled ? ' transform-row-disabled' : ''}`}>
                  <label className="transform-toggle" title={conflictDisabled ? `Adds the ${t.injected_axis_tag} axis, already added by "${owner.name}". Turn that off first.` : t.description}>
                    <input
                      type="checkbox"
                      checked={!!t.enabled}
                      disabled={busy || conflictDisabled}
                      onChange={(e) => onToggleTransform && onToggleTransform(t.id, e.target.checked)}
                    />
                    <span className="transform-name">{t.name}</span>
                  </label>
                  {t.description && <div className="transform-desc">{t.description}</div>}
                  {t.enabled && (t.params_schema || []).length > 0 && (
                    <div className="transform-params">
                      {(t.params_schema || []).map(p => (
                        <label key={p.key} className="transform-param">
                          <span className="transform-param-label">{p.label}</span>
                          {p.type === 'select' ? (
                            <select
                              className="transform-param-input"
                              value={t.params?.[p.key] ?? p.default}
                              disabled={busy}
                              onChange={(e) => onTransformParam && onTransformParam(t.id, p.key, e.target.value)}
                            >
                              {(p.options || []).map(o => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                              ))}
                            </select>
                          ) : p.type === 'bool' ? (
                            <input
                              type="checkbox"
                              checked={!!(t.params?.[p.key] ?? p.default)}
                              disabled={busy || staticMode}
                              onChange={(e) => onTransformParam && onTransformParam(t.id, p.key, e.target.checked)}
                            />
                          ) : (
                            <input
                              type="number"
                              className="transform-param-input"
                              value={t.params?.[p.key] ?? p.default}
                              min={p.min ?? undefined}
                              max={p.max ?? undefined}
                              step={p.type === 'float' ? 0.1 : 1}
                              disabled={busy || staticMode}
                              // Pass the RAW string so clearing the field or
                              // typing a leading '-' isn't coerced to 0 — the
                              // server coerces/clamps against the ParamSpec on
                              // commit (empty → default).
                              onChange={(e) => onTransformParam && onTransformParam(t.id, p.key, e.target.value)}
                            />
                          )}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        {familyName && (
          <div className="config-dropdown" ref={configRef}>
            <button
              className="btn btn-load-font"
              onClick={() => setConfigOpen(o => !o)}
              disabled={busy}
              title="Export or import the studio configuration (control axes, avar2 mappings, transforms) as one JSON file"
            >
              Config ▾
            </button>
            {configOpen && (
              <div className="load-font-menu">
                <div className="load-font-section-label">Studio configuration</div>
                <button
                  className="load-font-item"
                  onClick={handleExportConfig}
                >
                  <div className="load-font-item-name">Export configuration…</div>
                  <div className="load-font-item-subtitle">
                    Control axes, avar2 mappings, transforms as one JSON file
                  </div>
                </button>
                {(!staticMode || allowImportInStatic) && (
                <button
                  className="load-font-item"
                  onClick={handleImportClick}
                >
                  <div className="load-font-item-name">Import configuration…</div>
                  <div className="load-font-item-subtitle">
                    Replace the current configuration from an exported JSON file. You review a validation report before anything is applied.
                  </div>
                </button>
                )}
              </div>
            )}
            {/* Same offscreen-not-display:none, no-accept-filter treatment
                as the .glyphs input above — see the long comment there.
                Single file; the server validates the bundle contents. */}
            <input
              ref={configFileInputRef}
              type="file"
              style={{ position: 'fixed', left: '-9999px', width: 1, height: 1, opacity: 0 }}
              onChange={handleConfigFileChange}
            />
          </div>
        )}
        {fontLoaded !== undefined && familyName && !hideRebuild && (
          <button
            onClick={onBuildFont}
            disabled={building}
            className="btn btn-3d btn-rebuild"
          >
            {building ? 'Building...' : fontLoaded ? 'Rebuild' : 'Build'}
          </button>
        )}
      </div>
    </header>
    {importData && (
      <ImportConfigModal
        bundle={importData.bundle}
        report={importData.report}
        familyName={familyName}
        onCancel={() => setImportData(null)}
        onImported={handleImported}
      />
    )}
    </>
  );
}

export default Header;
