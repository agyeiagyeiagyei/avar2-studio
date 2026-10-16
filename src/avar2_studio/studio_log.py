"""Logging for the studio's own messages.

Every module logs through ``get_logger(__name__)``; the one handler writes
to whatever ``sys.stderr`` is at emit time (so test capture and
redirected launches both see it) with the plain message, which is what
the studio has always printed. Werkzeug's request log is untouched.
"""
from __future__ import annotations

import logging
import sys

ROOT = "avar2_studio"


class _LiveStderrHandler(logging.StreamHandler):
    """StreamHandler that re-reads sys.stderr on every emit."""

    def __init__(self):
        super().__init__(stream=sys.stderr)

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):  # StreamHandler assigns it in __init__ — ignore
        pass


def _configure() -> logging.Logger:
    root = logging.getLogger(ROOT)
    if not any(isinstance(h, _LiveStderrHandler) for h in root.handlers):
        handler = _LiveStderrHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)
        root.propagate = False
        root.setLevel(logging.INFO)
    return root


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(name if name.startswith(ROOT) else f"{ROOT}.{name}")
