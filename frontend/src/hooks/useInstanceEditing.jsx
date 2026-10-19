import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { csvDivergesFromSource } from '../instance-sync';
import { useBuildStatus } from './useBuildStatus';
import { useStudioData } from './useStudioData';
import { useTransforms } from './useTransforms';
import { useBuildWait, scrollToInstance } from './useBuildWait';

/**
 * Instance authoring: selection (incl. shift/cmd multi-select), the
 * per-instance coordinate drafts and their sync baselines, the tri-state
 * sync status, auto-persist of studio rows, and every mutating row action
 * (update/duplicate/rename/delete/reorder, CSV-vs-source writeback).
 *
 * Mounted ABOVE the tab switch (App wraps the shell in this provider), so
 * a selection and its drafts survive Instances → Preview → Instances;
 * the InstancesTab container below only reads this context.
 */
const InstanceEditingContext = createContext(null);

export function InstanceEditingProvider({ children }) {
  const { setBuilding } = useBuildStatus();
  const {
    instances, setInstances, axes, avar2Instances,
    previewOnlyAxisTags, injectedAxisTags, controlAxisTags,
    setError, vfFamilyId, setFontUrl, setFontLoaded, loadAvar2Data,
    sourceSwapCount, selectedInstanceNameRef,
  } = useStudioData();
  const { badgeGovernedTags } = useTransforms();

  const [selectedInstance, setSelectedInstance] = useState(null);
  // SHIFT-CLICK MULTI-SELECT: names of additionally highlighted rows (the
  // anchor included). Only SECONDARY-axis slider edits broadcast to the
  // set — those are preview-only values (stripped from saves, skipped by
  // sync status), so nothing persisted can flatten or mix across rows.
  const [multiSelectedNames, setMultiSelectedNames] = useState(() => new Set());
  const [editingCoordinates, setEditingCoordinates] = useState({});
  // Store editing coordinates per instance to persist when deselected
  const [instanceEditingCoordinates, setInstanceEditingCoordinates] = useState({});
  // Store original coordinates per instance for sync status comparison
  const [instanceOriginalCoordinates, setInstanceOriginalCoordinates] = useState({});
  const [originalCoordinates, setOriginalCoordinates] = useState({});
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [instanceToDelete, setInstanceToDelete] = useState(null);

  // The poll (useStudioData) reads the selected row's name through this
  // ref for its post-rebuild scroll restore.
  selectedInstanceNameRef.current = selectedInstance?.name;

  // SOURCE SWAP: loadData (useStudioData) bumped sourceSwapCount — the
  // new source's axis tags + instance names invalidate every keyed
  // scratch map here, so drop the lot (loadData's comment has the full
  // rationale; it can't reach this state from above the provider).
  const prevSwapCountRef = useRef(sourceSwapCount);
  useEffect(() => {
    if (prevSwapCountRef.current === sourceSwapCount) return;
    prevSwapCountRef.current = sourceSwapCount;
    setSelectedInstance(null);
    setEditingCoordinates({});
    setOriginalCoordinates({});
    setInstanceEditingCoordinates({});
    setInstanceOriginalCoordinates({});
  }, [sourceSwapCount]);

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
  }, [selectedInstance, avar2Instances, loadAvar2Data]);

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
    // instance.coordinates now carries the CSV (studio) values merged
    // over the source's for display; the pristine source set rides in
    // source_coordinates so the CSV-vs-source divergence (orange) still
    // reads against the real source file.
    const sourceCoords = instance.source_coordinates || instance.coordinates || {};
    const persisted = { ...sourceCoords, ...csvIn, ...csvOut };
    // Control-axis values hydrate from the control sidecar into
    // instance.coordinates — that IS their persisted home (they are
    // deliberately absent from source_coordinates and the CSV).
    for (const k of Object.keys(instance.coordinates || {})) {
      if (controlAxisTags.has(String(k).toLowerCase())) {
        persisted[k] = instance.coordinates[k];
      }
    }
    const csvCoords = Object.keys(csvOut).length > 0 ? csvOut : null;

    if (edits && Object.keys(edits).length > 0) {
      const dirty = Object.keys(edits).some(k => {
        if (previewOnlyAxisTags.has(String(k).toLowerCase())) return false;   // preview-only axis — never persisted
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

    if (csvCoords) {
      // Compare only the axes the CSV carries — see instance-sync.js.
      if (csvDivergesFromSource(csvCoords, sourceCoords)) {
        return 'orange';   // CSV diverges from source — needs a source push
      }
    }

    return 'green';
  }, [selectedInstance, editingCoordinates, instanceEditingCoordinates, avar2Instances, previewOnlyAxisTags, controlAxisTags]);

  // Advance-width measurement (per-row /api/text-width polling with a
  // cache + interpolation + live recalculation) was removed at the
  // designer's request — it dominated request traffic. The backend
  // endpoint survives for easy reinstatement.

  const handleSelectInstance = useCallback((instance, mods) => {
    // Shift-click selects the contiguous range between the already-selected
    // row (the anchor) and this one; cmd/ctrl-click toggles one row in the
    // set. The anchor stays the editing target — multi-selection only
    // widens where secondary-axis edits land.
    if (mods && (mods.shift || mods.toggle) && selectedInstance
        && selectedInstance.name !== instance.name) {
      if (mods.shift) {
        const names = instances.map(i => i.name);
        const a = names.indexOf(selectedInstance.name);
        const b = names.indexOf(instance.name);
        if (a !== -1 && b !== -1) {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          setMultiSelectedNames(new Set(names.slice(lo, hi + 1)));
          return;
        }
      } else {
        setMultiSelectedNames(prev => {
          const next = new Set(prev);
          next.add(selectedInstance.name);
          if (next.has(instance.name)) next.delete(instance.name);
          else next.add(instance.name);
          return next.size > 1 ? next : new Set();
        });
        return;
      }
    }
    setMultiSelectedNames(new Set());
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
  }, [selectedInstance, editingCoordinates, instanceEditingCoordinates, instanceOriginalCoordinates, originalCoordinates, instances]);

  const handleAxisChange = useCallback((tag, value) => {
    // Badge-governed axes never take per-instance slider edits — the
    // sidebar renders them inert, and this guard holds for any other
    // edit path that might route a value at them.
    if (badgeGovernedTags.has(tag)) return;
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
    // Broadcast SECONDARY-axis edits to every multi-selected row, then
    // persist them (debounced): per-row control values save to the
    // control sidecar via the csv-only update path, which never touches
    // the source file. Only the ONE dragged axis is written, so nothing
    // else can flatten across rows.
    if (controlAxisTags.has(String(tag).toLowerCase()) && multiSelectedNames.size > 1) {
      setInstanceEditingCoordinates(prevStored => {
        const next = { ...prevStored };
        for (const name of multiSelectedNames) {
          if (selectedInstance && name === selectedInstance.name) continue;
          const inst = instances.find(i => i.name === name);
          if (!inst) continue;
          next[name] = { ...(next[name] || inst.coordinates), [tag]: value };
        }
        return next;
      });
      const targets = [...multiSelectedNames];
      clearTimeout(broadcastSaveTimer.current);
      broadcastSaveTimer.current = setTimeout(async () => {
        // Sequential on purpose: each save rewrites the whole control
        // sidecar, so parallel PUTs would race the read-modify-write.
        for (const name of targets) {
          if (selectedInstance && name === selectedInstance.name
              && selectedInstance.origin === 'studio') continue; // anchor auto-saves
          try {
            await api.updateInstance(name, { [tag]: value }, { csvOnly: true });
          } catch (err) {
            console.warn(`Saving ${tag} for "${name}" failed:`, err.message || err);
          }
        }
        api.getInstances().then(d => setInstances(d.instances)).catch(() => {});
      }, 800);
    }
  }, [selectedInstance, editingCoordinates, badgeGovernedTags, controlAxisTags, multiSelectedNames, instances, setInstances]);

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
  const broadcastSaveTimer = useRef(null);
  const autoSaveChain = useRef(Promise.resolve());
  const autoSaveLastSaved = useRef({});
  useEffect(() => {
    if (!selectedInstance || selectedInstance.origin !== 'studio') return undefined;
    if (!editingCoordinates || Object.keys(editingCoordinates).length === 0) return undefined;
    const name = selectedInstance.name;
    const coords = { ...selectedInstance.coordinates, ...editingCoordinates };
    // Preview-only axes (secondary parametric, GRAD/ROND) never
    // persist — dragging them must not schedule CSV writes or
    // destabilize the saved-key guard. SPAC stays: it saves to the
    // CSV's per-instance spacing column.
    const persistedKey = (c) => {
      const kept = {};
      for (const k of Object.keys(c)) {
        if (!previewOnlyAxisTags.has(k.toLowerCase())) kept[k] = c[k];
      }
      return JSON.stringify(Object.keys(kept).sort().map(k => [k, kept[k]]));
    };
    for (const k of Object.keys(coords)) {
      if (previewOnlyAxisTags.has(k.toLowerCase())) delete coords[k];
    }
    const coordsKey = persistedKey(coords);
    // Idempotence guard. WITHOUT it, a completed rebuild (font reload,
    // instances refetch) re-fires this effect with UNCHANGED coords,
    // which saves again, which rebuilds again — a self-sustaining
    // rebuild loop while the user sits idle (shipped once: 442 PUTs
    // at ~11s intervals). Only genuinely new coordinates get saved.
    if (autoSaveLastSaved.current[name] === coordsKey) {
      return undefined;
    }
    // Selecting a row seeds editingCoordinates from the row itself;
    // that is not an edit. The guard above has no memory before the
    // first save, so without this check a plain click saved the
    // unchanged row, which bumped the mappings' mtime, marked the build
    // stale, and cost a full rebuild the moment the Preview tab opened.
    if (coordsKey === persistedKey(selectedInstance.coordinates || {})) {
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
  }, [editingCoordinates, selectedInstance, previewOnlyAxisTags, setInstances, loadAvar2Data]);

  // The post-mutation dance (wait for the server's rebuild, reload the
  // preview font, wait for the browser to have it) — one helper for the
  // update / duplicate / rename / delete paths (hooks/useBuildWait).
  const waitForBuildAndReloadFont = useBuildWait({ vfFamilyId, setFontUrl, setFontLoaded });

  // Helper function to update a specific instance by name. ``csvOnly``
  // is threaded into the API call so the server skips the source-file
  // writeback (the "Update in avar2-studio" path).
  const updateInstanceByName = async (instanceName, coordinatesToUse, options = {}) => {
    const instance = instances.find(inst => inst.name === instanceName);
    if (!instance) {
      throw new Error(`Instance "${instanceName}" not found`);
    }

    // Strip the preview-only axes (secondary parametric, GRAD/ROND) —
    // live preview state, not instance data. SPAC goes through: the
    // server routes it to the CSV's per-instance spacing column (never
    // to the .glyphs writeback).
    if (previewOnlyAxisTags.size > 0) {
      coordinatesToUse = Object.fromEntries(
        Object.entries(coordinatesToUse).filter(([tag]) => !previewOnlyAxisTags.has(tag.toLowerCase()))
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
  }, [selectedInstance, setError, setInstances, setBuilding]);

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
    // (skipped where the source can't be written back — the browser).
    if (api.capabilities.sourceWriteback && (!instanceName || instanceName === selectedInstance?.name)) {
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

      // The backend rebuilds after the update: wait it out and reload the
      // preview font (no second build is triggered here).
      await waitForBuildAndReloadFont();

      // Reload instances to ensure we have the latest data (in case updateInstanceByName didn't reload)
      // This is important because the backend may have updated the Glyphs file
      const instancesData = await api.getInstances();
      setInstances(instancesData.instances);
      // Refresh the CSV mapping too: the sync-status check compares the
      // draft against avar2Instances' `out` map, and the source writeback
      // just rewrote that CSV row. Without this the dot stayed red until a
      // page reload (the studio auto-save path already refreshes it).
      await loadAvar2Data();


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
        // The refreshed instance is the canonical post-write state: the
        // .glyphs serializer may round the float it stored (525.3777 →
        // 525.4). Keeping the pre-save request here left the draft 0.03
        // off the persisted value, so the row stayed red after a
        // successful source save.
        setEditingCoordinates(updatedCoords);
      }

      // Reset building state after font is fully loaded and ready
      setBuilding(false);

      // Scroll to updated instance after font is ready
      scrollToInstance(instanceNameToScroll);
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
      for (const k of Object.keys(coordinatesToUse)) {
        if (injectedAxisTags.has(k.toLowerCase())) delete coordinatesToUse[k];
      }

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
      // Preview-only axes (secondary parametric, GRAD/ROND) are not
      // instance data; SPAC is kept so a duplicate keeps its spacing.
      const persistedCoords = Object.fromEntries(
        Object.entries(coordinatesToUse).filter(([tag]) => !previewOnlyAxisTags.has(tag.toLowerCase()))
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

      // The backend rebuilds after the creation: wait it out and reload
      // the preview font.
      setBuilding(true); // Set building state immediately to show UI feedback
      await waitForBuildAndReloadFont();

      // Reload instances to get updated data (with new instance)
      const instancesDataAfterDup = await api.getInstances();
      setInstances(instancesDataAfterDup.instances);


      // Reload SPAC values if spacMode is enabled

      // Reset building state after font is fully loaded and ready
      setBuilding(false);

      // Scroll to new instance after font is ready
      scrollToInstance(instanceNameToScroll);
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

      // The backend rebuilds after the rename: wait it out and reload the
      // preview font.
      await waitForBuildAndReloadFont();

      // Reload instances to get updated data (with new name)
      const instancesData = await api.getInstances();
      setInstances(instancesData.instances);

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
      scrollToInstance(instanceNameToScroll);
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
  }, [selectedInstance, setError, setInstances]);

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
  }, [setError, setInstances]);

  const handleConfirmDelete = useCallback(async (deleteFromGlyphs) => {
    if (!instanceToDelete) return;

    const instanceName = instanceToDelete.name;

    try {
      setError(null);

      if (deleteFromGlyphs) {
        // Full deletion: delete from source file and CSV, then rebuild
        setBuilding(true); // Set building state immediately to show UI feedback

        await api.deleteInstance(instanceName);

        // The backend rebuilds after the deletion: wait it out and reload
        // the preview font.
        await waitForBuildAndReloadFont();

        // Reload instances to get updated list (without deleted instance)
        const instancesData = await api.getInstances();
        setInstances(instancesData.instances);

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
  }, [instanceToDelete, selectedInstance, waitForBuildAndReloadFont, setError, setInstances, setBuilding]);

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
    // Persist: the CSV row order is the instance order's home — without
    // this, the next refetch (any rebuild) snaps the list back.
    if (api.reorderInstances) {
      api.reorderInstances(newInstances.map(i => i.name)).catch(err =>
        console.error('Failed to persist instance order:', err));
    }
  }, [instances, setInstances]);

  const handleReorderInstances = useCallback((newInstances) => {
    setInstances(newInstances);
    if (api.reorderInstances) {
      api.reorderInstances(newInstances.map(i => i.name)).catch(err =>
        console.error('Failed to persist instance order:', err));
    }
  }, [setInstances]);

  const value = {
    selectedInstance,
    multiSelectedNames,
    editingCoordinates,
    instanceEditingCoordinates,
    instanceOriginalCoordinates,
    originalCoordinates,
    showDeleteModal, setShowDeleteModal,
    instanceToDelete, setInstanceToDelete,
    getInstanceSyncStatus,
    handleSelectInstance,
    handleAxisChange,
    handleUpdateInstanceStudio,
    handleUpdateInstanceSource,
    handleDemoteFromSource,
    handleUpdateInstance,
    handleResetCoordinates,
    handleCreateNewInstance,
    handleDuplicateInstance,
    handleRenameInstance,
    handleDeleteInstance,
    handleAddInstanceToSource,
    handleConfirmDelete,
    handleMoveInstance,
    handleReorderInstances,
  };

  return (
    <InstanceEditingContext.Provider value={value}>
      {children}
    </InstanceEditingContext.Provider>
  );
}

export function useInstanceEditing() {
  const ctx = useContext(InstanceEditingContext);
  if (!ctx) {
    throw new Error('useInstanceEditing() needs an <InstanceEditingProvider> above it (App mounts one).');
  }
  return ctx;
}
