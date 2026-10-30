# Development

The developer loop for avar2-studio: environments, launching, the
frontend bundle, every environment variable, tests, CI, branches and the
smoke playbook. Architecture is in [ARCHITECTURE.md](ARCHITECTURE.md),
sidecar formats in [SIDECARS.md](SIDECARS.md).

## Environments

- **The repo `.venv` is the canonical environment.** Create it with
  `python -m venv .venv && .venv/bin/pip install -e ".[dev]"` (Python
  3.10+; `fontc` and `gftools` come with the package). `pip install -e
  .[dev]` also installs `ruff` and `pytest`; re-run it whenever
  `pyproject.toml`'s dependencies change — an editable install's metadata
  is not re-synced by itself.
- **Optional:** the embedded outline editor needs Fontra in the same
  environment, from git (neither package is on PyPI under those names):
  `pip install git+https://github.com/fontra/fontra.git
  git+https://github.com/fontra/fontra-glyphs.git`.
- **Native accelerators** (optional, checkout only): `cargo build
  --release` in `wasm/fontc-web` produces `target/release/spac-inject`;
  `cd native/kinetikern2 && MACOSX_DEPLOYMENT_TARGET=11.0 cargo build
  --release` produces the Kinetikern dylib (Rust 1.74+). Platform wheels carry
  both (decision 0015); a checkout build is found in place, and
  `AVAR2_SPAC_INJECT` / `AVAR2_KK2_DYLIB` point at a build elsewhere. Without
  `spac-inject` the width-aware SPAC transform falls
  back to fontTools (slower; can overflow gvar's 64 KB ceiling) and says
  so in the log; without the dylib the Kinetikern routes answer 503.
  `avar2-studio doctor` reports both.
- **Check the interpreter, not the version.** A stale released wheel in the
  system Framework Python runs old code silently (endpoints 404, the sidebar
  breaks), and `ps` shows every venv process as the Framework binary, so it
  cannot tell you which one you are looking at.
  `python -c "import avar2_studio; print(avar2_studio.__file__)"` must point
  into this checkout's `src/`; editable metadata reports a stale `devN`, so do
  not identify the environment by version.
- **Worktrees.** A worktree has no `.venv`. Most fontc-gated tests resolve the
  compiler as `Path(sys.executable).with_name("fontc")`; three still hard-code
  `REPO/.venv/bin/fontc`. Symlink the main checkout's venv as `.venv` (the
  `.gitignore` entry covers a symlink) or those tests skip.

## Launching, ports, logs

- `.venv/bin/python -m avar2_studio <source> --port 5001` (5000 is AirPlay on
  macOS). Without a source the studio launches blind and offers the Load Font
  menu; loading a source later re-points the file watcher
  (`WATCHER.reschedule()` on every load).
- Fontra listens on `--fontra-port` / `AVAR2_FONTRA_PORT`, default 8001 — one
  studio instance owns it, so a second instance needs its own port. Other
  ports: the Glyphs launcher picks 5001–5010 and logs to
  `~/.avar2-studio/glyphs-plugin.log`; Vite dev 5173; the static e2e server
  8123; the fontra-embed dev server 8099.
- Never run a server against a source inside this repository: it writes
  sidecars and `.avar2-studio/` next to the file. Stage a copy (plus its
  `-avar.csv`) under a scratch directory.
- Test instances must be isolated: their own port and `HOME=<scratch>` so
  `~/.avar2-studio/` (workspaces, user transforms, the plugin record) is
  never touched.
- `/api/health` is the probe: `status`, `font_built`, `building`,
  `last_build_status`, `build_job`, and the `*_error` keys name the stage
  that degraded (see ARCHITECTURE §5).

## The frontend bundle

The server serves `src/avar2_studio/static/` and answers 503 "Frontend
bundle not present" without it. After any frontend change:

```bash
cd frontend && npm ci            # once
npm run build && rsync -a --delete build/ ../src/avar2_studio/static/
```

