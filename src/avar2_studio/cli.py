"""Console entry point for avar2-studio.

Dispatches between:

  - ``avar2-studio doctor``  → environment check (no Glyphs file required)
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

Run ``avar2-studio /path/to/MyFont.glyphs --help`` to see server options.
"""


def main() -> None:
    argv = sys.argv[1:]

    if argv and argv[0] == "doctor":
        from . import doctor
        doctor.main()
        return

    if argv and argv[0] in ("-h", "--help") and len(argv) == 1:
        print(_HELP)
        return

    # Default: dispatch to the server's main(). It reads sys.argv itself.
    from . import server
    server.main()


if __name__ == "__main__":
    main()
