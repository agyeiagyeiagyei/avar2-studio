"""Console entry point for avar2-studio.

Dispatches between:

  - ``avar2-studio doctor``  → environment check (no Glyphs file required)
  - ``avar2-studio build /path/to/MyFont.glyphs --out DIR``  → headless build
  - ``avar2-studio install-glyphs-plugins``  → link the Glyphs 3 plugins into Glyphs
  - ``avar2-studio /path/to/MyFont.glyphs [server args…]``  → run the server

Everything that isn't a known subcommand is forwarded to ``server.main()``
so the existing argparse contract continues to work.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HELP = """\
Usage:
  avar2-studio /path/to/MyFont.glyphs [options]
  avar2-studio doctor
  avar2-studio --help

Subcommands:
  doctor                  Run environment checks (fontc, gftools, frontend bundle, …)
  build                   Build the font once, headless, and copy it to --out (for CI)
  install-glyphs-plugins  Link the bundled Glyphs 3 plugins into Glyphs (--uninstall removes them)

Run ``avar2-studio /path/to/MyFont.glyphs --help`` to see server options.
"""


def main() -> None:
    argv = sys.argv[1:]

    if argv and argv[0] == "doctor":
        from . import doctor
        doctor.main()
        return

    if argv and argv[0] == "build":
        sys.exit(_build(argv[1:]))

    if argv and argv[0] == "install-glyphs-plugins":
        from . import glyphs_install
        sys.exit(glyphs_install.main(argv[1:]))

    if argv and argv[0] in ("-h", "--help") and len(argv) == 1:
        print(_HELP)
        return

    # Default: dispatch to the server's main(). It reads sys.argv itself.
    from . import server
    server.main()


def _axis_metadata_paths(source: Path):
    """Both homes for the declared axis defaults (C1).

    ``<stem>-axis-metadata.json`` beside the source is the canonical,
    COMMITTED location. ``.avar2-studio/axis-metadata.json`` is the pre-C1
    location: tool-managed and gitignored, so it only helps a checkout that
    already ran the studio.
    """
    return (
        source.parent / f"{source.stem}-axis-metadata.json",
        source.parent / ".avar2-studio" / "axis-metadata.json",
    )


def _axis_metadata_problem(source: Path):
    """None when the declared axis defaults ship with the source, else the
    message to fail with.

    Without this file the traditional axes' declared default (opsz 144 on
    Crispy) is not in the build inputs at all, so each one falls back to its
    column minimum and every named style lands off its mapped value (62 u
    measured). In CI that is a silent wrong font, not a warning.
    """
    sibling, legacy = _axis_metadata_paths(source)
    if sibling.exists() or legacy.exists():
        return None
    return (f"axis metadata not found: neither {sibling.name} nor "
            f".avar2-studio/{legacy.name} exists beside {source.name}. It holds "
            f"the declared axis defaults (opsz 144 etc.); without them named "
            f"styles land off their mapped values. Open the font in "
            f"avar2-studio once and commit {sibling.name} (or copy it from the "
            f"studio's .avar2-studio/axis-metadata.json).")


def _build(argv) -> int:
    """``avar2-studio build SOURCE --out DIR``: one headless build.

    Runs exactly the pipeline the server runs on load — sidecar bootstrap,
    control-axis shadow, grade braces, then the avar2 build — but with no web
    server, no file watcher and no Fontra. Meant for CI, where the old
    Makefile flow used to live: the studio owns the build now, so the build
    it ships is the build CI checks. Exit status is the build status.
    """
    import argparse
    import shutil
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="avar2-studio build")
    ap.add_argument("source", type=Path, help=".glyphs / .designspace source")
    ap.add_argument("--out", type=Path, required=True,
                    help="directory the built font is copied into (created)")
    ap.add_argument("--workdir", type=Path, default=None,
                    help="put the tool workdir (config, shadow, build output, "
                         "relocation memo) under DIR instead of beside the source, "
                         "so a CI checkout stays untouched")
    ap.add_argument("--csv", type=Path, default=None,
                    help="avar2 mappings CSV (default: <stem>-avar.csv beside the source)")
    args = ap.parse_args(argv)

    if args.csv is not None:
        # Dead override: _apply_source_path resets CSV_PATH and the build
        # always reads the sibling <stem>-avar.csv. Say so, don't pretend.
        print("error: --csv is no longer supported — the build reads "
              "<stem>-avar.csv next to the source file.", file=sys.stderr)
        return 2

    meta_problem = _axis_metadata_problem(args.source)
    if meta_problem:
        print(f"error: {meta_problem}", file=sys.stderr)
        return 2
    from . import server, state
    if args.workdir is not None:
        # Before _apply_source_path: it resolves the workdir from this.
        state.WORKDIR_OVERRIDE = args.workdir.resolve()
    try:
        server._apply_source_path(args.source)
    except Exception as exc:
        print(f"error: could not load {args.source}: {exc}", file=sys.stderr)
        return 2

    # The build runs in a worker subprocess (docs/decisions/0003-worker-subprocess-finish-then-rebuild.md);
    # wait for the queue to drain before reading the outcome globals.
    server.JOBS.wait_for_idle()

    if state.LAST_BUILD_STATUS != "ok" or not state.VARIABLE_FONT_PATH:
        print(f"error: build failed: {state.LAST_BUILD_ERROR or 'unknown'}", file=sys.stderr)
        return 1
    if state.LAST_AVAR2_ERROR:
        # The plain-VF fallback built, but the avar2 mapping did not. In CI
        # that is a failure, not a warning — the mapped font IS the product.
        print(f"error: avar2 build failed (plain fallback built instead): "
              f"{state.LAST_AVAR2_ERROR}", file=sys.stderr)
        return 1
    if state.LAST_GRADE_ERROR:
        # Grade is declared in the sidecar but its braces were not
        # generated: the font built, without its GRAD axis. Passing that
        # off as success is how a missing axis reaches a release.
        print(f"error: grade generation failed (the font has no GRAD axis): "
              f"{state.LAST_GRADE_ERROR}", file=sys.stderr)
        return 1
    if state.LAST_SOURCE_TRANSFORM_ERROR:
        # An enabled source-stage transform (round_corners) did not run:
        # the font built from the un-transformed source.
        print(f"error: source transform failed (the font built without it): "
              f"{state.LAST_SOURCE_TRANSFORM_ERROR}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / state.VARIABLE_FONT_PATH.name
    shutil.copy2(state.VARIABLE_FONT_PATH, dest)
    print(dest)
    return 0


if __name__ == "__main__":
    main()
