/**
 * API client for Glyphs Preview Server
 */

// Always hit the same origin the React bundle was served from — the
// Flask backend serves both the UI and the API on whatever --port the
// user picked. The old production branch hardcoded localhost:5001 and
// broke any non-default port. VITE_API_URL still wins so the
// dev-server + backend-on-5001 split workflow keeps working (the dev
// server also proxies /api — see vite.config.js).
// ``?.``: import.meta.env is undefined outside Vite (the node-only specs
// under e2e/ import this module); Vite still inlines the value.
const API_BASE = import.meta.env?.VITE_API_URL || '/api';

const EXPORT_TIMEOUT_MS = 20 * 60 * 1000;

// AbortSignal plumbing for the export poll: fetch rejects with a DOMException
// named AbortError when its signal fires; sleep() does the same so a Cancel
// mid-wait returns at once instead of after the poll interval.
const abortError = () => new DOMException('Export cancelled', 'AbortError');
const isAbort = (err) => !!err && err.name === 'AbortError';
const throwIfAborted = (signal) => { if (signal && signal.aborted) throw abortError(); };
const sleep = (ms, signal) => new Promise((resolve, reject) => {
  throwIfAborted(signal);
  const onAbort = () => { clearTimeout(timer); reject(abortError()); };
  const timer = setTimeout(() => {
    if (signal) signal.removeEventListener('abort', onAbort);
    resolve();
  }, ms);
  if (signal) signal.addEventListener('abort', onAbort, { once: true });
});

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
  // What this provider can do. The UI consults these instead of probing the
  // mode; the browser provider (static-api.js) swaps in its own answers, and
  // e2e/api-parity.spec.mjs keeps the two providers' method sets and these
  // keys in step.
  capabilities: {
    kinetikern: true,       // per-instance spacing engine (Kinetikern tab)
    woff2: true,            // WOFF2 preview/download + export sizes from the build queue
    cancel: true,           // cancel a running or queued build/export job
    masters: true,          // /api/masters (the brace-location pins list the masters)
    sourceWriteback: true,  // edits can be written back to the source file
    fontraEditor: true,     // open a brace layer in the outline editor
  },

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
      // Parse inside the try, THROW outside it. Throwing in the try was
      // caught by this same catch, which discarded the server's explanation
      // ("Instance 'X' already exists") and left a bare status code.
      let detail = null;
      try {
        detail = (await parseJSON(response)).error;
      } catch (e) { /* non-JSON body — fall through to the status */ }
      throw new Error(detail || `Build failed: ${response.status} ${response.statusText}`);
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

  getWoff2DownloadUrl() {
    // The background-compressed WOFF2 cache (409 while stale — callers check
    // the queue's export sizes for woff2_exact first).
    return `${API_BASE}/font?format=woff2&download=1`;
  },

  getWoff2PreviewUrl() {
    // Same cache, content-disposition inline, for preview FontFace loads.
    return `${API_BASE}/font?format=woff2`;
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
      // Parse inside the try, THROW outside it. Throwing in the try was
      // caught by this same catch, which discarded the server's explanation
      // ("Instance 'X' already exists") and left a bare status code.
      let detail = null;
      try {
        detail = (await parseJSON(response)).error;
      } catch (e) { /* non-JSON body — fall through to the status */ }
      throw new Error(detail || `Create failed: ${response.status} ${response.statusText}`);
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
      // Parse inside the try, THROW outside it. Throwing in the try was
      // caught by this same catch, which discarded the server's explanation
      // ("Instance 'X' already exists") and left a bare status code.
      let detail = null;
      try {
        detail = (await parseJSON(response)).error;
      } catch (e) { /* non-JSON body — fall through to the status */ }
      throw new Error(detail || `Update failed: ${response.status} ${response.statusText}`);
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
      // Parse inside the try, THROW outside it. Throwing in the try was
      // caught by this same catch, which discarded the server's explanation
      // ("Instance 'X' already exists") and left a bare status code.
      let detail = null;
      try {
        detail = (await parseJSON(response)).error;
      } catch (e) { /* non-JSON body — fall through to the status */ }
      throw new Error(detail || `Rename failed: ${response.status} ${response.statusText}`);
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
      // Parse inside the try, THROW outside it. Throwing in the try was
      // caught by this same catch, which discarded the server's explanation
      // ("Instance 'X' already exists") and left a bare status code.
      let detail = null;
      try {
        detail = (await parseJSON(response)).error;
      } catch (e) { /* non-JSON body — fall through to the status */ }
      throw new Error(detail || `Delete failed: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async addInstanceToSource(instanceName) {
    const response = await fetch(`${API_BASE}/instance/${encodeURIComponent(instanceName)}/add-to-source`, {
      method: 'POST',
    });
    if (!response.ok) {
      // Parse inside the try, THROW outside it. Throwing in the try was
      // caught by this same catch, which discarded the server's explanation
      // ("Instance 'X' already exists") and left a bare status code.
      let detail = null;
      try {
        detail = (await parseJSON(response)).error;
      } catch (e) { /* non-JSON body — fall through to the status */ }
      throw new Error(detail || `Add to source failed: ${response.status} ${response.statusText}`);
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

  async reorderInstances(names) {
    const response = await fetch(`${API_BASE}/instances/order`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }),
    });
    if (!response.ok) {
      const error = await parseJSON(response).catch(() => ({ error: `Failed to reorder: ${response.status}` }));
      throw new Error(error.error || `Failed to reorder: ${response.status} ${response.statusText}`);
    }
    return parseJSON(response);
  },

  async deleteAvar2Axis(axisName) {
    const response = await fetch(`${API_BASE}/avar2/axis/${encodeURIComponent(axisName)}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      const error = await parseJSON(response).catch(() => ({ error: `Failed to delete axis: ${response.status}` }));
      throw new Error(error.error || `Failed to delete axis: ${response.status} ${response.statusText}`);
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

  // Re-seed brace layers from the current source. `layers` omitted targets the
  // whole axis. A 409 means some layers hold hand-drawn outlines; the rejection
  // payload rides on the thrown Error as `.conflict` so the caller can list the
  // affected glyphs and offer to force, rather than reducing it to a string.
  async reseedControlAxisLayers(tag, { layers = null, force = false } = {}) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}/reseed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(layers ? { layers, force } : { force }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      const e = new Error(err.error || `Failed to re-seed layers: ${response.status}`);
      if (response.status === 409) e.conflict = err;
      e.detail = err.detail;
      throw e;
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

  // Same arguments as the browser provider, which needs the layer location
  // and the axes for its fontra-embed session; the server derives those from
  // the sidecar and only needs the glyph, which rides along so the injected
  // shim can show reference measurements for THIS layer without reading
  // Fontra's scene state.
  // eslint-disable-next-line no-unused-vars
  async openControlAxisInEditor(tag, glyphName, layerLocation = null, studioAxes = null) {
    const response = await fetch(`${API_BASE}/control-axes/${encodeURIComponent(tag)}/open-editor`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ glyph: glyphName || null }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Failed to open editor: ${response.status}`);
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

  async exportFont(options, { signal } = {}) {
    // POST /api/export-font — the export runs as a QUEUED JOB (build worker,
    // see docs/archive/WORKER_BUILD_PLAN.md): the POST returns 202 + a job id, we poll
    // THAT job's status until its artifact is ready, then download it.
    // Options: {hidden_axes: [tags], default_location: {tag: value} | null,
    //  format: "ttf" | "woff2"}. An AbortSignal stops the poll, cancels the
    // job server-side and rejects with an AbortError.
    throwIfAborted(signal);
    const response = await fetch(`${API_BASE}/export-font`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(options),
      signal,
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({}));
      throw new Error(err.error || `Export failed: ${response.status}`);
    }
    const { job_id } = await parseJSON(response);
    const cancelJob = () => this.cancelBuild(job_id).catch(() => {});
    // Exports are full builds when a default location is involved — budget
    // 20 minutes, then give up loudly (and drop the job: nobody will
    // download it).
    const deadline = Date.now() + EXPORT_TIMEOUT_MS;
    try {
      for (;;) {
        const status = await this.getBuildJob(job_id, { signal });
        if (status.state === 'finished') {
          if (status.cancelled) throw new Error('The export was cancelled before it finished.');
          if (!status.success) throw new Error(status.error || 'Export failed');
          break;
        }
        if (Date.now() > deadline) {
          await cancelJob();
          throw new Error(`Export timed out after ${EXPORT_TIMEOUT_MS / 60000} minutes — the build worker is still busy. Try again once the studio is idle.`);
        }
        await sleep(1000, signal);
      }
      const dl = await fetch(`${API_BASE}/export/${job_id}/download`, { signal });
      if (!dl.ok) {
        const err = await parseJSON(dl).catch(() => ({}));
        throw new Error(err.error || `Export download failed: ${dl.status}`);
      }
      return await dl.blob();
    } catch (err) {
      if (isAbort(err)) {
        await cancelJob();
        throw abortError();
      }
      throw err;
    }
  },

  async getBuildStatus() {
    const response = await fetch(`${API_BASE}/build/status`);
    if (!response.ok) {
      throw new Error(`Build status failed: ${response.status}`);
    }
    return parseJSON(response);
  },

  async getBuildJob(jobId, { signal } = {}) {
    // One job's state by id: {state: queued|running|finished, success,
    // cancelled, error, artifact_ready}. 404 = the studio no longer knows
    // the job (restarted, or long since evicted).
    const response = await fetch(`${API_BASE}/build/${encodeURIComponent(jobId)}`, { signal });
    if (response.status === 404) {
      throw new Error('The studio no longer knows this export job — was it restarted? Try the export again.');
    }
    if (!response.ok) {
      throw new Error(`Build job status failed: ${response.status}`);
    }
    return parseJSON(response);
  },

  async cancelBuild(jobId) {
    const response = await fetch(`${API_BASE}/build/${encodeURIComponent(jobId)}`, {
      method: 'DELETE',
    });
    return parseJSON(response);
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

  // --- Kinetikern: per-instance spacing (sidecar + engine proposals) ---
  async getKinetikern() {
    const response = await fetch(`${API_BASE}/kinetikern`);
    if (!response.ok) {
      throw new Error(`Failed to fetch Kinetikern state: ${response.status}`);
    }
    return parseJSON(response);
  },

  async proposeKinetikern({ instance, coordinates, looseness }) {
    const params = new URLSearchParams();
    if (instance) params.set('instance', instance);
    if (coordinates) params.set('coordinates', JSON.stringify(coordinates));
    params.set('looseness', String(looseness ?? 0));
    const response = await fetch(`${API_BASE}/kinetikern/propose?${params}`);
    if (!response.ok) {
      // A server started before Kinetikern existed answers 404, or 503 from
      // the Fontra fallthrough; say what that means instead of echoing it.
      if (response.status === 404 || response.status === 503) {
        throw new Error('This studio server does not have Kinetikern yet — restart the studio to load it.');
      }
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Kinetikern proposal failed: ${response.status}`);
    }
    return parseJSON(response);
  },

  async applyKinetikern(name, looseness, location) {
    const response = await fetch(`${API_BASE}/kinetikern/instances/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(location ? { looseness, location } : { looseness }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Kinetikern apply failed: ${response.status}`);
    }
    return parseJSON(response);
  },

  async revertKinetikern(name) {
    const response = await fetch(`${API_BASE}/kinetikern/instances/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Kinetikern revert failed: ${response.status}`);
    }
    return parseJSON(response);
  },

  async applyKinetikernAll(looseness) {
    const response = await fetch(`${API_BASE}/kinetikern/all`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ looseness }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Kinetikern apply-all failed: ${response.status}`);
    }
    return parseJSON(response);
  },

  // The family switch (K1). Off builds without Kinetikern but keeps the
  // entries, so this is not a destructive "clear".
  async setKinetikernEnabled(enabled) {
    const response = await fetch(`${API_BASE}/kinetikern`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !!enabled }),
    });
    if (!response.ok) {
      const err = await parseJSON(response).catch(() => ({ error: `Failed: ${response.status}` }));
      throw new Error(err.error || `Kinetikern toggle failed: ${response.status}`);
    }
    return parseJSON(response);
  },
};
