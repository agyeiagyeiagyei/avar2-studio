> **Archived 2026-10-16.** The capture brief for the README's screenshot set. Four of its shots were never taken and the README no longer references them (see sprint item T1.37); the Space tab shot (S08) is obsolete — the tab was removed. Kept for whoever retakes the set.

# README screenshots — capture brief

The README's images are one consistent set, captured on the hosted demo
(`https://agyeiagyeiagyei.github.io/avar2-studio/`) with the Crispy
project loaded and letters-only specimen text. This file is the brief
for whoever (or whatever agent) retakes them. Output goes to
`docs/images/` under the names below; nothing here commits.

Conventions for every shot: Chromium via Playwright (`playwright-core`
plus the system Chrome channel, as in `frontend/e2e/static-demo.spec.mjs`),
viewport 1600×1000, deviceScaleFactor 2, the same browser context for
the whole session, specimen text `Hamburgefonstiv`. Crispy has no digit
glyphs, so the app's default sample text (which ends in digits) would
render `.notdef` boxes: retype it first, always.

## Manifest

| ID | Filename | README section | Must be visible |
|---|---|---|---|
| S01 | `instances-overview.png` | Hero | Instances tab, `Def` selected, rows rendering the specimen, header with `Load Font ▾` `Transforms ▾` `Coverage n` `Config ▾` `Rebuild`, `SRC` badges on Def and Width Matcher Preview, `G 10%` on Def. Full viewport. |
| S02 | `instances-sidebar-mappings.png` | Instances & avar2 mappings | Sidebar: textarea, `Font Size`, `Core / parametric axes` at XTRA 663.1 / XOPQ 202.5 / YOPQ 121.8, `Secondary parametric axes` (lcwd, ymod), `AVAR2 MAPPINGS` WGHT 400 / WDTH 100 / OPSZ 48, the three buttons. Element crop. |
| S03 | `secondary-axes-layers.png` | Secondary parametric axes | `SECONDARY PARAMETRIC AXES` with `lcwd` expanded: `Applicable glyphs`, `2 layers` per glyph, a layer at `XTRA 1715 · XOPQ 1462 · YOPQ 275 · lcwd 100`, the correction row `as if XTRA 47 · XOPQ 1240` + `computed`, `studio` badges. Element crop. |
| S04 | `preview-lcwd.gif` (+ `preview-lcwd-off.png`, `preview-lcwd-on.png`) | Secondary parametric axes | Preview at the ultra corner, lcwd 0 → 100: lowercase narrows, `H` unchanged. Two-frame GIF. |
| S05 | `preview-user-axes.png` | Preview & export | Preview tab at the Def location; `User axes` (`avar2-mapped`), `Secondary parametric axes`, `Transform axes` (SPAC, GRAD), expanded `Parametric axes` following the mapping. Full viewport. |
| S06 | `preview-wght-sweep.gif` | Preview & export | WGHT 100 → 900, parametric sliders and specimen follow. Five-frame GIF. |
| S07 | `export-modal.png` | Preview & export | `Export font` modal: `Hide on export` chips, `Set default at wght 400 · wdth 100 …`, Cancel / Download. Element crop. |
| S08 | `space-tab-missing-corner.png` | Space tab | Cube with the red ghost chip + `Pin` at 1715-1462-275, `Coverage findings` rail with `Missing corner`, legend, orbit hint. Full viewport. |
| S09 | `grade-badge-popover.png` | Grade | Def row `G 10%` popover open (`higher = darker` / `max N% here`, Remove, Save). Element crop. |
| S10 | `transforms-menu.png` | Post-build transforms | `Transforms ▾` open: `Grade` on, `Default grade %` 25, `Intensity %` 100, `Limit grade to counter headroom` on; `Spacing — width-aware` ON (Min −40 / Max 40 / Wide bias 1 / Scale 1.25), the other four off. Menu + header crop. |
| S11 | `config-import-modal.png` (fallback `config-menu.png`) | Configuration export / import | `Import configuration` modal with its validation summary. Element crop. |
| S12 | `load-font-menu.png` | Try it in the browser | `Load Font ▾` open: `Examples` (Crispy Mini, Roboto Delta Mini), `Upload .glyphs or project .zip…`, `Forget this project`. Crop. |
| S13 | `fontra-drawer.png` | Secondary parametric axes | Deferred until the outline editor runs in the browser; `fontra-reference.png` stays until then. |

## Setup (once; keep the browser context for every shot)

