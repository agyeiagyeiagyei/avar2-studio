import React, { useState, useEffect, useCallback, useRef } from 'react';
import './App.css';
import { api } from './api';
import { isStaticMode, isUploadDataset, getSampleText, setSampleText as persistSampleText } from './static-api';
import logoGif from './assets/logo.gif';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import InstanceRows from './components/InstanceRows';
import PreviewTab from './components/PreviewTab';
import SpaceTab from './components/SpaceTab';
import FontraEditorModal from './components/FontraEditorModal';
import DeleteInstanceModal from './components/DeleteInstanceModal';
import DemoLanding from './components/DemoLanding';

// Default sample text uses only ASCII letters, digits, and space so it
// renders cleanly against minimal fixtures (e.g. examples/crispy-mini,
// examples/roboto-delta-mini, which subset to A-Z + a-z + 0-9 + space).
// "&" and "!" were dropped from the historical Crispy default for the
// same reason.
const DEFAULT_SAMPLE_TEXT = "The Quick Brown Fox Jumps Over The Lazy Dog 0123456789";

/**
 * Encode a viewInfo dict the way Fontra's ``dumpURLFragment``
 * does: ``"#" + base64(zlib(JSON.stringify(obj)))``. Fontra's
 * implementation uses ``fflate.zlibSync`` (RFC 1950 zlib wrapper)
 * + a custom ``btoa(String.fromCodePoint(...bytes))``. We mirror
 * both — ``CompressionStream('deflate')`` in the browser is the
 * same zlib-wrapped output.
 */
async function encodeFontraFragment(viewInfo) {
  const json = JSON.stringify(viewInfo);
  const blob = new Blob([new TextEncoder().encode(json)]);
  const stream = blob.stream().pipeThrough(new CompressionStream('deflate'));
  const compressed = new Uint8Array(await new Response(stream).arrayBuffer());
  let binString = '';
  for (let i = 0; i < compressed.length; i++) {
    binString += String.fromCharCode(compressed[i]);
  }
  return '#' + btoa(binString);
}

