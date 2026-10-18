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
import functools
import importlib.resources
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from string import Template
from typing import Dict, List, Optional

from .studio_log import get_logger

log = get_logger(__name__)
try:
    import yaml
except ImportError:
    log.warning("Warning: PyYAML not found. Install with: pip install pyyaml")
    yaml = None

from flask import Flask, jsonify, request
from flask_cors import CORS
from fontTools.ttLib import TTFont

try:
    from glyphsLib import GSFont  # noqa: F401 — the import itself is the
except ImportError:               # guard: fail early and legibly on the CLI
    log.error("Error: glyphsLib not found. Install with: pip install glyphsLib")
    sys.exit(1)

from . import build_jobs as _build_jobs
from . import control_axes as _control_axes
from . import csv_io as _csv_io
from . import instance_aliases as _instance_aliases
from . import sidecar_io as _sidecar_io
from . import source_font as _source_font
from . import state
from . import transforms as _transforms
from .pipeline import (
    _PLACEHOLDER_MAX as _PLACEHOLDER_MAX,
)
from .pipeline import (
    _PLACEHOLDER_MIN as _PLACEHOLDER_MIN,
)
from .pipeline import (
    TRADITIONAL_AXIS_DEFAULTS as TRADITIONAL_AXIS_DEFAULTS,
)

# The build/export pipeline lives in pipeline.py (Flask-free, so the build
# worker can import it). These back-imports keep this module's bare-name
# call sites — and the tests patching ``server.X`` — working unchanged.
from .pipeline import (
    _apply_source_path,
    _get_avar2_font_dir,
    _get_preview_config_path,
    _get_preview_csv_path,
    _resolve_active_source,
    _stop_fontra,
    _suppress_watcher,
)
from .pipeline import (
    _derive_traditional_range as _derive_traditional_range,
)
from .pipeline import (
    _evaluate_mapped_location as _evaluate_mapped_location,
)
from .pipeline import (
    _get_preview_dir as _get_preview_dir,
)
from .pipeline import (
    _grade_instance_coords as _grade_instance_coords,
)
from .pipeline import (
    _load_axis_metadata as _load_axis_metadata,
)
from .pipeline import (
    _repair_placeholder_ranges as _repair_placeholder_ranges,
)
from .pipeline import (
    _save_axis_metadata as _save_axis_metadata,
)
from .source_font import UnsupportedSourceFormat

app = Flask(__name__, static_folder=None)
# ``static_folder=None`` disables Flask's default ``/static/<path>``
# handler. Without that, Flask's auto-registered ``static`` endpoint
# shadows our custom ``/static/<path>`` route (routes/ui.py) and the CRA
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

# Mutable runtime state lives in state.py (imported as ``state``); this
# module keeps the constants, locks and the job-queue wiring.

@functools.lru_cache(maxsize=None)
def _read_asset(relative: str) -> str:
    """Text of a file shipped inside the avar2_studio package (the Fontra
    shim, its focus stylesheet, the Glyphs.app AppleScripts)."""
    return importlib.resources.files("avar2_studio").joinpath(relative).read_text(encoding="utf-8")

# Serializes read-modify-write on the control-axis sidecar so two concurrent
# edits can't interleave and drop each other's layers.
SIDECAR_LOCK = _sidecar_io.LOCK


