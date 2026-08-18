/**
 * Zip workspace format — project zips in and out of the static tool.
 *
 * Mirrors the full app's `_load_project_zip` contract
 * (src/avar2_studio/server.py): exactly one source (a .glyphs, or a
 * .designspace with its UFOs) anywhere in the archive; the studio
 * sidecars sit next to it — `<stem>-avar.csv`, `<stem>-control.json`,
 * `<stem>-transforms.json`, `*-axis-metadata.json` — plus
 * `.avar2-studio/axis-metadata.json`. `__MACOSX`/`._*` entries and
 * absolute/parent paths are rejected, the rest of `.avar2-studio/`
 * (build/shadow/archive) is tool-managed and never imported.
 *
 * Designspace caveat: fontc compiles UFO sources from the filesystem
 * only, so the browser can't compile a .designspace — those projects
 * load from a baked preview TTF inside the zip (`.avar2-studio/build/
 * <stem>-VF.ttf`, else any .ttf next to the source). Everything after
 * the initial compile runs off font bytes, so nothing else is lost.
 */

import { unzipSync, zipSync, strToU8, strFromU8 } from 'fflate';
import * as mappingsCsv from './mappings-csv';

const baseName = (path) => path.split('/').pop();
const dirOf = (path) => {
  const i = path.lastIndexOf('/');
  return i === -1 ? '' : path.slice(0, i + 1); // '' or 'dir/' (trailing slash)
};
const stemOf = (name) => name.replace(/\.[^.]+$/, '');
const isJunk = (path) =>
  path.split('/').some(part => part === '__MACOSX' || part.startsWith('._'));
const inStudioDir = (path) => path.split('/').includes('.avar2-studio');

/**
 * Read a project zip. Returns {
 *   sourcePath, sourceName, sourceExt ('glyphs'|'designspace'), stem,
 *   sourceDir, sourceText (glyphs only), entries (raw archive map),
 *   csvText, metadataText, controlText, transformsText, previewTtf
 * }.
 */
export function readWorkspaceZip(u8) {
  let entries;
  try {
    entries = unzipSync(u8);
  } catch {
    throw new Error('Not a valid .zip archive');
  }
  const names = Object.keys(entries);
  for (const n of names) {
    if (n.startsWith('/') || n.split('/').includes('..')) {
      throw new Error(`Unsafe path in archive: ${n}`);
    }
  }
  const files = names.filter(n => !n.endsWith('/') && !isJunk(n) && !inStudioDir(n));
  const studioMetaPath = names.find(n =>
    !n.endsWith('/') && !isJunk(n) && /(^|\/)\.avar2-studio\/axis-metadata\.json$/.test(n));

  const ds = files.filter(n => n.toLowerCase().endsWith('.designspace'));
  const gl = files.filter(n => n.toLowerCase().endsWith('.glyphs'));
  let sourcePath;
  if (ds.length === 1) {
    sourcePath = ds[0];
  } else if (ds.length === 0 && gl.length === 1) {
    sourcePath = gl[0];
  } else {
    throw new Error(
      `The archive must contain exactly one project (one .designspace or ` +
      `one .glyphs) — found ${ds.length} .designspace and ${gl.length} .glyphs.`
    );
  }

  const sourceName = baseName(sourcePath);
  const sourceExt = sourceName.slice(sourceName.lastIndexOf('.') + 1).toLowerCase();
  const stem = stemOf(sourceName);
  const sourceDir = dirOf(sourcePath);
  const text = (n) => (n ? strFromU8(entries[n]) : null);
  const beside = (suffix) =>
    files.find(n => dirOf(n) === sourceDir && baseName(n).toLowerCase().endsWith(suffix)) || null;

  let previewTtf = null;
  if (sourceExt === 'designspace') {
    // A designspace must travel with its UFOs (same check the server
    // runs) — the export carries them verbatim, so a zip we emit always
    // passes; zips from elsewhere get the server's error.
    const xml = strFromU8(entries[sourcePath]);
    const doc = new DOMParser().parseFromString(xml, 'text/xml');
    if (doc.querySelector('parsererror')) {
      throw new Error(`${sourceName} is not a valid .designspace document`);
    }
    const missing = [...doc.querySelectorAll('source')]
      .map(s => s.getAttribute('filename'))
      .filter(Boolean)
      .filter(f => !names.some(n => n === sourceDir + f || n.startsWith(`${sourceDir + f}/`)));
    if (missing.length) {
      throw new Error(
        `The archive's .designspace references sources that aren't in it: ` +
        `${missing.slice(0, 4).join(', ')}. Zip the whole project folder so the UFOs travel too.`
      );
    }
    const buildTtfPath = names.find(n =>
      !n.endsWith('/') && !isJunk(n) && /(^|\/)\.avar2-studio\/build\/[^/]+\.ttf$/i.test(n));
    const siblingTtfPath = files.find(n =>
      dirOf(n) === sourceDir && n.toLowerCase().endsWith('.ttf'));
    const ttfPath = buildTtfPath || siblingTtfPath;
    if (!ttfPath) {
      throw new Error(
        "This zip's .designspace can't be compiled in the browser — include the " +
        'built variable font (.avar2-studio/build/<name>-VF.ttf or a .ttf next ' +
        'to the source), or open the zip in the full app.'
      );
    }
    previewTtf = entries[ttfPath];
  }

  return {
    sourcePath,
    sourceName,
    sourceExt,
    stem,
    sourceDir,
    sourceText: sourceExt === 'glyphs' ? strFromU8(entries[sourcePath]) : null,
    entries,
    csvText: text(beside('-avar.csv')),
    metadataText: text(beside('-axis-metadata.json')) || text(studioMetaPath),
    controlText: text(beside('-control.json')),
    transformsText: text(beside('-transforms.json')),
    gradeText: text(beside('-grade.json')),
    cornerPinsText: text(beside('-cornerpins.json')),
    previewTtf,
  };
}