```
Goal: prepare the hosted avar2-studio demo with the Crispy project for README screenshots.

1. Assemble the project zip in a scratch directory (never inside the Crispy repo).
   Local paths on the maintainer's machine:
     /Users/agyei/Documents/Crispy/sources/Crispy.glyphs
     /Users/agyei/Documents/Crispy/sources/Crispy-avar.csv
     /Users/agyei/Documents/Crispy/sources/Crispy-grade.json
     /Users/agyei/Documents/Crispy/sources/Crispy-transforms.json
     /Users/agyei/Documents/Crispy/sources/Crispy-control.json
     /Users/agyei/Documents/Crispy/sources/.avar2-studio/axis-metadata.json  → copy as Crispy-axis-metadata.json
   zip -X crispy-demo.zip Crispy.glyphs Crispy-avar.csv Crispy-grade.json Crispy-transforms.json Crispy-control.json Crispy-axis-metadata.json
   Rules: exactly one .glyphs in the zip (leave out Crispy-adhesion.glyphs and crispy-demo-14aug.glyphs);
   no UFOs; use the zip CLI, not Finder.

2. Playwright: playwright-core with the system Chrome channel. New context: viewport
   {width: 1600, height: 1000}, deviceScaleFactor 2, fresh storage (no old IndexedDB session).
   goto https://agyeiagyeiagyei.github.io/avar2-studio/ and wait until the Instances tab shows
   the Crispy Mini rows (the demo boots into the bundled example; there is no landing overlay).

3. Upload: click `Load Font ▾` → `Upload .glyphs or project .zip…`; setInputFiles on the hidden
   `input[type=file][multiple]` with ONLY crispy-demo.zip (a zip mixed with other files is rejected).
   Wait for the in-browser compile (allow up to 120 s): the `Rebuilding — hold your edits…` veil is gone,
   there is no `The source file does not compile — the studio is paused.` banner and no amber fallback
   banner, the header family reads Crispy, the Instances tab lists the CSV rows (Narrow-Thin-MaxOpsz …
   Def, Width Matcher Preview), and the header shows a `Coverage n` button (Crispy has a missing corner).

4. Sample text (one shared value for the Instances rows and the Preview specimen, persisted with the
   session): Instances tab → sidebar textarea (placeholder `Enter sample text...`) → select all → type
   `Hamburgefonstiv`. Set `Font Size` to 48pt.

5. Select the `Def` row by clicking its name. The sidebar must show XTRA 663.1 / XOPQ 202.5 / YOPQ 121.8
   and `AVAR2 MAPPINGS` WGHT 400 / WDTH 100 / OPSZ 48.

6. The upload is saved to IndexedDB; later navigation in this context restores it without re-uploading.
   If any shot's header shows "Crispy Mini", redo step 3.

7. Fallback: if the Crispy zip fails to compile in the browser, report the error text, then use the
   bundled Crispy Mini example (it is editable) with the same letters-only text, and mark every
   affected shot in the QA notes. There is no `Def` row there; select `Regular` instead.

Slider driving: follow frontend/e2e/static-demo.spec.mjs — find the `.axis-control` whose `.axis-tag`
text equals the tag (case-insensitively; user axes may read WGHT or wght), set its `input[type=range]`
through the native value setter and dispatch a bubbling `input` event. The Preview specimen is the
`.preview-tab-sample` contentEditable (set textContent, dispatch `input`).
Save every file into docs/images/. Do not commit.
```

## Per-shot prompts (each assumes the setup state)