def _sidecar_locked(fn):
    """Run ``fn`` under the sidecar lock: every handler that reads a
    sidecar, mutates it and writes it back must hold the lock for the
    whole cycle, or two concurrent writers drop each other's change."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with SIDECAR_LOCK:
            return fn(*args, **kwargs)
    return wrapper
# Debounced shadow-regen + rebuild. Layer edits arrive in bursts (clicking ✕ a
# few times, adding several glyphs); regenerating the shadow and recompiling
# per edit costs seconds each and would queue up. Coalesce a burst into one job.
_REBUILD_TIMER_LOCK = threading.Lock()
_REBUILD_DEBOUNCE_SECONDS = 0.4    # sized for a ~1s in-process build, not the old 9s one

# The build/export job queue: all heavy pipeline work runs in worker
# SUBPROCESSES (docs/archive/WORKER_BUILD_PLAN.md) so a build can never starve
# request threads. trigger_build submits; builds coalesce through it.
# Bound to this module for its methods (_resolve_active_source, the
# preview-dir getters — both imported back from pipeline.py); the
# stage-time paths and result state it reads and writes (ORIGINAL_PATH,
# VARIABLE_FONT_PATH, LAST_*) live in state.py. pipeline.py reaches the
# queue through state.JOBS (the same object) so it never imports server.
JOBS = _build_jobs.JobQueue(sys.modules[__name__])
state.JOBS = JOBS


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

        applescript = Template(_read_asset("glyphsapp/document_modified.applescript")).substitute(abs_path=abs_path)

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
        log.warning(f"Warning: Could not check Glyphs file unsaved changes: {e}")
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
                applescript = Template(_read_asset("glyphsapp/reload_document.applescript")).substitute(abs_path=abs_path)

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
                    log.info("Re-saved changes after Glyphs.app save")
                elif result.returncode == 0:
                    # If no font object provided, reload from disk and save
                    # This ensures our changes are preserved
                    time.sleep(0.3)
                    from glyphsLib import load
                    font = load(str(glyphs_path))
                    font.save(str(glyphs_path))
                    log.info("Re-saved changes after Glyphs.app save")

            except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as e:
                # AppleScript failed - log but don't fail
                log.warning(f"Warning: Could not force reload Glyphs document: {e}")
    except Exception as e:
        # Silently fail - file save already succeeded, this is just a notification
        log.warning(f"Warning: Could not force reload Glyphs document: {e}")
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
        log.error(f"Error reading source file: {e}")
        raise


def get_axes_from_glyphs(glyphs_path: Path) -> List[Dict]:
    """Read axes from the source file (any supported format).

    Returns a list of ``{tag, name, min, max, default}`` dicts.
    """
    try:
        font, _fmt = _source_font.load_source_cached(glyphs_path)
        return _source_font.get_axes(font)
    except Exception as e:
        log.error(f"Error reading axes from source file: {e}")
        raise


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
        log.error(f"Error renaming instance in source file: {e}")
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
        log.error(f"Error deleting instance from source file: {e}")
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
        aliases = _instance_aliases.load(state.ORIGINAL_PATH) if state.ORIGINAL_PATH else {}
    except Exception:
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
        log.error(f"Error updating source file: {e}")
        import traceback
        traceback.print_exc()
        return False


def _run_shadow_regen_and_build():
    """Re-resolve the active build source (control + grade shadow) and rebuild.
    Runs on the debounce timer — never call directly from a request."""
    with _REBUILD_TIMER_LOCK:
        state._REBUILD_TIMER = None
    try:
        if state.ORIGINAL_PATH is None:
            return
        _resolve_active_source()
        try:
            trigger_build()
        except Exception as exc:
            log.warning(f"Warning: rebuild after control-axis change failed: {exc}")
    finally:
        with _REBUILD_TIMER_LOCK:
            state.BACKGROUND_WORK -= 1


def schedule_shadow_rebuild():
    """Queue a debounced shadow regen + rebuild, coalescing a burst of layer
    edits into ONE job. Returns immediately so the request can answer straight
    away; /api/health reports building=true until the job finishes."""
    with _REBUILD_TIMER_LOCK:
        if state._REBUILD_TIMER is not None:
            # Already queued — push it out; the pending job still counts as work.
            state._REBUILD_TIMER.cancel()
        else:
            state.BACKGROUND_WORK += 1
        state._REBUILD_TIMER = threading.Timer(_REBUILD_DEBOUNCE_SECONDS, _run_shadow_regen_and_build)
        state._REBUILD_TIMER.daemon = True
        state._REBUILD_TIMER.start()


def trigger_build():
    """Submit a build job, coalescing concurrent requests.

    Callers fire this from independent requests (e.g. clicking ✕ on several
    brace layers in a row). The job queue runs one worker at a time and
    re-stages from the current sidecars for every pass, so a request that
    lands mid-build is never dropped: the next pass builds exactly the
    state the sidecars hold then.
    """
    return JOBS.request_build()


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
                log.warning(f"Warning: failed to read built-font filename from {config_path}: {e}")
    if state.GLYPHS_PATH:
        family = state.GLYPHS_PATH.stem
        try:
            axes = get_axes_from_glyphs(state.GLYPHS_PATH)
            tags = sorted(a["tag"] for a in axes if a.get("tag"))
        except Exception:
            tags = []
        return f"{family}[{','.join(tags)}].ttf" if tags else f"{family}-VF.ttf"
    return None


def _is_build_stale() -> bool:
    """CSV modified after the last build ⇒ the served font's avar2
    table no longer reflects the mappings. Cheap mtime compare."""
    if state.LAST_BUILD_TIME is None:
        return False
    try:
        csv_path = _get_avar2_csv_path() or _get_preview_csv_path()
        if not csv_path or not csv_path.exists():
            return False
        return csv_path.stat().st_mtime > state.LAST_BUILD_TIME
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
    if state.LAST_BUILD_STATUS != "failed":
        return None
    path = request.path
    if path in _BUILD_GATE_ALLOW_EXACT or path.endswith(_BUILD_GATE_ALLOW_SUFFIX):
        return None
    if not path.startswith("/api/") and not path.startswith("/fontra"):
        return None
    return jsonify({
        "error": "The source file does not compile, so the studio is paused.",
        "detail": state.LAST_BUILD_ERROR,
        "build_blocked": True,
        "hint": "Fix the source and save it (the studio rebuilds on save), or press Rebuild.",
    }), 409


# ============================================================================
# Avar2 API Endpoints
# ============================================================================


def _get_avar2_csv_path() -> Optional[Path]:
    """Get path to avar2-mappings.csv (relative to Glyphs file or explicit)."""
    # For preview tool: use preview CSV if it exists (fresh start workflow)
    preview_csv = _get_preview_csv_path()
    if preview_csv and preview_csv.exists():
        return preview_csv

    # Fallback to explicit CSV path if set
    if state.CSV_PATH:
        return state.CSV_PATH

    # Default: look for avar2-mappings.csv in same directory as Glyphs file
    if state.GLYPHS_PATH:
        default_csv = state.GLYPHS_PATH.parent / "avar2-mappings.csv"
        if default_csv.exists():
            return default_csv

    return None


def _secondary_axis_tags() -> set:
    """Tags the studio injects into the shadow (secondary/control axes).

    They exist in the shadow font but are NOT avar2 mapping columns, so the
    CSV sync must never create columns for them — see exclude_axes in
    csv_io.update_csv_from_glyphs.
    """
    try:
        if state.ORIGINAL_PATH is None:
            return set()
        return {
            str(ax.get("tag") or "").upper()
            for ax in (_control_axes.list_axes(state.ORIGINAL_PATH) or [])
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
    compile as real fvar axes (see
    docs/decisions/0008-per-instance-control-values-in-sidecar.md)."""
    tags = set(_secondary_axis_tags())
    tags.add("GRAD")
    try:
        _transforms.discover()
        for t in _transforms.REGISTRY.values():
            tag = getattr(t.spec, "injected_axis_tag", None)
            if tag:
                tags.add(str(tag).upper())
    except Exception:
        pass
    return tags


