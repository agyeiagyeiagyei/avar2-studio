"""Entry point for the Fontra subprocess the studio spawns.

Imports the fontra-glyphs monkeypatch (control-axis brace-layer
editability — see ``_fontra_patch``) before handing off to Fontra's own
CLI ``main``. Launched as::

    python -m avar2_studio._fontra_launch --http-port <n> filesystem <dir>

Fontra parses ``sys.argv[1:]`` as usual, so the args pass straight
through; the only difference from invoking the ``fontra`` binary
directly is that the patch is applied first.
"""

from . import _fontra_patch  # noqa: F401  — applies the patch on import


def main() -> None:
    from fontra.__main__ import main as fontra_main

    fontra_main()


if __name__ == "__main__":
    main()
