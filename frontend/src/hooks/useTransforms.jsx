import React, { createContext, useContext, useRef, useState } from 'react';
import { api } from '../api';
import { useBuildStatus } from './useBuildStatus';
import { useStudioData } from './useStudioData';

/**
 * Transforms + grade authoring: the transforms/grade state itself lives
 * in useStudioData (loadData hydrates it with the rest of the dataset);
 * this owns the commit paths — toggle/param/apply for post-build
 * transforms, the grade controls, and the per-style ROND/slnt/GRAD
 * badge saves — plus `transformsDirty` (source-stage edits pending
 * Apply) and the derived rounding/slant/badgeGovernedTags views.
 */
const TransformsContext = createContext(null);

const _transformEntries = (list) =>
  list.map(t => ({ type: t.id, enabled: !!t.enabled, params: { ...(t.params || {}) } }));

export function TransformsProvider({ children }) {
  const { setBuilding } = useBuildStatus();
  const { transforms, setTransforms, grade, setGrade, setError, loadData } = useStudioData();

  // ---- Post-build transforms (e.g. SPAC) ----
  const transformCommitTimer = useRef(null);
  // Source-stage transforms (round_corners) rebuild the whole shadow on
  // commit — seconds, and a full preview reload. Their param edits stay
  // local until the flyout's Apply; this maps transform id -> unapplied
  // edits pending.
  const [transformsDirty, setTransformsDirty] = useState({});

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
      return true;
    } catch (err) {
      if (prev) setTransforms(prev);            // revert optimistic UI to last known-good
      setError(err.message);
      console.error('Transform update failed:', err);
      return false;
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
    setTransformsDirty(d => ({ ...d, [id]: false })); // the commit carries any pending edits
    commitTransforms(_transformEntries(next), prev); // toggling is deliberate — rebuild now
  };

  const handleTransformParam = (id, key, value) => {
    const prev = transforms;
    const next = transforms.map(t =>
      t.id === id ? { ...t, params: { ...(t.params || {}), [key]: value } } : t
    );
    setTransforms(next);                        // immediate input feedback, no rebuild yet
    // A SOURCE-stage transform rebuilds the whole shadow on commit —
    // seconds and a full preview reload, which yanked the flyout out from
    // under the second field. Its edits wait for the explicit Apply.
    const stage = (transforms.find(t => t.id === id) || {}).stage;
    if (stage === 'source') {
      setTransformsDirty(d => ({ ...d, [id]: true }));
      return;
    }
    // Font-stage (~1s rebuild): debounce — a recompile per keystroke is the
    // expensive, dishonest path the old SPAC code was pulled for. Commit
    // ~0.8s after the last edit.
    if (transformCommitTimer.current) clearTimeout(transformCommitTimer.current);
    transformCommitTimer.current = setTimeout(() => {
      transformCommitTimer.current = null;
      commitTransforms(_transformEntries(next), prev);
    }, 800);
  };

  const handleApplyTransform = async (id) => {
    setTransformsDirty(d => ({ ...d, [id]: false }));
    // No optimistic revert target: on failure the typed values stay in the
    // fields (still marked unapplied) instead of being thrown away.
    const ok = await commitTransforms(_transformEntries(transforms), null);
    if (!ok) setTransformsDirty(d => ({ ...d, [id]: true }));
  };

  // ---- Per-style rounding (round_corners: default % + style percents) ----
  const roundingEntry = (transforms || []).find(x => x.id === 'round_corners');
  const rounding = roundingEntry ? {
    enabled: !!roundingEntry.enabled,
    default_pct: parseFloat(roundingEntry.params?.default_pct) || 0,
    style_pcts: (roundingEntry.params?.style_pcts && typeof roundingEntry.params.style_pcts === 'object')
      ? roundingEntry.params.style_pcts : {},
  } : null;

  const commitStylePcts = (nextPcts) => {
    const prev = transforms;
    const next = transforms.map(x =>
      x.id === 'round_corners'
        ? { ...x, params: { ...(x.params || {}), style_pcts: nextPcts } }
        : x
    );
    setTransforms(next);
    // A style's percent is its ROND coordinate — a font-stage stamp, so
    // the server takes the fast rebuild path (no source re-round).
    return commitTransforms(_transformEntries(next), prev);
  };

  const handleSaveInstanceRounding = (instanceName, pct) =>
    commitStylePcts({ ...(rounding?.style_pcts || {}), [instanceName]: pct });

  const handleRemoveInstanceRounding = (instanceName) => {
    const nextPcts = { ...(rounding?.style_pcts || {}) };
    delete nextPcts[instanceName];
    return commitStylePcts(nextPcts);
  };

  // ---- Per-style slant (slant: default % + style percents) ----
  const slantEntry = (transforms || []).find(x => x.id === 'slant');
  const slant = slantEntry ? {
    enabled: !!slantEntry.enabled,
    default_pct: parseFloat(slantEntry.params?.default_pct) || 0,
    style_pcts: (slantEntry.params?.style_pcts && typeof slantEntry.params.style_pcts === 'object')
      ? slantEntry.params.style_pcts : {},
  } : null;

  const commitSlantStylePcts = (nextPcts) => {
    const prev = transforms;
    const next = transforms.map(x =>
      x.id === 'slant'
        ? { ...x, params: { ...(x.params || {}), style_pcts: nextPcts } }
        : x
    );
    setTransforms(next);
    // A style's percent is its slnt coordinate — a font-stage stamp, so
    // the server takes the fast rebuild path (no source re-slant).
    return commitTransforms(_transformEntries(next), prev);
  };

  const handleSaveInstanceSlant = (instanceName, pct) =>
    commitSlantStylePcts({ ...(slant?.style_pcts || {}), [instanceName]: pct });

  const handleRemoveInstanceSlant = (instanceName) => {
    const nextPcts = { ...(slant?.style_pcts || {}) };
    delete nextPcts[instanceName];
    return commitSlantStylePcts(nextPcts);
  };

  // Axes whose per-style value comes from a row badge (ROND/slnt via
  // their transforms' style_pcts, GRAD via grade): in the Instances
  // view their sidebar sliders are inert — a drag there LOOKS like a
  // per-instance edit but the row renders the badge's value, and the
  // axis never persists with the instance. The Preview tab's sliders
  // stay live for exploring them. SPAC is deliberately NOT in this set:
  // its per-instance CSV cell is real per-instance state.
  const badgeGovernedTags = React.useMemo(() => {
    const tags = new Set();
    for (const t of transforms || []) {
      if (!t.enabled || !t.injected_axis_tag) continue;
      if ((t.params_schema || []).some(p => p.key === 'style_pcts')) {
        tags.add(t.injected_axis_tag);
      }
    }
    // GRAD is deliberately NOT here: rows have no per-style GRAD render
    // overlay (they draw at GRAD 0), so its slider works as a live
    // preview of the selected row — like the Preview tab's. The badge
    // still owns the SAVED per-instance grade, and GRAD is
    // transform_injected, so a drag is stripped from every save and
    // ignored by the sync dots. ROND/slnt stay inert: their rows render
    // the badge percent, which would override (and contradict) a drag.
    return tags;
  }, [transforms]);

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

  // Intensity scales EVERY authored grade%, so the built font changes —
  // debounce the typing, then commit once (the commit rebuilds).
  const handleGradeIntensity = (value) => {
    setGrade(g => ({ ...g, intensity: value }));   // immediate input feedback
    if (gradeCommitTimer.current) clearTimeout(gradeCommitTimer.current);
    const prev = grade;
    gradeCommitTimer.current = setTimeout(() => {
      gradeCommitTimer.current = null;
      commitGrade({ intensity: value }, prev);
    }, 600);
  };

  // Whether a grade may thicken where the counters have no room to open.
  // Deliberate toggle — rebuild now.
  const handleGradeClamp = (value) => {
    const prev = grade;
    setGrade(g => ({ ...g, clamp_to_headroom: value }));
    commitGrade({ clamp_to_headroom: value }, prev);
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

  const value = {
    transformsDirty,
    rounding,
    slant,
    badgeGovernedTags,
    handleToggleTransform,
    handleTransformParam,
    handleApplyTransform,
    handleToggleGrade,
    handleGradeDefault,
    handleGradeIntensity,
    handleGradeClamp,
    handleSaveInstanceGrade,
    handleRemoveInstanceGrade,
    handleSaveInstanceRounding,
    handleRemoveInstanceRounding,
    handleSaveInstanceSlant,
    handleRemoveInstanceSlant,
  };

  return (
    <TransformsContext.Provider value={value}>
      {children}
    </TransformsContext.Provider>
  );
}

export function useTransforms() {
  const ctx = useContext(TransformsContext);
  if (!ctx) {
    throw new Error('useTransforms() needs a <TransformsProvider> above it (App mounts one).');
  }
  return ctx;
}