Then reload the browser; backend changes need a server restart. This
rsync is easy to forget — check the bundle's mtime when the app seems to
lack a feature you just wrote. `npm run dev` (Vite, 5173) proxies `/api`
only, so the embedded Fontra editor does not work under the dev server.
Gates: `npm run lint`, `npm run typecheck`, `npm run test:unit` (eleven
node-only specs). The wasm crate is rebuilt with `wasm-pack build --target web
--out-dir ../../frontend/src/wasm/fontc-web` in `wasm/fontc-web`; the binary is
gitignored and built in CI (decision 0014), while the small wasm-bindgen glue
stays committed so the frontend builds without a Rust toolchain. The project
page under `site/` is static (Netlify) and not part of the package.

## Environment variables

Every name the code reads. "Worker" marks the ones `build_jobs.ENV_KEYS`
forwards into the build worker's environment; the rest are read where the
process already is.

| Variable | Read by | Worker | Effect |
|---|---|---|---|
| `AVAR2_STUDIO_DEBUG` | `server.main` | — | `1`/`true`/`yes`: Flask debug + reloader (same as `--debug`); the reloader can kill an in-flight build |
| `AVAR2_STUDIO_DEMO` | `routes/health.py` | — | `1`: the frontend shows the landing overlay for a shared host |
| `AVAR2_FONTRA_PORT` | `state.py` (`fontra_port`) | — | Fontra's port, default 8001 — one studio instance owns it |
| `AVAR2_STUDIO_GFTOOLS_BUILDER` | `_perform_avar2_build`, `_export_pipeline` | yes | `1`: build through the external `gftools builder` subprocess instead of `_build_in_process` (a comparison valve) |
| `AVAR2_RELOCATE_BUILD_DEFAULT` | `_build_in_process` | yes | `0`/`false`/`no` turns the build-time default relocation off (decision 0001); on by default |
| `AVAR2_SPAC_INJECT` | `builtin_spac_widthaware._native_injector`, `doctor` | yes | path to the native `spac-inject` binary (overrides `wasm/fontc-web/target/release/spac-inject`) |
| `AVAR2_KK2_DYLIB` | `kinetikern.engine_path`, `doctor` | — (not a build input) | path to the Kinetikern2 engine library (overrides `native/kinetikern2/target/release/`) |
| `AVAR2_STUDIO_AXIS_RANGES` | `build/_shims/gftools-gen-avar2` | set by the build | JSON `{tag: [min, default, max]}` the studio sets for the gen-avar2 shim — never set it by hand |
| `VITE_API_URL` | `frontend/src/api.js` (build time) | — | backend base URL for an embedded demo; default `/api` |
| `VITE_FONTRA_EMBED_URL` | `frontend/src/editor-bridge.js` (build time) | — | the fontra-embed bundle URL (default: the fontra-embed Pages site; dev `http://localhost:8099/editor.html`) |
| `window.AVAR2_EMBED_URL` | `editor-bridge.js` (runtime) | — | runtime override of the embed URL, for tests and tools |
| `AVAR2_PYTHON` | cargo oracle tests | — | the Python with fontTools + gftools the oracles shell out to; default `<crate>/../../.venv/bin/python`, then `python3` |
| `AVAR2_SPIKE_FONT`, `AVAR2_ORACLE`, `AVAR2_CSV` | `tests/avar2_oracle.rs`, `braces_oracle.rs` | — | fixture overrides; the oracles build their own fixtures in `target/tmp/` when these are unset |
| `CORRECTION_FONT` | `tests/correction_oracle.rs` | — | the fixture that enables the otherwise `#[ignore]`d correction oracle |
| `STUB_WORKER_MODE`, `STUB_WORKER_SLEEP` | `tests/stub_build_worker.py` | — | `ok` / `sleep` / `die` behaviour of the stub worker in the job-queue tests |

Note that `AVAR2_KK2_DYLIB` is not in `ENV_KEYS`: the worker finds the
dylib through the same `engine_path()` default, so set it in the server's
environment only when the default location is wrong on both sides.

## Tests

### Python

`.venv/bin/python -m pytest tests/ -q` — **654 passed, 0 skipped** on 2026-10-29
with fontc and the Kinetikern engine available (≈80 s; 620 `def test_` functions,
the rest parametrised cases);
`ruff check src tests` must be clean (the baseline is in `pyproject.toml`:
E4/E7/E9/F/B/I001/RUF100/W, with E402, E501 and B905 ignored).

