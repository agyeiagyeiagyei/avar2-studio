# Executor resume brief — cleanup sprint 2026-10

Paste to the executor agent to start each round. Everything it needs is
in files; no conversation history is required.

> Resume the avar2-studio cleanup sprint as the executor. Work in the
> worktree `~/Documents/avar2-studio-sprint` on branch `cleanup-2026-10`
> (`git pull` first). Read, in this order:
> `docs/sprints/cleanup-2026-10/STATUS.md` (the `APPROVED:` line is your
> scope for this round), `docs/sprints/cleanup-2026-10/findings.md`
> (latest "Round N" section: fix every FAILED item first; standing
> instructions at the bottom apply), `docs/sprints/cleanup-2026-10/PLAN.md`
> (the item list and IDs), `docs/sprints/cleanup-2026-10/LOG.md` (append
> only), and `docs/CODEBASE_AUDIT.md` for the evidence behind each item.
> Rules: one commit per item (`T<tier>.<n>: …`), test or probe for every
> behavioural change, commands + results in LOG.md, never touch
> `~/Documents/Crispy` or port 5070 (live probes on a copy at 5071 only).
> When the approved scope is done — or after ~15 commits — set STATUS.md
> to `STATE: ready-for-verification` with ROUND+1, HEAD and ITEMS_READY,
> commit `sprint: ready for verification (round N)`, push, and stop. The
> verifier re-checks every hand-off and writes the next `APPROVED:` scope.

The verifier never edits this file's rules without a user instruction.
