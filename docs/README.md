# Documentation map

## For designers

| Read this | When you want |
|---|---|
| [../README.md](../README.md) | what the studio is, the browser demo, install, the files it reads and writes, the six features |
| [guides/authoring-instances.md](guides/authoring-instances.md) | instances and avar2 mappings, walked on the example fonts |
| [guides/secondary-parametric-axes.md](guides/secondary-parametric-axes.md) | glyph-scoped axes: coverage, brace and correction layers, drawing them in Fontra |
| [guides/grade.md](guides/grade.md) | grade: headroom, intensity, the caps, how the GRAD braces are generated |
| [guides/kinetikern.md](guides/kinetikern.md) | per-style spacing with the Kinetikern2 engine |
| [guides/transforms.md](guides/transforms.md) | the built-in transforms, their order, writing your own |
| [../examples/README.md](../examples/README.md) | the two bundled example projects |

## Appendices (for developers)

| Read this | When you want |
|---|---|
| [CLI.md](CLI.md) | the headless build: flags, the committed build config, exit codes, `doctor --strict`, the CI recipe |
| [SIDECARS.md](SIDECARS.md) | every file format the studio writes: the mappings CSV, the five JSON sidecars, `.avar2-studio/`, the config bundle, the workspace zip |
| [ARCHITECTURE.md](ARCHITECTURE.md) | processes, module map, the source-path model, the pipeline order and its physics, the API surface, desktop-vs-static parity, the Fontra contracts |
| [DEVELOPMENT.md](DEVELOPMENT.md) | environments, launching and ports, the frontend bundle loop, every environment variable, tests, CI / Pages / releases, branch policy, the smoke playbook |
| [decisions/](decisions/README.md) | the fifteen decision records, with the measurements behind them |
| [../src/avar2_studio/glyphs/README.md](../src/avar2_studio/glyphs/README.md) | the Glyphs 3 plugins |
| [../native/kinetikern2/README.md](../native/kinetikern2/README.md) | the vendored Kinetikern2 engine |

## Internal

Sprint and audit records, kept out of the reader set:
[CODEBASE_AUDIT.md](CODEBASE_AUDIT.md) (the 2026-10-16 audit that drove the
cleanup), [sprints/cleanup-2026-10/](sprints/cleanup-2026-10/PLAN.md) (plan,
log, status, verifier findings) and [archive/](archive/README.md) (dated
records that are no longer maintained).

Conventions: documents reference symbols, never line numbers; a claim that
contradicts the code is stale and the code wins; history goes into a decision
record or a named test, not into a comment (see
[DEVELOPMENT.md](DEVELOPMENT.md#code-comments-and-where-history-goes)).
