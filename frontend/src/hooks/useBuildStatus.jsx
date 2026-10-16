import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';

/**
 * The ONE building/stale signal. App's health poll (and loadData) feed it
 * through applyHealth(); App's mutation handlers flip `building` early for
 * immediate feedback through setBuilding(); Header, the build veil,
 * ControlAxes, KinetikernTab and FontraEditorModal read it from here
 * instead of each receiving a threaded `building` prop.
 *
 *   building        — a build is running or wanted server-side (health
 *                     folds in background shadow regen), or a mutation
 *                     handler is waiting for its rebuild to land.
 *   buildJob        — the running job ({job_id, kind, elapsed, phase}) or null.
 *   buildStale      — the saved mappings changed after the last build:
 *                     auto-saved coordinate edits don't rebuild per save,
 *                     so the preview font is behind until the next build.
 *   lastBuildStatus — "ok" | "failed" | null: outcome of the most recent
 *                     build attempt. "failed" => the preview is stale (the
 *                     last-good font keeps serving) and the server gates
 *                     every edit until a build succeeds.
 *   lastBuildError  — the failure message that goes with "failed".
 */
const BuildStatusContext = createContext(null);

export function BuildStatusProvider({ children }) {
  const [building, setBuilding] = useState(false);
  const [buildJob, setBuildJob] = useState(null);
  const [buildStale, setBuildStale] = useState(false);
  const [lastBuildStatus, setLastBuildStatus] = useState(null);
  const [lastBuildError, setLastBuildError] = useState(null);

  // Fold one /api/health answer into the signal.
  const applyHealth = useCallback((health) => {
    setBuilding(health.building || false);
    setBuildJob(health.build_job || null);
    setBuildStale(health.build_stale || false);
    setLastBuildStatus(health.last_build_status || null);
    setLastBuildError(health.last_build_error || null);
  }, []);

  const value = useMemo(() => ({
    building, setBuilding, buildJob, buildStale, lastBuildStatus, lastBuildError, applyHealth,
  }), [building, buildJob, buildStale, lastBuildStatus, lastBuildError, applyHealth]);

  return (
    <BuildStatusContext.Provider value={value}>
      {children}
    </BuildStatusContext.Provider>
  );
}

export function useBuildStatus() {
  const ctx = useContext(BuildStatusContext);
  if (!ctx) {
    throw new Error('useBuildStatus() needs a <BuildStatusProvider> above it (index.jsx mounts one).');
  }
  return ctx;
}
