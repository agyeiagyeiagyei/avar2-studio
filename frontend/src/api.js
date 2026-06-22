/**
 * API client for Glyphs Preview Server
 */

// Always hit the same origin the React bundle was served from — the
// Flask backend serves both the UI and the API on whatever --port the
// user picked. The old production branch hardcoded localhost:5001 and
// broke any non-default port. REACT_APP_API_URL still wins so the
// dev-server-on-3000 + backend-on-5001 split workflow keeps working.
const API_BASE = process.env.REACT_APP_API_URL || '/api';

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

  getPreviewFontUrl() {
    // Add cache busting timestamp to force reload when font is rebuilt
    const timestamp = Date.now();
    return `${API_BASE}/preview-font?t=${timestamp}`;
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

  // SPAC API methods (checkSpacAxis, getSpacValues, initSpacAxis,
  // updateSpacValue, rebuildPreviewFont) were removed when SPAC support
  // was deferred. The dormant backend endpoints under /api/spacing/*
  // can be re-exposed here when the axis lands.

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
      throw new Error(`Failed to list control axes: ${response.status}`);
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
      throw new Error(err.error || `Failed to create control axis: ${response.status}`);
    }
    return parseJSON(response);
  },

  async deleteControlAxis(tag) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to delete control axis: ${response.status}`);
    }
    return parseJSON(response);
  },

  async setControlAxisCoverage(tag, coverage) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}/coverage`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ coverage }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to set coverage: ${response.status}`);
    }
    return parseJSON(response);
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
};
