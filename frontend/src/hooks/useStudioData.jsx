import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useBuildStatus } from './useBuildStatus';
import { isStaticMode, isUploadDataset, getSampleText, setSampleText as persistSampleText, getTesterState, setTesterState as persistTesterState } from '../static-api';

// Default sample text uses only ASCII letters, digits, and space so it
// renders cleanly against minimal fixtures (e.g. examples/crispy-mini,
// examples/roboto-delta-mini, which subset to A-Z + a-z + 0-9 + space).
// "&" and "!" were dropped from the historical Crispy default for the
// same reason.
const DEFAULT_SAMPLE_TEXT = "The Quick Brown Fox Jumps Over The Lazy Dog 0123456789";

/**
 * Studio data: every piece of server-derived state (instances, axes,
 * masters, transforms, grade, avar2 mappings, glyph coverage), the
 * loaders that fetch it (loadData / loadAvar2Data / loadGlyphCoverage),
 * and the 2 s health poll. The full-screen "Loading…" gate keys off
 * `loading`, which only covers the FIRST load — later refreshes (a
 * transform commit's loadData, a Load Font swap) leave the UI up.
 *
 * Per-instance editing state (selection, coordinate drafts) lives in
 * useInstanceEditing; it clears itself when `sourceSwapCount` bumps.
 */
const StudioDataContext = createContext(null);

