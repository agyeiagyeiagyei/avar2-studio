# 0002 — TTF preview; WOFF2 compressed in the background and cached per build

- **Status:** Accepted
- **Date:** decided 2026-10-11; shipped 2026-10-13 (commits `5196154`, `266469a`)
- **Area:** job queue, `/api/font`, export modal

## Context

The preview font travels over loopback, so transfer is free, but Brotli
compression of the Crispy working font costs a measured **13.2 s**
(8.72 MB TTF → 0.69 MB WOFF2). Putting that on the build path would add
13 s to every rebuild for nothing. The preview artifact also doubles as
the quick-install file (`/api/font?download=1`), and Font Book / Glyphs
do not take WOFF2. A web deliverable, on the other hand, wants WOFF2, and
the designer wants to know the size before downloading.

## Decision

- The served preview artifact stays **TTF**; `/api/font?download=1` stays
  TTF.
- After every successful build the supervisor queues a **lowest-priority
  `compress` job** (`JobQueue._maybe_compress` → `submit_compress`); it
  runs only when nothing else is pending and writes
  `.avar2-studio/build/<font>.woff2` plus a key file `<font>.woff2.json`
  `{ttf_size, ttf_mtime, woff2_bytes, source_bytes}` tying the cache to
  the exact build.
- `GET /api/font?format=woff2` serves the cache **only while it matches
  the current build** (409 otherwise, so the client falls back to TTF or
  to an export job). `GET /api/build/status` reports `sizes`: TTF exact,
  WOFF2 exact when the cache is current, else an **estimate from the last
  known ratio** (fallback 8 % of TTF), flagged `woff2_exact: false`.
- The export job takes `format: "ttf" | "woff2"`; a WOFF2 export compresses
  on demand inside the worker (`_save_woff2`, lossless — `font.flavor =
  "woff2"`).
- The frontend registers the preview face **from bytes**, not `url()`
  (Chromium's FontFace loader hangs on `/api/font`'s `no-cache, no-store`
  response), and prefers `?format=woff2` whenever the cache is fresh.

## Consequences

- The preview path never waits on compression; a WOFF2 download is
  instant once the background job has run (verified: `wOF2` magic,
  903 592 B on a Crispy copy).
- The modal's size labels can say "(est.)" for a few seconds after a
  rebuild — by design.
- Export polling keys on `status.last`, so a job finishing inside the
  poll window can be missed (audit summary #11; sprint item T2.5).

## Where it lives

- `build_jobs.JobQueue` — `submit_compress`, `_maybe_compress`,
  `export_sizes`, `_promote` (compress branch), `KEEP_EXPORTS`.
- `server.get_font` (`format=woff2`), `server._export_pipeline`,
  `server._save_woff2`.
- `frontend/src/fontLoad.js`, `frontend/src/api.js` (`getWoff2PreviewUrl`,
  `getWoff2DownloadUrl`, `exportFont`).
- `tests/test_build_jobs.py` (woff2 follow-up, woff2 export format),
  `tests/test_font_download.py`.
- History: archived `WORKER_BUILD_PLAN.md`, "Export formats".
