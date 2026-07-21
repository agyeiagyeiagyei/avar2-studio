# AGENTS.md — avar2-studio

Visual authoring and preview tool for avar2 variable fonts. Flask backend
(`src/avar2_studio/server.py` + modules), React frontend (`frontend/src/`),
bundled wheel frontend in `src/avar2_studio/static/` (gitignored, CI-assembled).

## Operating standard

Follow `rules/precision-thoroughness.mdc` (alwaysApply): root-cause fixes,
evidence before changes, no swallowed errors, no unverified "done".
In addition to that file, these repo-specific rules apply.

## Environment (traps that bite)

- **Use only the repo `.venv`.** It has avar2-studio editable + `flask-sock`
  (hard dep, NOT in pyproject) + `fontra`/`fontra-glyphs` (from git). The
  system Framework Python has a stale released wheel — launching with the
  wrong interpreter silently runs old code. Verify with
  `python -c "import avar2_studio; print(avar2_studio.__file__)"` → must
  point into this repo's `src/`.
- Launch: `.venv/bin/python -m avar2_studio <source> --port 5070`
  (default port 5001). Blind launch opens the Load Font dropdown.

## Test & build commands

- Backend tests: `.venv/bin/python -m pytest tests/ -q` (suite exists; keep
  it green and extend it for behavior changes).
- Frontend changes: `cd frontend && npm run build && rsync -a --delete
  build/ ../src/avar2_studio/static/` — browser reload suffices for
  frontend-only changes; backend changes need a server restart.
- ESLint before shipping frontend edits: `frontend/node_modules/.bin/eslint`.

## Invariants — do not break

- **Sidecars resolve against ORIGINAL_PATH, never the shadow**: `-avar.csv`,
  `-control.json`, `-transforms.json` live next to the user's real source.
- **The studio never writes the user's original** for studio authoring —
  control axes and brace layers live in `.avar2-studio/shadow/` + sidecars.
  The `open-editor` routing enforces this; treat changes there as high-risk.
- Layer mutations go through the **delta API** under `SIDECAR_LOCK`, not the
  whole-list PUT (whole-list replace from a stale client caused data loss once).
- Mapping-slider commits run through **one serialized promise chain** with
  flush-on-pointer-up-and-unmount — the server rewrites the whole CSV per commit.
- `trigger_build()` coalesces; never call `_run_build()` directly.

## Working practices from the field

- **Test instances must be isolated**: own port + `HOME=/tmp/...` so
  `~/.avar2-studio` of the user is never touched. Uploads wipe
  `~/.avar2-studio/workspace/uploaded/` on every upload — shared and destructive.
- Verify UI changes with headless-Chrome/Playwright screenshots and read them
  before reporting; verify font-level claims by reading built tables
  (fontTools), not by eyeballing the preview.
- fontra runs on a single global port (8001) — one studio instance owns it.
- No `git commit`/`push`/other mutations without explicit per-action approval.

## Docs map

- `README.md` — user-facing features and launch.
- `docs/HANDOVER.md` — tribal knowledge: traps, architecture internals,
  known issues ranked, smoke-test playbook. Keep it current when behavior changes.
- `docs/authoring-instances.md`, `docs/control-axes.md` — workflows/design.
