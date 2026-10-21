"""Mutable runtime state extracted from ``server.py`` (audit §3 step b).

Pure values only — no imports from this package, so nothing here can
cycle. Every reader/writer (server.py, pipeline.py, build_jobs.py, the
build worker, tests) goes through ``state.X``; never ``from .state import
X`` — that binds a copy that goes stale on the next assignment.
"""

from pathlib import Path
from typing import Dict, Optional

GLYPHS_PATH: Optional[Path] = None  # The ACTIVE source — what reads + builds operate on. When CONTROL AXES are declared, this points at the shadow .glyphs; otherwise it's the same as ORIGINAL_PATH. Name kept for git history; SOURCE_FORMAT tracks which.
ORIGINAL_PATH: Optional[Path] = None  # The ORIGINAL source the user pointed at. Sidecar paths and push-to-source target this. Studio NEVER writes to it directly for control-axis work (shadow staging only).

# Fontra subprocess management — v2.5a launches the Fontra editor as
# a child process pointed at the shadow folder so we can iframe it in
# the studio UI. Started lazily on first "open in editor" request and
# kept warm across opens. Killed on shutdown via atexit.
FONTRA_PROCESS: Optional[object] = None
FONTRA_PORT: int = 8001
# Per-session state for the embedded editor, set by open-editor and
# read by the proxy's CSS builder + the injected shim (via
# /api/fontra-shim-config). Studio sessions get the trimmed
# multi-source panel; source-derived sessions keep the bare canvas.
FONTRA_EDITOR_SESSION: Optional[dict] = None
FONTRA_CONTENT_ROOT: Optional[Path] = None
# mtime of the shadow .glyphs Fontra was spawned against. If the file
# is rewritten (a layer edit regenerates the shadow), Fontra's backend
# still serves the cached/old glyph, so we restart it on next open.
FONTRA_SHADOW_MTIME: Optional[float] = None
SOURCE_FORMAT: Optional[str] = None  # "glyphs" | "designspace"
BUILD_DIR: Optional[Path] = None
VARIABLE_FONT_PATH: Optional[Path] = None  # Last-good built font; only updated after a successful build
LAST_BUILD_TIME: Optional[float] = None
# Re-entrancy guard inside the build WORKER only (a fresh process per job —
# see docs/archive/WORKER_BUILD_PLAN.md): _run_build/_perform_avar2_build manage it
# there. The server process never runs builds; request threads read
# JOBS.is_building() instead.
BUILDING: bool = False
# Outcome of the most recent build attempt. ``ok`` => current preview reflects
# fresh source state. ``failed`` => the preview is stale (we're still serving
# the last-good font; the user's most recent edit didn't make it through).
LAST_BUILD_STATUS: Optional[str] = None  # "ok" | "failed" | None
LAST_BUILD_ERROR: Optional[str] = None   # human-readable detail for "failed"
# Set when the avar2 build failed and the plain-VF fallback is being
# served instead. The fallback keeps LAST_BUILD_STATUS "ok", which made
# avar2 failures invisible — the preview silently lost its mapped axes.
LAST_AVAR2_ERROR: Optional[str] = None
# Set when the built font could not be relocated onto the ORIGIN row: the
# avar2 font is still served (all axes, avar2 table) but its default is the
# compiled origin, so LAST_BUILD_STATUS reports "degraded", not "ok".
LAST_ORIGIN_RELOCATION_ERROR: Optional[str] = None
# "hit"/"miss": whether the last build reused a memoised relocation. Carried
# across the worker boundary for the same reason as the error above — the
# worker's own log is discarded, so the field IS the evidence.
LAST_ORIGIN_RELOCATION_MEMO: Optional[str] = None
# The AUTHORED mapping (axes triples + avar segments) with the parametric
# defaults moved onto the ORIGIN row's out, computed only when the relocation
# failed: the served table is anchored on the compiled origin, so this is what
# /api/mapped-location must evaluate (S1(c)).
LAST_ORIGIN_AUTHORED_AVAR: Optional[Dict] = None
# Set when grade is declared but its braces could not be generated — the
# built font is then missing its GRAD axis, which must not pass as ok.
LAST_GRADE_ERROR: Optional[str] = None
# Set when an enabled SOURCE-stage transform (round_corners) failed: the
# font then built from the un-transformed source, which must not pass as ok.
LAST_SOURCE_TRANSFORM_ERROR: Optional[str] = None
# Non-null ⇒ the mappings define styles but NO row sits at the axis
# defaults: the font rests at the compiled origin rather than any
# authored style (the "resting dip" — e.g. opsz default 48 with rows
# only at 14 and 144). Surfaced as a banner; the build itself is fine.
LAST_ORIGIN_WARNING: Optional[str] = None
# Set when an ENABLED post-build transform failed during the last build (the
# base font still compiled, so LAST_BUILD_STATUS stays "ok", but the requested
# transform — e.g. SPAC — was skipped). Surfaced in /api/health + the
# transforms PUT so the UI can flag an enabled-but-failing transform.
LAST_TRANSFORM_ERROR: Optional[str] = None
# sync_csv_with_glyphs is a closure inside server.main(); it registers here
# so the job supervisor can run it before staging a build whose source
# changed mid-flight.
_SYNC_CSV_HOOK = None
# Count of background regen/rebuild tasks in flight. The sidecar write returns
# immediately so the UI stays responsive, but the shadow regen + font build
# take seconds — /api/health folds this into `building` so the frontend can
# show the preview catching up.
BACKGROUND_WORK: int = 0