def _check_csv_external_edit(csv_path: Path) -> bool:
    """Check if CSV was modified externally. Returns True if modified externally."""
    csv_str = str(csv_path)
    current_mtime = csv_path.stat().st_mtime if csv_path.exists() else 0

    if csv_str in state._CSV_MODIFICATION_TIMES:
        if current_mtime > state._CSV_MODIFICATION_TIMES[csv_str]:
            return True

    state._CSV_MODIFICATION_TIMES[csv_str] = current_mtime
    return False


def _update_csv_modification_time(csv_path: Path) -> None:
    """Update tracked modification time after we write to CSV."""
    csv_str = str(csv_path)
    if csv_path.exists():
        state._CSV_MODIFICATION_TIMES[csv_str] = csv_path.stat().st_mtime


def _get_glyphs_axis_tags() -> set:
    """Get set of axis tags from the source file (source of truth for parametric axes).

    Name kept as ``_get_glyphs_axis_tags`` for git-blame stability; works on
    both .glyphs and .designspace via the source_font dispatcher.
    """
    if not state.GLYPHS_PATH or not state.GLYPHS_PATH.exists():
        return set()

    try:
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
        return {axis["tag"].lower() for axis in _source_font.get_axes(font)}
    except Exception as e:
        log.error(f"Error reading axes from source file: {e}")
        return set()


# ----------------------------------------------------------------------
# Control-axis routes. See docs/guides/secondary-parametric-axes.md.
# ----------------------------------------------------------------------




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
        state.FONTRA_PROCESS is not None
        and state.FONTRA_PROCESS.poll() is None
        and state.FONTRA_CONTENT_ROOT == content_root
        and (current_mtime is None or current_mtime == state.FONTRA_SHADOW_MTIME)
    ):
        return state.FONTRA_PORT

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
        "--http-port", str(state.FONTRA_PORT),
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
                if s.connect_ex(("127.0.0.1", state.FONTRA_PORT)) == 0:
                    break
        except OSError:
            pass
        time.sleep(0.25)
    else:
        _stop_fontra()
        raise RuntimeError(f"fontra didn't bind 127.0.0.1:{state.FONTRA_PORT} within ~5s")

    state.FONTRA_PROCESS = proc
    state.FONTRA_CONTENT_ROOT = content_root
    state.FONTRA_SHADOW_MTIME = current_mtime
    log.info(f"Started Fontra on http://127.0.0.1:{state.FONTRA_PORT} (root={content_root})")
    return state.FONTRA_PORT


# Make sure Fontra is cleaned up when the studio exits.
import atexit as _atexit

_atexit.register(_stop_fontra)


