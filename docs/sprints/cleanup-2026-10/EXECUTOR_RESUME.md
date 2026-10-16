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