# Watchdog suppression windows and self-write watermarks — armed by
# server._suppress_watcher around server-initiated writes; the mechanism
# and the window-length constants live in server.py.
_SUPPRESS_WATCHDOG_UNTIL: float = 0.0
_SUPPRESS_ORIGINAL_UNTIL = 0.0
_SELF_WRITE_WATERMARK = 0.0
_SELF_WRITE_ORIGINAL_WATERMARK = 0.0

# The build/export job queue, constructed by server.py at import time
# (server.JOBS is the same object; pipeline.py reaches it via this slot so
# it never has to import server).
JOBS = None

# Debounce timer for the shadow-regen + rebuild burst coalescing in
# server.schedule_shadow_rebuild.
_REBUILD_TIMER = None
OBSERVER = None  # Optional[watchdog.observers.Observer] — the source file watcher
CSV_PATH: Optional[Path] = None  # Path to avar2-mappings.csv
USE_FONTC: bool = True  # Use fontc by default, fallback to fontmake
PREVIEW_DIR: Optional[Path] = None  # Per-project workdir (sibling .avar2-studio/)
# Relocation memo: the stable directory jobs write it to. A worker builds in a
# throwaway job dir (only the font and the config are promoted back), so the
# memo has to be told where to live or it dies with the job.
RELOCATION_MEMO_DIR: Optional[Path] = None
PREVIEW_CSV_PATH: Optional[Path] = None  # Path to preview CSV (sibling to .glyphs)
PREVIEW_CONFIG_PATH: Optional[Path] = None  # Path to preview config (.avar2-studio/config.yaml)
EDITING_INSTANCES: set = set()  # Track instances currently being edited (protected from sync)

# Track CSV modification times for external edit detection
_CSV_MODIFICATION_TIMES: Dict[str, float] = {}

# Cached TTFont for /api/mapped-location — reparsing the built font per
# slider tick would dwarf the actual avar evaluation. Keyed on
# (path, mtime) so rebuilds invalidate naturally.
_MAPPED_FONT_CACHE: Dict[str, object] = {"key": None, "font": None}

_SOURCE_GLYPH_SIGS: "tuple" = (None, {})   # (source mtime_ns, {glyph: sig})

# One computation per input version: a single build consults
# _declared_axis_ranges from three sites (the shim env via _builder_env,
# the default-row detection and the fvar-axes fix in _build_in_process),
# each of which used to re-read axis-metadata.json + re-parse the source.
_DECLARED_RANGES_CACHE: Dict[tuple, Dict[str, list]] = {}