export function StudioDataProvider({ children }) {
  const [instances, setInstances] = useState([]);
  // Parametric master corners — pre-listed in the control-axis brace
  // flow so the designer places a crbr view at a specific corner.
  const [masters, setMasters] = useState([]);
  // Post-build transforms (e.g. SPAC): available list merged with this
  // project's enabled state + params, from GET /api/transforms.
  const [transforms, setTransforms] = useState([]);
  // Grade transform (source-level, its own /api/transforms/grade sidecar):
  // { enabled, default_pct, instances: [{name, pct}], max_pct: {name: cap} }.
  const [grade, setGrade] = useState({ enabled: false, default_pct: 0.25, intensity: 1,
                                      clamp_to_headroom: true, instances: [], max_pct: {}, diagnostics: [] });
  const [axes, setAxes] = useState([]);
  const [fontLoaded, setFontLoaded] = useState(false);
  // Static demo (GitHub Pages): the api object was swapped for the
  // snapshot reader before render (index.jsx / static-api.js). Read-only.
  const [staticMode] = useState(isStaticMode());
  const [fontUrl, setFontUrl] = useState(null);
  // True only during the FIRST loadData. The old code re-gated the whole
  // UI behind "Loading…" on every refresh — a commit-triggered loadData
  // blanked the workspace mid-edit. Refreshes now leave the UI mounted.
  const [loading, setLoading] = useState(true);
  const firstLoadDoneRef = useRef(false);
  const [error, setError] = useState(null);
  // The one building/stale signal (hooks/useBuildStatus): fed by the health
  // poll + loadData below, flipped early by the mutation handlers.
  const { setBuilding, applyHealth } = useBuildStatus();
  // Non-null ⇒ the avar2 build is failing and the served font is the
  // plain fallback (no avar2 table / mapped axes). Surfaced on the
  // Preview tab so the fallback is never silent.
  const [avar2Error, setAvar2Error] = useState(null);
  // Set when the avar2 font is served but its default could not be moved
  // onto the ORIGIN row (health.origin_relocation_error); the preview keeps
  // every axis, it just does not rest at the ORIGIN row's style.
  const [originRelocationError, setOriginRelocationError] = useState(null);
  // Non-null ⇒ a source-stage transform (round corners / slant) failed
  // and the font was built WITHOUT it — surfaced as a banner so an
  // enabled-but-inert transform never passes silently.
  const [sourceTransformError, setSourceTransformError] = useState(null);
  // Non-null ⇒ the mappings define styles but no row sits at the axis
  // defaults: the font rests at the compiled origin, not at any
  // authored style (the "resting dip").
  const [originWarning, setOriginWarning] = useState(null);
  const [sampleText, setSampleTextState] = useState(DEFAULT_SAMPLE_TEXT);
  // Wrap setSampleText to also persist to the session when in static mode
  const setSampleText = useCallback((text) => {
    setSampleTextState(text);
    if (isStaticMode() && isUploadDataset()) {
      persistSampleText(text);
    }
  }, []);
  const [testerState, setTesterStateState] = useState(null);
  // Wrap setTesterState to also persist to the session when in static mode
  const setTesterState = useCallback((state) => {
    setTesterStateState(state);
    if (isStaticMode() && isUploadDataset()) {
      persistTesterState(state);
    }
  }, []);
  const [fontSize, setFontSize] = useState(2); // Default 2rem
  const [familyName, setFamilyName] = useState(null);
  // Render-fresh mirror for the poll closure (its deps don't include
  // familyName) + an in-flight latch: a tab opened while the server was
  // down/restarting fails its initial loadData and would otherwise show
  // "No font loaded" forever — the poll re-runs the full load once
  // health shows a loaded source again.
  const familyNameRef = useRef(null);
  familyNameRef.current = familyName;
  const blindRecoverRef = useRef(false);
  // True while any loadData runs: the self-heal below must never start
  // a SECOND concurrent loadData during the initial one (familyName
  // stays null until its tail, so the poll would otherwise double-load
  // on every normal page open).
  const loadDataInFlightRef = useRef(false);
  // "glyphs" | "designspace" | null — from /api/health. Gates
  // control-axis authoring, which is .glyphs-only for now.
  const [sourceFormat, setSourceFormat] = useState(null);
  // Font family used for FontFace registration; comes from /api/health so the
  // tool works on any .glyphs file, not just Crispy.
  const [vfFamilyId, setVfFamilyId] = useState(null);
  // Hosted shared-demo instance (health.demo).
  const [demoMode, setDemoMode] = useState(false);
  const [showLanding, setShowLanding] = useState(false);
  const demoInitRef = useRef(false);
  // Built avar2 font filename (used for the download button); from /api/health.
  const [builtFontFilename, setBuiltFontFilename] = useState(null);
  // A ref, deliberately not state: the poll compares against it to
  // decide "the font was rebuilt — reload". As state, the comparison
  // read a render-closure value that lags the setter by however long
  // React takes to commit — and a big font's FontFace re-parse can
  // starve the main thread past the next poll tick, so every tick saw
  // the stale value and reloaded again: a self-sustaining reload spiral
  // for the first ~40s after load (shipped once, with the slnt-doubled
  // Crispy). A ref updates synchronously; duplicate reloads can't fire.
  const lastBuildTimeRef = useRef(null);
  // avar2Mode is now always true when avar2 data exists (no toggle needed)
  const [avar2Mode] = useState(true);
  const [avar2Instances, setAvar2Instances] = useState([]);
  const [avar2Axes, setAvar2Axes] = useState(null);
  // CONTROL AXES — v1 read-only. Populated from /api/glyph-coverage.
  // Disabled set is session-local: when an axis tag is in the set,
  // the preview pins it to its default value at render time.
  const [glyphCoverageAxes, setGlyphCoverageAxes] = useState([]);
  // Glyph-name → displayable character (from the source's unicode
  // data) — layer thumbnails render text, so "eight" needs "8".
  const [glyphChars, setGlyphChars] = useState({});
  const [disabledControlAxes, setDisabledControlAxes] = useState(new Set());
  // Static demo: another tab holds this workspace's session lock
  // (BroadcastChannel; IndexedDB has no storage events).
  const [sessionLockLost, setSessionLockLost] = useState(false);
  // Tracks the most recent ``glyphs_path`` we loaded data for. Used by
  // loadData() to detect a source swap (Load Font dropdown → new font)
  // so we can clear per-instance state. A polling-tick reload to the
  // same source must NOT wipe in-progress edits, so we only reset
  // when this ref's value actually changes.
  const loadedGlyphsPathRef = useRef(null);
  // Bumped by loadData() when it detects a source swap; the instance-
  // editing state (keyed by the old source's axis tags + instance
  // names) clears itself in useInstanceEditing on this signal. loadData
  // can't clear it directly — that state lives below this provider.
  const [sourceSwapCount, setSourceSwapCount] = useState(0);
  // SPAC support is deferred from v1; the spacMode/spacAxisExists/
  // spacValues state, the checkSpacAxisStatus() helper, and the
  // loadSpacValues() helper were all removed when SPAC was pulled
  // from the project context.
  const [glyphsFileHasUnsavedChanges, setGlyphsFileHasUnsavedChanges] = useState(false);
  const [avar2PreviewMode, setAvar2PreviewMode] = useState(false); // New mode: Default vs Avar2 Preview
  const [avar2FontUrl, setAvar2FontUrl] = useState(null);
  const [avar2FontLoaded, setAvar2FontLoaded] = useState(false);
  const [jumpLocation] = useState(null);
  // Render-fresh mirrors for the 2 s poll closure: which row is selected
  // (written by useInstanceEditing) and the latest loadData, so the
  // interval never has to be torn down to see new values.
  const selectedInstanceNameRef = useRef(null);
  const loadDataRef = useRef(null);

  // Load initial data
  useEffect(() => {
    loadDataRef.current();
    // Preload avar2 data so it's ready when toggled
    loadAvar2Data().catch(() => {
      // Silently fail - avar2 is optional
    });
    // Preload CONTROL AXES coverage. Hidden in the UI unless the
    // source ships glyph-scoped variation.
    loadGlyphCoverage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Intentionally empty - only run on mount

  // Load avar2 data on mount (always enabled now)
  useEffect(() => {
    // Load avar2 data if not already loaded
    if (avar2Instances.length === 0 && !avar2Axes) {
      loadAvar2Data();
    }
  }, [avar2Instances.length, avar2Axes]); // eslint-disable-line react-hooks/exhaustive-deps

  // Poll for font rebuilds and Glyphs file unsaved changes (check every 2 seconds)
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const [health, glyphsStatus] = await Promise.all([
          api.health(),
          api.glyphsFileStatus().catch(() => ({ has_unsaved_changes: false }))
        ]);

        // Hosted shared-demo instance: show the landing overlay on
        // every page load; Enter dismisses it until the next load.
        if (health.demo && !demoInitRef.current) {
          demoInitRef.current = true;
          setDemoMode(true);
          setShowLanding(true);
        }

        applyHealth(health);
        setAvar2Error(health.avar2_error || null);
        setOriginRelocationError(health.origin_relocation_error || null);
        setSourceTransformError(health.source_transform_error || null);
        setOriginWarning(health.origin_warning || null);
        setSessionLockLost(!!health.session_lock_lost);
        setGlyphsFileHasUnsavedChanges(glyphsStatus.has_unsaved_changes || false);

        // Register the preview font as soon as a build exists — the poll
        // answers in milliseconds while loadData's endpoint chain takes tens
        // of seconds on big sources, and the preview sat on the placeholder
        // font until it finished.
        if (health.font_built) {
          setFontUrl(prev => prev ?? api.getFontUrl());
          setFontLoaded(true);
        }

        // Self-heal the blind empty state: the initial loadData failed
        // (server down or mid-restart when the tab loaded), but health
        // now shows a loaded source — run the full load.
        if (!familyNameRef.current && health.family_name
            && !blindRecoverRef.current && !loadDataInFlightRef.current) {
          blindRecoverRef.current = true;
          loadDataRef.current().finally(() => { blindRecoverRef.current = false; });
          return;
        }

        // If font was rebuilt (new build time), reload
        if (health.font_built && health.last_build_time && health.last_build_time !== lastBuildTimeRef.current) {
          // Store scroll position before reloading
          const scrollY = window.scrollY;
          const selectedInstanceName = selectedInstanceNameRef.current;

          lastBuildTimeRef.current = health.last_build_time;
          // Force font reload by generating new URL with timestamp
          setFontLoaded(false); // Reset first to trigger reload
          setTimeout(() => {
            setFontUrl(api.getFontUrl()); // New URL with fresh timestamp
            setFontLoaded(true);
          }, 100);
          // Reload instances and axes in case they changed
          const [instancesData, axesData] = await Promise.all([
            api.getInstances(),
            api.getAxes(),
          ]);

          setInstances(instancesData.instances);
          setAxes(axesData.axes || []);

          // Restore scroll position after a brief delay
          if (selectedInstanceName) {
            setTimeout(() => {
              const element = document.querySelector(`[data-instance-name="${selectedInstanceName}"]`);
              if (element) {
                element.scrollIntoView({ behavior: 'auto', block: 'nearest' });
              } else {
                // Fallback to stored scroll position
                window.scrollTo(0, scrollY);
              }
            }, 200);
          }
        }
      } catch (err) {
        // Silently fail polling errors
        console.debug('Polling error:', err);
      }
    }, 2000); // Poll every 2 seconds

    return () => clearInterval(interval);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadData = async () => {
    try {
      loadDataInFlightRef.current = true;
      // The full-screen gate is first-load only; refreshes keep the UI up.
      if (!firstLoadDoneRef.current) setLoading(true);
      setError(null);

      // Check health and font status
      const health = await api.health();

      // Detect a source swap. The Load Font dropdown ultimately calls
      // loadData() with a new source; the polling tick calls loadData()
      // with the same source. Per-instance state (selectedInstance,
      // editingCoordinates, scratch maps) is keyed by axis tags +
      // instance names that belong to the OLD source — none of those
      // are valid in the new source, and leaving them around makes
      // the Sidebar slider dict read NaN and the InstanceRow grid
      // render with the wrong axis values.
      //
      // Key off original_path, not glyphs_path. CONTROL AXES swaps
      // glyphs_path from original → shadow but the user is still
      // working on the same source; treating that as a swap would
      // throw away their in-progress edits on every axis declaration.
      const sourceIdentity = health.original_path || health.glyphs_path || null;
      const isSourceSwap = (
        loadedGlyphsPathRef.current !== null &&
        loadedGlyphsPathRef.current !== sourceIdentity
      );
      if (isSourceSwap) {
        // useInstanceEditing watches this and drops its per-instance
        // state (selection, coordinate drafts, sync baselines).
        setSourceSwapCount(c => c + 1);
        // Clear avar2 state only on source swap — the mappings belong
        // to the old source and would leak into the new one.
        setAvar2Instances([]);
        setAvar2Axes(null);
      }
      loadedGlyphsPathRef.current = sourceIdentity;

      // Blind launch: no source loaded server-side. Skip the auto-build
      // (there's nothing to build) — the Header's Load Font dropdown
      // will swap a source in, after which loadData re-runs.
      if (!health.glyphs_path) {
        setFontLoaded(false);
        setFamilyName(null);
        setSourceFormat(null);
        setInstances([]);
        setAxes([]);
        setLoading(false);
        firstLoadDoneRef.current = true;
        return;
      }

      // Clear the blind empty state IMMEDIATELY: a loaded source is
      // known from this first health response, but familyName used to
      // be set only at the tail of loadData — on a heavy font the boot
      // takes ~30-40s (slow endpoints + a 9MB font parse), and the UI
      // showed "No font loaded" (with its use-Load-Font advice) the
      // whole time, reading as broken while it was merely loading.
      setFamilyName(health.family_name || null);
      setSourceFormat(health.source_format || null);
      setVfFamilyId(health.vf_family_id
        || (health.family_name ? `${health.family_name}-VF` : null));

      // If font is not built, trigger auto-build on hard reset
      if (!health.font_built && !health.building) {
        try {
          await api.buildFont();
          // Reload health to get updated status
          const updatedHealth = await api.health();
          setFontLoaded(updatedHealth.font_built);
          lastBuildTimeRef.current = updatedHealth.last_build_time || null;
        } catch (err) {
          console.error('Auto-build on load failed:', err);
          // Continue loading even if build fails
        }
      }

      const [instancesData, axesData, mastersData, transformsData, gradeData] = await Promise.all([
        api.getInstances(),
        api.getAxes(),
        api.capabilities.masters ? api.getMasters().catch(() => ({ masters: [] })) : Promise.resolve({ masters: [] }),
        api.getTransforms().catch(() => ({ transforms: [] })),
        api.getGrade().catch(() => ({ enabled: false, default_pct: 0.25, intensity: 1, clamp_to_headroom: true, instances: [], max_pct: {}, diagnostics: [] })),
      ]);

      setInstances(instancesData.instances);
      setMasters(mastersData.masters || []);
      setAxes(axesData.axes || []);
      setTransforms(transformsData.transforms || []);
      setGrade({
        enabled: !!gradeData.enabled,
        default_pct: gradeData.default_pct ?? 0.25,
        intensity: gradeData.intensity ?? 1,
        clamp_to_headroom: gradeData.clamp_to_headroom !== false,
        instances: gradeData.instances || [],
        max_pct: gradeData.max_pct || {},
        diagnostics: gradeData.diagnostics || [],
      });
      setFontLoaded(health.font_built);
      setFamilyName(health.family_name || null);
      setSourceFormat(health.source_format || null);
      setVfFamilyId(health.vf_family_id || (health.family_name ? `${health.family_name}-VF` : null));
      setBuiltFontFilename(health.built_font_filename || null);
      const prevBuildTime = lastBuildTimeRef.current;
      lastBuildTimeRef.current = health.last_build_time || null;
      applyHealth(health);
      setAvar2Error(health.avar2_error || null);
      setOriginRelocationError(health.origin_relocation_error || null);
      setSourceTransformError(health.source_transform_error || null);
      setOriginWarning(health.origin_warning || null);

      // Restore persisted sample text (static mode with upload only)
      if (isStaticMode() && isUploadDataset()) {
        const savedSample = getSampleText();
        if (savedSample) setSampleTextState(savedSample);
        const savedTesterState = getTesterState();
        if (savedTesterState) setTesterStateState(savedTesterState);
      }

      // avar2 mappings are per-dataset: refetch on every loadData to get
      // the latest CSV state. State is only CLEARED on source swap (see
      // above) — on rebuilds the refetch carries the same values through.
      loadAvar2Data().catch(() => {});

      // If font was rebuilt (new build time), reload the font
      if (health.font_built && health.last_build_time && health.last_build_time !== prevBuildTime) {
        setFontUrl(api.getFontUrl()); // This is synchronous, returns string
        // Force font reload by updating fontLoaded state
        setFontLoaded(true);
      } else if (health.font_built && !fontUrl) {
        setFontUrl(api.getFontUrl());
      }

      // CONTROL AXES coverage is source-derived; refetch on every
      // loadData (source swap, post-build reload). Disabled-set
      // resets too — a new source's axes are a fresh slate.
      loadGlyphCoverage();
      setDisabledControlAxes(new Set());
    } catch (err) {
      setError(err.message);
      console.error('Failed to load data:', err);
    } finally {
      loadDataInFlightRef.current = false;
      firstLoadDoneRef.current = true;
      setLoading(false);
    }
  };
  loadDataRef.current = loadData;

  // useCallback([]): these close over setters + the api module only, so a
  // stable identity is identical behavior — and consumers (the auto-save
  // effect in useInstanceEditing) can list them as deps without the
  // debounce resetting on every provider render.
  const loadAvar2Data = useCallback(async () => {
    try {
      // No-op without a loaded source: the avar2 endpoints 404 when
      // there's no CSV, which blind launch (and only blind launch) is
      // guaranteed to hit — firing them anyway litters the console
      // with errors on an otherwise clean empty state.
      const health = await api.health().catch(() => null);
      if (!health || !health.glyphs_path) {
        setAvar2Instances([]);
        setAvar2Axes(null);
        return;
      }

      // Load both in parallel, but update state as soon as each arrives
      const instancesPromise = api.getAvar2Instances().then(data => {
        setAvar2Instances(data.instances || []);
        return data;
      });

      const axesPromise = api.getAvar2Axes().then(data => {
        setAvar2Axes(data);
        return data;
      });

      // Wait for both to complete (but state updates happen immediately)
      await Promise.all([instancesPromise, axesPromise]);

      // avar2Mode is always enabled, no need to set it
    } catch (err) {
      console.error('Failed to load avar2 data:', err);
      // Don't show error to user - avar2 mode is optional
      setAvar2Instances([]);
      setAvar2Axes(null);
    }
  }, []);

  // CONTROL AXES — coverage fetch. Categorised server-side as
  // universal / scoped; the Sidebar's ControlAxes component filters
  // to scoped and hides the section if none exist (and there's no
  // declare affordance). Reloaded whenever the source changes.
  const loadGlyphCoverage = useCallback(async () => {
    try {
      const data = await api.getGlyphCoverage();
      setGlyphCoverageAxes(data.axes || []);
      setGlyphChars(data.glyph_chars || {});
    } catch (err) {
      // Silent — the endpoint returns [] on no-source-loaded; any
      // hard failure just leaves the CONTROL AXES panel empty.
      setGlyphCoverageAxes([]);
      setGlyphChars({});
    }
  }, []);

  const handleToggleDisableControlAxis = useCallback((tag) => {
    setDisabledControlAxes(prev => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }, []);

  const handleBuildFont = async () => {
    try {
      setBuilding(true);
      setError(null);
      await api.buildFont();
      setFontLoaded(true);
      setFontUrl(api.getFontUrl()); // This is synchronous, returns string
      // Reload axes from built font
      const axesData = await api.getAxes();
      setAxes(axesData.axes);
    } catch (err) {
      setError(err.message);
      console.error('Build failed:', err);
    } finally {
      setBuilding(false);
    }
  };

  const handleAddAvar2Axis = async (axisData) => {
    try {
      const result = await api.addAvar2Axis(axisData);
      // Reload avar2 data to get updated axes and instances
      // Add a small delay to ensure backend has written files
      await new Promise(resolve => setTimeout(resolve, 300));
      await loadAvar2Data();
    } catch (err) {
      console.error('Failed to add axis:', err);
      throw err; // Re-throw to let modal handle error display
    }
  };

  const handleUpdateAvar2Axis = async (axisName, axisData) => {
    try {
      await api.updateAvar2Axis(axisName, axisData);
      // Reload avar2 data to get updated metadata
      await loadAvar2Data();
    } catch (err) {
      console.error('Failed to update axis:', err);
      throw err; // Re-throw to let component handle error display
    }
  };

  const handleDeleteAvar2Axis = async (axisName) => {
    await api.deleteAvar2Axis(axisName);
    await loadAvar2Data();
  };

  const handleUpdateAvar2Mapping = async (instanceName, axisName, value) => {
    try {
      await api.updateAvar2Mapping(instanceName, axisName, value);
      // Reload avar2 data to get updated instances
      await loadAvar2Data();
    } catch (err) {
      console.error('Failed to update mapping:', err);
      // Check if it's an external edit error
      if (err.message && err.message.includes('externally')) {
        // Reload data and show error
        await loadAvar2Data();
      }
      throw err; // Re-throw to let component handle error display
    }
  };

  // SPAC helpers (checkSpacAxisStatus / loadSpacValues / handleSpacModeChange)
  // were removed when SPAC support was deferred. The state, props, and
  // backend endpoints they relied on are all dormant.

  const handleAvar2PreviewModeChange = async (enabled) => {
    setAvar2PreviewMode(enabled);

    if (enabled) {
      // Switch to Avar2 Preview mode
      // Load avar2 data if not already loaded
      if (avar2Instances.length === 0 || !avar2Axes) {
        await loadAvar2Data();
      }

      // Load avar2 font if it exists
      try {
        const fontUrl = api.getAvar2FontUrl();
        // Try to load font to check if it exists
        const response = await fetch(fontUrl);
        if (response.ok) {
          setAvar2FontUrl(fontUrl);
          setAvar2FontLoaded(true);

          // Load font using FontFace API
          if (!vfFamilyId) {
            throw new Error("vfFamilyId not yet known");
          }
          const fontFace = new FontFace(vfFamilyId, `url(${fontUrl})`);
          await fontFace.load();
          document.fonts.add(fontFace);
          await document.fonts.ready;
        } else {
          // Font doesn't exist yet, user needs to build it
          setAvar2FontLoaded(false);
        }
      } catch (err) {
        // Font doesn't exist yet
        setAvar2FontLoaded(false);
      }
    } else {
      // Switch back to Default mode — serve the main built font.
      setFontUrl(api.getFontUrl());
      setAvar2FontUrl(null);
      setAvar2FontLoaded(false);
    }
  };

  // SPAC rebuild handler removed alongside the rest of SPAC support.

  // Axis tag → default value lookup. Used by InstanceRow's
  // preview-coordinates memo to pin disabled control axes to their
  // axis default at render time without mutating the user's chosen
  // slider value.
  const axisDefaults = React.useMemo(
    () => Object.fromEntries((axes || []).map(a => [a.tag, a.default])),
    [axes],
  );

  // Preview-only axes: transform-injected (SPAC) and secondary
  // parametric (control) axes are live preview state, never
  // per-instance data. Excluded from dirtiness checks and save
  // payloads — the server refuses to persist them anyway (and on the
  // static app they'd become instances-CSV columns), so counting them
  // as "edits" left dots permanently red and saves apparent no-ops.
  const injectedAxisTags = React.useMemo(
    // LOWERCASED on purpose. fvar carries tags as authored ('lcwd', 'SPAC')
    // while instance coordinates are keyed by CSV column name, which is
    // upper-cased ('LCWD'). Comparing the two literally never matched, so
    // preview-only axes leaked into saved instances as blank columns — and a
    // blank column reads as a required-but-empty parametric axis, which fails
    // the whole avar2 build.
    () => new Set((axes || [])
      .filter(a => a.transform_injected || a.is_control_axis)
      .map(a => String(a.tag).toLowerCase())),
    [axes],
  );

  // Injected axes that are genuinely preview-only (GRAD/ROND — their
  // per-instance values live in badge-managed sidecars). SPAC persists
  // via the CSV's per-instance column; CONTROL axes (ymod/lcwd) persist
  // via the control sidecar's instance_values — both flow through saves
  // like any coordinate and hydrate back from /api/instances.
  const previewOnlyAxisTags = React.useMemo(
    () => new Set((axes || [])
      .filter(a => a.transform_injected
        && String(a.tag).toLowerCase() !== 'spac')
      .map(a => String(a.tag).toLowerCase())),
    [axes],
  );

  // Secondary (control) axes alone — the only tags a multi-select
  // broadcast may touch.
  const controlAxisTags = React.useMemo(
    () => new Set((axes || [])
      .filter(a => a.is_control_axis)
      .map(a => String(a.tag).toLowerCase())),
    [axes],
  );

  // The DEFAULT-ORIGIN row(s): instances whose mapping inputs all sit
  // at the user-axis defaults. That row's parametric values define the
  // font's resting state — the build interpolates a real master there
  // and compiles with it as the origin (avar2 cannot remap the default
  // location, so this is the one row that works by being the origin
  // rather than by a mapping). Mirrors the server's _is_default_row:
  // every authored in-cell equals its axis's declared default; a blank
  // cell sits at the default by definition.
  const defaultOriginNames = React.useMemo(() => {
    const meta = avar2Axes?.metadata || {};
    const defs = {};
    for (const m of Object.values(meta)) {
      if (!m || m.is_parametric || m.default == null) continue;
      const tag = (m.registered_tag || '').toLowerCase();
      if (tag) defs[tag] = Number(m.default);
    }
    const out = new Set();
    if (!Object.keys(defs).length) return out;
    for (const inst of avar2Instances || []) {
      const ins = inst?.avar2_mapping?.in || {};
      const entries = Object.entries(ins);
      if (!entries.length) continue;
      const allDefault = entries.every(([t, v]) =>
        defs[t] != null && Math.abs(Number(v) - defs[t]) < 1e-6);
      if (allDefault) out.add(inst.instance_name);
    }
    return out;
  }, [avar2Instances, avar2Axes]);

  const value = {
    instances, setInstances,
    masters,
    axes, setAxes,
    transforms, setTransforms,
    grade, setGrade,
    fontLoaded, setFontLoaded,
    staticMode,
    fontUrl, setFontUrl,
    loading,
    error, setError,
    avar2Error,
    originRelocationError,
    sourceTransformError,
    originWarning,
    sampleText, setSampleText,
    testerState, setTesterState,
    fontSize, setFontSize,
    familyName,
    sourceFormat,
    vfFamilyId,
    demoMode,
    showLanding, setShowLanding,
    builtFontFilename,
    avar2Mode,
    avar2Instances,
    avar2Axes,
    glyphCoverageAxes,
    glyphChars,
    disabledControlAxes, setDisabledControlAxes,
    sessionLockLost,
    glyphsFileHasUnsavedChanges,
    sourceSwapCount,
    selectedInstanceNameRef,
    jumpLocation,
    loadData,
    loadAvar2Data,
    loadGlyphCoverage,
    buildFont: handleBuildFont,
    handleToggleDisableControlAxis,
    handleAddAvar2Axis,
    handleUpdateAvar2Axis,
    handleDeleteAvar2Axis,
    handleUpdateAvar2Mapping,
    axisDefaults,
    injectedAxisTags,
    previewOnlyAxisTags,
    controlAxisTags,
    defaultOriginNames,
  };

  return (
    <StudioDataContext.Provider value={value}>
      {children}
    </StudioDataContext.Provider>
  );
}

export function useStudioData() {
  const ctx = useContext(StudioDataContext);
  if (!ctx) {
    throw new Error('useStudioData() needs a <StudioDataProvider> above it (App mounts one).');
  }
  return ctx;
}
