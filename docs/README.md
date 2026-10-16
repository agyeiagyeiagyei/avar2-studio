# Documentation map

| Read this | When you want |
|---|---|
| [../README.md](../README.md) | what the studio is, the browser demo, install, the files it reads and writes, the features, the CLI |
| [ARCHITECTURE.md](ARCHITECTURE.md) | the processes, the module map, the source-path model, the pipeline order and its physics, the API surface, desktop-vs-static parity, the Fontra contracts |
| [DEVELOPMENT.md](DEVELOPMENT.md) | environments and venvs, launching and ports, the frontend bundle loop, every environment variable, tests and fixtures, CI / Pages / releases, branch policy, the smoke playbook |
| [SIDECARS.md](SIDECARS.md) | every file format the studio writes: the mappings CSV, the five JSON sidecars, `.avar2-studio/`, the config bundle, the workspace zip |
| [guides/authoring-instances.md](guides/authoring-instances.md) | the instance and avar2 mapping workflow, on both example fonts |
| [guides/secondary-parametric-axes.md](guides/secondary-parametric-axes.md) | glyph-scoped axes: coverage, brace and correction layers, the shadow, editing in Fontra |
| [guides/grade.md](guides/grade.md) | the grade model: headroom, intensity, caps, how the GRAD braces are generated |
| [guides/kinetikern.md](guides/kinetikern.md) | per-style spacing with the Kinetikern2 engine |
| [guides/transforms.md](guides/transforms.md) | the built-in transforms, their order and failure semantics, writing your own |
| [decisions/](decisions/README.md) | the twelve decision records, with the measurements behind them |
| [HANDOVER.md](HANDOVER.md) | where the old handover's content went |
| [CODEBASE_AUDIT.md](CODEBASE_AUDIT.md) | the 2026-10-16 audit that drives the cleanup sprint |
| [sprints/cleanup-2026-10/](sprints/cleanup-2026-10/PLAN.md) | the sprint plan, log, status and verifier findings |
| [archive/](archive/README.md) | dated records: the old handover, the worker plan, the Aug 2026 debugging record, the static-build design record, the screenshot brief |
| [../src/avar2_studio/glyphs/README.md](../src/avar2_studio/glyphs/README.md) | the Glyphs 3 plugins |
| [../native/kinetikern2/README.md](../native/kinetikern2/README.md) | the vendored Kinetikern2 engine |
| [../examples/README.md](../examples/README.md) | the two bundled example projects |

Conventions: documents reference symbols, never line numbers; a claim
that contradicts the code is stale and the code wins; history goes into a
decision record or a named test, not into a comment (see
[DEVELOPMENT.md](DEVELOPMENT.md#code-comments-and-where-history-goes)).
