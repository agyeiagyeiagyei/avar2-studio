/**
 * API client for Glyphs Preview Server
 */

// Always hit the same origin the React bundle was served from — the
// Flask backend serves both the UI and the API on whatever --port the
// user picked. The old production branch hardcoded localhost:5001 and
// broke any non-default port. VITE_API_URL still wins so the
// dev-server + backend-on-5001 split workflow keeps working (the dev
// server also proxies /api — see vite.config.js).
const API_BASE = import.meta.env.VITE_API_URL || '/api';

// Helper to parse JSON response with error handling
async function parseJSON(response) {
  const text = await response.text();
  if (!text) {
    throw new Error(`Empty response from server (status: ${response.status})`);
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error(`Invalid JSON response: ${text.substring(0, 100)}`);
  }
}

export const api = {
  async health() {
    const response = await fetch(`${API_BASE}/health`);
    if (!response.ok) {
      throw new Error(`Health check failed: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async glyphsFileStatus() {
    const response = await fetch(`${API_BASE}/glyphs-file-status`);
    if (!response.ok) {
      throw new Error(`Failed to check Glyphs file status: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async getInstances() {
    const response = await fetch(`${API_BASE}/instances`);
    if (!response.ok) {
      throw new Error(`Failed to fetch instances: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async getMasters() {
    const response = await fetch(`${API_BASE}/masters`);
    if (!response.ok) {
      throw new Error(`Failed to fetch masters: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async getAxes() {
    const response = await fetch(`${API_BASE}/axes`);
    if (!response.ok) {
      throw new Error(`Failed to fetch axes: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async buildFont() {
    const response = await fetch(`${API_BASE}/build`, {
      method: 'POST',
    });
    if (!response.ok) {
      try {
        const error = await parseJSON(response);
        throw new Error(error.error || 'Build failed');
      } catch (e) {
        throw new Error(`Build failed: ${response.status} ${response.statusText}`);
      }
    }
    return parseJSON(response);
  },

  getFontUrl() {
    // Add cache busting timestamp to force reload when font is rebuilt
    const timestamp = Date.now();
    return `${API_BASE}/font?t=${timestamp}`;
  },

  getAvar2FontUrl() {
    // Add cache busting timestamp to force reload when font is rebuilt
    const timestamp = Date.now();
    return `${API_BASE}/avar2-font?t=${timestamp}`;
  },

  async checkSyncStatus() {
    const response = await fetch(`${API_BASE}/check-sync-status`);
    if (!response.ok) {
      throw new Error('Failed to check sync status');
    }
    return parseJSON(response);
  },

  async buildAvar2Font(traditionalAxes, avar2Axes) {
    const response = await fetch(`${API_BASE}/build-avar2`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        traditional_axes: traditionalAxes,
        avar2_axes: avar2Axes,
      }),
    });
    if (!response.ok) {
      const error = await parseJSON(response);
      throw new Error(error.error || 'Build failed');
    }
    return parseJSON(response);
  },

  async createInstance(instanceName, coordinates, insertAfter = null) {
    const body = { name: instanceName, coordinates };
    if (insertAfter) {
      body.insert_after = insertAfter;
    }
    const response = await fetch(`${API_BASE}/instance`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      try {
        const error = await parseJSON(response);
        throw new Error(error.error || 'Create failed');
      } catch (e) {
        throw new Error(`Create failed: ${response.status} ${response.statusText}`);
      }
    }
    return parseJSON(response);
  },

  async updateInstance(instanceName, coordinates, options = {}) {
    // ``options.csvOnly`` adds ``?csv_only=true`` so the server skips
    // the source-file writeback. The flyout uses this for the "Update
    // in avar2-studio" action: tweak the CSV row (and the avar2 mapping
    // it represents) without touching .glyphs / .designspace.
    const qs = options.csvOnly ? '?csv_only=true' : '';
    const response = await fetch(`${API_BASE}/instance/${encodeURIComponent(instanceName)}${qs}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ coordinates }),
    });
    if (!response.ok) {
      try {
        const error = await parseJSON(response);
        throw new Error(error.error || 'Update failed');
      } catch (e) {
        throw new Error(`Update failed: ${response.status} ${response.statusText}`);
      }
    }
    return parseJSON(response);
  },

  async renameInstance(instanceName, newName) {
    const response = await fetch(`${API_BASE}/instance/${encodeURIComponent(instanceName)}/rename`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ new_name: newName }),
    });
    if (!response.ok) {
      try {
        const error = await parseJSON(response);
        throw new Error(error.error || 'Rename failed');
      } catch (e) {
        throw new Error(`Rename failed: ${response.status} ${response.statusText}`);
      }
    }
    return parseJSON(response);
  },

  async deleteInstance(instanceName, options = {}) {
    // Three modes:
    //   - {csvOnly: true}    → CSV-only delete; source declaration kept
    //   - {sourceOnly: true} → source-only delete; CSV row kept (DEMOTE)
    //   - default            → both source + CSV
    let qs = '';
    if (options.csvOnly) qs = '?csv_only=true';
    else if (options.sourceOnly) qs = '?source_only=true';
    const response = await fetch(`${API_BASE}/instance/${encodeURIComponent(instanceName)}${qs}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      try {
        const error = await parseJSON(response);
        throw new Error(error.error || 'Delete failed');
      } catch (e) {
        throw new Error(`Delete failed: ${response.status} ${response.statusText}`);
      }
    }
    return parseJSON(response);
  },

  async addInstanceToSource(instanceName) {
    const response = await fetch(`${API_BASE}/instance/${encodeURIComponent(instanceName)}/add-to-source`, {
      method: 'POST',
    });
    if (!response.ok) {
      try {
        const error = await parseJSON(response);
        throw new Error(error.error || 'Add to source failed');
      } catch (e) {
        throw new Error(`Add to source failed: ${response.status} ${response.statusText}`);
      }
    }
    return parseJSON(response);
  },

  async getAvar2Instances() {
    const response = await fetch(`${API_BASE}/avar2/instances`);
    if (!response.ok) {
      throw new Error(`Failed to fetch avar2 instances: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async getAvar2Axes() {
    const response = await fetch(`${API_BASE}/avar2/axes`);
    if (!response.ok) {
      throw new Error(`Failed to fetch avar2 axes: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async addAvar2Axis(axisData) {
    const response = await fetch(`${API_BASE}/avar2/axis`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(axisData),
    });
    if (!response.ok) {
      const error = await parseJSON(response).catch(() => ({ error: `Failed to add axis: ${response.status}` }));
      throw new Error(error.error || `Failed to add axis: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async updateAvar2Axis(axisName, axisData) {
    const response = await fetch(`${API_BASE}/avar2/axis/${encodeURIComponent(axisName)}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(axisData),
    });
    if (!response.ok) {
      const error = await parseJSON(response).catch(() => ({ error: `Failed to update axis: ${response.status}` }));
      throw new Error(error.error || `Failed to update axis: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async updateAvar2Mapping(instanceName, axisName, value) {
    const response = await fetch(`${API_BASE}/avar2/mapping/${encodeURIComponent(instanceName)}/${encodeURIComponent(axisName)}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ value }),
    });
    if (!response.ok) {
      const error = await parseJSON(response).catch(() => ({ error: `Failed to update mapping: ${response.status}` }));
      throw new Error(error.error || `Failed to update mapping: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  // SPAC now ships as a post-build transform — see getTransforms /
  // updateTransforms above. Enabling it injects a SPAC axis into the built
  // font, which then surfaces as a normal parametric slider.

  async registerEditingInstance(instanceName) {
    const response = await fetch(`${API_BASE}/instance/${encodeURIComponent(instanceName)}/editing`, {
      method: 'POST',
    });
    if (!response.ok) {
      // Silently fail - editing registration is best effort
      return;
    }
    return parseJSON(response);
  },

  async unregisterEditingInstance(instanceName) {
    const response = await fetch(`${API_BASE}/instance/${encodeURIComponent(instanceName)}/editing`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      // Silently fail - editing registration is best effort
      return;
    }
    return parseJSON(response);
  },

  async getGlyphCoverage() {
    const response = await fetch(`${API_BASE}/glyph-coverage`);
    if (!response.ok) {
      throw new Error(`Failed to fetch glyph coverage: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async listControlAxes() {
    const response = await fetch(`${API_BASE}/control-axes`);
    if (!response.ok) {
      throw new Error(`Failed to list secondary parametric axes: ${response.status}`);
    }
    return parseJSON(response);
  },

  async getTransforms() {
    const response = await fetch(`${API_BASE}/transforms`);
    if (!response.ok) {
      throw new Error(`Failed to list transforms: ${response.status}`);
    }
    return parseJSON(response);
  },

  async updateTransforms(entries) {
    const response = await fetch(`${API_BASE}/transforms`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transforms: entries }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to update transforms: ${response.status}`);
    }
    return parseJSON(response);
  },

  // ---- Grade transform (source-level; toggle + per-instance grade%) ----
  async getGrade() {
    const response = await fetch(`${API_BASE}/transforms/grade`);
    if (!response.ok) {
      throw new Error(`Failed to get grade: ${response.status}`);
    }
    return parseJSON(response);
  },

  async setGrade(patch) {
    const response = await fetch(`${API_BASE}/transforms/grade`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to update grade: ${response.status}`);
    }
    return parseJSON(response);
  },

  async setInstanceGrade(instanceName, pct) {
    const response = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceName)}/grade`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pct === undefined || pct === null ? {} : { pct }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to set instance grade: ${response.status}`);
    }
    return parseJSON(response);
  },

  async removeInstanceGrade(instanceName) {
    const response = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceName)}/grade`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error(`Failed to remove instance grade: ${response.status}`);
    }
    return parseJSON(response);
  },

  async createControlAxis(axis) {
    const response = await fetch(`${API_BASE}/control-axes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(axis),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to create secondary parametric axis: ${response.status}`);
    }
    return parseJSON(response);
  },

  async updateControlAxis(tag, patch) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to update secondary parametric axis: ${response.status}`);
    }
    return parseJSON(response);
  },

  async deleteControlAxis(tag) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to delete secondary parametric axis: ${response.status}`);
    }
    return parseJSON(response);
  },

  /**
   * Add and/or remove brace layers without sending the whole list. Preferred
   * for interactive edits: the server merges against the on-disk list, so a
   * save made while our cached copy is stale composes instead of wiping the
   * layers we hadn't loaded yet.
   */
  async controlAxisLayerDelta(tag, { add = [], remove = [] } = {}) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}/layers/delta`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ add, remove }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to update layers: ${response.status}`);
    }
    return parseJSON(response);
  },

  async setControlAxisLayers(tag, layers) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}/layers`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ layers }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to set layers: ${response.status}`);
    }
    return parseJSON(response);
  },

  async openControlAxisInEditor(tag) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}/open-editor`, {
      method: 'POST',
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to open editor: ${response.status}`);
    }
    return parseJSON(response);
  },

  // A static cut of the current build at one brace layer's location with the
  // secondary axis OFF — drop it into Fontra's Reference Font panel to draw
  // the correction directly on top of the shape you started from.
  controlAxisReferenceFontUrl(tag, glyph, index = 0) {
    return `${API_BASE}/control-axes/${encodeURIComponent(tag)}/reference-font`
      + `?glyph=${encodeURIComponent(glyph)}&index=${index}`;
  },

  async listExamples() {
    const response = await fetch(`${API_BASE}/examples`);
    if (!response.ok) {
      throw new Error(`Failed to list examples: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async loadExample(exampleId) {
    const response = await fetch(`${API_BASE}/load-source`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ example: exampleId }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to load example: ${response.status}`);
    }
    return parseJSON(response);
  },

  async uploadSource(files) {
    // ``files`` is a FileList or array. The backend picks the .glyphs
    // file as the source and recognises a sibling ``-avar.csv`` and/or
    // ``avar2-axis-metadata.json`` if the user selected them in the
    // same picker. Anything else is reported back in ``ignored_files``.
    const formData = new FormData();
    Array.from(files).forEach((f, i) => formData.append(`file_${i}`, f));
    const response = await fetch(`${API_BASE}/load-source`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to upload: ${response.status}`);
    }
    return parseJSON(response);
  },

  async exportFont(options) {
    // POST /api/export-font — returns the font binary. Options:
    // {hidden_axes: [tags], default_location: {tag: value} | null}.
    const response = await fetch(`${API_BASE}/export-font`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(options),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({}));
      throw new Error(err.error || `Export failed: ${response.status}`);
    }
    return response.blob();
  },

  async getMappedLocation(coordinates) {
    // Evaluate the built font's avar table at a user-space location —
    // returns { mapped: {tag: value} }, the effective post-mapping
    // value of every fvar axis. Drives the preview's "parametric
    // sliders follow the avar2 mapping" reflection.
    const params = new URLSearchParams({ coordinates: JSON.stringify(coordinates) });
    const response = await fetch(`${API_BASE}/mapped-location?${params}`);
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({}));
      throw new Error(err.error || `Failed to map location: ${response.status}`);
    }
    return parseJSON(response);
  },

  async getTextWidth(text, coordinates, fontSizeRem = 2.0) {
    const params = new URLSearchParams({
      text: text,
      coordinates: JSON.stringify(coordinates),
      font_size_rem: fontSizeRem.toString(),
    });
    const response = await fetch(`${API_BASE}/text-width?${params}`);
    if (!response.ok) {
      try {
        const error = await parseJSON(response);
        throw new Error(error.error || `Failed to measure text width: ${response.status} ${response.statusText}`);
      } catch (e) {
        if (e instanceof Error && e.message.includes('error')) {
          throw e;
        }
        throw new Error(`Failed to measure text width: ${response.status} ${response.statusText}`);
      }
    }
    return parseJSON(response);
  },

  exportConfigUrl() {
    // URL builder only, no fetch — the Header uses this as an anchor
    // href and lets the server's Content-Disposition header turn it
    // into a download (same trick as getFontUrl).
    return `${API_BASE}/config/export`;
  },

  async importConfig(bundle, dryRun) {
    const response = await fetch(`${API_BASE}/config/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bundle, dry_run: dryRun }),
    });
    if (!response.ok) {
      // A 400 carries one of two shapes: a plain {error} (e.g. no source
      // loaded) or a validation report ({ok, errors, warnings, summary})
      // when the bundle itself was rejected. Attach the report to the
      // thrown error so the import modal can swap it in and show exactly
      // why the apply failed.
      const body = await parseJSON(response).catch(() => null);
      const message = (body && body.error)
        || (body && body.errors && body.errors[0])
        || `Failed to import configuration: ${response.status}`;
      const err = new Error(message);
      if (body && body.errors) err.report = body;
      throw err;
    }
    return parseJSON(response);
  },
};
