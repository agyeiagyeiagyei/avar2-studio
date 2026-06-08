import React, { useState, useEffect, useCallback } from 'react';
import './App.css';
import { api } from './api';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import InstanceRows from './components/InstanceRows';
import DeleteInstanceModal from './components/DeleteInstanceModal';

// Default sample text uses only ASCII letters, digits, and space so it
// renders cleanly against minimal fixtures (e.g. examples/crispy-mini,
// examples/roboto-delta-mini, which subset to A-Z + a-z + 0-9 + space).
// "&" and "!" were dropped from the historical Crispy default for the
// same reason.
const DEFAULT_SAMPLE_TEXT = "The Quick Brown Fox Jumps Over The Lazy Dog 0123456789";

function App() {
  const [instances, setInstances] = useState([]);
  const [axes, setAxes] = useState([]);
  const [selectedInstance, setSelectedInstance] = useState(null);
  const [editingCoordinates, setEditingCoordinates] = useState({});
  // Store editing coordinates per instance to persist when deselected
  const [instanceEditingCoordinates, setInstanceEditingCoordinates] = useState({});
  // Store original coordinates per instance for sync status comparison
  const [instanceOriginalCoordinates, setInstanceOriginalCoordinates] = useState({});
  const [fontLoaded, setFontLoaded] = useState(false);
  const [fontUrl, setFontUrl] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [building, setBuilding] = useState(false);
  const [sampleText, setSampleText] = useState(DEFAULT_SAMPLE_TEXT);
  const [fontSize, setFontSize] = useState(2); // Default 2rem
  const [familyName, setFamilyName] = useState(null);
  // Font family used for FontFace registration; comes from /api/health so the
  // tool works on any .glyphs file, not just Crispy.
  const [vfFamilyId, setVfFamilyId] = useState(null);
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
  // Advance width cache: { coordinatesKey: width_pixels }
  // coordinatesKey is a normalized string representation of coordinates
  const [advanceWidthCache, setAdvanceWidthCache] = useState({});
  // Current advance width for display (calculated from current coordinates)
  const [currentAdvanceWidth, setCurrentAdvanceWidth] = useState(null); // in font units
  const [currentAdvanceWidthPixels, setCurrentAdvanceWidthPixels] = useState(null); // in pixels (for reference)
  // Loading state for advance width recalculation
  const [advanceWidthLoading, setAdvanceWidthLoading] = useState(false);

  // Load initial data
  useEffect(() => {
    loadData();
    // Preload avar2 data so it's ready when toggled
    loadAvar2Data().catch(() => {
      // Silently fail - avar2 is optional
    });
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
  useEffect(() => {
    if (selectedInstance && avar2Instances.length > 0) {
      const mapping = avar2Instances.find(
        inst => inst.instance_name === selectedInstance.name
      );
      // If instance not in CSV, it will be added automatically by backend
      // when we fetch avar2 instances (backend handles missing instances)
      if (!mapping || mapping.match_status === 'missing_in_csv') {
        // Reload avar2 data to get updated CSV
        loadAvar2Data();
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
        
        setBuilding(health.building || false);
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
      
      const [instancesData, axesData] = await Promise.all([
        api.getInstances(),
        api.getAxes(),
      ]);

      setInstances(instancesData.instances);
      setAxes(axesData.axes || []);
      setFontLoaded(health.font_built);
      setFamilyName(health.family_name || null);
      setVfFamilyId(health.vf_family_id || (health.family_name ? `${health.family_name}-VF` : null));
      setBuiltFontFilename(health.built_font_filename || null);
      setLastBuildTime(health.last_build_time || null);
      setBuilding(health.building || false);

      if (health.font_built) {
        // Wait a bit for font to be ready, then cache widths.
        setTimeout(() => {
          cacheInstanceAdvanceWidths(instancesData.instances, {});
        }, 1000);
      }

      // If font was rebuilt (new build time), reload the font
      if (health.font_built && health.last_build_time && health.last_build_time !== lastBuildTime) {
        setFontUrl(api.getFontUrl()); // This is synchronous, returns string
        // Force font reload by updating fontLoaded state
        setFontLoaded(true);
      } else if (health.font_built && !fontUrl) {
        setFontUrl(api.getFontUrl());
      }
    } catch (err) {
      setError(err.message);
      console.error('Failed to load data:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadAvar2Data = async () => {
    try {
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

  const handleBuildAvar2Font = async ({ traditionalAxes, avar2Axes, includeSpac }) => {
    try {
      setBuilding(true);
      setError(null);
      
      const result = await api.buildAvar2Font(traditionalAxes, avar2Axes, includeSpac);
      
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
      // Switch back to Default mode
      // Use main font URL (serves designspace font from preview-fonts/spac)
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

  const [originalCoordinates, setOriginalCoordinates] = useState({});

  // Sync status for the orange-dot indicator. Compares the editing
  // coordinates against the originals — a divergence means the user has
  // unsaved edits.
  const getInstanceSyncStatus = useCallback((instance) => {
    if (selectedInstance && selectedInstance.name === instance.name) {
      if (!originalCoordinates || Object.keys(originalCoordinates).length === 0) {
        return 'green';
      }
      const isSynced = JSON.stringify(editingCoordinates) === JSON.stringify(originalCoordinates);
      return isSynced ? 'green' : 'orange';
    }

    const savedCoordinates = instanceEditingCoordinates[instance.name];
    const savedOriginalCoords = instanceOriginalCoordinates[instance.name];

    if (savedCoordinates && Object.keys(savedCoordinates).length > 0) {
      const comparisonCoords =
        savedOriginalCoords && Object.keys(savedOriginalCoords).length > 0
          ? savedOriginalCoords
          : instance.coordinates;
      const isSynced = JSON.stringify(savedCoordinates) === JSON.stringify(comparisonCoords);
      if (!isSynced) {
        return 'orange';
      }
    }
    return 'green';
  }, [selectedInstance, editingCoordinates, originalCoordinates, instanceEditingCoordinates, instanceOriginalCoordinates]);

  // Helper: Create normalized key from coordinates and text for caching
  const getCoordinatesKey = useCallback((coordinates, text = sampleText) => {
    // Sort keys and round values for consistent keys
    const coordsKey = Object.keys(coordinates)
      .sort()
      .map(key => `${key}:${coordinates[key].toFixed(2)}`)
      .join('|');
    // Include text in key so cache updates when text changes
    return `${text}|${coordsKey}`;
  }, [sampleText]);

  // Helper: Interpolate advance width between cached points
  const interpolateAdvanceWidth = useCallback((targetCoords, cache) => {
    // Find nearest cached points
    const cachedPoints = Object.entries(cache).map(([key, width]) => {
      // Parse coordinates from key (key format: "TAG1:value1|TAG2:value2" - text already filtered out)
      const coords = {};
      const parts = key.split('|');
      parts.forEach(part => {
        if (part.includes(':')) {
          const [tag, value] = part.split(':');
          coords[tag] = parseFloat(value);
        }
      });
      return { coords, width };
    });

    if (cachedPoints.length === 0) return null;

    // Find nearest point(s) for interpolation
    // For simplicity, use weighted average of all cached points based on distance
    // More sophisticated: find points that differ in only one axis and interpolate along that axis
    
    // Calculate distance to each cached point
    const distances = cachedPoints.map(point => {
      let distance = 0;
      let axisCount = 0;
      Object.keys(targetCoords).forEach(tag => {
        const targetVal = targetCoords[tag] || 0;
        const pointVal = point.coords[tag] || 0;
        const diff = Math.abs(targetVal - pointVal);
        distance += diff * diff; // Squared distance
        axisCount++;
      });
      return { point, distance: Math.sqrt(distance), axisCount };
    });

    // Sort by distance
    distances.sort((a, b) => a.distance - b.distance);

    // If we have a very close point (distance < 1), use it directly
    if (distances[0].distance < 1) {
      return distances[0].point.width;
    }

    // Use weighted average of nearest 3 points (inverse distance weighting)
    const nearest = distances.slice(0, Math.min(3, distances.length));
    let totalWeight = 0;
    let weightedSum = 0;

    nearest.forEach(({ point, distance }) => {
      // Avoid division by zero
      const weight = distance > 0 ? 1 / (distance + 0.1) : 1000;
      totalWeight += weight;
      weightedSum += weight * point.width;
    });

    return totalWeight > 0 ? weightedSum / totalWeight : null;
  }, [getCoordinatesKey]);

  // Cache advance widths for all instances. The optional second
  // arg (formerly spacValuesMap) is kept for call-site compatibility
  // but no longer used.
  const cacheInstanceAdvanceWidths = useCallback(async (instancesList, _unused = {}, showLoading = false) => {
    if (instancesList.length === 0) {
      return;
    }

    if (showLoading) {
      setAdvanceWidthLoading(true);
    }

    try {
      const cachePromises = instancesList.map(async (instance) => {
        const coords = { ...instance.coordinates };
        const key = getCoordinatesKey(coords, sampleText);
        
        try {
          const result = await api.getTextWidth(sampleText, coords, fontSize);
          // Store font units instead of pixels
          return { key, width: result.width_font_units };
        } catch (err) {
          console.error(`Failed to cache advance width for ${instance.name}:`, err);
          return null;
        }
      });

      const results = await Promise.all(cachePromises);
      let cachedCount = 0;
      
      // Use functional update to ensure we're updating the latest cache state
      setAdvanceWidthCache(currentCache => {
        const newCache = { ...currentCache };
        results.forEach(result => {
          if (result) {
            newCache[result.key] = result.width;
            cachedCount++;
          }
        });
        return newCache;
      });
    } catch (err) {
      console.error('Failed to cache advance widths:', err);
    } finally {
      if (showLoading) {
        setAdvanceWidthLoading(false);
      }
    }
  }, [sampleText, fontSize, getCoordinatesKey]);

  // Calculate advance width for current coordinates (with interpolation)
  // Returns width in font units
  const calculateAdvanceWidth = useCallback((coordinates, text = sampleText) => {
    if (!coordinates || Object.keys(coordinates).length === 0) {
      return null;
    }
    
    const key = getCoordinatesKey(coordinates, text);
    
    // Check cache first (cache now stores font units)
    if (advanceWidthCache[key] !== undefined) {
      return advanceWidthCache[key];
    }

    // Try interpolation (need to filter cache by same text)
    const textFilteredCache = {};
    Object.keys(advanceWidthCache).forEach(cacheKey => {
      if (cacheKey.startsWith(text + '|')) {
        // Extract coordinates part (everything after text|)
        const coordsPart = cacheKey.substring(text.length + 1);
        textFilteredCache[coordsPart] = advanceWidthCache[cacheKey];
      }
    });
    
    const interpolated = interpolateAdvanceWidth(coordinates, textFilteredCache);
    if (interpolated !== null && interpolated !== undefined) {
      return interpolated;
    }

    return null;
  }, [advanceWidthCache, getCoordinatesKey, interpolateAdvanceWidth, sampleText]);


  // Recache advance widths when sample text changes
  useEffect(() => {
    if (instances.length > 0 && fontLoaded) {
      setAdvanceWidthCache({});
      setTimeout(() => {
        cacheInstanceAdvanceWidths(instances, {}, true);
      }, 500);
    }
  }, [sampleText]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Calculate advance width in real-time when coordinates change
  useEffect(() => {
    if (!fontLoaded || !selectedInstance) {
      setCurrentAdvanceWidth(null);
      setCurrentAdvanceWidthPixels(null);
      return;
    }

    // Build current coordinates — instance base overlaid with edits.
    const currentCoords = { ...selectedInstance.coordinates, ...editingCoordinates };

    // For selected instance, always use API call for accuracy (skip cache to avoid interpolation errors)
    // Debounce to avoid too many API calls while dragging slider
    const timeoutId = setTimeout(async () => {
      try {
        const widthResult = await api.getTextWidth(sampleText, currentCoords, fontSize);
        
        if (widthResult && widthResult.width_font_units !== undefined) {
          const widthFontUnits = widthResult.width_font_units;
          const widthPixels = widthResult.width_pixels;
          
          // Update cache with font units (for consistency)
          const cacheKey = getCoordinatesKey(currentCoords, sampleText);
          setAdvanceWidthCache(currentCache => ({
            ...currentCache,
            [cacheKey]: widthFontUnits // Store in font units
          }));
          
          setCurrentAdvanceWidth(widthFontUnits);
          setCurrentAdvanceWidthPixels(widthPixels);
        } else {
          setCurrentAdvanceWidth(null);
          setCurrentAdvanceWidthPixels(null);
        }
      } catch (err) {
        // Don't fallback to interpolation - it's inaccurate
        setCurrentAdvanceWidth(null);
        setCurrentAdvanceWidthPixels(null);
      }
    }, 200); // 200ms debounce

    return () => clearTimeout(timeoutId);
  }, [editingCoordinates, selectedInstance, fontLoaded, calculateAdvanceWidth, sampleText, fontSize, advanceWidthCache, getCoordinatesKey]);

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

  // Resolve which coordinates to persist when the flyout fires. For
  // the selected row that's ``editingCoordinates``; for any other row
  // it's whatever the user had been editing there (stored per-instance
  // when they navigated away).
  const _resolveEditedCoords = (instanceName) => {
    const targetInstanceName = instanceName || selectedInstance?.name;
    if (!targetInstanceName) return null;
    let coordinatesToUse;
    if (instanceName && instanceName !== selectedInstance?.name) {
      coordinatesToUse = instanceEditingCoordinates[instanceName];
      if (!coordinatesToUse) return null;
    } else {
      if (!selectedInstance) return null;
      const hasChanges = JSON.stringify(editingCoordinates) !== JSON.stringify(originalCoordinates);
      if (!hasChanges) return null;
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

  const handleUpdateInstance = async (instanceName) => {
    // Existing source-writeback path. Used by:
    //   - The flyout's source-update branch (for source-defined rows).
    //   - The Sidebar's per-axis Update Instance flow (when SPAC mode
    //     used to drive a row write; now just a singular update path).
    const resolved = _resolveEditedCoords(instanceName);
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
    if (!instanceName || instanceName === selectedInstance?.name) {
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
      
      // Clear and recache advance widths since font metrics may have changed after rebuild
      setAdvanceWidthCache({});
      setCurrentAdvanceWidth(null);
      setCurrentAdvanceWidthPixels(null);
      
      // Find the updated instance
      const updatedInstance = instancesData.instances.find(inst => inst.name === targetInstanceName);
      if (!updatedInstance) {
        setError(`Instance "${targetInstanceName}" not found after update`);
        setBuilding(false);
        return;
      }
      
      // Recache advance widths after the font rebuild.
      setTimeout(() => {
        cacheInstanceAdvanceWidths(instancesData.instances, {}, true);
      }, 500);

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
      
      // Create new instance, inserting after the selected instance
      await api.createInstance(newInstanceName, coordinatesToUse, selectedInstance.name);
      
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
      
      // Clear and recache advance widths since font metrics may have changed after rebuild
      setAdvanceWidthCache({});
      setCurrentAdvanceWidth(null);
      setCurrentAdvanceWidthPixels(null);
      
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
          
          // Update coordinates with SPAC if spacMode is enabled
          const updatedCoords = { ...renamed.coordinates };
          
          setEditingCoordinates(updatedCoords);
          setOriginalCoordinates(updatedCoords);
          
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

        // Clear and recache advance widths since font metrics may have changed after rebuild
        setAdvanceWidthCache({});
        setCurrentAdvanceWidth(null);
        setCurrentAdvanceWidthPixels(null);

        // Recache advance widths after the font rebuild.
        setTimeout(() => {
          cacheInstanceAdvanceWidths(instancesData.instances, {}, true);
        }, 500);

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
      <Header
        onBuildFont={handleBuildFont}
        building={building}
        fontLoaded={fontLoaded}
        familyName={familyName}
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
        <div className="content-area">
          <Sidebar
            axes={axes}
            coordinates={editingCoordinates}
            onAxisChange={handleAxisChange}
            disabled={!selectedInstance}
            sampleText={sampleText}
            onSampleTextChange={setSampleText}
            selectedInstance={selectedInstance}
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
            onAddAvar2Axis={handleAddAvar2Axis}
            onUpdateAvar2Axis={handleUpdateAvar2Axis}
            onUpdateAvar2Mapping={handleUpdateAvar2Mapping}
            onReloadAvar2Data={loadAvar2Data}
            glyphsFileHasUnsavedChanges={glyphsFileHasUnsavedChanges}
            getInstanceSyncStatus={getInstanceSyncStatus}
            instances={instances}
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
            calculateAdvanceWidth={calculateAdvanceWidth}
            advanceWidthLoading={advanceWidthLoading}
            currentAdvanceWidth={currentAdvanceWidth}
          />
        </div>
      </div>
    </div>
  );
}

export default App;
