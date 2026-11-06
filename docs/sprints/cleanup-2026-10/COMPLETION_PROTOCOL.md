# Completion protocol — sprint `cleanup-2026-10` (from 2026-11-06)

The user's instruction (2026-11-06): **"we need everything working"**. The agent and the
verifier run the rest of the sprint between themselves, through the sprint files, without
waiting on the user. The user is needed only at the gates in §6.

This file is the contract. Where it differs from older notes, this file wins. The user's
decisions recorded in STATUS (VERIFIER / APPROVED lines) and in `findings.md` still stand.

## 1. End state

- Every item in §4 is done and verified.
- Every GitHub CI job is green on the branch tip.
- `APPROVED: B11a–c` is on the STATUS line, and B11b and B11c are done.
- STATE says `awaiting user (ship gates)`, listing only the gates in §6.

## 2. Roles

- **Agent**
  - Implements, tests and measures.
  - Writes the LOG entry for each round and maintains STATE / ROUND / HEAD / NEXT in STATUS.md.
  - Owns the code on `cleanup-2026-10`. E2i-c is merged; do not reopen `e2ic-wip`.
- **Verifier**
  - Reviews every STATUS push.
  - Reruns the claims on the agent's own artifacts and on its own fixtures, and checks CI.
  - Writes findings to `findings.md` and notes on the `VERIFIER:` line, and owns the `APPROVED:` line.
  - Runs the automated checkpoint pass (`~/avar2-verifier`, ports 5073/8003).
- **User:** only the gates in §6. The verifier relays anything else the user says onto the VERIFIER line, as "USER DECISION …".

## 3. The loop (no silent stops)

1. **Channel:** `docs/sprints/cleanup-2026-10/` on `origin/cleanup-2026-10` (STATUS.md, findings.md, LOG.md). The agent cannot be messaged directly, and the verifier wakes on every change to STATUS.md.
2. **Agent: push a STATUS change every time it stops.** That means an item finished, a round ended, or it is blocked. STATE is one of:
   - `working`
   - `checkpoint`
   - `blocked (reason)`
   - `awaiting user decision (question + options + recommendation)`
   - `awaiting user (ship gates)`
3. **Agent: after every STATUS push, WAIT FOR THE VERIFIER; do not go idle.** Poll `origin/cleanup-2026-10` (your verifier watcher, or `git fetch` every 2 min) until a verifier commit lands. Read its VERIFIER note and findings section, act on them, and push the next STATUS.
   - If a verifier reply says "carry on", keep going without waiting for anything else.
   - If nothing comes for 60 min, re-push STATUS with an appended "waiting for verifier since HH:MM" and keep polling. Do not assume approval.
4. **Verifier:** answers every STATUS push, normally within the hour.
   - On `checkpoint`: runs the automated pass and its manual review, then writes either `APPROVED: …` or findings.
   - On `awaiting user decision`: asks the user and writes the answer back.
5. **Checkpoint** only when everything holds:
   - every §4 item is done;
   - every CI job is green on the HEAD you name;
   - the local suite, ruff and frontend lint are clean;
   - the evidence is in LOG.md, with commands, numbers and paths.
6. **Verifier notes are blocking unless marked "optional" or "not blocking".** Answer each one, either with the fix or with evidence that it is wrong.

## 4. Work items (in order; each with its acceptance)

**W1 — E2i-c in the Rust injector** (`wasm/fontc-web/src/spac.rs`). User decision 2026-11-06: option 3, port now.
- Mirror `transforms/builtin_spac_widthaware.py` and `spac_amounts.py` exactly:
  - per-master corner amounts from the master locations, with the corners normalised against the BUILT font's fvar/avar;
  - the export's integer per-side amount (same rounding);
  - the 1 u tight-end floor, overhanging glyphs only;
  - sequential-rounding deltas, as `getDeltas(ints, round=round)` does;
  - E2k: a composite takes its base's amount, and `USE_MY_METRICS` composites are skipped;
  - derived (slant/round) masters take their upright parent's amounts;
  - `.notdef` excluded.
- The same crate builds the native `spac-inject` that the wheel ships, so both paths change together. The native CLI gets the master locations through its payload.
- Acceptance:
  - (a) `spac_oracle` passes on ubuntu and macos.
  - (b) The oracle gains a multi-master fixture on which TODAY's constant rule FAILS. Show that run against the old `spac.rs`.
  - (c) At every master corner, Rust = Python = the export twin, on a pinned build of the coincide copy (`~/avar2-verifier/scripts/spac_at_masters.py`).
  - (d) Say whether the Python side should use the native injector again. If yes, re-run the round trip on a pinned build and it must pass unchanged:
    - `roundtrip_compare.py` with `--designspace`;
    - `sparse_sweep.py`;
    - `spac_at_masters.py`;
    - `ft_hb_check.py`.
  - (e) The static app's SPAC works end to end in a browser check, the Pages build served locally on 5072. Record the numbers and a screenshot read.

**W2 — the two tests that pass on the old code** (verifier 17:14).
- `tests/test_spac_amounts_injector.py`: build the fixture so the wide corner's ink really changes (e.g. ufoLib2 glyphs compiled by fontmake, or gvar deltas on the right-hand points only). Choose amounts where Σround(Δ) ≠ round(amount). It must FAIL with `builtin_spac_widthaware.py` taken from `80e96e8`. Show the run.
- `test_a_rotated_component_takes_its_own_advance_not_its_bases`:
  - give nine a different advance and offset at each master;
  - put the sparse layer strictly between the masters;
  - assert both the interpolated advance and the outline's xMin.

  It must FAIL when the layer copies the parent master's values (the attempt-3 bug). Show the run.