- `tests/conftest.py` gives every writing test a throwaway copy of an
  example source under `tmp_path` (`crispy_source`, `crispy_csv`,
  `roboto_source`), points transform discovery at an isolated directory so
  `~/.avar2-studio/transforms/` never leaks in, and **replaces `server.JOBS`
  with an inert fake** for every test. A test that needs the real queue
  opts in with `@pytest.mark.real_jobs` (registered in `pyproject.toml`).
- `tests/test_build_jobs.py` runs the real `JobQueue` with
  `tests/stub_build_worker.py` (no fontc, milliseconds).
  `tests/test_worker_shadow.py` is the one real-worker end-to-end build.
- **fontc-gated tests** skip when fontc is missing, and there are more of them
  than there used to be: `test_avar2_default_relocation.py`,
  `test_kinetikern.py`, `test_kinetikern_inject.py`, `test_worker_shadow.py`,
  `test_build_degraded.py`, `test_build_config_worker.py`,
  `test_export_active_source.py`, `test_export_relocation.py` and
  `test_sources_export.py` (that last one also skips without fontmake). Most
  resolve the compiler as `Path(sys.executable).with_name("fontc")`. The
  Kinetikern tests also need the dylib (`kinetikern.engine_path()`).
- Glyphs-plugin tests (about 60 % of the suite) run against fake modules;
  no Glyphs.app is needed.

### Rust

`cargo test` in `wasm/fontc-web` compiles 15 test binaries. The oracles shell
out to Python (`AVAR2_PYTHON`, else the repo venv, else `python3` — in a worktree
set `AVAR2_PYTHON` to a venv with fontTools and gftools) and write their outputs
under `target/tmp/`. The fixtures are generated in-test from the crisp-mini
example and from `tests/fixtures/` (`FoldedMini.glyphs` for the rounding
classification, `StrandedMini.glyphs` for the clamp oracle, regenerated by
`spike/make_stranded_fixture.py`), so nothing under `/tmp` is needed.

`correction_oracle` is `#[ignore]`d with a reason; `CORRECTION_FONT=…
cargo test -- --ignored` runs it.

### Frontend and end-to-end

`npm run test:unit` runs the node-only specs: `avar2-eval.spec.mjs`
(self-skips without its `/tmp/crispy-demo-live.ttf` fixture; regenerate
with `reflection-extract.mjs`), `grade-model.spec.mjs` (caps vs the Python
oracle), `avar2-lint.spec.mjs`. The Playwright specs use `playwright-core`
with the system Chrome channel:

| Spec | Needs |
|---|---|
| `static-demo.spec.mjs` (28 sections) | `python scripts/snapshot_static_demo.py`, then `npx vite build --base=./ --outDir dist-pages` in `frontend/`, `python3 -m http.server 8123 -d frontend/dist-pages`, `STATIC_URL=http://localhost:8123`; the venv's Python (fontTools, Pillow) to verify downloads |
| `editor-bridge.spec.mjs` (55 checks) | the same dist-pages plus a built fontra-embed served on 8099 |
| `grade-live.spec.mjs`, `reflection-live.spec.mjs` | the designer's own font + its config; the live site or `STATIC_URL` |
| `prod-smoke.mjs` | the live Pages demo and the live fontra-embed deploy; run after deploys |

An e2e trap that cost a session: after the download check the app sits
on the Preview tab, which has no `.sidebar`; normalise to the Instances
tab before asserting. A prior IndexedDB session auto-restores, so a
"first visit" may not be one. Synthetic `input` events leave the
mapped-location reflection one step behind; real `page.mouse` drags do
not.

## CI, Pages and releases

- `.github/workflows/pages.yml` — on push to `main` or `github-pages`:
  `pip install -e .`, `scripts/snapshot_static_demo.py`, Node 22, `npm ci`,
  lint + typecheck + test:unit, `cargo install wasm-pack` then
  `wasm-pack build` for the wasm binary, `vite build --base=/avar2-studio/
  --outDir dist-pages`, deploy (decision 0014 — the binary is a build output and
  is not committed). Commit + push is the deploy.
