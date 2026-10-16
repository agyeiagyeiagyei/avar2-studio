#!/usr/bin/env python3
"""
glyphs-preview-server.py

Backend server for Glyphs file preview tool.
Provides API endpoints to:
- Read instances from Glyphs file
- Build variable font
- Extract axes from built font
- Update instance coordinates in Glyphs file
- Serve font files
"""

import argparse
import contextlib
import csv
import gc
import json
import os
import shutil
import subprocess
import io
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    print("Warning: PyYAML not found. Install with: pip install pyyaml", file=sys.stderr)
    yaml = None

from flask import Flask, jsonify, request, send_file, send_from_directory, Response, stream_with_context
from werkzeug.utils import secure_filename
from flask_cors import CORS
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    # Create dummy types for type hints when watchdog is not available
    Observer = None
    FileSystemEventHandler = None
    print("Warning: watchdog not available. Auto-rebuild on file save disabled.", file=sys.stderr)
    print("Install with: pip install watchdog", file=sys.stderr)

try:
    from glyphsLib import GSFont, load
except ImportError:
    print("Error: glyphsLib not found. Install with: pip install glyphsLib", file=sys.stderr)
    sys.exit(1)

from . import source_font as _source_font
from . import csv_io as _csv_io
from . import glyph_coverage as _glyph_coverage
from . import control_axes as _control_axes
from . import instance_aliases as _instance_aliases
from . import config_port as _config_port
from . import transforms as _transforms
from . import grade as _grade
from . import kinetikern as _kinetikern
from . import grade_shadow as _grade_shadow
from . import build_jobs as _build_jobs
from .source_font import UnsupportedSourceFormat

app = Flask(__name__, static_folder=None)
# ``static_folder=None`` disables Flask's default ``/static/<path>``
# handler. Without that, Flask's auto-registered ``static`` endpoint
# shadows our custom ``/static/<path>`` route below and the CRA
# bundle's JS/CSS would 404.
CORS(app)  # Enable CORS for React frontend (used during dev when the
           # React dev server runs on a separate port from the API)

# flask-sock is used for the WebSocket leg of the Fontra reverse
# proxy (v2 slice 6 focused UI). Fontra's frontend computes its ws
# URL from ``window.location.host + "/websocket"``; when we serve
# Fontra same-origin via the proxy, we have to forward ws traffic
# too.
from flask_sock import Sock
sock = Sock(app)

# Directory of the bundled React build that gets shipped inside the
# wheel (populated by the Release CI). Routes below serve it under
# the same origin as the API so the installed package is a single
# self-contained command — no separate ``serve``/``npm start`` step.
_BUNDLE_DIR = Path(__file__).parent / "static"


@app.route('/')
def _serve_ui_index():
    index_html = _BUNDLE_DIR / "index.html"
    if not index_html.exists():
        return (
            "<h1>Frontend bundle not present</h1>"
            "<p>This install of avar2-studio is missing the React bundle. "
            "Run <code>avar2-studio doctor</code> for setup help, or build "
            "the frontend yourself (<code>cd frontend &amp;&amp; npm ci &amp;&amp; "
            "npm run build</code>) if you cloned from source.</p>",
            503,
        )
    return send_file(str(index_html))


@app.route('/static/<path:filename>')
def _serve_ui_static_asset(filename):
    """Vite-built JS/CSS/images, served from the bundle root.

    The CRA build nested assets under ``static/static/``; the Vite bundle
    keeps them in ``static/assets/`` with URLs already rooted at
    ``/static/`` (vite.config's ``base``), so the route serves the bundle
    directory itself."""
    return send_from_directory(str(_BUNDLE_DIR), filename)


@app.route('/asset-manifest.json')
def _serve_ui_asset_manifest():
    return send_from_directory(str(_BUNDLE_DIR), "asset-manifest.json")


@app.route('/manifest.json')
def _serve_ui_manifest():
    target = _BUNDLE_DIR / "manifest.json"
    if not target.exists():
        return jsonify({}), 404
    return send_from_directory(str(_BUNDLE_DIR), "manifest.json")


@app.route('/favicon.ico')
def _serve_ui_favicon():
    target = _BUNDLE_DIR / "favicon.ico"
    if not target.exists():
        return ('', 204)  # silent: no favicon in the bundle is fine
    return send_from_directory(str(_BUNDLE_DIR), "favicon.ico")

# Global state
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
# see docs/WORKER_BUILD_PLAN.md): _run_build/_perform_avar2_build manage it
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

# Registered-axis conventions, used wherever a declared axis has no
# explicit default: the /api/avar2/axes response AND the compiled-axis
# ranges handed to the gen-avar2 shim. One table so the sliders and
# the built fvar can never disagree about where "default" is.
TRADITIONAL_AXIS_DEFAULTS = {
    "wght": 400.0,
    "wdth": 100.0,
    "opsz": 72.0,
    "cntr": 0.0,
    "slnt": 0.0,
    "ital": 0.0,
    "grad": 0.0,
    "spac": 0.0,
}
# Set when an ENABLED post-build transform failed during the last build (the
# base font still compiled, so LAST_BUILD_STATUS stays "ok", but the requested
# transform — e.g. SPAC — was skipped). Surfaced in /api/health + the
# transforms PUT so the UI can flag an enabled-but-failing transform.
LAST_TRANSFORM_ERROR: Optional[str] = None
# sync_csv_with_glyphs is a closure inside main(); it registers here so the
# job supervisor can run it before staging a build whose source changed
# mid-flight.
_SYNC_CSV_HOOK = None
# Count of background regen/rebuild tasks in flight. The sidecar write returns
# immediately so the UI stays responsive, but the shadow regen + font build
# take seconds — /api/health folds this into `building` so the frontend can
# show the preview catching up.
BACKGROUND_WORK: int = 0
# Serializes read-modify-write on the control-axis sidecar so two concurrent
# edits can't interleave and drop each other's layers.
SIDECAR_LOCK = threading.Lock()
# Serializes _resolve_active_source: it rewrites the shadow + transformed
# copy and re-points GLYPHS_PATH, and is called from request threads, the
# debounce timer, the watchdog thread and the job supervisor — two
# overlapping resolves interleave those writes (measured in the T0.7
# verifier round). Never taken while holding SIDECAR_LOCK, and nothing
# holding it may take SIDECAR_LOCK (the config import nests
# SIDECAR→RESOLVE; keep that the only order).
_RESOLVE_LOCK = threading.RLock()

# Watchdog suppression window: while time.time() < this value, the file
# watcher ignores source-file modifications. Set around SERVER-INITIATED
# writes (instance rename/update/delete, shadow regen) — those changes
# already carry their own rebuild, and letting the watcher fire on them
# produced a redundant CSV sync plus a second full build per edit (the
# "saved, saved, rebuilt, rebuilt" sequence). External saves from
# Glyphs.app outside the window still sync + rebuild as before.
_SUPPRESS_WATCHDOG_UNTIL: float = 0.0
_SUPPRESS_WATCHDOG_SECONDS = 2.5   # outlast the server's own shadow write (~1s), no more
# The one server path that writes the designer's ORIGINAL is the instance-edit
# round-trip through Glyphs.app (save / close / reopen via AppleScript), which
# can take several seconds. It gets its own window so a designer's save is
# never held hostage to the shadow-regen window above: the two files are
# written by different things and must be suppressed for different reasons.
_SUPPRESS_ORIGINAL_SECONDS = 6.0
_SUPPRESS_ORIGINAL_UNTIL = 0.0
# mtime watermarks, armed at the same sites as the windows above. The time
# window alone fails when the watchdog handler is BUSY (a multi-second
# resolve): the self-write's event is processed after the window expired
# and re-fires the handler, which resolved again and re-queued the build —
# the T0.7 round-1 storm. The watermark suppresses an event whenever the
# file's mtime is not newer than the last server-initiated write, no matter
# how late the event is processed; a designer's save always re-writes the
# file, so its mtime is newer and the event goes through.
_SELF_WRITE_WATERMARK = 0.0
_SELF_WRITE_ORIGINAL_WATERMARK = 0.0


def _suppress_watcher(original: bool = False) -> None:
    """Mark a server-initiated write for watcher suppression: the time
    window covers the in-flight write (mtime still moving), the watermark
    covers events processed late. Arm AFTER the write when possible."""
    global _SUPPRESS_WATCHDOG_UNTIL, _SUPPRESS_ORIGINAL_UNTIL
    global _SELF_WRITE_WATERMARK, _SELF_WRITE_ORIGINAL_WATERMARK
    now = time.time()
    if original:
        _SUPPRESS_ORIGINAL_UNTIL = now + _SUPPRESS_ORIGINAL_SECONDS
        _SELF_WRITE_ORIGINAL_WATERMARK = now
    else:
        _SUPPRESS_WATCHDOG_UNTIL = now + _SUPPRESS_WATCHDOG_SECONDS
        _SELF_WRITE_WATERMARK = now
# Debounced shadow-regen + rebuild. Layer edits arrive in bursts (clicking ✕ a
# few times, adding several glyphs); regenerating the shadow and recompiling
# per edit costs seconds each and would queue up. Coalesce a burst into one job.
_REBUILD_TIMER = None
_REBUILD_TIMER_LOCK = threading.Lock()
_REBUILD_DEBOUNCE_SECONDS = 0.4    # sized for a ~1s in-process build, not the old 9s one
OBSERVER: Optional[Observer] = None
CSV_PATH: Optional[Path] = None  # Path to avar2-mappings.csv
USE_FONTC: bool = True  # Use fontc by default, fallback to fontmake
PREVIEW_DIR: Optional[Path] = None  # Per-project workdir (sibling .avar2-studio/)
PREVIEW_CSV_PATH: Optional[Path] = None  # Path to preview CSV (sibling to .glyphs)
PREVIEW_CONFIG_PATH: Optional[Path] = None  # Path to preview config (.avar2-studio/config.yaml)
EDITING_INSTANCES: set = set()  # Track instances currently being edited (protected from sync)

# The build/export job queue: all heavy pipeline work runs in worker
# SUBPROCESSES (docs/WORKER_BUILD_PLAN.md) so a build can never starve
# request threads. trigger_build submits; builds coalesce through it.
# Bound to this module so the supervisor can read stage-time paths and
# apply result state (VARIABLE_FONT_PATH, LAST_*).
JOBS = _build_jobs.JobQueue(sys.modules[__name__])


def _check_glyphs_file_unsaved_changes(glyphs_path: Path) -> bool:
    """
    Check if Glyphs file is open in Glyphs.app and has unsaved changes.
    
    Returns True if file has unsaved changes, False otherwise.
    """
    if sys.platform != 'darwin':
        # Only works on macOS with Glyphs.app
        return False
    
    try:
        abs_path = glyphs_path.resolve()
        
        applescript = f'''
        tell application "Glyphs"
            try
                set docPath to POSIX file "{abs_path}" as alias
                set openDocs to documents whose path is (docPath as string)
                set docCount to count of openDocs
                
                if docCount > 0 then
                    repeat with aDoc in openDocs
                        tell aDoc
                            if modified then
                                return true
                            end if
                        end tell
                    end repeat
                end if
                return false
            on error
                return false
            end try
        end tell
        '''
        
        result = subprocess.run(
            ['osascript', '-e', applescript],
            capture_output=True,
            text=True,
            timeout=5,
            check=False
        )
        
        if result.returncode == 0:
            return result.stdout.strip().lower() == 'true'
        return False
    except Exception as e:
        print(f"Warning: Could not check Glyphs file unsaved changes: {e}", file=sys.stderr)
        return False


def _force_reload_glyphs_document(glyphs_path: Path, font_object=None) -> None:
    """
    Force Glyphs.app to reload the document by saving unsaved changes, 
    then closing and reopening all windows of the document.
    
    This ensures the document reflects external changes without save conflicts.
    
    Flow:
    1. Save any unsaved changes in Glyphs.app (preserves user work)
    2. Re-save our changes (since Glyphs.app may have overwritten them)
    3. Close all windows of the document
    4. Wait briefly for close to complete
    5. Reopen the document
    
    Args:
        glyphs_path: Path to the Glyphs file
        font_object: Optional font object to re-save after Glyphs.app saves
                    (if None, will reload and save from disk)
    """
    # This write (and the Glyphs.app save/close/reopen dance below) is
    # server-initiated — suppress the file watcher so it doesn't queue a
    # redundant sync + second build on top of the caller's own rebuild.
    _suppress_watcher()
    _suppress_watcher(original=True)
    try:
        # Touch the file to update its modification time
        current_time = time.time()
        os.utime(glyphs_path, (current_time, current_time))
        
        # On macOS, use AppleScript to handle save/close/reopen
        if sys.platform == 'darwin':
            try:
                abs_path = glyphs_path.resolve()
                
                # Step 1: Save any unsaved changes in Glyphs.app
                # Step 2: Re-save our changes (reload from disk and save)
                # Step 3: Close all windows of the document
                # Step 4: Wait briefly
                # Step 5: Reopen the document
                applescript = f'''
                tell application "Glyphs"
                    try
                        set docPath to POSIX file "{abs_path}" as alias
                        set openDocs to documents whose path is (docPath as string)
                        set docCount to count of openDocs
                        
                        if docCount > 0 then
                            -- Step 1: Save any unsaved changes in all open windows
                            -- Reference documents from the openDocs list
                            repeat with aDoc in openDocs
                                tell aDoc
                                    if modified then
                                        save
                                    end if
                                end tell
                            end repeat
                            
                            -- Step 2: Close all windows of this document
                            -- Close all documents from the openDocs list
                            repeat with aDoc in openDocs
                                tell aDoc
                                    close saving no
                                end tell
                            end repeat
                            
                            -- Step 3: Wait for close to complete
                            delay 0.5
                            
                            -- Step 4: Reopen the document
                            open docPath
                        end if
                    end try
                end tell
                '''
                
                # Run AppleScript with longer timeout for save/close/reopen operations
                result = subprocess.run(
                    ['osascript', '-e', applescript],
                    capture_output=True,
                    timeout=10,
                    check=False
                )
                
                # After Glyphs.app saves, we need to re-save our changes
                # (since Glyphs.app may have overwritten them with its in-memory state)
                if result.returncode == 0 and font_object is not None:
                    # Small delay to ensure Glyphs.app has finished saving
                    time.sleep(0.3)
                    # Re-save our changes
                    font_object.save(str(glyphs_path))
                    print(f"Re-saved changes after Glyphs.app save", file=sys.stderr)
                elif result.returncode == 0:
                    # If no font object provided, reload from disk and save
                    # This ensures our changes are preserved
                    time.sleep(0.3)
                    from glyphsLib import load
                    font = load(str(glyphs_path))
                    font.save(str(glyphs_path))
                    print(f"Re-saved changes after Glyphs.app save", file=sys.stderr)
                    
            except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as e:
                # AppleScript failed - log but don't fail
                print(f"Warning: Could not force reload Glyphs document: {e}", file=sys.stderr)
    except Exception as e:
        # Silently fail - file save already succeeded, this is just a notification
        print(f"Warning: Could not force reload Glyphs document: {e}", file=sys.stderr)
    finally:
        # Re-arm after the dance: the file's final mtime lands here, so the
        # watermark covers events delivered (or processed) late.
        _suppress_watcher()
        _suppress_watcher(original=True)


def get_instances_from_glyphs(glyphs_path: Path) -> List[Dict]:
    """Read source-defined instances from the source file (any supported format).

    Returns a list of ``{name, coordinates}`` dicts. Each instance also gets
    an ``origin: "source"`` tag — studio-only instances are computed by the
    caller via the CSV.
    """
    try:
        font, _fmt = _source_font.load_source_cached(glyphs_path)
        instances = _source_font.get_source_instances(font)
        for entry in instances:
            entry["origin"] = "source"
        return instances
    except Exception as e:
        print(f"Error reading source file: {e}", file=sys.stderr)
        raise


def get_axes_from_glyphs(glyphs_path: Path) -> List[Dict]:
    """Read axes from the source file (any supported format).

    Returns a list of ``{tag, name, min, max, default}`` dicts.
    """
    try:
        font, _fmt = _source_font.load_source_cached(glyphs_path)
        return _source_font.get_axes(font)
    except Exception as e:
        print(f"Error reading axes from source file: {e}", file=sys.stderr)
        raise


def build_variable_font(glyphs_path: Path, output_dir: Path, use_fontc: bool = True) -> Path:
    """
    Build variable font from Glyphs file using fontc (with fontmake fallback).
    
    Args:
        glyphs_path: Path to Glyphs file
        output_dir: Directory to output the font
        use_fontc: If True, try fontc first, fallback to fontmake on failure
    
    Returns path to the built variable font TTF.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Try fontc first if enabled
    if use_fontc:
        # Use fontc from PATH (pip-installed)
        fontc_cmd = shutil.which("fontc")
        if fontc_cmd:
            # Output filename derives from the .glyphs filename so the tool
            # works on any font, not just Crispy.
            output_file = output_dir / f"{glyphs_path.stem}-VF.ttf"
            cmd = [
                fontc_cmd,
                "--output-file", str(output_file),
                str(glyphs_path)
            ]
            
            print(f"Building variable font with fontc: {' '.join(cmd)}", file=sys.stderr)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=glyphs_path.parent
            )
            
            if result.returncode == 0 and output_file.exists():
                print("✅ fontc compilation successful", file=sys.stderr)
                return output_file
            else:
                error_msg = result.stderr or result.stdout or "Unknown error"
                print(f"fontc failed, falling back to fontmake: {error_msg}", file=sys.stderr)
        else:
            print(f"fontc not found in PATH, using fontmake", file=sys.stderr)
    
    # Fallback to fontmake
    cmd = [
        "fontmake",
        "-o", "variable",
        "-g", str(glyphs_path),
        "--output-dir", str(output_dir)
    ]
    
    print(f"Building variable font with fontmake: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=glyphs_path.parent
    )
    
    if result.returncode != 0:
        error_msg = result.stderr or result.stdout or "Unknown error"
        print(f"fontmake failed with exit code {result.returncode}", file=sys.stderr)
        print(f"fontmake stderr: {error_msg}", file=sys.stderr)
        if result.stdout:
            print(f"fontmake stdout: {result.stdout}", file=sys.stderr)
        raise RuntimeError(f"fontmake failed: {error_msg}")
    
    # Find the generated variable font
    # fontmake may create files directly in output_dir or in a subdirectory
    variable_fonts = list(output_dir.rglob("*.ttf"))
    if not variable_fonts:
        raise RuntimeError(f"No variable font found in {output_dir}")
    
    # Return the first variable font found
    # (In practice, there should be only one)
    return variable_fonts[0]


def get_axes_from_built_font(font_path: Path) -> List[Dict]:
    """
    Extract axes from built variable font (for verification).
    This is used after building to confirm the axes match the Glyphs file.
    
    Returns list of axis dicts with:
    - tag: axis tag
    - name: axis name
    - min: minimum value
    - max: maximum value
    - default: default value
    """
    font = TTFont(str(font_path))
    
    if "fvar" not in font:
        return []
    
    fvar = font["fvar"]
    axes = []
    
    for axis in fvar.axes:
        tag = axis.axisTag
        
        # Get axis name from STAT table if available
        name = tag  # Default to tag
        if "STAT" in font:
            stat = font["STAT"]
            # Try to find axis name in STAT table
            # This is simplified - STAT table parsing is complex
            # For now, use common axis names
            axis_names = {
                "wght": "Weight",
                "wdth": "Width",
                "opsz": "Optical Size",
                "cntr": "Contrast",
                "SPAC": "Spacing",
                "slnt": "Slant",
                "XOPQ": "X-Opacity",
                "YOPQ": "Y-Opacity",
                "XTRA": "X-Transparency",
            }
            name = axis_names.get(tag, tag)

        axes.append({
            "tag": tag,
            "name": name,
            "min": float(axis.minValue),
            "max": float(axis.maxValue),
            "default": float(axis.defaultValue),
            # Built-font axes carry real gvar deltas (parametric masters or
            # transform-injected phantom-point deltas), so the slider does
            # something — surface it as covered. Used by the built-font
            # overlay in get_axes() for transform-injected axes like SPAC.
            "has_master_coverage": True,
        })
    
    return axes


def _source_is_glyphs(glyphs_path: Path) -> bool:
    return glyphs_path.suffix.lower() == ".glyphs"


def rename_instance_in_glyphs(glyphs_path: Path, old_name: str, new_name: str) -> bool:
    """Rename a source-defined instance."""
    try:
        font, _fmt = _source_font.load_source(glyphs_path)
        ok = _source_font.rename_source_instance(font, glyphs_path, old_name, new_name)
        if not ok:
            return False
        if _source_is_glyphs(glyphs_path):
            _force_reload_glyphs_document(glyphs_path, font_object=font)
        return True
    except ValueError:
        raise
    except Exception as e:
        print(f"Error renaming instance in source file: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


def delete_instance_in_glyphs(glyphs_path: Path, instance_name: str) -> bool:
    """Delete a source-defined instance."""
    try:
        font, _fmt = _source_font.load_source(glyphs_path)
        ok = _source_font.delete_source_instance(font, glyphs_path, instance_name)
        if not ok:
            return False
        if _source_is_glyphs(glyphs_path):
            _force_reload_glyphs_document(glyphs_path, font_object=font)
        return True
    except Exception as e:
        print(f"Error deleting instance from source file: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


def _source_instance_name(display_name: str) -> str:
    """The name an instance carries INSIDE the original source file.

    Renames are studio-level (the alias sidecar, applied to every
    regenerated shadow) and never rewrite the original — so a source
    writeback for a renamed instance must address the ORIGINAL name or
    it silently finds nothing."""
    try:
        aliases = _instance_aliases.load(ORIGINAL_PATH) if ORIGINAL_PATH else {}
    except Exception:  # noqa: BLE001
        return display_name
    for old, new in aliases.items():
        if new == display_name:
            return old
    return display_name


def update_instance_in_glyphs(glyphs_path: Path, instance_name: str, coordinates: Dict[str, float]) -> bool:
    """
    Update instance coordinates in Glyphs file.
    Modifies instance.axes values directly.
    
    Returns True if update was successful, False otherwise.
    """
    try:
        font, _fmt = _source_font.load_source(glyphs_path)
        ok = _source_font.update_source_instance_coords(
            font, glyphs_path, instance_name, coordinates
        )
        if not ok:
            return False
        if _source_is_glyphs(glyphs_path):
            _force_reload_glyphs_document(glyphs_path, font_object=font)
        return True
    except Exception as e:
        print(f"Error updating source file: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


@app.route('/api/masters', methods=['GET'])
def get_masters():
    """Return the source's masters — the parametric design corners.

    Pre-listed in the control-axis brace flow so the designer places a
    crbr layer at a specific corner. Read from the active source
    (shadow when control axes are live); master parametric coordinates
    are identical either way.
    """
    if GLYPHS_PATH is None:
        return jsonify({"masters": []})
    try:
        font, _fmt = _source_font.load_source_cached(GLYPHS_PATH)
        return jsonify({"masters": _source_font.get_masters(font)})
    except Exception as e:
        print(f"Error reading masters: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


@app.route('/api/instances', methods=['GET'])
def get_instances():
    """Return source-defined + studio-only instances with origin tags.

    - ``origin: "source"`` — instance is declared inside the .glyphs /
      .designspace file. Edits write back to that file.
    - ``origin: "studio"`` — instance lives only in the sibling CSV.
      Edits write to the CSV; the source is untouched until the user
      explicitly calls ``POST /api/instance/<name>/add-to-source``.
    """
    if GLYPHS_PATH is None:
        return jsonify({"instances": []})
    try:
        instances = get_instances_from_glyphs(GLYPHS_PATH)
        source_names = {entry["name"] for entry in instances}

        # Pull studio-only rows: any CSV row whose Instance Name isn't in
        # the source file. For SOURCE rows, merge the CSV's cells over the
        # source coordinates: the CSV is the STUDIO layer, and an edit
        # saved there (the flyout's "Save to studio") must come back after
        # a reload — the pristine source values ride along as
        # ``source_coordinates`` so the sync dot can still show the
        # source mismatch (orange). Covers every column matching a source
        # axis tag, plus SPAC (transform-injected, never a source axis).
        try:
            source_axis_tags = {str(ax.get("tag", ""))
                                for ax in get_axes_from_glyphs(GLYPHS_PATH)}
        except Exception:  # noqa: BLE001
            source_axis_tags = set()
        csv_path = _get_preview_csv_path()
        csv_order: Dict[str, int] = {}
        if csv_path and csv_path.exists():
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        name = (row.get("Instance Name") or "").strip()
                        if not name:
                            continue
                        if name not in csv_order:
                            csv_order[name] = len(csv_order)
                        if name in source_names:
                            inst = next((i for i in instances if i["name"] == name), None)
                            if inst is None:
                                continue
                            inst.setdefault("source_coordinates",
                                            dict(inst["coordinates"]))
                            for key, value in row.items():
                                if not key or key == "Instance Name":
                                    continue
                                if key != "SPAC" and key not in source_axis_tags:
                                    continue  # mapping column (WGHT/OPSZ…), not a coordinate
                                cell = (str(value) if value is not None else "").strip()
                                if not cell:
                                    continue  # blank cell → the source value stands
                                try:
                                    inst["coordinates"][key] = float(cell)
                                except (TypeError, ValueError):
                                    pass
                            continue
                        coordinates = {}
                        for key, value in row.items():
                            if not key or key == "Instance Name":
                                continue
                            try:
                                coordinates[key] = float(value)
                            except (TypeError, ValueError):
                                continue
                        instances.append({
                            "name": name,
                            "coordinates": coordinates,
                            "origin": "studio",
                        })
            except Exception as e:
                print(f"Warning: Could not read studio-only rows from CSV: {e}", file=sys.stderr)

        # Per-instance CONTROL-axis values hydrate from the control
        # sidecar so sliders and row previews come back after a reload.
        # Deliberately NOT merged into source_coordinates: they are
        # studio state, and padding that dict would break the CSV-vs-
        # source divergence check (it compares key sets).
        try:
            ctl_values = _control_axes.instance_values(ORIGINAL_PATH)
        except Exception:  # noqa: BLE001
            ctl_values = {}
        if ctl_values:
            for inst in instances:
                for tag, per_name in ctl_values.items():
                    if inst["name"] in per_name:
                        inst["coordinates"][tag] = per_name[inst["name"]]

        # The CSV row order is the user's authored row order (the drag
        # reorder persists it there). Instances without a CSV row keep
        # their current (source) order, ahead of ordered rows — stable
        # sort, so a CSV-less project is untouched.
        instances.sort(key=lambda i: csv_order.get(i.get("name"), -1))

        return jsonify({"instances": instances})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/instances/order', methods=['PUT'])
def reorder_instances():
    """Persist the instance row order by reordering the CSV rows.

    Body: {"names": ["Instance A", "Instance B", ...]} — the full
    desired order. Rows named here are arranged in this order; rows not
    named keep their relative order after them. Order is display state
    only (the mapping model is order-independent), so no rebuild."""
    data = request.get_json(silent=True) or {}
    names = data.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return jsonify({"error": "Body must include 'names' as a list of strings."}), 400
    csv_path = _get_preview_csv_path()
    if not csv_path or not csv_path.exists():
        return jsonify({"error": "Preview CSV not available"}), 500
    if _check_csv_external_edit(csv_path):
        return jsonify({
            "error": "The studio's saved data changed outside this window. Please reload.",
            "reload_required": True
        }), 409
    try:
        import csv
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            rows = list(reader)
        rank = {n: i for i, n in enumerate(names)}
        rows.sort(key=lambda r: rank.get((r.get("Instance Name") or "").strip(),
                                         len(rank)))
        _csv_io.backup_sidecar(csv_path)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        _update_csv_modification_time(csv_path)
        return jsonify({"success": True})
    except Exception as e:
        print(f"Error reordering instances: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


@app.route('/api/axes', methods=['GET'])
def get_axes():
    """Get axes from the source file.

    Always reads from the source rather than the built font: the source
    carries semantic information the built font drops (notably
    ``has_master_coverage`` — true iff at least one master sits at a
    non-default position for the axis). The built-font path
    (``get_axes_from_built_font``) is kept around for verification but
    isn't the API path anymore.
    """
    if GLYPHS_PATH is None:
        return jsonify({"axes": []})
    try:
        axes = get_axes_from_glyphs(GLYPHS_PATH)

        # CONTROL AXES overlay. The source/shadow .glyphs may or may
        # not have the control axis yet (depending on slice — slice 2
        # generates a shadow but builds from the original, so the
        # source-derived axes won't include the control axis). Either
        # way the sidecar carries the canonical declaration; merge
        # it in so the frontend gets a unified list.
        if ORIGINAL_PATH is not None:
            try:
                sidecar_axes = list(_control_axes.list_axes(ORIGINAL_PATH))
                if sidecar_axes:
                    by_tag = {str(ax.get("tag", "")).lower(): ax for ax in axes}
                    for spec in sidecar_axes:
                        tag_lower = str(spec.get("tag", "")).lower()
                        if not tag_lower:
                            continue
                        existing = by_tag.get(tag_lower)
                        if existing is not None:
                            # Source already declares this tag — overlay
                            # the designer's range on top.
                            existing["min"] = float(spec["min"])
                            existing["max"] = float(spec["max"])
                            existing["default"] = float(spec["default"])
                            existing["name"] = spec.get("display_name") or existing.get("name")
                            existing["has_master_coverage"] = True
                            existing["is_control_axis"] = True
                        else:
                            # Source doesn't know about this axis yet
                            # (slice 2: no shadow swap). Synthesise an
                            # entry from sidecar so the slider still
                            # renders. font-variation-settings on a
                            # non-existent axis is silently ignored by
                            # the browser, so moving the slider has no
                            # visual effect — exactly what we want
                            # until brace layers exist in slice 3+.
                            axes.append({
                                "tag": tag_lower,
                                "name": spec.get("display_name") or tag_lower,
                                "min": float(spec["min"]),
                                "max": float(spec["max"]),
                                "default": float(spec["default"]),
                                "has_master_coverage": True,
                                "is_control_axis": True,
                            })
            except Exception as overlay_exc:
                print(f"Warning: control-axes overlay on /api/axes failed: {overlay_exc}", file=sys.stderr)

        # GRADE axis overlay. GRAD lives in the shadow but its range comes from
        # VIRTUAL masters (no real master spans it), so get_axes_from_glyphs
        # reports it as [0,0] — and the built-font overlay below then skips it
        # as "already present". When grade is enabled with ≥1 graded instance,
        # surface the real -10..+10 range so the sidebar renders a usable
        # slider. Like SPAC it's a live-preview axis (transform_injected), not
        # per-instance data.
        if ORIGINAL_PATH is not None:
            try:
                if _grade.list_graded_instances(ORIGINAL_PATH):
                    tag_lower = _grade.GRAD_TAG.lower()
                    entry = next((a for a in axes if str(a.get("tag", "")).lower() == tag_lower), None)
                    if entry is None:
                        entry = {"tag": _grade.GRAD_TAG}
                        axes.append(entry)
                    entry["name"] = _grade.GRAD_NAME
                    entry["min"] = _grade.GRAD_MIN
                    entry["default"] = _grade.GRAD_DEFAULT
                    entry["max"] = _grade.GRAD_MAX
                    entry["has_master_coverage"] = True
                    entry["transform_injected"] = True
                    entry["is_grade_axis"] = True
            except Exception as grade_exc:
                print(f"Warning: grade overlay on /api/axes failed: {grade_exc}", file=sys.stderr)

        # TRANSFORM-AXES overlay. Transforms can add whole axes WITH twin
        # masters (round_corners' ROND), which reach this list through the
        # TRANSFORMED source and so carry real master coverage — but like
        # SPAC and GRAD they are build products, not per-instance data.
        # Mark every enabled transform's declared axis so the frontend
        # keeps it out of instance saves and CSV sync.
        if ORIGINAL_PATH is not None:
            try:
                injected_tags = set()
                for stage in ("font", "source"):
                    for t, _params in _transforms.registry.active(ORIGINAL_PATH, stage=stage):
                        tag = getattr(t.spec, "injected_axis_tag", None)
                        if tag:
                            injected_tags.add(tag.lower())
                for a in axes:
                    if str(a.get("tag", "")).lower() in injected_tags:
                        a["transform_injected"] = True
            except Exception as inj_exc:
                print(f"Warning: transform-axes overlay on /api/axes failed: {inj_exc}", file=sys.stderr)

        # BUILT-FONT overlay. Post-build transforms (e.g. SPAC) inject fvar
        # axes that have no source master, so get_axes_from_glyphs can't see
        # them. Read the built font's fvar and surface any tag not already
        # present. Two kinds:
        #   - avar2 USER axes (the CSV's in-columns — wght/opsz): mapping
        #     targets with no masters of their own → has_master_coverage
        #     False, so the Preview tab files them under USER AXES.
        #   - transform-injected axes (SPAC): treated as parametric sliders
        #     (has_master_coverage=True → draggable; not a control axis →
        #     sits with XTRA/XOPQ/YOPQ).
        if VARIABLE_FONT_PATH is not None and Path(VARIABLE_FONT_PATH).exists():
            try:
                user_axis_tags = set()
                csv_path = _get_avar2_csv_path()
                if csv_path and csv_path.exists():
                    try:
                        _, _, in_cols, _, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
                        user_axis_tags = {
                            _csv_io.normalize_in_axis_name(c).lower() for c in in_cols
                        }
                        user_axis_tags.discard("spac")  # transform-injected, stays parametric
                    except Exception:
                        user_axis_tags = set()
                have = {str(a.get("tag", "")).lower() for a in axes}
                built_axes = list(get_axes_from_built_font(VARIABLE_FONT_PATH))
                # DEFAULT overlay for axes the source already lists: the
                # build relocates the parametric defaults to the ORIGIN
                # row's location (the default-origin master), so the
                # BUILT font's defaults are where the sliders must rest
                # — the source still carries the raw compile origin
                # (e.g. the hairline corner). Identity when no
                # relocation happened. Ranges stay source-derived.
                built_default_by_tag = {
                    str(b.get("tag", "")).lower(): b.get("default") for b in built_axes
                }
                for a in axes:
                    bd = built_default_by_tag.get(str(a.get("tag", "")).lower())
                    if bd is not None:
                        a["default"] = bd
                for b in built_axes:
                    tag_lower = str(b.get("tag", "")).lower()
                    if tag_lower in have:
                        continue
                    is_user_axis = tag_lower in user_axis_tags
                    axes.append({
                        "tag": b["tag"],
                        "name": b.get("name", b["tag"]),
                        "min": b["min"],
                        "max": b["max"],
                        "default": b["default"],
                        "has_master_coverage": not is_user_axis,
                        # Built-font-only and not an avar2 user axis ⇒
                        # injected by a post-build transform (SPAC).
                        # Injected axes are LIVE PREVIEW state, never
                        # per-instance data: the frontend must exclude
                        # them from dirtiness checks and save payloads,
                        # or a dragged Spacing slider turns every dot
                        # permanently red ("saves do nothing").
                        "transform_injected": not is_user_axis,
                    })
            except Exception as built_exc:
                print(f"Warning: built-font overlay on /api/axes failed: {built_exc}", file=sys.stderr)

        return jsonify({"axes": axes})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_context():
    """Read-only build state handed to each transform's apply()."""
    return _transforms.BuildContext(
        build_dir=BUILD_DIR,
        source_path=ORIGINAL_PATH,
        glyphs_path=GLYPHS_PATH,
        family=(GLYPHS_PATH.stem if GLYPHS_PATH else "font"),
        log=lambda m: print(f"[transform] {m}", file=sys.stderr),
    )


