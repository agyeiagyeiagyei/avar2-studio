# Command line

The headless half of the studio: the same build pipeline the app runs when it
loads a font, with no web server, no file watcher and no Fontra. This is what
CI uses — the studio owns the build, so the build CI checks is the build the
app shows.

```
avar2-studio                       # the studio (default command)
avar2-studio build SOURCE --out DIR
avar2-studio doctor [--strict]
avar2-studio install-glyphs-plugins
```

## `avar2-studio build`

Builds `SOURCE` (`.glyphs` or `.designspace`) into `DIR`, printing every
artifact path on stdout, one per line.

| Flag | What it does |
|---|---|
| `--out DIR` | **required.** Where the built font (or the sources zip) is written; created if missing |
| `--workdir DIR` | Put the tool workdir (config, shadow, build output, relocation memo) under `DIR` instead of beside the source, so a CI checkout stays untouched |
| `--hide-axes XOPQ,XTRA` | Set the `fvar` HIDDEN flag so font pickers stop surfacing those axes |
| `--default wght=400,opsz=14` | Re-origin the export at that resting style: the parametric defaults move to the mapped location and the avar2 table is regenerated around it, every axis range intact |
| `--format ttf` / `woff2` / `sources` | Output format, repeatable; default `ttf`. `sources` writes `<family>-sources.zip` (masters as UFOs + a designspace 5 with the mappings) instead of a font |
| `--config FILE` | The committed build config (default: `<stem>-build.json` beside the source, when present) |
| `--write-config` | Write `<stem>-build.json` from the current sidecars and exit, without building |

`--csv` still parses but is refused (exit 2): the build always reads
`<stem>-avar.csv` beside the source, and pretending otherwise was a trap.

`--format sources` writes from the active source, so it needs neither a built
font nor the export pipeline. It refuses `--default` (`sources rest at the
compiled origin; --default applies to ttf/woff2 only`) because a designspace
default must be a master location. A one-line note on stderr says where the font
rests: the compiled origin, not the studio's relocated default. Spacing rides the
sources — a `SPAC` axis as metrics-only twin masters and the Kinetikern
corrections as metrics-only brace layers, rendered from the same sidecars the
built font uses.

## The committed build config

`<stem>-build.json` beside the source records the export choices a repo wants
reproducible. Every key is optional:

```json
{
  "format": "avar2-studio-build",
  "format_version": 1,
  "axes": { "hide": ["XOPQ", "XTRA"] },
  "default": { "wght": 400, "opsz": 14 },
  "transforms": [{ "type": "slant", "enabled": true, "params": { "angle": -11 } }],
  "instances": { "fix": true, "stat": true },
  "formats": ["ttf", "woff2"]
}
```

- `transforms` **replaces** the transforms sidecar for this build only; the
  studio's committed `<stem>-transforms.json` is untouched.
- `instances.fix` / `instances.stat` fold into the `fix_instances` / `gen_stat`
  transforms.
- Flags override the config; `--write-config` emits it from the current state.
- Design-authored state is refused with exit 2: `control`, `grade`,
  `kinetikern`, `coordinates` and their spellings are edited in the studio, not
  in this file.

The build reads the source, `<stem>-avar.csv` and
`<stem>-axis-metadata.json` (the declared axis defaults — without it the build
exits 2, because named styles would silently land off their mapped values).
Everything design-authored — control axes, grade, Kinetikern, instance
coordinates — is read from its sidecar and never written by the build.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Built (or wrote the config) |
| 1 | The build itself failed. The `error:` lines name the stage — build, avar2 mapping, origin relocation, grade, source transform — and why |
| 2 | Usage or load error: bad flag value, unreadable source, missing/unreadable build config, design-only key in the config, or missing axis metadata |

A relocation that cannot fit exits **1**, not 2: the font builds, the reason
reads `origin relocation failed (degraded build — the avar2 mapping is not the
authored one)`, and the studio shows the same thing in its banner. An avar2
failure, a missing grade axis or a source-transform failure also exit 1 — a
plain fallback font is never a pass.

## Building in CI

```bash
pip install "git+https://github.com/agyeiagyeiagyei/avar2-studio"  # until 0.1.0 is on PyPI
avar2-studio doctor --strict        # fail early, with the missing piece named
avar2-studio build sources/MyFont.glyphs --config sources/MyFont-build.json \
    --workdir "$RUNNER_TEMP/avar2" --out fonts/
```

A font repo's workflow:

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
  with: {python-version: "3.11"}
- run: pip install "git+https://github.com/agyeiagyeiagyei/avar2-studio"
- run: avar2-studio doctor --strict
- run: >
    avar2-studio build sources/MyFont.glyphs
    --out fonts/ --workdir "$RUNNER_TEMP/avar2"
```

`--workdir` matters in CI: without it the tool workdir (config, shadow, build
output, relocation memo) is created beside the source, inside the checkout.

## `avar2-studio doctor`

Reports what the build will resolve: the compiler (`fontc`, then `fontmake`),
`gftools`, `glyphsLib`, the Fontra bridge, the static bundle, the two native
accelerators (`spac-inject`, `libkinetikern2`), stale processes and install
shadowing. `--strict` turns any missing requirement into a non-zero exit, which
is how a release workflow asserts a fresh install is complete.

`doctor` finds `fontc` on `PATH`; the build also looks beside
`sys.executable`. A `pointing at a missing file` note means an environment
override is set and broken.

## `avar2-studio install-glyphs-plugins`

Symlinks the Glyphs 3 plugins (`avar2 Studio` hub and the six design tools) into
`~/Library/Application Support/Glyphs 3/Plugins/`. Restart Glyphs afterwards;
edits in the checkout are live from then on. See
[`../src/avar2_studio/glyphs/README.md`](../src/avar2_studio/glyphs/README.md).

## Environment variables

The build reads a small set of overrides (ports, accelerator binaries, the
gftools-builder safety valve); they are listed once in
[`DEVELOPMENT.md`](DEVELOPMENT.md#environment-variables) rather than here.
