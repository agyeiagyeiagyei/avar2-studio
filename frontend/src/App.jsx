import React, { useEffect, useState } from 'react';
import './App.css';
import { api } from './api';
import { useBuildStatus } from './hooks/useBuildStatus';
import { StudioDataProvider, useStudioData } from './hooks/useStudioData';
import { TransformsProvider } from './hooks/useTransforms';
import { ControlAxesProvider, useControlAxes } from './hooks/useControlAxes';
import { InstanceEditingProvider, useInstanceEditing } from './hooks/useInstanceEditing';
import { isUploadDataset } from './static-api';
import Header from './components/Header';
import InstancesTab from './components/InstancesTab';
import PreviewTab from './components/PreviewTab';
import KinetikernTab from './components/KinetikernTab';
import TesterTab from './components/TesterTab';
import FontraEditorModal from './components/FontraEditorModal';
import DeleteInstanceModal from './components/DeleteInstanceModal';
import DemoLanding from './components/DemoLanding';
import StatusBanners from './components/StatusBanners';
import BuildVeil from './components/BuildVeil';

// The shell: first-load gate, landing overlay, header, status banners,
// tab bar and tab content. All state lives in the context hooks (see
// hooks/useStudioData, useTransforms, useControlAxes, useInstanceEditing);
// what remains here is composition plus the mainTab view switch.
function AppShell() {
  const { building, buildJob, buildStale, lastBuildStatus } = useBuildStatus();
  const {
    loading, showLanding, setShowLanding, familyName, vfFamilyId, fontLoaded,
    fontUrl, builtFontFilename, sampleText, setSampleText, testerState,
    setTesterState, fontSize, setFontSize, axes, instances, avar2Error,
    jumpLocation, glyphsFileHasUnsavedChanges, staticMode, loadData,
    kinetikernFocus,
  } = useStudioData();
  const { fontraEditor, handleCloseFontraEditor } = useControlAxes();
  const {
    showDeleteModal, setShowDeleteModal, instanceToDelete,
    setInstanceToDelete, handleConfirmDelete,
  } = useInstanceEditing();
  // Which top-level view is active: 'instances' (authoring) or
  // 'preview' (free-form driving of the built font).
  const [mainTab, setMainTab] = useState('instances');

  // A row's Kinetikern badge asked to open the tab on its own style (K1):
  // switch here, the tab itself selects the name from `kinetikernFocus`.
  useEffect(() => {
    if (kinetikernFocus) setMainTab('kinetikern');
  }, [kinetikernFocus]);

  // Rebuild-on-demand: coordinate auto-saves no longer rebuild per
  // edit, so the built font can be stale when the designer opens the
  // Preview tab (the one place the avar2 table is actually consumed).
  // Kick ONE rebuild when arriving there stale; build_stale clears
  // when the build lands, so this can't loop.
  useEffect(() => {
    // `building` excludes the WOFF2 compress job that follows a build; asking
    // for a rebuild while one is queued only piles up behind it (S5).
    if (mainTab === 'preview' && buildStale && !building && !buildJob) {
      api.buildFont().catch(() => {});
    }
  }, [mainTab, buildStale, building, buildJob]);

  // First load only: refreshes (a commit's loadData, a Load Font swap)
  // keep the mounted workspace instead of re-gating behind "Loading…".
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
        fontLoaded={fontLoaded}
        familyName={familyName}
        onSourceLoaded={loadData}
        staticMode={staticMode}
        isUploadDataset={isUploadDataset()}
        hideRebuild={staticMode && !isUploadDataset()}
        allowImportInStatic={isUploadDataset()}
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

      <StatusBanners />

      <div className={`main-content${lastBuildStatus === 'failed' ? ' main-content-blocked' : ''}`}
           aria-disabled={lastBuildStatus === 'failed' ? 'true' : undefined}>
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
          {api.capabilities.kinetikern && (
          <button
            type="button"
            className={`main-tab${mainTab === 'kinetikern' ? ' active' : ''}`}
            onClick={() => setMainTab('kinetikern')}
          >
            Kinetikern
          </button>
          )}
          <button
            type="button"
            className={`main-tab${mainTab === 'tester' ? ' active' : ''}`}
            onClick={() => setMainTab('tester')}
          >
            Tester
          </button>
        </div>
        <div className={`build-scope${building ? ' is-building' : ''}`}>
        {mainTab === 'kinetikern' && api.capabilities.kinetikern ? (
          <KinetikernTab
            instances={instances}
            axes={axes}
            vfFamilyId={vfFamilyId}
            fontLoaded={fontLoaded}
            sampleText={sampleText}
            onSampleTextChange={setSampleText}
            fontSize={fontSize}
            onFontSizeChange={setFontSize}
          />
        ) : mainTab === 'tester' ? (
          <TesterTab
            axes={axes}
            vfFamilyId={vfFamilyId}
            fontLoaded={fontLoaded}
            fontSize={fontSize}
            onFontSizeChange={setFontSize}
            testerState={testerState}
            onTesterStateChange={setTesterState}
          />
        ) : mainTab === 'instances' ? (
          <InstancesTab />
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
        <BuildVeil />
        </div>
        </>
        )}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <StudioDataProvider>
      <TransformsProvider>
        <ControlAxesProvider>
          <InstanceEditingProvider>
            <AppShell />
          </InstanceEditingProvider>
        </ControlAxesProvider>
      </TransformsProvider>
    </StudioDataProvider>
  );
}
