import React from 'react';
import { useBuildStatus } from '../hooks/useBuildStatus';
import { useStudioData } from '../hooks/useStudioData';

/**
 * The status banners stacked between the header and the workspace:
 * the dismissable mutation-error banner, the static-mode session-lock
 * notice, the build-failed block (the workspace is inert under it), and
 * the three amber health warnings (origin / source transform / avar2
 * fallback). All state comes from the two contexts; nothing is drilled.
 */
export default function StatusBanners() {
  const { building, lastBuildStatus, lastBuildError } = useBuildStatus();
  const {
    error, setError, sessionLockLost, originWarning, buildDegraded,
    sourceTransformError, avar2Error, originRelocationError, buildFont,
  } = useStudioData();

  return (
    <>
      {error && (
        <div className="error-banner">
          Error: {error}
          <button
            type="button"
            className="error-banner-dismiss"
            aria-label="Dismiss error"
            onClick={() => setError(null)}
          >
            ×
          </button>
        </div>
      )}

      {sessionLockLost && (
        <div className="error-banner" role="alert" style={{ background: '#e8f0fe', color: '#1a3c8f', borderColor: '#9db8e8' }}>
          This project is open in another tab, which holds the editing session. Edits here are not protected against it — close the other tab (or reload this one after) to take over.
        </div>
      )}

      {lastBuildStatus === 'failed' && (
        // The source does not compile. The server refuses every edit with 409
        // while this is true (see _refuse_edits_while_source_is_broken), so the
        // workspace below is made inert rather than left looking editable.
        <div className="build-blocked" role="alert">
          <div className="build-blocked-title">The source file does not compile — the studio is paused.</div>
          <pre className="build-blocked-error">{lastBuildError}</pre>
          <div className="build-blocked-actions">
            <span>Fix the source and save it; the studio rebuilds on save. Or</span>
            <button type="button" onClick={buildFont} disabled={building}>
              {building ? 'Rebuilding…' : 'Rebuild now'}
            </button>
          </div>
        </div>
      )}
      {/* The relocation-failure banner below carries the specific reason,
          so the generic "relocation is off" note would be a second amber box
          saying the same thing. */}
      {lastBuildStatus !== 'failed' && originWarning && !originRelocationError && (
        <div className="error-banner" style={{ background: '#fff4e0', color: '#8a4b00', borderColor: '#f1c277' }}>
          {originWarning}
        </div>
      )}
      {lastBuildStatus !== 'failed' && buildDegraded && (
        // The build succeeded but has nothing to rest on: no declared user-axis
        // defaults. Amber, not red — the preview works, it is just not at any
        // authored style (O1).
        <div className="build-degraded-banner" style={{ background: '#fff4e0', color: '#8a4b00', borderColor: '#f1c277' }}>
          This font rests at the compiled corner. {buildDegraded}
        </div>
      )}
      {lastBuildStatus !== 'failed' && sourceTransformError && (
        <div className="error-banner" style={{ background: '#fff4e0', color: '#8a4b00', borderColor: '#f1c277' }}>
          A build transform failed and the font was built without it. {sourceTransformError}
        </div>
      )}
      {originRelocationError && (
        <div className="error-banner" style={{ background: '#fff4e0', color: '#8a4b00', borderColor: '#f1c277' }}>
          The font cannot be relocated onto the ORIGIN row, so the preview rests
          at the compiled origin instead. Every axis and mapping is intact —
          put the ORIGIN row back to clear this. {originRelocationError}
        </div>
      )}
      {lastBuildStatus !== 'failed' && avar2Error && (
        <div className="error-banner" style={{ background: '#fff4e0', color: '#8a4b00', borderColor: '#f1c277' }}>
          The mapped font failed to build; the preview is showing the plain fallback without your mappings. {avar2Error}
        </div>
      )}
    </>
  );
}
