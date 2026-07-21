import React, { useState } from 'react';
import { api } from '../api';
import './ImportConfigModal.css';

// Confirmation step of the two-step config import. The Header has
// already POSTed the bundle with dry_run=true and hands us the
// validation report; this modal shows what the import would do and
// only the Import button POSTs again with dry_run=false to apply it.
function ImportConfigModal({ bundle, report: initialReport, familyName, onCancel, onImported }) {
  // ``report`` is state (not just the prop) because a failed apply can
  // replace it with the server's 400 report — see handleImport.
  const [report, setReport] = useState(initialReport);
  const [applying, setApplying] = useState(false);

  const errors = report.errors || [];
  const warnings = report.warnings || [];
  const summary = report.summary || {};
  const hasErrors = errors.length > 0;

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  const handleImport = async () => {
    setApplying(true);
    try {
      await api.importConfig(bundle, false);
      onImported && onImported();
    } catch (err) {
      // The dry run passed but the apply 400'd (server state moved
      // between the two calls). api.importConfig attaches the server's
      // report to the error — swap it in so the user sees the exact
      // errors. No report attached means a plain {error} failure;
      // surface the message as a generic error entry instead.
      if (err.report) {
        setReport(err.report);
      } else {
        setReport(r => ({ ...r, ok: false, errors: [...(r.errors || []), err.message || String(err)] }));
      }
      setApplying(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="import-config-modal">
        <h3>Import configuration</h3>
        <p className="import-config-summary">
          {plural(summary.axes ?? 0, 'control axis', 'control axes')},{' '}
          {plural(summary.layers ?? 0, 'brace layer', 'brace layers')},{' '}
          {plural(summary.mapping_rows ?? 0, 'mapping row', 'mapping rows')},{' '}
          {plural(summary.transforms ?? 0, 'transform', 'transforms')}.
        </p>
        <p className="import-config-note">
          Importing <strong>replaces</strong> the current studio configuration
          on <strong>{familyName}</strong>. Drawn outlines are not included —
          brace layers are re-seeded by interpolation.
        </p>
        {hasErrors && (
          <div className="import-config-errors">
            <div className="import-config-list-title">This file cannot be imported:</div>
            <ul>
              {errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          </div>
        )}
        {warnings.length > 0 && (
          <div className="import-config-warnings">
            <div className="import-config-list-title">Warnings — the import can still proceed:</div>
            <ul>
              {warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        )}
        <div className="import-config-buttons">
          <button
            className="import-config-cancel"
            onClick={onCancel}
            disabled={applying}
          >
            Cancel
          </button>
          <button
            className="import-config-confirm"
            onClick={handleImport}
            disabled={hasErrors || applying}
          >
            {applying ? 'Importing…' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ImportConfigModal;
