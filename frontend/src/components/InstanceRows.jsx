import React, { useEffect, useState } from 'react';
import './InstanceRows.css';
import InstanceRow from './InstanceRow';
import { api } from '../api';
import { registerPreviewFont } from '../fontLoad';
import { useBuildStatus } from '../hooks/useBuildStatus';
import { useTransforms } from '../hooks/useTransforms';

function InstanceRows({ instances, selectedInstance, multiSelectedNames, onSelectInstance, editingCoordinates, instanceEditingCoordinates, sampleText, fontUrl, fontLoaded, vfFamilyId, onReorderInstances, fontSize, onDeleteInstance, onMoveInstance, getInstanceSyncStatus, onRenameInstance, onUpdateInstanceStudio, onUpdateInstanceSource, onDemoteFromSource, disabledControlAxes, axisDefaults, defaultOriginNames, originMasterByRow, axes }) {
  // Per-row badge state (grade / rounding / slant) + save handlers come
  // from the useTransforms context, renamed to the old prop names so the
  // row rendering below is untouched.
  const {
    grade, rounding, slant,
    handleSaveInstanceGrade: onSaveInstanceGrade,
    handleRemoveInstanceGrade: onRemoveInstanceGrade,
    handleSaveInstanceRounding: onSaveInstanceRounding,
    handleRemoveInstanceRounding: onRemoveInstanceRounding,
    handleSaveInstanceSlant: onSaveInstanceSlant,
    handleRemoveInstanceSlant: onRemoveInstanceSlant,
  } = useTransforms();
  const [draggedIndex, setDraggedIndex] = useState(null);
  const [dragOverIndex, setDragOverIndex] = useState(null);
  const [fontReady, setFontReady] = useState(false);
  const { buildJob } = useBuildStatus();

  // Load font when available. The family id comes from /api/health so the
  // tool works on any .glyphs file, not just Crispy.
  useEffect(() => {
    if (!(fontUrl && fontLoaded && typeof fontUrl === 'string' && vfFamilyId)) {
      setFontReady(false);
      return undefined;
    }
    // `building` clears as soon as the build lands, but the WOFF2 compress
    // job runs AFTER it; asking for the cached WOFF2 in that window is a
    // guaranteed 409 (and a console error per page load). Wait for the
    // queue to be empty, not just for the font to be new.
    if (buildJob) {
      setFontReady(false);
      return undefined;
    }
    // A transient fetch failure (server busy, tab refreshed mid-request)
    // used to leave the preview in the fallback font forever — retry a
    // couple of times before giving up.
    let cancelled = false;
    let retryTimer = null;
    const attempt = (triesLeft) => {
      registerPreviewFont(vfFamilyId, fontUrl,
                          api.capabilities.woff2 ? api.getWoff2PreviewUrl() : null)
        .then(() => {
          if (!cancelled) setFontReady(true);
        })
        .catch(err => {
          console.error('Failed to load font:', err);
          if (cancelled) return;
          setFontReady(false);
          if (triesLeft > 0) {
            retryTimer = setTimeout(() => attempt(triesLeft - 1), 2000);
          }
        });
    };
    attempt(2);
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [fontUrl, fontLoaded, vfFamilyId, buildJob]);

  // Handle wheel scrolling during drag
  useEffect(() => {
    if (draggedIndex === null) return;
    
    const container = document.querySelector('.instance-rows-container');
    if (!container) return;
    
    // Allow wheel scrolling on the container during drag
    const handleWheel = (e) => {
      // Don't prevent default - allow normal scrolling
    };
    
    container.addEventListener('wheel', handleWheel, { passive: true });
    return () => {
      container.removeEventListener('wheel', handleWheel);
    };
  }, [draggedIndex]);

  if (!fontLoaded) {
    return (
      <div className="instance-rows-container">
        <div className="no-font-message">
          Build the font to see instance previews
        </div>
      </div>
    );
  }

  const handleDragStart = (index) => {
    setDraggedIndex(index);
  };

  const handleDragOver = (e, index) => {
    e.preventDefault();
    setDragOverIndex(index);
    
    // Enable scrolling during drag
    const container = e.currentTarget.closest('.instance-rows-container');
    if (container) {
      const rect = container.getBoundingClientRect();
      const scrollThreshold = 50; // pixels from edge
      const scrollSpeed = 10; // pixels per scroll
      
      // Check if near top edge
      if (e.clientY - rect.top < scrollThreshold) {
        container.scrollTop -= scrollSpeed;
      }
      // Check if near bottom edge
      else if (rect.bottom - e.clientY < scrollThreshold) {
        container.scrollTop += scrollSpeed;
      }
    }
  };

  const handleDragEnd = () => {
    if (draggedIndex === null || dragOverIndex === null || draggedIndex === dragOverIndex) {
      setDraggedIndex(null);
      setDragOverIndex(null);
      return;
    }

    const newInstances = [...instances];
    const [draggedItem] = newInstances.splice(draggedIndex, 1);
    newInstances.splice(dragOverIndex, 0, draggedItem);
    
    onReorderInstances(newInstances);
    setDraggedIndex(null);
    setDragOverIndex(null);
  };

  const handleDragLeave = () => {
    setDragOverIndex(null);
  };

  return (
    <div className="instance-rows-container">
      {instances.map((instance, index) => (
        <div
          key={instance.name}
          draggable
          onDragStart={() => handleDragStart(index)}
          onDragOver={(e) => handleDragOver(e, index)}
          onDragEnd={handleDragEnd}
          onDragLeave={handleDragLeave}
          className={`instance-row-wrapper ${draggedIndex === index ? 'dragging' : ''} ${dragOverIndex === index ? 'drag-over' : ''}`}
        >
          <InstanceRow
            instance={instance}
            isSelected={selectedInstance?.name === instance.name}
            isMultiSelected={!!(multiSelectedNames && multiSelectedNames.has(instance.name))
              && selectedInstance?.name !== instance.name}
            onSelect={(mods) => onSelectInstance(instance, mods)}
            editingCoordinates={editingCoordinates}
            instanceEditingCoordinates={instanceEditingCoordinates}
            sampleText={sampleText}
            fontLoaded={fontLoaded && fontReady}
            fontSize={fontSize}
            vfFamilyId={vfFamilyId}
            onDelete={onDeleteInstance}
            onMove={onMoveInstance}
            allInstances={instances}
            syncStatus={getInstanceSyncStatus ? getInstanceSyncStatus(instance) : 'green'}
            onRename={onRenameInstance}
            onUpdateInstanceStudio={onUpdateInstanceStudio ? () => onUpdateInstanceStudio(instance.name) : undefined}
            onUpdateInstanceSource={onUpdateInstanceSource ? () => onUpdateInstanceSource(instance) : undefined}
            onDemoteFromSource={onDemoteFromSource ? () => onDemoteFromSource(instance) : undefined}
            disabledControlAxes={disabledControlAxes}
            axisDefaults={axisDefaults}
            gradeEnabled={!!grade?.enabled}
            gradePct={(grade?.instances || []).find(e => e.name === instance.name)?.pct ?? null}
            gradeMaxPct={grade?.max_pct?.[instance.name]}
            gradeDefaultPct={grade?.default_pct ?? 0.25}
            gradeDiagnostics={grade?.diagnostics}
            onSaveInstanceGrade={onSaveInstanceGrade}
            onRemoveInstanceGrade={onRemoveInstanceGrade}
            parametricTags={new Set((axes || []).filter(a => a.has_master_coverage !== false).map(a => a.tag))}
            transformTags={new Set((axes || []).filter(a => a.transform_injected).map(a => a.tag))}
            gradeTag="GRAD"
            roundingEnabled={!!rounding?.enabled}
            roundingPct={rounding?.style_pcts?.[instance.name] ?? null}
            roundingDefaultPct={rounding?.default_pct ?? 0}
            onSaveInstanceRounding={onSaveInstanceRounding}
            onRemoveInstanceRounding={onRemoveInstanceRounding}
            slantEnabled={!!slant?.enabled}
            slantPct={slant?.style_pcts?.[instance.name] ?? null}
            slantDefaultPct={slant?.default_pct ?? 0}
            slantAxisMin={(axes || []).find(a => a.tag === 'slnt')?.min ?? null}
            onSaveInstanceSlant={onSaveInstanceSlant}
            onRemoveInstanceSlant={onRemoveInstanceSlant}
            isDefaultOrigin={!!defaultOriginNames?.has(instance.name)}
            originMaster={originMasterByRow?.[instance.name] || null}
          />
        </div>
      ))}
    </div>
  );
}

export default InstanceRows;
