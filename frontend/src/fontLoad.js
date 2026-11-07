/**
 * Register a preview font into document.fonts from BYTES, not url().
 *
 * Chromium's FontFace url() loader hangs forever on /api/font's
 * `no-cache, no-store` response (the font loader can't hand a non-storable
 * response between processes) — the preview silently fell back to the
 * system font. Fetching the bytes first is immune to that and gives us
 * real error handling. When a fresh WOFF2 cache exists (the build queue
 * compresses after every build), prefer it: ~12x less data to move and
 * parse on every rebuild.
 */

async function fetchFontBuffer(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`font fetch failed (${resp.status})`);
  return resp.arrayBuffer();
}

export async function registerPreviewFont(family, ttfUrl, woff2Url = null) {
  // Replace any stale registration of the same family first.
  const old = Array.from(document.fonts).find(f => f.family === family);
  if (old) document.fonts.delete(old);

  let buffer = null;
  if (woff2Url) {
    try {
      buffer = await fetchFontBuffer(woff2Url); // 409 while the cache is stale
    } catch { /* fall through to the TTF */ }
  }
  if (!buffer) buffer = await fetchFontBuffer(ttfUrl);

  const face = new FontFace(family, buffer);
  await face.load();
  document.fonts.add(face);
  await document.fonts.ready;
  return face;
}
