# Executor resume brief — cleanup sprint 2026-10

Paste to the executor agent to (re)start it. Everything it needs is in
files; no conversation history is required.

> Resume the avar2-studio cleanup sprint as the executor. Work in the
> worktree `~/Documents/avar2-studio-sprint` on branch `cleanup-2026-10`
> (`git pull` first). Read, in this order:
> `docs/sprints/cleanup-2026-10/STATUS.md`, then
> `docs/sprints/cleanup-2026-10/findings.md` (newest "Round N" section
> first; the standing instructions at the bottom apply), then
> `docs/sprints/cleanup-2026-10/PLAN.md` (item list and IDs) and
> `LOG.md` (append only), with `docs/CODEBASE_AUDIT.md` as the evidence
> behind each item.
>
> **Your scope is the entire remaining backlog, in PLAN.md order, until
> every item is done or BLOCKED — do not stop at the end of a tier.**
> Before any new work, fix every item the newest findings round marks
> FAILED. Then continue down the backlog.
>
> **Checkpoints are not stops.** Every ~15 commits (or at a tier
> boundary) set STATUS.md to `STATE: checkpoint` with ROUND+1, HEAD and
> ITEMS_READY, commit `sprint: checkpoint (round N)`, push — and keep
> working immediately. The verifier verifies each checkpoint in
> parallel and appends a "Round N" section to findings.md; at your next
> checkpoint, `git pull` first and fix anything it marked FAILED before
> new items. Set `STATE: ready-for-verification` only when PLAN.md is
> exhausted (everything done or BLOCKED), then stop.
>
> Rules: one commit per item (`T<tier>.<n>: …`); a test or probe for
> every behavioural change; commands + results in LOG.md; never touch
> `~/Documents/Crispy` or port 5070 (live probes on a copy at port 5071
> only, and shut your probe server down afterwards — it spawns a Fontra
> child on port 8001). If a push is rejected, `git pull --rebase` (the
> verifier only ever touches findings.md/STATUS.md) and push again.

The verifier never edits this file's rules without a user instruction.

## Phase C resume brief — CI-grade CLI (added 2026-10-28 on the user's instruction; supersedes the port/path rules above)

> Resume the avar2-studio sprint as the executor, phase C. Work in the
> worktree `~/Documents/avar2-studio-sprint` on branch `cleanup-2026-10`
> (`git pull` first). Read, in this order: `docs/sprints/cleanup-2026-10/STATUS.md`,
> then in `findings.md` the section "Approved next scope — a CI-grade
> `avar2-studio build`" (items C1–C9, each with its acceptance evidence),
> then `LOCAL_TEST_ROUTINE.md` (how a copy is staged, `ui_probe.py`).
> No conversation history is needed.
>
> **Goal.** One command the Google Fonts team can run from a clean
> checkout: `avar2-studio build Font.glyphs --config Font-build.json
> --out fonts/` — the source, the mappings CSV and one committed config
> produce the shipped font (transforms, hidden axes, resting style, named
> instances + STAT, TTF and WOFF2). Design-authored data (secondary axes,
> grade, Kinetikern, instance coordinates) stays studio-only: the CLI
> reads it as the studio's build does and never offers a way to edit it.
>
> **Order.** C9 first (`--fontra-port` + `AVAR2_FONTRA_PORT`), then
> C1 → C7, then C8 (your loop). One commit per item (`C<n>: …`) with its
> test; evidence in `LOCAL_TEST_RESULTS.md`; commands + results in
> `LOG.md` (append only). Set `STATE: checkpoint` in STATUS.md (ROUND+1,
> HEAD) when C1–C9 are all in, push, and keep going on anything the
> verifier then marks FAILED. The verifier's loop verifies every
> checkpoint automatically and writes its review into findings.md; at
> each of your pushes `git pull --rebase` first (the verifier only
> touches findings.md/STATUS.md/EXECUTOR_RESUME.md).
>
> **Your loop (C8).** Build `~/avar2-agent/loop.sh` (outside `/tmp` —
> macOS's periodic cleanup empties it) and keep it running: after each
> of your pushes it restages a fresh Crispy copy per the routine, runs
> `avar2-studio build` from the installed package on a git-clean
> checkout of that copy (`git status --porcelain` empty before and
> after), starts the studio on **port 5072 with `--fontra-port 8002`**
> on the new bundle, runs the full `ui_probe.py` at idle, appends the
> results to `LOCAL_TEST_RESULTS.md` and pushes. Its results section
> names the root, the ports and the git-clean check.
>
> **Reserved — never touch, never probe:** `~/Documents/Crispy` (copy
> from it, never write or watch it), port 5070 (the designer's live
> studio), port 5071 and `~/avar2-studio-test` (the designer's Phase B
> studio and its sync), and Fontra's port 8001. The designer is testing
> there while you work; a run that touches any of these fails the
> hand-off regardless of its test results. Your roots are `~/avar2-agent/`
> and ports 5072/8002 only; the verifier uses 5073/8003.
