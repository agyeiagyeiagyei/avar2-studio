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
| `--workdir DIR` | Put the tool workdir (config, shadow, build output, relocation memo) under `DIR` instead of beside the source, so a CI checkout stays untouched. The two design-authored files below are still created beside the source on a first run — commit them |
| `--hide-axes XOPQ,XTRA` | Set the `fvar` HIDDEN flag so font pickers stop surfacing those axes |
| `--default wght=400,opsz=14` | Re-origin the export at that resting style: the parametric defaults move to the mapped location and the avar2 table is regenerated around it, every axis range intact |
| `--format ttf` / `woff2` / `sources` | Output format, repeatable; default `ttf`. `sources` writes `<family>-sources.zip` (masters as UFOs + a designspace 5 with the mappings) instead of a font, and it needs a `.glyphs` source — a `.designspace` project exits 2 ("sources can only be written from a .glyphs file") |
| `--config FILE` | The committed build config (default: `<stem>-build.json` beside the source, when present) |
| `--write-config` | Write `<stem>-build.json` from the current sidecars and exit, without building |

`--csv` still parses but is refused (exit 2): the build always reads
`<stem>-avar.csv` beside the source, and pretending otherwise was a trap.

`--format sources` writes `<family>-sources.zip` from the active source, and it
**needs the font build to succeed first** — the same build every other format
uses. The spacing amounts are measured on the built font at each master
(`_ink_widths_from_build`), exactly as the injector measures the font it is
handed, so the two agree. So a failed build fails the run (exit 1) before any zip
is written — there is no sources-only shortcut.

It refuses `--default` (`sources rest at the compiled origin; --default applies
to ttf/woff2 only`) because a designspace default must be a master location. A
one-line note on stderr says where the font rests: the compiled origin, not the
studio's relocated default. Spacing rides the sources — a `SPAC` axis as
metrics-only twin masters (one per range end, symmetric ranges included, capped
where a master's advance is narrower than the amount) and the Kinetikern
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

The build reads the source, `<stem>-avar.csv` and the declared axis defaults —
`<stem>-axis-metadata.json` beside the source, or the legacy
`.avar2-studio/axis-metadata.json`; it exits 2 only when **neither** exists,
because without them each traditional axis' declared default (opsz 144 on
Crispy) falls back to its column minimum and every named style lands off its
mapped value.
Everything design-authored — control axes, grade, Kinetikern, instance
coordinates — is read from its sidecar and never written by the build. Two
files are the exception, and only on a first run: when `<stem>-avar.csv` is
missing the build writes it from the source's own instances (header plus one
row each), and when `<stem>-axis-metadata.json` is missing it is created empty
(`{}`) or migrated from the legacy `.avar2-studio/axis-metadata.json`. Both
land beside the source even under `--workdir`, because they are the designer's
to commit — an empty metadata file is logged as a warning, since it leaves the
font resting at the compiled corner.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Built (or wrote the config) |
| 1 | The build itself failed. The `error:` lines name the stage — build, avar2 mapping, origin relocation, grade, source transform — and why |
| 2 | Usage or load error: bad flag value, unreadable or unparsable source, missing/unreadable build config, design-only key in the config, missing axis metadata, `--write-config` failing to write its file, or a sources write the designspace cannot carry (mappings not authored, duplicate axis tags/names, a row with no input or at the axis defaults, no default source) |

A relocation that cannot fit exits **1**, not 2: the font builds, the reason
reads `origin relocation failed (degraded build — the avar2 mapping is not the
authored one)`, and the studio shows the same thing in its banner. An avar2
failure, a missing grade axis or a source-transform failure also exit 1 — a
plain fallback font is never a pass.

## Building in CI

```bash
# until 0.1.0 is on PyPI: pin the ref this page documents
pip install "git+https://github.com/agyeiagyeiagyei/avar2-studio@cleanup-2026-10"
avar2-studio doctor --strict        # fail early, with the missing piece named
avar2-studio build sources/MyFont.glyphs --config sources/MyFont-build.json \
    --workdir "$RUNNER_TEMP/avar2" --out fonts/
```

The pin matters. Unpinned, `pip install` takes GitHub's default branch (`main`,
last updated 2026-09-12), whose `build` has neither `--config` nor `--workdir`
and whose `doctor` has no `--strict` — the commands above exit 2 there. Point
the pin at whatever ref carries these flags; `avar2-studio build --help |
grep -q -- --workdir` exits 0 exactly when it does.

A font repo's workflow:

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
  with: {python-version: "3.11"}
- run: pip install "git+https://github.com/agyeiagyeiagyei/avar2-studio@cleanup-2026-10"
- run: avar2-studio doctor --strict
- run: >
    avar2-studio build sources/MyFont.glyphs
    --out fonts/ --workdir "$RUNNER_TEMP/avar2"
```

`--workdir` matters in CI: without it the tool workdir (config, shadow, build
output, relocation memo) is created beside the source, inside the checkout.
`<stem>-avar.csv` and `<stem>-axis-metadata.json` are created beside the source
either way on a first run, so a clean CI job should either commit them or
export them from the studio first.

## `avar2-studio doctor`

Reports what the build will resolve: Python, `fontc`, `gftools`, `glyphsLib`,
the static bundle, the Fontra bridge, the two native accelerators
(`spac-inject`, `libkinetikern2`), install shadowing and stale processes. It
does **not** look for `fontmake` — the build's compiler is `fontc`, and
`gftools` carries the Python-side steps. `--strict` fails only on a
**headless-build prerequisite** that is missing (`fontc`, `gftools-builder`,
`glyphsLib` — the names it prints) or on an environment override that names a
file which does not exist (`$AVAR2_SPAC_INJECT`, `$AVAR2_KK2_DYLIB`); the UI-only pieces and an
absent accelerator do not fail it — that is the code's contract (`doctor.run`),
and no workflow in this repo runs `doctor --strict`.

`doctor` finds `fontc` on `PATH`; the build also looks beside
`sys.executable`. A parenthesised note naming an override that `does not exist`
(`($AVAR2_SPAC_INJECT=… does not exist)`, `($AVAR2_KK2_DYLIB=… does not
exist)`) means an environment override is set and broken — that is what
`--strict` refuses.

## `avar2-studio install-glyphs-plugins`

Symlinks the Glyphs 3 plugins (`avar2 Studio` hub and the six design tools) into
`~/Library/Application Support/Glyphs 3/Plugins/`. Restart Glyphs afterwards;
edits in the checkout are live from then on. `--uninstall` removes the links this
command made — a link that points elsewhere, or a real directory, is left alone
and reported — and deletes the config that records the interpreter. See
[`../src/avar2_studio/glyphs/README.md`](../src/avar2_studio/glyphs/README.md).

## Environment variables

The build reads a small set of overrides (ports, accelerator binaries, the
gftools-builder safety valve); they are listed once in
[`DEVELOPMENT.md`](DEVELOPMENT.md#environment-variables) rather than here.
