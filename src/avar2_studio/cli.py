"""Console entry point for avar2-studio.

For Phase 1 (lift-and-shift) this is a thin wrapper around the original
server's ``main()`` so the existing argparse contract still works. Phase 2
will reshape the CLI to accept a positional ``.glyphs`` path and own its
own argument parsing.
"""

from . import server


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
