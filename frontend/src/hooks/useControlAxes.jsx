import React, { createContext, useCallback, useContext, useState } from 'react';
import { api } from '../api';
import { useBuildStatus } from './useBuildStatus';
import { useStudioData } from './useStudioData';

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

/**
 * Secondary parametric (control) axes: declare/update/delete, brace-layer
 * writes, reseed, and the Fontra editor modal. The coverage data itself
 * (glyphCoverageAxes / glyphChars / disabledControlAxes) lives in
 * useStudioData — loadData refetches and resets it with the rest of the
 * dataset — and is re-exported here so Sidebar takes one context.
 */
const ControlAxesContext = createContext(null);

export function ControlAxesProvider({ children }) {
  const { setBuilding } = useBuildStatus();
  const {
    axes, setAxes, setError, setFontUrl, loadGlyphCoverage,
    glyphCoverageAxes, glyphChars, disabledControlAxes, setDisabledControlAxes,
    handleToggleDisableControlAxis,
  } = useStudioData();
  // Fontra iframe state — non-null = modal is open. The url comes
  // from POST /api/control-axes/<tag>/open-editor.
  const [fontraEditor, setFontraEditor] = useState(null);

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
  }, [loadGlyphCoverage, setAxes, setFontUrl]);

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
  }, [refreshAfterControlAxisChange, setDisabledControlAxes, setError]);

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

  // CONTROL AXES — pull an updated source through a brace layer.
  //
  // A drawn layer's outline is stored in the sidecar and restored on every
  // rebuild, which is what makes it survive the shadow being wiped — and also
  // what stops it following the masters it was drawn over. Re-seeding
  // recomputes it from the current source and DISCARDS the drawing.
  //
  // Two guards, because the badge the UI renders can be stale: we confirm up
  // front when we already know a drawing is there, and the server refuses with
  // 409 when it finds one we didn't know about (drawn since the last poll).
  // Only that second confirmation sends force.
  const handleControlAxisReseed = useCallback(async (tag, { layers, hasDrawing } = {}) => {
    const names = [...new Set((layers || []).map(l => l.glyph))].join(', ') || 'this layer';
    if (hasDrawing) {
      const ok = window.confirm(
        `Replace the hand-drawn outline on ${names}?\n\n` +
        `Re-seeding recomputes the layer from the current masters and discards the drawing. ` +
        `This updates the studio's saved layers and can't be undone from here.`
      );
      if (!ok) return;
    }
    const send = async (force) => {
      await api.reseedControlAxisLayers(tag, { layers, force });
      await refreshAfterControlAxisChange();
    };
    try {
      setError(null);
      setBuilding(true);
      await send(!!hasDrawing);
    } catch (err) {
      if (err.conflict) {
        const blocked = [...new Set((err.conflict.blocked || []).map(b => b.glyph))].join(', ');
        const ok = window.confirm(
          `${blocked} now hold hand-drawn outlines.\n\n` +
          `${err.detail || 'Re-seeding would replace them.'}\n\nReplace them anyway?`
        );
        if (!ok) { setBuilding(false); return; }
        try {
          await send(true);
        } catch (e2) {
          setError(e2.message);
        }
      } else {
        setError(err.message);
      }
    } finally {
      setBuilding(false);
    }
  }, [refreshAfterControlAxisChange, setError]);

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
      // Both providers take (tag, glyphName, layerLocation, axes): the
      // browser's editor-bridge session needs the location and axes, the
      // live one only sends the glyph (the server has the sidecar).
      const data = await api.openControlAxisInEditor(tag, glyphName, layerLocation, axes);
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
      setFontraEditor({ url, directUrl, tag, glyphName, controlValue, axisName, editingOriginal: !!data.editing_original, bridge: data.bridge || null });
    } catch (err) {
      setError(err.message || `Failed to open Fontra for "${tag}"`);
    }
  }, [axes, setError]);

  // Close the Fontra modal and rebuild the font so the studio
  // preview reflects whatever the designer drew. Fontra writes to
  // the shadow on save; trigger_build picks up the new outlines.
  const handleCloseFontraEditor = useCallback(async () => {
    // Static demo: the bridge applied each edit to the sidecar as it
    // arrived (coalesced rebuilds); closing waits out the in-flight one
    // and reloads the preview font. The editor session is over either
    // way — dispose the bridge.
    const bridge = fontraEditor?.bridge || null;
    setFontraEditor(null);
    if (bridge) {
      bridge.dispose();
      try {
        await api.flushEditorRebuilds();
        setFontUrl(api.getFontUrl());
      } catch (err) {
        console.warn('Final rebuild after editor close failed:', err);
      }
      return;
    }
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
  }, [fontraEditor, setFontUrl]);

  const value = {
    glyphCoverageAxes,
    glyphChars,
    disabledControlAxes,
    handleToggleDisableControlAxis,
    handleCreateControlAxis,
    handleUpdateControlAxis,
    handleDeleteControlAxis,
    controlAxisAuthoringDisabledReason: null,
    handleSetControlAxisLayers,
    handleControlAxisLayerDelta,
    handleControlAxisReseed,
    handleOpenControlAxisInEditor,
    fontraEditor,
    handleCloseFontraEditor,
  };

  return (
    <ControlAxesContext.Provider value={value}>
      {children}
    </ControlAxesContext.Provider>
  );
}

export function useControlAxes() {
  const ctx = useContext(ControlAxesContext);
  if (!ctx) {
    throw new Error('useControlAxes() needs a <ControlAxesProvider> above it (App mounts one).');
  }
  return ctx;
}