def _strip_injected_axes(vf_path: Path, tags: set) -> Path:
    """Remove axes a transform is about to inject, so re-running is a no-op.

    Pins each named axis to its default and instances it away, leaving the
    outlines exactly where the default sat. Returns the original path
    untouched when there is nothing to strip, so the common case costs one
    fvar read.
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    font = TTFont(str(vf_path))
    if "fvar" not in font:
        return vf_path
    present = [a.axisTag for a in font["fvar"].axes]
    doomed = [t for t in tags if t in present]
    if not doomed:
        return vf_path
    location = {t: font["fvar"].axes[present.index(t)].defaultValue for t in doomed}
    inst = instantiateVariableFont(font, location, inplace=False)
    out = Path(vf_path)
    inst.save(str(out))
    print(
        f"transforms: stripped {len(doomed)} previously-injected "
        f"{'axis' if len(doomed) == 1 else 'axes'} ({', '.join(doomed)}) before re-applying",
        file=sys.stderr,
    )
    return out


def _apply_transform_chain(vf_path):
    """Run the project's enabled post-build transforms (e.g. SPAC) over a
    freshly-compiled VF, in order, and return the final font path. Applied at
    the single point where each build promotes its output to
    ``VARIABLE_FONT_PATH``, so it covers every build trigger. A transform
    that raises — or returns a path that doesn't exist — is logged and
    skipped, and the chain keeps the last GOOD font, so the preview degrades
    gracefully instead of pointing VARIABLE_FONT_PATH at a missing file.
    Per-transform failures are recorded in ``LAST_TRANSFORM_ERROR`` so the UI
    can surface an enabled-but-failing transform instead of a silent no-op."""
    global LAST_TRANSFORM_ERROR
    LAST_TRANSFORM_ERROR = None
    if vf_path is None or ORIGINAL_PATH is None:
        return vf_path
    _transforms.discover()
    try:
        chain = _transforms.active(ORIGINAL_PATH)
    except Exception as e:  # noqa: BLE001
        print(f"transforms: could not resolve active chain: {e}", file=sys.stderr)
        return vf_path
    out = Path(vf_path)
    errors = []
    # A transform that injects an axis appends it unconditionally, so running
    # the chain over a font that already carries that axis stacks a duplicate.
    # The chain is not always handed a fresh compile — the plain-VF fallback
    # runs it over whatever build_variable_font returned, so a run of failed
    # builds could pile up several SPAC axes (observed: four, which makes an
    # invalid font). Pin any previously-injected axis out first, so applying
    # the chain twice lands in the same place as applying it once.
    # Only FONT-stage injectors (SPAC) — a source-stage transform's axis
    # (round_corners' ROND) is built into the compile itself; stripping
    # it here would tear the axis out of every build.
    injected_tags = {
        t.spec.injected_axis_tag for t, _ in chain
        if getattr(t.spec, "injected_axis_tag", None)
        and getattr(t.spec, "stage", "font") == "font"
    }
    if injected_tags:
        try:
            out = Path(_strip_injected_axes(out, injected_tags))
        except Exception as e:  # noqa: BLE001
            print(f"transforms: could not strip prior injected axes: {e}", file=sys.stderr)
    # Kinetikern's per-instance spacing goes in first, straight after the
    # compile and the default relocation: phantom-point tuples on the
    # parametric axes, which the SPAC injector then re-encodes with the
    # rest (one small tuple per instance per glyph; a brace in the source
    # would be shredded by the relocation into dozens). It strips its own
    # earlier tuples first, so a re-run of the chain lands in the same place.
    try:
        out = Path(_kinetikern.inject.apply_to_font(
            out, ORIGINAL_PATH, log=lambda m: print(m, file=sys.stderr)))
    except Exception as e:  # noqa: BLE001
        print(f"kinetikern: injection failed: {e}", file=sys.stderr)
        errors.append(f"Kinetikern: {e}")
    # Source-stage transforms' font-stage apply() (round_corners' ROND
    # instance stamp) runs LAST: fix_instances regenerates the instance
    # list, and a stamp applied before it would be thrown away.
    chain = sorted(chain, key=lambda tp: getattr(tp[0].spec, "stage", "font") == "source")
    for transform, params in chain:
        try:
            result = transform.apply(out, params, _build_context())
            # Only adopt a returned path that actually exists — a transform
            # (esp. a user script) that returns a bad/unwritten path must not
            # overwrite the last-good font and blank the preview.
            if result is not None and Path(result).exists():
                out = Path(result)
            elif result is not None:
                raise RuntimeError(f"returned a path that does not exist: {result}")
        except Exception as e:  # noqa: BLE001
            print(f"transform '{transform.spec.id}' failed: {e}", file=sys.stderr)
            errors.append(f"{transform.spec.name}: {e}")
    if errors:
        LAST_TRANSFORM_ERROR = "; ".join(errors)
    return _stamp_control_instance_values(out)


def _stamp_control_instance_values(vf_path):
    """Pin per-instance CONTROL-axis values (control sidecar
    ``instance_values``) onto matching fvar named instances — the
    control-axis analogue of SPAC's ``_instance_spac_overrides``.
    Best-effort by name: studio-only rows have no fvar instance to
    stamp (same as SPAC); the UI previews them via the hydrated
    coordinates instead. Runs after the transform chain because
    fix_instances regenerates the instance list."""
    if vf_path is None or ORIGINAL_PATH is None:
        return vf_path
    try:
        values = _control_axes.instance_values(ORIGINAL_PATH)
    except Exception:  # noqa: BLE001
        values = {}
    if not values:
        return vf_path
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(str(vf_path))
        if "fvar" not in font:
            font.close()
            return vf_path
        fvar = font["fvar"]
        axis_by_tag = {a.axisTag: a for a in fvar.axes}
        name_table = font["name"]
        stamped = 0
        for inst in fvar.instances:
            iname = name_table.getDebugName(inst.subfamilyNameID) or ""
            for tag, per_name in values.items():
                axis = axis_by_tag.get(tag)
                if axis is None or iname not in per_name:
                    continue
                v = min(axis.maxValue, max(axis.minValue, float(per_name[iname])))
                inst.coordinates[tag] = v
                stamped += 1
        if stamped:
            font.save(str(vf_path))
            print(f"control axes: stamped {stamped} per-instance value(s) "
                  f"onto fvar instances", file=sys.stderr)
        font.close()
    except Exception as exc:  # noqa: BLE001
        print(f"control axes: instance stamping failed: {exc}", file=sys.stderr)
    return vf_path


@contextlib.contextmanager
def _gc_paused():
    """Pause CPython's cyclic GC for a build-critical section.

    glyphsLib churns millions of temporaries; with a long-lived server
    heap, every allocation spurt triggers full-heap collection passes
    and the IDENTICAL source-transform chain measured 42 seconds in a
    fresh process vs 8+ minutes after the heap had grown
    (gc_collect_main dominating the sample). One collect at the end
    reclaims everything the section leaked."""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        # NO forced full collect here: after a build the heap holds
        # millions of dead objects, and one monolithic collect turned
        # into a multi-minute munmap storm that wedged the build thread
        # with BUILDING stuck true (observed: 30+ min of "Rebuilding"
        # at idle CPU). Re-enabled incremental GC drains it gradually
        # without blocking the world.


def _capture_shadow_drawings() -> int:
    """Pull hand-drawn brace outlines from the shadow into the sidecar.

    Fontra sessions for studio axes edit the SHADOW (the open-editor
    routing serves ``shadow_path_for(ORIGINAL_PATH)``), so without this a
    drawing waits for the next config export to reach the sidecar — the
    only durable copy — and a shadow wipe or an unstaged build loses it
    before then. Untouched seeds are skipped by their stamps, so this is
    a cheap no-op after server-side regens. Returns layers captured.
    """
    if ORIGINAL_PATH is None or GLYPHS_PATH is None:
        return 0
    if Path(GLYPHS_PATH).resolve() == Path(ORIGINAL_PATH).resolve():
        return 0  # no shadow in play — Fontra is editing the original itself
    with SIDECAR_LOCK:
        return _control_axes.capture_outlines(ORIGINAL_PATH)


def _resolve_active_source() -> Optional[Path]:
    """Re-derive the active build source (``GLYPHS_PATH``) from the CURRENT
    control-axis + grade state, without triggering a build. Sets and returns
    the global.

    The build reads a **shadow** of the original whenever a studio feature adds
    per-glyph deltas the original lacks — control-axis brace layers and/or grade
    braces — otherwise it reads the untouched original. Both features compose
    onto the SAME shadow file (grade braces layered on top of the control
    shadow), so this is the single authority on which source the build reads.

    Sharing this everywhere ``GLYPHS_PATH`` is (re)pointed — load, control-axis
    edits, grade edits — is what makes grade (and authored brace layers) survive
    a restart or an unrelated rebuild. Previously the grade composition lived
    ONLY on the grade-edit path, so a plain reload silently dropped the GRAD
    axis from the built font.

    Serialized on ``_RESOLVE_LOCK``: callers arrive from request threads, the
    debounce timer, the watchdog thread and the job supervisor, and two
    overlapping resolves interleave the shadow/transformed writes.
    """
    with _RESOLVE_LOCK, _gc_paused():
        global GLYPHS_PATH, LAST_GRADE_ERROR
        global LAST_SOURCE_TRANSFORM_ERROR
        if ORIGINAL_PATH is None:
            return GLYPHS_PATH
        LAST_GRADE_ERROR = None
        LAST_SOURCE_TRANSFORM_ERROR = None

        # 1) control-axis shadow (brace layers). regenerate_shadow returns None
        #    when the sidecar declares no control axes; a shadow with an axis but no
        #    layers compiles to the original's fvar, so we only *use* it when some
        #    axis actually has layers.
        shadow = None
        try:
            shadow = _control_axes.regenerate_shadow(ORIGINAL_PATH)
            # Regenerating rewrites the watched source file — the caller triggers
            # the build itself, so suppress the watcher for it.
            _suppress_watcher()
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: shadow regeneration failed: {exc}", file=sys.stderr)
        try:
            any_layers = any(ax.get("layers") for ax in _control_axes.list_axes(ORIGINAL_PATH))
        except Exception:  # noqa: BLE001
            any_layers = False

        # 2) grade braces, composed onto the SAME shadow. fresh_shadow only when
        #    there's no control shadow to build on, so re-runs never stack braces.
        any_grades = False
        try:
            if _grade.list_graded_instances(ORIGINAL_PATH):
                grade_shadow = _grade_shadow.apply_grades(
                    ORIGINAL_PATH, _grade_instance_coords(), fresh_shadow=(shadow is None)
                )
                if grade_shadow is not None:
                    shadow = grade_shadow
                    any_grades = True
                    _suppress_watcher()
        except Exception as exc:  # noqa: BLE001
            LAST_GRADE_ERROR = str(exc)
            print(f"Warning: grade shadow generation failed: {exc}", file=sys.stderr)

        # 3) source-stage transforms (round_corners), over a BUILD COPY of the
        #    composed shadow (or the original), so the control and grade braces
        #    generated above go through them together with the masters. Never
        #    the canonical shadow itself: the shadow is regen's STATE, and its
        #    preserve-what-the-designer-drew pass would capture a transformed
        #    brace as a hand drawing and leak it into later untransformed
        #    builds (measured: a rounded lcwd brace against sharp masters is
        #    exactly fontc's "interpolation-incompatible paths"). A failure
        #    keeps the un-transformed source and is surfaced — a silently
        #    sharp font must not pass as ok.
        any_source = False
        src_chain = []
        try:
            _transforms.discover()
            src_chain = _transforms.active(ORIGINAL_PATH, stage="source")
            # Deterministic order regardless of sidecar entry order: slant
            # (10) before rounding (50), so rounds land on slanted drawings.
            src_chain.sort(key=lambda tp: getattr(tp[0].spec, "source_priority", 100))
        except Exception as exc:  # noqa: BLE001
            print(f"transforms: could not resolve source-stage chain: {exc}", file=sys.stderr)
        if src_chain and ORIGINAL_PATH.suffix.lower() == ".glyphs":
            current = None
            try:
                base = shadow if shadow is not None else ORIGINAL_PATH
                transformed = (_control_axes.shadow_dir_for(ORIGINAL_PATH)
                               / "transformed" / ORIGINAL_PATH.name)
                transformed.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(base, transformed)
                from glyphsLib import GSFont as _GSFont
                font = _GSFont(str(transformed))
                ctx = _build_context()
                for current, params in src_chain:
                    current.apply_to_source(font, params, ctx)
                from .source_font import save_font_atomically as _save_atomically
                _save_atomically(font, transformed)
                # Re-arm the watcher suppression for OUR transformed-copy
                # write: the window set after the shadow regen above can
                # expire during a multi-second transform pass, and an
                # unsuppressed self-write re-fires the watcher as an external
                # save → resolve → write → … (the T0.7 storm).
                _suppress_watcher()
                shadow = transformed
                any_source = True
            except Exception as exc:  # noqa: BLE001
                name = current.spec.id if current is not None else "source-stage transforms"
                LAST_SOURCE_TRANSFORM_ERROR = f"{name}: {exc}"
                print(f"Warning: source transform failed, building without it: "
                      f"{LAST_SOURCE_TRANSFORM_ERROR}", file=sys.stderr)

        GLYPHS_PATH = shadow if (shadow is not None and (any_layers or any_grades or any_source)) \
            else ORIGINAL_PATH
        return GLYPHS_PATH


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
            _resolve_active_source()
            return
        except Exception as exc:
            if attempt:
                print(f"Warning: shadow regeneration failed: {exc}", file=sys.stderr)
            else:
                _wait_for_save_to_settle(path, settle=1.0)


def _classify_watched_path(src: Path) -> Optional[str]:
    """Which studio file a watchdog event belongs to: "source" (the active
    build source — the transformed copy when source transforms are on),
    "original" (the designer's own file), "shadow" (the control-axis shadow
    that studio-axis Fontra sessions save into), or "glif" (inside a
    designspace shadow's pooled UFOs). None — not ours; ignore the event.

    The shadow needs its own class: with transforms on, GLYPHS_PATH is the
    transformed/ copy, so a Fontra save to shadow/<name> is neither source
    nor original and would go unseen (T0.9).
    """
    if GLYPHS_PATH is None:
        return None
    resolved = src.resolve()
    if resolved == Path(GLYPHS_PATH).resolve():
        return "source"
    if ORIGINAL_PATH is not None:
        if resolved == Path(ORIGINAL_PATH).resolve():
            return "original"
        if resolved == _control_axes.shadow_path_for(ORIGINAL_PATH).resolve():
            return "shadow"
    if str(GLYPHS_PATH).lower().endswith(".designspace") \
            and src.suffix.lower() in (".glif", ".plist"):
        return "glif"
    return None


def _run_shadow_regen_and_build():
    """Re-resolve the active build source (control + grade shadow) and rebuild.
    Runs on the debounce timer — never call directly from a request."""
    global BACKGROUND_WORK, _REBUILD_TIMER
    with _REBUILD_TIMER_LOCK:
        _REBUILD_TIMER = None
    try:
        if ORIGINAL_PATH is None:
            return
        _resolve_active_source()
        try:
            trigger_build()
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: rebuild after control-axis change failed: {exc}", file=sys.stderr)
    finally:
        with _REBUILD_TIMER_LOCK:
            BACKGROUND_WORK -= 1


def schedule_shadow_rebuild():
    """Queue a debounced shadow regen + rebuild, coalescing a burst of layer
    edits into ONE job. Returns immediately so the request can answer straight
    away; /api/health reports building=true until the job finishes."""
    global _REBUILD_TIMER, BACKGROUND_WORK
    with _REBUILD_TIMER_LOCK:
        if _REBUILD_TIMER is not None:
            # Already queued — push it out; the pending job still counts as work.
            _REBUILD_TIMER.cancel()
        else:
            BACKGROUND_WORK += 1
        _REBUILD_TIMER = threading.Timer(_REBUILD_DEBOUNCE_SECONDS, _run_shadow_regen_and_build)
        _REBUILD_TIMER.daemon = True
        _REBUILD_TIMER.start()


def trigger_build():
    """Submit a build job, coalescing concurrent requests.

    Callers fire this from independent requests (e.g. clicking ✕ on several
    brace layers in a row). The job queue runs one worker at a time and
    re-stages from the current sidecars for every pass, so a request that
    lands mid-build is never dropped: the next pass builds exactly the
    state the sidecars hold then.
    """
    return JOBS.request_build()


def _run_build():
    """One build pass: try the avar2 build first so the preview reflects the
    actual avar2 table the browser will apply. Falls back to a plain
    variable-font build if the avar2 build fails (e.g. CSV is mid-edit,
    gftools is missing, or the user hasn't authored mappings yet) so the rows
    view always has *some* font to show.
    """
    global VARIABLE_FONT_PATH, LAST_BUILD_TIME, BUILDING, USE_FONTC
    global LAST_BUILD_STATUS, LAST_BUILD_ERROR, LAST_AVAR2_ERROR

    # Try the avar2 build first. _perform_avar2_build manages BUILDING itself.
    # GC paused for the whole pass: the avar2 pipeline (update_config,
    # the instancer relocation over a ~9MB gvar, fix/STAT) churns as many
    # temporaries as the source chain and showed the same
    # gc_collect_main-dominated profile once the heap had grown.
    with _gc_paused():
        avar2_result = _perform_avar2_build()
    if avar2_result.get("success"):
        LAST_AVAR2_ERROR = None
        print(f"Avar2 font built: {avar2_result['font_path']}", file=sys.stderr)
        return True

    # Record WHY, so /api/health can surface that the served font is
    # the plain fallback (no avar2 table, no mapped axes) — otherwise
    # everything reports "ok" while the preview quietly loses its
    # user-facing axes.
    _avar2_details = str(avar2_result.get("details") or "").strip()
    LAST_AVAR2_ERROR = (
        f"{avar2_result.get('error')}"
        + (f": …{_avar2_details[-400:]}" if _avar2_details else "")
    )
    print(
        f"Avar2 build skipped/failed ({avar2_result.get('error')}); "
        f"falling back to plain variable-font build.",
        file=sys.stderr,
    )

    BUILDING = True
    try:
        print(f"Building font from {GLYPHS_PATH}...", file=sys.stderr)
        VARIABLE_FONT_PATH = _apply_transform_chain(
            build_variable_font(GLYPHS_PATH, BUILD_DIR, use_fontc=USE_FONTC)
        )
        LAST_BUILD_TIME = time.time()
        # The fallback succeeded — a working font is being served, so the
        # stale-banner state from the avar2 failure has to be cleared,
        # otherwise the UI keeps showing "Build failed" indefinitely.
        LAST_BUILD_STATUS = "ok"
        LAST_BUILD_ERROR = None
        print(f"Font built successfully: {VARIABLE_FONT_PATH}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Build failed: {e}", file=sys.stderr)
        # BOTH paths failed — the avar2 attempt and the plain fallback.
        # Report the compiler error (the actionable one: the source
        # itself can't compile) with the avar2 failure as context,
        # instead of leaving only the avar2 error in the banner. The
        # compiler output is multiline; collapse it so the banner stays
        # one line, and keep the tail where the actual error lives.
        flat = " | ".join(ln for ln in str(e).splitlines() if ln.strip())
        _record_build_failure({
            "success": False,
            "error": "Font build failed",
            "details": f"…{flat[-350:]} (avar2 build also failed: {avar2_result.get('error')})",
        })
        return False
    finally:
        BUILDING = False


@app.route('/api/build', methods=['POST'])
def build_font():
    """Submit a build job and wait for the queue to go idle (the wait is
    GIL-free — every other endpoint keeps answering during the build)."""
    try:
        JOBS.request_build()
        success = JOBS.wait_for_idle()

        if not success:
            return jsonify({"error": LAST_BUILD_ERROR or "Build failed"}), 500

        # Get axes after building (for verification)
        axes = get_axes_from_built_font(VARIABLE_FONT_PATH)

        return jsonify({
            "success": True,
            "font_path": str(VARIABLE_FONT_PATH),
            "axes": axes,
            "build_time": LAST_BUILD_TIME
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/build/status', methods=['GET'])
def build_status():
    """Job queue state: running job (+phase), queued jobs, last result,
    export sizes."""
    return jsonify(JOBS.status())


@app.route('/api/build/<job_id>', methods=['DELETE'])
def cancel_build(job_id):
    """Cancel a queued or running job (the worker's process group is
    killed; the last-good font keeps serving)."""
    return jsonify({"cancelled": JOBS.cancel(job_id)})


@app.route('/api/export/<job_id>/download', methods=['GET'])
def export_download(job_id):
    """Serve a finished export job's artifact."""
    path = JOBS.export_artifact(job_id)
    if path is None:
        return jsonify({"error": "no such export (finished artifacts are "
                                 "kept for a few exports only)"}), 404
    return send_file(str(path), as_attachment=True, download_name=path.name)


@app.route('/api/font', methods=['GET'])
def get_font():
    """Serve the built variable font (avar2 build preferred, plain VF fallback).

    ``?format=woff2`` serves the background-compressed WOFF2 cache (kept in
    step with the build by the job queue) when it is current — 409 while it
    is stale or missing, so the caller falls back to an export job.
    """
    # Phase 3: SPAC font is deferred and the avar2 build is now the primary
    # output. ``trigger_build()`` sets VARIABLE_FONT_PATH to the avar2 build
    # when it succeeds, or the plain VF when it doesn't, so we just serve
    # whatever's there.
    if request.args.get('format') == 'woff2':
        sizes = JOBS.export_sizes()
        if not (VARIABLE_FONT_PATH and sizes["woff2_exact"]):
            return jsonify({"error": "woff2 cache is stale or missing"}), 409
        woff2 = Path(VARIABLE_FONT_PATH).with_suffix(".woff2")
        return send_file(str(woff2), mimetype='font/woff2',
                         as_attachment=bool(request.args.get('download')),
                         download_name=woff2.name)
    if VARIABLE_FONT_PATH and VARIABLE_FONT_PATH.exists():
        font_path = VARIABLE_FONT_PATH
    else:
        return jsonify({"error": "Variable font not built yet."}), 404
    
    # ?download=1 → attachment. The Preview tab's Download button is a
    # plain anchor at this URL; with the inline-with-filename disposition
    # the anchor's download attribute never triggered a save, so the
    # button silently did nothing.
    as_attachment = request.args.get('download', '').lower() in ('1', 'true', 'yes')
    response = send_file(
        str(font_path),
        mimetype='font/ttf',
        as_attachment=as_attachment,
        download_name=font_path.name if as_attachment else None,
    )
    # Add cache control headers to prevent browser caching
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    # Add ETag based on file modification time for cache validation
    mtime = font_path.stat().st_mtime
    response.headers['ETag'] = f'"{int(mtime)}"'
    return response


@app.route('/api/text-width', methods=['GET'])
def get_text_width():
    """
    Measure advance width of text at specific variable font coordinates.
    
    Query parameters:
    - text: Text string to measure (default: sample text)
    - coordinates: JSON object with axis coordinates (e.g., {"XTRA": 627.0, "XOPQ": 187.672})
    - font_size_rem: Font size in rem units (default: 2.0)
    
    Returns:
    - width_pixels: Total advance width in pixels
    - width_font_units: Total advance width in font units
    - width_em: Total advance width in em units
    - upm: Units per em from font
    """
    try:
        import tempfile
        import os
        
        # Get parameters
        text = request.args.get('text', 'The Quick Brown Fox Jumps Over The Lazy Dog 0123456789 &!')
        font_size_rem = float(request.args.get('font_size_rem', 2.0))
        
        # Parse coordinates from JSON string
        coordinates_json = request.args.get('coordinates', '{}')
        try:
            coordinates = json.loads(coordinates_json)
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid coordinates JSON"}), 400
        
        # Measure against whatever the avar2 build produced (the same font
        # the preview is rendering). SPAC-axis lookup used to run here as a
        # preferred path, but SPAC is deferred to v2 — falling back to
        # VARIABLE_FONT_PATH keeps the measurement consistent with the
        # preview and avoids regenerating UFOs every time the user types.
        if VARIABLE_FONT_PATH and VARIABLE_FONT_PATH.exists():
            font_path = VARIABLE_FONT_PATH
        else:
            return jsonify({"error": "Variable font not built yet."}), 404
        
        # Load font
        font = TTFont(str(font_path))
        
        # Get UPM (units per em)
        upm = font['head'].unitsPerEm
        
        # Check if font has fvar table (variable font)
        if 'fvar' not in font:
            # Use font as-is for non-variable fonts
            instance_font = font
        else:
            # Get axis tags from fvar table
            fvar = font['fvar']
            # Map lowercase input tags to original fvar axis tags
            axis_tag_map = {axis.axisTag.lower(): axis.axisTag for axis in fvar.axes}
            
            # Build location dictionary for instancer
            # Use original axis tag case from fvar table
            location = {}
            for tag, value in coordinates.items():
                tag_lower = tag.lower()
                if tag_lower in axis_tag_map:
                    # Use original case from fvar table
                    original_tag = axis_tag_map[tag_lower]
                    location[original_tag] = float(value)
            
            # Set other axes to their defaults from fvar table
            for axis in fvar.axes:
                if axis.axisTag not in location:
                    location[axis.axisTag] = axis.defaultValue
            
            # Create a temporary instance font
            with tempfile.NamedTemporaryFile(suffix='.ttf', delete=False) as tmp_file:
                tmp_path = tmp_file.name
            
            try:
                # Instantiate the font at the specified location
                instance_font = instancer.instantiateVariableFont(font, location)
                instance_font.save(tmp_path)
            except Exception as e:
                print(f"Error during instantiation: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
                return jsonify({"error": f"Font instantiation failed: {e}"}), 500
            finally:
                # Clean up temporary file
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except:
                        pass
        
        # Shape the text with uharfbuzz against the instantiated font.
        # This applies kerning (GPOS) and substitutions (GSUB) the same way
        # a browser shaper does, so the displayed Width matches the laid-out
        # width — not just the sum of raw hmtx advances. The per-glyph
        # breakdown also falls out of the same shape() call, which is what
        # the grade-master delta view needs.
        import uharfbuzz as hb
        from io import BytesIO

        font_buffer = BytesIO()
        instance_font.save(font_buffer)
        font_bytes = font_buffer.getvalue()

        hb_face = hb.Face(font_bytes)
        hb_font = hb.Font(hb_face)
        hb_font.scale = (upm, upm)  # advances in font units, not 26.6 fixed-point

        hb_buf = hb.Buffer()
        hb_buf.add_str(text)
        hb_buf.guess_segment_properties()
        hb.shape(hb_font, hb_buf)

        infos = hb_buf.glyph_infos
        positions = hb_buf.glyph_positions
        glyph_order = instance_font.getGlyphOrder()

        total_width_font_units = 0.0
        per_glyph = []
        for i, (info, pos) in enumerate(zip(infos, positions)):
            advance = float(pos.x_advance)
            total_width_font_units += advance

            gid = info.codepoint  # post-shaping this is the glyph id
            glyph_name = glyph_order[gid] if 0 <= gid < len(glyph_order) else f"gid{gid}"

            # In uharfbuzz, ``cluster`` is the input-string index this glyph
            # came from. Ligatures collapse multiple input chars into one
            # cluster; decompositions split one input char into many. For
            # Latin without GSUB it's 1:1.
            cluster = info.cluster
            next_cluster = infos[i + 1].cluster if i + 1 < len(infos) else len(text)
            input_text = text[cluster:next_cluster]

            per_glyph.append({
                "glyph_id": gid,
                "glyph_name": glyph_name,
                "cluster": cluster,
                "text": input_text,
                "advance_font_units": advance,
            })

        # Convert to pixels (assuming 16px = 1rem, the browser default)
        pixels_per_rem = 16.0
        font_size_pixels = font_size_rem * pixels_per_rem
        width_pixels = (total_width_font_units / upm) * font_size_pixels
        width_em = total_width_font_units / upm

        return jsonify({
            "width_pixels": width_pixels,
            "width_font_units": total_width_font_units,
            "width_em": width_em,
            "upm": upm,
            "font_size_rem": font_size_rem,
            "font_size_pixels": font_size_pixels,
            "per_glyph": per_glyph,
        })
    
    except Exception as e:
        print(f"Error measuring text width: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/instance', methods=['POST'])
def create_instance():
    """Create a new studio-only instance.

    Two-tier model: created instances are written only to the sibling CSV
    with ``origin: studio``. The source file (.glyphs / .designspace) is
    NOT mutated until the user explicitly promotes via
    ``POST /api/instance/<name>/add-to-source``. This keeps the source
    instance list from getting bloated by exploratory grid points.
    """
    data = request.get_json(silent=True)
    if not data or 'name' not in data or 'coordinates' not in data:
        return jsonify({"error": "Missing 'name' or 'coordinates' in request body"}), 400

    if not isinstance(data['name'], str):
        return jsonify({"error": "'name' must be a string"}), 400
    instance_name = data['name'].strip()
    if not instance_name:
        return jsonify({"error": "Instance name cannot be empty"}), 400

    coordinates = data['coordinates']
    try:
        coordinates = {k: float(v) for k, v in coordinates.items()}
    except (ValueError, TypeError):
        return jsonify({"error": "Coordinates must be numeric"}), 400

    insert_after = data.get('insert_after', None)
    if insert_after:
        insert_after = insert_after.strip()

    # Refuse to create if the name already exists either in the source or in the CSV.
    # The axis list rides the same load: the previous shape read `font`
    # again AFTER the except, so a failed load crashed with
    # UnboundLocalError instead of falling back.
    _excluded = _non_mapping_axis_tags()
    try:
        font, _fmt = _source_font.load_source_cached(GLYPHS_PATH)
        existing_source_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
        # ``font`` is the TRANSFORMED SHADOW: subtract the injected and
        # control axes or they become CSV mapping columns below.
        source_axes = [a for a in _source_font.get_axes(font)
                       if str(a.get("tag", "")).upper() not in _excluded]
    except Exception:
        existing_source_names = set()
        source_axes = []
    if instance_name in existing_source_names:
        return jsonify({"error": f"Instance '{instance_name}' already exists in the source file"}), 400

    csv_path = _get_preview_csv_path()
    if not csv_path or not csv_path.exists():
        return jsonify({"error": "Preview CSV not initialized — cannot create studio-only instance"}), 500

    try:
        rows: List[Dict] = []
        fieldnames: List[str] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            for row in reader:
                rows.append(row)

        for row in rows:
            if (row.get("Instance Name") or "").strip() == instance_name:
                return jsonify({"error": f"Instance '{instance_name}' already exists"}), 400

        # Fall back to each axis's declared default when the caller didn't
        # supply a value, NOT to 0. wght=0 is outside the [100, 900] axis
        # range and would silently corrupt the row; defaulting to the
        # source's own declared default value keeps the row valid.
        axis_defaults = {axis["tag"].upper(): axis["default"] for axis in source_axes}

        # Evolve the CSV schema: if the source declares an axis the CSV
        # header doesn't list yet, append it. This handles the case where
        # an existing CSV was bootstrapped before the source gained more
        # axes. Without this, DictWriter rejects rows that carry the new
        # fields. SPAC is no longer special-cased — it's deferred.
        for axis in source_axes:
            upper = axis["tag"].upper()
            if upper == "INSTANCE NAME":
                continue
            if upper not in fieldnames:
                fieldnames.append(upper)
                for row in rows:
                    row.setdefault(upper, str(axis["default"]))

        new_row: Dict[str, str] = {"Instance Name": instance_name}
        for axis in source_axes:
            tag = axis["tag"]
            upper = tag.upper()
            value = coordinates.get(tag, coordinates.get(upper, axis["default"]))
            new_row[upper] = str(value)
        # Per-instance CONTROL-axis values ride along when provided (a
        # duplicate of a corrected row) — stored in the control sidecar,
        # same contract as update_instance.
        try:
            _ctl_tags = {str(a.get("tag", "")).lower()
                         for a in _control_axes.list_axes(ORIGINAL_PATH)}
            ctl_values = {t: v for t, v in coordinates.items()
                          if str(t).lower() in _ctl_tags}
            if ctl_values:
                _control_axes.set_instance_values(ORIGINAL_PATH, instance_name,
                                                  ctl_values)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: could not save control-axis values: {exc}",
                  file=sys.stderr)

        # Per-instance SPAC rides along when the caller provides one (a
        # duplicate of a spaced row) — same contract as update_instance:
        # mint the column on first use, blanks elsewhere are inert.
        spac_value = coordinates.get('SPAC', coordinates.get('spac'))
        if spac_value is not None:
            if 'SPAC' not in fieldnames:
                fieldnames.append('SPAC')
                for row in rows:
                    row.setdefault('SPAC', "")
            new_row['SPAC'] = str(spac_value)
        for field in fieldnames:
            # Columns beyond the parametric axes (OPSZ/WGHT mapping
            # inputs) start BLANK, never 0: a stamped zero becomes a
            # real avar2 in: value and drags the compiled axis range
            # to it (the recurring unset-becomes-zero bug class).
            new_row.setdefault(field, "")

        if insert_after:
            insert_index = None
            for i, row in enumerate(rows):
                if (row.get("Instance Name") or "").strip() == insert_after:
                    insert_index = i + 1
                    break
            if insert_index is None:
                rows.append(new_row)
            else:
                rows.insert(insert_index, new_row)
        else:
            rows.append(new_row)

        _csv_io.backup_sidecar(csv_path)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        _update_csv_modification_time(csv_path)
        print(f"Created studio-only instance '{instance_name}' in CSV", file=sys.stderr)
        success = True
    except Exception as e:
        print(f"Error creating studio-only instance in CSV: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to create instance: {e}"}), 500

    # Rebuild so the avar2 build picks up the new studio-only row.
    # (Non-blocking — trigger_build submits to the job queue.)
    trigger_build()

    return jsonify({
        "success": True,
        "message": f"Created studio-only instance '{instance_name}'",
        "origin": "studio",
    })


@app.route('/api/instance/<instance_name>', methods=['PUT'])
def update_instance(instance_name: str):
    """Update instance coordinates.

    Default behavior writes to both the CSV and the source file (for
    source-defined instances). Pass ``?csv_only=true`` (or set
    ``csv_only`` in the body) to skip the source writeback — the
    flyout's two-action UI uses this for the "Update in avar2-studio"
    path so source instances can be edited without touching .glyphs /
    .designspace.
    """
    data = request.get_json(silent=True)
    if not data or 'coordinates' not in data:
        return jsonify({"error": "Missing 'coordinates' in request body"}), 400

    coordinates = data['coordinates']
    try:
        coordinates = {k: float(v) for k, v in coordinates.items()}
    except (ValueError, TypeError):
        return jsonify({"error": "Coordinates must be numeric"}), 400

    csv_only_flag = (
        request.args.get('csv_only', '').lower() in ('1', 'true', 'yes')
        or bool(data.get('csv_only'))
    )

    font, _fmt = _source_font.load_source(GLYPHS_PATH)
    source_instance_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
    is_source_instance = instance_name in source_instance_names
    # GLYPHS_PATH is the TRANSFORMED SHADOW — its axis list includes the
    # injected (slnt/ROND/SPAC) and control (lcwd/ymod) axes, which are
    # never per-instance mapping data. Without this subtraction their
    # values leak into the CSV row and the .glyphs writeback.
    _excluded = _non_mapping_axis_tags()
    source_axis_tags = {axis["tag"] for axis in _source_font.get_axes(font)
                        if str(axis.get("tag", "")).upper() not in _excluded}

    glyphs_coordinates = {
        tag: value for tag, value in coordinates.items()
        if tag in source_axis_tags
    }
    # The CSV row takes source axes AND SPAC — the per-instance spacing
    # coordinate lives in the CSV only (it's transform-injected, not a
    # source axis, so it never goes to the .glyphs writeback).
    csv_coordinates = {
        tag: value for tag, value in coordinates.items()
        if tag in source_axis_tags or tag == 'SPAC'
    }

    # Per-instance CONTROL-axis values (ymod/lcwd) persist in the control
    # sidecar — never as CSV columns (the CSV uppercases headers, so a
    # ymod column mints a bogus YMOD user axis). /api/instances hydrates
    # them back; the build stamps them onto matching fvar instances.
    try:
        _ctl_tags = {str(a.get("tag", "")).lower()
                     for a in _control_axes.list_axes(ORIGINAL_PATH)}
    except Exception:  # noqa: BLE001
        _ctl_tags = set()
    control_values = {tag: value for tag, value in coordinates.items()
                      if str(tag).lower() in _ctl_tags}
    if control_values:
        _suppress_watcher()
        try:
            _control_axes.set_instance_values(ORIGINAL_PATH, instance_name,
                                              control_values)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: could not save control-axis values: {exc}",
                  file=sys.stderr)

    global EDITING_INSTANCES
    EDITING_INSTANCES.discard(instance_name)

    # Source writeback is skipped if the caller asked for CSV-only.
    # It targets the ORIGINAL — the user's file, the one explicit write
    # the flyout's "Save to source file" promises. (It used to target
    # GLYPHS_PATH, the TRANSFORMED SHADOW: a build artifact regenerated
    # from the original, so the "source save" evaporated on the next
    # regen and the user's file never changed.)
    glyphs_updated = False
    if not csv_only_flag and is_source_instance and glyphs_coordinates:
        glyphs_updated = update_instance_in_glyphs(
            ORIGINAL_PATH, _source_instance_name(instance_name), glyphs_coordinates)
        if not glyphs_updated:
            return jsonify({"error": f"Failed to update instance '{instance_name}' in source file"}), 500
        # The shadow now trails the original — regenerate it BEFORE the
        # CSV sync below reads it, or the sync copies the stale values
        # straight back over the save.
        _resolve_active_source()

    # CSV writeback fires whenever there are coords to record. For
    # studio-only instances this is the only persistence path; for
    # source instances it keeps the avar2 mapping CSV row in lockstep
    # with the source's <location>.
    csv_writeback_needed = bool(csv_coordinates)
    if csv_writeback_needed:
        csv_path = _get_preview_csv_path()
        if csv_path and csv_path.exists():
            try:
                import csv
                rows = []
                with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    fieldnames = list(reader.fieldnames) if reader.fieldnames else []
                    rows = list(reader)

                # Update the row in the CSV when:
                #   - the instance is studio-only (CSV is the source of truth), OR
                #   - csv_only_flag is set (caller wants studio-only behavior
                #     for a source instance — e.g. the flyout's "Update in
                #     avar2-studio" path).
                # For a source instance with csv_only_flag=false, the
                # bottom-of-function sync from source → CSV handles it.
                should_write_csv_row = csv_only_flag or (not is_source_instance)
                instance_updated = False
                updated_count = 0
                for row in rows:
                    if row.get("Instance Name", "").strip() == instance_name:
                        if should_write_csv_row:
                            for tag, value in csv_coordinates.items():
                                col = tag.upper()
                                if col not in fieldnames:
                                    if col == 'SPAC':
                                        # Mint the SPAC column on first
                                        # use. It is the one injected axis
                                        # with a per-instance CSV contract:
                                        # /api/instances reads it back, the
                                        # spacing transforms pin it onto
                                        # named fvar instances
                                        # (_instance_spac_overrides), and
                                        # config generation excludes it
                                        # everywhere — so blank cells in
                                        # the other rows are inert.
                                        fieldnames.append(col)
                                        for r in rows:
                                            r.setdefault(col, "")
                                    else:
                                        # Unknown coordinate tag — DON'T
                                        # create a column for it. The
                                        # mappings data models source
                                        # parametric axes plus declared
                                        # mapping axes only; a control-axis
                                        # column (lcwd/ymod) reads as a
                                        # required-but-blank parametric
                                        # axis and aborts the avar2 build.
                                        continue
                                row[col] = str(value)
                        instance_updated = True
                        updated_count += 1

                if instance_updated:
                    if updated_count > 1:
                        print(f"Warning: Found {updated_count} duplicate rows for '{instance_name}', updated all", file=sys.stderr)
                    _csv_io.backup_sidecar(csv_path)
                    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    _update_csv_modification_time(csv_path)
                    print(f"Saved '{instance_name}' in the studio", file=sys.stderr)
                else:
                    print(f"Error: Instance '{instance_name}' not found in the studio", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not update CSV: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
    
    # Sync CSV to pick up source-file coord changes (skip the in-flight
    # edit; that row was already updated above). Skip the sync entirely
    # for csv-only edits — we just wrote the user's edit to CSV and
    # don't want to overwrite it with the source's unchanged values.
    if not csv_only_flag:
        csv_path = _get_avar2_csv_path()
        if csv_path and csv_path.exists():
            try:
                if _csv_io.update_csv_from_glyphs(GLYPHS_PATH, csv_path, skip_instances={instance_name},
                                                  exclude_axes=_non_mapping_axis_tags()):
                    _update_csv_modification_time(csv_path)   # ours, not external
                    print("CSV synced after instance update", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not sync CSV after update: {e}", file=sys.stderr)
    
    if csv_only_flag:
        # NO rebuild for auto-saved coordinate edits. The instance
        # rows preview through font-variation-settings on the
        # already-built font, so a rebuild changes nothing they show —
        # it only refreshes the avar2 table, which is consumed on the
        # Preview tab. Health reports ``build_stale`` (CSV newer than
        # the last build); the frontend rebuilds once when the Preview
        # tab opens. Rebuilding per slider settle made every tweak
        # cost a multi-second build and a preview flash.
        pass
    else:
        # Explicit source writeback: rebuild immediately (non-blocking —
        # trigger_build submits to the job queue).
        trigger_build()
    
    message_parts = []
    if glyphs_updated:
        message_parts.append(f"Updated instance '{instance_name}' in source file")
    if csv_writeback_needed:
        message_parts.append(f"Saved '{instance_name}' in the studio")

    return jsonify({
        "success": True,
        "message": "; ".join(message_parts) if message_parts else f"Updated instance '{instance_name}'",
    })


@app.route('/api/instance/<instance_name>/rename', methods=['PUT'])
def rename_instance(instance_name: str):
    """Rename an instance in the Glyphs file."""
    data = request.get_json(silent=True)
    if not data or 'new_name' not in data:
        return jsonify({"error": "Missing 'new_name' in request body"}), 400

    if not isinstance(data['new_name'], str):
        return jsonify({"error": "'new_name' must be a string"}), 400
    new_name = data['new_name'].strip()
    if not new_name:
        return jsonify({"error": "New instance name cannot be empty"}), 400
    
    if new_name == instance_name:
        return jsonify({"error": "New name is the same as current name"}), 400

    try:
        font, _fmt = _source_font.load_source(GLYPHS_PATH)
        source_instance_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
        is_source_instance = instance_name in source_instance_names

        if is_source_instance:
            success = rename_instance_in_glyphs(GLYPHS_PATH, instance_name, new_name)
            if success and ORIGINAL_PATH is not None:
                # The rename just edited the SHADOW, which is re-derived
                # from the original on every regeneration — record it so
                # regenerate_shadow re-applies it and the old name never
                # resurrects.
                try:
                    _instance_aliases.record(ORIGINAL_PATH, instance_name, new_name)
                except Exception as alias_exc:  # noqa: BLE001
                    print(f"Warning: could not persist instance rename: {alias_exc}", file=sys.stderr)
        else:
            # Studio-only: rename happens in CSV only, below.
            success = True

        if success:
            # Keep any grade attached to this instance across the rename.
            try:
                _grade.rename_instance(ORIGINAL_PATH, instance_name, new_name)
            except Exception:  # noqa: BLE001
                pass
            try:
                _kinetikern.sidecar.rename_instance(ORIGINAL_PATH, instance_name, new_name)
            except Exception:  # noqa: BLE001
                pass
            # Same for per-instance control-axis values.
            try:
                _control_axes.rename_instance_values(ORIGINAL_PATH,
                                                     instance_name, new_name)
            except Exception:  # noqa: BLE001
                pass
            # Update preview CSV if it exists
            csv_path = _get_preview_csv_path()
            if csv_path and csv_path.exists():
                try:
                    rows = []
                    fieldnames = []
                    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                        reader = csv.DictReader(f)
                        fieldnames = list(reader.fieldnames) if reader.fieldnames else []
                        for row in reader:
                            if row.get("Instance Name", "").strip() == instance_name:
                                row["Instance Name"] = new_name
                            rows.append(row)
                    
                    # Write updated CSV
                    _csv_io.backup_sidecar(csv_path)
                    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    
                    # Update modification time cache
                    _update_csv_modification_time(csv_path)
                    
                    print(f"Renamed instance '{instance_name}' to '{new_name}' in preview CSV", file=sys.stderr)
                except Exception as e:
                    print(f"Warning: Could not update instance name in preview CSV: {e}", file=sys.stderr)
                    import traceback
                    traceback.print_exc()
            trigger_build()

            return jsonify({"success": True, "message": f"Renamed instance '{instance_name}' to '{new_name}'"})
        else:
            return jsonify({"error": f"Failed to rename instance '{instance_name}'"}), 500
    except ValueError as e:
        # Duplicate name error
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/instance/<instance_name>/add-to-source', methods=['POST'])
def add_instance_to_source(instance_name: str):
    """Promote a studio-only instance into the source file's instance list.

    Reads the instance's parametric coordinates from its CSV row and
    appends a matching <instance> / GSInstance to the source. After this
    succeeds, the next ``GET /api/instances`` returns the instance with
    ``origin: source`` and the UI's studio-only badge disappears.
    """
    try:
        # Promotion writes the ORIGINAL (the user's file) — loading and
        # saving the shadow here would both lose the write on the next
        # regen AND risk leaking shadow artifacts if saved elsewhere.
        font, _fmt = _source_font.load_source(ORIGINAL_PATH)
        source_instance_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
        if instance_name in source_instance_names:
            return jsonify({"error": f"Instance '{instance_name}' is already in the source file"}), 400

        # Pull the studio-only row from the CSV to get its coordinates.
        csv_path = _get_preview_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "Preview CSV not available"}), 500

        coords: Dict[str, float] = {}
        found = False
        source_axis_tags = {axis["tag"] for axis in _source_font.get_axes(font)}
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row.get("Instance Name") or "").strip() != instance_name:
                    continue
                found = True
                # CSV columns are upper-case axis tags (XOPQ/YOPQ/XTRA/…)
                # whereas the source uses the original tag case.
                for key, value in row.items():
                    if not key or key == "Instance Name" or key.upper() == "SPAC":
                        continue
                    tag_candidates = [key, key.upper(), key.lower()]
                    matched_tag = next((t for t in tag_candidates if t in source_axis_tags), None)
                    if matched_tag is None:
                        continue
                    try:
                        coords[matched_tag] = float(value)
                    except (TypeError, ValueError):
                        continue
                break

        if not found:
            return jsonify({"error": f"Instance '{instance_name}' not found in the studio"}), 404

        ok = _source_font.add_instance_to_source(font, ORIGINAL_PATH, instance_name, coords)
        if not ok:
            return jsonify({"error": f"Failed to add '{instance_name}' to source file"}), 500

        # For .glyphs sources, also poke Glyphs.app to reload the document.
        if _source_is_glyphs(ORIGINAL_PATH):
            _force_reload_glyphs_document(ORIGINAL_PATH, font_object=font)

        # The shadow (and with it the instances listing) trails the
        # original until regenerated — resolve now so the origin flip
        # is visible immediately; rebuild in the background.
        _resolve_active_source()
        threading.Thread(target=lambda: trigger_build(), daemon=True).start()

        return jsonify({
            "success": True,
            "message": f"Added '{instance_name}' to source file",
            "origin": "source",
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def get_font_family_name(glyphs_path: Path) -> Optional[str]:
    """Return the font family name (source file stem in both formats).

    Locked convention: file stem, not source-declared family. Keeps the
    avar2 build, sibling CSV, and built-font filename in lockstep
    regardless of what's inside the source. No read, no cache: the stem
    is free.
    """
    if not glyphs_path:
        return None
    return glyphs_path.stem


def _get_avar2_built_font_filename() -> Optional[str]:
    """Return the avar2 build's output filename, e.g. ``Crispy[SPAC,XOPQ,XTRA,YOPQ].ttf``.

    Priority:
    1. An existing TTF already on disk in the avar2 font dir (authoritative
       after at least one build has succeeded).
    2. The first key of ``fvarInstances`` in the preview config — the same key
       gftools-builder uses to name its TTF output before a build has run.
    3. A name derived from the Glyphs file stem and any axes it declares.
    """
    avar2_font_dir = _get_avar2_font_dir()
    if avar2_font_dir and avar2_font_dir.exists():
        # Prefer non-avar1 variants (the primary build, not the fallback
        # backward-compat conversion). Within that, prefer most recent.
        primary = sorted(
            (p for p in avar2_font_dir.glob("*.ttf") if "-avar1" not in p.stem),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if primary:
            return primary[0].name
        any_ttf = sorted(avar2_font_dir.glob("*.ttf"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        if any_ttf:
            return any_ttf[0].name
    if yaml is not None:
        config_path = _get_preview_config_path()
        if config_path and config_path.exists():
            try:
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}
                fvar_instances = config.get("fvarInstances") or {}
                if fvar_instances:
                    return next(iter(fvar_instances.keys()))
            except Exception as e:
                print(f"Warning: failed to read built-font filename from {config_path}: {e}",
                      file=sys.stderr)
    if GLYPHS_PATH:
        family = GLYPHS_PATH.stem
        try:
            axes = get_axes_from_glyphs(GLYPHS_PATH)
            tags = sorted(a["tag"] for a in axes if a.get("tag"))
        except Exception:
            tags = []
        return f"{family}[{','.join(tags)}].ttf" if tags else f"{family}-VF.ttf"
    return None


def _is_build_stale() -> bool:
    """CSV modified after the last build ⇒ the served font's avar2
    table no longer reflects the mappings. Cheap mtime compare."""
    if LAST_BUILD_TIME is None:
        return False
    try:
        csv_path = _get_avar2_csv_path() or _get_preview_csv_path()
        if not csv_path or not csv_path.exists():
            return False
        return csv_path.stat().st_mtime > LAST_BUILD_TIME
    except Exception:
        return False


# --------------------------------------------------------------------------
# Build gate: a source that does not compile stops the studio
# --------------------------------------------------------------------------
# When the linked source fails to compile, the server keeps serving the
# last-good font so the UI can still render. It must NOT keep accepting
# edits: every instance, grade, layer or mapping change would be authored
# against a font that no longer exists, and an export would ship a stale
# build as if it were current. So while LAST_BUILD_STATUS is "failed" every
# mutating request is refused with 409. Reads stay open so the UI can show
# the error, and a short allowlist stays open purely so the user can
# RECOVER: retry the build, switch source, and the in-memory editing
# bookkeeping. One hook rather than a check in each of ~25 routes, so it
# cannot be forgotten by the next endpoint.
_BUILD_GATE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_BUILD_GATE_ALLOW_EXACT = {"/api/build", "/api/load-source"}
_BUILD_GATE_ALLOW_SUFFIX = ("/editing",)


@app.before_request
def _refuse_edits_while_source_is_broken():
    if request.method not in _BUILD_GATE_METHODS:
        return None
    if LAST_BUILD_STATUS != "failed":
        return None
    path = request.path
    if path in _BUILD_GATE_ALLOW_EXACT or path.endswith(_BUILD_GATE_ALLOW_SUFFIX):
        return None
    if not path.startswith("/api/") and not path.startswith("/fontra"):
        return None
    return jsonify({
        "error": "The source file does not compile, so the studio is paused.",
        "detail": LAST_BUILD_ERROR,
        "build_blocked": True,
        "hint": "Fix the source and save it (the studio rebuilds on save), or press Rebuild.",
    }), 409


@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    try:
        family_name = None
        if GLYPHS_PATH:
            family_name = get_font_family_name(GLYPHS_PATH) or GLYPHS_PATH.stem

        return jsonify({
            "status": "ok",
            # Hosted shared-demo instance (AVAR2_STUDIO_DEMO=1): the
            # frontend shows the landing overlay + shared-session note.
            "demo": os.environ.get("AVAR2_STUDIO_DEMO") == "1",
            "glyphs_path": str(GLYPHS_PATH) if GLYPHS_PATH else None,
            # original_path is the path the USER pointed at. glyphs_path
            # is the ACTIVE build/read path which becomes the shadow
            # whenever CONTROL AXES are declared. Frontend uses the
            # former as the "source identity" for swap detection so
            # declaring a control axis doesn't look like a swap.
            "original_path": str(ORIGINAL_PATH) if ORIGINAL_PATH else None,
            "source_format": SOURCE_FORMAT,
            "font_built": VARIABLE_FONT_PATH.exists() if VARIABLE_FONT_PATH else False,
            "family_name": family_name,
            "vf_family_id": f"{family_name}-VF" if family_name else None,
            # Report the ACTUAL served font's name (VARIABLE_FONT_PATH is what
            # /api/font serves and the download saves) so a transform-injected
            # name like ``…[SPAC].ttf`` matches the bytes. Fall back to the
            # avar2-dir derivation before the first build completes.
            "built_font_filename": (
                VARIABLE_FONT_PATH.name if VARIABLE_FONT_PATH and VARIABLE_FONT_PATH.exists()
                else _get_avar2_built_font_filename()
            ),
            "last_build_time": LAST_BUILD_TIME,
            "last_build_status": LAST_BUILD_STATUS,
            "last_build_error": LAST_BUILD_ERROR,
            "transform_error": LAST_TRANSFORM_ERROR,
            # Non-null ⇒ the served font is the plain fallback build;
            # the avar2 build is failing for this reason.
            "avar2_error": LAST_AVAR2_ERROR,
            "grade_error": LAST_GRADE_ERROR,
            "source_transform_error": LAST_SOURCE_TRANSFORM_ERROR,
            "origin_warning": LAST_ORIGIN_WARNING,
            # True ⇒ the mappings CSV changed after the last build —
            # the served font's avar2 table is stale. Auto-saved
            # coordinate edits set this instead of rebuilding; the
            # Preview tab rebuilds once on open when it's true.
            "build_stale": _is_build_stale(),
            # Fold in background regen/rebuild work: the layer save returns
            # instantly, so the queue's state alone would read false while the
            # shadow is still regenerating and the preview is stale.
            "building": JOBS.is_building() or BACKGROUND_WORK > 0,
            "build_job": JOBS.status().get("running"),
        })
    except Exception as e:
        print(f"Error in health endpoint: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/glyphs-file-status', methods=['GET'])
def glyphs_file_status():
    """Check if Glyphs file has unsaved changes."""
    if GLYPHS_PATH is None:
        return jsonify({"has_unsaved_changes": False, "file_path": None})
    try:
        has_unsaved = _check_glyphs_file_unsaved_changes(GLYPHS_PATH)
        return jsonify({
            "has_unsaved_changes": has_unsaved,
            "file_path": str(GLYPHS_PATH)
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "has_unsaved_changes": False
        }), 500


# ============================================================================
# Avar2 API Endpoints
# ============================================================================

# Track CSV modification times for external edit detection
_CSV_MODIFICATION_TIMES: Dict[str, float] = {}

def _get_avar2_csv_path() -> Optional[Path]:
    """Get path to avar2-mappings.csv (relative to Glyphs file or explicit)."""
    # For preview tool: use preview CSV if it exists (fresh start workflow)
    preview_csv = _get_preview_csv_path()
    if preview_csv and preview_csv.exists():
        return preview_csv
    
    # Fallback to explicit CSV path if set
    global CSV_PATH
    if CSV_PATH:
        return CSV_PATH
    
    # Default: look for avar2-mappings.csv in same directory as Glyphs file
    if GLYPHS_PATH:
        default_csv = GLYPHS_PATH.parent / "avar2-mappings.csv"
        if default_csv.exists():
            return default_csv
    
    return None


def _get_avar2_metadata_path() -> Optional[Path]:
    """Get path to axis-metadata.json (inside the .avar2-studio workdir)."""
    workdir = _get_preview_dir()
    if not workdir:
        return None
    return workdir / "axis-metadata.json"


def _get_preview_dir() -> Optional[Path]:
    """Return the per-project working directory.

    avar2-studio writes its tool-managed state into ``$GLYPHS_PARENT/.avar2-studio/``
    next to the .glyphs file — config, axis metadata, and built fonts all live
    here. The dir is created on demand. Designers should gitignore it; the
    sibling ``MyFont-avar.csv`` (outside this dir) is the authored artifact.
    """
    global PREVIEW_DIR
    if PREVIEW_DIR:
        return PREVIEW_DIR

    # Resolve against the ORIGINAL source, never GLYPHS_PATH: once a control
    # axis has brace layers GLYPHS_PATH points at the shadow, and deriving from
    # it nests a second .avar2-studio/ INSIDE the shadow dir.
    base = ORIGINAL_PATH or GLYPHS_PATH
    if not base:
        return None

    workdir = base.parent / ".avar2-studio"
    workdir.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR = workdir
    return workdir


def _get_preview_csv_path() -> Optional[Path]:
    """Get path to the avar2 CSV (sibling to the .glyphs file).

    e.g. for ``MyFont.glyphs`` the CSV is ``MyFont-avar.csv`` in the same
    directory. The CSV is the designer's authored artifact and is intentionally
    OUTSIDE the .avar2-studio workdir so it gets committed alongside the source.
    """
    global PREVIEW_CSV_PATH
    if PREVIEW_CSV_PATH:
        return PREVIEW_CSV_PATH

    # Resolve against the ORIGINAL source, never GLYPHS_PATH: once a control
    # axis has brace layers GLYPHS_PATH points at the shadow, which would put
    # the designer's authored CSV inside .avar2-studio/shadow/ and make every
    # /api/avar2/* lookup miss the real one next to the source.
    base = ORIGINAL_PATH or GLYPHS_PATH
    if not base:
        return None

    # Locked convention: family name is the source file's stem in both
    # .glyphs and .designspace flows, so the CSV name is deterministic
    # without loading the source.
    family_name = base.stem
    csv_path = base.parent / f"{family_name}-avar.csv"
    PREVIEW_CSV_PATH = csv_path
    return csv_path


def _get_preview_config_path() -> Optional[Path]:
    """Get path to the gftools-builder config inside the .avar2-studio workdir."""
    global PREVIEW_CONFIG_PATH
    if PREVIEW_CONFIG_PATH:
        return PREVIEW_CONFIG_PATH

    workdir = _get_preview_dir()
    if not workdir:
        return None

    config_path = workdir / "config.yaml"
    PREVIEW_CONFIG_PATH = config_path
    return config_path


def _get_avar2_font_dir() -> Optional[Path]:
    """Get avar2 font directory (collapsed into the shared build dir)."""
    workdir = _get_preview_dir()
    if not workdir:
        return None
    font_dir = workdir / "build"
    font_dir.mkdir(parents=True, exist_ok=True)
    return font_dir


def _initialize_preview_csv_from_glyphs() -> Optional[Path]:
    """Initialize preview CSV from the source file.

    Creates a CSV with ``Instance Name`` and one column per source axis.
    SPAC is deferred from v1 — no SPAC column is added on bootstrap.
    """
    csv_path = _get_preview_csv_path()
    if not csv_path:
        return None

    # If CSV exists, leave it alone.
    if csv_path.exists():
        return csv_path
    
    if not GLYPHS_PATH or not GLYPHS_PATH.exists():
        return None
    
    try:
        import csv
        font, _fmt = _source_font.load_source(GLYPHS_PATH)
        # All source axes get a column. SPAC support is deferred — even
        # if the source declares a SPAC axis it's treated as just
        # another parametric column with no special handling.
        parametric_axes = [
            axis["tag"].upper() for axis in _source_font.get_axes(font)
        ]
        source_instances = _source_font.get_source_instances(font)

        # Always create the CSV with the header so studio-only instance
        # creation works against an empty designspace (zero source instances).
        fieldnames = ["Instance Name"] + parametric_axes
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for entry in source_instances:
                row = {"Instance Name": entry["name"]}
                for tag, value in entry["coordinates"].items():
                    row[tag.upper()] = value
                for axis in parametric_axes:
                    row.setdefault(axis, 0)
                writer.writerow(row)

        print(f"Initialized preview CSV: {csv_path}", file=sys.stderr)
        return csv_path
    except Exception as e:
        print(f"Error initializing preview CSV: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()

    return None


def _initialize_preview_config_from_glyphs() -> Optional[Path]:
    """
    Initialize preview config.yaml from Glyphs file.
    Creates minimal config with sources, familyName, and fvarInstances from Glyphs.
    Only creates if config doesn't exist.
    """
    config_path = _get_preview_config_path()
    if not config_path:
        return None
    
    # Don't overwrite existing config — except to repair the empty-
    # fvarInstances case: an instance-less source produced ``ttf: []``,
    # which the gftools builder's schema rejects (failing every avar2
    # build). Delete and regenerate; avar2/STAT merge back on next build.
    if config_path.exists():
        try:
            import yaml
            existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            fvar = existing.get("fvarInstances") or {}
            if fvar and all(not v for v in fvar.values()):
                # Broken either way: an instance-less source shouldn't
                # carry the key at all (the builder's strictyaml schema
                # rejects ``ttf: []``), and an instance-BEARING source
                # should carry its instances — a stale empty list from
                # before they existed fails every avar2 build just the
                # same. Regenerate from the source in both cases.
                config_path.unlink()
            else:
                return config_path
        except Exception:
            return config_path
    
    if not GLYPHS_PATH or not GLYPHS_PATH.exists():
        return None
    
    try:
        import yaml
        font, _fmt = _source_font.load_source(GLYPHS_PATH)

        # Locked convention: family name from source file stem.
        family_name = _source_font.get_family_name(font, GLYPHS_PATH)

        axes = _source_font.get_axes(font)
        source_instances = _source_font.get_source_instances(font)

        fvar_instances = []
        for entry in source_instances:
            coords = {tag.lower(): float(value) for tag, value in entry["coordinates"].items()}
            if coords:
                fvar_instances.append({
                    "name": entry["name"],
                    "coordinates": coords,
                })

        # Built-font filename encodes axis tags exactly as gftools-builder
        # will produce them — preserve case (lowercase for registered axes,
        # uppercase for custom/parametric).
        parametric_tags = sorted(axis["tag"] for axis in axes if axis.get("tag"))
        if parametric_tags:
            font_filename = f"{family_name}[{','.join(parametric_tags)}].ttf"
        else:
            font_filename = f"{family_name}-VF.ttf"
        
        # Use absolute path for sources to avoid path issues
        sources_path = str(GLYPHS_PATH.resolve())

        # Only emit fvarInstances when the source declares instances.
        # An empty list crashes the gftools builder's strictyaml schema
        # ("ugly disallowed JSONesque flow mapping") — which silently
        # failed the whole avar2 build for instance-less sources, so the
        # preview never got avar2 or its user axes.
        config = {
            "sources": [sources_path],
            "familyName": family_name,
        }
        if fvar_instances:
            config["fvarInstances"] = {font_filename: fvar_instances}
        
        # Write config
        with config_path.open("w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        
        print(f"Initialized preview config: {config_path}", file=sys.stderr)
        return config_path
    except Exception as e:
        print(f"Error initializing preview config: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
    
    return None


# Extent written for an axis whose real range isn't known yet. Parametric
# axes are re-synced from the source's masters on every load, so a placeholder
# there is self-healing; traditional axes have no such anchor, so this value
# is also the marker that lets _repair_placeholder_ranges() spot an entry that
# was never really initialised and derive it properly.
_PLACEHOLDER_MIN, _PLACEHOLDER_MAX = -1000, 1000


def _is_placeholder_range(entry: Dict) -> bool:
    """True while an entry still carries the untouched placeholder extent.

    Used to distinguish "never initialised" from "the designer set this",
    so repairs never clobber a hand-edited range.
    """
    try:
        return (
            float(entry.get("min")) == float(_PLACEHOLDER_MIN)
            and float(entry.get("max")) == float(_PLACEHOLDER_MAX)
        )
    except (TypeError, ValueError):
        return False


def _is_degenerate_range(entry: Dict) -> bool:
    """True if an entry's extent has collapsed to a point.

    Axes carried by VIRTUAL masters (GRAD) or injected post-build (SPAC) have
    no real master spanning them, so the source reports them as [0, 0] — a
    slider with nowhere to go. Treated as repairable, but only when something
    actually defines the axis, so a genuinely unused axis stays as-is.
    """
    try:
        return float(entry.get("min")) >= float(entry.get("max"))
    except (TypeError, ValueError):
        return False


def _derive_traditional_range(tag: str, rows: Optional[List[Dict]] = None,
                              col: Optional[str] = None):
    """Real ``(min, max)`` for a traditional (non-parametric) axis.

    Parametric axes take their extent from the source's masters. Traditional
    axes have no such anchor, so recover it from whichever artifact actually
    defines the axis:

    1. an enabled transform that injects it (e.g. SPAC) — the transform's own
       min/max params, which are what it writes into fvar;
    2. the grade axis — the fixed registered GRAD extent;
    3. otherwise the CSV column's own values, which ARE the mapping corners
       (the same source the generated STAT table already uses).

    Returns ``None`` when nothing defines the axis, leaving the placeholder in
    place rather than inventing a range.
    """
    tag_upper = (tag or "").strip().upper()

    # 1. Transform-injected axes (SPAC): the transform owns the fvar extent.
    if ORIGINAL_PATH is not None:
        try:
            _transforms.discover()
            for transform, params in _transforms.active(ORIGINAL_PATH):
                if (transform.spec.injected_axis_tag or "").upper() != tag_upper:
                    continue
                lo, hi = params.get("min"), params.get("max")
                if lo is not None and hi is not None and float(lo) < float(hi):
                    return float(lo), float(hi)
        except Exception as exc:
            print(f"Warning: transform range lookup for {tag_upper} failed: {exc}",
                  file=sys.stderr)

    # 2. The grade axis has a fixed registered extent.
    if tag_upper == _grade.GRAD_TAG.upper():
        return float(_grade.GRAD_MIN), float(_grade.GRAD_MAX)

    # 3. Fall back to the CSV column's own spread. Blank cells mean "inherit",
    #    not zero, so they're skipped rather than counted as a corner.
    if col is None:
        return None
    values: List[float] = []
    for row in rows or []:
        raw = row.get(col)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            values.append(float(str(raw).strip()))
        except ValueError:
            continue
    if len(values) >= 2 and min(values) < max(values):
        return min(values), max(values)
    return None


def _repair_placeholder_ranges(metadata: Dict, rows: List[Dict], in_cols: List[str]) -> bool:
    """Re-derive any traditional axis still sitting at the placeholder extent.

    Without this, an axis seeded before its CSV column had values (or before
    its transform was enabled) would keep -1000/1000 forever — and that range
    reaches real fvar, not just the UI. Hand-edited ranges are left alone.
    Returns True if anything changed.
    """
    changed = False
    for key, entry in metadata.items():
        if not isinstance(entry, dict):
            continue
        if not (_is_placeholder_range(entry) or _is_degenerate_range(entry)):
            continue
        # Only axes that are actually a CSV column can be derived from its
        # values; the rest (GRAD, SPAC) fall through to their own definitions.
        col = key if key in in_cols else None
        derived = _derive_traditional_range(
            entry.get("registered_tag") or key, rows, col
        )
        if derived is None:
            continue
        entry["min"], entry["max"] = derived
        # A default carried over from the placeholder era can sit outside the
        # real range (e.g. opsz 72 against a 12..144 axis is fine, but a
        # stale one may not be) — clamp it so fvar stays well-formed.
        if isinstance(entry.get("default"), (int, float)):
            entry["default"] = max(derived[0], min(derived[1], float(entry["default"])))
        changed = True
    return changed


def _load_axis_metadata() -> Dict[str, Dict[str, any]]:
    """Load axis metadata from JSON file. Auto-populates with Glyphs file axes if missing."""
    metadata_path = _get_avar2_metadata_path()
    if not metadata_path:
        return {}

    # Create file with empty dict if it doesn't exist
    if not metadata_path.exists():
        try:
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump({}, f, indent=2)
        except Exception as e:
            print(f"Error creating axis metadata file: {e}", file=sys.stderr)
            return {}
    
    # Load existing metadata
    try:
        with metadata_path.open("r", encoding="utf-8") as f:
            metadata = json.load(f)
    except Exception as e:
        print(f"Error loading axis metadata: {e}", file=sys.stderr)
        metadata = {}
    
    # Get parametric axes from the source file and add to metadata if missing.
    if GLYPHS_PATH and GLYPHS_PATH.exists():
        try:
            font, _fmt = _source_font.load_source_cached(GLYPHS_PATH)
            metadata_updated = False

            for axis in _source_font.get_axes(font):
                axis_tag = axis["tag"]
                axis_tag_upper = axis_tag.upper()
                axis_name = axis.get("name") or axis_tag_upper
                # An axis declared in the source is only truly "parametric"
                # if it has master coverage. Empty axes (declared but with
                # min==max or no master deltas) are avar2 mapping inputs —
                # they should remain editable in the AVAR2 MAPPINGS UI.
                is_parametric = axis.get("has_master_coverage", True)

                if axis_tag_upper not in metadata:
                    # The source's masters already give the real extent — seed
                    # with it rather than a placeholder, so the entry is never
                    # briefly wrong (and stays right even if the re-sync in
                    # /api/avar2/axes doesn't run, e.g. no CSV yet). Axes on
                    # virtual masters report [0,0] here, so fall back to
                    # whatever defines them before the placeholder.
                    axis_min = float(axis.get("min", 0.0))
                    axis_max = float(axis.get("max", 0.0))
                    if axis_min >= axis_max:
                        axis_min, axis_max = _derive_traditional_range(axis_tag) or (
                            _PLACEHOLDER_MIN, _PLACEHOLDER_MAX
                        )
                    metadata[axis_tag_upper] = {
                        "display_name": axis_name,
                        "registered_tag": axis_tag.lower(),
                        "min": axis_min,
                        "max": axis_max,
                        "is_parametric": is_parametric,
                    }
                    metadata_updated = True
                else:
                    # Always re-sync the parametric flag — coverage can
                    # change as masters are added/removed.
                    if metadata[axis_tag_upper].get("is_parametric") != is_parametric:
                        metadata[axis_tag_upper]["is_parametric"] = is_parametric
                        metadata_updated = True
                    if metadata[axis_tag_upper].get("display_name") == axis_tag_upper:
                        metadata[axis_tag_upper]["display_name"] = axis_name
                        metadata_updated = True
            
            # Save updated metadata if we added any parametric axes
            if metadata_updated:
                _save_axis_metadata(metadata)
        except Exception as e:
            print(f"Error reading axes from Glyphs file for metadata: {e}", file=sys.stderr)
    
    return metadata


def _save_axis_metadata(metadata: Dict[str, Dict[str, any]]) -> bool:
    """Save axis metadata to JSON file."""
    metadata_path = _get_avar2_metadata_path()
    if not metadata_path:
        return False
    
    try:
        _csv_io.backup_sidecar(metadata_path)
        with metadata_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error saving axis metadata: {e}", file=sys.stderr)
        return False


def _secondary_axis_tags() -> set:
    """Tags the studio injects into the shadow (secondary/control axes).

    They exist in the shadow font but are NOT avar2 mapping columns, so the
    CSV sync must never create columns for them — see exclude_axes in
    csv_io.update_csv_from_glyphs.
    """
    try:
        if ORIGINAL_PATH is None:
            return set()
        return {
            str(ax.get("tag") or "").upper()
            for ax in (_control_axes.list_axes(ORIGINAL_PATH) or [])
            if ax.get("tag")
        }
    except Exception:
        return set()


def _non_mapping_axis_tags() -> set:
    """UPPERCASE tags that must never become CSV mapping columns: the
    studio-injected control axes plus every transform-injectable axis
    (SPAC/ROND/slnt) and GRAD. Instance flows load ``GLYPHS_PATH`` — the
    TRANSFORMED SHADOW, whose axis list includes all of these — so any
    CSV schema or row derivation from that font's axes must subtract
    this set, or the CSV mints bogus uppercase user axes that then
    compile as real fvar axes (the LCWD/SLNT/YMOD/ROND column bug)."""
    tags = set(_secondary_axis_tags())
    tags.add("GRAD")
    try:
        _transforms.discover()
        for t in _transforms.REGISTRY.values():
            tag = getattr(t.spec, "injected_axis_tag", None)
            if tag:
                tags.add(str(tag).upper())
    except Exception:  # noqa: BLE001 — a broken user transform must not block saves
        pass
    return tags


def _check_csv_external_edit(csv_path: Path) -> bool:
    """Check if CSV was modified externally. Returns True if modified externally."""
    csv_str = str(csv_path)
    current_mtime = csv_path.stat().st_mtime if csv_path.exists() else 0
    
    if csv_str in _CSV_MODIFICATION_TIMES:
        if current_mtime > _CSV_MODIFICATION_TIMES[csv_str]:
            return True
    
    _CSV_MODIFICATION_TIMES[csv_str] = current_mtime
    return False


def _update_csv_modification_time(csv_path: Path) -> None:
    """Update tracked modification time after we write to CSV."""
    csv_str = str(csv_path)
    if csv_path.exists():
        _CSV_MODIFICATION_TIMES[csv_str] = csv_path.stat().st_mtime


def _validate_axis_tag(tag: str) -> tuple[bool, Optional[str]]:
    """Validate OpenType axis tag. Returns (is_valid, error_message)."""
    if not tag:
        return False, "Tag cannot be empty"
    if len(tag) != 4:
        return False, "Tag must be exactly 4 characters"
    if not tag.islower():
        return False, "Tag must be lowercase"
    if not tag.isalnum():
        return False, "Tag must contain only alphanumeric characters"
    return True, None


def _get_glyphs_axis_tags() -> set:
    """Get set of axis tags from the source file (source of truth for parametric axes).

    Name kept as ``_get_glyphs_axis_tags`` for git-blame stability; works on
    both .glyphs and .designspace via the source_font dispatcher.
    """
    if not GLYPHS_PATH or not GLYPHS_PATH.exists():
        return set()

    try:
        font, _fmt = _source_font.load_source_cached(GLYPHS_PATH)
        return {axis["tag"].lower() for axis in _source_font.get_axes(font)}
    except Exception as e:
        print(f"Error reading axes from source file: {e}", file=sys.stderr)
        return set()


def _add_missing_instance_to_csv(instance_name: str, glyphs_coords: Dict[str, float], csv_path: Path) -> bool:
    """Add a missing instance to CSV with blank traditional axis values."""
    import csv
    try:
        # Use normalized read function
        
        rows, fieldnames, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
        
        if not fieldnames:
            return False
        
        # Create new row with instance name
        new_row = {"Instance Name": instance_name}
        
        # Initialize all columns (use normalized fieldnames)
        for col in fieldnames:
            if col == "Instance Name":
                continue
            elif col in in_cols:
                # Traditional axes: blank
                new_row[col] = ""
            elif col in out_cols:
                # Parametric axes: use value from Glyphs (match by uppercase)
                # Find matching glyphs coordinate (case-insensitive)
                glyphs_key = None
                for glyphs_tag in glyphs_coords.keys():
                    if glyphs_tag.upper() == col:
                        glyphs_key = glyphs_tag
                        break
                if glyphs_key:
                    new_row[col] = str(glyphs_coords[glyphs_key])
                else:
                    new_row[col] = ""
            else:
                # Other columns: blank
                new_row[col] = ""
        
        # Add new row
        rows.append(new_row)
        
        # Write updated CSV (use normalized fieldnames)
        _csv_io.backup_sidecar(csv_path)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        return True
    except Exception as e:
        print(f"Error adding instance to CSV: {e}", file=sys.stderr)
        return False


@app.route('/api/avar2/instances', methods=['GET'])
def get_avar2_instances():
    """Get instances matched to avar2 mappings. Automatically adds missing instances to CSV."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({
                "error": "avar2-mappings.csv not found",
                "suggestion": "Set CSV path or place avar2-mappings.csv in same directory as Glyphs file"
            }), 404
        
        # Import matching function
        
        # Get Glyphs instances
        glyphs_instances = _csv_io.get_glyphs_instances(GLYPHS_PATH)
        
        # Get matches
        matches = _csv_io.match_instances(GLYPHS_PATH, csv_path)
        
        # Add missing instances to CSV — unless an existing row already
        # sits at the instance's exact parametric location. A RENAMED row
        # keeps its coordinates, and the source still carries the old
        # name (the studio never writes the original): re-adding by name
        # would resurrect the old instance as a coordinate duplicate on
        # every refresh after a rename.
        rows, _fns, _in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
        existing_locs = []
        for row in rows:
            loc = {}
            for col in out_cols:
                v = (row.get(col) or "").strip()
                if v:
                    try:
                        loc[col.upper()] = float(v)
                    except ValueError:
                        pass
            if loc:
                existing_locs.append(loc)

        out_upper = {str(c).upper() for c in out_cols}

        def _row_already_at(coords):
            # Compare only over the CSV's parametric columns: a source
            # instance also reports control axes (lcwd/ymod at 0) that
            # rows never carry.
            cu = {str(k).upper(): float(v) for k, v in (coords or {}).items()
                  if str(k).upper() in out_upper}
            if not cu:
                return False
            return any(
                all(abs(loc.get(k, 1e9) - v) <= 0.05 for k, v in cu.items())
                for loc in existing_locs
            )

        added_count = 0
        for match in matches:
            if match.get("match_status") == "missing_in_csv":
                instance_name = match["instance_name"]
                glyphs_coords = match.get("glyphs_coordinates", {})
                if _row_already_at(glyphs_coords):
                    continue
                if _add_missing_instance_to_csv(instance_name, glyphs_coords, csv_path):
                    added_count += 1
        
        # If we added instances, reload matches
        if added_count > 0:
            matches = _csv_io.match_instances(GLYPHS_PATH, csv_path)

        # And keep renamed instances out of the LISTING too: a source
        # instance whose location an existing row holds is that row, not
        # a second instance to display.
        matches = [m for m in matches
                   if not (m.get("match_status") in ("missing_in_csv", "mismatch")
                           and not any(r.get("Instance Name", "").strip() == m.get("instance_name")
                                       for r in rows)
                           and _row_already_at(m.get("glyphs_coordinates", {})))]

        return jsonify({
            "instances": matches,
            "csv_path": str(csv_path),
            "added_instances": added_count
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/glyph-coverage', methods=['GET'])
def glyph_coverage():
    """Return per-axis glyph coverage (v1 read-only for CONTROL AXES).

    Walks brace layers (``.glyphs``) or alternate-master UFOs
    (``.designspace``) and reports, per axis, the names of glyphs
    that contribute variation along that axis. The frontend uses
    ``covers_count / total_glyphs`` + the ``kind`` classification to
    surface axes under the appropriate panel:

      - ``"universal"`` (100% coverage) → stays under AVAR2 MAPPINGS /
                                          parametric.
      - ``"scoped"``    (anything less) → CONTROL AXES.

    Response shape::

        {
          "axes": [
            {
              "tag": "XOPQ",
              "name": "X-Opacity",
              "covers": ["A", "B", ..., "z"],
              "covers_count": 245,
              "total_glyphs": 245,
              "kind": "universal"
            },
            ...
          ]
        }
    """
    if GLYPHS_PATH is None:
        return jsonify({"axes": []})
    try:
        font, _fmt = _source_font.load_source_cached(GLYPHS_PATH)
        coverage = _glyph_coverage.compute_coverage(font)

        # Pair the coverage data with axis names / display info from the
        # already-loaded source axes so the frontend gets one
        # self-sufficient response. Axis tag is the join key.
        source_axes = _source_font.get_axes(font)
        axis_by_tag = {ax["tag"]: ax for ax in source_axes}

        out = []
        for tag, info in coverage.items():
            axis_meta = axis_by_tag.get(tag, {})
            out.append({
                "tag": tag,
                "name": axis_meta.get("name") or tag,
                "covers": info["covers"],
                "covers_count": info["covers_count"],
                "total_glyphs": info["total_glyphs"],
                "kind": info["kind"],
                # Per-glyph brace-layer / alternate-master locations —
                # lets the frontend render source-derived scoped axes
                # in the same layers panel as studio ones (read-only).
                "layers": info.get("layers", []),
                # Design-space axis extremes, same units as the layer
                # locations above — the frontend prefers these over the
                # built font's fvar for coverage classification.
                "min": info.get("min"),
                "default": info.get("default"),
                "max": info.get("max"),
                # ``source`` lets the frontend distinguish source-derived
                # axes (read from brace layers / alternate masters) from
                # studio-declared control axes (read from the sidecar).
                # Only the latter get edit / delete affordances in v2.
                "source": "source",
            })

        # Merge in studio-declared control axes from the sidecar.
        # If an axis tag is in the sidecar, it's authoritatively a
        # studio-declared control axis — re-tag any existing
        # source-derived entry with ``source: "studio"`` and overlay
        # the sidecar's metadata + coverage list. Otherwise append
        # the sidecar entry as a new row.
        #
        # The retag matters once the shadow .glyphs has the axis in
        # its axis list (slice 2.2+): a source-derived entry would
        # otherwise hide the studio origin and the UI loses its
        # edit / delete affordances.
        try:
            total = next(iter(coverage.values()), {}).get("total_glyphs", 0) if coverage else 0
            sidecar_path = ORIGINAL_PATH if ORIGINAL_PATH is not None else GLYPHS_PATH
            by_tag = {str(a["tag"]).lower(): a for a in out}
            sidecar_axes = list(_control_axes.list_axes(sidecar_path))
            # Resolve each studio layer's parametric location to its master
            # corner / instance name, so the layers panel can show it — the
            # same names the shadow's Fontra source labels carry.
            corner_of = None
            if isinstance(font, GSFont) and sidecar_axes:
                try:
                    sidecar_tags = {str(a.get("tag") or "").lower() for a in sidecar_axes}
                    control_idx = {
                        i for i, fax in enumerate(getattr(font, "axes", None) or [])
                        if str(getattr(fax, "axisTag", "")).lower() in sidecar_tags
                    }
                    corner_of = _control_axes.corner_name_resolver(
                        font, control_idx, ORIGINAL_PATH
                    )
                except Exception as corner_exc:  # noqa: BLE001
                    print(f"Warning: corner-name resolution unavailable: {corner_exc}", file=sys.stderr)
            for ax in sidecar_axes:
                tag_lower = (ax.get("tag") or "").lower()
                if not tag_lower:
                    continue
                existing = by_tag.get(tag_lower)
                # v2.7 unified schema: ``layers`` is the canonical
                # array. Coverage is derived from unique glyph names.
                layers = list(ax.get("layers") or [])
                if corner_of is not None:
                    for entry in layers:
                        corner = corner_of(entry.get("location"))
                        if corner:
                            entry["corner"] = corner
                derived_coverage: List[str] = []
                seen_glyphs: set = set()
                for entry in layers:
                    g = entry.get("glyph")
                    if g and g not in seen_glyphs:
                        seen_glyphs.add(g)
                        derived_coverage.append(g)

                if existing is not None:
                    src_covers = existing.get("covers") or []
                    # Union — derived (designer intent) wins for the
                    # ordering; brace-layer-derived (from shadow) is
                    # secondary. Should overlap perfectly once
                    # regenerate_shadow has run.
                    merged = list(dict.fromkeys([*derived_coverage, *src_covers]))
                    existing["source"] = "studio"
                    existing["name"] = ax.get("display_name") or existing.get("name")
                    existing["covers"] = merged
                    existing["covers_count"] = len(merged)
                    existing["default"] = ax.get("default")
                    existing["min"] = ax.get("min")
                    existing["max"] = ax.get("max")
                    existing["layers"] = layers
                    existing["kind"] = _glyph_coverage._classify(
                        len(merged), existing.get("total_glyphs", total)
                    )
                else:
                    out.append({
                        "tag": tag_lower,
                        "name": ax.get("display_name") or tag_lower,
                        "covers": derived_coverage,
                        "covers_count": len(derived_coverage),
                        "total_glyphs": total,
                        "kind": _glyph_coverage._classify(len(derived_coverage), total),
                        "source": "studio",
                        "default": ax.get("default"),
                        "min": ax.get("min"),
                        "max": ax.get("max"),
                        "layers": layers,
                    })
                    by_tag[tag_lower] = out[-1]
        except Exception as e:
            print(f"Warning: failed to merge control-axes sidecar: {e}", file=sys.stderr)

        # Stable ordering: universal first (least interesting), then
        # scoped. Within each bucket, by tag.
        kind_order = {"universal": 0, "scoped": 1}
        out.sort(key=lambda a: (kind_order.get(a["kind"], 99), a["tag"]))
        # Glyph-name → character map for the layer thumbnails: they
        # render TEXT in the built font, so "eight" must become "8" —
        # typesetting the name only works for single-char names.
        try:
            glyph_chars = _glyph_coverage.compute_glyph_chars(font)
        except Exception:
            glyph_chars = {}
        return jsonify({"axes": out, "glyph_chars": glyph_chars})
    except Exception as e:
        print(f"Error in /api/glyph-coverage: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------------------------
# TRANSFORMS — post-build VF→VF steps (e.g. SPAC spacing axis)
#
# Available transforms are discovered globally (built-ins +
# ~/.avar2-studio/transforms/); which are enabled + their params is
# per-project state in ``<basename>-transforms.json``. Enabling one runs
# it over the compiled font on every build (see _apply_transform_chain)
# and, for axis-injecting transforms like SPAC, surfaces the new axis as
# a parametric slider (see the built-font overlay in get_axes).
# ----------------------------------------------------------------------


# Cached TTFont for /api/mapped-location — reparsing the built font per
# slider tick would dwarf the actual avar evaluation. Keyed on
# (path, mtime) so rebuilds invalidate naturally.
_MAPPED_FONT_LOCK = threading.Lock()
_MAPPED_FONT_CACHE: Dict[str, object] = {"key": None, "font": None}


@app.route('/api/mapped-location', methods=['GET'])
def mapped_location():
    """Evaluate the BUILT font's avar table at a user-space location.

    Returns the effective post-mapping value of every fvar axis,
    denormalized back to user space. The preview uses this to make the
    parametric sliders FOLLOW the avar2 mappings as the designer drags
    wght/opsz — computed from the compiled table itself (fontTools
    ``avar.renormalizeLocation``), not a JS reimplementation of the
    mapping model, so what the sliders show is exactly what the font
    does. Fonts without an avar table just echo the input location.
    """
    if VARIABLE_FONT_PATH is None or not VARIABLE_FONT_PATH.exists():
        return jsonify({"error": "No built font"}), 404
    try:
        coords = json.loads(request.args.get("coordinates", "{}")) or {}
    except (ValueError, TypeError):
        return jsonify({"error": "coordinates must be a JSON object"}), 400

    try:
        return jsonify({"mapped": _evaluate_mapped_location(coords)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _evaluate_mapped_location(coords: Dict) -> Dict[str, float]:
    """Evaluate the built font's avar table at a user-space location;
    returns every fvar axis's effective post-mapping value in user
    space. Shared by /api/mapped-location (slider reflection) and
    /api/export-font (default relocation)."""
    from fontTools.varLib.models import normalizeLocation

    with _MAPPED_FONT_LOCK:
        key = (str(VARIABLE_FONT_PATH), VARIABLE_FONT_PATH.stat().st_mtime)
        if _MAPPED_FONT_CACHE["key"] != key:
            _MAPPED_FONT_CACHE["font"] = TTFont(str(VARIABLE_FONT_PATH), lazy=True)
            _MAPPED_FONT_CACHE["key"] = key
        tt = _MAPPED_FONT_CACHE["font"]

        axes = {
            a.axisTag: (a.minValue, a.defaultValue, a.maxValue)
            for a in tt["fvar"].axes
        }
        loc = {}
        for tag, (lo, d, hi) in axes.items():
            try:
                v = float(coords.get(tag, d))
            except (TypeError, ValueError):
                v = d
            loc[tag] = max(lo, min(hi, v))
        norm = normalizeLocation(loc, axes)
        avar = tt.get("avar")
        renorm = avar.renormalizeLocation(norm, tt) if avar is not None else norm

    def _denorm(tag, n):
        lo, d, hi = axes[tag]
        return d + n * ((hi - d) if n > 0 else (d - lo))

    # Axes absent from the renormalized dict sit at their default
    # (normalized 0) — emit them too so the caller needn't guess.
    return {tag: round(_denorm(tag, renorm.get(tag, 0.0)), 2) for tag in axes}


def _build_export_source(parametric_location: Dict[str, float], export_dir: Path,
                         source_path: Optional[Path] = None) -> Path:
    """Materialize the export source: the loaded font as a designspace
    with ONE ADDED MASTER — a fully interpolated instance (outlines,
    metrics, kerning) at the mapped parametric location — set as the
    default, so fontc compiles the export resting on that master
    natively. This is the designer's formulation: corner deltas
    anchored to the new default master, natural-size gvar. (The
    earlier binary rebase via fontTools instancer preserved behavior
    by splitting every variation region around the new origin — ~6x
    the font size.) The user's source file is never touched."""
    # GC paused: staging interpolates a new default master over the
    # composed source and writes every master out as a UFO — the same
    # allocation storm as the build paths, and unwrapped it spent ~9
    # minutes before the first UFO landed.
    with _gc_paused():
        import glyphsLib
        import ufoLib2
        from fontmake.instantiator import Instantiator
        from fontTools.designspaceLib import SourceDescriptor, InstanceDescriptor

        src_dir = export_dir / "sources"
        src_dir.mkdir(parents=True, exist_ok=True)

        source_path = Path(source_path) if source_path is not None else GLYPHS_PATH
        if str(source_path).lower().endswith(".glyphs"):
            gsfont = glyphsLib.GSFont(str(source_path))
            nonexport = {str(g.name) for g in gsfont.glyphs
                         if not getattr(g, "export", True)}
            ds = glyphsLib.to_designspace(gsfont, ufo_module=ufoLib2)
        else:
            from fontTools.designspaceLib import DesignSpaceDocument
            ds = DesignSpaceDocument.fromfile(str(source_path))
            ds.loadSourceFonts(ufoLib2.Font.open)
            _EXPORT_KEY = "com.schriftgestaltung.Glyphs.Export"
            nonexport = set()
            for s in ds.sources:
                for layer in s.font.layers:
                    for g in layer:
                        if g.lib.get(_EXPORT_KEY) is False:
                            nonexport.add(g.name)

        # Non-exporting glyphs: fontc's native .glyphs reader drops them,
        # but glyphsLib's designspace conversion KEEPS them, writing an
        # export lib key that lands on only some masters' glifs — fontc
        # then sees the glyph in some masters and not others and dies with
        # "interpolation-incompatible paths" (Crispy's braceleft, flagged
        # export=0, broke every designspace build this way). Strip them
        # from every source so this build matches the .glyphs build.
        if nonexport:
            kept = set()
            for s in ds.sources:
                for layer in s.font.layers:
                    for name in list(layer.keys()):
                        if name in nonexport:
                            del layer[name]
                        else:
                            kept.add(name)
            # An EXPORTING glyph referencing a stripped one would now have a
            # dangling component — fail loudly rather than compile garbage
            # (Glyphs decomposes these at export; wire that up if a source
            # ever legitimately needs it).
            dangling = set()
            for s in ds.sources:
                for layer in s.font.layers:
                    for g in layer:
                        for comp in g.components:
                            if comp.baseGlyph in nonexport:
                                dangling.add(f"{g.name} → {comp.baseGlyph}")
            if dangling:
                raise RuntimeError(
                    "exporting glyph(s) reference non-exporting component(s): "
                    + ", ".join(sorted(dangling)))
            for s in ds.sources:
                font = s.font
                order = font.lib.get("public.glyphOrder")
                if order:
                    font.lib["public.glyphOrder"] = [n for n in order if n not in nonexport]
                if font.kerning:
                    font.kerning = {pair: v for pair, v in font.kerning.items()
                                    if pair[0] not in nonexport and pair[1] not in nonexport}
                if font.groups:
                    font.groups = {k: [n for n in v if n not in nonexport]
                                   for k, v in font.groups.items()}

        # BAKED-CORRECTION braces can sit at a master's EXACT location —
        # a correction pinned at the control axis's default (ymod rests
        # on its corrected end) over a master corner. fontc renders the
        # brace there, but fontmake's Instantiator refuses duplicate
        # source locations outright ("Locations must be unique", first
        # seen on glyph 'B'). Reproduce the compiled behavior: copy each
        # such brace glyph OVER the coincident master's glyph, then drop
        # the brace source.
        import copy as _copy
        full_by_loc = {}
        for src in ds.sources:
            if not src.layerName:
                full_by_loc[tuple(sorted((src.location or {}).items()))] = src
        merged = 0
        for src in list(ds.sources):
            if not src.layerName:
                continue
            master = full_by_loc.get(tuple(sorted((src.location or {}).items())))
            if master is None:
                continue
            brace_layer = src.font.layers[src.layerName]
            target = master.font.layers.defaultLayer
            for g in brace_layer:
                dup = _copy.deepcopy(g)
                if g.name in target:
                    del target[g.name]
                target.addGlyph(dup)
                merged += 1
            ds.sources.remove(src)
        if merged:
            print(f"  export: merged {merged} baked-correction glyph(s) from "
                  f"brace sources coincident with masters", file=sys.stderr)

        if str(source_path).lower().endswith(".glyphs"):
            for i, s in enumerate(ds.sources):
                fname = Path(s.filename or f"master{i}.ufo").name
                s.font.save(str(src_dir / fname), overwrite=True)
                s.path = str(src_dir / fname)

        name_by_tag = {a.tag: a.name for a in ds.axes}
        design_loc = {
            name_by_tag[t]: float(v)
            for t, v in parametric_location.items()
            if t in name_by_tag
        }
        # Fill unspecified axes at their (design-space) defaults.
        for a in ds.axes:
            if a.name not in design_loc:
                design_loc[a.name] = float(a.map_forward(a.default))

        inst = InstanceDescriptor()
        inst.familyName = ds.sources[0].familyName or "Export"
        inst.styleName = "ExportOrigin"
        inst.location = dict(design_loc)
        generator = Instantiator.from_designspace(ds, round_geometry=True)
        origin_ufo = generator.generate_instance(inst)

        # Sources that sit EXACTLY at the new origin (baked corrections
        # pinned at their axis defaults over this spot) are already in
        # the interpolated origin — the instantiator returns the source
        # outline at an exact source location. Keeping them would hand
        # fontc two sources at one location ("duplicate location",
        # glyph 'multiply'). Sparse ones are dropped; a coincident FULL
        # master would mean the chosen default IS a master — fail loud.
        coincident = [src for src in ds.sources
                      if {k: float(v) for k, v in (src.location or {}).items()} == design_loc]
        for src in coincident:
            if not src.layerName:
                raise RuntimeError(
                    "export default location coincides with master "
                    f"'{src.name}' — pick a location that is not a master, "
                    "or export without 'set default'")
            ds.sources.remove(src)
        if coincident:
            print(f"  export: dropped {len(coincident)} brace source(s) coincident "
                  f"with the new origin (content baked into the origin master)",
                  file=sys.stderr)
        origin_path = src_dir / "ExportOrigin.ufo"
        origin_ufo.save(str(origin_path), overwrite=True)

        s = SourceDescriptor()
        s.path = str(origin_path)
        s.name = "Export Origin"
        s.familyName = inst.familyName
        s.styleName = "ExportOrigin"
        s.location = dict(design_loc)
        ds.addSource(s)

        # The added master becomes the DEFAULT: designspace axis defaults
        # move to its location (parametric axes carry no maps, so design
        # coords are axis coords).
        for a in ds.axes:
            if a.name in design_loc:
                a.default = design_loc[a.name]

        # SAME stem as the original: the gftools builder derives output
        # names from the source filename, and the config's stat/avar2
        # sections are keyed by that name — a suffix here breaks the keys.
        out = export_dir / f"{Path(str(source_path)).stem}.designspace"
        ds.write(str(out))
        return out


def _apply_hidden_axes(path: Path, hidden_tags) -> None:
    """Set the fvar HIDDEN flag (0x0001) on the given axes so end-user
    font pickers don't surface them. In place; unknown tags ignored."""
    tags = {str(t) for t in (hidden_tags or [])}
    if not tags:
        return
    tt = TTFont(str(path))
    changed = False
    for a in tt["fvar"].axes:
        if str(a.axisTag) in tags and not (a.flags & 0x0001):
            a.flags |= 0x0001
            changed = True
    if changed:
        tt.save(str(path))


def _save_woff2(font_path, out_path):
    """Lossless TTF→WOFF2 (fontTools Brotli flavor)."""
    font = TTFont(str(font_path))
    font.flavor = "woff2"
    font.save(str(out_path))
    font.close()
    return Path(out_path)


def _export_pipeline(font_path, hidden_axes, default_location, export_dir,
                     format="ttf"):
    """The export build, caller-agnostic: re-origin at ``default_location``
    if given, post-transform, flag hidden axes. Returns the artifact path.
    Runs in the build worker (queued export jobs); ``font_path`` is the
    current built font, read-only. ``export_dir`` is the job's own dir.
    ``format`` "woff2" converts the result losslessly.
    """
    font_path = Path(font_path)
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    if not default_location:
        out_path = export_dir / font_path.name
        shutil.copy2(str(font_path), str(out_path))
    else:
        tt = TTFont(str(font_path), lazy=True)
        fvar_tags = {str(a.axisTag) for a in tt["fvar"].axes}
        requested = {}
        for t, v in default_location.items():
            if str(t) in fvar_tags:
                try:
                    requested[str(t)] = float(v)
                except (TypeError, ValueError):
                    pass
        if not requested:
            raise ValueError("default_location has no known axis values")
        tt.close()

        # Where the combination lands in the design space — the
        # parametric axes get rebased there so the new origin
        # renders this exact master.
        # The parametric location the export will REST at — the new
        # default master gets interpolated exactly here.
        # The authored mapping rows come FIRST: with build-time
        # relocation off, the preview font's table cannot express the
        # default-location row (it is excluded as the un-storable
        # base), so evaluating THAT table at the default input
        # returns the compiled corner — and the export would rest on
        # the wrong style (observed: "set default" at wght 400 aimed
        # at the origin master and collided with its sources). An
        # exact row match uses the authored out:; everything else
        # (in-between locations) still evaluates the built table,
        # which is exact away from the default input.
        mapped = None
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load(Path(_get_preview_config_path()).read_text()) or {}
            av = cfg.get("avar2")
            rows = av if isinstance(av, list) else (next(iter(av.values())) if isinstance(av, dict) and av else [])
            req_keys = {str(k).lower() for k in requested}
            for m in rows or []:
                ins = m.get("in") or {}
                # Same axis SET and equal values — a row that merely
                # omits an axis must not match by default-filling, or
                # the parametric-identity rows (in: XOPQ/XTRA/YOPQ)
                # match any user request and the export rests on
                # whichever row comes first (shipped once: the
                # export landed on 1420/150/122 instead of the
                # ORIGIN row).
                if {str(k).lower() for k in ins} != req_keys:
                    continue
                ins_ci = {str(k).lower(): float(v) for k, v in ins.items()}
                if all(abs(ins_ci[str(k).lower()] - float(v)) < 0.01
                       for k, v in requested.items()):
                    mapped = {str(t): float(v) for t, v in (m.get("out") or {}).items()}
                    break
        except Exception as exc:  # noqa: BLE001 — fall back to the table
            print(f"export: authored-row lookup failed ({exc}); using the built table",
                  file=sys.stderr)
        if mapped is None:
            mapped = _evaluate_mapped_location(requested)
        export_defaults = {
            t: round(mapped[t]) for t in fvar_tags if t not in requested and t in mapped
        }

        # Interpolate the new default master at the mapped
        # location and stage a designspace resting on it.
        export_source = _build_export_source(export_defaults, export_dir)

        # Config for the export build: regenerated against the
        # staged designspace, with blank in: cells MATERIALIZED
        # at the standard declared defaults — blank means "at
        # default", and with the default relocated the blanks
        # would alias onto the new origin ("Locations must be
        # unique").
        export_workdir = export_dir / "work"
        export_workdir.mkdir(parents=True, exist_ok=True)
        export_config = export_workdir / "config.yaml"
        shutil.copy2(str(_get_preview_config_path()), str(export_config))
        # The axis metadata must travel with the config:
        # _declared_axis_defaults resolves it as a config
        # sibling, and without it the blank-cell fill silently
        # no-ops — blanks then rebind to the relocated defaults
        # and alias rows ("Locations must be unique").
        _meta_src = _get_avar2_metadata_path()
        if _meta_src and Path(_meta_src).exists():
            shutil.copy2(str(_meta_src), str(export_workdir / "axis-metadata.json"))
        from .build import config_generator as _cfg_gen
        _cfg_gen.update_config(
            csv_path=_get_preview_csv_path(),
            config_path=export_config,
            backup=False,
            source_path=export_source,
            fill_in_defaults=True,
        )

        build_env = _builder_env(default_overrides=requested)
        fontc_path = shutil.which("fontc", path=build_env["PATH"])
        if not fontc_path:
            raise RuntimeError("fontc not found in PATH")
        # Same in-process pipeline as the preview build (see
        # _build_in_process); the requested default location rides in
        # as the declared-axis override, exactly as it did via env.
        if os.environ.get("AVAR2_STUDIO_GFTOOLS_BUILDER") == "1":
            builder_cmd = ["gftools", "builder", "--experimental-fontc", fontc_path, str(export_config.resolve())]
            result = subprocess.run(builder_cmd, capture_output=True, text=True, cwd=str(export_workdir), env=build_env)
            if result.returncode != 0:
                raise RuntimeError("Export build failed: %s"
                                   % (result.stderr or result.stdout or "")[-800:])
        else:
            _build_in_process(export_config, export_workdir, fontc_path,
                              default_overrides=requested)
        produced = []
        for candidate in (export_workdir / "fonts", export_workdir.parent / "fonts", export_dir / "fonts"):
            produced += list(candidate.glob("**/*.ttf")) if candidate.exists() else []
        produced = sorted(set(produced), key=lambda p: p.stat().st_mtime, reverse=True)
        if not produced:
            raise RuntimeError("Export build produced no font")
        out_path = export_dir / produced[0].name
        shutil.move(str(produced[0]), str(out_path))
        # Same post-build transforms (SPAC…) as the preview.
        out_path = Path(_apply_transform_chain(out_path))

    _apply_hidden_axes(out_path, hidden_axes)
    if format == "woff2":
        out_path = _save_woff2(out_path, out_path.with_suffix(".woff2"))
    return out_path


@app.route('/api/export-font', methods=['POST'])
def export_font():
    """Queue an export job; the artifact is polled for and downloaded.

    Body::

        {"hidden_axes": ["XOPQ", ...],
         "default_location": {"opsz": 12, "wght": 900} | null}

    ``hidden_axes`` sets the fvar HIDDEN flag so font pickers don't
    surface those axes. ``default_location`` re-origins the export: the
    plain build's parametric defaults are rebased to the avar2-mapped
    location of the requested combination (instancer L3, applied by
    the gen-avar2 shim while the font is still plain — fontTools
    refuses partial-instancing once avar2 exists) and the table is
    generated against the requested user-axis defaults, so the font
    OPENS at that style with every axis range intact. The preview's
    served font is never touched.

    Returns 202 + job id; the client polls ``/api/build/status`` and
    fetches ``/api/export/<job id>/download`` when done.
    """
    if VARIABLE_FONT_PATH is None or not VARIABLE_FONT_PATH.exists():
        return jsonify({"error": "No built font"}), 404
    body = request.get_json(silent=True) or {}
    hidden = body.get("hidden_axes") or []
    default_location = body.get("default_location") or None
    fmt = body.get("format") or "ttf"
    if fmt not in ("ttf", "woff2"):
        return jsonify({"error": "format must be ttf or woff2"}), 400

    if default_location:
        tt = TTFont(str(VARIABLE_FONT_PATH), lazy=True)
        fvar_tags = {str(a.axisTag) for a in tt["fvar"].axes}
        tt.close()
        requested = {}
        for t, v in default_location.items():
            if str(t) in fvar_tags:
                try:
                    requested[str(t)] = float(v)
                except (TypeError, ValueError):
                    pass
        if not requested:
            return jsonify({"error": "default_location has no known axis values"}), 400

    job_id = JOBS.submit_export(hidden_axes=hidden, default_location=default_location,
                                format=fmt)
    if job_id is None:
        return jsonify({"error": "No source loaded"}), 400
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@app.route('/api/transforms', methods=['GET'])
def list_transforms():
    """Return the available transforms merged with this project's enabled
    state + params."""
    if ORIGINAL_PATH is None:
        return jsonify({"transforms": []})
    try:
        _transforms.discover()
        return jsonify({
            "transforms": _transforms.available(ORIGINAL_PATH),
            "sidecar_path": str(_transforms.sidecar_path_for(ORIGINAL_PATH)),
        })
    except Exception as e:
        print(f"Error listing transforms: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


@app.route('/api/transforms', methods=['PUT'])
def update_transforms():
    """Replace the project's transform set. Body::

        {"transforms": [
            {"type": "spac", "enabled": true, "params": {"min": -20, "max": 40}}
        ]}

    Unknown types are dropped; params are coerced against each transform's
    schema. Persists to the sidecar, then rebuilds so the preview + axes
    reflect the new chain (transforms are VF post-processors — no shadow
    rewrite needed)."""
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    entries = data.get("transforms")
    if not isinstance(entries, list):
        return jsonify({"error": "Body must include 'transforms' as a list."}), 400
    try:
        _transforms.discover()
        previous = {}
        try:
            previous = {t.get("id"): t for t in _transforms.available(ORIGINAL_PATH)}
        except Exception as prev_exc:  # noqa: BLE001
            print(f"transforms: could not read previous state: {prev_exc}", file=sys.stderr)
        # Guardrail: a transform whose engine reads specific axes
        # (round_corners' XOPQ/XTRA radius formula) is refused on a
        # source that doesn't declare them — enabling it would build an
        # inert transform (a silently sharp font must not pass as ok).
        src_tags = _get_glyphs_axis_tags()
        if src_tags:  # unreadable source → the engine's own raise surfaces instead
            for e in entries:
                if not isinstance(e, dict) or not e.get("enabled"):
                    continue
                t = _transforms.REGISTRY.get(str(e.get("type", "")))
                req = getattr(getattr(t, "spec", None), "requires_axis_tags", ()) if t else ()
                missing = [tag for tag in req if tag.lower() not in src_tags]
                if missing:
                    return jsonify({"error": (
                        f"{t.spec.name} needs the parametric ax{'es' if len(missing) > 1 else 'is'} "
                        f"{', '.join(missing)} — this source doesn't declare "
                        f"{'them' if len(missing) > 1 else 'it'}.")}), 400
        try:
            stored = _transforms.set_active(ORIGINAL_PATH, entries)
        except ValueError as ve:
            # Invalid config (e.g. SPAC min >= max) — reject with feedback
            # instead of persisting an enabled-but-doomed transform.
            return jsonify({"error": str(ve)}), 400
        # A change that involves a SOURCE-stage transform (round_corners) —
        # enabling one, disabling one, or changing its SOURCE params —
        # changes the FILE the compiler must read, so it goes through the
        # same regen-and-rebuild path the grade toggle uses. Edits limited
        # to a transform's declared font_stage_param_keys (round_corners'
        # per-style percents) only change its VF->VF apply, so they take
        # the fast trigger_build path instead of re-rounding the shadow.
        def _source_touched():
            for e in stored:
                # ``stored`` is set_active's return: the UI list, whose
                # entries carry "id" (sidecar entries carry "type").
                tid = e.get("type") or e.get("id")
                t = _transforms.REGISTRY.get(tid)
                if t is None or getattr(t.spec, "stage", "font") != "source":
                    continue
                prev = previous.get(tid) or {}
                if bool(e.get("enabled")) != bool(prev.get("enabled")):
                    return True
                if not e.get("enabled"):
                    continue
                fast = set(getattr(t.spec, "font_stage_param_keys", ()) or ())
                new_p = e.get("params") or {}
                old_p = prev.get("params") or {}
                for k in set(new_p) | set(old_p):
                    if k not in fast and new_p.get(k) != old_p.get(k):
                        return True
            return False

        def _resolve_then_build():
            # Synchronous twin of the debounced regen job: same steps, none
            # of its timer bookkeeping (calling the job directly unbalances
            # BACKGROUND_WORK and with it /api/health's ``building``).
            _resolve_active_source()
            return trigger_build()

        rebuild = _resolve_then_build if _source_touched() else trigger_build
        # NB: do NOT null VARIABLE_FONT_PATH here. trigger_build() reassigns it
        # on any successful build and leaves it untouched on failure, so the
        # last-good font keeps serving if the rebuild fails.
        build_ok = False
        try:
            build_ok = rebuild()
        except Exception as build_exc:
            print(f"Warning: rebuild after transforms update failed: {build_exc}", file=sys.stderr)
        # A transform can fail even when the base font compiled fine — surface
        # it so the UI doesn't show an enabled transform that silently no-op'd.
        return jsonify({
            "success": True,
            "transforms": stored,
            "build_ok": bool(build_ok),
            "transform_error": LAST_TRANSFORM_ERROR,
        })
    except Exception as e:
        print(f"Error updating transforms: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------------------------
# GRADE transform — per-source ``<basename>-grade.json``
#
# The Transforms "Grade" toggle + per-instance grade%. A grade is
# source-level (injects GRAD brace layers onto the shadow, like control
# axes — NOT a post-build VF→VF transform), so its endpoints live here and
# schedule a shadow rebuild rather than going through the transforms
# registry. Per-instance grades persist regardless of the toggle.
# ----------------------------------------------------------------------


def _grade_instance_coords():
    """Parametric {XTRA,XOPQ,YOPQ} for EVERY instance — source-defined AND
    studio-only (CSV rows) alike — keyed by name, since a grade can target
    either kind. Source coords win; the CSV supplies studio-only rows (and any
    parametric tag a source instance happens to lack). CrispyMini, for example,
    has zero source instances — all live in the CSV — so reading source alone
    (the original bug) found no coords and every grade was skipped."""
    coords = {}
    try:
        font, _fmt = _source_font.load_source_cached(ORIGINAL_PATH)
        for inst in _source_font.get_source_instances(font):
            coords[inst["name"]] = dict(inst.get("coordinates", {}))
    except Exception:  # noqa: BLE001
        pass
    try:
        csv_path = _get_preview_csv_path()
        if csv_path and csv_path.exists():
            import csv as _csvmod
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in _csvmod.DictReader(f):
                    name = (row.get("Instance Name") or "").strip()
                    if not name:
                        continue
                    c = coords.setdefault(name, {})
                    for tag in _grade.PARAM_TAGS:
                        if tag in c:
                            continue
                        v = row.get(tag)
                        if v not in (None, ""):
                            try:
                                c[tag] = float(v)
                            except (TypeError, ValueError):
                                pass
    except Exception:  # noqa: BLE001
        pass
    return coords


def _grade_param_ranges():
    """(min, max) per parametric tag, or {} when the source can't be read."""
    ranges = {}
    try:
        axes = _source_font.get_axes(_source_font.load_source_cached(ORIGINAL_PATH)[0])
        by_tag = {a["tag"].upper(): a for a in axes}
        for t in _grade.PARAM_TAGS:
            if t in by_tag:
                ranges[t] = (by_tag[t]["min"], by_tag[t]["max"])
    except Exception:  # noqa: BLE001
        return {}
    return ranges


def _grade_diagnostics(data, coords, param_ranges):
    """Problems with the CURRENT grade declaration, worst first.

    Each entry is ``{level, code, instance, message, detail}`` where level is
    "error" (the axis won't build, or this instance contributes nothing),
    "warning" (it builds but the result is degraded) or "info".

    The checks exist because the failure modes here are invisible in the
    preview until you drag the slider to an extreme: an instance pinned on the
    XTRA floor has no counters left to open, so every stem unit the grade adds
    lands in the counter and the glyph bleeds shut.
    """
    out = []
    strength = float(data.get("intensity", 1.0))
    capped = bool(data.get("clamp_to_headroom", True))
    entries = list(data.get("instances") or [])
    live = [e for e in entries
            if _grade.effective_pct(e.get("pct", 0.0), strength) > 0]

    # --- axis-level: will GRAD exist at all? ------------------------------
    if not data.get("enabled"):
        out.append({
            "level": "info", "code": "grade_disabled", "instance": None,
            "message": "Grade is switched off, so the GRAD axis is not built.",
            "detail": "Per-instance grades are remembered and return when you switch it back on.",
        })
    elif not entries:
        out.append({
            "level": "error", "code": "no_graded_instances", "instance": None,
            "message": "No instance is graded, so the GRAD axis is not built.",
            "detail": "Grade at least one instance to bring the axis into the font.",
        })
    elif strength <= 0:
        out.append({
            "level": "error", "code": "intensity_zero", "instance": None,
            "message": "Grade intensity is 0, so every grade is cancelled and the GRAD axis is not built.",
            "detail": f"{len(entries)} instance(s) are graded. Raise intensity above 0 to bring them back.",
        })
    elif not live:
        out.append({
            "level": "error", "code": "all_grades_zero", "instance": None,
            "message": "Every graded instance sits at 0%, so the GRAD axis is not built.",
            "detail": "Give at least one instance a grade above 0.",
        })
    elif len(live) == 1:
        out.append({
            "level": "info", "code": "single_anchor", "instance": live[0].get("name"),
            "message": f"Only \u201c{live[0].get('name')}\u201d is graded, so GRAD fades to nothing away from it.",
            "detail": "Grade more instances to carry the axis across the space.",
        })

    if not param_ranges:
        return out

    # --- per-instance --------------------------------------------------
    for entry in entries:
        name = entry.get("name")
        authored = float(entry.get("pct", 0.0) or 0.0)
        pct = _grade.effective_pct(authored, strength)
        base = coords.get(name)
        if base is None:
            out.append({
                "level": "error", "code": "unknown_instance", "instance": name,
                "message": f"\u201c{name}\u201d is graded but no longer exists.",
                "detail": "It was renamed or deleted. Remove the grade or re-add the instance.",
            })
            continue
        if pct <= 0:
            continue

        cap = _grade.max_pct_for(base, param_ranges)
        lo_x = param_ranges.get("XTRA", (None, None))[0]
        x = base.get("XTRA")
        room = None if (x is None or lo_x is None) else max(0.0, x - lo_x)

        # What the model actually delivers here, both directions.
        light, dark = _grade.grade_coords(base, pct, param_ranges,
                                          clamp_to_headroom=capped)
        o = base.get("XOPQ", 0.0)
        want = pct * o / 2.0
        got_dark = dark.get("XOPQ", o) - o
        got_light = o - light.get("XOPQ", o)

        if room == 0:
            if capped:
                out.append({
                    "level": "error", "code": "no_headroom_capped", "instance": name,
                    "message": f"\u201c{name}\u201d cannot darken: its counters are already as tight as the design allows.",
                    "detail": ("XTRA is at its minimum, so there is no counter room to absorb a thicker "
                               "stem. The darkening half of the grade is held at 0 here; the lightening "
                               "half still works. Move the instance off the XTRA floor, or turn off "
                               "\u201climit grade to counter headroom\u201d to allow uncompensated darkening."),
                })
            else:
                out.append({
                    "level": "warning", "code": "no_headroom_uncompensated", "instance": name,
                    "message": f"\u201c{name}\u201d darkens straight into its counters.",
                    "detail": (f"XTRA is at its minimum, so none of the {2 * want:.0f} units of counter "
                               f"relief this grade needs are available: all {want:.0f} units of added stem "
                               "fill the counters. Lower this instance's grade%, or turn on "
                               "\u201climit grade to counter headroom\u201d."),
                })
        elif authored > cap + 1e-9:
            out.append({
                "level": "warning", "code": "pct_over_cap", "instance": name,
                "message": f"\u201c{name}\u201d is graded beyond what its parametric space can deliver.",
                "detail": (f"Grade is {authored:.0%} but only {cap:.0%} fits before an axis hits its limit; "
                           "the excess is discarded, so raising it further changes nothing."),
            })
        elif want > 0 and (got_dark < want - 0.5 or got_light < want - 0.5):
            worst = min(got_dark, got_light)
            out.append({
                "level": "warning", "code": "partially_clamped", "instance": name,
                "message": f"\u201c{name}\u201d only reaches part of its grade.",
                "detail": (f"{worst:.0f} of {want:.0f} stem units are delivered before an axis hits its "
                           "limit, so the two directions are no longer symmetric."),
            })

    order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda d: order.get(d["level"], 3))
    return out


def _grade_state_payload():
    """Grade sidecar + per-instance slider caps and diagnostics for the UI."""
    data = _grade.load(ORIGINAL_PATH)
    param_ranges = _grade_param_ranges()
    try:
        coords = _grade_instance_coords()
        caps = {
            name: _grade.max_pct_for(coords[name], param_ranges)
            for name in coords if param_ranges
        }
    except Exception:  # noqa: BLE001
        coords, caps = {}, {}
    data["max_pct"] = caps
    try:
        data["diagnostics"] = _grade_diagnostics(data, coords, param_ranges)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: grade diagnostics failed: {exc}", file=sys.stderr)
        data["diagnostics"] = []
    data["sidecar_path"] = str(_grade.sidecar_path_for(ORIGINAL_PATH))
    return data


@app.route('/api/transforms/grade', methods=['GET'])
def get_grade():
    """Return the grade toggle, default, per-instance grades, and slider caps."""
    if ORIGINAL_PATH is None:
        return jsonify(_grade._empty())
    try:
        return jsonify(_grade_state_payload())
    except Exception as e:  # noqa: BLE001
        print(f"Error reading grade: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


@app.route('/api/transforms/grade', methods=['PUT'])
def set_grade():
    """Set the Grade toggle and/or the global knobs. Body::

        { "enabled": true, "default_pct": 0.25,
          "intensity": 1.0, "clamp_to_headroom": true }

    All fields optional. ``intensity`` scales every authored grade% at once;
    ``clamp_to_headroom`` stops a grade thickening where the counters have no
    room to open. Rebuilds when the built font would change."""
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        rebuild = False
        if "enabled" in data:
            _grade.set_enabled(ORIGINAL_PATH, bool(data["enabled"]))
            rebuild = True  # toggling adds/removes the GRAD axis
        if "default_pct" in data:
            # The default only seeds NEW grades; changing it never alters the
            # built font, so persist without a rebuild.
            _grade.set_default_pct(ORIGINAL_PATH, data["default_pct"])
        if "intensity" in data:
            # Scales every authored grade%, so the built font changes.
            _grade.set_intensity(ORIGINAL_PATH, data["intensity"])
            rebuild = True
        if "clamp_to_headroom" in data:
            _grade.set_clamp_to_headroom(ORIGINAL_PATH, bool(data["clamp_to_headroom"]))
            rebuild = True
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if rebuild:
        schedule_shadow_rebuild()
    return jsonify(_grade_state_payload())


@app.route('/api/instances/<instance_name>/grade', methods=['PUT'])
def set_instance_grade(instance_name: str):
    """Add or update a grade on one instance. Body ``{"pct": 0.25}`` — omit
    ``pct`` to use the global default. Rebuilds."""
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        entry = _grade.set_instance_grade(ORIGINAL_PATH, instance_name, data.get("pct"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    schedule_shadow_rebuild()
    return jsonify(entry)


@app.route('/api/instances/<instance_name>/grade', methods=['DELETE'])
def delete_instance_grade(instance_name: str):
    """Remove a grade from one instance. Rebuilds."""
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    removed = _grade.remove_instance_grade(ORIGINAL_PATH, instance_name)
    if removed:
        schedule_shadow_rebuild()
    return jsonify({"removed": removed})


@app.route('/api/kinetikern/propose', methods=['GET'])
def kinetikern_propose():
    """Kinetikern2 Pass 1 (sidebearings) for one instance (``instance=<name>``)
    or any location of the built font (``coordinates=<JSON {tag: value}>``),
    at ``looseness`` (-1 tight … +1 loose; 0 is the engine's calibrated
    middle). Returns current and proposed sidebearings per encoded glyph.
    Nothing is written."""
    if not (VARIABLE_FONT_PATH and VARIABLE_FONT_PATH.exists()):
        return jsonify({"error": "Variable font not built yet."}), 404
    if _kinetikern.engine_path() is None:
        return jsonify({"error": "Kinetikern2 engine not built (native/kinetikern2/README.md)."}), 503
    name = request.args.get("instance")
    if name:
        coords = _grade_instance_coords().get(name)
        if not coords:
            return jsonify({"error": f"Unknown instance: {name}"}), 404
        location = {t: coords[t] for t in _grade.PARAM_TAGS if t in coords}
    else:
        try:
            location = json.loads(request.args.get("coordinates", ""))
        except json.JSONDecodeError:
            return jsonify({"error": "instance or coordinates (JSON) required"}), 400
    try:
        looseness = float(request.args.get("looseness", 0.0))
    except ValueError:
        return jsonify({"error": "looseness must be a number"}), 400
    try:
        out = _kinetikern.propose(VARIABLE_FONT_PATH, location, looseness)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    out["instance"] = name
    # what the sidecar already holds for this instance, by glyph: the build
    # that carries it shows ``cur`` = baseline + applied
    stored = _kinetikern.sidecar.find(ORIGINAL_PATH, name) if name else None
    applied = (stored or {}).get("deltas", {})
    source_names = _kinetikern_source_names() if applied else {}
    for g in out["glyphs"]:
        g["applied"] = applied.get(source_names.get(g["unicode"], g["name"]), [0, 0])
    out["looseness_applied"] = stored.get("looseness") if stored else None
    return jsonify(out)


@app.route('/api/kinetikern', methods=['GET'])
def kinetikern_state():
    """The Kinetikern sidecar: every corrected instance with its looseness
    and per-glyph deltas (``sidecar.load``)."""
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    return jsonify(_kinetikern.sidecar.load(ORIGINAL_PATH))


def _kinetikern_apply_one(name: str, coords: Dict[str, float], looseness: float,
                          location: Optional[Dict[str, float]] = None) -> Dict:
    """Space ``name`` at ``looseness`` against the current build and store the
    result as deltas. The engine's targets are absolute, so the stored delta
    is the existing one plus the move the current build still needs; once
    that build carries the entry a re-apply changes nothing."""
    location = {t: coords[t] for t in _grade.PARAM_TAGS if t in coords}
    out = _kinetikern.propose(VARIABLE_FONT_PATH, location, looseness)
    existing = (_kinetikern.sidecar.find(ORIGINAL_PATH, name) or {}).get("deltas", {})
    source_names = _kinetikern_source_names()
    deltas = {}
    for g in out["glyphs"]:
        if not g["spaced"]:
            continue
        glyph = source_names.get(g["unicode"], g["name"])
        old = existing.get(glyph, [0, 0])
        dl = g["new"]["lsb"] - g["cur"]["lsb"] + old[0]
        dr = g["new"]["rsb"] - g["cur"]["rsb"] + old[1]
        if dl or dr:
            deltas[glyph] = [dl, dr]
    return _kinetikern.sidecar.set_entry(ORIGINAL_PATH, name, looseness, deltas, location=location)


def _kinetikern_source_names() -> Dict[int, str]:
    """{code point: source glyph name}. The bake works on the source, whose
    names fontc replaces with production names in the build (euro -> Euro)."""
    try:
        font, fmt = _source_font.load_source_cached(ORIGINAL_PATH)
    except Exception:  # noqa: BLE001
        return {}
    if "glyphs" not in str(fmt).lower():
        return {}
    names = {}
    for glyph in font.glyphs:
        for u in (getattr(glyph, "unicodes", None) or ([glyph.unicode] if glyph.unicode else [])):
            try:
                names.setdefault(int(str(u), 16), glyph.name)
            except ValueError:
                pass
    return names


def _kinetikern_ready():
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    if not (VARIABLE_FONT_PATH and VARIABLE_FONT_PATH.exists()):
        return jsonify({"error": "Variable font not built yet."}), 404
    if _kinetikern.engine_path() is None:
        return jsonify({"error": "Kinetikern2 engine not built (native/kinetikern2/README.md)."}), 503
    return None


@app.route('/api/kinetikern/instances/<instance_name>', methods=['PUT'])
def kinetikern_apply_instance(instance_name: str):
    """Space one instance with Pass 1 at body ``{"looseness": t}`` and store
    the deltas. Rebuilds."""
    blocked = _kinetikern_ready()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    location = data.get("location")
    if location:
        try:
            coords = {str(t).upper(): float(location[t]) for t in location}
        except (TypeError, ValueError):
            return jsonify({"error": "location values must be numbers"}), 400
        if any(t not in coords for t in _grade.PARAM_TAGS):
            return jsonify({"error": f"location needs {', '.join(_grade.PARAM_TAGS)}"}), 400
    else:
        coords = _grade_instance_coords().get(instance_name)
        if not coords:
            return jsonify({"error": f"Unknown instance: {instance_name}"}), 404
    try:
        looseness = float(data.get("looseness", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "looseness must be a number"}), 400
    entry = _kinetikern_apply_one(instance_name, coords, looseness, location=coords if location else None)
    schedule_shadow_rebuild()
    return jsonify(entry)


@app.route('/api/kinetikern/instances/<instance_name>', methods=['DELETE'])
def kinetikern_revert_instance(instance_name: str):
    """Drop one instance's correction. Rebuilds."""
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    removed = _kinetikern.sidecar.remove_entry(ORIGINAL_PATH, instance_name)
    if removed:
        schedule_shadow_rebuild()
    return jsonify({"removed": removed})


@app.route('/api/kinetikern/all', methods=['PUT'])
def kinetikern_apply_all():
    """Space every instance that has parametric coordinates at one
    ``looseness``, so the whole family is pinned consistently. Rebuilds once."""
    blocked = _kinetikern_ready()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    try:
        looseness = float(data.get("looseness", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "looseness must be a number"}), 400
    applied = []
    for name, coords in _grade_instance_coords().items():
        if all(t in coords for t in _grade.PARAM_TAGS):
            _kinetikern_apply_one(name, coords, looseness)
            applied.append(name)
    if applied:
        schedule_shadow_rebuild()
    return jsonify({"applied": applied, "looseness": looseness})


# ----------------------------------------------------------------------
# CONTROL AXES sidecar — v2 slice 1 (declaration only)
#
# These endpoints manage the per-source ``<basename>-control.json``
# file: the canonical store for studio-declared control axes. The
# shadow source file + brace-layer authoring + Fontra integration
# arrive in later v2 slices; here we just persist the declarations
# so the UI can show + manage them.
# ----------------------------------------------------------------------


_SOURCE_GLYPH_SIGS: "tuple" = (None, {})   # (source mtime_ns, {glyph: sig})


def _source_glyph_sigs():
    """{glyph: master-geometry signature} for the ORIGINAL source, recomputed
    only when the file's mtime changes. /api/control-axes is polled, and a
    glyphsLib parse per poll would be the wrong price for a staleness flag."""
    global _SOURCE_GLYPH_SIGS
    if ORIGINAL_PATH is None or not ORIGINAL_PATH.exists():
        return None
    try:
        mtime = ORIGINAL_PATH.stat().st_mtime_ns
    except OSError:
        return None
    if _SOURCE_GLYPH_SIGS[0] == mtime:
        return _SOURCE_GLYPH_SIGS[1]
    try:
        font, _fmt = _source_font.load_source(ORIGINAL_PATH)
        sigs = {g.name: _control_axes.glyph_masters_sig(font, g.name) for g in font.glyphs}
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not fingerprint source glyphs: {exc}", file=sys.stderr)
        return None
    _SOURCE_GLYPH_SIGS = (mtime, sigs)
    return sigs


@app.route('/api/control-axes', methods=['GET'])
def list_control_axes():
    """Return the sidecar's control-axis declarations."""
    if ORIGINAL_PATH is None:
        return jsonify({"axes": []})
    try:
        # Strip the captured drawings. They are large path dumps the UI never
        # reads, and this endpoint is polled — but WHETHER a layer holds one
        # matters, because that is the hand work a re-seed would discard.
        sigs = _source_glyph_sigs()
        slim = []
        for ax in _control_axes.list_axes(ORIGINAL_PATH):
            entry = dict(ax)
            layers = []
            for layer in (ax.get("layers") or []):
                layer = layer or {}
                has_outline = bool(layer.get("outline"))
                stored = layer.get("source_sig")
                # A drawing is stale when the masters it was made over have
                # changed since capture. Only answerable for drawings that
                # recorded their source_sig; older captures report unknown.
                if has_outline and stored and sigs is not None:
                    source_changed = sigs.get(layer.get("glyph")) != stored
                else:
                    source_changed = None
                layers.append(dict(
                    {k: v for k, v in layer.items() if k not in ("outline", "source_sig")},
                    has_outline=has_outline,
                    source_changed=source_changed,
                ))
            entry["layers"] = layers
            slim.append(entry)
        return jsonify({
            "axes": slim,
            "sidecar_path": str(_control_axes.sidecar_path_for(ORIGINAL_PATH)),
        })
    except Exception as e:
        print(f"Error listing control axes: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


@app.route('/api/control-axes', methods=['POST'])
def create_control_axis():
    """Declare a new control axis. JSON body::

        { "tag": "crbr", "display_name": "Crossbar",
          "default": 0, "min": -100, "max": 100 }

    Persists to the sibling ``<basename>-control.json``, regenerates
    the shadow .glyphs file (with the new axis added to the source's
    axis list + each master extended at the axis default), and
    triggers a font rebuild. The slider appears in the preview
    immediately; until coverage glyphs + brace layers exist (later
    slices) moving it has no visual effect because every glyph sits
    at the axis default on every master.
    """
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    # Both source formats author through a shadow now: .glyphs gets
    # brace layers inside a shadow copy, .designspace gets pooled
    # sparse UFO sources next to a shadow document.
    data = request.get_json(silent=True) or {}
    try:
        entry = _control_axes.add_axis(
            ORIGINAL_PATH,
            tag=data.get("tag", ""),
            display_name=data.get("display_name", ""),
            default=data.get("default", 0),
            min_value=data.get("min", 0),
            max_value=data.get("max", 0),
        )

        # Generate / refresh the shadow for slice 3+ to consume.
        # Build stays pointed at the original — see _apply_source_path.
        shadow = None
        try:
            shadow = _control_axes.regenerate_shadow(ORIGINAL_PATH)
        except Exception as shadow_exc:
            print(f"Warning: shadow regeneration after add failed: {shadow_exc}", file=sys.stderr)

        return jsonify({"success": True, "axis": entry, "shadow_path": str(shadow) if shadow else None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        print(f"Error creating control axis: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------------------------
# Fontra subprocess lifecycle (v2 slice 5a)
# ----------------------------------------------------------------------


def _ensure_fontra_running(content_root: Path, watch_file: Optional[Path] = None) -> int:
    """Ensure a Fontra subprocess is running and serving the given
    folder as its filesystem project root. If a process is already
    running but pointed at a different folder, restart it.

    Returns the port Fontra is listening on. Raises on failure.

    Setup note: this requires two pieces in the same venv as
    avar2-studio (neither is on PyPI under the obvious name):

      pip install -e git+https://github.com/fontra/fontra.git
      pip install -e git+https://github.com/fontra/fontra-glyphs.git

    The ``fontra-glyphs`` package registers the ``.glyphs``
    filesystem backend — without it Fontra throws
    ``FileNotFoundError(None)`` when opening a .glyphs project.
    The ``.designspace`` / ``.ufo`` backends are bundled with
    Fontra itself.
    """
    import subprocess
    import time

    global FONTRA_PROCESS, FONTRA_CONTENT_ROOT, FONTRA_SHADOW_MTIME

    content_root = content_root.resolve()

    # mtime of the shadow file Fontra serves, so we can tell whether a
    # layer edit rewrote it since Fontra was spawned.
    current_mtime = None
    if watch_file is not None:
        try:
            current_mtime = watch_file.stat().st_mtime
        except OSError:
            current_mtime = None

    # Already running at the right root AND the shadow hasn't changed —
    # reuse. If the shadow was rewritten (current_mtime differs), fall
    # through to restart so Fontra's backend reloads fresh glyph data
    # instead of serving its cache.
    if (
        FONTRA_PROCESS is not None
        and FONTRA_PROCESS.poll() is None
        and FONTRA_CONTENT_ROOT == content_root
        and (current_mtime is None or current_mtime == FONTRA_SHADOW_MTIME)
    ):
        return FONTRA_PORT

    # Different root, dead process, or the shadow was rewritten — kill
    # + restart.
    _stop_fontra()

    # Launch Fontra through our own module so the fontra-glyphs
    # monkeypatch (control-axis brace-layer editability, see
    # _fontra_patch) is applied before Fontra reads any font. This
    # replaces invoking the bare ``fontra`` console script; the CLI
    # args are identical.
    cmd = [
        sys.executable,
        "-m", "avar2_studio._fontra_launch",
        "--http-port", str(FONTRA_PORT),
        "filesystem", str(content_root),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # New session so killing the studio doesn't take Fontra with
        # it via signal propagation; we manage shutdown explicitly.
        start_new_session=True,
    )

    # Give Fontra a moment to bind the port.
    for _ in range(20):
        if proc.poll() is not None:
            raise RuntimeError(
                f"fontra exited immediately (exit code {proc.returncode})"
            )
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", FONTRA_PORT)) == 0:
                    break
        except OSError:
            pass
        time.sleep(0.25)
    else:
        _stop_fontra()
        raise RuntimeError(f"fontra didn't bind 127.0.0.1:{FONTRA_PORT} within ~5s")

    FONTRA_PROCESS = proc
    FONTRA_CONTENT_ROOT = content_root
    FONTRA_SHADOW_MTIME = current_mtime
    print(f"Started Fontra on http://127.0.0.1:{FONTRA_PORT} (root={content_root})", file=sys.stderr)
    return FONTRA_PORT


def _stop_fontra() -> None:
    """Kill any running Fontra subprocess. Safe to call repeatedly."""
    global FONTRA_PROCESS, FONTRA_CONTENT_ROOT
    if FONTRA_PROCESS is None:
        return
    try:
        if FONTRA_PROCESS.poll() is None:
            FONTRA_PROCESS.terminate()
            try:
                FONTRA_PROCESS.wait(timeout=3)
            except Exception:
                FONTRA_PROCESS.kill()
    except Exception as exc:
        print(f"Warning: error stopping Fontra: {exc}", file=sys.stderr)
    finally:
        FONTRA_PROCESS = None
        FONTRA_CONTENT_ROOT = None


# Make sure Fontra is cleaned up when the studio exits.
import atexit as _atexit
_atexit.register(_stop_fontra)


# ----------------------------------------------------------------------
# Reverse proxy: avar2-studio at :5070 fronts Fontra at :8001
# ----------------------------------------------------------------------
#
# Why proxy: cross-origin iframes can't share state with the parent
# (no CSS injection, no JS bridging). To present a FOCUSED Fontra UI
# — hiding the panels irrelevant to a control-axis brace edit — we
# serve Fontra under our own origin and inject a tiny stylesheet on
# the way through. Same-origin also unblocks future bidirectional
# postMessage if we want to bridge state.
#
# Two legs:
#   - HTTP:  /fontra/<path>  → http://127.0.0.1:8001/<path>
#            HTML responses get a stylesheet appended that hides
#            the panels we don't want, and absolute-path references
#            (``href="/css/..."``) get rewritten to live under
#            /fontra/.
#   - WS:    /websocket?...  → ws://127.0.0.1:8001/websocket?...
#            (Fontra's JS builds the ws URL from window.location.host
#            so it lands here, not on Fontra's port directly.)


# Focused-UI CSS for the embedded Fontra editor. Hides the
# sidebar panels + edit tools that aren't useful for a
# brace-layer edit. Selectors map to Fontra's own DOM:
#   - Sidebar tabs use ``.sidebar-tab[data-sidebar-name="..."]``
#     keyed by the panel class's ``identifier`` property
#     (views-editor/src/panel-*.js).
#   - Edit tools use ``[data-tool="..."]`` from
#     edit-tools-*.js identifier properties.
#   - Selectors are paired with their .sidebar-content / wrapper
#     equivalents so the panel disappears whether it was the
#     currently-open panel or not.
#
# If Fontra renames panels/tools we'll have to update; these are
# documented in their identifier properties so changes are easy
# to track.
#
# The CSS is built per session: a STUDIO-axis session keeps the
# designspace-navigation panel visible — its glyph-sources list is
# the multi-source batch-editing surface, trimmed to studio layers
# by the shim script below — while every other session hides it
# along with the rest.


def _fontra_focus_css() -> str:
    show_nav = bool((FONTRA_EDITOR_SESSION or {}).get("studio"))
    hidden = [
        "text-entry", "selection-info", "reference-font", "glyph-search",
        "selection-transformation", "glyph-note", "related-glyphs",
        "characters-glyphs",
    ]
    if not show_nav:
        hidden.insert(1, "designspace-navigation")
    panel_rules = ",\n  ".join(
        f'.sidebar-tab[data-sidebar-name="{n}"],\n'
        f'  .sidebar-content[data-sidebar-name="{n}"]'
        for n in hidden
    )
    # In studio sessions the sidebar containers must stay visible so
    # the designspace-navigation panel has somewhere to live; the
    # NOTE about shadow DOM still applies (accordion internals are
    # trimmed by the shim, not this stylesheet).
    if show_nav:
        chrome_rules = ".top-bar-container,\n  menu-bar"
    else:
        chrome_rules = (
            ".top-bar-container,\n  .sidebar-container.left,\n"
            "  .sidebar-container.right,\n  menu-bar"
        )
    return f"""
<style id="avar2-studio-fontra-focus">
  /* Hide the sidebar panels that aren't useful for a brace-layer
     edit. avar2-studio seeds and manages the control-axis layers
     and navigates Fontra to the exact layer via the ↗ button.

     NOTE: the accordion SECTIONS inside designspace-navigation (the
     #font-axes / #glyph-sources items) live in a shadow DOM that
     injected global CSS can't reach — so hiding happens at the
     light-DOM container level (here) or via the shim script. */
  {panel_rules} {{
    display: none !important;
  }}

  /* Top-level menu bar (File / Edit / View / Font / Glyph / Help).
     Not needed — the studio drives the editor. The edit tools live
     in a floating #edit-tools overlay, not a sidebar, so they
     survive. */
  {chrome_rules} {{
    display: none !important;
  }}

  /* Edit tools — hide drawing tools, knife, shapes. Keep:
     pointer-tools (selection + drag), power-ruler-tool
     (measure), metrics-tool (sidebearings — kerning sub-tool
     stays visible inside the group), hand-tool (pan), and the
     entire zoom-tools group. Structural edits would also desync
     multi-source editing, so the pen stays hidden in studio
     sessions on purpose. */
  #edit-tools > .tool-button[data-tool="pen-tool"],
  #edit-tools > .tool-button[data-tool="pen-tool-cubic"],
  #edit-tools > .tool-button[data-tool="pen-tool-quad"],
  #edit-tools > .tool-button[data-tool="knife-tool"],
  #edit-tools > .tool-button[data-tool="shape-tool"],
  #edit-tools > .tool-button[data-tool="shape-tool-rectangle"],
  #edit-tools > .tool-button[data-tool="shape-tool-ellipse"],
  .tool-button.multi-tool[data-tool="pen-tool"],
  .tool-button.multi-tool[data-tool="shape-tool"] {{
    display: none !important;
  }}
</style>
"""


# Studio-session shim: runs inside the proxied Fontra page (same
# origin, so it can reach our /api and Fontra's DOM alike). Active
# only when /api/fontra-shim-config says the session is a
# studio-axis edit. It:
#   1. opens the designspace-navigation sidebar tab,
#   2. trims the panel to the glyph-sources accordion (no font axes,
#      no layers list, no add/remove source buttons),
#   3. sorts the sources list by source name (Fontra's own
#      "by-source-name" option), so the master/instance-labelled studio
#      layers read A→Z by style,
#   4. hides every sources-list row that is not a STUDIO layer of the
#      session's axis (masters, font sources, other axes' layers),
#   5. keeps multi-source editing OFF — the designer toggles rows on
#      deliberately; an opened layer edits alone by default. Masters and
#      source layers are never editing targets (Fontra's own init can put
#      the selected source there, so that strip keeps re-applying).
# A row is a studio layer when its dense location sits OFF the
# session axis's default — only sidecar seeding creates such layers
# in the shadow. Fallback: match the seed-time source-name label
# ("<name>" or, for unnamed locations, "<tag> <value>" coordinates).
# If no row matches, the list is left untrimmed (fail open) and
# nothing is auto-enabled (fail closed).
_FONTRA_STUDIO_SHIM_JS = """
<script id="avar2-studio-fontra-shim">
(function () {
  'use strict';
  fetch('/api/fontra-shim-config').then(r => r.json()).then((cfg) => {
    if (!cfg || !cfg.studio) return;
    const keys = [cfg.axis_name, cfg.tag].filter(Boolean);
    // Case-insensitive and regex-escaped: labels print the AUTHORED tag
    // case, and a tag drift on either side must never silently unmatch.
    const tagEsc = String(cfg.tag).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const labelRe = new RegExp('(^|[\\\\s,])' + tagEsc + '\\\\s+-?[\\\\d.]+', 'i');
    // The selected glyph's raw sources — their SPARSE locations keep
    // the authored pins verbatim, unlike the panel items' dense (all
    // axes, defaults filled) or display (defaults stripped) locations.
    const glyphSources = () => {
      const ec = window.editorController;
      const pg = ec && ec.sceneController && ec.sceneController.sceneModel
        && ec.sceneController.sceneModel.getSelectedPositionedGlyph
        && ec.sceneController.sceneModel.getSelectedPositionedGlyph();
      return (pg && pg.varGlyph && pg.varGlyph.glyph && pg.varGlyph.glyph.sources) || null;
    };
    const isStudioItem = (item, sources) => {
      if (!item || item.isFontSource || item.isDefaultSource) return false;
      // A studio brace PINS this control axis explicitly in its own
      // source location — even when the pinned value IS the axis
      // default (the designer may deliberately rest the axis on the
      // corrected end, ymod default 100). Masters never carry the key.
      // The old value!==default test on the dense location went blind
      // the day the default moved onto the braces' own value: masters
      // and braces all read "at default" and every row got hidden.
      const src = (sources && typeof item.sourceIndex === 'number')
        ? sources[item.sourceIndex] : null;
      const sloc = (src && src.location) || null;
      if (sloc) {
        for (const k of keys) { if (k in sloc) return true; }
      }
      const dl = item.denseLocation || {};
      for (const k of keys) {
        if (k in dl) return Number(dl[k]) !== Number(cfg.axis_default);
      }
      return labelRe.test(String(item.name || ''));
    };
    let tabOpened = false, panelTrimmed = false, sortSet = false;
    const sweep = () => {
      const host = document.querySelector(
        '.sidebar-content[data-sidebar-name="designspace-navigation"]');
      const panel = host && host.children[0];
      if (!panel || !panel.sourcesList) return;
      if (!sortSet) {
        // Alphabetical by source name — studio layers are labelled by
        // master/instance name, so the list reads A→Z by style. Retry
        // until the editor's settings object exists: the panel's DOM can
        // be queryable before start.js finishes wiring the controller.
        const settings = window.editorController
          && window.editorController.sceneController
          && window.editorController.sceneController.applicationSettings;
        if (settings) {
          if (settings.glyphSourcesSortOptions !== 'by-source-name') {
            settings.glyphSourcesSortOptions = 'by-source-name';
          }
          if (typeof panel._updateSources === 'function') panel._updateSources();
          sortSet = true;
        }
      }
      if (!tabOpened) {
        const tab = document.querySelector(
          '.sidebar-tab[data-sidebar-name="designspace-navigation"]');
        if (tab && !tab.classList.contains('selected')) tab.click();
        tabOpened = true;
      }
      if (!panelTrimmed && panel.shadowRoot) {
        // The accordion items live TWO shadow roots deep —
        // panel-designspace-navigation#shadow > ui-accordion#shadow — so a
        // style appended to the panel's own root never reaches them (CSS does
        // not cross a shadow boundary). Verified against a live editor: the
        // ids resolve only inside the inner ui-accordion.
        const rules =
          '#font-axes-accordion-item,#glyph-axes-accordion-item,' +
          '#glyph-layers-accordion-item,#sources-list-add-remove-buttons' +
          '{display:none !important;}';
        const roots = [panel.shadowRoot];
        const inner = panel.shadowRoot.querySelector('ui-accordion');
        if (inner && inner.shadowRoot) roots.push(inner.shadowRoot);
        for (const root of roots) {
          const st = document.createElement('style');
          st.textContent = rules;
          root.appendChild(st);
        }
        // Only claim success once the inner root — the one that matters — was
        // actually reachable; otherwise retry on the next sweep, since the
        // accordion may not have upgraded yet.
        panelTrimmed = roots.length > 1;
      }
      const list = panel.sourcesList;
      const items = list.items || [];
      const sources = glyphSources();
      const anyStudio = items.some((it) => isStudioItem(it, sources));
      const rows = list.shadowRoot
        ? list.shadowRoot.querySelectorAll('.contents > .row') : [];
      rows.forEach((row) => {
        const item = items[Number(row.dataset.rowIndex)];
        if (!item) return;
        const studio = isStudioItem(item, sources);
        row.style.display = (anyStudio && !studio) ? 'none' : '';
        if (!anyStudio) return;
        if (!studio && item.editing) {
          // Hard rule: masters and source layers are never batch-edit
          // targets — Fontra's own init can put the selected source
          // here, so keep stripping it, not just once.
          item.editing = false;
        }
      });
    };
    setInterval(sweep, 500);

    // REFERENCE MEASUREMENTS.
    //
    // Correcting a glyph's horizontals means matching it to the glyphs that
    // already read right — E's bar to H's, at the same designspace point.
    // Fontra's ruler can measure what you have drawn, but it cannot tell you
    // what to aim at, because the target lives in a different glyph. This HUD
    // supplies those numbers: every figure is measured on the built font at
    // THIS layer's exact coordinates.
    //
    // The numbers live in a plain DOM panel (no Fontra internals to break);
    // the comparison overlay rides Fontra's visualization-layer registry so it
    // draws in the drawing editor itself, and degrades to nothing if that API
    // ever moves. The edited glyph's own row is its PRE-EDIT state — the server
    // measures the last build, not what is on the canvas right now.
    if (!cfg.glyph) return;

    // Scanline stroke measurement, matching the server's rule so the live and
    // reference numbers are comparable: a horizontal cut low in the letter
    // reads its vertical stems, a vertical cut mid-advance reads its
    // horizontal bars, and the THINNEST run of each is the stroke weight.
    const measure = (contours, advance) => {
      const runs = (coord, vertical) => {
        const vals = [];
        for (const c of contours) {
          for (let i = 0; i < c.length; i++) {
            const P = c[i], Q = c[(i + 1) % c.length];
            const a1 = vertical ? P[0] : P[1], b1 = vertical ? P[1] : P[0];
            const a2 = vertical ? Q[0] : Q[1], b2 = vertical ? Q[1] : Q[0];
            if ((a1 <= coord && coord < a2) || (a2 <= coord && coord < a1)) {
              vals.push(b1 + (coord - a1) * (b2 - b1) / (a2 - a1));
            }
          }
        }
        vals.sort((x, y) => x - y);
        const out = [];
        for (let i = 0; i + 1 < vals.length; i += 2) {
          const w = vals[i + 1] - vals[i];
          if (w > 5) out.push([w, vals[i], vals[i + 1]]);
        }
        return out;
      };
      const ys = [];
      for (const c of contours) for (const pt of c) ys.push(pt[1]);
      if (!ys.length) return {};
      const yMin = Math.min(...ys), yMax = Math.max(...ys);
      // Scan coordinates + the winning (thinnest) run extents ride along so
      // the overlay can draw EXACTLY what produced each number.
      const scanY = yMin + (yMax - yMin) * 0.25;
      const scanX = advance * 0.5;
      const stems = runs(scanY, false);
      const bars = runs(scanX, true);
      const r = { scanY: scanY, scanX: scanX };
      if (stems.length) {
        const m = stems.reduce((a, b) => (b[0] < a[0] ? b : a));
        r.stem = m[0]; r.stemRun = [m[1], m[2]];
      }
      if (bars.length) {
        const m = bars.reduce((a, b) => (b[0] < a[0] ? b : a));
        r.bar = m[0]; r.barRun = [m[1], m[2]];
      }
      if (r.stem && r.bar) r.contrast = r.stem / r.bar;
      return r;
    };

    // Fontra's live path for the glyph being edited: a flat coordinate array
    // plus contourInfo endpoints. Reading it is what makes the figure track
    // the points as they move, rather than reporting the last build.
    const livePath = () => {
      const ec = window.editorController;
      const pg = ec && ec.sceneController && ec.sceneController.sceneModel
        && ec.sceneController.sceneModel.getSelectedPositionedGlyph
        && ec.sceneController.sceneModel.getSelectedPositionedGlyph();
      if (!pg || !pg.glyph || !pg.glyph.path) return null;
      const path = pg.glyph.path;
      const co = path.coordinates, info = path.contourInfo;
      if (!co || !info) return null;
      const contours = [];
      let startPt = 0;
      for (const ci of info) {
        const pts = [];
        for (let i = startPt; i <= ci.endPoint; i++) pts.push([co[i * 2], co[i * 2 + 1]]);
        if (pts.length > 2) contours.push(pts);
        startPt = ci.endPoint + 1;
      }
      return { name: pg.glyph.name, contours, advance: pg.glyph.xAdvance };
    };

    // The location being edited RIGHT NOW: Fontra's scene follows the
    // selected source/layer (axis-NAME-keyed, source space). This is what
    // the reference must be measured at — a glyph can carry several layers
    // on the axis, and the sidecar's first one is not necessarily the one
    // on the canvas.
    const editLocation = () => {
      const ec = window.editorController;
      const ss = ec && ec.sceneController
        && ((ec.sceneController.sceneModel && ec.sceneController.sceneModel.sceneSettings)
            || ec.sceneController.sceneSettings);
      const loc = ss && ss.fontLocationSourceMapped;
      if (!loc || typeof loc !== 'object') return null;
      const out = {};
      for (const k of Object.keys(loc)) {
        const v = Number(loc[k]);
        if (isFinite(v)) out[k] = v;
      }
      return Object.keys(out).length ? out : null;
    };

    const num = (v) => (v === undefined || v === null) ? '\u2013' : (Math.round(v * 10) / 10).toFixed(1);
    let refData = null, refGlyph = '', lastLive = null;

    const box = document.createElement('div');
    box.id = 'avar2-studio-metrics';
    box.innerHTML =
      '<div class="hd"></div>'
      + '<table><tr><th></th><th>bar</th><th>stem</th><th>s/b</th></tr>'
      + '<tr class="live"><td class="g"></td><td class="b"></td><td class="s"></td><td class="c"></td></tr>'
      + '<tr class="ref"><td class="g"><input id="avar2-ref-glyph" value="" placeholder="H" maxlength="12" spellcheck="false"></td>'
      + '<td class="b"></td><td class="s"></td><td class="c"></td></tr></table>'
      + '<div class="ft">live vs reference, measured at this location. '
      + 'Type a glyph name to compare \u2014 its outline is drawn in the editor '
      + 'under the glyph (scan cuts marked). Clear the field to hide it.</div>';
    const st = document.createElement('style');
    st.textContent =
      '#avar2-studio-metrics{position:fixed;right:12px;bottom:12px;z-index:99999;'
      + 'background:rgba(28,28,30,.94);color:#eee;font:11px/1.45 ui-monospace,monospace;'
      + 'padding:8px 10px;border-radius:8px;box-shadow:0 4px 18px rgba(0,0,0,.4);min-width:200px}'
      + '#avar2-studio-metrics .hd{color:#9a9aa0;margin-bottom:5px;font-size:10px}'
      + '#avar2-studio-metrics table{border-collapse:collapse;width:100%}'
      + '#avar2-studio-metrics th{color:#8a8a90;font-weight:400;text-align:right;'
      + 'padding:0 0 2px 10px;font-size:10px}'
      + '#avar2-studio-metrics td{text-align:right;padding:1px 0 1px 10px}'
      + '#avar2-studio-metrics td.g{text-align:left;padding-left:0;color:#9a9aa0}'
      + '#avar2-studio-metrics tr.live td{color:#ffd479}'
      + '#avar2-studio-metrics input{width:5.5em;background:#3a3a3e;border:1px solid #55555a;'
      + 'color:#eee;font:inherit;border-radius:3px;padding:0 3px}'
      + '#avar2-studio-metrics .ft{color:#7a7a80;margin-top:6px;font-size:9.5px;'
      + 'max-width:215px;white-space:normal;line-height:1.35}';
    document.head.appendChild(st);
    document.body.appendChild(box);

    const cell = (sel, v) => { const e = box.querySelector(sel); if (e) e.textContent = v; };
    // CANVAS OVERLAY. The reference outline is drawn in the drawing editor
    // itself, under the glyph being edited: editorController
    // .visualizationLayers.definitions IS Fontra's live registry array, so a
    // definition spliced in at runtime (zIndex-ordered, exactly what
    // registerVisualizationLayerDefinition does) paints on every frame and
    // pans/zooms with the canvas. Scan cuts are marked on both shapes \u2014
    // gray for the reference, amber for the live glyph \u2014 so the table's
    // numbers are visible as geometry, in place, at drawing scale.
    const LAYER_ID = 'avar2.studio.reference';
    const overlay = { contours: null, bbox: null, refCuts: null, liveCuts: null, sig: null };
    let layerInstalled = false;
    const repaint = () => {
      const ec = window.editorController;
      if (ec && ec.canvasController) ec.canvasController.requestUpdate();
    };
    const drawOverlay = (context, positionedGlyph, parameters) => {
      const contours = overlay.contours;
      if (!contours) return;
      const path = new Path2D();
      for (const c of contours) {
        path.moveTo(c[0][0], c[0][1]);
        for (let i = 1; i < c.length; i++) path.lineTo(c[i][0], c[i][1]);
        path.closePath();
      }
      context.fillStyle = parameters.fillColor;
      context.fill(path, 'nonzero');
      const line = (x1, y1, x2, y2, color, width, dashed) => {
        context.strokeStyle = color;
        context.lineWidth = width;
        context.setLineDash(dashed ? [parameters.dash * 2, parameters.dash] : []);
        context.beginPath();
        context.moveTo(x1, y1);
        context.lineTo(x2, y2);
        context.stroke();
      };
      const bb = overlay.bbox;
      const cuts = (m, color) => {
        if (!m || !bb) return;
        if (m.scanY !== undefined) line(bb.x0, m.scanY, bb.x1, m.scanY, color, parameters.hair, true);
        if (m.scanX !== undefined) line(m.scanX, bb.y0, m.scanX, bb.y1, color, parameters.hair, true);
        if (m.stemRun) line(m.stemRun[0], m.scanY, m.stemRun[1], m.scanY, color, parameters.bold, false);
        if (m.barRun) line(m.scanX, m.barRun[0], m.scanX, m.barRun[1], color, parameters.bold, false);
      };
      cuts(overlay.refCuts, parameters.refColor);
      cuts(overlay.liveCuts, parameters.liveColor);
    };
    const installLayer = () => {
      if (layerInstalled) return true;
      const ec = window.editorController;
      const vl = ec && ec.visualizationLayers;
      if (!vl || !Array.isArray(vl.definitions)) return false;
      const def = {
        identifier: LAYER_ID,
        name: 'avar2 reference overlay',
        selectionFunc: (visContext) => {
          if (!overlay.contours) return [];
          const pg = visContext.model.getSelectedPositionedGlyph
            && visContext.model.getSelectedPositionedGlyph();
          return pg ? [pg] : [];
        },
        zIndex: 100,
        // screenParameters are divided by the magnification before draw, so
        // these are screen pixels: constant weight at any zoom.
        screenParameters: { hair: 1, bold: 4, dash: 7 },
        colors: { fillColor: 'rgba(128,128,136,0.30)', refColor: 'rgba(96,96,104,0.85)',
                  liveColor: 'rgba(200,128,0,0.9)' },
        colorsDarkMode: { fillColor: 'rgba(170,170,180,0.28)', refColor: 'rgba(190,190,200,0.85)',
                          liveColor: 'rgba(255,212,121,0.9)' },
        draw: drawOverlay,
      };
      let idx = 0;
      while (idx < vl.definitions.length && vl.definitions[idx].zIndex <= def.zIndex) idx++;
      vl.definitions.splice(idx, 0, def);
      vl.toggle(LAYER_ID, true);
      layerInstalled = true;
      return true;
    };
    const syncOverlay = () => {
      installLayer();
      const refM = refGlyph && refData && refData.metrics && refData.metrics[refGlyph];
      if (!refM || !refM.contours || !refM.contours.length) {
        if (overlay.contours) { overlay.contours = null; overlay.sig = null; repaint(); }
        return;
      }
      const pts = [];
      for (const c of refM.contours) for (const pt of c) pts.push(pt);
      if (lastLive) for (const c of lastLive.contours) for (const pt of c) pts.push(pt);
      const xs = pts.map((pt) => pt[0]), ysv = pts.map((pt) => pt[1]);
      const bb = { x0: Math.min(...xs), x1: Math.max(...xs),
                   y0: Math.min(...ysv), y1: Math.max(...ysv) };
      const refCuts = measure(refM.contours, refM.advance || 0);
      const liveCuts = lastLive ? measure(lastLive.contours, lastLive.advance) : null;
      const sig = JSON.stringify([refGlyph, bb, refCuts, liveCuts]);
      if (sig !== overlay.sig) {
        overlay.bbox = bb; overlay.refCuts = refCuts; overlay.liveCuts = liveCuts;
        overlay.contours = refM.contours; overlay.sig = sig;
        repaint();
      }
    };
    const paintRef = () => {
      const m = refData && refData.metrics && refData.metrics[refGlyph];
      cell('tr.ref td.b', m ? num(m.bar) : '\u2013');
      cell('tr.ref td.s', m ? num(m.stem) : '\u2013');
      cell('tr.ref td.c', m && m.contrast ? m.contrast.toFixed(2) : '\u2013');
    };
    let fetchedLocKey = null;
    const loadRef = (g) => {
      refGlyph = g;
      if (!g) {
        // Blank is the OFF state: no reference row, no overlay.
        refData = null;
        fetchedLocKey = null;
        cell('.hd', '');
        paintRef();
        syncOverlay();
        return;
      }
      const loc = editLocation();
      fetchedLocKey = loc ? JSON.stringify(loc) : null;
      fetch('/api/control-axes/' + encodeURIComponent(cfg.tag)
            + '/reference-metrics?glyph=' + encodeURIComponent(cfg.glyph)
            + '&refs=' + encodeURIComponent(g)
            + (loc ? '&location=' + encodeURIComponent(JSON.stringify(loc)) : ''))
        .then(r => r.ok ? r.json() : null)
        .then((m) => {
          if (!m) return;
          refData = m;
          const loc = Object.entries(m.location || {})
            .filter(([k]) => k.toLowerCase() !== String(cfg.tag || '').toLowerCase())
            .map(([k, v]) => k + ' ' + v).join(' \u00b7 ');
          cell('.hd', 'at ' + loc);
          paintRef();
          syncOverlay();
        }).catch(() => {});
    };
    const input = box.querySelector('#avar2-ref-glyph');
    input.addEventListener('change', () => loadRef(input.value.trim()));
    input.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') input.blur();
    });
    paintRef();

    setInterval(() => {
      // Reference follows the canvas: switching layers (or nudging the
      // location) re-measures at the NEW location.
      if (refGlyph) {
        const loc = editLocation();
        const key = loc ? JSON.stringify(loc) : null;
        if (key && key !== fetchedLocKey) loadRef(refGlyph);
      }
      const lp = livePath();
      if (lp) {
        lastLive = lp;
        const m = measure(lp.contours, lp.advance);
        cell('tr.live td.g', lp.name);
        cell('tr.live td.b', num(m.bar));
        cell('tr.live td.s', num(m.stem));
        cell('tr.live td.c', m.contrast ? m.contrast.toFixed(2) : '\u2013');
      }
      syncOverlay();
    }, 400);

  }).catch((e) => console.warn('avar2-studio shim:', e));
})();
</script>
"""


def _is_html_response(headers) -> bool:
    ctype = headers.get("Content-Type", "")
    return "text/html" in ctype.lower()


def _is_css_response(headers) -> bool:
    ctype = headers.get("Content-Type", "")
    return "text/css" in ctype.lower()


def _rewrite_html_paths(body: bytes) -> bytes:
    """Rewrite absolute-path URL references in proxied Fontra HTML
    so they resolve under our ``/fontra/`` mount instead of root.
    Also injects the focused-UI stylesheet just before ``</head>``."""
    text = body.decode("utf-8", errors="replace")
    # ``href="/x"`` / ``src="/x"`` → keep "/fontra/x"
    text = text.replace('href="/', 'href="/fontra/')
    text = text.replace("href='/", "href='/fontra/")
    text = text.replace('src="/', 'src="/fontra/')
    text = text.replace("src='/", "src='/fontra/")
    # JS import map — Fontra ships ``"fontra/": "/"`` so any
    # ``import "fontra/core/..."`` resolves to ``/core/...`` on the
    # parent origin. With us proxying Fontra under ``/fontra/`` we
    # need to point that import map at ``/fontra/`` instead, or
    # every JS module load 404s and the canvas never initialises.
    text = text.replace('"fontra/": "/"', '"fontra/": "/fontra/"')
    text = text.replace("'fontra/': '/'", "'fontra/': '/fontra/'")
    # Inject the focused-UI CSS + studio shim before </head>;
    # tolerate uppercase. The CSS is session-aware (see
    # _fontra_focus_css); the shim self-deactivates unless
    # /api/fontra-shim-config marks the session as a studio edit.
    for needle in ("</head>", "</HEAD>"):
        if needle in text:
            text = text.replace(
                needle, _fontra_focus_css() + _FONTRA_STUDIO_SHIM_JS + needle, 1
            )
            break
    return text.encode("utf-8")


def _rewrite_css_paths(body: bytes) -> bytes:
    """Rewrite ``url(/x)`` references in proxied Fontra CSS."""
    text = body.decode("utf-8", errors="replace")
    text = text.replace("url(/", "url(/fontra/")
    text = text.replace('url("/', 'url("/fontra/')
    text = text.replace("url('/", "url('/fontra/")
    return text.encode("utf-8")


def _proxy_to_fontra(upstream_path: str):
    """Shared proxy mechanic — forward an HTTP request to Fontra and
    return its response, rewriting HTML/CSS path references and
    injecting focused-UI CSS into HTML on the way back. Called from
    every route that forwards to Fontra: /fontra/* and the
    root-level runtime-fetch paths Fontra needs (/lang, /data,
    /images, /webfonts, /projectlist, /serverinfo, /api/*).
    """
    if FONTRA_PROCESS is None or FONTRA_PROCESS.poll() is not None:
        return jsonify({"error": "Fontra subprocess is not running"}), 503

    import urllib.request
    import urllib.error

    upstream = f"http://127.0.0.1:{FONTRA_PORT}{upstream_path}"
    if request.query_string:
        upstream += "?" + request.query_string.decode("utf-8")

    req = urllib.request.Request(
        upstream,
        method=request.method,
        data=request.get_data() if request.method in ("POST", "PUT", "PATCH") else None,
    )
    # Forward useful request headers; skip Host (we're the new host).
    for header, value in request.headers.items():
        if header.lower() in ("host", "content-length"):
            continue
        req.add_header(header, value)

    try:
        upstream_resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        upstream_resp = exc  # serve error body too
    except Exception as exc:
        return jsonify({"error": f"Fontra proxy failed: {exc}"}), 502

    raw_body = upstream_resp.read()
    headers = dict(upstream_resp.headers.items())

    if _is_html_response(headers):
        body = _rewrite_html_paths(raw_body)
        headers["Content-Length"] = str(len(body))
    elif _is_css_response(headers):
        body = _rewrite_css_paths(raw_body)
        headers["Content-Length"] = str(len(body))
    else:
        body = raw_body

    # Strip headers that browsers may reject when we re-emit them.
    for h in ("Transfer-Encoding", "Connection"):
        headers.pop(h, None)

    return Response(body, status=upstream_resp.status, headers=headers)


@app.route('/fontra/', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/fontra/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def fontra_http_proxy(subpath: str):
    """Main Fontra proxy. Iframe loads /fontra/editor.html?... and
    everything resolves under this prefix via the HTML/CSS path
    rewrites injected on the way back."""
    return _proxy_to_fontra("/" + subpath)


# Fontra's frontend issues runtime fetches against root paths the
# importmap rewrite can't reach (the bundle calls things like
# ``fetch("/lang/en.js")`` directly). Each of those needs to
# resolve through our origin to keep Fontra same-origin with the
# iframe parent. The collision-prone /api/* is handled by the
# catch-all below — Flask routes specific avar2-studio /api paths
# first and only falls through to the catch-all when nothing else
# matches.
@app.route('/lang/<path:subpath>', methods=['GET'])
def fontra_lang_proxy(subpath: str):
    return _proxy_to_fontra(f"/lang/{subpath}")


@app.route('/data/<path:subpath>', methods=['GET'])
def fontra_data_proxy(subpath: str):
    return _proxy_to_fontra(f"/data/{subpath}")


@app.route('/images/<path:subpath>', methods=['GET'])
def fontra_images_proxy(subpath: str):
    return _proxy_to_fontra(f"/images/{subpath}")


@app.route('/webfonts/<path:subpath>', methods=['GET'])
def fontra_webfonts_proxy(subpath: str):
    return _proxy_to_fontra(f"/webfonts/{subpath}")


@app.route('/projectlist', methods=['GET'])
def fontra_projectlist_proxy():
    return _proxy_to_fontra("/projectlist")


@app.route('/serverinfo', methods=['GET'])
def fontra_serverinfo_proxy():
    return _proxy_to_fontra("/serverinfo")


# Catch-all for /api/<anything> that doesn't match an avar2-studio
# route. Flask's routing prefers more-specific rules, so
# /api/glyph-coverage / /api/control-axes/<tag> / etc. match
# before this catch-all and stay on avar2-studio. Anything Fontra
# requests through /api/ (export, etc.) falls through here.
@app.route('/api/<path:rest>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def fontra_api_fallthrough(rest: str):
    return _proxy_to_fontra(f"/api/{rest}")


# Root-level catch-all. Fontra's client bundle ships chunk JS
# (``230.chunk.js``) and WASM modules (``<hash>.wasm``,
# ``<hash>.module.wasm``) at the root of its content tree —
# these are dynamically imported by the editor and aren't
# referenced from the editor.html we rewrite. Without this
# fall-through they 404 against avar2-studio's root, the editor
# fails to init HarfBuzz / lazy chunks, and the canvas comes up
# blank.
#
# Flask/Werkzeug routes by specificity: all specific avar2-studio
# routes (``/``, ``/static/<path>``, ``/manifest.json``,
# ``/favicon.ico``, every ``/api/*``, ``/fontra/*``, ``/lang/*``,
# ``/data/*``, ``/images/*``, ``/webfonts/*``, ``/projectlist``,
# ``/serverinfo``) match first. Only paths none of those claim
# fall through here — i.e. exactly the Fontra runtime files we
# want to forward.
@app.route('/<path:rest>', methods=['GET'])
def fontra_root_fallthrough(rest: str):
    return _proxy_to_fontra(f"/{rest}")


@sock.route('/websocket')
def fontra_ws_proxy(ws):
    """Proxy WebSocket traffic to Fontra. The frontend opens
    ``ws://<host>/websocket?...``; we connect upstream to
    ``ws://127.0.0.1:8001/websocket?...`` with the same query string
    and relay messages in both directions until either side hangs up.
    """
    if FONTRA_PROCESS is None or FONTRA_PROCESS.poll() is not None:
        ws.close()
        return

    import websocket as ws_client_lib  # websocket-client; pulled in by flask-sock indirectly
    qs = request.query_string.decode("utf-8")
    upstream_url = f"ws://127.0.0.1:{FONTRA_PORT}/websocket"
    if qs:
        upstream_url += "?" + qs

    upstream = ws_client_lib.create_connection(upstream_url, timeout=30)
    upstream.settimeout(0.05)

    import threading as _threading

    def pump_upstream_to_client():
        try:
            while True:
                try:
                    msg = upstream.recv()
                except ws_client_lib._exceptions.WebSocketTimeoutException:
                    continue
                except Exception:
                    break
                if msg is None:
                    break
                ws.send(msg)
        finally:
            try:
                ws.close()
            except Exception:
                pass

    pumper = _threading.Thread(target=pump_upstream_to_client, daemon=True)
    pumper.start()

    try:
        while True:
            msg = ws.receive(timeout=None)
            if msg is None:
                break
            upstream.send(msg)
    except Exception:
        pass
    finally:
        try:
            upstream.close()
        except Exception:
            pass


@app.route('/api/fontra-shim-config')
def fontra_shim_config():
    """Session config for the shim injected into proxied Fontra
    pages. Must be registered before Fontra's /api catch-all so the
    specific rule wins."""
    return jsonify(FONTRA_EDITOR_SESSION or {"studio": False})


@app.route('/api/control-axes/<tag>/open-editor', methods=['POST'])
def open_control_axis_in_editor(tag: str):
    """Spin up Fontra and return the iframe URL the frontend should
    load. Studio-authored axes edit the shadow copy (the coverage
    save regenerates it with seed brace layers). When no shadow
    exists — source-derived axes on a .designspace or plain .glyphs —
    Fontra opens the original source directly, flagged in the
    response as ``editing_original``.
    """
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400

    # Route by the AXIS, not by global shadow existence. A studio
    # (sidecar-declared) axis only exists in the shadow — opening the
    # original for it would let Fontra write into the user's real file
    # at a location that isn't even declared there. Conversely a
    # source-derived axis's layers live in the original — opening the
    # shadow for it would strand the designer's edits in a copy that
    # the next regeneration rebuilds from the original.
    try:
        is_studio_axis = any(
            (ax.get("tag") or "").lower() == tag.lower()
            for ax in _control_axes.list_axes(ORIGINAL_PATH)
        )
    except Exception:
        is_studio_axis = False

    if is_studio_axis:
        if not _control_axes.shadow_exists(ORIGINAL_PATH):
            # No coverage / no shadow yet — there's nothing to edit.
            return jsonify({
                "error": "No shadow file yet. Add coverage glyphs first so the studio can seed brace layers."
            }), 400
        # Studio-authored axes: Fontra edits the shadow copy; the
        # user's original source stays untouched.
        target_path = _control_axes.shadow_path_for(ORIGINAL_PATH)
        # The shadow/ dir contains only the shadow file, so serving
        # the directory exposes nothing else.
        content_root = target_path.parent
        editing_original = False
    else:
        # Source-derived axis (brace layers in the .glyphs, alternate
        # masters in the .designspace): its source of truth IS the
        # original file, so open it directly. Fontra saves write to
        # the user's actual source; the frontend surfaces that. Pass
        # the FILE as the root (Fontra's single-file mode) so sibling
        # font projects in the same folder aren't exposed.
        target_path = ORIGINAL_PATH
        content_root = target_path
        editing_original = True

    # Record the session for the proxy (CSS variant) and the injected
    # shim (which layers to show + batch-enable). Studio-axis sessions
    # expose the designspace-navigation panel trimmed to this axis's
    # studio layers; everything else keeps the bare-canvas UI.
    global FONTRA_EDITOR_SESSION
    spec = next(
        (
            ax for ax in (_control_axes.list_axes(ORIGINAL_PATH) or [])
            if (ax.get("tag") or "").lower() == tag.lower()
        ),
        None,
    ) if is_studio_axis else None
    # The glyph is the frontend's to know (it builds Fontra's URL fragment from
    # it); recording it here lets the injected shim ask for THIS layer's
    # reference measurements without reaching into Fontra's scene state.
    body = request.get_json(silent=True) or {}
    FONTRA_EDITOR_SESSION = {
        "studio": bool(is_studio_axis and not editing_original),
        # Authored tag case (fvar tags are case-sensitive; the shim matches
        # layer labels printed in authored case).
        "tag": (spec or {}).get("tag") or tag,
        "axis_name": (spec or {}).get("display_name") or (spec or {}).get("tag") or tag,
        "axis_default": float((spec or {}).get("default") or 0.0),
        "glyph": (body.get("glyph") or "").strip() or None,
    }

    try:
        port = _ensure_fontra_running(content_root, watch_file=target_path)
    except Exception as exc:
        return jsonify({"error": f"Failed to start Fontra: {exc}"}), 500

    project = target_path.name  # e.g. "CrispyMini.glyphs"
    # Return the same-origin URL through avar2-studio's reverse
    # proxy. This is what unlocks CSS injection (the focused-UI
    # stylesheet that hides irrelevant Fontra panels) — cross-origin
    # iframes don't expose their DOM. The direct ``:8001`` URL is
    # still useful as a "Open in new tab" escape hatch.
    same_origin_url = f"/fontra/editor.html?project={project}"
    direct_url = f"http://127.0.0.1:{port}/editor.html?project={project}"
    return jsonify({
        "success": True,
        "url": same_origin_url,
        "direct_url": direct_url,
        "port": port,
        "project": project,
        "tag": (spec or {}).get("tag") or tag,
        # True when Fontra is serving the user's actual source (no
        # shadow) — saves write straight to it. The frontend shows a
        # notice so that's never a surprise.
        "editing_original": editing_original,
    })


@app.route('/api/control-axes/<tag>/layers', methods=['PUT'])
def set_control_axis_layers(tag: str):
    """Replace an axis's unified ``layers`` list. Body::

        {"layers": [
            {"glyph": "e", "location": {"crbr": -100}},
            {"glyph": "e", "location": {"crbr": 100}},
            {"glyph": "e", "location": {"crbr": -50, "XOPQ": 407}}
        ]}

    Every brace layer is explicit — no auto seeding at axis-min/max.
    Each entry pins a brace layer at a specific N-D location for one
    glyph. Axes omitted from ``location`` interpolate from masters.
    Saves to sidecar, regenerates the shadow with the new layers,
    and triggers a rebuild. Switches the build path to the shadow
    when there's at least one layer; otherwise reverts to original.
    """
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    entries = data.get("layers")
    if not isinstance(entries, list):
        return jsonify({"error": "Body must include 'layers' as a list."}), 400
    try:
        # Whole-list replace. Prefer the delta endpoint for interactive edits —
        # this one overwrites whatever the caller didn't know about.
        with SIDECAR_LOCK:
            stored = _control_axes.set_layers(ORIGINAL_PATH, tag, entries)
        schedule_shadow_rebuild()
        return jsonify({"success": True, "tag": tag, "layers": stored})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        print(f"Error setting control-axis layers: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/control-axes/<tag>/layers/delta', methods=['POST'])
def patch_control_axis_layers(tag: str):
    """Add and/or remove brace layers without sending the whole list. Body::

        {"add": [{"glyph": "e", "location": {...}}],
         "remove": [{"glyph": "e", "location": {...}}]}

    The on-disk list is the base, so an edit made while the client's copy is
    stale composes instead of clobbering — the whole-list PUT silently drops
    layers the caller hadn't loaded yet, which reads as layers "resetting".
    Removals apply before additions, so a replace is remove+add. Serialized on
    SIDECAR_LOCK so concurrent edits can't interleave.
    """
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    add = data.get("add") or []
    remove = data.get("remove") or []
    if not isinstance(add, list) or not isinstance(remove, list):
        return jsonify({"error": "'add' and 'remove' must be lists."}), 400
    if not add and not remove:
        return jsonify({"error": "Nothing to do: provide 'add' and/or 'remove'."}), 400
    try:
        with SIDECAR_LOCK:
            stored = _control_axes.apply_layer_delta(ORIGINAL_PATH, tag, add=add, remove=remove)
        schedule_shadow_rebuild()
        return jsonify({"success": True, "tag": tag, "layers": stored})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        print(f"Error setting control-axis layers: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _measure_strokes(glyph_set, hmtx, name):
    """Vertical-stem and horizontal-bar thickness for one glyph.

    A horizontal scanline across the letter cuts its vertical stems; a vertical
    scanline cuts its horizontal bars. Ink runs on each are the stroke widths.
    Returns the thinnest of each, which is what "how heavy is this stroke"
    means for a correction like ymod, or None where the scan is degenerate.
    """
    from fontTools.pens.recordingPen import DecomposingRecordingPen

    pen = DecomposingRecordingPen(glyph_set)
    try:
        glyph_set[name].draw(pen)
    except Exception:
        return None
    contours, cur = [], []
    for op, args in pen.value:
        if op == "moveTo":
            cur = [args[0]]
        elif op in ("lineTo", "curveTo", "qCurveTo"):
            cur.extend([p for p in args if p])
        elif op == "closePath":
            if cur:
                contours.append(cur)
                cur = []
    if cur:
        contours.append(cur)
    if not contours:
        return None

    def runs(coord, vertical):
        vals = []
        for c in contours:
            for i in range(len(c)):
                p, q = c[i], c[(i + 1) % len(c)]
                a1, b1, a2, b2 = (p[0], p[1], q[0], q[1]) if vertical else (p[1], p[0], q[1], q[0])
                if (a1 <= coord < a2) or (a2 <= coord < a1):
                    vals.append(b1 + (coord - a1) * (b2 - b1) / (a2 - a1))
        vals.sort()
        return [vals[i + 1] - vals[i] for i in range(0, len(vals) - 1, 2)]

    # Scan relative to the glyph's OWN ink extent rather than a font metric:
    # self-correcting for any glyph at any location, and it keeps this in step
    # with the live measurement the editor HUD runs on Fontra's in-memory path.
    ys = [pt[1] for c in contours for pt in c]
    if not ys:
        return None
    y_min, y_max = min(ys), max(ys)
    advance = hmtx[name][0]
    # Sample the stems low in the letter (crossbars usually sit near the
    # middle, where a horizontal scan reads one solid run instead of two stems).
    stems = [r for r in runs(y_min + (y_max - y_min) * 0.25, False) if r > 5]
    bars = [r for r in runs(advance * 0.5, True) if r > 5]
    out = {"advance": round(float(advance), 1)}
    if stems:
        out["stem"] = round(float(min(stems)), 1)
    if bars:
        out["bar"] = round(float(min(bars)), 1)
    if stems and bars:
        out["contrast"] = round(float(min(stems)) / float(min(bars)), 2)
    # The measured polygon itself, for the HUD's overlay preview: what the
    # scanlines actually cut (off-curves as vertices), so the drawing and
    # the numbers can never disagree.
    out["contours"] = [[[round(float(x), 1), round(float(y), 1)] for x, y in c]
                       for c in contours]
    return out


@app.route('/api/control-axes/<tag>/reseed', methods=['POST'])
def reseed_control_axis_layers(tag: str):
    """Re-seed brace layers from the CURRENT source. Body::

        { "layers": [{"glyph": "e", "location": {...}}], "force": false }

    Omit ``layers`` to target the whole axis.

    A drawn layer's outline is captured into the sidecar and restored in
    preference to re-seeding, so it survives the shadow being wiped — and stays
    frozen against later edits to the masters it was drawn over. Re-seeding
    pulls the updated source through and DISCARDS the drawing, so layers
    holding one come back as 409 ``blocked`` unless ``force`` is set. There is
    no undo once the sidecar is written.
    """
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    body = request.get_json(silent=True) or {}
    layers = body.get("layers")
    if layers is not None and not isinstance(layers, list):
        return jsonify({"error": "layers must be a list of {glyph, location}"}), 400
    force = bool(body.get("force"))
    try:
        result = _control_axes.reseed_layers(ORIGINAL_PATH, tag, layers, force)
    except KeyError:
        return jsonify({"error": f"control axis '{tag}' not found"}), 404
    except Exception as e:  # noqa: BLE001
        print(f"Error re-seeding control axis layers: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500

    if result["blocked"]:
        names = ", ".join(sorted({str(b["glyph"]) for b in result["blocked"]}))
        return jsonify({
            "error": "Some layers hold hand-drawn outlines",
            "detail": ("Re-seeding replaces the drawing with a fresh "
                       "interpolation of the current source, and cannot be "
                       "undone. Affected glyphs: %s." % names),
            **result,
        }), 409

    if result["reseeded"]:
        schedule_shadow_rebuild()
    return jsonify(result)


@app.route('/api/control-axes/<tag>/reference-metrics', methods=['GET'])
def control_axis_reference_metrics(tag: str):
    """Stroke thicknesses for a layer's glyph and its reference glyphs, all cut
    at that layer's exact coordinates with the secondary axis OFF.

    Drawing a horizontal correction means matching a glyph's bars to the ones
    that already read correctly — E's to H's, at the same designspace point.
    Fontra can measure what you have drawn (its ruler stays enabled in studio
    sessions) but it cannot tell you what to aim AT, because the reference is
    another glyph. These are those target numbers.

    The edited glyph's own figures are its pre-edit state — where you started,
    not what is on screen once you begin drawing.

    Query: ``glyph`` (required), ``index`` (which layer, default 0),
    ``refs`` (comma-separated reference glyphs, default "H,N,O").
    """
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    if not (VARIABLE_FONT_PATH and Path(VARIABLE_FONT_PATH).exists()):
        return jsonify({"error": "Font not built yet"}), 404
    glyph = (request.args.get("glyph") or "").strip()
    if not glyph:
        return jsonify({"error": "glyph is required"}), 400
    try:
        index = int(request.args.get("index") or 0)
    except ValueError:
        index = 0
    refs = [g.strip() for g in (request.args.get("refs") or "H,N,O").split(",") if g.strip()]
    # The EDITED layer's location, sent live by the Fontra shim (scene
    # fontLocationSourceMapped — keyed by axis NAME or tag). Without it
    # the lookup below falls back to the glyph's FIRST sidecar layer,
    # which is simply the wrong location whenever a glyph carries
    # several layers on the axis (e's six ymod layers): the HUD then
    # shows a self-consistent guide and overlay that are both measured
    # somewhere else.
    explicit_location = None
    loc_param = request.args.get("location")
    if loc_param:
        try:
            parsed = json.loads(loc_param)
            if isinstance(parsed, dict):
                explicit_location = {str(k): float(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            return jsonify({"error": "location must be a JSON object of axis: value"}), 400

    axis = _control_axes.find_axis(ORIGINAL_PATH, tag)
    if axis is None:
        return jsonify({"error": f"No secondary axis '{tag}'"}), 404
    layers = [l for l in (axis.get("layers") or []) if l.get("glyph") == glyph]
    if not layers and explicit_location is None:
        return jsonify({"error": f"'{glyph}' has no layers on '{tag}'"}), 404
    layer = layers[min(index, len(layers) - 1)] if layers else {}

    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    try:
        font = TTFont(str(VARIABLE_FONT_PATH))
        fvar_axes = {a.axisTag: a for a in font["fvar"].axes}
        if explicit_location is not None:
            # Normalize keys: the scene speaks axis NAMES (source space);
            # the sidecar and fvar speak tags. Accept both.
            name_to_tag = {}
            try:
                src_font, _fmt = _source_font.load_source(GLYPHS_PATH)
                for ax in _source_font.get_axes(src_font):
                    name_to_tag[str(ax.get("name") or "").lower()] = str(ax.get("tag") or "")
                    name_to_tag[str(ax.get("tag") or "").lower()] = str(ax.get("tag") or "")
            except Exception:  # noqa: BLE001
                pass
            for a_tag in fvar_axes:
                name_to_tag.setdefault(a_tag.lower(), a_tag)
            stored = {}
            for k, v in explicit_location.items():
                mapped = name_to_tag.get(str(k).lower())
                if mapped:
                    stored[mapped.lower()] = v
        else:
            stored = {str(k).lower(): v for k, v in (layer.get("location") or {}).items()}
        location = {}
        for a_tag, a in fvar_axes.items():
            if a_tag.lower() == tag.lower():
                location[a_tag] = a.defaultValue
            elif a_tag.lower() in stored:
                location[a_tag] = float(stored[a_tag.lower()])
            else:
                location[a_tag] = a.defaultValue
        try:
            inst = instantiateVariableFont(font, location, inplace=False)
        except NotImplementedError:
            font = TTFont(str(VARIABLE_FONT_PATH))
            if "avar" in font:
                del font["avar"]
            inst = instantiateVariableFont(font, location, inplace=False)

        gs = inst.getGlyphSet()
        hmtx = inst["hmtx"]
        os2 = inst["OS/2"] if "OS/2" in inst else None
        x_height = float(getattr(os2, "sxHeight", 0) or 0) or 1000.0
        cap_height = float(getattr(os2, "sCapHeight", 0) or 0) or 1400.0

        order = set(inst.getGlyphOrder())
        measured = {}
        for name in [glyph] + [r for r in refs if r != glyph]:
            if name not in order:
                continue
            m = _measure_strokes(gs, hmtx, name)
            if m:
                measured[name] = m
        return jsonify({
            "tag": tag,
            "glyph": glyph,
            "location": {k: v for k, v in location.items() if k.lower() != tag.lower()},
            "metrics": measured,
            # The scan geometry, so a caller measuring the LIVE glyph in the
            # editor can use the same rule and produce comparable numbers.
            "x_height": round(x_height, 1),
            "cap_height": round(cap_height, 1),
            "note": "Figures for the edited glyph are its pre-edit state.",
        })
    except Exception as e:
        print(f"Error measuring reference metrics: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/control-axes/<tag>', methods=['PATCH'])
def update_control_axis(tag: str):
    """Edit an existing control axis's display_name / min / max /
    default. Tag is immutable — a rename would need to migrate every
    layer's location dict. Any field left absent from the body is
    left unchanged. Regenerates the shadow after a successful edit
    so the preview reflects the new range immediately."""
    global VARIABLE_FONT_PATH
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        entry = _control_axes.update_axis(
            ORIGINAL_PATH,
            tag,
            display_name=data.get("display_name"),
            default=data.get("default"),
            min_value=data.get("min"),
            max_value=data.get("max"),
        )
        try:
            # Full re-resolve, not a bare regenerate_shadow: the compile
            # source is the TRANSFORMED shadow (grade + source-stage
            # transforms layered on the control shadow), and refreshing
            # only stage 1 left it stale — an axis-default edit then
            # rebuilt with the OLD default whenever grade/rounding was
            # enabled (observed: ymod default stuck at 100 after the
            # sidecar said 0).
            _resolve_active_source()
            # See set_control_axis_layers: keep the last-good font on failure.
            try:
                trigger_build()
            except Exception as build_exc:
                print(f"Warning: rebuild after axis update failed: {build_exc}", file=sys.stderr)
        except Exception as shadow_exc:
            print(f"Warning: shadow refresh after update failed: {shadow_exc}", file=sys.stderr)
        return jsonify({"success": True, "axis": entry})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        print(f"Error updating control axis: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/control-axes/<tag>', methods=['DELETE'])
def delete_control_axis(tag: str):
    """Remove a control-axis declaration from the sidecar. If the
    sidecar still has any axes, regenerate the shadow without the
    deleted one; if it's now empty, remove the shadow entirely so
    the build pipeline falls back to the original source."""
    global GLYPHS_PATH, VARIABLE_FONT_PATH
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    try:
        removed = _control_axes.remove_axis(ORIGINAL_PATH, tag)
        if not removed:
            return jsonify({"error": f"control axis '{tag}' not found"}), 404

        # Re-derive the shadow + build target. If the sidecar still
        # has any axis with brace layers, the shadow stays active.
        # Otherwise the shadow can be dropped and the build reverts to
        # the original. (Gate on ``layers`` — the flat per-axis list
        # of {glyph, location}; ``coverage`` is a legacy key the
        # current sidecar never emits, so gating on it left the build
        # stuck on the original.)
        try:
            # Re-derive the active source from the REMAINING control axes and
            # any grades — grade must survive deleting an unrelated control axis.
            _resolve_active_source()
            # Drop the shadow dir only when NEITHER feature still needs it.
            if (GLYPHS_PATH == ORIGINAL_PATH
                    and not _control_axes.list_axes(ORIGINAL_PATH)
                    and not _grade.list_graded_instances(ORIGINAL_PATH)):
                _control_axes.remove_shadow(ORIGINAL_PATH)
            # See set_control_axis_layers: keep the last-good font on failure.
            try:
                trigger_build()
            except Exception as build_exc:
                print(f"Warning: rebuild after delete failed: {build_exc}", file=sys.stderr)
        except Exception as shadow_exc:
            print(f"Warning: shadow refresh after delete failed: {shadow_exc}", file=sys.stderr)

        return jsonify({"success": True, "tag": tag})
    except Exception as e:
        print(f"Error deleting control axis: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


@app.route('/api/config/export', methods=['GET'])
def export_config_bundle():
    """Download the studio-authored configuration (control axes +
    brace-layer declarations, avar2 mappings CSV, transforms) as one
    portable JSON bundle. See config_port.py for the format."""
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    try:
        # build_export captures drawn outlines into the sidecar first, which
        # is a write next to the watched source — suppress the watcher so an
        # export does not look like a designer edit and trigger a rebuild.
        _suppress_watcher()
        bundle = _config_port.build_export(ORIGINAL_PATH, _get_avar2_csv_path())
    except Exception as e:
        print(f"Error exporting config: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500
    family = (bundle.get("source") or {}).get("family_name") or "source"
    return Response(
        json.dumps(bundle, indent=2) + "\n",
        mimetype="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_config_port.bundle_filename(family)}"'
            )
        },
    )


@app.route('/api/config/import', methods=['POST'])
def import_config_bundle():
    """Validate (``dry_run: true``) or apply a config bundle.

    All-or-nothing: a failed validation writes nothing and returns 400
    with the report. Applying REPLACES the current studio config
    (control axes + transforms wholesale; the avar2 CSV only when the
    bundle carries one), then regenerates the shadow and rebuilds.
    """
    global GLYPHS_PATH
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    body = request.get_json(silent=True) or {}
    bundle = body.get("bundle")
    if bundle is None:
        return jsonify({"error": "POST JSON {bundle: {...}, dry_run: bool}"}), 400
    dry_run = bool(body.get("dry_run", True))
    try:
        report = _config_port.validate_bundle(bundle, ORIGINAL_PATH)
    except Exception as e:
        print(f"Error validating config bundle: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500
    if dry_run or not report["ok"]:
        return jsonify(report), (200 if dry_run else 400)

    with SIDECAR_LOCK:
        try:
            # CSV destination: the CSV the studio is currently editing,
            # else the conventional <stem>-avar.csv next to the ORIGINAL
            # (never the shadow — same rule as every sidecar).
            csv_dest = _get_avar2_csv_path() or _get_preview_csv_path()
            report = _config_port.apply_bundle(bundle, ORIGINAL_PATH, csv_dest)
        except Exception as e:
            print(f"Error applying config bundle: {e}", file=sys.stderr)
            return jsonify({"error": str(e)}), 500

        # Same shadow + build dance as delete_control_axis: re-derive the
        # active source from the imported control axes AND grades, dropping the
        # shadow only when neither needs it; keep the last-good font on failure.
        try:
            _resolve_active_source()
            if (GLYPHS_PATH == ORIGINAL_PATH
                    and not _control_axes.list_axes(ORIGINAL_PATH)
                    and not _grade.list_graded_instances(ORIGINAL_PATH)):
                _control_axes.remove_shadow(ORIGINAL_PATH)
            try:
                trigger_build()
            except Exception as build_exc:
                print(f"Warning: rebuild after config import failed: {build_exc}",
                      file=sys.stderr)
        except Exception as shadow_exc:
            print(f"Warning: shadow refresh after config import failed: {shadow_exc}",
                  file=sys.stderr)

    return jsonify(report)


@app.route('/api/avar2/axes', methods=['GET'])
def get_avar2_axes():
    """Get traditional axes (in:) and parametric axes (out:) from CSV, including metadata."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({
                "error": "avar2-mappings.csv not found"
            }), 404
        
        
        rows, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)

        # Normalize traditional axis names
        traditional_axes = [_csv_io.normalize_in_axis_name(col) for col in in_cols]
        
        # Load metadata
        metadata = _load_axis_metadata()
        
        # Get parametric axes from the source file and populate metadata.
        glyphs_axes_info = {}
        if GLYPHS_PATH and GLYPHS_PATH.exists():
            try:
                font, _fmt = _source_font.load_source_cached(GLYPHS_PATH)
                for axis in _source_font.get_axes(font):
                    tag = axis["tag"]
                    tag_upper = tag.upper()
                    glyphs_axes_info[tag_upper] = {
                        "display_name": axis.get("name") or tag_upper,
                        "registered_tag": tag.lower(),
                        # Only axes with master coverage are truly
                        # parametric. Empty axes (declared in source but
                        # no master deltas) are user-editable avar2 inputs.
                        "is_parametric": axis.get("has_master_coverage", True),
                        "min": float(axis["min"]),
                        "max": float(axis["max"]),
                    }
            except Exception as e:
                print(f"Warning: Could not read axes from source file: {e}", file=sys.stderr)
        
        # Build axes with metadata
        # If axis doesn't exist in metadata, create default entry and save it
        axes_with_metadata = {}
        metadata_updated = False
        
        # First, populate parametric axes from Glyphs file (these are in out_cols)
        for col in out_cols:
            col_upper = col.upper()
            if col_upper in glyphs_axes_info:
                # This is a parametric axis from Glyphs file
                glyphs_info = glyphs_axes_info[col_upper]
                if col_upper not in metadata:
                    metadata[col_upper] = {
                        "display_name": glyphs_info["display_name"],
                        "registered_tag": glyphs_info["registered_tag"],
                        "is_parametric": glyphs_info.get("is_parametric", True),
                        "min": glyphs_info.get("min", 0.0),
                        "max": glyphs_info.get("max", 1000.0)
                    }
                    metadata_updated = True
                else:
                    desired_parametric = glyphs_info.get("is_parametric", True)
                    if metadata[col_upper].get("is_parametric") != desired_parametric:
                        metadata[col_upper]["is_parametric"] = desired_parametric
                        metadata_updated = True
                    # Always update display_name, registered_tag, min, and max from Glyphs (source of truth)
                    if metadata[col_upper].get("display_name") != glyphs_info["display_name"]:
                        metadata[col_upper]["display_name"] = glyphs_info["display_name"]
                        metadata_updated = True
                    if metadata[col_upper].get("registered_tag") != glyphs_info["registered_tag"]:
                        metadata[col_upper]["registered_tag"] = glyphs_info["registered_tag"]
                        metadata_updated = True
                    # Update min/max from Glyphs (parametric axes should always match Glyphs)
                    if metadata[col_upper].get("min") != glyphs_info.get("min", 0.0):
                        metadata[col_upper]["min"] = glyphs_info.get("min", 0.0)
                        metadata_updated = True
                    if metadata[col_upper].get("max") != glyphs_info.get("max", 1000.0):
                        metadata[col_upper]["max"] = glyphs_info.get("max", 1000.0)
                        metadata_updated = True
                axes_with_metadata[col_upper] = metadata[col_upper]
            else:
                # Parametric axis not found in Glyphs (shouldn't happen, but handle gracefully)
                if col_upper not in metadata:
                    normalized_tag = _csv_io.normalize_in_axis_name(col)
                    metadata[col_upper] = {
                        "display_name": col,
                        "registered_tag": normalized_tag,
                        "is_parametric": True,
                        "min": -1000,
                        "max": 1000
                    }
                    metadata_updated = True
                axes_with_metadata[col_upper] = metadata[col_upper]
        
        # Then, handle traditional axes (not in Glyphs file)
        # Map of normalized tags to default display names
        default_display_names = {
            "wght": "Weight",
            "wdth": "Width",
            "opsz": "Optical Size",
            "cntr": "Contrast",
            "spac": "Spacing",
            "grad": "Grade",
            "slnt": "Slant",
            "ital": "Italic"
        }
        
        for col in in_cols:
            if col not in metadata:
                # Create default entry for traditional axis (not in Glyphs file)
                normalized_tag = _csv_io.normalize_in_axis_name(col)
                # Use proper display name if available, otherwise use column name
                default_display_name = default_display_names.get(normalized_tag.lower(), col)
                # Seed with the axis's real extent where we can recover it —
                # the placeholder only stands in when nothing defines the axis.
                derived = _derive_traditional_range(normalized_tag, rows, col)
                axis_min, axis_max = derived or (_PLACEHOLDER_MIN, _PLACEHOLDER_MAX)
                metadata[col] = {
                    "display_name": default_display_name,
                    "registered_tag": normalized_tag,
                    "min": axis_min,
                    "max": axis_max,
                    "is_parametric": False  # Mark as traditional (not in Glyphs file)
                }
                metadata_updated = True
            else:
                # The flag is DERIVED from the source's real axes, never
                # trusted from the file: a stale is_parametric=true (from
                # an era when the tag WAS a source axis) otherwise locks
                # the mapping slider and the edit/delete modal read-only
                # forever.
                normalized_tag = _csv_io.normalize_in_axis_name(col)
                glyphs_axis_tags = _get_glyphs_axis_tags()
                desired_flag = normalized_tag in glyphs_axis_tags
                if metadata[col].get("is_parametric") != desired_flag:
                    metadata[col]["is_parametric"] = desired_flag
                    metadata_updated = True
                
                # Update display_name if it's still the column name (tag) - migrate to proper display name
                normalized_tag = _csv_io.normalize_in_axis_name(col)
                if metadata[col].get("display_name") == col and normalized_tag.lower() in default_display_names:
                    metadata[col]["display_name"] = default_display_names[normalized_tag.lower()]
                    metadata_updated = True
            
            axes_with_metadata[col] = metadata[col]

        # Repair traditional axes still stuck on the placeholder extent —
        # entries seeded before their CSV column had values, or before the
        # transform that injects them was enabled. Parametric axes already
        # re-sync from the source above; this gives traditional axes the
        # same self-healing instead of leaking -1000/1000 into real fvar.
        if _repair_placeholder_ranges(metadata, rows, in_cols):
            metadata_updated = True

        # Save updated metadata if we added any new axes
        if metadata_updated:
            _save_axis_metadata(metadata)

        # Ensure every axis entry has a numeric `default` so the frontend
        # can initialise sliders without hardcoding values.
        TRADITIONAL_DEFAULTS = TRADITIONAL_AXIS_DEFAULTS
        for entry in axes_with_metadata.values():
            if "default" in entry and isinstance(entry["default"], (int, float)):
                continue
            tag = (entry.get("registered_tag") or "").lower()
            if entry.get("is_parametric"):
                # Parametric: prefer min (matches get_axes_from_glyphs behaviour).
                entry["default"] = entry.get("min", 0.0)
            elif tag in TRADITIONAL_DEFAULTS:
                entry["default"] = TRADITIONAL_DEFAULTS[tag]
            else:
                lo = entry.get("min", 0.0) or 0.0
                hi = entry.get("max", 0.0) or 0.0
                entry["default"] = (lo + hi) / 2.0

        # Exclude control axes — they belong to the CONTROL AXES
        # section, not AVAR2 MAPPINGS. A control axis has no master
        # coverage (so it would otherwise be misclassified as a
        # traditional/avar2 target), but it's neither parametric nor an
        # avar2 mapping input; it's driven by brace layers.
        control_tags_upper = set()
        if ORIGINAL_PATH is not None:
            try:
                control_tags_upper = {
                    str(a.get("tag", "")).upper()
                    for a in _control_axes.list_axes(ORIGINAL_PATH)
                    if a.get("tag")
                }
            except Exception:
                control_tags_upper = set()
        if control_tags_upper:
            axes_with_metadata = {
                k: v for k, v in axes_with_metadata.items()
                if k.upper() not in control_tags_upper
            }
            in_cols = [
                c for c in in_cols
                if c.upper() not in control_tags_upper
                and _csv_io.normalize_in_axis_name(c).upper() not in control_tags_upper
            ]
            traditional_axes = [
                t for t in traditional_axes if t.upper() not in control_tags_upper
            ]

        return jsonify({
            "traditional_axes": {
                "columns": in_cols,
                "normalized": traditional_axes
            },
            "parametric_axes": out_cols,  # All parametric axes from Glyphs file
            "metadata": axes_with_metadata  # Includes all axes with is_parametric flag + default
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/instance/<instance_name>/editing', methods=['POST'])
def register_editing_instance(instance_name: str):
    """Register an instance as being edited (protect from sync)."""
    global EDITING_INSTANCES
    EDITING_INSTANCES.add(instance_name)
    return jsonify({"success": True, "message": f"Instance '{instance_name}' registered as editing"})

@app.route('/api/instance/<instance_name>', methods=['DELETE'])
def delete_instance(instance_name: str):
    """Delete or demote an instance. Three modes selected by query param:

    - default (no param)        — remove from source file AND CSV
    - ``?csv_only=true``        — remove from CSV; keep source declaration
    - ``?source_only=true``     — remove from source; keep CSV row (DEMOTE
                                  a source instance to studio-only)

    The two ``_only`` modes are mutually exclusive; specifying both
    returns 400.
    """
    csv_only_flag = request.args.get('csv_only', '').lower() in ('1', 'true', 'yes')
    source_only_flag = request.args.get('source_only', '').lower() in ('1', 'true', 'yes')

    if csv_only_flag and source_only_flag:
        return jsonify({"error": "csv_only and source_only are mutually exclusive."}), 400

    # For source-file writeback we still require the Glyphs file not be
    # holding unsaved edits in the Glyphs.app process. CSV-only deletes
    # don't touch the source, so the check doesn't apply.
    touches_source = not csv_only_flag  # both default and source_only touch source
    if touches_source and _check_glyphs_file_unsaved_changes(GLYPHS_PATH):
        return jsonify({"error": "Glyphs file has unsaved changes. Please save the file first."}), 409

    try:
        global EDITING_INSTANCES
        EDITING_INSTANCES.discard(instance_name)

        # A full delete (not a demote / csv-only) removes the instance
        # entirely, so drop any grade attached to it. Demote/csv-only keep
        # the instance around, so the grade stays.
        if not csv_only_flag and not source_only_flag:
            try:
                _grade.remove_instance_grade(ORIGINAL_PATH, instance_name)
            except Exception:  # noqa: BLE001
                pass
            try:
                _kinetikern.sidecar.remove_entry(ORIGINAL_PATH, instance_name)
            except Exception:  # noqa: BLE001
                pass
            try:
                _control_axes.remove_instance_values(ORIGINAL_PATH, instance_name)
            except Exception:  # noqa: BLE001
                pass

        # Source-file delete fires for default + source_only. CSV-only
        # skips it (the row was never in the source).
        if touches_source:
            glyphs_deleted = delete_instance_in_glyphs(
                ORIGINAL_PATH, _source_instance_name(instance_name))
            if not glyphs_deleted:
                return jsonify({"error": f"Failed to delete instance '{instance_name}' from source file"}), 500
            # Regenerate the shadow from the now-updated original so the
            # listing stops serving the deleted instance.
            _resolve_active_source()

        # CSV delete fires for default + csv_only. source_only PRESERVES
        # the CSV row — that's the "demote to studio-only" semantic.
        csv_path = _get_preview_csv_path() if not source_only_flag else None
        if csv_path and csv_path.exists():
            try:
                import csv
                # Read CSV
                rows = []
                fieldnames = []
                with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    fieldnames = list(reader.fieldnames) if reader.fieldnames else []
                    for row in reader:
                        # Skip the instance being deleted
                        if row.get("Instance Name", "").strip() != instance_name:
                            rows.append(row)
                
                # Write updated CSV (without deleted instance)
                _csv_io.backup_sidecar(csv_path)
                with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                
                # Update modification time cache
                _update_csv_modification_time(csv_path)
                print(f"Removed instance '{instance_name}' from preview CSV", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not remove instance from preview CSV: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
        trigger_build()

        if csv_only_flag:
            msg = f"Deleted instance '{instance_name}' from CSV (source untouched)"
        elif source_only_flag:
            msg = f"Demoted instance '{instance_name}' — removed from source, CSV row kept"
        else:
            msg = f"Deleted instance '{instance_name}' from source file and CSV"
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/instance/<instance_name>/editing', methods=['DELETE'])
def unregister_editing_instance(instance_name: str):
    """Unregister an instance from editing (allow sync)."""
    global EDITING_INSTANCES
    EDITING_INSTANCES.discard(instance_name)
    return jsonify({"success": True, "message": f"Instance '{instance_name}' unregistered from editing"})

@app.route('/api/avar2/axis', methods=['POST'])
def add_avar2_axis():
    """Add a new traditional axis to the CSV."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404
        
        # Check for external edits
        if _check_csv_external_edit(csv_path):
            return jsonify({
                "error": "The studio's saved data changed outside this window. Please reload.",
                "reload_required": True
            }), 409
        
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        axis_name = data.get("axis_name")  # CSV column header (e.g., "WGHT")
        display_name = data.get("display_name", axis_name)  # Display name (e.g., "Weight")
        registered_tag = data.get("registered_tag", "").lower()  # OpenType tag (e.g., "wght")
        default_value = data.get("default_value", 0)
        min_value = data.get("min", -1000)
        max_value = data.get("max", 1000)
        
        # Validate inputs
        if not axis_name:
            return jsonify({"error": "axis_name is required"}), 400
        
        is_valid, error_msg = _validate_axis_tag(registered_tag)
        if not is_valid:
            return jsonify({"error": f"Invalid registered tag: {error_msg}"}), 400
        
        # Check if tag exists in Glyphs file (parametric axes) - cannot add traditional axis with same tag
        glyphs_axis_tags = _get_glyphs_axis_tags()
        if registered_tag.lower() in glyphs_axis_tags:
            return jsonify({
                "error": f"Registered tag '{registered_tag}' already exists in Glyphs file as a parametric axis. Traditional axes cannot use tags that exist in the Glyphs file."
            }), 400
        
        if min_value < -1000 or min_value > 1000:
            return jsonify({"error": "min must be between -1000 and 1000"}), 400
        if max_value < -1000 or max_value > 1000:
            return jsonify({"error": "max must be between -1000 and 1000"}), 400
        if min_value >= max_value:
            return jsonify({"error": "min must be less than max"}), 400
        
        # Normalized up front: the duplicate-tag check below compares against
        # it, and it used to be assigned only after that loop — a
        # use-before-assignment that stayed hidden until the font had axis
        # metadata for the loop to iterate over.
        axis_name_normalized = axis_name.upper()

        # Check for duplicate registered tags in existing metadata
        metadata = _load_axis_metadata()
        for existing_axis, existing_meta in metadata.items():
            if existing_meta.get("registered_tag") == registered_tag and existing_axis != axis_name_normalized:
                return jsonify({"error": f"Registered tag '{registered_tag}' already used by axis '{existing_axis}'"}), 400
        
        # Read CSV using normalized function
        
        rows, fieldnames, _, _, fieldname_mapping = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)

        # Check if axis already exists (case-insensitive)
        if axis_name_normalized in fieldnames:
            return jsonify({"error": f"Axis '{axis_name}' already exists"}), 400
        
        # Add new column to CSV (use normalized name). Existing rows get
        # a BLANK cell, not the axis default: the designer hasn't
        # authored a mapping value for them yet, and a stamped number
        # becomes real data — it feeds the avar2 in: locations and drags
        # the built axis's range/default to wherever the stamp landed
        # (the "opsz slider goes to 0" bug). Blank renders as "—" in the
        # UI and is omitted from the mapping until assigned.
        fieldnames.append(axis_name_normalized)
        for row in rows:
            row[axis_name_normalized] = ""
        
        # Write updated CSV (use normalized fieldnames)
        import csv
        _csv_io.backup_sidecar(csv_path)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        _update_csv_modification_time(csv_path)
        
        # Save metadata (use normalized name)
        # Check if this is a parametric axis (shouldn't happen, but be safe)
        is_parametric = False
        glyphs_axis_tags = _get_glyphs_axis_tags()
        if registered_tag.lower() in glyphs_axis_tags:
            is_parametric = True
        
        metadata[axis_name_normalized] = {
            "display_name": display_name,
            "registered_tag": registered_tag,
            "is_parametric": is_parametric,
            "min": min_value,
            # The declared default was previously dropped on the floor —
            # the modal collects it, so record it. (It feeds the axis
            # metadata consumers; the CSV backfill deliberately does NOT
            # use it, see above.)
            "default": default_value,
            "max": max_value
        }
        _save_axis_metadata(metadata)
        
        return jsonify({
            "success": True,
            "axis_name": axis_name_normalized,
            "metadata": metadata[axis_name_normalized]
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/avar2/axis/<axis_name>', methods=['PUT'])
def update_avar2_axis(axis_name: str):
    """Update axis metadata. Cannot edit axes that exist in Glyphs file (parametric axes)."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404
        
        # Check for external edits
        if _check_csv_external_edit(csv_path):
            return jsonify({
                "error": "The studio's saved data changed outside this window. Please reload.",
                "reload_required": True
            }), 409
        
        # Check if this axis exists in Glyphs file (parametric axis) - cannot edit
        
        _, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
        
        # Normalize axis_name to uppercase for lookup
        axis_name_normalized = axis_name.upper()
        
        # If axis is in parametric axes (exists in Glyphs file), cannot edit
        if axis_name_normalized in out_cols:
            return jsonify({
                "error": f"Cannot edit axis '{axis_name}' - it exists in the Glyphs file as a parametric axis. Parametric axes are managed in the Glyphs file, not in avar2 mappings."
            }), 403
        
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        new_display_name = data.get("display_name")
        new_registered_tag = data.get("registered_tag", "").lower()
        new_min = data.get("min")
        new_max = data.get("max")
        
        # Normalize axis_name to uppercase for lookup
        axis_name_normalized = axis_name.upper()
        
        # Load metadata
        metadata = _load_axis_metadata()
        if axis_name_normalized not in metadata:
            return jsonify({"error": f"Axis '{axis_name}' not found in metadata"}), 404
        
        # Validate registered tag if provided
        if new_registered_tag:
            is_valid, error_msg = _validate_axis_tag(new_registered_tag)
            if not is_valid:
                return jsonify({"error": f"Invalid registered tag: {error_msg}"}), 400
            
            # Check if tag exists in Glyphs file (parametric axes) - cannot use same tag
            glyphs_axis_tags = _get_glyphs_axis_tags()
            if new_registered_tag.lower() in glyphs_axis_tags:
                return jsonify({
                    "error": f"Registered tag '{new_registered_tag}' already exists in Glyphs file as a parametric axis. Traditional axes cannot use tags that exist in the Glyphs file."
                }), 400
            
            # Check for duplicate tags in existing metadata
            for existing_axis, existing_meta in metadata.items():
                if existing_axis != axis_name_normalized and existing_meta.get("registered_tag") == new_registered_tag:
                    return jsonify({"error": f"Registered tag '{new_registered_tag}' already used by axis '{existing_axis}'"}), 400
        
        # Validate min/max if provided
        if new_min is not None:
            if new_min < -1000 or new_min > 1000:
                return jsonify({"error": "min must be between -1000 and 1000"}), 400
        if new_max is not None:
            if new_max < -1000 or new_max > 1000:
                return jsonify({"error": "max must be between -1000 and 1000"}), 400
        if new_min is not None and new_max is not None and new_min >= new_max:
            return jsonify({"error": "min must be less than max"}), 400
        new_default = data.get("default")
        if new_default is not None and new_min is not None and new_max is not None:
            if not (new_min <= new_default <= new_max):
                return jsonify({"error": "default must be between min and max"}), 400
        
        # Read CSV
        import csv
        rows = []
        fieldnames = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            for row in reader:
                rows.append(row)
        
        if axis_name not in fieldnames:
            return jsonify({"error": f"Axis '{axis_name}' not found in the studio data"}), 404
        
        # Update metadata (use normalized name)
        if axis_name_normalized not in metadata:
            # Create metadata entry if it doesn't exist
            metadata[axis_name_normalized] = {
                "display_name": axis_name_normalized,
                "registered_tag": _csv_io.normalize_in_axis_name(axis_name_normalized),
                "min": -1000,
                "max": 1000
            }
        
        current_meta = metadata[axis_name_normalized]
        if new_display_name:
            current_meta["display_name"] = new_display_name
        if new_registered_tag:
            old_tag = current_meta.get("registered_tag")
            current_meta["registered_tag"] = new_registered_tag
        if new_min is not None:
            current_meta["min"] = new_min
        if new_max is not None:
            current_meta["max"] = new_max
        if new_default is not None:
            # Compiles into the built axis via the gen-avar2 shim's
            # declared ranges — where the axis RESTS, and the one
            # location avar2 cannot remap.
            current_meta["default"] = new_default
        
        # Ensure is_parametric flag is preserved (don't allow changing it via edit)
        # It should already be set correctly from initialization
        
        # If axis_name was different case, update metadata key
        if axis_name_normalized != axis_name and axis_name in metadata:
            del metadata[axis_name]
        
        metadata[axis_name_normalized] = current_meta
        _save_axis_metadata(metadata)
        
        # Write CSV back (use normalized fieldnames)
        import csv
        _csv_io.backup_sidecar(csv_path)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        _update_csv_modification_time(csv_path)
        
        return jsonify({
            "success": True,
            "axis_name": axis_name_normalized,
            "metadata": current_meta
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/avar2/axis/<axis_name>', methods=['DELETE'])
def delete_avar2_axis(axis_name: str):
    """Delete a mapping axis: drop its CSV column and its metadata
    entry, then rebuild (the axis leaves the compiled fvar). Parametric
    (out) columns are refused — they're managed in the source file.

    The static demo has had this since the mapping-axis arc (e2e §25);
    the desktop server simply never grew the endpoint, so the delete
    affordance was dead here."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404
        if _check_csv_external_edit(csv_path):
            return jsonify({
                "error": "The studio's saved data changed outside this window. Please reload.",
                "reload_required": True
            }), 409

        _, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
        axis_name_normalized = axis_name.upper()
        if axis_name_normalized in out_cols:
            return jsonify({
                "error": f"Cannot delete axis '{axis_name}' - it exists in the Glyphs "
                         "file as a parametric axis. Parametric axes are managed in "
                         "the Glyphs file, not in avar2 mappings."
            }), 403

        import csv
        rows = []
        fieldnames = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            rows = list(reader)
        matches = [c for c in fieldnames if c.upper() == axis_name_normalized]
        if not matches:
            return jsonify({"error": f"Axis '{axis_name}' not found in the studio data"}), 404

        kept = [c for c in fieldnames if c.upper() != axis_name_normalized]
        _csv_io.backup_sidecar(csv_path)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=kept)
            writer.writeheader()
            writer.writerows([{k: v for k, v in row.items() if k in kept} for row in rows])
        _update_csv_modification_time(csv_path)

        metadata = _load_axis_metadata()
        if axis_name_normalized in metadata:
            del metadata[axis_name_normalized]
            _save_axis_metadata(metadata)

        # The axis set changed — the compiled fvar must follow.
        build_ok = False
        try:
            build_ok = trigger_build()
        except Exception as build_exc:  # noqa: BLE001
            print(f"Warning: rebuild after axis delete failed: {build_exc}", file=sys.stderr)
        return jsonify({"success": True, "deleted": axis_name_normalized,
                        "build_ok": bool(build_ok)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/avar2/mapping/<instance_name>/<axis_name>', methods=['PUT'])
def update_avar2_mapping(instance_name: str, axis_name: str):
    """Update a single cell value in the CSV."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404
        
        # Check for external edits
        if _check_csv_external_edit(csv_path):
            return jsonify({
                "error": "The studio's saved data changed outside this window. Please reload.",
                "reload_required": True
            }), 409
        
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        new_value = data.get("value")
        if new_value is None:
            return jsonify({"error": "value is required"}), 400
        
        # Validate value is numeric
        try:
            float_value = float(new_value)
        except (ValueError, TypeError):
            return jsonify({"error": "value must be a number"}), 400
        
        # Normalize axis_name to uppercase for lookup
        axis_name_normalized = axis_name.upper()
        
        # Load metadata to check range
        metadata = _load_axis_metadata()
        if axis_name_normalized in metadata:
            axis_meta = metadata[axis_name_normalized]
            min_val = axis_meta.get("min", -1000)
            max_val = axis_meta.get("max", 1000)
            if float_value < min_val or float_value > max_val:
                return jsonify({
                    "error": f"Value {float_value} is outside allowed range [{min_val}, {max_val}]"
                }), 400
        
        # Read CSV using normalized function
        
        rows, fieldnames, _, _, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
        
        if axis_name_normalized not in fieldnames:
            return jsonify({"error": f"Axis '{axis_name}' not found in the studio data"}), 404
        
        # Find and update the instance
        instance_found = False
        for row in rows:
            if row.get("Instance Name", "").strip() == instance_name:
                row[axis_name_normalized] = str(float_value)
                instance_found = True
                break
        
        if not instance_found:
            return jsonify({"error": f"Instance '{instance_name}' not found in the studio"}), 404
        
        # Write updated CSV (use normalized fieldnames)
        import csv
        _csv_io.backup_sidecar(csv_path)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        _update_csv_modification_time(csv_path)
        
        return jsonify({
            "success": True,
            "instance_name": instance_name,
            "axis_name": axis_name_normalized,
            "value": float_value
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _record_build_failure(result: Dict) -> Dict:
    """Stamp the global last-build status as failed and return the dict."""
    global LAST_BUILD_STATUS, LAST_BUILD_ERROR
    LAST_BUILD_STATUS = "failed"
    msg = result.get("error") or "build failed"
    detail = result.get("details")
    LAST_BUILD_ERROR = f"{msg}: {detail}" if detail else msg
    return result


def _builder_env(default_overrides: Optional[Dict[str, float]] = None) -> Dict[str, str]:
    """Environment for the gftools-builder subprocess.

    Resolves compilers against THIS python env first (ninja steps call
    gftools-* scripts from PATH), with build/_shims/ in front so our
    patched gftools-gen-avar2 shadows the venv console script (fvar
    axis dedup + declared ranges + export rebasing — see the shim).

    Also hands the studio's declared axis ranges to the shim: created
    axes (opsz/wght…) get their declared min/DEFAULT/max instead of
    upstream's range-from-in-values with default=min — default=min
    makes the lowest-mapped instance the un-remappable avar2 origin.
    ``default_overrides`` ({fvar tag: value}) replaces specific axes'
    DEFAULT — the export-with-default build uses it to make the
    requested combination the compiled origin.
    """
    build_env = os.environ.copy()
    shims_dir = Path(__file__).parent / "build" / "_shims"
    build_env["PATH"] = (
        str(shims_dir)
        + os.pathsep
        + str(Path(sys.executable).parent)
        + os.pathsep
        + build_env.get("PATH", "")
    )
    _ranges = _declared_axis_ranges(default_overrides)
    if _ranges:
        build_env["AVAR2_STUDIO_AXIS_RANGES"] = json.dumps(_ranges)
    return build_env


def _declared_axis_ranges(default_overrides: Optional[Dict[str, float]] = None) -> Dict[str, list]:
    """{fvar tag: [min, default, max]} for the studio's CREATED axes.

    Upstream gen-avar2 derives a created axis's range purely from the
    mapping's in: values and sets default=min, which makes the lowest-mapped
    instance the un-remappable origin of the space. These declared triples
    put the default where the designer said it is. Shared by the subprocess
    shim (via env) and the in-process build (directly).
    """
    key = _declared_ranges_cache_key(default_overrides)
    cached = _DECLARED_RANGES_CACHE.get(key)
    if cached is not None:
        return {t: list(v) for t, v in cached.items()}
    try:
        _meta = _load_axis_metadata() or {}
    except Exception:  # noqa: BLE001
        return {}
    _ranges: Dict[str, list] = {}
    for _col, _m in _meta.items():
        _tag = (_m.get("registered_tag") or "").strip()
        if not _tag or _m.get("is_parametric"):
            continue
        _lo, _hi = _m.get("min"), _m.get("max")
        if _lo is None or _hi is None:
            continue
        # Same fallback chain as /api/avar2/axes: explicit metadata default,
        # else the registered-axis convention (wght 400, opsz 72, …), else the
        # minimum — clamped into range so the triple is never rejected.
        _d = _m.get("default")
        if _d is None:
            _d = TRADITIONAL_AXIS_DEFAULTS.get(_tag.lower(), _lo)
        _d = max(float(_lo), min(float(_hi), float(_d)))
        _ranges[_tag] = [float(_lo), _d, float(_hi)]
    if default_overrides:
        for _tag, _val in default_overrides.items():
            if _tag in _ranges:
                _lo, _, _hi = _ranges[_tag]
                _ranges[_tag] = [_lo, max(_lo, min(_hi, float(_val))), _hi]
    _DECLARED_RANGES_CACHE.clear()
    _DECLARED_RANGES_CACHE[key] = _ranges
    return _ranges


# One computation per input version: a single build consults
# _declared_axis_ranges from three sites (the shim env via _builder_env,
# the default-row detection and the fvar-axes fix in _build_in_process),
# each of which used to re-read axis-metadata.json + re-parse the source.
_DECLARED_RANGES_CACHE: Dict[tuple, Dict[str, list]] = {}


def _declared_ranges_cache_key(default_overrides) -> tuple:
    parts = []
    for p in (_get_avar2_metadata_path(), GLYPHS_PATH):
        try:
            st = Path(p).stat()
            parts.append((str(p), st.st_mtime, st.st_size))
        except (OSError, TypeError):
            parts.append(None)
    ov = tuple(sorted((str(k), float(v)) for k, v in (default_overrides or {}).items()))
    return (tuple(parts), ov)


def _build_in_process(config_path: Path, workdir: Path, fontc_path: str,
                      default_overrides: Optional[Dict[str, float]] = None) -> Path:
    """The gftools-builder pipeline, without gftools-builder.

    Profiling a rebuild put fontc at 0.06s and ``gftools builder`` at 9.5s:
    after the compile, ninja spawned four separate Python processes (fix,
    STAT, avar2, fvar instances), each paying interpreter start-up and a
    fontTools import, serially. None of that was compilation. This runs the
    same four library functions the CLIs wrap, in one process, and hands back
    the font gftools would have written — same name, same tables — so
    everything downstream (transform chain, promotion to VARIABLE_FONT_PATH)
    is untouched.

    Fidelity, step by step, each mirroring the CLI it replaces:
      - fontc gets the flags gftools rewrites from the recipe's fontmake
        filters (flatten components, decompose transformed components).
      - fix_font returns a deepcopy; it is saved and re-read so every later
        step sees exactly what the CLI chain would have loaded from disk.
      - gen_avar2's gen_fvar_axes is patched with the same fix build/_shims
        applies: drop in: axes the font already has (the upstream membership
        bug that duplicated every parametric axis) and give created axes their
        declared min/default/max.
      - Per-font sections are passed as LISTS, not filename-keyed dicts, so
        nothing reaches for TTFont.reader.file.name.
    """
    import yaml
    from fontTools.ttLib import TTFont as _TTFont
    from gftools.fix import fix_font
    from gftools.stat import gen_stat_tables_from_config
    from gftools.scripts import gen_avar2 as _gen_avar2
    from gftools.scripts.gen_fvar_instances import gen_fvar_instances

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources = config.get("sources") or []
    if not sources:
        raise RuntimeError("config has no sources")
    source = Path(sources[0])
    if not source.is_absolute():
        source = (config_path.parent / source).resolve()

    # The target filename is the key every per-font section shares. gftools
    # derives it from family name + source axes; reading it off the config is
    # exact by construction and avoids re-implementing the naming.
    keys = [k for k in (config.get("avar2") or {}) if "SC[" not in k] \
        or [k for k in (config.get("stat") or {}) if "SC[" not in k]
    if not keys:
        raise RuntimeError("config has no avar2/stat entry to name the font from")
    name = keys[0]
    out_dir = workdir.parent / "fonts" / "variable"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name

    # 0.5. Default-origin master. A mapping row whose in: sits entirely
    # at the user-axis defaults declares the intended DEFAULT appearance
    # — and avar2 cannot remap the default location (fontTools'
    # _add_avar silently used such a row as the model BASE, subtracting
    # its out from every other row: the long-misdiagnosed understated-
    # table bug). The fix is at the COMPILE level: interpolate a real
    # master at that row's out location (the export path's machinery),
    # stage a designspace resting on it, and compile THAT — the default
    # row then lands as a true identity and gvar stays natural-size (an
    # earlier binary instancer rebase was exact too, but split every
    # region around the new origin: measured 57KB → 495KB on Crispy).
    avar2_cfg_early = (config.get("avar2") or {}).get(name)
    compile_source = source
    default_rows = []
    if avar2_cfg_early:
        declared_for_defaults = _declared_axis_ranges(default_overrides)

        def _is_default_row(m):
            ins = m.get("in") or {}
            if not ins:
                return False
            for tag, v in ins.items():
                trip = declared_for_defaults.get(tag)
                if not trip:
                    return False
                try:
                    if abs(float(v) - float(trip[1])) > 1e-6:
                        return False
                except (TypeError, ValueError):
                    return False
            return True

        default_rows = [m for m in avar2_cfg_early if _is_default_row(m)]
        # Loud warning for the "resting dip": rows map user-axis styles
        # but none sits at the axis defaults, so nothing anchors the
        # resting state — the font rests at the compiled origin, and the
        # default location pops toward the nearest mapped stop the
        # moment a slider moves. Only meaningful when declared defaults
        # exist and at least one row maps a declared axis.
        global LAST_ORIGIN_WARNING
        LAST_ORIGIN_WARNING = None
        maps_declared_axis = any(
            any(tag in declared_for_defaults for tag in (m.get("in") or {}))
            for m in avar2_cfg_early)
        if declared_for_defaults and maps_declared_axis and not default_rows:
            defaults_desc = ", ".join(
                f"{tag} {trip[1]:g}" for tag, trip in sorted(declared_for_defaults.items()))
            LAST_ORIGIN_WARNING = (
                f"No instance row sits at the axis defaults ({defaults_desc}) — the "
                "font rests at the compiled origin, not at any authored style. Add a "
                "row at the defaults (it becomes the ORIGIN row), or move an axis "
                "default onto a mapped stop.")
        if len(default_rows) > 1:
            # The config generator's effective-location dedupe normally
            # collapses these (keeping the first, warning by instance
            # name); this belt covers hand-edited configs — two rows
            # both claiming the resting state must fail loudly, not
            # silently pick one.
            raise RuntimeError(
                "multiple mapping rows sit at the axis defaults — only one may "
                "define the resting state; their out locations: "
                + "; ".join(str(m.get("out")) for m in default_rows))
        # METHOD NOTE — why the instancer rebase (step 3.5) and not an
        # inserted master: relocating the default to an interior point
        # while preserving the font's rendering EVERYWHERE requires
        # splitting the corner-anchored gvar regions around the new
        # origin — that ~7x tuple growth (495KB vs 57KB on Crispy) is
        # the intrinsic cost, not an inefficiency. Both "insert one
        # interpolated master and recompile" routes were built and
        # MEASURED: exact at master corners (0.0 units) but the
        # re-solved model pins the field through the new center and
        # deviates by hundreds of units BETWEEN masters (468u at an
        # interior corner, 223u at an authored style row on the
        # CrispyMini fixture) — it is a subtly different font, which
        # would silently shift every calibrated style. The earlier
        # designspace attempt's "lost YOPQ deltas" was this same math,
        # not a glyphsLib conversion bug.

    # 1. compile — the only step that was ever fast
    cmd = [fontc_path, "-o", str(out), str(compile_source),
           "--flatten-components", "--decompose-transformed-components"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(workdir))
    if res.returncode != 0 or not out.exists():
        raise RuntimeError("fontc failed: " + (res.stderr or res.stdout or "no output")[-1000:])

    # 2. fix  (gftools-fix-font -o out in)
    font = _TTFont(str(out))
    font = fix_font(font)
    font.save(str(out)); font.close()

    # 3. STAT  (gftools-gen-stat --inplace --src <stat section>)
    stat_cfg = (config.get("stat") or {}).get(name)
    if stat_cfg:
        font = _TTFont(str(out))
        gen_stat_tables_from_config(stat_cfg, [font])
        font.save(str(out)); font.close()

    # 3.5. Default relocation (see the method NOTE in step 0.5): move
    # the parametric fvar defaults to the default row's out location on
    # the avar-free font — the row then compiles as a true identity in
    # step 4 and every other row anchors to the real resting state.
    #
    # ON by default. The 2026-10 experiment that turned this off for the
    # working font (the ~7x gvar growth compounds with rounding/slant
    # twins; 9.1MB preview font) ran into harder physics: every avar2
    # tent is anchored to zero at the INPUT default, so with the ORIGIN
    # row excluded the whole NEIGHBORHOOD of the default input collapses
    # toward the compiled corner — not a point dip but a valley across
    # the default cross (observed: wght 399 mapped to XTRA 92 instead of
    # ~663). A preview font that misrenders near its resting weight is
    # worse than a large one. Shipped fonts stay small either way: the
    # export's "set default" uses the interpolated-origin-master method
    # (~0.67MB measured). AVAR2_RELOCATE_BUILD_DEFAULT=0 restores the
    # small-but-valleyed working font if ever wanted.
    relocate_at_build = os.environ.get(
        "AVAR2_RELOCATE_BUILD_DEFAULT", "1").lower() in ("1", "true", "yes")
    if relocate_at_build and avar2_cfg_early and default_rows:
        from fontTools.varLib import instancer as _instancer
        font = _TTFont(str(out))
        fvar_by_tag = {a.axisTag: a for a in font["fvar"].axes}
        limits = {}
        for tag, v in (default_rows[0].get("out") or {}).items():
            ax = fvar_by_tag.get(tag)
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if ax is None or abs(v - ax.defaultValue) < 1e-6:
                continue
            if not (ax.minValue <= v <= ax.maxValue):
                raise RuntimeError(
                    f"default-location mapping row puts {tag} at {v}, outside "
                    f"its range [{ax.minValue}, {ax.maxValue}]")
            limits[tag] = (ax.minValue, v, ax.maxValue)
        if limits:
            _instancer.instantiateVariableFont(font, limits, inplace=True,
                                               updateFontNames=False)
            font.save(str(out))
        font.close()

    # 4. avar2  (gftools-gen-avar2 --inplace in <avar2 section>), shim applied
    avar2_cfg = avar2_cfg_early
    if not relocate_at_build and default_rows:
        # Without the relocation, the default-location row CANNOT be
        # expressed: its input is the all-default location, which the
        # VarStore treats as the base master and silently discards —
        # and every other row's delta then anchors to it, skewing EVERY
        # mapped stop by the discarded amount (measured: wght 900 landed
        # 616/201/121 units short of its authored location). Drop the
        # row from the preview table so the remaining stops are exact;
        # the resting state becomes the compiled origin, and the export
        # path's "set default" still rests shipped fonts on this row.
        dropped = {id(m) for m in default_rows}
        avar2_cfg = [m for m in avar2_cfg_early if id(m) not in dropped]
        print(f"  avar2: default-location row(s) excluded from the preview "
              f"table (build-time relocation off); mapped stops stay exact, "
              f"the preview rests at the compiled origin", file=sys.stderr)
        # (global LAST_ORIGIN_WARNING is declared earlier in this function.)
        LAST_ORIGIN_WARNING = (
            "Build-time default relocation is off: the preview font rests at "
            "the compiled origin, not at the ORIGIN row's style. Every mapped "
            "stop is exact; use the export's \u201cset default\u201d to ship a font "
            "resting on the ORIGIN row.")
    if avar2_cfg:
        declared = _declared_axis_ranges(default_overrides)
        _orig = _gen_avar2.gen_fvar_axes

        def _fixed_gen_fvar_axes(font, mapping):
            existing = {str(a.axisTag) for a in font["fvar"].axes}
            filtered = [{**m, "in": {k: v for k, v in (m.get("in") or {}).items() if k not in existing}}
                        for m in mapping]
            result = _orig(font, filtered)
            for a in font["fvar"].axes:
                tag = str(a.axisTag)
                if tag in existing or tag not in declared:
                    continue
                try:
                    lo, d, hi = (float(v) for v in declared[tag])
                except (TypeError, ValueError):
                    continue
                if lo <= d <= hi:
                    a.minValue, a.defaultValue, a.maxValue = lo, d, hi
            return result

        _gen_avar2.gen_fvar_axes = _fixed_gen_fvar_axes
        try:
            font = _TTFont(str(out))
            _gen_avar2.gen_avar2_mapping(font, avar2_cfg)
            font.save(str(out)); font.close()
        finally:
            _gen_avar2.gen_fvar_axes = _orig

    # 5. fvar instances  (gftools-gen-fvar-instances --inplace in <section>)
    # gen_fvar_instances print()s a "missing axis, using default" line per
    # instance per axis. The subprocess swallowed those; in-process they
    # would flood the server log on every rebuild, so swallow them here too.
    inst_cfg = (config.get("fvarInstances") or {}).get(name)
    if inst_cfg:
        import contextlib
        import io
        font = _TTFont(str(out))
        with contextlib.redirect_stdout(io.StringIO()):
            gen_fvar_instances(font, inst_cfg)
        font.save(str(out)); font.close()

    return out


def _perform_avar2_build() -> Dict:
    """Build the avar2 variable font in-process.

    Returns a dict with ``success: bool`` and either ``font_path`` (on success)
    or ``error`` + ``details`` (on failure). Designed to be called by both the
    Flask endpoint and the watchdog-triggered rebuild path, so build-on-save
    doesn't have to round-trip through HTTP.

    On failure ``VARIABLE_FONT_PATH`` is left untouched, so ``/api/font`` keeps
    serving the last-good font and the UI can show a stale-build banner driven
    by ``LAST_BUILD_STATUS`` in ``/api/health``.

    Sets the BUILDING flag for its lifetime; raises nothing — all failures
    come back as a dict so callers can surface them however they want.
    """
    global BUILDING, VARIABLE_FONT_PATH, LAST_BUILD_TIME
    global LAST_BUILD_STATUS, LAST_BUILD_ERROR

    if BUILDING:
        return {"success": False, "error": "Build already in progress"}

    preview_csv = _get_preview_csv_path()
    if not preview_csv or not preview_csv.exists():
        return _record_build_failure({"success": False, "error": "Preview CSV not found"})

    avar2_font_dir = _get_avar2_font_dir()
    if not avar2_font_dir:
        return _record_build_failure({"success": False, "error": "Could not create avar2 font directory"})

    BUILDING = True
    try:
        preview_config_path = _get_preview_config_path()
        if not preview_config_path or not preview_config_path.exists():
            return _record_build_failure({"success": False, "error": "Preview config not found", "details": str(preview_config_path)})
        config_to_update = preview_config_path

        workdir = _get_preview_dir()
        try:
            from .build import config_generator
        except ImportError as e:
            # Should never happen in a normal install — fall through to
            # the caller's plain-VF fallback instead of letting the
            # ImportError escape and break the whole startup build.
            return _record_build_failure({
                "success": False,
                "error": "avar2 config_generator module not importable",
                "details": str(e),
            })
        try:
            config_generator.update_config(csv_path=preview_csv, config_path=config_to_update, backup=False, source_path=GLYPHS_PATH)
        except Exception as e:
            return _record_build_failure({"success": False, "error": "Failed to update config", "details": str(e)})

        build_env = _builder_env()

        fontc_path = shutil.which("fontc", path=build_env["PATH"])
        if not fontc_path:
            return _record_build_failure({"success": False, "error": "fontc not found in PATH"})

        # Same steps gftools-builder would run, in this process. The subprocess
        # builder is kept behind AVAR2_STUDIO_GFTOOLS_BUILDER=1 as a safety valve
        # for a build the in-process path gets wrong — set it and compare.
        if os.environ.get("AVAR2_STUDIO_GFTOOLS_BUILDER") == "1":
            builder_cmd = ["gftools", "builder", "--experimental-fontc", fontc_path, str(config_to_update.resolve())]
            result = subprocess.run(builder_cmd, capture_output=True, text=True, cwd=str(workdir), env=build_env)
            if result.returncode != 0:
                return _record_build_failure({
                    "success": False,
                    "error": "Font build failed",
                    # TAIL, not head: builder stderr opens with pages of
                    # glyphsLib INFO noise; the actual error is at the end.
                    "details": (result.stderr or result.stdout or "No error details")[-1000:],
                })
        else:
            try:
                _build_in_process(config_to_update, workdir, fontc_path)
            except Exception as e:  # noqa: BLE001
                return _record_build_failure({
                    "success": False,
                    "error": "Font build failed",
                    "details": str(e)[-1000:],
                })

            class _R:  # the post-build code reads .stdout for its error text
                stdout = ""
            result = _R()
        project_fonts_dir = workdir.parent / "fonts" / "variable"
        produced = sorted(project_fonts_dir.glob("*.ttf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not produced:
            return _record_build_failure({
                "success": False,
                "error": "Built font not found",
                "details": f"No .ttf in {project_fonts_dir}. gftools output: {(result.stdout or '')[:500]}",
            })
        built_font = produced[0]

        avar2_font_dir.mkdir(parents=True, exist_ok=True)
        font_file = avar2_font_dir / built_font.name
        shutil.move(str(built_font), str(font_file))

        # Sanity: a compiled font must never carry duplicate fvar tags.
        # gen-avar2's axis-append bug produced exactly that (each
        # parametric axis twice → deltas apply twice → glyphs render
        # wide, looks like a phantom spacing transform). Fail the avar2
        # build loudly so the plain-VF fallback serves instead of a
        # corrupted font.
        try:
            _tags = [a.axisTag for a in TTFont(str(font_file))["fvar"].axes]
        except Exception:
            _tags = []
        _dupes = sorted({t for t in _tags if _tags.count(t) > 1})
        if _dupes:
            return _record_build_failure({
                "success": False,
                "error": "avar2 build produced duplicate fvar axes",
                "details": f"duplicated tags: {', '.join(_dupes)} — "
                           "gen-avar2 appended in: axes that already exist "
                           "(shim missing or bypassed?)",
            })

        try:
            if project_fonts_dir.exists() and not any(project_fonts_dir.iterdir()):
                project_fonts_dir.rmdir()
            parent_fonts = project_fonts_dir.parent
            if parent_fonts.exists() and not any(parent_fonts.iterdir()):
                parent_fonts.rmdir()
        except OSError:
            pass

        VARIABLE_FONT_PATH = _apply_transform_chain(font_file)
        LAST_BUILD_TIME = time.time()
        LAST_BUILD_STATUS = "ok"
        LAST_BUILD_ERROR = None
        return {"success": True, "font_path": str(VARIABLE_FONT_PATH)}

    finally:
        BUILDING = False


@app.route('/api/avar2-font', methods=['GET'])
def get_avar2_font():
    """Serve the avar2 font file."""
    avar2_font_dir = _get_avar2_font_dir()
    if not avar2_font_dir or not avar2_font_dir.exists():
        return jsonify({"error": "Avar2 font directory not found"}), 404

    # Look for the avar2 font — name comes from the active config's fvarInstances key
    built_filename = _get_avar2_built_font_filename()
    if not built_filename:
        return jsonify({"error": "Could not determine built font filename"}), 500
    font_file = avar2_font_dir / built_filename

    if not font_file.exists():
        # Tolerate the case where the project workdir has a single avar2 TTF
        # with a different axis order than the config would suggest.
        candidates = list(avar2_font_dir.glob("*.ttf"))
        if len(candidates) == 1:
            font_file = candidates[0]
        else:
            return jsonify({"error": "Avar2 font not built yet"}), 404
    
    return send_file(
        str(font_file),
        mimetype="font/ttf",
        as_attachment=False,
        download_name=font_file.name
    )


# ---------------------------------------------------------------------------
# Built-in example fixtures + late-binding source loader
# ---------------------------------------------------------------------------

# Repo layout: src/avar2_studio/server.py → repo root is parent.parent.parent.
# When the package is installed from PyPI the examples directory will NOT be
# present; the endpoint reports an empty list in that case and the frontend
# hides the built-in examples section.
_REPO_ROOT_FOR_EXAMPLES = Path(__file__).resolve().parent.parent.parent
_BUILTIN_EXAMPLES = [
    {
        "id": "roboto-delta-mini",
        "name": "Roboto Delta Mini",
        "subtitle": "Case-split parametric axes (XOUC/YOUC/XTUC, XOLC/YOLC/XTLC, XOFI/YOFI/XTFI)",
        "source_rel": "examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace",
    },
    {
        "id": "crispy-mini",
        "name": "Crispy Mini",
        "subtitle": "Unified parametric axes (XTRA/XOPQ/YOPQ)",
        "source_rel": "examples/crispy-mini/sources/CrispyMini.glyphs",
    },
]


def _builtin_example_source(example_id: str) -> Optional[Path]:
    """Resolve an example's bundled source path, if it's reachable from
    the running install (dev checkout). Returns None for PyPI installs
    where the examples directory isn't shipped."""
    for ex in _BUILTIN_EXAMPLES:
        if ex["id"] == example_id:
            candidate = _REPO_ROOT_FOR_EXAMPLES / ex["source_rel"]
            return candidate if candidate.exists() else None
    return None


def _staged_workspace_for(example_id: str) -> Path:
    """Per-example staging dir under ~/.avar2-studio/workspace/. The
    studio writes its sibling CSV + build artifacts into this copy so
    the shipped fixture stays clean (and git-clean) across loads."""
    return Path.home() / ".avar2-studio" / "workspace" / example_id


def _stage_builtin_example(example_id: str) -> Optional[Path]:
    """Copy a built-in example's source tree into the workspace if it
    isn't already there, then return the staged source-file path.
    Re-stages keep the user's prior edits because we leave the
    workspace alone if it exists."""
    src = _builtin_example_source(example_id)
    if src is None:
        return None
    workspace = _staged_workspace_for(example_id)
    if not workspace.exists():
        # Copy the whole sources/ directory so UFOs travel with the .designspace.
        import shutil
        sources_dir = src.parent
        workspace.mkdir(parents=True, exist_ok=True)
        for child in sources_dir.iterdir():
            dest = workspace / child.name
            if child.is_dir():
                shutil.copytree(child, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(child, dest)
    return workspace / src.name


def _apply_source_path(path: Path) -> None:
    """Point the running server at ``path`` as the active source. Mutates
    the GLYPHS_PATH / SOURCE_FORMAT / BUILD_DIR / CSV_PATH globals,
    re-bootstraps the CSV + config sidecars, and kicks off an
    auto-build. Raises ValueError / UnsupportedSourceFormat on bad input
    so callers (CLI + /api/load-source) can surface the failure."""
    global GLYPHS_PATH, ORIGINAL_PATH, SOURCE_FORMAT, BUILD_DIR, CSV_PATH, VARIABLE_FONT_PATH
    global PREVIEW_DIR, PREVIEW_CSV_PATH, PREVIEW_CONFIG_PATH
    global LAST_BUILD_TIME, LAST_BUILD_STATUS, LAST_BUILD_ERROR
    global EDITING_INSTANCES

    resolved = path.resolve()
    if not resolved.exists():
        raise ValueError(f"source file not found: {resolved}")
    if resolved.suffix.lower() == ".ufo":
        raise ValueError(
            "avar2-studio requires a .designspace, not an individual UFO master. "
            "Point it at the sibling .designspace file."
        )

    SOURCE_FORMAT = _source_font.detect_format(resolved)
    ORIGINAL_PATH = resolved
    # GLYPHS_PATH currently always equals ORIGINAL_PATH — slice 2 keeps
    # the build pipeline pointed at the original. The shadow .glyphs is
    # still generated (so slice 3+ can consume it for brace-layer
    # authoring) but until those layers exist the shadow and the
    # original compile to identical fvars (fontc drops axes with no
    # deltas). Slice 3 will conditionally swap to the shadow.
    GLYPHS_PATH = resolved

    workdir = GLYPHS_PATH.parent / ".avar2-studio"
    BUILD_DIR = workdir / "build"
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    # Reset every state global that's keyed to the previous source —
    # otherwise PREVIEW_DIR/PREVIEW_CSV_PATH/PREVIEW_CONFIG_PATH stay
    # pinned to the first load and every subsequent ``/api/avar2/*``
    # call reads the OLD font's mapping CSV + axis metadata. That's
    # how Roboto Delta's XOUC/YOUC/XTUC columns kept showing up after
    # the user swapped to Crispy.
    VARIABLE_FONT_PATH = None
    CSV_PATH = None
    PREVIEW_DIR = None
    PREVIEW_CSV_PATH = None
    PREVIEW_CONFIG_PATH = None
    LAST_BUILD_TIME = None
    LAST_BUILD_STATUS = None
    LAST_BUILD_ERROR = None
    EDITING_INSTANCES = set()

    # Stop any Fontra subprocess pointed at the previous source's
    # shadow. It'll be re-spawned on demand when the user opens a
    # control axis in the editor on the new source.
    _stop_fontra()

    _initialize_preview_csv_from_glyphs()
    _initialize_preview_config_from_glyphs()

    # Re-establish the active build source from the current control-axis AND
    # grade state — a shadow of the original carrying brace layers and/or grade
    # braces, or the original itself. Runs AFTER the CSV init because grade reads
    # per-instance parametric coords from it. This is what makes grade (and
    # authored brace layers) survive a restart: before, only a feature EDIT
    # re-applied them, so a plain reload dropped the GRAD axis (and brace deltas)
    # from the built font until the designer re-saved.
    try:
        _resolve_active_source()
    except Exception as exc:
        print(f"Warning: failed to resolve active build source on load: {exc}", file=sys.stderr)

    # A new source invalidates every queued/running job: cancel them and
    # start clean.
    JOBS.cancel_all()

    print(f"Auto-building font from {GLYPHS_PATH}...", file=sys.stderr)
    try:
        trigger_build()
    except Exception as exc:
        # Non-fatal — the frontend can retry via /api/build.
        print(f"⚠ Auto-build failed: {exc}", file=sys.stderr)


@app.route('/api/examples', methods=['GET'])
def list_examples():
    """List the bundled example fixtures that are reachable from this
    install. PyPI installs won't ship them — the endpoint returns
    ``[]`` in that case so the frontend can hide the section."""
    out = []
    for ex in _BUILTIN_EXAMPLES:
        if _builtin_example_source(ex["id"]) is not None:
            out.append({
                "id": ex["id"],
                "name": ex["name"],
                "subtitle": ex["subtitle"],
            })
    return jsonify({"examples": out})


def _load_project_zip(upload):
    """Load a whole project from a .zip archive: locate the source
    (.designspace with its UFOs, or a .glyphs) anywhere in the
    archive's folder structure, harvest the studio files sitting next
    to it (mappings, secondary axes, transforms, axis metadata), and
    stage everything into the font's per-upload workspace so the
    designer continues where they left off. Existing workspace studio
    data is preserved unless the archive carries a replacement —
    which is backed up first, same as every sidecar write."""
    import shutil
    import tempfile
    import zipfile
    import re as _re

    tmp = Path(tempfile.mkdtemp(prefix="avar2-upload-"))
    try:
        zip_path = tmp / "upload.zip"
        upload.save(str(zip_path))
        with zipfile.ZipFile(zip_path) as zf:
            for m in zf.infolist():
                p = Path(m.filename)
                if p.is_absolute() or ".." in p.parts:
                    raise ValueError(f"Unsafe path in archive: {m.filename}")
            zf.extractall(tmp / "x")
        root = tmp / "x"

        def _candidates(suffix):
            # .avar2-studio is tool-managed — its shadow copies would
            # otherwise read as second sources.
            return [
                p for p in sorted(root.rglob(f"*{suffix}"))
                if "__MACOSX" not in p.parts
                and ".avar2-studio" not in p.parts
                and not p.name.startswith("._")
            ]

        ds, gl = _candidates(".designspace"), _candidates(".glyphs")
        if len(ds) == 1:
            source = ds[0]
        elif not ds and len(gl) == 1:
            source = gl[0]
        else:
            found = f"{len(ds)} .designspace and {len(gl)} .glyphs"
            raise ValueError(
                f"The archive must contain exactly one project "
                f"(one .designspace or one .glyphs) — found {found}."
            )

        # A designspace must travel with its UFOs.
        if source.suffix.lower() == ".designspace":
            from fontTools.designspaceLib import DesignSpaceDocument
            doc = DesignSpaceDocument.fromfile(str(source))
            missing = [
                s.filename or s.path for s in doc.sources
                if not (source.parent / (s.filename or "")).exists()
                and not (s.path and Path(s.path).exists())
            ]
            if missing:
                raise ValueError(
                    "The archive's .designspace references sources that "
                    f"aren't in it: {', '.join(str(m) for m in missing[:4])}. "
                    "Zip the whole project folder so the UFOs travel too."
                )

        stem = source.stem
        slug = _re.sub(r'[^A-Za-z0-9._-]+', '_', stem) or "font"
        workspace = Path.home() / ".avar2-studio" / "workspace" / "uploads" / slug
        workspace.mkdir(parents=True, exist_ok=True)
        src_dir = source.parent
        attached = []

        # Archive the outgoing source file before replacing it.
        source_dest = workspace / source.name
        if source_dest.exists():
            archive = workspace / ".avar2-studio" / "archive"
            archive.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.move(str(source_dest), str(archive / f"{stamp}-{source.name}"))
            for old in sorted(archive.glob(f"*-{source.name}"))[:-5]:
                old.unlink()

        for item in sorted(src_dir.iterdir()):
            name = item.name
            if name.startswith("._"):
                continue
            if item.is_dir():
                if name == ".avar2-studio":
                    # Take the axis metadata; build/shadow/archive are
                    # regenerable or history, never imported.
                    meta = item / "axis-metadata.json"
                    if meta.exists():
                        dest = workspace / ".avar2-studio" / "axis-metadata.json"
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        _csv_io.backup_sidecar(dest)
                        shutil.copy2(meta, dest)
                        attached.append("axis metadata")
                elif name.lower().endswith(".ufo"):
                    dest = workspace / name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                # other directories: not part of a project we understand
                continue
            lower = name.lower()
            if name == source.name:
                shutil.copy2(item, source_dest)
            elif lower.endswith("-avar.csv"):
                dest = workspace / f"{stem}-avar.csv"
                _csv_io.backup_sidecar(dest)
                shutil.copy2(item, dest)
                attached.append("avar2 mappings")
            elif lower.endswith("-control.json"):
                dest = workspace / f"{stem}-control.json"
                _csv_io.backup_sidecar(dest)
                shutil.copy2(item, dest)
                attached.append("secondary parametric axes")
            elif lower.endswith("-transforms.json"):
                dest = workspace / f"{stem}-transforms.json"
                _csv_io.backup_sidecar(dest)
                shutil.copy2(item, dest)
                attached.append("transforms")
            elif lower == "avar2-axis-metadata.json" or lower.endswith("-axis-metadata.json"):
                dest = workspace / ".avar2-studio" / "axis-metadata.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                _csv_io.backup_sidecar(dest)
                shutil.copy2(item, dest)
                attached.append("axis metadata")
            else:
                # Plain project files (feature files, notes) ride along.
                shutil.copy2(item, workspace / name)

        _apply_source_path(source_dest)
        return jsonify({
            "success": True,
            "path": str(source_dest),
            "attached": sorted(set(attached)),
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.route('/api/load-source', methods=['POST'])
def load_source():
    """Swap the active source at runtime. Two modes:
       - JSON body ``{example: <id>}`` loads a built-in fixture from
         the staged workspace under ~/.avar2-studio/workspace/.
       - multipart/form-data: pick one .glyphs as the main source
         plus any combination of sibling files: ``*-avar.csv`` (avar2
         mapping table) and ``avar2-axis-metadata.json`` (axis ranges
         / display names). Anything unrecognised is rejected with 400.
    Returns the new active path on success."""
    try:
        # Multipart upload branch. Each font gets its OWN workspace dir
        # keyed by basename: re-uploading the same font replaces just
        # the .glyphs (the outgoing copy is archived) and PRESERVES the
        # studio sidecars — mappings CSV, axis metadata, transforms,
        # control axes. The old shared "uploaded/" slot wiped everything
        # on every upload, which destroyed a designer's authored studio
        # data the first time they re-uploaded an edited source.
        uploaded = list(request.files.values())
        if uploaded:
            import re as _re
            import shutil

            # A .zip is a whole project archive — source + UFOs +
            # studio files discovered inside its folder structure.
            zips = [
                f for f in uploaded
                if (f.filename or '').lower().endswith('.zip')
            ]
            if zips:
                if len(uploaded) > 1:
                    return jsonify({
                        "error": "Upload the project .zip by itself."
                    }), 400
                return _load_project_zip(zips[0])

            glyphs_name: Optional[str] = None
            staged: list = []
            extras_skipped: list = []
            for f in uploaded:
                # Client-supplied filename: strip any directory components
                # before it becomes a path under the workspace.
                name = secure_filename((f.filename or '').strip())
                if not name:
                    continue
                lower = name.lower()
                if lower.endswith('.glyphs'):
                    if glyphs_name is not None:
                        return jsonify({"error": "Upload one .glyphs file at a time (got multiple)."}), 400
                    glyphs_name = name
                staged.append((f, name, lower))

            if glyphs_name is None:
                return jsonify({
                    "error": "No source in the upload. Pick a .glyphs file, or "
                             "a .zip of your project folder (required for "
                             ".designspace so its UFOs travel too)."
                }), 400

            slug = _re.sub(r'[^A-Za-z0-9._-]+', '_', Path(glyphs_name).stem) or "font"
            workspace = Path.home() / ".avar2-studio" / "workspace" / "uploads" / slug

            # One-time migration from the legacy shared slot: if this
            # same font was last authored in workspace/uploaded/, carry
            # its sidecars into the per-font home instead of orphaning
            # them.
            legacy = Path.home() / ".avar2-studio" / "workspace" / "uploaded"
            if not workspace.exists() and (legacy / glyphs_name).exists():
                workspace.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy), str(workspace))
                print(f"Migrated legacy upload workspace → {workspace}", file=sys.stderr)

            workspace.mkdir(parents=True, exist_ok=True)
            glyphs_dest = workspace / glyphs_name

            # Archive the outgoing source instead of overwriting it —
            # bounded undo for every future "wait, the old version had
            # it" moment.
            if glyphs_dest.exists():
                archive = workspace / ".avar2-studio" / "archive"
                archive.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                shutil.move(str(glyphs_dest), str(archive / f"{stamp}-{glyphs_name}"))
                for old in sorted(archive.glob(f"*-{glyphs_name}"))[:-5]:
                    old.unlink()

            csv_dest: Optional[Path] = None
            metadata_dest: Optional[Path] = None
            for f, name, lower in staged:
                if lower.endswith('.glyphs'):
                    f.save(str(glyphs_dest))
                elif lower.endswith('-avar.csv') or lower == 'avar2-mappings.csv':
                    # Explicitly uploaded CSV replaces the project's —
                    # after a safety backup of what it replaces.
                    csv_dest = workspace / f"{Path(glyphs_name).stem}-avar.csv"
                    _csv_io.backup_sidecar(csv_dest)
                    f.save(str(csv_dest))
                elif lower == 'avar2-axis-metadata.json' or lower.endswith('-axis-metadata.json'):
                    workdir = workspace / ".avar2-studio"
                    workdir.mkdir(parents=True, exist_ok=True)
                    metadata_dest = workdir / "axis-metadata.json"
                    _csv_io.backup_sidecar(metadata_dest)
                    f.save(str(metadata_dest))
                else:
                    extras_skipped.append(name)

            _apply_source_path(glyphs_dest)
            return jsonify({
                "success": True,
                "path": str(glyphs_dest),
                "csv_attached": str(csv_dest) if csv_dest else None,
                "metadata_attached": str(metadata_dest) if metadata_dest else None,
                "ignored_files": extras_skipped,
            })

        # Built-in example branch.
        data = request.get_json(silent=True) or {}
        example_id = data.get('example')
        if not example_id:
            return jsonify({"error": "Provide either an uploaded 'file' or JSON {example: <id>}."}), 400
        staged = _stage_builtin_example(example_id)
        if staged is None:
            return jsonify({
                "error": f"Built-in example '{example_id}' isn't available in this install. "
                         "Examples ship with the dev checkout only."
            }), 404
        _apply_source_path(staged)
        return jsonify({"success": True, "path": str(staged), "example": example_id})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"Error in /api/load-source: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


def main():
    global GLYPHS_PATH, SOURCE_FORMAT, BUILD_DIR, CSV_PATH, OBSERVER

    parser = argparse.ArgumentParser(description="avar2-studio server")
    parser.add_argument(
        "glyphs",
        type=Path,
        nargs="?",
        default=None,
        help="Path to the source file (.glyphs or .designspace), positional."
    )
    parser.add_argument(
        "--glyphs",
        dest="glyphs_flag",
        type=Path,
        default=None,
        help="Path to the source file (alternative to the positional form)."
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=None,
        help="Override the build output directory (default: .avar2-studio/build/ next to the .glyphs file)."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5001,
        help="Port to run server on (default: 5001, avoiding macOS AirPlay on 5000)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Path to the avar2 mappings CSV (default: <stem>-avar.csv beside the source)"
    )
    parser.add_argument(
        "--no-fontc",
        action="store_true",
        help="Disable fontc and use fontmake only"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Flask in debug mode (auto-reload on source change). "
             "Off by default because the reloader can kill in-flight "
             "gftools subprocesses."
    )
    
    args = parser.parse_args()

    # --csv / --build-dir are dead: the worker builds a staged snapshot
    # (sibling sidecars, job-dir output), so these overrides change nothing
    # the user can see. Fail loudly instead of pretending.
    if args.csv:
        print("Error: --csv is no longer supported — the studio reads "
              "<stem>-avar.csv next to the source file.", file=sys.stderr)
        sys.exit(2)
    if args.build_dir:
        print("Error: --build-dir is no longer supported — builds write to "
              ".avar2-studio/build/ next to the source file.", file=sys.stderr)
        sys.exit(2)

    global USE_FONTC
    USE_FONTC = not args.no_fontc  # Use fontc unless --no-fontc is specified

    glyphs_arg = args.glyphs or args.glyphs_flag
    if glyphs_arg:
        # Path-provided launch: apply immediately. CLI errors bubble up
        # as sys.exit(1) so the script's exit code stays meaningful.
        try:
            resolved = glyphs_arg.resolve()
            if not resolved.exists():
                print(f"Error: source file not found: {resolved}", file=sys.stderr)
                sys.exit(1)
            _apply_source_path(resolved)
        except (ValueError, UnsupportedSourceFormat) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        # Blind launch: no source loaded yet. The frontend will offer
        # the Load-Font dropdown (built-in examples or .glyphs upload)
        # which POSTs to /api/load-source. All read endpoints return
        # graceful empties while GLYPHS_PATH is None.
        print(
            "No source file provided — launching blind. "
            "Use the Load Font dropdown in the UI to pick an example or upload a .glyphs file.",
            file=sys.stderr,
        )
    
    print(f"Starting server on {args.host}:{args.port}", file=sys.stderr)
    print(f"Compiler: {'fontc (with fontmake fallback)' if USE_FONTC else 'fontmake only'}", file=sys.stderr)
    if GLYPHS_PATH:
        print(f"Glyphs file: {GLYPHS_PATH}", file=sys.stderr)
        print(f"Build directory: {BUILD_DIR}", file=sys.stderr)
        if CSV_PATH:
            print(f"CSV file: {CSV_PATH}", file=sys.stderr)
        else:
            csv_path = _get_avar2_csv_path()
            if csv_path:
                print(f"CSV file (auto-detected): {csv_path}", file=sys.stderr)
            else:
                print(f"CSV file: not found (avar2 endpoints will be unavailable)", file=sys.stderr)
    
    preview_csv = _get_preview_csv_path()
    if preview_csv:
        print(f"Preview CSV: {preview_csv}", file=sys.stderr)
    preview_config = _get_preview_config_path()
    if preview_config:
        print(f"Preview config: {preview_config}", file=sys.stderr)
    
    # Set up real-time file watching using watchdog
    def sync_csv_with_glyphs():
        """Sync CSV with the source file, skipping instances being edited."""
        global EDITING_INSTANCES

        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return

        try:
            ok = _csv_io.update_csv_from_glyphs(
                GLYPHS_PATH,
                csv_path,
                skip_instances=set(EDITING_INSTANCES),
                exclude_axes=_non_mapping_axis_tags(),
            )
            if ok:
                # OUR write, not an external one. Without this the
                # external-edit guard sees the new mtime on the next save and
                # refuses it — and since every secondary-axis layer edit
                # regenerates the shadow, which trips this watcher, that made
                # "changed outside this window" fire on essentially every
                # avar2 save.
                _update_csv_modification_time(csv_path)
                skipped_msg = (
                    f" (skipped {len(EDITING_INSTANCES)} editing instances)"
                    if EDITING_INSTANCES else ""
                )
                print(f"CSV synced with source file{skipped_msg}", file=sys.stderr)
            else:
                print("Warning: CSV sync returned no-op", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not sync CSV: {e}", file=sys.stderr)
    
    global _SYNC_CSV_HOOK
    _SYNC_CSV_HOOK = sync_csv_with_glyphs

    class GlyphsFileHandler(FileSystemEventHandler):
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
            """Handle file modification events."""
            if event.is_directory:
                return

            # Relevant changes: the active build source (sync + rebuild),
            # the designer's ORIGINAL (GLYPHS_PATH is the SHADOW once a
            # secondary axis has layers — comparing only against it meant
            # edits to the real source never fired: deleting an instance in
            # Glyphs left the shadow stale), the shadow itself (Fontra saves
            # for studio axes — a separate class because GLYPHS_PATH is the
            # transformed/ copy when source transforms are on), and — for an
            # active .designspace shadow — .glif edits inside its UFOs.
            src = Path(event.src_path)
            event_kind = _classify_watched_path(src)
            if event_kind is None:
                return
            is_source = event_kind == "source"
            is_original = event_kind == "original"
            is_shadow = event_kind == "shadow"
            is_glif = event_kind == "glif"

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
            if is_original:
                if current_time < _SUPPRESS_ORIGINAL_UNTIL or (
                        src_mtime is not None
                        and src_mtime <= _SELF_WRITE_ORIGINAL_WATERMARK):
                    return
            elif current_time < _SUPPRESS_WATCHDOG_UNTIL or (
                    src_mtime is not None and src_mtime <= _SELF_WRITE_WATERMARK):
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

            global VARIABLE_FONT_PATH, LAST_BUILD_TIME

            if JOBS.is_building():
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
                            print(f"Warning: could not capture outlines: {exc}", file=sys.stderr)
                    # Re-resolve NOW so the read endpoints (/api/instances,
                    # /api/axes, coverage) stop serving the pre-save shadow
                    # while the current build is still running; the queue
                    # re-resolves before staging the follow-up regardless.
                    _resolve_source_after_save(src)
                    JOBS.request_build(source_refresh=kind)
                else:
                    JOBS.request_build()
                print("Source changed mid-build; queued for after this build.", file=sys.stderr)
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
                    print("\nOriginal source modified, regenerating shadow...", file=sys.stderr)
                    _resolve_source_after_save(src)
                    sync_csv_with_glyphs()
                elif is_source or is_shadow:
                    print(f"\nSource file modified, syncing CSV and rebuilding...", file=sys.stderr)
                    # A Fontra save writes the SHADOW: pull hand-drawn brace
                    # outlines into the sidecar first, so drawings are durable
                    # even if the shadow is later regenerated away. Untouched
                    # seeds are skipped by their stamps — a cheap no-op after
                    # server-side regens.
                    try:
                        captured = _capture_shadow_drawings()
                        if captured:
                            print(f"Captured {captured} drawn outline(s) into the sidecar.", file=sys.stderr)
                    except Exception as exc:
                        print(f"Warning: could not capture outlines: {exc}", file=sys.stderr)
                    # Sync CSV first (skips editing instances)
                    sync_csv_with_glyphs()
                else:
                    print(f"\nUFO glyph edited (Fontra), rebuilding...", file=sys.stderr)
                trigger_build()
            except Exception as e:
                print(f"Error handling file change: {e}", file=sys.stderr)
    
    # Set up file watcher if watchdog is available AND we have a source
    # loaded. Blind launches skip watching — the source will be loaded
    # via /api/load-source, but a runtime-swappable file watcher is
    # left for a future iteration. The frontend's polling tick covers
    # the no-watcher case acceptably.
    if WATCHDOG_AVAILABLE and GLYPHS_PATH is not None:
        event_handler = GlyphsFileHandler()
        observer = Observer()
        # .designspace shadows need recursion: Fontra writes .glif
        # files inside the pooled UFO directories.
        observer.schedule(
            event_handler,
            path=str(GLYPHS_PATH.parent),
            recursive=GLYPHS_PATH.suffix.lower() == ".designspace",
        )
        # Watch the ORIGINAL's directory too when the build source is the
        # shadow — they live in different directories, and edits to the real
        # file are the ones the designer actually makes.
        try:
            if ORIGINAL_PATH is not None:
                orig_dir = Path(ORIGINAL_PATH).parent.resolve()
                if orig_dir != GLYPHS_PATH.parent.resolve():
                    observer.schedule(event_handler, path=str(orig_dir), recursive=False)
        except Exception as exc:
            print(f"Warning: could not watch the original source: {exc}", file=sys.stderr)
        # And the shadow directory itself when the active source is the
        # transformed/ copy (source transforms on): studio-axis Fontra
        # sessions save shadow/<name>, which the GLYPHS_PATH watch —
        # transformed/ only — never sees (T0.9). When transforms are off
        # GLYPHS_PATH IS the shadow and its dir is already watched.
        try:
            if (ORIGINAL_PATH is not None
                    and Path(GLYPHS_PATH).resolve() != Path(ORIGINAL_PATH).resolve()):
                shadow_dir = _control_axes.shadow_dir_for(ORIGINAL_PATH)
                if shadow_dir.exists() \
                        and shadow_dir.resolve() != Path(GLYPHS_PATH).parent.resolve():
                    observer.schedule(event_handler, path=str(shadow_dir), recursive=False)
        except Exception as exc:
            print(f"Warning: could not watch the shadow dir: {exc}", file=sys.stderr)
        observer.start()
        OBSERVER = observer
        print(f"Real-time file watching enabled: watching {GLYPHS_PATH}", file=sys.stderr)
    elif GLYPHS_PATH is None:
        pass  # blind launch — watcher attaches when a source is loaded later (TODO)
    else:
        print(f"Warning: watchdog not available, falling back to periodic checking", file=sys.stderr)
        # Fallback to periodic checking
        PERIODIC_CHECK_INTERVAL = 15
        def check_and_rebuild_periodically():
            global VARIABLE_FONT_PATH, LAST_BUILD_TIME
            if JOBS.is_building():
                return
            try:
                if GLYPHS_PATH is None or not GLYPHS_PATH.exists():
                    return
                current_mtime = GLYPHS_PATH.stat().st_mtime
                if LAST_BUILD_TIME is None or current_mtime > LAST_BUILD_TIME:
                    sync_csv_with_glyphs()
                    trigger_build()
            except Exception as e:
                print(f"Error in periodic check: {e}", file=sys.stderr)
        
        def start_periodic_checker():
            def periodic_loop():
                while True:
                    time.sleep(PERIODIC_CHECK_INTERVAL)
                    check_and_rebuild_periodically()
            checker_thread = threading.Thread(target=periodic_loop, daemon=True)
            checker_thread.start()
            print(f"Periodic file checking enabled: checking every {PERIODIC_CHECK_INTERVAL} seconds", file=sys.stderr)
        
        start_periodic_checker()
    
    # Debug mode is off by default. Flask's reloader watches files and
    # restarts the process on change — fine during dev, but it can kill
    # a running gftools subprocess (the avar2 build's intermediate files
    # touch the watched tree). Re-enable with --debug or AVAR2_STUDIO_DEBUG=1
    # if you're hacking on the server.
    debug = args.debug or os.environ.get("AVAR2_STUDIO_DEBUG", "").lower() in ("1", "true", "yes")
    try:
        app.run(host=args.host, port=args.port, debug=debug)
    finally:
        if OBSERVER:
            OBSERVER.stop()
            OBSERVER.join()


if __name__ == "__main__":
    main()
