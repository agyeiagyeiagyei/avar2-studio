STATE: working
ROUND: 21
HEAD: aa6b87f
VERIFIED_HEAD: aa6b87f
NEXT: O1 (a build with no declared axis defaults is marked degraded with a reason naming <stem>-axis-metadata.json, in health, the banner and the log — never silently "ok" at the compiled corner; CLI unchanged, exit 2 pinned by a test), then O2 (the C1 migration copies the legacy .avar2-studio/axis-metadata.json to the sibling and keeps it, instead of unlinking it). One commit each with its test; the agent's loop after each push as before. Specs and acceptance in findings.md, "O1 and O2 approved (user decision 2026-10-29)"; the evidence is the section above it. STATE: checkpoint when both are in and the Test workflow run on that checkpoint push commit is green in all six jobs (cite that run id).
APPROVED: O1 and O2 as written in findings.md (user decision 2026-10-29). Everything earlier (P1–P3, K1–K4, K1b, CI0a–d) is verified at aa6b87f.