- **S01 `instances-overview.png`** — Instances tab, Crispy restored, `Def` selected, text `Hamburgefonstiv`, Font Size 48pt, every menu closed, no popover, scrolled to top. Full-viewport screenshot (3200×2000 px). Expect the header buttons, three tabs, the sidebar, at least six instance rows with live specimens and chips such as `XTRA: 663.1`, `SRC` on Def and Width Matcher Preview, `G 10%` on Def.
- **S02 `instances-sidebar-mappings.png`** — Same state. Screenshot the sidebar element (left column from the textarea down to the three buttons) with 16 px padding; capture at full scroll height if it scrolls. Expect XTRA/XOPQ/YOPQ at 663.1 / 202.5 / 121.8 and WGHT/WDTH/OPSZ at 400 / 100 / 48; lcwd and ymod under `Secondary parametric axes`.
- **S03 `secondary-axes-layers.png`** — Instances tab. In `SECONDARY PARAMETRIC AXES`, expand the `lcwd` row (its edit control) so the layers editor opens. Expect `Applicable glyphs` listing lowercase names (a, a.alts, b, c, c.ss01 …), each `2 layers`, one at `XTRA 1715 · XOPQ 1462 · YOPQ 275 · lcwd 100` and one at `XTRA 47 …` carrying `as if XTRA 47 · XOPQ 1240` with the `computed` badge; `studio` badges; `+ Add applicable glyphs` at the bottom. Screenshot the panel clipped to its first six glyphs (about 1600×1100 CSS px). Collapse afterwards. Add or delete nothing.
- **S04 `preview-lcwd.gif`** — Preview tab → `Reset` → untick `Auto optical size` → Font Size 96pt → user axes WGHT 1000, WDTH 220, OPSZ 144 (maps to XTRA 47 / XOPQ 1462 / YOPQ 275, where the lcwd layers sit). Specimen `Hamburgefonstiv`. Frame A: `lcwd` 0 under `Secondary parametric axes`; frame B: `lcwd` 100. Capture the same clip (specimen plus the secondary-axes group) for both frames. Expect lowercase to narrow while `H` is unchanged. Assemble a looping GIF at 1200 ms per frame; keep both PNGs. Reset lcwd to 0 afterwards.
- **S05 `preview-user-axes.png`** — Preview tab → `Reset` → untick `Auto optical size` → WGHT 400, WDTH 100, OPSZ 48 → Font Size 64pt → text `Hamburgefonstiv` → expand `Parametric axes`. Expect the parametric sliders near 663.1 / 202.5 / 121.8, `Transform axes` showing SPAC and GRAD, `Secondary parametric axes` showing lcwd and ymod. Optionally hover XTRA for the tooltip `Following the avar2 mapping — drag to override this axis directly.` Full viewport.
- **S06 `preview-wght-sweep.gif`** — Same state as S05. Full-viewport frames at WGHT 100, 300, 500, 700, 900 (wait 400 ms after each set). GIF at 700 ms per frame, last frame 1500 ms, looping. Return WGHT to 400.
- **S07 `export-modal.png`** — Same state as S05. Click `Download font…`. Expect the `Export font` modal with `Hide on export` chips and the `Set default at wght 400 · wdth 100 …` control, Cancel and Download. Screenshot the modal plus 24 px margin. Click Cancel. Never click Download.
- **S08 `space-tab-missing-corner.png`** — Space tab; wait for the cube (XTRA / XOPQ / YOPQ). If the `Coverage findings` rail is closed, click the header `Coverage n` button. Drag a little so the red ghost chip at XTRA 1715 · XOPQ 1462 · YOPQ 275 with its `Pin` button faces the camera; click that chip so the probe rail shows it (probe text `adhesion`, chips render `a`). Expect `Missing corner` in the rail, the legend, and the `drag to orbit …` hint. Do not click `Pin` or `Drop out-of-range sources`. Full viewport.
- **S09 `grade-badge-popover.png`** — Instances tab, `Def` selected. Click the `G 10%` badge on the Def row. Expect the popover with its slider, the hint `higher = darker` or `max N% here`, `Remove` and `Save`. Screenshot a clip covering the Def row and the popover (about 1600×420 CSS px). Press Escape; do not Save or Remove.
- **S10 `transforms-menu.png`** — Instances tab. Click `Transforms ▾`. Expect `Grade` on, `Default grade %` 25, `Intensity %` 100, `Limit grade to counter headroom` on; under `Post-build transforms`, `Spacing — width-aware` ON with Min −40 / Max 40 / Wide bias 1 / Scale 1.25 and the other four off. Screenshot the menu plus the header strip. Touch nothing (a toggle triggers a rebuild). Escape.
- **S11 `config-import-modal.png`** — `Config ▾` → `Export configuration…`; capture the download with Playwright's `waitForEvent('download')` into a scratch file. Then `Config ▾` → `Import configuration…` → setInputFiles with that JSON. Expect the `Import configuration` modal listing secondary axes / mappings / transforms / grade, and `Warnings — the import can still proceed:` if present. Screenshot the modal plus 24 px margin, then Cancel. Fallback if downloads cannot be captured: screenshot the open `Config ▾` menu as `config-menu.png`.
- **S12 `load-font-menu.png`** — Click `Load Font ▾`. Expect `Examples` (Crispy Mini, Roboto Delta Mini), `Upload .glyphs or project .zip…`, `Forget this project`. Screenshot menu plus header. Escape; click nothing inside.
- **S13 `fontra-drawer.png`** (later) — Instances tab → `SECONDARY PARAMETRIC AXES` → `lcwd` → the ↗ button on the `e` layer at `XTRA 1715 · …`. Expect the right-side drawer with the outline editor open on `e` at that location, the studio layers listed, `Hamburg` visible in the text bar. Full viewport.

## QA checklist

- No digits in any rendered specimen; no `.notdef` boxes. UI chrome such as `XTRA: 663.1` or `47-1-1` is in the UI font and fine.
- No `Rebuilding — hold your edits…` veil, no build or fallback banner, no scrollbars inside a crop.
- Full-viewport PNGs are 3200×2000 (`sips -g pixelWidth -g pixelHeight`); crops are at 2×.
- The header family is Crispy in every shot; S01, S02 and S09 show `Def` selected.
- GIFs loop and are under 3 MB; PNG fallbacks exist for S04.
- Files land in `docs/images/` with the manifest names. Once every shot is in, delete the images the README no longer references (`for f in docs/images/*; do grep -q "$(basename "$f")" README.md docs/*.md || echo "$f"; done`).
