"""Sidecar persistence primitives shared by every writer.

The studio's sidecars (``-avar.csv``, ``-control.json``, ``-grade.json``,
``-transforms.json``, ``-kinetikern.json``, ``axis-metadata.json``,
``config.yaml``) are small files rewritten whole by request handlers, the
file watcher, and the job supervisor's CSV sync. Two invariants keep them
safe, and both live here so no writer can opt out:

* ``LOCK`` — one process-wide re-entrant lock around every
  read-modify-write. Without it two writers interleave (the second save
  silently drops the first one's change). Re-entrant so a locked route
  can call a locked helper. Lock order with the server's resolve lock is
  SIDECAR → RESOLVE, never the reverse.
* ``atomic_writer`` — all-or-nothing writes: the content goes to a
  temporary file in the same directory and is renamed over the target,
  so a concurrent reader (the supervisor staging a snapshot, the watcher
  parsing a save, a request) never sees a half-written file.
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator, TextIO

LOCK = threading.RLock()

# {resolved path: time of the studio's last write} — the file watcher uses
# it to tell the studio's own sidecar writes from external edits.
LAST_WRITES: dict[str, float] = {}


@contextlib.contextmanager
def atomic_writer(path: os.PathLike | str, *, encoding: str = "utf-8",
                  newline: str | None = None) -> Iterator[TextIO]:
    """``with atomic_writer(p) as f: f.write(...)`` — ``p`` is replaced
    only when the block completes; on any exception the target is left
    exactly as it was and the temporary file is removed. Holds ``LOCK``
    for the duration of the write."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
                yield f
                f.flush()
                os.fsync(f.fileno())
            try:
                mode = stat.S_IMODE(target.stat().st_mode)
            except OSError:
                mode = 0o644
            os.chmod(tmp, mode)
            os.replace(tmp, target)
            LAST_WRITES[str(target.resolve())] = time.time()
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


def write_text(path: os.PathLike | str, text: str, *, encoding: str = "utf-8",
               newline: str | None = None) -> None:
    with atomic_writer(path, encoding=encoding, newline=newline) as f:
        f.write(text)


def write_json(path: os.PathLike | str, data: Any, *, indent: int = 2,
               ensure_ascii: bool = False, trailing_newline: bool = True) -> None:
    text = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    if trailing_newline:
        text += "\n"
    write_text(path, text)
