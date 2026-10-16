# Handover

The handover document was split on 2026-10-16 so that live knowledge is
not mixed with history. Where things went:

| Was in HANDOVER | Now in |
|---|---|
| §1 repo state, branches, releases, the Crispy relationship | [DEVELOPMENT.md](DEVELOPMENT.md) — "CI, Pages and releases", "Branches and the neighbouring repositories" |
| §2 environment traps, the rsync loop, launch conventions | [DEVELOPMENT.md](DEVELOPMENT.md) — "Environments", "Launching, ports, logs", "The frontend bundle" |
| §3 source-path model, build orchestration, open-editor routing, Fontra integration, the static app | [ARCHITECTURE.md](ARCHITECTURE.md) |
| §3 `/api/glyph-coverage` contract | [ARCHITECTURE.md](ARCHITECTURE.md) §2 (`glyph_coverage.py`) and §5 |
| §3 transforms internals, corner rounding, slanting | [guides/transforms.md](guides/transforms.md); decisions [0005](decisions/0005-rond-axis-per-style-percent-rendered-fill.md), [0006](decisions/0006-slant-name-exemption-italic-doubling.md) |
| §3 Kinetikern | [guides/kinetikern.md](guides/kinetikern.md); decision [0004](decisions/0004-kinetikern-font-stage-phantom-tuples.md) |
| §3 frontend patterns, e2e traps | [DEVELOPMENT.md](DEVELOPMENT.md) — "Frontend and end-to-end" |
| §4 known issues | the open ones are sprint items in [sprints/cleanup-2026-10/PLAN.md](sprints/cleanup-2026-10/PLAN.md); the avar2-reflection item was a misdiagnosis, recorded in decision [0001](decisions/0001-build-time-default-relocation.md) |
| §4.2 the export method note | decision [0007](decisions/0007-export-set-default-relocation-direction.md) |
| §5 verification playbook | [DEVELOPMENT.md](DEVELOPMENT.md) — "Smoke playbook" |
| §6 suggested first week | overtaken by the sprint plan |

The original text is kept verbatim in
[archive/HANDOVER-2026-10.md](archive/HANDOVER-2026-10.md). The full map
of the documentation is [README.md](README.md).
