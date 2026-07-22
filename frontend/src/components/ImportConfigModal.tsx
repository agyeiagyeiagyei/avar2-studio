import React, { useState } from 'react';
import { api } from '../api';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import './ImportConfigModal.css';

// Shapes of the validation report the server returns for both the
// dry-run and the apply POST /api/config/import — see importConfig.
export interface ImportSummary {
  axes?: number;
  layers?: number;
  mapping_rows?: number;
  transforms?: number;
}

export interface ImportReport {
  ok?: boolean;
  errors?: string[];
  warnings?: string[];
  summary?: ImportSummary;
}

// api.importConfig attaches the server's 400 report to the thrown error.
type ImportError = Error & { report?: ImportReport };

interface ImportConfigModalProps {
  bundle: unknown;
  report: ImportReport;
  familyName?: string;
  onCancel?: () => void;
  onImported?: () => void;
}

// Confirmation step of the two-step config import. The Header has
// already POSTed the bundle with dry_run=true and hands us the
// validation report; this modal shows what the import would do and
// only the Import button POSTs again with dry_run=false to apply it.
//
// The overlay/box shell is the vendored shadcn Dialog (Radix). Any
// dismissal (X, Escape, overlay click) is treated as Cancel — except
// while the apply is in flight, matching the Cancel button's disabled
// state. The content keeps its original CSS classes; ``block`` undoes
// DialogContent's default grid and ``border-0`` its border, so the
// existing margin-based, borderless styling is untouched.
function ImportConfigModal({ bundle, report: initialReport, familyName, onCancel, onImported }: ImportConfigModalProps) {
  // ``report`` is state (not just the prop) because a failed apply can
  // replace it with the server's 400 report — see handleImport.
  const [report, setReport] = useState<ImportReport>(initialReport);
  const [applying, setApplying] = useState(false);

  const errors = report.errors || [];
  const warnings = report.warnings || [];
  const summary = report.summary || {};
  const hasErrors = errors.length > 0;

  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

  const handleImport = async () => {
    setApplying(true);
    try {
      await api.importConfig(bundle, false);
      onImported && onImported();
    } catch (e) {
      // The dry run passed but the apply 400'd (server state moved
      // between the two calls). api.importConfig attaches the server's
      // report to the error — swap it in so the user sees the exact
      // errors. No report attached means a plain {error} failure;
      // surface the message as a generic error entry instead.
      const err = e as ImportError;
      if (err.report) {
        setReport(err.report);
      } else {
        setReport(r => ({ ...r, ok: false, errors: [...(r.errors || []), err.message || String(err)] }));
      }
      setApplying(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !applying) onCancel && onCancel(); }}>
      <DialogContent className="import-config-modal block border-0">
        <DialogTitle asChild>
          <h3>Import configuration</h3>
        </DialogTitle>
        <DialogDescription asChild>
          <p className="import-config-summary">
            {plural(summary.axes ?? 0, 'control axis', 'control axes')},{' '}
            {plural(summary.layers ?? 0, 'brace layer', 'brace layers')},{' '}
            {plural(summary.mapping_rows ?? 0, 'mapping row', 'mapping rows')},{' '}
            {plural(summary.transforms ?? 0, 'transform', 'transforms')}.
          </p>
        </DialogDescription>
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
      </DialogContent>
    </Dialog>
  );
}

export default ImportConfigModal;
