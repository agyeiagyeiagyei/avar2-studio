"""Watchdog lifecycle for the studio's source files (audit §3 step f).

server.py owns the app + ``main()``; this module owns the file watcher:
the event handler (classification, debounce, suppression, settle, dedup,
the mid-build and idle branches) and a ``SourceWatcher`` with explicit
``start``/``stop``/``reschedule`` — so ``/api/load-source`` can re-point
watching at a swapped-in source (the old TODO), and ``main()`` shrinks to
wiring.

Handlers call back into server.py through late-bound ``server.X``
attribute access (this module is imported while server is mid-import),
read shared state via ``state.X``, and resolve via pipeline.py.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Optional

from . import build_jobs as _build_jobs
from . import control_axes as _control_axes
from . import pipeline as _pipeline
from . import (
    server,  # late-bound: handlers call server.JOBS/trigger_build/…
    state,
)
from . import sidecar_io as _sidecar_io
from .studio_log import get_logger

log = get_logger(__name__)

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    Observer = None
    FileSystemEventHandler = None
    WATCHDOG_AVAILABLE = False


def _capture_shadow_drawings() -> int:
    """Pull hand-drawn brace outlines from the shadow into the sidecar.

    Fontra sessions for studio axes edit the SHADOW (the open-editor
    routing serves ``shadow_path_for(ORIGINAL_PATH)``), so without this a
    drawing waits for the next config export to reach the sidecar — the
    only durable copy — and a shadow wipe or an unstaged build loses it
    before then. Untouched seeds are skipped by their stamps, so this is
    a cheap no-op after server-side regens. Returns layers captured.
    """
    if state.ORIGINAL_PATH is None or state.GLYPHS_PATH is None:
        return 0
    if Path(state.GLYPHS_PATH).resolve() == Path(state.ORIGINAL_PATH).resolve():
        return 0  # no shadow in play — Fontra is editing the original itself
    with _sidecar_io.LOCK:
        return _control_axes.capture_outlines(state.ORIGINAL_PATH)


def _wait_for_save_to_settle(path: Path, settle: float = 0.5, timeout: float = 30.0) -> None:
    """Block until ``path``'s mtime has stopped moving for ``settle`` seconds.

    A multi-second glyphsLib/Fontra save fires its first watchdog event
    mid-write; acting on that event reads a half-written file ("Unexpected
    EOF while parsing plist"). Runs on the watchdog thread — never on a
    request path.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return  # vanished mid-dance (save rename) — let the caller's parse say so
        if age >= settle:
            return
        time.sleep(min(settle - age, 0.5))


def _resolve_source_after_save(path: Path) -> None:
    """Resolve the active source once a just-saved file is complete: wait for
    the write to settle, then resolve, retrying once — OS event delivery can
    still lag a long save. Final failures keep the established warning."""
    _wait_for_save_to_settle(path)
    for attempt in (0, 1):
        try:
            _pipeline._resolve_active_source()
            return
        except Exception as exc:
            if attempt:
                log.warning(f"Warning: shadow regeneration failed: {exc}")
            else:
                _wait_for_save_to_settle(path, settle=1.0)


_SIDECAR_SUFFIXES = tuple(_build_jobs.SIDECAR_SUFFIXES)


def _classify_watched_path(src: Path) -> Optional[str]:
    """Which studio file a watchdog event belongs to: "source" (the active
    build source — the transformed copy when source transforms are on),
    "original" (the designer's own file), "shadow" (the control-axis shadow
    that studio-axis Fontra sessions save into), "sidecar" (a studio data
    file edited outside the studio — git checkout, a hand edit), or "glif"
    (inside a designspace shadow's pooled UFOs). None — not ours; ignore the
    event.

    The shadow needs its own class: with transforms on, GLYPHS_PATH is the
    transformed/ copy, so a Fontra save to shadow/<name> is neither source
    nor original and would go unseen (T0.9).
    """
    if state.GLYPHS_PATH is None:
        return None
    parts = src.parts
    if ".avar2-studio" in parts and "jobs" in parts[parts.index(".avar2-studio"):]:
        # Build-job snapshots live under the watched tree (recursively, for
        # designspace sources): staging copies UFOs there and the worker
        # regenerates pooled UFOs inside the job dir. Reacting to those
        # .glif writes re-queued a build per job, forever.
        return None
    resolved = src.resolve()
    if resolved == Path(state.GLYPHS_PATH).resolve():
        return "source"
    if state.ORIGINAL_PATH is not None:
        original = Path(state.ORIGINAL_PATH)
        if resolved == original.resolve():
            return "original"
        if resolved == _control_axes.shadow_path_for(original).resolve():
            return "shadow"
        if resolved.parent == original.parent.resolve() and resolved.name in {
                f"{original.stem}{suffix}" for suffix in _SIDECAR_SUFFIXES}:
            return "sidecar"
    if str(state.GLYPHS_PATH).lower().endswith(".designspace") \
            and src.suffix.lower() in (".glif", ".plist"):
        return "glif"
    return None


class GlyphsFileHandler(FileSystemEventHandler if FileSystemEventHandler is not None else object):
    """Watchdog handler for real-time Glyphs file changes."""

    def __init__(self):
        # Per-PATH: a Glyphs save is a write + rename (two events, ms
        # apart) and must fold to one build; but the server's own shadow
        # write arrives in the same drain as a designer's save that
        # landed mid-build, and with one shared timestamp the shadow's
        # events spent the throttle budget and the save was dropped.
        self.last_modified: Dict[str, float] = {}
        # First-processed-event-per-final-mtime: a multi-second save
        # fires several events as the file is written.
        self.settled_mtime: Dict[str, float] = {}
        self.debounce_interval = 0.5  # seconds

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(Path(event.src_path))

    def on_created(self, event):
        # Editors and git materialise a file as create (+ modify); a plain
        # on_modified-only handler could miss a restored sidecar.
        if not event.is_directory:
            self._handle(Path(event.src_path))

    def on_moved(self, event):
        # Atomic saves (write temp + rename) surface as a move onto the
        # watched name on some platforms; the destination is what changed.
        if not event.is_directory:
            self._handle(Path(event.dest_path))

    def _handle(self, src: Path):
        """One settled, de-duplicated reaction per external change to a
        watched file; the server's own writes are filtered out first."""

        # Relevant changes: the active build source (sync + rebuild),
        # the designer's ORIGINAL (GLYPHS_PATH is the SHADOW once a
        # secondary axis has layers — comparing only against it meant
        # edits to the real source never fired: deleting an instance in
        # Glyphs left the shadow stale), the shadow itself (Fontra saves
        # for studio axes — a separate class because GLYPHS_PATH is the
        # transformed/ copy when source transforms are on), and — for an
        # active .designspace shadow — .glif edits inside its UFOs.
        event_kind = _classify_watched_path(src)
        if event_kind is None:
            return
        is_source = event_kind == "source"
        is_original = event_kind == "original"
        is_shadow = event_kind == "shadow"
        is_sidecar = event_kind == "sidecar"

        # Debounce rapid saves
        current_time = time.time()
        key = str(src.resolve())
        if current_time - self.last_modified.get(key, 0.0) < self.debounce_interval:
            return
        self.last_modified[key] = current_time

        # Server-initiated write (instance edit / shadow regen / transform
        # save): the triggering code already queued its rebuild — a
        # watcher-driven sync+build here would duplicate it. Two guards:
        # the time window covers the in-flight write; the mtime watermark
        # covers events PROCESSED late — the handler thread can be busy in
        # a multi-second resolve when the time window expires, and a late
        # self-event then resolved again and re-queued the build forever
        # (the T0.7 round-1 storm). The Glyphs.app instance-edit
        # round-trip writes the ORIGINAL and arms the second watermark.
        try:
            src_mtime = src.stat().st_mtime
        except OSError:
            src_mtime = None
        if is_sidecar:
            # Every studio edit rewrites a sidecar through sidecar_io, which
            # stamps the write; only a LATER mtime is an external edit (git
            # checkout, a hand edit, another tool) worth regenerating for.
            if src_mtime is not None and src_mtime <= _sidecar_io.LAST_WRITES.get(key, 0.0):
                return
        elif is_original:
            if current_time < state._SUPPRESS_ORIGINAL_UNTIL or (
                    src_mtime is not None
                    and src_mtime <= state._SELF_WRITE_ORIGINAL_WATERMARK):
                return
        elif current_time < state._SUPPRESS_WATCHDOG_UNTIL or (
                src_mtime is not None and src_mtime <= state._SELF_WRITE_WATERMARK):
            return

        # A multi-second save fires several events as the file is written;
        # settle, then process only the first event per final mtime.
        _wait_for_save_to_settle(src)
        try:
            settled_mtime = src.stat().st_mtime
        except OSError:
            settled_mtime = None
        if settled_mtime is not None and settled_mtime == self.settled_mtime.get(key):
            return
        self.settled_mtime[key] = settled_mtime

        if is_sidecar:
            log.info(f"{src.name} changed outside the studio, regenerating...")
            server.schedule_shadow_rebuild()
            return

        if state.JOBS.is_building():
            # Do NOT drop the save. Record what changed so the queue's
            # next pass re-syncs the CSV and re-stages the source before
            # rebuilding; a rebuild from the pre-save snapshot would look
            # like success while serving stale outlines.
            kind = "original" if is_original else ("source" if (is_source or is_shadow) else None)
            if kind:
                if is_source or is_shadow:
                    # A Fontra save writes the SHADOW: secure any drawing
                    # into the sidecar before the resolve below regens it.
                    try:
                        _capture_shadow_drawings()
                    except Exception as exc:
                        log.warning(f"Warning: could not capture outlines: {exc}")
                # Re-resolve NOW so the read endpoints (/api/instances,
                # /api/axes, coverage) stop serving the pre-save shadow
                # while the current build is still running; the queue
                # re-resolves before staging the follow-up regardless.
                _resolve_source_after_save(src)
                state.JOBS.request_build(source_refresh=kind)
            else:
                state.JOBS.request_build()
            log.info("Source changed mid-build; queued for after this build.")
            return

        try:
            if is_original:
                # The user's own file changed. Re-derive the active source
                # FROM it so masters, instances and glyph edits propagate;
                # without this the shadow keeps serving a stale copy.
                # Go through _resolve_active_source, NOT regenerate_shadow:
                # the latter rebuilds only the control-axis shadow, so every
                # save in Glyphs silently dropped the grade braces (and the
                # GRAD axis with them) until an unrelated grade edit ran.
                log.info("Original source modified, regenerating shadow...")
                _resolve_source_after_save(src)
                server.sync_csv_with_glyphs()
            elif is_source or is_shadow:
                log.info("Source file modified, syncing CSV and rebuilding...")
                # A Fontra save writes the SHADOW: pull hand-drawn brace
                # outlines into the sidecar first, so drawings are durable
                # even if the shadow is later regenerated away. Untouched
                # seeds are skipped by their stamps — a cheap no-op after
                # server-side regens.
                try:
                    captured = _capture_shadow_drawings()
                    if captured:
                        log.info(f"Captured {captured} drawn outline(s) into the sidecar.")
                except Exception as exc:
                    log.warning(f"Warning: could not capture outlines: {exc}")
                # Sync CSV first (skips editing instances)
                server.sync_csv_with_glyphs()
            else:
                log.info("UFO glyph edited (Fontra), rebuilding...")
            server.trigger_build()
        except Exception as e:
            log.error(f"Error handling file change: {e}")


class SourceWatcher:
    """The watchdog Observer + handler as one lifecycle object.

    ``start()`` watches the active source set (the build source's dir, the
    original's dir when different, and the shadow dir when the active
    source is the transformed/ copy); ``stop()`` shuts it down;
    ``reschedule()`` re-points after a source swap — ``/api/load-source``
    calls it, which is the audit's long-standing TODO.
    """

    def __init__(self):
        self._observer = None
        self._handler = None

    def start(self) -> bool:
        """Start watching the active source set. Returns False when there's
        nothing to do (blind launch) or watchdog is unavailable — the caller
        (main) owns the periodic-check fallback for the latter."""
        if not WATCHDOG_AVAILABLE or state.GLYPHS_PATH is None:
            return False
        self.stop()
        self._handler = GlyphsFileHandler()
        observer = Observer()
        # .designspace shadows need recursion: Fontra writes .glif
        # files inside the pooled UFO directories.
        observer.schedule(
            self._handler,
            path=str(state.GLYPHS_PATH.parent),
            recursive=state.GLYPHS_PATH.suffix.lower() == ".designspace",
        )
        # Watch the ORIGINAL's directory too when the build source is the
        # shadow — they live in different directories, and edits to the real
        # file are the ones the designer actually makes.
        try:
            if state.ORIGINAL_PATH is not None:
                orig_dir = Path(state.ORIGINAL_PATH).parent.resolve()
                if orig_dir != state.GLYPHS_PATH.parent.resolve():
                    observer.schedule(self._handler, path=str(orig_dir), recursive=False)
        except Exception as exc:
            log.warning(f"Warning: could not watch the original source: {exc}")
        # And the shadow directory itself when the active source is the
        # transformed/ copy (source transforms on): studio-axis Fontra
        # sessions save shadow/<name>, which the GLYPHS_PATH watch —
        # transformed/ only — never sees (T0.9). When transforms are off
        # GLYPHS_PATH IS the shadow and its dir is already watched.
        try:
            if (state.ORIGINAL_PATH is not None
                    and Path(state.GLYPHS_PATH).resolve() != Path(state.ORIGINAL_PATH).resolve()):
                shadow_dir = _control_axes.shadow_dir_for(state.ORIGINAL_PATH)
                if shadow_dir.exists() \
                        and shadow_dir.resolve() != Path(state.GLYPHS_PATH).parent.resolve():
                    observer.schedule(self._handler, path=str(shadow_dir), recursive=False)
        except Exception as exc:
            log.warning(f"Warning: could not watch the shadow dir: {exc}")
        observer.start()
        self._observer = observer
        state.OBSERVER = observer
        log.info(f"Real-time file watching enabled: watching {state.GLYPHS_PATH}")
        return True

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            finally:
                self._observer = None
                state.OBSERVER = None

    def reschedule(self) -> bool:
        """Re-point at the CURRENT state.GLYPHS_PATH/ORIGINAL_PATH — called
        by /api/load-source after a source swap."""
        return self.start()


#: The one process-wide watcher. ``main()`` starts it; routes/load.py
#: reschedules it; both go through this object so tests can substitute.
WATCHER = SourceWatcher()
