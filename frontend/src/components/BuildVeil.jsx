import React from 'react';
import { api } from '../api';
import { useBuildStatus } from '../hooks/useBuildStatus';

/**
 * The "Rebuilding — hold your edits…" veil over the workspace while a
 * build/export job runs (with a Cancel button where the provider
 * supports it). Reads the one building signal from useBuildStatus.
 */
export default function BuildVeil() {
  const { building, buildJob } = useBuildStatus();
  if (!building) return null;
  return (
    <div className="build-veil" aria-live="polite">
      <span className="build-veil-logo">avar2studio</span>
      <span className="build-veil-text">
        {buildJob && buildJob.kind !== 'build'
          ? `${buildJob.kind === 'export' ? 'Exporting' : 'Compressing'} — the preview stays live…`
          : 'Rebuilding — hold your edits…'}
      </span>
      {api.capabilities.cancel && buildJob && buildJob.job_id && (
        <button
          type="button"
          className="build-veil-cancel"
          title="Cancel this job — the last-good font keeps serving"
          onClick={() => { api.cancelBuild(buildJob.job_id).catch(() => {}); }}
        >
          Cancel
        </button>
      )}
    </div>
  );
}
