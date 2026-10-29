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
export const SPACING_WARNING = 'Spacing is not included. The SPAC axis and Kinetikern corrections are applied to the built font, not to the sources; a font built from these sources has the source\'s own spacing.';

export const SOURCES_COPY =
  'Sources for fontmake and gftools: the masters as UFOs and a designspace 5 ' +
  'file with the avar2 mappings, the declared axis defaults and the named ' +
  'instances.';

export const SOURCES_DEFAULT_REASON =
  'A designspace default must be a master location; the sources rest at the ' +
  'compiled origin.';

/** The chips, in display order. `estimate` false ⇒ no size is shown. */
export function exportFormats({ woff2 = true } = {}) {
  return [
    { id: 'ttf', label: 'TTF', button: 'Download TTF', estimate: true },
    ...(woff2 ? [{ id: 'woff2', label: 'WOFF2', button: 'Download WOFF2', estimate: true }] : []),
    {
      id: 'sources',
      label: 'UFO + designspace (zip)',
      button: 'Download sources',
      estimate: false,
      copy: SOURCES_COPY,
      warning: SPACING_WARNING,
      defaultDisabledReason: SOURCES_DEFAULT_REASON,
    },
  ];
}

/** The button label for a format id (falls back to the id). */
export function buttonLabel(id) {
  const f = exportFormats().find(x => x.id === id);
  return f ? f.button : id;
}
