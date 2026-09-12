"""Console entry point for avar2-studio.

Dispatches between:

  - ``avar2-studio doctor``  → environment check (no Glyphs file required)
  - ``avar2-studio build /path/to/MyFont.glyphs --out DIR``  → headless build
  - ``avar2-studio /path/to/MyFont.glyphs [server args…]``  → run the server

Everything that isn't a known subcommand is forwarded to ``server.main()``
so the existing argparse contract continues to work.
"""

from __future__ import annotations

import sys


_HELP = """\
Usage:
  avar2-studio /path/to/MyFont.glyphs [options]
  avar2-studio doctor
  avar2-studio --help

Subcommands:
  doctor   Run environment checks (fontc, gftools, frontend bundle, …)
  build    Build the font once, headless, and copy it to --out (for CI)

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

    if argv and argv[0] in ("-h", "--help") and len(argv) == 1:
        print(_HELP)
        return

    # Default: dispatch to the server's main(). It reads sys.argv itself.
    from . import server
    server.main()


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
    ap.add_argument("--csv", type=Path, default=None,
                    help="avar2 mappings CSV (default: <stem>-avar.csv beside the source)")
    args = ap.parse_args(argv)

    from . import server
    if args.csv is not None:
        server.CSV_PATH = args.csv.resolve()
    try:
        server._apply_source_path(args.source)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not load {args.source}: {exc}", file=sys.stderr)
        return 2

    if server.LAST_BUILD_STATUS != "ok" or not server.VARIABLE_FONT_PATH:
        print(f"error: build failed: {server.LAST_BUILD_ERROR or 'unknown'}", file=sys.stderr)
        return 1
    if server.LAST_AVAR2_ERROR:
        # The plain-VF fallback built, but the avar2 mapping did not. In CI
        # that is a failure, not a warning — the mapped font IS the product.
        print(f"error: avar2 build failed (plain fallback built instead): "
              f"{server.LAST_AVAR2_ERROR}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / server.VARIABLE_FONT_PATH.name
    shutil.copy2(server.VARIABLE_FONT_PATH, dest)
    print(dest)
    return 0


if __name__ == "__main__":
    main()