**W3 — Fontra open-editor, intermittent 500** (verifier 19:00).
- Capture Fontra's stdout and stderr to a log file instead of DEVNULL.
- Make the probe print the HTTP error body (the diff is in `~/avar2-verifier/probe-diag/ui_probe.py`).
- Acceptance: the verifier's next full pass has `control-axis` PASS, or the log names the cause and you fix it at the root.

**W4 — the SPAC gate's control.** `--self-test` re-implements the verdict on synthetic samples. Replace it, or back it, with a font-level negative control that runs the REAL `main()`: a TTF copy with one glyph's SPAC deltas shifted by 1 u per side must FAIL, and the original must PASS. The verifier's `~/avar2-verifier/negctl/perturbed-g.ttf` shows the shape.

**W5 — green everywhere.**
- All GitHub CI jobs green on the tip: python × 2, rust × 2, frontend, cli-build.
- If a job is flaky, show it, fix it, or quarantine it with a reason the verifier accepts. Never quietly re-run until it passes.

Then set STATE `checkpoint`.

## 5. Release steps (only after the STATUS line says `APPROVED: B11a–c`)

As specified in `findings.md`, "B11 pushes approved for the agent" and "B11c amendment":

- **R1 — B11c, release wheels, dry run only.**
  - Run `release.yml` via `workflow_dispatch` (artifacts, or `publish_to: testpypi`).
  - Install the platform wheel in a clean venv.
  - The wheel must contain `avar2_studio/bin/spac-inject` (the W1 build) and `libkinetikern2`, and `avar2-studio doctor` must find both at the wheel path with no `AVAR2_*` set.
  - **Never push a version tag.**
- **R2 — B11b, fontra-embed.** Push `~/Documents/fontra-embed` branch `shared-studio-shim` (`3de37a6`) to origin as a branch. A PR is fine; merging it is the user's call.
- **R3 — main, SQUASH merge.** The user approved this on 2026-11-06, together with the strategy. The branch history carries ~113 MB of committed artifacts (04cc018, 431524e, aec81b9, c606e25), and a squash keeps them out of `main` for good.
  - Squash `cleanup-2026-10` into `main` as one commit. The message summarises the sprint and points to `docs/sprints/cleanup-2026-10/`.
  - Push `main` normally: no force, no tag.
  - Proof: `git diff <cleanup tip> main` is empty for the tree (or lists only what you explain), and every CI job is green on `main`.
  - Push a STATUS change, and wait for the verifier's check before R4.
- **R4 — B11a, Pages, from `main`.** Merge `main` into `github-pages` with a regular merge commit, keeping `github-pages`-only site content. Do NOT merge `cleanup-2026-10` directly: that would bring the artifact history along.
  - Push, then read the Pages workflow run.
  - Proof: the run id is green, and the site serves the new bundle (give the asset hash). The static app's SPAC works on the live site (W1 e).
- Then STATE: `awaiting user (ship gates)`. B12 is the user's next step.

## 6. User gates (the only things that wait for the user)

1. ~~Merge strategy~~: DECIDED 2026-11-06. Squash into `main`, then `main` → `github-pages` (R3, R4).
2. ~~Approving the merge into `main`~~: APPROVED 2026-11-06. It runs as R3, after `APPROVED: B11a–c` and R1–R2.
3. **B12:** the user runs it, after R4, on `main`: the real studio on 5070 on the real Crispy, plus a short re-run of the affected Part B items (spacing, export). Anything it finds is fixed forward on a new branch from `main`, under the same loop.
4. **The version tag** (a PyPI publish). Handled separately by the user, outside this loop.
5. **Rebuilding and committing Crispy's production font.** Handled separately by the user, outside this loop.

**Why the user needs this:** they need the tool ready to finish Crispy's design and planning. Order the work so the desktop studio on `main` is solid first. Nothing in W1–W5 may regress the desktop studio's behaviour that has already been verified.

A new decision that is genuinely the user's, such as a scope change, a design choice or anything irreversible: set `awaiting user decision` with the options and a recommendation, and keep working on everything that does not depend on it.

## 7. Standing rules (unchanged; restated so nothing is lost)

**Files and ports**
- Never touch `~/Documents/Crispy` sources or sidecars. Copy them read-only.
- Never start, stop or probe ports 5070, 5071 or 8001, or touch `~/avar2-studio-test`.
- Ports: the agent uses 5072/8002; the verifier uses 5073/8003.
- Nothing durable under `/tmp`.

**Git**
- No history rewrite, no force-push, no tag, no PyPI publish.
- Never run a bare `git stash`.
- Commit trailer: the `Co-Authored-By:` line your own session's attribution instructions give (the model that wrote the commit).

**Builds**
- Acceptance builds come from a PINNED worktree, with provenance at the head of `build.log`: branch, commit, dirty count, `avar2_studio.__file__`. Check the build signature before measuring.

**Machine (16 GB Mac, small free disk)**
- One heavy real-Crispy build at a time (≈6 GB RAM).
- Never one while the verifier's pass runs (`pgrep -f verify-checkpoint.sh`).
- Before a heavy build, free disk must be ≥ 3 GB (`df -h /System/Volumes/Data`).
- Delete your own stale build and test-output dirs when you are done with them.

**Evidence**
- A test "fails on the old code" only when you have RUN it against the old code. Name the commit and paste the failure.
- No swallowed errors.
- No validation claimed that was not run.
- Evidence and hypothesis stay distinct.

**Copy**
- UI copy never names sidecar, JSON or CSV files.
