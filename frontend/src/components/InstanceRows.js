import React, { useEffect, useState } from 'react';
import './InstanceRows.css';
import InstanceRow from './InstanceRow';

function InstanceRows({ instances, selectedInstance, onSelectInstance, editingCoordinates, instanceEditingCoordinates, sampleText, fontUrl, fontLoaded, vfFamilyId, onReorderInstances, fontSize, onDeleteInstance, onMoveInstance, getInstanceSyncStatus, onRenameInstance, onUpdateInstanceStudio, onUpdateInstanceSource, calculateAdvanceWidth, advanceWidthLoading, currentAdvanceWidth }) {
  const [draggedIndex, setDraggedIndex] = useState(null);
  const [dragOverIndex, setDragOverIndex] = useState(null);
  const [fontReady, setFontReady] = useState(false);

  // Load font when available. The family id comes from /api/health so the
  // tool works on any .glyphs file, not just Crispy.
  useEffect(() => {
    if (fontUrl && fontLoaded && typeof fontUrl === 'string' && vfFamilyId) {
      // Remove old font if it exists to force reload
      const oldFont = Array.from(document.fonts).find(f => f.family === vfFamilyId);
      if (oldFont) {
        document.fonts.delete(oldFont);
      }

      const fontFace = new FontFace(vfFamilyId, `url(${fontUrl})`);
      fontFace.load()
        .then((loadedFont) => {
          document.fonts.add(loadedFont);
          // Wait for font to be ready
          return document.fonts.ready.then(() => {
            setFontReady(true);
          });
        })
        .catch(err => {
          console.error('Failed to load font:', err);
          setFontReady(false);
        });
    } else {
      setFontReady(false);
    }
  }, [fontUrl, fontLoaded, vfFamilyId]);

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
            onSelect={() => onSelectInstance(instance)}
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
            calculateAdvanceWidth={calculateAdvanceWidth}
            advanceWidthLoading={advanceWidthLoading}
            currentAdvanceWidth={selectedInstance?.name === instance.name ? currentAdvanceWidth : null}
          />
        </div>
      ))}
    </div>
  );
}

export default InstanceRows;
