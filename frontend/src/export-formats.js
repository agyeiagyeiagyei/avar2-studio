/**
 * The export dialog's three formats, in one place (E1).
 *
 * The sources format is the Google Fonts team's ask: masters as UFOs plus a
 * designspace 5 with the mappings, so they can build with fontmake/gftools.
 * Its spacing warning is ALWAYS visible, not a tooltip — a font built from the
 * sources does not have the studio's spacing, and that must not be a surprise
 * discovered after the build.
 */

// Verbatim, and pinned against the backend constant by the Python tests (one
// line on purpose: the test greps for the sentence, so keep it unbroken).
export const RESTING_NOTE = 'The sources rest at the compiled origin; the studio\'s font relocates its default to the ORIGIN row, so the default row differs there.';

export const SOURCES_COPY =
  'Sources for fontmake and gftools: the masters as UFOs and a designspace 5 ' +
  'file with the avar2 mappings, the declared axis defaults and the named ' +
  'instances.';

export const SOURCES_DEFAULT_REASON =
  'A designspace default must be a master location; the sources rest at the ' +
  'compiled origin.';

/**
 * E2l: why a TTF/WOFF2 export is larger, in the words the designer needs.
 *
 * Shown whenever the font's resting location is not the source's DEFAULT
 * master, because the studio then rebuilds the variations around a style that
 * isn't the default — and on a twin-heavy source the preview's spacing is
 * approximate between masters (measured on Crispy: up to ~397 u at some
 * styles). The exported fonts are exact, and that is the point of the note.
 *
 * It does NOT advise moving the default to the resting style: a mid-range
 * default master has no neighbours along the axis lines through it, so the
 * design goes flat there — measured at up to 3,205 u on Crispy (verifier,
 * 2026-11-06 15:49). `null` means "say nothing": the default master, or a
 * source we could not read.
 */
export function sizeWarningCopy(report) {
  if (!report) return null;
  if (report.status !== 'other_master' && report.status !== 'no_master') return null;
  const where = report.status === 'other_master'
    ? ` It rests on the master “${report.name}”, not on the source's default master “${report.default_master}”.`
    : (report.name
      ? ` It rests at a location that is not a master (the nearest is “${report.name}”).`
      : ' It rests at a location that is not a master.');
  return 'This file is larger than one built at the default master: the studio ' +
    'rebuilds the variations around the style the font rests at. The ' +
    "preview's spacing is approximate between masters, so it can differ a lot " +
    `at some styles; the exported fonts are exact.${where}`;
}

/**
 * Which location the size warning is about, as the backend wants it.
 *
 * "Set default at" ON ⇒ the preview's own location (the export rebuilds so its
 * resting state IS this style). OFF ⇒ `null`, i.e. the ORIGIN row, which the
 * backend resolves from the mappings file. Kept here so the decision is
 * testable without a browser.
 */
export function restingQuery(exportUseDefault, location) {
  return exportUseDefault ? (location || null) : null;
}

/**
 * The export request's default location. Sources never take one (the box is
 * disabled and shown unticked there), so a "Set default at" left ticked from a
 * TTF/WOFF2 export must not ride along: the backend refuses that combination.
 */
export function exportDefaultLocation(format, exportUseDefault, location) {
  return format !== 'sources' && exportUseDefault ? (location || null) : null;
}

/**
 * The downloaded file's name. The sources zip is named after the family, as the
 * backend and the CLI name it (`<Family>-sources.zip`); the fonts keep the
 * built font's name.
 */
export function exportFilename(format, fontFilename, familyName) {
  if (format === 'sources') return `${(familyName || 'font').replace(/ /g, '')}-sources.zip`;
  return fontFilename.replace(/\.ttf$/i, format === 'woff2' ? '.woff2' : '.ttf');
}

/** The chips, in display order. `estimate` false ⇒ no size is shown. */
export function exportFormats({ woff2 = true, sources = true } = {}) {
  return [
    { id: 'ttf', label: 'TTF', button: 'Download TTF', estimate: true },
    ...(woff2 ? [{ id: 'woff2', label: 'WOFF2', button: 'Download WOFF2', estimate: true }] : []),
    ...(sources ? [{
      id: 'sources',
      label: 'UFO + designspace (zip)',
      button: 'Download sources',
      estimate: false,
      copy: SOURCES_COPY,
      warning: RESTING_NOTE,
      defaultDisabledReason: SOURCES_DEFAULT_REASON,
    }] : []),
  ];
}

/** The button label for a format id (falls back to the id). */
export function buttonLabel(id) {
  const f = exportFormats().find(x => x.id === id);
  return f ? f.button : id;
}