/**
 * Write the project zip for the current dataset: sources verbatim,
 * studio files re-emitted from live state (the CSV is the authoring
 * source of truth), and the current font bytes as the preview build.
 * Sidecars land next to the source, where the full app looks for them.
 */
export function buildWorkspaceZip(dataset) {
  const dir = dataset.sourceDir || '';
  const stem = dataset.stem;
  const out = {};

  const isStudioFile = (path) => {
    const base = baseName(path).toLowerCase();
    return inStudioDir(path)
      || base.endsWith('-avar.csv')
      || base.endsWith('-control.json')
      || base.endsWith('-transforms.json')
      || base.endsWith('-grade.json')
      || base.endsWith('-cornerpins.json')
      || base.endsWith('-axis-metadata.json');
  };

  if (dataset.workspaceEntries) {
    // Zip-loaded project: every non-studio entry rides along verbatim
    // (the source itself is never edited in the static tool).
    for (const [path, data] of Object.entries(dataset.workspaceEntries)) {
      if (path.endsWith('/') || isStudioFile(path)) continue;
      out[path] = data;
    }
  } else if (dataset.sourceText != null) {
    out[`${stem}.glyphs`] = strToU8(dataset.sourceText);
  }

  out[`${dir}${stem}-avar.csv`] = strToU8(mappingsCsv.serializeMappingsCsv(dataset.instancesCsv));
  if (dataset.controlAxes?.length) {
    out[`${dir}${stem}-control.json`] = strToU8(
      JSON.stringify({ version: 1, axes: dataset.controlAxes }, null, 2));
  }
  if (dataset.transforms?.length) {
    const transforms = dataset.transforms.map(t => ({
      type: t.type || t.id,
      enabled: !!t.enabled,
      params: t.params || {},
    }));
    out[`${dir}${stem}-transforms.json`] = strToU8(
      JSON.stringify({ version: 1, transforms }, null, 2));
  }
  // Grade sidecar (server convention: <basename>-grade.json). Written
  // whenever any state exists — grades persist even with the toggle off.
  if (dataset.grade && (dataset.grade.enabled || dataset.grade.instances?.length)) {
    const { enabled = false, default_pct = 0.25, instances = [] } = dataset.grade;
    out[`${dir}${stem}-grade.json`] = strToU8(
      JSON.stringify({ version: 1, enabled: !!enabled, default_pct, instances }, null, 2));
  }
  if (Object.keys(dataset.axisRanges || {}).length) {
    out[`${dir}.avar2-studio/axis-metadata.json`] = strToU8(
      JSON.stringify(dataset.axisRanges, null, 2));
  }
  if (dataset.cornerPins?.length) {
    out[`${dir}${stem}-cornerpins.json`] = strToU8(
      JSON.stringify({ version: 1, pins: dataset.cornerPins }, null, 2));
  }
  out[`${dir}.avar2-studio/build/${stem}-VF.ttf`] = dataset.fontBytes;
  return zipSync(out);
}
