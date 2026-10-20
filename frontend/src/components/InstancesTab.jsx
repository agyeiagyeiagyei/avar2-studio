import React from 'react';
import { api } from '../api';
import { isUploadDataset } from '../static-api';
import { useStudioData } from '../hooks/useStudioData';
import { useTransforms } from '../hooks/useTransforms';
import { useControlAxes } from '../hooks/useControlAxes';
import { useInstanceEditing } from '../hooks/useInstanceEditing';
import Sidebar from './Sidebar';
import InstanceRows from './InstanceRows';

/**
 * The Instances tab: Sidebar + InstanceRows. All state comes from the
 * context hooks (studio data / instance editing / transforms); this
 * container only wires it into the two prop-driven components.
 */
export default function InstancesTab() {
  const {
    axes, sampleText, setSampleText, familyName, fontSize, setFontSize,
    instances, masters, vfFamilyId, fontLoaded, fontUrl,
    avar2Mode, avar2Instances, avar2Axes, glyphsFileHasUnsavedChanges,
    axisDefaults, defaultOriginNames, originMasterByRow, staticMode,
    handleAddAvar2Axis, handleUpdateAvar2Axis, handleDeleteAvar2Axis,
    handleUpdateAvar2Mapping, loadAvar2Data,
  } = useStudioData();
  const { badgeGovernedTags } = useTransforms();
  const {
    selectedInstance, multiSelectedNames, editingCoordinates,
    instanceEditingCoordinates, originalCoordinates,
    getInstanceSyncStatus, handleSelectInstance, handleAxisChange,
    handleUpdateInstance, handleResetCoordinates, handleDuplicateInstance,
    handleCreateNewInstance, handleDeleteInstance, handleMoveInstance,
    handleReorderInstances, handleRenameInstance, handleUpdateInstanceStudio,
    handleUpdateInstanceSource, handleDemoteFromSource,
  } = useInstanceEditing();
  const { disabledControlAxes } = useControlAxes();

  return (
    <div className="content-area">
      <Sidebar
        axes={axes}
        coordinates={editingCoordinates}
        onAxisChange={handleAxisChange}
        multiTargetCount={multiSelectedNames.size > 1 ? multiSelectedNames.size : 0}
        badgeGovernedTags={badgeGovernedTags}
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
      />
      <InstanceRows
        instances={instances}
        selectedInstance={selectedInstance}
        multiSelectedNames={multiSelectedNames}
        onSelectInstance={handleSelectInstance}
        editingCoordinates={editingCoordinates}
        instanceEditingCoordinates={instanceEditingCoordinates}
        sampleText={sampleText}
        fontUrl={fontUrl}
        vfFamilyId={vfFamilyId}
        fontLoaded={fontLoaded}
        onReorderInstances={handleReorderInstances}
        fontSize={fontSize}
        onDeleteInstance={handleDeleteInstance}
        getInstanceSyncStatus={getInstanceSyncStatus}
        onMoveInstance={handleMoveInstance}
        onRenameInstance={handleRenameInstance}
        onUpdateInstanceStudio={handleUpdateInstanceStudio}
        onUpdateInstanceSource={handleUpdateInstanceSource}
        onDemoteFromSource={handleDemoteFromSource}
        disabledControlAxes={disabledControlAxes}
        originMasterByRow={originMasterByRow}
        axisDefaults={axisDefaults}
        defaultOriginNames={defaultOriginNames}
        axes={axes}
      />
    </div>
  );
}