function App() {
  const [instances, setInstances] = useState([]);
  // Parametric master corners — pre-listed in the control-axis brace
  // flow so the designer places a crbr view at a specific corner.
  const [masters, setMasters] = useState([]);
  // Post-build transforms (e.g. SPAC): available list merged with this
  // project's enabled state + params, from GET /api/transforms.
  const [transforms, setTransforms] = useState([]);
  // Grade transform (source-level, its own /api/transforms/grade sidecar):
  // { enabled, default_pct, instances: [{name, pct}], max_pct: {name: cap} }.
  const [grade, setGrade] = useState({ enabled: false, default_pct: 0.25, instances: [], max_pct: {} });
  const [axes, setAxes] = useState([]);
  const [selectedInstance, setSelectedInstance] = useState(null);
  const [editingCoordinates, setEditingCoordinates] = useState({});
  // Store editing coordinates per instance to persist when deselected
  const [instanceEditingCoordinates, setInstanceEditingCoordinates] = useState({});
  // Store original coordinates per instance for sync status comparison
  const [instanceOriginalCoordinates, setInstanceOriginalCoordinates] = useState({});
  const [fontLoaded, setFontLoaded] = useState(false);
  // Static demo (GitHub Pages): the api object was swapped for the
  // snapshot reader before render (index.jsx / static-api.js). Read-only.
  const [staticMode] = useState(isStaticMode());
  const [fontUrl, setFontUrl] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [building, setBuilding] = useState(false);
  // Non-null ⇒ the avar2 build is failing and the served font is the
  // plain fallback (no avar2 table / mapped axes). Surfaced on the
  // Preview tab so the fallback is never silent.
  const [avar2Error, setAvar2Error] = useState(null);
  // True ⇒ the CSV changed after the last build (auto-saved edits no
  // longer rebuild per save). The Preview tab rebuilds once on open.
  const [buildStale, setBuildStale] = useState(false);
  const [sampleText, setSampleTextState] = useState(DEFAULT_SAMPLE_TEXT);
  // Wrap setSampleText to also persist to the session when in static mode
  const setSampleText = useCallback((text) => {
    setSampleTextState(text);
    if (isStaticMode() && isUploadDataset()) {
      persistSampleText(text);
    }
  }, []);
  const [fontSize, setFontSize] = useState(2); // Default 2rem
  // Which top-level view is active: 'instances' (authoring) or
  // 'preview' (free-form driving of the built font).
  const [mainTab, setMainTab] = useState('instances');

  // Rebuild-on-demand: coordinate auto-saves no longer rebuild per
  // edit, so the built font can be stale when the designer opens the
  // Preview tab (the one place the avar2 table is actually consumed).
  // Kick ONE rebuild when arriving there stale; build_stale clears
  // when the build lands, so this can't loop.
  useEffect(() => {
    if (mainTab === 'preview' && buildStale && !building) {
      api.buildFont().catch(() => {});
    }
  }, [mainTab, buildStale, building]);
  const [familyName, setFamilyName] = useState(null);
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
  const [lastBuildTime, setLastBuildTime] = useState(null);
  // Outcome of the most recent build attempt. "failed" => the preview is stale
  // (we're still showing the last-good font); "ok" => preview reflects fresh
  // source state. Null until the first build completes.
  const [lastBuildStatus, setLastBuildStatus] = useState(null);
  const [lastBuildError, setLastBuildError] = useState(null);
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
  // Fontra iframe state — non-null = modal is open. The url comes
  // from POST /api/control-axes/<tag>/open-editor.
  const [fontraEditor, setFontraEditor] = useState(null);
  // Tracks the most recent ``glyphs_path`` we loaded data for. Used by
  // loadData() to detect a source swap (Load Font dropdown → new font)
  // so we can clear per-instance state. A polling-tick reload to the
  // same source must NOT wipe in-progress edits, so we only reset
  // when this ref's value actually changes.
  const loadedGlyphsPathRef = useRef(null);
  // SPAC support is deferred from v1; the spacMode/spacAxisExists/
  // spacValues state, the checkSpacAxisStatus() helper, and the
  // loadSpacValues() helper were all removed when SPAC was pulled
  // from the project context.
  const [glyphsFileHasUnsavedChanges, setGlyphsFileHasUnsavedChanges] = useState(false);
  const [avar2PreviewMode, setAvar2PreviewMode] = useState(false); // New mode: Default vs Avar2 Preview
  const [syncStatus, setSyncStatus] = useState(null);
  const [showBuildAvar2Modal, setShowBuildAvar2Modal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [instanceToDelete, setInstanceToDelete] = useState(null);
  const [avar2FontUrl, setAvar2FontUrl] = useState(null);
  const [avar2FontLoaded, setAvar2FontLoaded] = useState(false);
  const [coverage, setCoverage] = useState([]);
  const [coveragePins, setCoveragePins] = useState([]);
  const [jumpLocation, setJumpLocation] = useState(null);
  // Load initial data
  useEffect(() => {
    loadData();
    // Preload avar2 data so it's ready when toggled
    loadAvar2Data().catch(() => {
      // Silently fail - avar2 is optional
    });
    // Preload CONTROL AXES coverage. Hidden in the UI unless the
    // source ships glyph-scoped variation.
    loadGlyphCoverage();
    // Check sync status
    checkSyncStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Intentionally empty - only run on mount
  
  // Unregister instance when deselected or component unmounts
  useEffect(() => {
    const currentInstance = selectedInstance;
    return () => {
      // Cleanup: unregister instance when deselected or component unmounts
      if (currentInstance) {
        api.unregisterEditingInstance(currentInstance.name).catch(() => {});
      }
    };
  }, [selectedInstance]);

  // Load avar2 data on mount (always enabled now)
  useEffect(() => {
    // Load avar2 data if not already loaded
    if (avar2Instances.length === 0 && !avar2Axes) {
      loadAvar2Data();
    }
  }, [avar2Instances.length, avar2Axes]); // eslint-disable-line react-hooks/exhaustive-deps

  // Ensure selected instance is in CSV when avar2 data exists (avar2Mode always enabled now)
  const loadingAvar2Ref = useRef(false);
  useEffect(() => {
    if (loadingAvar2Ref.current) return;  // Prevent re-entrant loop
    if (selectedInstance && avar2Instances.length > 0) {
      const mapping = avar2Instances.find(
        inst => inst.instance_name === selectedInstance.name
      );
      // If instance not in CSV, it will be added automatically by backend
      // when we fetch avar2 instances (backend handles missing instances)
      if (!mapping || mapping.match_status === 'missing_in_csv') {
        loadingAvar2Ref.current = true;
        loadAvar2Data().finally(() => {
          loadingAvar2Ref.current = false;
        });
      }
    }
  }, [selectedInstance, avar2Instances]); // eslint-disable-line react-hooks/exhaustive-deps

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

        setBuilding(health.building || false);
        setAvar2Error(health.avar2_error || null);
        setBuildStale(health.build_stale || false);
        setGlyphsFileHasUnsavedChanges(glyphsStatus.has_unsaved_changes || false);
        setLastBuildStatus(health.last_build_status || null);
        setLastBuildError(health.last_build_error || null);

        // If font was rebuilt (new build time), reload
        if (health.font_built && health.last_build_time && health.last_build_time !== lastBuildTime) {
          // Store scroll position before reloading
          const scrollY = window.scrollY;
          const selectedInstanceName = selectedInstance?.name;
          
          setLastBuildTime(health.last_build_time);
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
  }, [lastBuildTime, selectedInstance?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadData = async () => {
    try {
      setLoading(true);
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
        setSelectedInstance(null);
        setEditingCoordinates({});
        setOriginalCoordinates({});
        setInstanceEditingCoordinates({});
        setInstanceOriginalCoordinates({});
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
        return;
      }

      // If font is not built, trigger auto-build on hard reset
      if (!health.font_built && !health.building) {
        try {
          await api.buildFont();
          // Reload health to get updated status
          const updatedHealth = await api.health();
          setFontLoaded(updatedHealth.font_built);
          setLastBuildTime(updatedHealth.last_build_time || null);
        } catch (err) {
          console.error('Auto-build on load failed:', err);
          // Continue loading even if build fails
        }
      }
      
      const [instancesData, axesData, mastersData, transformsData, gradeData, coverageData] = await Promise.all([
        api.getInstances(),
        api.getAxes(),
        api.getMasters().catch(() => ({ masters: [] })),
        api.getTransforms().catch(() => ({ transforms: [] })),
        api.getGrade().catch(() => ({ enabled: false, default_pct: 0.25, instances: [], max_pct: {} })),
        api.getCoverage ? api.getCoverage().catch(() => ({ findings: [] })) : Promise.resolve({ findings: [] }),
      ]);

      setInstances(instancesData.instances);
      setMasters(mastersData.masters || []);
      setAxes(axesData.axes || []);
      setTransforms(transformsData.transforms || []);
      setCoverage(coverageData.findings || []);
      setCoveragePins(coverageData.pins || []);
      setGrade({
        enabled: !!gradeData.enabled,
        default_pct: gradeData.default_pct ?? 0.25,
        instances: gradeData.instances || [],
        max_pct: gradeData.max_pct || {},
      });
      setFontLoaded(health.font_built);
      setFamilyName(health.family_name || null);
      setSourceFormat(health.source_format || null);
      setVfFamilyId(health.vf_family_id || (health.family_name ? `${health.family_name}-VF` : null));
      setBuiltFontFilename(health.built_font_filename || null);
      setLastBuildTime(health.last_build_time || null);
      setBuilding(health.building || false);
      setAvar2Error(health.avar2_error || null);
      setBuildStale(health.build_stale || false);

      // Restore persisted sample text (static mode with upload only)
      if (isStaticMode() && isUploadDataset()) {
        const savedSample = getSampleText();
        if (savedSample) setSampleTextState(savedSample);
      }

      // avar2 mappings are per-dataset: refetch on every loadData to get
      // the latest CSV state. State is only CLEARED on source swap (see
      // above) — on rebuilds the refetch carries the same values through.
      loadAvar2Data().catch(() => {});

      // If font was rebuilt (new build time), reload the font
      if (health.font_built && health.last_build_time && health.last_build_time !== lastBuildTime) {
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
      setLoading(false);
    }
  };

  const loadAvar2Data = async () => {
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
  };

  // CONTROL AXES — coverage fetch. Categorised server-side as
  // universal / scoped; the Sidebar's ControlAxes component filters
  // to scoped and hides the section if none exist (and there's no
  // declare affordance). Reloaded whenever the source changes.
  const loadGlyphCoverage = async () => {
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
  };

  const handleToggleDisableControlAxis = useCallback((tag) => {
    setDisabledControlAxes(prev => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }, []);

  // CONTROL AXES — declare + remove. The backend regenerates the
  // shadow .glyphs file and rebuilds; we refetch axes + coverage +
  // font URL so the new slider appears and the preview swaps to the
  // shadow build. loadData() can't be used here because it would
  // re-fire the source-swap detection mid-edit and clear state.
  const refreshAfterControlAxisChange = useCallback(async () => {
    try {
      const [axesData] = await Promise.all([
        api.getAxes(),
        loadGlyphCoverage(),
      ]);
      setAxes(axesData.axes || []);
      // Cache-bust the font URL so the preview picks up the rebuilt
      // shadow font. trigger_build ran server-side; the next URL hit
      // gets the new bytes.
      setFontUrl(api.getFontUrl());
    } catch (err) {
      console.warn('Failed to refresh after control-axis change:', err);
    }
  }, []);

  const handleCreateControlAxis = useCallback(async (axis) => {
    await api.createControlAxis(axis);
    await refreshAfterControlAxisChange();
  }, [refreshAfterControlAxisChange]);

  const handleUpdateControlAxis = useCallback(async (axis) => {
    await api.updateControlAxis(axis.tag, {
      display_name: axis.display_name,
      default: axis.default,
      min: axis.min,
      max: axis.max,
    });
    await refreshAfterControlAxisChange();
  }, [refreshAfterControlAxisChange]);

  const handleDeleteControlAxis = useCallback(async (tag) => {
    try {
      await api.deleteControlAxis(tag);
      await refreshAfterControlAxisChange();
      // Drop it from the disabled set if it was disabled.
      setDisabledControlAxes(prev => {
        if (!prev.has(tag)) return prev;
        const next = new Set(prev);
        next.delete(tag);
        return next;
      });
    } catch (err) {
      setError(err.message || `Failed to delete secondary parametric axis "${tag}"`);
    }
  }, [refreshAfterControlAxisChange]);

  // CONTROL AXES — unified PUT /layers replaces the two old
  // endpoints (coverage + extra-locations). Every brace layer is
  // explicit; coverage is derived server-side.
  const handleSetControlAxisLayers = useCallback(async (tag, layers) => {
    await api.setControlAxisLayers(tag, layers);
    await refreshAfterControlAxisChange();
  }, [refreshAfterControlAxisChange]);

  // Interactive layer edits go through the delta endpoint, never a whole-list
  // replace: the modal now closes before the refetch lands, so a list rebuilt
  // from our cached copy would silently drop layers added moments earlier.
  const handleControlAxisLayerDelta = useCallback(async (tag, delta) => {
    await api.controlAxisLayerDelta(tag, delta);
    await refreshAfterControlAxisChange();
  }, [refreshAfterControlAxisChange]);

  // CONTROL AXES — open the shadow in Fontra.
  // ``url`` is the same-origin proxied path; ``direct_url`` is the
  // raw cross-origin URL (the iframe currently uses direct_url to
  // unblock runtime fetches that bypass the HTML rewriter).
  //
  // When a layer's location is provided, we build a Fontra
  // viewInfo dict and encode it as a URL FRAGMENT — the canonical
  // way Fontra's editor.js loads bookmarked state. Query params
  // race against async state hydration; the fragment goes through
  // ``loadURLFragment`` which feeds ``updateSceneSettingsFromViewInfo``
  // with proper waitKeyBefore/waitKeyAfter barriers (text waits
  // for characterLines, selection waits for positionedLines, etc).
  //
  // viewInfo dict shape:
  //   - text:         "e" or "/e" — what to render in the text view
  //   - location:     full {axis_tag: value} for the brace layer
  //                   (sparse pins overlaid on source axis defaults)
  //   - selectedGlyph: {lineIndex:0, glyphIndex:0, isEditing:true}
  //                   — drops the editor into edit mode on cell 0
  const handleOpenControlAxisInEditor = useCallback(async (tag, glyphName, layerLocation) => {
    try {
      setError(null);
      const data = await api.openControlAxisInEditor(tag, glyphName);
      let url = data.url;
      let directUrl = data.direct_url;
      if (glyphName) {
        const textValue = glyphName.length === 1 ? glyphName : '/' + glyphName;
        const viewInfo = {
          text: textValue,
          selectedGlyph: { lineIndex: 0, glyphIndex: 0, isEditing: true },
        };
        // Build the FULL location vector by overlaying the sparse
        // layer location on top of each axis's default. Pass
        // every axis the source declares so Fontra's location
        // matches a real brace layer point.
        //
        // SPACE CONTRACT: layerLocation arrives in USER space —
        // LayersEditor passes `location_user`, which the backend
        // derives by inverting designspace axis maps (identity for
        // .glyphs and unmapped axes). The axis defaults from
        // /api/axes are user space too, so the mix is consistent
        // with Fontra's fontLocationUser. A null layerLocation
        // means the map wasn't invertible: we still open the glyph,
        // just without a location fragment.
        //
        // KEY: Fontra-glyphs builds its location dicts keyed by
        // ``axis.name`` (the display name — "Crossbar",
        // "X-Transparency") rather than ``axis.tag`` (the 4-char
        // OT code). Our internal layer.location uses tags; we
        // translate at the URL-fragment boundary so Fontra's
        // ``fontLocationUser`` setting matches its own
        // brace-layer locations and the editor navigates there.
        if (layerLocation && axes && axes.length > 0) {
          const fullLocation = {};
          for (const axis of axes) {
            const value = (layerLocation[axis.tag] !== undefined)
              ? Number(layerLocation[axis.tag])
              : Number(axis.default);
            // Use the axis's display name, not the tag.
            fullLocation[axis.name || axis.tag] = value;
          }
          viewInfo.location = fullLocation;
        }
        const fragment = await encodeFontraFragment(viewInfo);
        url = url + fragment;
        directUrl = directUrl + fragment;
      }
      const controlValue = (layerLocation && layerLocation[tag] !== undefined)
        ? Number(layerLocation[tag]) : null;
      const axisName = ((axes || []).find(a => a.tag === tag) || {}).name || tag;
      setFontraEditor({ url, directUrl, tag, glyphName, controlValue, axisName, editingOriginal: !!data.editing_original });
    } catch (err) {
      setError(err.message || `Failed to open Fontra for "${tag}"`);
    }
  }, [axes]);

  // Close the Fontra modal and rebuild the font so the studio
  // preview reflects whatever the designer drew. Fontra writes to
  // the shadow on save; trigger_build picks up the new outlines.
  const handleCloseFontraEditor = useCallback(async () => {
    setFontraEditor(null);
    try {
      // Rebuild from the shadow. /api/build hits trigger_build on
      // the server with the current GLYPHS_PATH (= shadow when any
      // control axis has coverage).
      await api.buildFont();
      // Refresh font URL so the preview reloads the rebuilt font.
      setFontUrl(api.getFontUrl());
    } catch (err) {
      console.warn('Rebuild after Fontra edits failed:', err);
    }
  }, []);

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
    () => new Set((axes || []).filter(a => a.transform_injected || a.is_control_axis).map(a => a.tag)),
    [axes],
  );

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

  const checkSyncStatus = async () => {
    try {
      const status = await api.checkSyncStatus();
      setSyncStatus(status);
    } catch (err) {
      console.error('Failed to check sync status:', err);
      setSyncStatus({ synced: false, message: 'Failed to check sync status' });
    }
  };

  const handleBuildAvar2Font = async ({ traditionalAxes, avar2Axes }) => {
    try {
      setBuilding(true);
      setError(null);

      const result = await api.buildAvar2Font(traditionalAxes, avar2Axes);
      
      // Update sync status from response
      if (result.sync_status) {
        setSyncStatus(result.sync_status);
      }
      
      // If in avar2 preview mode, load the font
      // Also auto-switch to avar2 preview mode after successful build
      if (!avar2PreviewMode) {
        setAvar2PreviewMode(true);
      }
      
      const fontUrl = api.getAvar2FontUrl();
      setAvar2FontUrl(fontUrl);
      setAvar2FontLoaded(true);
      
      // Load font using FontFace API
      try {
        if (!vfFamilyId) {
          throw new Error("vfFamilyId not yet known — health check has not returned family_name");
        }
        // Remove old font if it exists to force reload
        const oldFont = Array.from(document.fonts).find(f => f.family === vfFamilyId);
        if (oldFont) {
          document.fonts.delete(oldFont);
        }

        const fontFace = new FontFace(vfFamilyId, `url(${fontUrl})`);
        await fontFace.load();
        document.fonts.add(fontFace);
        await document.fonts.ready;
      } catch (err) {
        console.error('Failed to load avar2 font:', err);
      }
      
      // Show success message (could be a toast notification)
      
      return result;
    } catch (err) {
      setError(err.message || 'Failed to build avar2 font');
      throw err;
    } finally {
      setBuilding(false);
    }
  };

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

  // ---- Post-build transforms (e.g. SPAC) ----
  const transformCommitTimer = useRef(null);

  const _transformEntries = (list) =>
    list.map(t => ({ type: t.id, enabled: !!t.enabled, params: { ...(t.params || {}) } }));

  // PUT the transform set, then a FULL loadData(): a transform like SPAC
  // adds/removes an fvar axis AND changes advances, so instances, axes and
  // the preview font all need refreshing — not just getAxes. `prev` is the
  // transforms state before the optimistic update, restored on failure so a
  // rejected PUT (e.g. invalid range → 400) doesn't leave the toggle/inputs
  // lying about what the server actually stored.
  const commitTransforms = async (entries, prev) => {
    try {
      setBuilding(true);
      setError(null);
      const result = await api.updateTransforms(entries);
      if (result.transforms) setTransforms(result.transforms);
      await loadData();
      // The base font can build while an enabled transform still failed
      // (bad params, missing binary). Surface it rather than silently no-op.
      if (result.transform_error) setError(`Transform: ${result.transform_error}`);
    } catch (err) {
      if (prev) setTransforms(prev);            // revert optimistic UI to last known-good
      setError(err.message);
      console.error('Transform update failed:', err);
    } finally {
      setBuilding(false);
    }
  };

  const handleToggleTransform = (id, enabled) => {
    if (transformCommitTimer.current) {
      clearTimeout(transformCommitTimer.current);
      transformCommitTimer.current = null;
    }
    const prev = transforms;
    const next = transforms.map(t => (t.id === id ? { ...t, enabled } : t));
    setTransforms(next);                          // immediate checkbox feedback
    commitTransforms(_transformEntries(next), prev); // toggling is deliberate — rebuild now
  };

  const handleTransformParam = (id, key, value) => {
    const prev = transforms;
    const next = transforms.map(t =>
      t.id === id ? { ...t, params: { ...(t.params || {}), [key]: value } } : t
    );
    setTransforms(next);                        // immediate input feedback, no rebuild yet
    // Debounce the rebuild — a font recompile per keystroke is the expensive,
    // dishonest path the old SPAC code was pulled for. Commit ~0.8s after the
    // last edit.
    if (transformCommitTimer.current) clearTimeout(transformCommitTimer.current);
    transformCommitTimer.current = setTimeout(() => {
      transformCommitTimer.current = null;
      commitTransforms(_transformEntries(next), prev);
    }, 800);
  };

  // ---- Grade transform (source-level; toggle + default + per-instance) ----
  const gradeCommitTimer = useRef(null);

  // Toggle / default. Toggling (dis)appears the GRAD axis, so rebuild now.
  const commitGrade = async (patch, prevGrade) => {
    try {
      setBuilding(true);
      setError(null);
      const result = await api.setGrade(patch);
      setGrade(g => ({ ...g, ...result }));
      await loadData();
    } catch (err) {
      console.error('[App] Grade update failed:', err);
      if (prevGrade) setGrade(prevGrade);
      setError(err.message);
    } finally {
      setBuilding(false);
    }
  };

  const handleToggleGrade = (enabled) => {
    const prev = grade;
    setGrade(g => ({ ...g, enabled }));   // immediate checkbox feedback
    commitGrade({ enabled }, prev);
  };

  // The default only seeds NEW grades, so it never changes the built font —
  // persist it (debounced) with no rebuild. The backend skips the rebuild for
  // a default-only change.
  const handleGradeDefault = (pct) => {
    setGrade(g => ({ ...g, default_pct: pct }));   // immediate input feedback
    if (gradeCommitTimer.current) clearTimeout(gradeCommitTimer.current);
    gradeCommitTimer.current = setTimeout(() => {
      gradeCommitTimer.current = null;
      api.setGrade({ default_pct: pct }).catch(err => setError(err.message));
    }, 600);
  };

  // Per-instance grade% — committed ONLY on an explicit Save from the badge
  // popover (no rebuild while the user types). One save → one rebuild.
  const handleSaveInstanceGrade = async (instanceName, pct) => {
    const prev = grade;
    setGrade(g => {
      const others = g.instances.filter(e => e.name !== instanceName);
      return { ...g, instances: [...others, { name: instanceName, pct }] };
    });
    try {
      setBuilding(true);
      await api.setInstanceGrade(instanceName, pct);
      await loadData();
    } catch (err) {
      setGrade(prev);
      setError(err.message);
    } finally {
      setBuilding(false);
    }
  };

  const handleRemoveInstanceGrade = async (instanceName) => {
    const prev = grade;
    setGrade(g => ({ ...g, instances: g.instances.filter(e => e.name !== instanceName) }));
    try {
      setBuilding(true);
      await api.removeInstanceGrade(instanceName);
      await loadData();
    } catch (err) {
      setGrade(prev);
      setError(err.message);
    } finally {
      setBuilding(false);
    }
  };

  const [originalCoordinates, setOriginalCoordinates] = useState({});

  // Tri-state sync status:
  //   - 'red'    — local edits exist but haven't been persisted anywhere
  //   - 'orange' — persisted to the avar2-studio CSV but not reflected
  //                in the source file. Either a studio-only row (lives
  //                in CSV only) or a source-defined row whose CSV
  //                parametric values diverge from the source's.
  //   - 'green'  — source-defined AND source coords match CSV coords
  //                (or no CSV row exists yet and there are no edits)
  //
  // The orange→green transition only happens via the flyout's
  // "Save to source file" action. The flyout is reachable from any
  // non-green state (and from green for source instances too — that's
  // where the demote action lives).
  const _sameCoords = (a, b) => {
    if (!a || !b) return false;
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    for (const k of ka) {
      const va = Number(a[k]);
      const vb = Number(b[k]);
      if (!Number.isFinite(va) || !Number.isFinite(vb)) {
        if (a[k] !== b[k]) return false;
      } else if (Math.abs(va - vb) > 0.01) {
        return false;
      }
    }
    return true;
  };

  const getInstanceSyncStatus = useCallback((instance) => {
    // Resolve the user's "current edits" view for this instance.
    let edits;
    if (selectedInstance && selectedInstance.name === instance.name) {
      edits = editingCoordinates;
    } else {
      edits = instanceEditingCoordinates[instance.name];
    }

    // The CSV's values for this row, split by domain. /api/avar2/instances
    // exposes ``out`` (parametric coords), ``in`` (user-axis values, incl.
    // SPAC — csv_io classifies the SPAC column as an in-axis), and
    // ``glyphs_coordinates`` (source-derived). Dirtiness is PER KEY against
    // the union of everything persisted — the old whole-dict key-count
    // compare could never clear once a draft carried SPAC or an in: axis
    // (5 draft keys vs 3 persisted out: keys → permanently red).
    const mapping = avar2Instances.find(m => m.instance_name === instance.name);
    const csvOut = (mapping && mapping.avar2_mapping && mapping.avar2_mapping.out) || {};
    const csvIn = (mapping && mapping.avar2_mapping && mapping.avar2_mapping.in) || {};
    const sourceCoords = instance.coordinates || {};
    const persisted = { ...sourceCoords, ...csvIn, ...csvOut };
    const csvCoords = Object.keys(csvOut).length > 0 ? csvOut : null;

    if (edits && Object.keys(edits).length > 0) {
      const dirty = Object.keys(edits).some(k => {
        if (injectedAxisTags.has(k)) return false;   // preview-only axis (SPAC, secondary) — never persisted
        if (!(k in persisted)) return true;          // value with no persisted home
        const ve = Number(edits[k]);
        const vp = Number(persisted[k]);
        if (!Number.isFinite(ve) || !Number.isFinite(vp)) return edits[k] !== persisted[k];
        return Math.abs(ve - vp) > 0.01;
      });
      if (dirty) return 'red';   // local edits not persisted
    }

    if (instance.origin === 'studio') {
      // Studio-only rows live in the CSV alone. Promotion to source
      // turns them green.
      return 'orange';
    }

    if (csvCoords && !_sameCoords(csvCoords, sourceCoords)) {
      return 'orange';   // CSV diverges from source — needs a source push
    }

    return 'green';
  }, [selectedInstance, editingCoordinates, instanceEditingCoordinates, avar2Instances, injectedAxisTags]);

  // Advance-width measurement (per-row /api/text-width polling with a
  // cache + interpolation + live recalculation) was removed at the
  // designer's request — it dominated request traffic. The backend
  // endpoint survives for easy reinstatement.


  const handleSelectInstance = useCallback((instance) => {
    // If clicking the same instance, don't reset coordinates
    if (selectedInstance && selectedInstance.name === instance.name) {
      return; // Already selected, keep current editing coordinates
    }
    
    // Save current editing coordinates for the previously selected instance
    // Also save the originalCoordinates so getInstanceSyncStatus can compare correctly
    if (selectedInstance && Object.keys(editingCoordinates).length > 0) {
      setInstanceEditingCoordinates(prev => ({
        ...prev,
        [selectedInstance.name]: { ...editingCoordinates }
      }));
      // Also save originalCoordinates for this instance so sync status works correctly
      // We need to store this per-instance so getInstanceSyncStatus can compare
      setInstanceOriginalCoordinates(prev => ({
        ...prev,
        [selectedInstance.name]: { ...originalCoordinates }
      }));
    }
    
    setSelectedInstance(instance);
    
    // Restore editing coordinates for this instance if they exist, otherwise use instance coordinates.
    const savedCoordinates = instanceEditingCoordinates[instance.name];
    setEditingCoordinates(savedCoordinates ? { ...savedCoordinates } : { ...instance.coordinates });

    const savedOriginalCoords = instanceOriginalCoordinates[instance.name];
    setOriginalCoordinates(savedOriginalCoords || { ...instance.coordinates });
  }, [selectedInstance, editingCoordinates, instanceEditingCoordinates, instanceOriginalCoordinates, originalCoordinates]);

  const handleAxisChange = useCallback((tag, value) => {
    // Register instance as editing when first axis change happens
    if (selectedInstance && Object.keys(editingCoordinates).length === 0) {
      api.registerEditingInstance(selectedInstance.name).catch(() => {});
    }
    
    setEditingCoordinates(prev => {
      const updated = {
        ...prev,
        [tag]: value,
      };
      
      // Also update the stored coordinates for the current instance
      if (selectedInstance) {
        setInstanceEditingCoordinates(prevStored => ({
          ...prevStored,
          [selectedInstance.name]: updated
        }));
      }
      return updated;
    });
  }, [selectedInstance, editingCoordinates]);


  // AUTO-PERSIST studio-instance coordinate edits (debounced,
  // serialized). The old contract — slider edits live in page state
  // until "Update Instance" is clicked — reads as saved-but-isn't,
  // and a refresh has discarded real authoring twice. Studio rows
  // live in the studio's own CSV, so writing through is safe; SOURCE
  // rows keep the explicit Update flow (rewriting a designer's
  // .glyphs is never implicit). Calls the API directly and leaves
  // local editing state alone, so a drag that continues while a save
  // is in flight can't be snapped back by a refetch.
  const autoSaveTimer = useRef(null);
  const autoSaveChain = useRef(Promise.resolve());
  const autoSaveLastSaved = useRef({});
  useEffect(() => {
    if (!selectedInstance || selectedInstance.origin !== 'studio') return undefined;
    if (!editingCoordinates || Object.keys(editingCoordinates).length === 0) return undefined;
    const name = selectedInstance.name;
    const coords = { ...selectedInstance.coordinates, ...editingCoordinates };
    // Preview-only axes (SPAC, secondary parametric) never persist —
    // dragging them must not schedule CSV writes or destabilize the
    // saved-key guard.
    for (const t of injectedAxisTags) delete coords[t];
    const coordsKey = JSON.stringify(Object.keys(coords).sort().map(k => [k, coords[k]]));
    // Idempotence guard. WITHOUT it, a completed rebuild (font reload,
    // instances refetch) re-fires this effect with UNCHANGED coords,
    // which saves again, which rebuilds again — a self-sustaining
    // rebuild loop while the user sits idle (shipped once: 442 PUTs
    // at ~11s intervals). Only genuinely new coordinates get saved.
    if (autoSaveLastSaved.current[name] === coordsKey) {
      return undefined;
    }
    clearTimeout(autoSaveTimer.current);
    autoSaveTimer.current = setTimeout(() => {
      autoSaveChain.current = autoSaveChain.current.then(async () => {
        try {
          await api.updateInstance(name, coords, { csvOnly: true });
          // Mark saved only on success so a failure retries on the
          // next edit rather than silently never saving.
          autoSaveLastSaved.current[name] = coordsKey;
          // Refresh BOTH row caches WITHOUT touching editing state.
          // The sync dot judges "persisted" against avar2Instances —
          // refreshing only `instances` left it stale, so a saved
          // studio row stayed red forever.
          const d = await api.getInstances();
          setInstances(d.instances);
          await loadAvar2Data();
        } catch (err) {
          console.warn(`Auto-save of "${name}" failed:`, err.message || err);
        }
      });
    }, 800);
    return () => clearTimeout(autoSaveTimer.current);
  }, [editingCoordinates, selectedInstance, injectedAxisTags]);

  // Helper function to wait for font to be loaded and ready
  const waitForFontReady = async (maxAttempts = 50, delayMs = 100) => {
    if (!vfFamilyId) {
      // No family registered yet; the polling loop will populate it.
      return false;
    }
    for (let i = 0; i < maxAttempts; i++) {
      // Check if font is loaded by checking document.fonts
      if (document.fonts && document.fonts.check(`12px "${vfFamilyId}"`)) {
        // Also wait for fonts.ready to ensure font is fully ready
        await document.fonts.ready;
        return true;
      }
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }
    return false; // Timeout - font might not be ready, but proceed anyway
  };

  // Helper function to update a specific instance by name. ``csvOnly``
  // is threaded into the API call so the server skips the source-file
  // writeback (the "Update in avar2-studio" path).
  const updateInstanceByName = async (instanceName, coordinatesToUse, options = {}) => {
    const instance = instances.find(inst => inst.name === instanceName);
    if (!instance) {
      throw new Error(`Instance "${instanceName}" not found`);
    }

    // Strip transform-injected axes (SPAC) — live preview state, not
    // instance data; the server won't persist them and comparing them
    // makes saves look like no-ops.
    if (injectedAxisTags.size > 0) {
      coordinatesToUse = Object.fromEntries(
        Object.entries(coordinatesToUse).filter(([tag]) => !injectedAxisTags.has(tag))
      );
    }

    const originalCoords = { ...instance.coordinates };
    const parametricChanged = Object.keys(coordinatesToUse).some(
      key => Math.abs((coordinatesToUse[key] ?? 0) - (originalCoords[key] ?? 0)) > 0.01
    );

    await api.updateInstance(instanceName, coordinatesToUse, options);

    if (parametricChanged) {
      const instancesData = await api.getInstances();
      setInstances(instancesData.instances);

      if (selectedInstance && selectedInstance.name === instanceName) {
        const updated = instancesData.instances.find(inst => inst.name === instanceName);
        if (updated) {
          setSelectedInstance(updated);
          // For csv_only updates on a source instance the source file
          // is unchanged, so `updated.coordinates` still reflects the
          // OLD source values. Trusting it would snap the preview back
          // to the original state. Use the user's just-saved values
          // instead — the CSV row reflects them, the preview should
          // too. For source-file writes, `updated.coordinates` is
          // already the new state and is the canonical answer.
          const coordsAfterSave = options.csvOnly
            ? { ...coordinatesToUse }
            : { ...updated.coordinates };
          setEditingCoordinates(coordsAfterSave);
          setOriginalCoordinates(coordsAfterSave);
        }
      }
    } else {
      setInstanceEditingCoordinates(prev => ({
        ...prev,
        [instanceName]: { ...coordinatesToUse },
      }));
    }

    await api.unregisterEditingInstance(instanceName).catch(() => {});
  };

  // Resolve which coordinates to persist when the flyout fires.
  //
  // ``requireDirty=true`` (default) — skip when local state matches the
  // originals. The "Save to avar2-studio (CSV)" path uses this: no
  // edits → no save.
  //
  // ``requireDirty=false`` — return current state even when it matches
  // the originals. The "Save to source file" path uses this so an
  // orange-no-edits row (CSV saved, source not pushed) can still push
  // its CSV values up to the source. In that state editingCoordinates
  // already mirrors the CSV — writing them to source is the correct
  // orange→green transition.
  const _resolveEditedCoords = (instanceName, options = {}) => {
    const requireDirty = options.requireDirty !== false;
    const targetInstanceName = instanceName || selectedInstance?.name;
    if (!targetInstanceName) return null;
    let coordinatesToUse;
    if (instanceName && instanceName !== selectedInstance?.name) {
      coordinatesToUse = instanceEditingCoordinates[instanceName];
      if (!coordinatesToUse) {
        // No scratch state for this row. Fall back to the source
        // coords so the source path can still operate (the CSV-divergence
        // case shows the same data via avar2Instances; the caller can
        // override coords if it needs to).
        const fallback = instances.find(inst => inst.name === targetInstanceName);
        if (!fallback) return null;
        coordinatesToUse = { ...fallback.coordinates };
      }
    } else {
      if (!selectedInstance) return null;
      const hasChanges = JSON.stringify(editingCoordinates) !== JSON.stringify(originalCoordinates);
      if (requireDirty && !hasChanges) return null;
      coordinatesToUse = editingCoordinates;
    }
    return { name: targetInstanceName, coords: coordinatesToUse };
  };

  // CSV-only update — flyout's "Update in avar2-studio" button.
  const handleUpdateInstanceStudio = async (instanceName) => {
    const resolved = _resolveEditedCoords(instanceName);
    if (!resolved) return;
    try {
      setError(null);
      setBuilding(true);
      await updateInstanceByName(resolved.name, resolved.coords, { csvOnly: true });
    } catch (err) {
      setError(err.message || 'Failed to update in avar2-studio');
    } finally {
      setBuilding(false);
    }
  };

  // Source writeback — flyout's "Update source file" / "Add to source"
  // path for source-defined rows. Studio-only rows route through
  // handleAddInstanceToSource instead (the "Add to source" semantics).
  const handleUpdateInstanceSource = async (instanceObj) => {
    if (instanceObj && instanceObj.origin === 'studio') {
      return handleAddInstanceToSource(instanceObj.name);
    }
    return handleUpdateInstance(instanceObj?.name);
  };

  // Demote a source-defined row to studio-only: delete the source
  // declaration but KEEP the CSV row so the avar2 mapping is preserved.
  // The row stays in the UI; only its SRC badge goes away.
  const handleDemoteFromSource = useCallback(async (instanceObj) => {
    if (!instanceObj || instanceObj.origin !== 'source') return;
    const confirmed = window.confirm(
      `Remove "${instanceObj.name}" from the source file?\n\n` +
      `It stays in the studio — the instance becomes studio-only and the SRC badge goes away. ` +
      `This rewrites your .glyphs / .designspace file and can't be undone from here.`
    );
    if (!confirmed) return;
    try {
      setError(null);
      setBuilding(true);
      await api.deleteInstance(instanceObj.name, { sourceOnly: true });
      const instancesData = await api.getInstances();
      setInstances(instancesData.instances);
      // The demoted row still exists (now as studio-only); reselect it
      // so the user keeps their place. Re-bind selectedInstance to the
      // fresh studio-only entry so the SRC badge transition lands.
      if (selectedInstance && selectedInstance.name === instanceObj.name) {
        const next = instancesData.instances.find(i => i.name === instanceObj.name);
        if (next) setSelectedInstance(next);
      }
    } catch (err) {
      setError(err.message || 'Failed to demote instance');
    } finally {
      setBuilding(false);
    }
  }, [selectedInstance]);

  const handleUpdateInstance = async (instanceName) => {
    // Existing source-writeback path. Used by:
    //   - The flyout's source-update branch (for source-defined rows).
    //   - The Sidebar's per-axis Update Instance flow (when SPAC mode
    //     used to drive a row write; now just a singular update path).
    // requireDirty=false so an orange-no-edits row (CSV pushed past
    // source) can still trigger the source write — pushing the CSV
    // values up flips the row to green.
    const resolved = _resolveEditedCoords(instanceName, { requireDirty: false });
    if (!resolved) return;
    const { name: targetInstanceName, coords: coordinatesToUse } = resolved;

    const instance = instances.find(inst => inst.name === targetInstanceName);
    if (!instance) {
      setError(`Instance "${targetInstanceName}" not found`);
      return;
    }

    const originalCoords = { ...instance.coordinates };
    const parametricChanged = Object.keys(coordinatesToUse).some(
      key => Math.abs((coordinatesToUse[key] ?? 0) - (originalCoords[key] ?? 0)) > 0.01
    );

    // Show confirmation dialog only for selected instance updates
    // (skip in static mode — there is no source file to modify).
    if (!staticMode && (!instanceName || instanceName === selectedInstance?.name)) {
      const confirmed = window.confirm(
        `Update instance "${targetInstanceName}"?\n\n` +
        (parametricChanged ? `This will modify the source file.\n` : '')
      );

      if (!confirmed) return;
    }

    try {
      setError(null);
      setBuilding(true); // Set building state immediately to show UI feedback
      
      await updateInstanceByName(targetInstanceName, coordinatesToUse);
      
      // Store instance name before rebuild (in case it changes)
      const instanceNameToScroll = targetInstanceName;
      
      // Backend already rebuilds the font (regular or SPAC) after instance update
      // Just wait for it to complete - no need to trigger another rebuild
      // The backend will regenerate SPAC font if it exists, or rebuild regular font otherwise
      
      // Backend rebuilds automatically after instance update
      // Wait for build to complete by polling health endpoint
      let buildComplete = false;
      let attempts = 0;
      const maxAttempts = 60; // 60 seconds max wait
      
      while (!buildComplete && attempts < maxAttempts) {
        try {
          const health = await api.health();
          if (!health.building) {
            buildComplete = true;
            break;
          }
        } catch (err) {
          // Ignore polling errors, continue waiting
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
        attempts++;
      }
      
      // Reload font URL to get updated font (with timestamp to force reload)
      // This ensures we get the newly built font, not a cached/partial version
      // Set fontLoaded to false first to prevent duplicate loading
      setFontLoaded(false);
      // Use setTimeout to batch state updates and prevent duplicate font loads
      await new Promise(resolve => setTimeout(resolve, 100));
      const newFontUrl = api.getFontUrl();
      setFontUrl(newFontUrl);
      await new Promise(resolve => setTimeout(resolve, 100));
      setFontLoaded(true);
      
      // Wait for font to be loaded before scrolling
      await waitForFontReady();
      
      // Additional small delay to ensure DOM is updated after font load
      await new Promise(resolve => setTimeout(resolve, 200));
      
      // Reload instances to ensure we have the latest data (in case updateInstanceByName didn't reload)
      // This is important because the backend may have updated the Glyphs file
      const instancesData = await api.getInstances();
      setInstances(instancesData.instances);
      
      
      // Find the updated instance
      const updatedInstance = instancesData.instances.find(inst => inst.name === targetInstanceName);
      if (!updatedInstance) {
        setError(`Instance "${targetInstanceName}" not found after update`);
        setBuilding(false);
        return;
      }
      

      const updatedCoords = { ...updatedInstance.coordinates };
      
      // Update instanceOriginalCoordinates for this instance
      setInstanceOriginalCoordinates(prev => ({
        ...prev,
        [targetInstanceName]: updatedCoords
      }));
      
      // Update instanceEditingCoordinates to match saved value (so sync status turns green)
      setInstanceEditingCoordinates(prev => ({
        ...prev,
        [targetInstanceName]: updatedCoords
      }));
      
      // Update selected instance and editingCoordinates if this was the selected one
      if (selectedInstance && selectedInstance.name === targetInstanceName) {
        setSelectedInstance(updatedInstance);
        setOriginalCoordinates(updatedCoords);
        // Use the coordinates that were just saved (coordinatesToUse), not the instance coordinates
        // This ensures we show the exact values that were saved, including any rounding/clamping
        setEditingCoordinates(coordinatesToUse);
      }
      
      // Reset building state after font is fully loaded and ready
      setBuilding(false);
      
      // Scroll to updated instance after font is ready
      const element = document.querySelector(`[data-instance-name="${instanceNameToScroll}"]`);
      if (element) {
        // Use requestAnimationFrame to ensure DOM is ready
        requestAnimationFrame(() => {
          element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
      }
    } catch (err) {
      setError(err.message);
      console.error('Update failed:', err);
      setBuilding(false); // Reset building state on error
      throw err; // Re-throw for error handling in batch update
    }
  };

  // handleUpdateAllInstances removed — updates are singular now,
  // dispatched from each row's orange sync-dot flyout.

  const handleResetCoordinates = useCallback(() => {
    if (!selectedInstance) return;
    setEditingCoordinates({ ...originalCoordinates });
  }, [selectedInstance, originalCoordinates]);

  const handleCreateNewInstance = async (newInstanceName, requestedCoords) => {
    // Fresh studio-only instance. The modal collects per-axis values
    // (with axis defaults pre-filled) and passes them in. Fall back to
    // axis defaults if the caller skipped the modal somehow — the
    // server backfills missing fields the same way.
    try {
      setError(null);
      const coordinatesToUse = { ...(requestedCoords || {}) };
      (axes || []).forEach(axis => {
        if (coordinatesToUse[axis.tag] === undefined) {
          coordinatesToUse[axis.tag] = axis.default;
        }
      });
      // Preview-only axes (SPAC, secondary parametric) never persist —
      // on the static app they'd become instances-CSV columns.
      for (const t of injectedAxisTags) delete coordinatesToUse[t];

      await api.createInstance(newInstanceName, coordinatesToUse);

      const instancesData = await api.getInstances();
      setInstances(instancesData.instances);
      const newInstance = instancesData.instances.find(
        inst => inst.name === newInstanceName
      );
      if (newInstance) {
        setSelectedInstance(newInstance);
        setOriginalCoordinates(newInstance.coordinates);
        setEditingCoordinates(newInstance.coordinates);
      }
    } catch (err) {
      console.error('Failed to create instance:', err);
      setError(err.message || 'Failed to create instance');
    }
  };

  const handleDuplicateInstance = async (newInstanceName) => {
    if (!selectedInstance) return;

    try {
      setError(null);
      
      // Use current editing coordinates (if adjusted) or original instance coordinates
      const coordinatesToUse = Object.keys(editingCoordinates).length > 0 && 
        JSON.stringify(editingCoordinates) !== JSON.stringify(originalCoordinates)
        ? editingCoordinates
        : selectedInstance.coordinates;
      
      // Create new instance, inserting after the selected instance.
      // Preview-only axes (SPAC, secondary parametric) are not instance data.
      const persistedCoords = Object.fromEntries(
        Object.entries(coordinatesToUse).filter(([tag]) => !injectedAxisTags.has(tag))
      );
      await api.createInstance(newInstanceName, persistedCoords, selectedInstance.name);
      
      // Reload instances to get the new one
      const instancesData = await api.getInstances();
      setInstances(instancesData.instances);
      
      // Find and select the new instance
      const newInstance = instancesData.instances.find(
        inst => inst.name === newInstanceName
      );
      
      if (!newInstance) {
        throw new Error(`Failed to find newly created instance "${newInstanceName}"`);
      }
      
      // Store instance name before rebuild (in case it changes)
      const instanceNameToScroll = newInstanceName;
      
      // Set up coordinates for the new instance (including SPAC if spacMode is enabled)
      const newCoords = { ...newInstance.coordinates };
      
      setSelectedInstance(newInstance);
      setEditingCoordinates(newCoords);
      setOriginalCoordinates(newCoords);
      
      // Backend rebuilds the font after instance creation
      // Wait for build to complete by polling health endpoint (same as update instance)
      setBuilding(true); // Set building state immediately to show UI feedback
      
      let buildComplete = false;
      let attempts = 0;
      const maxAttempts = 60; // 60 seconds max wait
      
      while (!buildComplete && attempts < maxAttempts) {
        try {
          const health = await api.health();
          if (!health.building) {
            buildComplete = true;
            break;
          }
        } catch (err) {
          // Ignore polling errors, continue waiting
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
        attempts++;
      }
      
      // Reload font URL to get updated font (with timestamp to force reload)
      setFontUrl(api.getFontUrl());
      setFontLoaded(false);
      await new Promise(resolve => setTimeout(resolve, 500));
      setFontLoaded(true);
      
      // Wait for font to be loaded before scrolling
      await waitForFontReady();
      
      // Additional small delay to ensure DOM is updated after font load
      await new Promise(resolve => setTimeout(resolve, 200));
      
      // Reload instances to get updated data (with new instance)
      const instancesDataAfterDup = await api.getInstances();
      setInstances(instancesDataAfterDup.instances);
      
      
      // Reload SPAC values if spacMode is enabled
      
      // Reset building state after font is fully loaded and ready
      setBuilding(false);
      
      // Scroll to new instance after font is ready
      const element = document.querySelector(`[data-instance-name="${instanceNameToScroll}"]`);
      if (element) {
        // Use requestAnimationFrame to ensure DOM is ready
        requestAnimationFrame(() => {
          element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
      }
    } catch (err) {
      setError(err.message);
      console.error('Duplicate failed:', err);
      setBuilding(false); // Reset building state on error
    }
  };

  const handleRenameInstance = async (oldName, newName) => {
    if (!oldName || !newName || oldName === newName) {
      return;
    }

    try {
      setError(null);
      setBuilding(true); // Set building state immediately to show UI feedback
      
      await api.renameInstance(oldName, newName);
      
      // Store new name for scrolling after rebuild
      const instanceNameToScroll = newName;
      
      // Backend already rebuilds the font (regular or SPAC) after rename
      // Just wait for it to complete - no need to trigger another rebuild
      // The backend will regenerate SPAC font if it exists, or rebuild regular font otherwise
      
      // Backend rebuilds automatically after rename
      // Wait for build to complete by polling health endpoint
      let buildComplete = false;
      let attempts = 0;
      const maxAttempts = 60; // 60 seconds max wait
      
      while (!buildComplete && attempts < maxAttempts) {
        try {
          const health = await api.health();
          if (!health.building) {
            buildComplete = true;
            break;
          }
        } catch (err) {
          // Ignore polling errors, continue waiting
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
        attempts++;
      }
      
      // Reload instances to get updated data (with new name)
      const instancesData = await api.getInstances();
      setInstances(instancesData.instances);
      
      // Reload font URL to get updated font (with timestamp to force reload)
      setFontUrl(api.getFontUrl());
      setFontLoaded(false);
      await new Promise(resolve => setTimeout(resolve, 500));
      setFontLoaded(true);
      
      // Wait for font to be loaded before updating state
      await waitForFontReady();
      
      // Additional small delay to ensure DOM is updated after font load
      await new Promise(resolve => setTimeout(resolve, 200));
      
      // Update selected instance if it was renamed
      if (selectedInstance && selectedInstance.name === oldName) {
        const renamed = instancesData.instances.find(
          inst => inst.name === newName
        );
        if (renamed) {
          setSelectedInstance(renamed);
          
          // Preserve an in-flight draft across the rename. Reseeding
          // both coordinate states from the backend here used to
          // silently discard unsaved slider edits — the rename
          // "overwrote" them. The rename changes the name only, so the
          // draft and its baseline stay valid as-is.
          const hasDraft = JSON.stringify(editingCoordinates) !== JSON.stringify(originalCoordinates);
          if (!hasDraft) {
            const updatedCoords = { ...renamed.coordinates };
            setEditingCoordinates(updatedCoords);
            setOriginalCoordinates(updatedCoords);
          }
          
          // Update instanceEditingCoordinates and instanceOriginalCoordinates
          // Remove old name, add new name
          setInstanceEditingCoordinates(prev => {
            const newState = { ...prev };
            if (prev[oldName]) {
              newState[newName] = prev[oldName];
              delete newState[oldName];
            }
            return newState;
          });
          setInstanceOriginalCoordinates(prev => {
            const newState = { ...prev };
            if (prev[oldName]) {
              newState[newName] = prev[oldName];
              delete newState[oldName];
            }
            return newState;
          });
          
          // Reload SPAC values if spacMode is enabled
        }
      }
      
      // Reset building state after font is fully loaded and ready
      setBuilding(false);
      
      // Scroll to renamed instance after font is ready
      const element = document.querySelector(`[data-instance-name="${instanceNameToScroll}"]`);
      if (element) {
        // Use requestAnimationFrame to ensure DOM is ready
        requestAnimationFrame(() => {
          element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
      }
    } catch (err) {
      setError(err.message);
      console.error('Rename failed:', err);
      setBuilding(false); // Reset building state on error
      throw err; // Re-throw to let component handle error display
    }
  };

  const handleDeleteInstance = useCallback(async (instance) => {
    // Studio-only rows don't exist in the source file — there's nothing
    // to confirm, so we skip the modal and just remove the CSV row.
    // Source-defined rows still get the modal because the source-side
    // delete is destructive.
    if (instance && instance.origin === 'studio') {
      try {
        setError(null);
        await api.deleteInstance(instance.name, { csvOnly: true });
        const instancesData = await api.getInstances();
        setInstances(instancesData.instances);
        if (selectedInstance && selectedInstance.name === instance.name) {
          setSelectedInstance(null);
          setEditingCoordinates({});
          setOriginalCoordinates({});
        }
        setInstanceEditingCoordinates(prev => {
          const next = { ...prev };
          delete next[instance.name];
          return next;
        });
        setInstanceOriginalCoordinates(prev => {
          const next = { ...prev };
          delete next[instance.name];
          return next;
        });
      } catch (err) {
        setError(err.message || 'Failed to delete studio-only instance');
      }
      return;
    }
    setInstanceToDelete(instance);
    setShowDeleteModal(true);
  }, [selectedInstance]);

  const handleAddInstanceToSource = useCallback(async (instanceName) => {
    try {
      setError(null);
      await api.addInstanceToSource(instanceName);
      // Refresh the instance list so the badge disappears and the
      // origin flips from "studio" to "source".
      const refreshed = await api.getInstances();
      setInstances(refreshed.instances || []);
    } catch (err) {
      console.error('Failed to add instance to source:', err);
      setError(err.message || 'Failed to add instance to source');
    }
  }, []);

  const handleConfirmDelete = useCallback(async (deleteFromGlyphs) => {
    if (!instanceToDelete) return;

    const instanceName = instanceToDelete.name;

    try {
      setError(null);

      if (deleteFromGlyphs) {
        // Full deletion: delete from source file and CSV, then rebuild
        setBuilding(true); // Set building state immediately to show UI feedback

        await api.deleteInstance(instanceName);

        // Backend already rebuilds the font after deletion
        // Wait for build to complete by polling health endpoint
        let buildComplete = false;
        let attempts = 0;
        const maxAttempts = 60; // 60 seconds max wait

        while (!buildComplete && attempts < maxAttempts) {
          try {
            const health = await api.health();
            if (!health.building) {
              buildComplete = true;
              break;
            }
          } catch (err) {
            // Ignore polling errors, continue waiting
          }
          await new Promise(resolve => setTimeout(resolve, 1000));
          attempts++;
        }

        // Reload instances to get updated list (without deleted instance)
        const instancesData = await api.getInstances();
        setInstances(instancesData.instances);

        // Reload font URL to get updated font (with timestamp to force reload)
        setFontUrl(api.getFontUrl());
        setFontLoaded(false);
        await new Promise(resolve => setTimeout(resolve, 500));
        setFontLoaded(true);

        // Wait for font to be loaded
        await waitForFontReady();

        // Additional small delay to ensure DOM is updated after font load
        await new Promise(resolve => setTimeout(resolve, 200));



        // Reset building state after font is fully loaded and ready
        setBuilding(false);
      } else {
        // CSV-only deletion ("unmap" — keep the source instance but
        // remove its avar2 mapping row). Hits the backend with
        // csv_only=true so this is a real persistent delete, not just
        // a local-state filter that the next polling tick undoes.
        await api.deleteInstance(instanceName, { csvOnly: true });
        const instancesData = await api.getInstances();
        setInstances(instancesData.instances);
      }

      // Clear selection if the deleted instance was selected
      if (selectedInstance && selectedInstance.name === instanceName) {
        setSelectedInstance(null);
        setEditingCoordinates({});
        setOriginalCoordinates({});
      }

      // Clean up instance-specific state
      setInstanceEditingCoordinates(prev => {
        const newState = { ...prev };
        delete newState[instanceName];
        return newState;
      });
      setInstanceOriginalCoordinates(prev => {
        const newState = { ...prev };
        delete newState[instanceName];
        return newState;
      });

      // Close modal
      setShowDeleteModal(false);
      setInstanceToDelete(null);
    } catch (err) {
      setError(err.message);
      console.error('Delete failed:', err);
      setBuilding(false); // Reset building state on error
      // Keep modal open so user can try again or cancel
    }
  }, [instanceToDelete, selectedInstance, waitForFontReady]);

  const handleMoveInstance = useCallback((instanceToMove, targetInstance, position) => {
    if (!instanceToMove || !targetInstance) return;
    
    // Find current indices
    const currentIndex = instances.findIndex(inst => inst.name === instanceToMove.name);
    const targetIndex = instances.findIndex(inst => inst.name === targetInstance.name);
    
    // If already in the correct position, silently ignore
    if (currentIndex === targetIndex || 
        (position === 'before' && currentIndex === targetIndex - 1) ||
        (position === 'after' && currentIndex === targetIndex + 1)) {
      return;
    }
    
    // Calculate new index
    let newIndex;
    if (position === 'before') {
      newIndex = targetIndex;
    } else {
      newIndex = targetIndex + 1;
    }
    
    // Adjust if moving from before the target position
    if (currentIndex < newIndex) {
      newIndex--;
    }
    
    // Perform move
    const newInstances = [...instances];
    const [movedItem] = newInstances.splice(currentIndex, 1);
    newInstances.splice(newIndex, 0, movedItem);
    
    setInstances(newInstances);
  }, [instances]);

  if (loading) {
    return (
      <div className="App">
        <div className="loading">Loading...</div>
      </div>
    );
  }

  return (
    <div className="App">
      {showLanding && (
        <DemoLanding
          familyId={vfFamilyId}
          fontLoaded={fontLoaded}
          onEnter={() => setShowLanding(false)}
        />
      )}
      <FontraEditorModal
        editor={fontraEditor}
        onClose={handleCloseFontraEditor}
      />
      <Header
        onBuildFont={handleBuildFont}
        building={building}
        fontLoaded={fontLoaded}
        familyName={familyName}
        onSourceLoaded={loadData}
        busy={building || loading}
        transforms={transforms}
        onToggleTransform={handleToggleTransform}
        onTransformParam={handleTransformParam}
        grade={grade}
        onToggleGrade={handleToggleGrade}
        onGradeDefault={handleGradeDefault}
        staticMode={staticMode}
        isUploadDataset={isUploadDataset()}
        hideRebuild={staticMode && !isUploadDataset()}
        allowImportInStatic={isUploadDataset()}
        coverageFindings={coverage}
        onShowCoverage={() => setMainTab('space')}
      />

      <DeleteInstanceModal
        isOpen={showDeleteModal}
        onClose={() => {
          setShowDeleteModal(false);
          setInstanceToDelete(null);
        }}
        instanceName={instanceToDelete?.name || ''}
        onConfirm={handleConfirmDelete}
        glyphsFileHasUnsavedChanges={glyphsFileHasUnsavedChanges}
      />
      
      {error && (
        <div className="error-banner">
          Error: {error}
        </div>
      )}

      {lastBuildStatus === 'failed' && (
        <div className="error-banner" style={{ background: '#fff4e0', color: '#8a4b00', borderColor: '#f1c277' }}>
          Build failed — preview is stale. {lastBuildError}
        </div>
      )}

      <div className="main-content">
        {!familyName ? (
          // Blind launch — nothing loaded server-side. Steer the user
          // to the Header dropdown. Header keeps rendering, so the
          // dropdown is reachable from this state without extra wiring.
          <div className="content-empty-state">
            <h2>No font loaded</h2>
            <p>
              Use <strong>Load Font</strong> in the top bar to pick a
              built-in example or upload your own <code>.glyphs</code> file.
            </p>
          </div>
        ) : (
        <>
        <div className="main-tabs">
          <button
            type="button"
            className={`main-tab${mainTab === 'instances' ? ' active' : ''}`}
            onClick={() => setMainTab('instances')}
          >
            Instances
          </button>
          <button
            type="button"
            className={`main-tab${mainTab === 'preview' ? ' active' : ''}`}
            onClick={() => setMainTab('preview')}
          >
            Preview
          </button>
          <button
            type="button"
            className={`main-tab${mainTab === 'space' ? ' active' : ''}`}
            onClick={() => setMainTab('space')}
          >
            Space
          </button>
        </div>
        <div className={`build-scope${building ? ' is-building' : ''}`}>
        {mainTab === 'space' ? (
          <SpaceTab
            axes={axes}
            coverageFindings={coverage}
            coveragePins={coveragePins}
            fontUrl={fontUrl}
            vfFamilyId={vfFamilyId}
            onPinCorner={async (loc) => {
              // Errors (e.g. untrendable-corner refusals) surface inline
              // in the Space tab's pinAt — don't catch here.
              const res = await api.pinCorner(loc);
              await loadData();
              return res; // pinAt reads .synthesized for the notice
            }}
            onClampOutOfRange={api.clampOutOfRange ? async () => {
              await api.clampOutOfRange();
              await loadData();
            } : undefined}
            onAddMappingRow={api.addMappingRow ? async (loc) => {
              await api.addMappingRow(loc);
              await loadData();
              await loadAvar2Data();
            } : undefined}
            onJumpToLocation={(loc) => { setJumpLocation(loc); setMainTab('preview'); }}
          />
        ) : mainTab === 'instances' ? (
        <div className="content-area">
          <Sidebar
            axes={axes}
            coordinates={editingCoordinates}
            onAxisChange={handleAxisChange}
            disabled={(staticMode && !isUploadDataset()) || !selectedInstance}
            sampleText={sampleText}
            onSampleTextChange={setSampleText}
            selectedInstance={selectedInstance}
            familyName={familyName}
            onUpdateInstance={handleUpdateInstance}
            onResetCoordinates={handleResetCoordinates}
            originalCoordinates={originalCoordinates}
            fontSize={fontSize}
            onFontSizeChange={setFontSize}
            onDuplicateInstance={handleDuplicateInstance}
            onCreateNewInstance={handleCreateNewInstance}
            avar2Mode={avar2Mode}
            avar2Instances={avar2Instances}
            avar2Axes={avar2Axes}
            glyphCoverageAxes={glyphCoverageAxes}
            glyphChars={glyphChars}
            disabledControlAxes={disabledControlAxes}
            onToggleDisableControlAxis={handleToggleDisableControlAxis}
            onCreateControlAxis={handleCreateControlAxis}
            onUpdateControlAxis={handleUpdateControlAxis}
            onDeleteControlAxis={handleDeleteControlAxis}
            controlAxisAuthoringDisabledReason={null}
            onSetControlAxisLayers={handleSetControlAxisLayers}
            onControlAxisLayerDelta={handleControlAxisLayerDelta}
            onOpenControlAxisInEditor={handleOpenControlAxisInEditor}
            onAddAvar2Axis={handleAddAvar2Axis}
            onUpdateAvar2Axis={handleUpdateAvar2Axis}
            onDeleteAvar2Axis={api.deleteAvar2Axis ? handleDeleteAvar2Axis : undefined}
            onUpdateAvar2Mapping={handleUpdateAvar2Mapping}
            onReloadAvar2Data={loadAvar2Data}
            glyphsFileHasUnsavedChanges={glyphsFileHasUnsavedChanges}
            getInstanceSyncStatus={getInstanceSyncStatus}
            instances={instances}
            masters={masters}
            vfFamilyId={vfFamilyId}
            fontLoaded={fontLoaded}
            building={building}
          />
          <InstanceRows
            instances={instances}
            selectedInstance={selectedInstance}
            onSelectInstance={handleSelectInstance}
            editingCoordinates={editingCoordinates}
            instanceEditingCoordinates={instanceEditingCoordinates}
            sampleText={sampleText}
            fontUrl={fontUrl}
            vfFamilyId={vfFamilyId}
            fontLoaded={fontLoaded}
            onReorderInstances={setInstances}
            fontSize={fontSize}
            onDeleteInstance={handleDeleteInstance}
            getInstanceSyncStatus={getInstanceSyncStatus}
            onMoveInstance={handleMoveInstance}
            onRenameInstance={handleRenameInstance}
            onUpdateInstanceStudio={handleUpdateInstanceStudio}
            onUpdateInstanceSource={handleUpdateInstanceSource}
            onDemoteFromSource={handleDemoteFromSource}
            disabledControlAxes={disabledControlAxes}
            axisDefaults={axisDefaults}
            grade={grade}
            onSaveInstanceGrade={handleSaveInstanceGrade}
            onRemoveInstanceGrade={handleRemoveInstanceGrade}
            axes={axes}
          />
        </div>
        ) : (
          <PreviewTab
            axes={axes}
            avar2Error={avar2Error}
            familyName={familyName}
            vfFamilyId={vfFamilyId}
            fontLoaded={fontLoaded}
            fontUrl={fontUrl}
            builtFontFilename={builtFontFilename}
            sampleText={sampleText}
            onSampleTextChange={setSampleText}
            fontSize={fontSize}
            onFontSizeChange={setFontSize}
            jumpLocation={jumpLocation}
          />
        )}
        {building && (
          <div className="build-veil" aria-live="polite">
            <img className="build-veil-logo" src={logoGif} alt="" />
            <span className="build-veil-text">Rebuilding — hold your edits…</span>
          </div>
        )}
        </div>
        </>
        )}
      </div>
    </div>
  );
}

export default App;
