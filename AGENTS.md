# AGENTS.md — avar2-studio

Visual authoring and preview tool for avar2 variable fonts: a Flask
backend (`src/avar2_studio/`), a React frontend (`frontend/src/`), a Rust
crate that runs the same engines in the browser (`wasm/fontc-web/`), and
seven Glyphs 3 plugins (`src/avar2_studio/glyphs/`). The product term is
"secondary parametric axes"; code, routes and sidecars say "control axes".

## Operating standard

Follow `rules/precision-thoroughness.mdc` (alwaysApply): reproduce before
fixing, root cause over symptom, evidence kept distinct from hypothesis,
no swallowed errors, no unverified "done". Think before coding (state
assumptions; ask when interpretations differ), simplicity first (the
minimum that solves the problem), surgical changes (every changed line
traces to the request; clean up only orphans your change created; mention
pre-existing dead code, do not delete it), goal-driven execution (turn the
task into a verifiable check and loop until it passes).

## Read first

- `docs/README.md` — the map. Architecture, pipeline order and API:
  `docs/ARCHITECTURE.md`. Environments, env vars, tests, CI, branches,
  smoke playbook: `docs/DEVELOPMENT.md`. File formats: `docs/SIDECARS.md`.
- `docs/decisions/` — twelve dated decision records. Before "fixing" a
  relocation, an ordering, a tuple budget or a tag-case rule, read the
  record it points at; the measurements are there.
- Workflows and feature mechanics: `docs/guides/`.

## Invariants — do not break

- **Sidecars resolve against `ORIGINAL_PATH`, never the shadow.**
- **The studio never writes the user's original** for studio authoring:
  control axes, brace layers and grades live in the sidecars and the
  shadow under `.avar2-studio/`. `open-editor` routes per axis; treat
  changes there as high risk.
- **The canonical shadow is state; `shadow/transformed/` is derived.**
  Never run a transform over the canonical shadow.
- Layer mutations go through the **delta API under `SIDECAR_LOCK`**, not
  the whole-list PUT (a stale whole-list replace lost data once).
- `trigger_build()` only submits to the job queue; builds and exports run
  in worker subprocesses; nothing writes build outputs except the
  supervisor's promotion. Never call `_run_build()` in the server process.
- Pipeline order is physics, not taste: relocation before avar2,
  Kinetikern before SPAC, injected axes stripped before re-apply, instance
  stamps after `fix_instances`. The gvar 64 KB per-glyph ceiling is real.
- Mapping-slider commits run through **one serialized promise chain** with
  flush-on-pointer-up-and-unmount; the server rewrites the whole CSV per
  commit.
- Tags keep their authored case; matching is case-insensitive.
- UI copy never mentions file formats (no "CSV", "JSON", "sidecar"): say
  "the studio", "your source file".

## Environment and test rules

- Use the repo `.venv` only; verify with
  `python -c "import avar2_studio; print(avar2_studio.__file__)"`.
- Frontend changes: `cd frontend && npm run build && rsync -a --delete
  build/ ../src/avar2_studio/static/`, then reload; backend changes need a
  restart. Gates: `npm run lint`, `npm run typecheck`, `npm run test:unit`.
- Backend tests: `.venv/bin/python -m pytest tests/ -q` — keep it green and
  add a test or a probe for every behavioural change; `ruff check src tests`.
- Test instances are isolated: their own port and `HOME=<scratch>`; never
  run a server against a source inside the repo (stage a copy).
- **Never touch `~/Documents/Crispy` or port 5070** (the designer's live
  studio and sources). Live probes run on a copy at port 5071 and shut
  their server down afterwards (it spawns a Fontra child on the single
  global port 8001).
- Verify UI claims with screenshots you have read; verify font claims by
  reading built tables with fontTools, not by eyeballing the preview.
- No `git commit` / `push` or other repository mutation without explicit
  per-action approval; dirty working trees are expected — inspect first.

## Comments and docs

A comment states the invariant and what breaks (≤ 6 lines) and points at
`docs/decisions/NNNN` or a test; measurements and dates go into a decision
record or a named test. Docs reference symbols, never line numbers.
Keep `docs/ARCHITECTURE.md`, `docs/SIDECARS.md` and the guides current
when behaviour changes; add a decision record when a trade-off is made.