@_sidecar_locked
def sync_csv_with_glyphs():
    """Sync CSV with the source file, skipping instances being edited."""

    csv_path = _get_avar2_csv_path()
    if not csv_path or not csv_path.exists():
        return

    try:
        ok = _csv_io.update_csv_from_glyphs(
            state.GLYPHS_PATH,
            csv_path,
            skip_instances=set(state.EDITING_INSTANCES),
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
                f" (skipped {len(state.EDITING_INSTANCES)} editing instances)"
                if state.EDITING_INSTANCES else ""
            )
            log.info(f"CSV synced with source file{skipped_msg}")
        else:
            log.warning("Warning: CSV sync returned no-op")
    except Exception as e:
        log.warning(f"Warning: Could not sync CSV: {e}")



# Every @app.route handler lives in a blueprint under routes/ (audit §3
# step e). Registered here, after all helper defs: the route modules read
# this module's attributes late-bound (and @_sidecar_locked decorates some
# handlers at import time), so they must load after those names exist.
from . import routes as _routes

_routes.register(app)


def main():

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
        log.error("Error: --csv is no longer supported — the studio reads "
              "<stem>-avar.csv next to the source file.")
        sys.exit(2)
    if args.build_dir:
        log.error("Error: --build-dir is no longer supported — builds write to "
              ".avar2-studio/build/ next to the source file.")
        sys.exit(2)

    state.USE_FONTC = not args.no_fontc  # Use fontc unless --no-fontc is specified

    glyphs_arg = args.glyphs or args.glyphs_flag
    if glyphs_arg:
        # Path-provided launch: apply immediately. CLI errors bubble up
        # as sys.exit(1) so the script's exit code stays meaningful.
        try:
            resolved = glyphs_arg.resolve()
            if not resolved.exists():
                log.error(f"Error: source file not found: {resolved}")
                sys.exit(1)
            _apply_source_path(resolved)
        except (ValueError, UnsupportedSourceFormat) as exc:
            log.error(f"Error: {exc}")
            sys.exit(1)
    else:
        # Blind launch: no source loaded yet. The frontend will offer
        # the Load-Font dropdown (built-in examples or .glyphs upload)
        # which POSTs to /api/load-source. All read endpoints return
        # graceful empties while GLYPHS_PATH is None.
        log.info("No source file provided — launching blind. "
            "Use the Load Font dropdown in the UI to pick an example or upload a .glyphs file.")

    log.info(f"Starting server on {args.host}:{args.port}")
    log.info(f"Compiler: {'fontc (with fontmake fallback)' if state.USE_FONTC else 'fontmake only'}")
    if state.GLYPHS_PATH:
        log.info(f"Glyphs file: {state.GLYPHS_PATH}")
        log.info(f"Build directory: {state.BUILD_DIR}")
        if state.CSV_PATH:
            log.info(f"CSV file: {state.CSV_PATH}")
        else:
            csv_path = _get_avar2_csv_path()
            if csv_path:
                log.info(f"CSV file (auto-detected): {csv_path}")
            else:
                log.info("CSV file: not found (avar2 endpoints will be unavailable)")

    preview_csv = _get_preview_csv_path()
    if preview_csv:
        log.info(f"Preview CSV: {preview_csv}")
    preview_config = _get_preview_config_path()
    if preview_config:
        log.info(f"Preview config: {preview_config}")

    # Set up real-time file watching using watchdog — the lifecycle object
    # lives in watch.py; /api/load-source re-points it via reschedule().
    state._SYNC_CSV_HOOK = sync_csv_with_glyphs

    from . import watch as _watch
    if state.GLYPHS_PATH is None:
        pass  # blind launch — the watcher attaches when a source is loaded
    elif not _watch.WATCHER.start():
        log.warning("Warning: watchdog not available, falling back to periodic checking")
        # Fallback to periodic checking
        PERIODIC_CHECK_INTERVAL = 15
        def check_and_rebuild_periodically():
            if JOBS.is_building():
                return
            try:
                if state.GLYPHS_PATH is None or not state.GLYPHS_PATH.exists():
                    return
                current_mtime = state.GLYPHS_PATH.stat().st_mtime
                if state.LAST_BUILD_TIME is None or current_mtime > state.LAST_BUILD_TIME:
                    sync_csv_with_glyphs()
                    trigger_build()
            except Exception as e:
                log.error(f"Error in periodic check: {e}")

        def start_periodic_checker():
            def periodic_loop():
                while True:
                    time.sleep(PERIODIC_CHECK_INTERVAL)
                    check_and_rebuild_periodically()
            checker_thread = threading.Thread(target=periodic_loop, daemon=True)
            checker_thread.start()
            log.info(f"Periodic file checking enabled: checking every {PERIODIC_CHECK_INTERVAL} seconds")

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
        _watch.WATCHER.stop()


if __name__ == "__main__":
    main()