- `.github/workflows/release.yml` — five jobs on a `v*` tag (bump
  `pyproject.toml` first): `build` (Node, frontend, rsync into
  `src/avar2_studio/static/`, `python -m build`, `twine check`, the GitHub
  Release), `build-platform-wheels` (the macOS/Linux matrix that cargo-builds
  `spac-inject` and `libkinetikern2` into `src/avar2_studio/bin/` and tags the
  wheel `py3-none-<plat>` — decision 0015), `release-platform-assets`, and the
  two publish jobs (`publish-pypi` on trusted publishing, `publish-testpypi` for
  the rehearsal). As of 2026-10-30 **no `v*` tag has been cut**, so 0.1.0 is not
  on PyPI; the README says so.
- `.github/workflows/test.yml` — on every push and PR: `python` (a version
  matrix running pytest + ruff), `cli-build` (builds the bundled example from an
  installed package and asserts the checkout stays clean), `frontend` (lint,
  typecheck, unit specs, build) and `rust` (`cargo test`). Six check names with
  the matrix, and the sprint's acceptance is that all of them are green.

## Branches and the neighbouring repositories

- **`github-pages` is the working branch.** `main` is stale (nothing
  since 2026-09-12) and only ever fast-forwarded. Crispy's CI installs
  `avar2-studio @ git+…@github-pages`, so the branch is load-bearing:
  never rewrite its history. The 2026-10 cleanup runs on `cleanup-2026-10`
  (worktrees `avar2-studio-sprint`, `avar2-studio-t2-docs`) and merges
  back into `github-pages`.
- **Crispy** (`~/Documents/Crispy`) is the parent project this tool was
  extracted from; its `sources/` are the designer's live work, so never build
  against them in place (stage a copy). Its old avar2 tooling lives in
  `archive/avar2-tooling/` there, not here.
- **fontra-embed** (`~/Documents/fontra-embed`, GPL) is the browser
  editor; its contract with this repo is the postMessage protocol in
  ARCHITECTURE §7. A bug on the embed side (e.g. the `location` forward
  for `getReferenceMetrics`) is fixed there and deployed to its Pages site.
- No `git commit` / `push` or other repository mutation without explicit
  per-action approval; dirty working trees are expected — inspect before
  touching.

## Smoke playbook

```bash
.venv/bin/python -m pytest tests/ -q                     # 654 passed, 0 skipped (fontc + engine present)
.venv/bin/ruff check src tests
cd frontend && npm run lint && npm run typecheck && npm run test:unit && npm run build && cd ..
cd wasm/fontc-web && AVAR2_PYTHON=../../.venv/bin/python cargo test && cd ../..   # fixtures are generated in-test

# a copy of the example, never the repo fixture
cp -r examples/crispy-mini/sources /tmp/crispy-mini-smoke
HOME=/tmp/avar2-home .venv/bin/python -m avar2_studio /tmp/crispy-mini-smoke/CrispyMini.glyphs --port 5071
curl -s localhost:5071/api/health            # status ok, font_built true, building false, last_build_status ok
curl -s localhost:5071/api/build/status      # running null once idle; sizes.woff2_exact true after the compress job
curl -s localhost:5071/api/glyph-coverage    # axes with layers / min / default / max, glyph_chars
curl -s -X POST localhost:5071/api/control-axes/<studio-tag>/open-editor
                                             # editing_original false, project = the shadow file
```

Repeat with `examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace`:
scoped axes show the read-only layers panel with thumbnails, and
`open-editor` returns `editing_original: true` with the `.designspace` as
project. In the UI: add a brace layer on a studio axis — the save is
instant, "rebuilding preview…" appears, the preview catches up in a few
seconds (shadow regen ≈1 s, fontc ≈0.09 s on the example; a full Crispy
build is ≈36 s wall-clock). Verify font-level claims by reading the built
tables with fontTools, never by eyeballing the preview; verify UI changes
with headless-Chrome screenshots you have actually looked at.

## Code comments and where history goes

A comment states the invariant and what breaks if it is violated, in six
lines or fewer, and ends with a pointer: `See docs/decisions/NNNN` and/or
`tests/test_x.py::name`. Measurements, dates and "observed / once / the
hard way" belong in a decision record or a named test; docstrings
describe the contract, not its history; documentation references symbols,
never line numbers. The records are in [decisions/](decisions/README.md).
