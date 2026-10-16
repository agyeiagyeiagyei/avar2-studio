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
import csv
import functools
import importlib.resources
import json
import os
import subprocess
import sys
import tempfile
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

from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from werkzeug.utils import secure_filename

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    # Create dummy types for type hints when watchdog is not available
    Observer = None
    FileSystemEventHandler = None
    log.warning("Warning: watchdog not available. Auto-rebuild on file save disabled.")
    log.info("Install with: pip install watchdog")

try:
    from glyphsLib import GSFont
except ImportError:
    log.error("Error: glyphsLib not found. Install with: pip install glyphsLib")
    sys.exit(1)

from . import build_jobs as _build_jobs
from . import config_port as _config_port
from . import control_axes as _control_axes
from . import csv_io as _csv_io
from . import glyph_coverage as _glyph_coverage
from . import grade as _grade
from . import instance_aliases as _instance_aliases
from . import kinetikern as _kinetikern
from . import sidecar_io as _sidecar_io
from . import source_font as _source_font
from . import state
from . import transforms as _transforms

# The build/export pipeline lives in pipeline.py (Flask-free, so the build
# worker can import it). These back-imports keep this module's bare-name
# call sites — and the tests patching ``server.X`` — working unchanged.
from .pipeline import (
    _PLACEHOLDER_MAX,
    _PLACEHOLDER_MIN,
    TRADITIONAL_AXIS_DEFAULTS,
    _apply_source_path,
    _derive_traditional_range,
    _evaluate_mapped_location,
    _get_avar2_font_dir,
    _get_preview_config_path,
    _get_preview_csv_path,
    _grade_instance_coords,
    _load_axis_metadata,
    _repair_placeholder_ranges,
    _resolve_active_source,
    _save_axis_metadata,
    _stop_fontra,
    _suppress_watcher,
)
from .pipeline import (
    _get_preview_dir as _get_preview_dir,
)
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
# SUBPROCESSES (docs/WORKER_BUILD_PLAN.md) so a build can never starve
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


@app.route('/api/masters', methods=['GET'])
def get_masters():
    """Return the source's masters — the parametric design corners.

    Pre-listed in the control-axis brace flow so the designer places a
    crbr layer at a specific corner. Read from the active source
    (shadow when control axes are live); master parametric coordinates
    are identical either way.
    """
    if state.GLYPHS_PATH is None:
        return jsonify({"masters": []})
    try:
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
        return jsonify({"masters": _source_font.get_masters(font)})
    except Exception as e:
        log.error(f"Error reading masters: {e}")
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
    if state.GLYPHS_PATH is None:
        return jsonify({"instances": []})
    try:
        instances = get_instances_from_glyphs(state.GLYPHS_PATH)
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
                                for ax in get_axes_from_glyphs(state.GLYPHS_PATH)}
        except Exception:
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
                log.warning(f"Warning: Could not read studio-only rows from CSV: {e}")

        # Per-instance CONTROL-axis values hydrate from the control
        # sidecar so sliders and row previews come back after a reload.
        # Deliberately NOT merged into source_coordinates: they are
        # studio state, and padding that dict would break the CSV-vs-
        # source divergence check (it compares key sets).
        try:
            ctl_values = _control_axes.instance_values(state.ORIGINAL_PATH)
        except Exception:
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
@_sidecar_locked
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
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        _update_csv_modification_time(csv_path)
        return jsonify({"success": True})
    except Exception as e:
        log.error(f"Error reordering instances: {e}")
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
    if state.GLYPHS_PATH is None:
        return jsonify({"axes": []})
    try:
        axes = get_axes_from_glyphs(state.GLYPHS_PATH)

        # CONTROL AXES overlay. The source/shadow .glyphs may or may
        # not have the control axis yet (depending on slice — slice 2
        # generates a shadow but builds from the original, so the
        # source-derived axes won't include the control axis). Either
        # way the sidecar carries the canonical declaration; merge
        # it in so the frontend gets a unified list.
        if state.ORIGINAL_PATH is not None:
            try:
                sidecar_axes = list(_control_axes.list_axes(state.ORIGINAL_PATH))
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
                log.warning(f"Warning: control-axes overlay on /api/axes failed: {overlay_exc}")

        # GRADE axis overlay. GRAD lives in the shadow but its range comes from
        # VIRTUAL masters (no real master spans it), so get_axes_from_glyphs
        # reports it as [0,0] — and the built-font overlay below then skips it
        # as "already present". When grade is enabled with ≥1 graded instance,
        # surface the real -10..+10 range so the sidebar renders a usable
        # slider. Like SPAC it's a live-preview axis (transform_injected), not
        # per-instance data.
        if state.ORIGINAL_PATH is not None:
            try:
                if _grade.list_graded_instances(state.ORIGINAL_PATH):
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
                log.warning(f"Warning: grade overlay on /api/axes failed: {grade_exc}")

        # TRANSFORM-AXES overlay. Transforms can add whole axes WITH twin
        # masters (round_corners' ROND), which reach this list through the
        # TRANSFORMED source and so carry real master coverage — but like
        # SPAC and GRAD they are build products, not per-instance data.
        # Mark every enabled transform's declared axis so the frontend
        # keeps it out of instance saves and CSV sync.
        if state.ORIGINAL_PATH is not None:
            try:
                injected_tags = set()
                for stage in ("font", "source"):
                    for t, _params in _transforms.registry.active(state.ORIGINAL_PATH, stage=stage):
                        tag = getattr(t.spec, "injected_axis_tag", None)
                        if tag:
                            injected_tags.add(tag.lower())
                for a in axes:
                    if str(a.get("tag", "")).lower() in injected_tags:
                        a["transform_injected"] = True
            except Exception as inj_exc:
                log.warning(f"Warning: transform-axes overlay on /api/axes failed: {inj_exc}")

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
        if state.VARIABLE_FONT_PATH is not None and Path(state.VARIABLE_FONT_PATH).exists():
            try:
                user_axis_tags = set()
                csv_path = _get_avar2_csv_path()
                if csv_path and csv_path.exists():
                    try:
                        _, _, in_cols, _, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)
                        user_axis_tags = {
                            _csv_io.normalize_in_axis_name(c).lower() for c in in_cols
                        }
                        user_axis_tags.discard("spac")  # transform-injected, stays parametric
                    except Exception:
                        user_axis_tags = set()
                have = {str(a.get("tag", "")).lower() for a in axes}
                built_axes = list(get_axes_from_built_font(state.VARIABLE_FONT_PATH))
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
                log.warning(f"Warning: built-font overlay on /api/axes failed: {built_exc}")

        return jsonify({"axes": axes})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    with SIDECAR_LOCK:
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
            _resolve_active_source()
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
    that studio-axis Fontra sessions save into), or "glif" (inside a
    designspace shadow's pooled UFOs). None — not ours; ignore the event.

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


@app.route('/api/build', methods=['POST'])
def build_font():
    """Submit a build job and wait for the queue to go idle (the wait is
    GIL-free — every other endpoint keeps answering during the build)."""
    try:
        JOBS.request_build()
        success = JOBS.wait_for_idle()

        if not success:
            return jsonify({"error": state.LAST_BUILD_ERROR or "Build failed"}), 500

        # Get axes after building (for verification)
        axes = get_axes_from_built_font(state.VARIABLE_FONT_PATH)

        return jsonify({
            "success": True,
            "font_path": str(state.VARIABLE_FONT_PATH),
            "axes": axes,
            "build_time": state.LAST_BUILD_TIME
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/build/status', methods=['GET'])
def build_status():
    """Job queue state: running job (+phase), queued jobs, last result,
    export sizes."""
    return jsonify(JOBS.status())


@app.route('/api/build/<job_id>', methods=['GET'])
def build_job_status(job_id):
    """One job's state by id (queued / running / finished + outcome). The
    export poll keys on this: ``status().last`` names only the newest
    finished job, so any job finishing inside a poll window went missing."""
    status = JOBS.job_status(job_id)
    if status is None:
        return jsonify({"error": "no such job"}), 404
    return jsonify(status)


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
        if not (state.VARIABLE_FONT_PATH and sizes["woff2_exact"]):
            return jsonify({"error": "woff2 cache is stale or missing"}), 409
        woff2 = Path(state.VARIABLE_FONT_PATH).with_suffix(".woff2")
        return send_file(str(woff2), mimetype='font/woff2',
                         as_attachment=bool(request.args.get('download')),
                         download_name=woff2.name)
    if state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists():
        font_path = state.VARIABLE_FONT_PATH
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
        import os
        import tempfile

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
        if state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists():
            font_path = state.VARIABLE_FONT_PATH
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
                log.error(f"Error during instantiation: {e}")
                import traceback
                traceback.print_exc()
                return jsonify({"error": f"Font instantiation failed: {e}"}), 500
            finally:
                # Clean up temporary file
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        # Shape the text with uharfbuzz against the instantiated font.
        # This applies kerning (GPOS) and substitutions (GSUB) the same way
        # a browser shaper does, so the displayed Width matches the laid-out
        # width — not just the sum of raw hmtx advances. The per-glyph
        # breakdown also falls out of the same shape() call, which is what
        # the grade-master delta view needs.
        from io import BytesIO

        import uharfbuzz as hb

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
        log.error(f"Error measuring text width: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/instance', methods=['POST'])
@_sidecar_locked
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
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
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
                         for a in _control_axes.list_axes(state.ORIGINAL_PATH)}
            ctl_values = {t: v for t, v in coordinates.items()
                          if str(t).lower() in _ctl_tags}
            if ctl_values:
                _control_axes.set_instance_values(state.ORIGINAL_PATH, instance_name,
                                                  ctl_values)
        except Exception as exc:
            log.warning(f"Warning: could not save control-axis values: {exc}")

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
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        _update_csv_modification_time(csv_path)
        log.info(f"Created studio-only instance '{instance_name}' in CSV")
    except Exception as e:
        log.error(f"Error creating studio-only instance in CSV: {e}")
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
@_sidecar_locked
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

    font, _fmt = _source_font.load_source(state.GLYPHS_PATH)
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
    # sidecar, never as CSV columns (see
    # docs/decisions/0008-per-instance-control-values-in-sidecar.md).
    # /api/instances hydrates them back; the build stamps them onto
    # matching fvar instances.
    try:
        _ctl_tags = {str(a.get("tag", "")).lower()
                     for a in _control_axes.list_axes(state.ORIGINAL_PATH)}
    except Exception:
        _ctl_tags = set()
    control_values = {tag: value for tag, value in coordinates.items()
                      if str(tag).lower() in _ctl_tags}
    if control_values:
        _suppress_watcher()
        try:
            _control_axes.set_instance_values(state.ORIGINAL_PATH, instance_name,
                                              control_values)
        except Exception as exc:
            log.warning(f"Warning: could not save control-axis values: {exc}")

    state.EDITING_INSTANCES.discard(instance_name)

    # Source writeback is skipped if the caller asked for CSV-only.
    # It targets the ORIGINAL — the user's file, the one explicit write
    # the flyout's "Save to source file" promises. (It used to target
    # GLYPHS_PATH, the TRANSFORMED SHADOW: a build artifact regenerated
    # from the original, so the "source save" evaporated on the next
    # regen and the user's file never changed.)
    glyphs_updated = False
    if not csv_only_flag and is_source_instance and glyphs_coordinates:
        glyphs_updated = update_instance_in_glyphs(
            state.ORIGINAL_PATH, _source_instance_name(instance_name), glyphs_coordinates)
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
                        log.warning(f"Warning: Found {updated_count} duplicate rows for '{instance_name}', updated all")
                    _csv_io.backup_sidecar(csv_path)
                    with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    _update_csv_modification_time(csv_path)
                    log.info(f"Saved '{instance_name}' in the studio")
                else:
                    log.error(f"Error: Instance '{instance_name}' not found in the studio")
            except Exception as e:
                log.warning(f"Warning: Could not update CSV: {e}")
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
                if _csv_io.update_csv_from_glyphs(state.GLYPHS_PATH, csv_path, skip_instances={instance_name},
                                                  exclude_axes=_non_mapping_axis_tags()):
                    _update_csv_modification_time(csv_path)   # ours, not external
                    log.info("CSV synced after instance update")
            except Exception as e:
                log.warning(f"Warning: Could not sync CSV after update: {e}")

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
@_sidecar_locked
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
        font, _fmt = _source_font.load_source(state.GLYPHS_PATH)
        source_instance_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
        is_source_instance = instance_name in source_instance_names

        if is_source_instance:
            success = rename_instance_in_glyphs(state.GLYPHS_PATH, instance_name, new_name)
            if success and state.ORIGINAL_PATH is not None:
                # The rename just edited the SHADOW, which is re-derived
                # from the original on every regeneration — record it so
                # regenerate_shadow re-applies it and the old name never
                # resurrects.
                try:
                    _instance_aliases.record(state.ORIGINAL_PATH, instance_name, new_name)
                except Exception as alias_exc:
                    log.warning(f"Warning: could not persist instance rename: {alias_exc}")
        else:
            # Studio-only: rename happens in CSV only, below.
            success = True

        if success:
            # Keep any grade attached to this instance across the rename.
            try:
                _grade.rename_instance(state.ORIGINAL_PATH, instance_name, new_name)
            except Exception:
                pass
            try:
                _kinetikern.sidecar.rename_instance(state.ORIGINAL_PATH, instance_name, new_name)
            except Exception:
                pass
            # Same for per-instance control-axis values.
            try:
                _control_axes.rename_instance_values(state.ORIGINAL_PATH,
                                                     instance_name, new_name)
            except Exception:
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
                    with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)

                    # Update modification time cache
                    _update_csv_modification_time(csv_path)

                    log.info(f"Renamed instance '{instance_name}' to '{new_name}' in preview CSV")
                except Exception as e:
                    log.warning(f"Warning: Could not update instance name in preview CSV: {e}")
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
        font, _fmt = _source_font.load_source(state.ORIGINAL_PATH)
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

        ok = _source_font.add_instance_to_source(font, state.ORIGINAL_PATH, instance_name, coords)
        if not ok:
            return jsonify({"error": f"Failed to add '{instance_name}' to source file"}), 500

        # For .glyphs sources, also poke Glyphs.app to reload the document.
        if _source_is_glyphs(state.ORIGINAL_PATH):
            _force_reload_glyphs_document(state.ORIGINAL_PATH, font_object=font)

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


@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    try:
        family_name = None
        if state.GLYPHS_PATH:
            family_name = get_font_family_name(state.GLYPHS_PATH) or state.GLYPHS_PATH.stem

        return jsonify({
            "status": "ok",
            # Hosted shared-demo instance (AVAR2_STUDIO_DEMO=1): the
            # frontend shows the landing overlay + shared-session note.
            "demo": os.environ.get("AVAR2_STUDIO_DEMO") == "1",
            "glyphs_path": str(state.GLYPHS_PATH) if state.GLYPHS_PATH else None,
            # original_path is the path the USER pointed at. glyphs_path
            # is the ACTIVE build/read path which becomes the shadow
            # whenever CONTROL AXES are declared. Frontend uses the
            # former as the "source identity" for swap detection so
            # declaring a control axis doesn't look like a swap.
            "original_path": str(state.ORIGINAL_PATH) if state.ORIGINAL_PATH else None,
            "source_format": state.SOURCE_FORMAT,
            "font_built": state.VARIABLE_FONT_PATH.exists() if state.VARIABLE_FONT_PATH else False,
            "family_name": family_name,
            "vf_family_id": f"{family_name}-VF" if family_name else None,
            # Report the ACTUAL served font's name (VARIABLE_FONT_PATH is what
            # /api/font serves and the download saves) so a transform-injected
            # name like ``…[SPAC].ttf`` matches the bytes. Fall back to the
            # avar2-dir derivation before the first build completes.
            "built_font_filename": (
                state.VARIABLE_FONT_PATH.name if state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists()
                else _get_avar2_built_font_filename()
            ),
            "last_build_time": state.LAST_BUILD_TIME,
            "last_build_status": state.LAST_BUILD_STATUS,
            "last_build_error": state.LAST_BUILD_ERROR,
            # {sidecar path: parse error} — a corrupt control sidecar reads as
            # empty but is never overwritten; the UI should say so.
            "sidecar_errors": _control_axes.load_errors(),
            "transform_error": state.LAST_TRANSFORM_ERROR,
            # Non-null ⇒ the served font is the plain fallback build;
            # the avar2 build is failing for this reason.
            "avar2_error": state.LAST_AVAR2_ERROR,
            "grade_error": state.LAST_GRADE_ERROR,
            "source_transform_error": state.LAST_SOURCE_TRANSFORM_ERROR,
            "origin_warning": state.LAST_ORIGIN_WARNING,
            # True ⇒ the mappings CSV changed after the last build —
            # the served font's avar2 table is stale. Auto-saved
            # coordinate edits set this instead of rebuilding; the
            # Preview tab rebuilds once on open when it's true.
            "build_stale": _is_build_stale(),
            # Fold in background regen/rebuild work: the layer save returns
            # instantly, so the queue's state alone would read false while the
            # shadow is still regenerating and the preview is stale.
            "building": JOBS.is_building() or state.BACKGROUND_WORK > 0,
            "build_job": JOBS.status().get("running"),
        })
    except Exception as e:
        log.error(f"Error in health endpoint: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/glyphs-file-status', methods=['GET'])
def glyphs_file_status():
    """Check if Glyphs file has unsaved changes."""
    if state.GLYPHS_PATH is None:
        return jsonify({"has_unsaved_changes": False, "file_path": None})
    try:
        has_unsaved = _check_glyphs_file_unsaved_changes(state.GLYPHS_PATH)
        return jsonify({
            "has_unsaved_changes": has_unsaved,
            "file_path": str(state.GLYPHS_PATH)
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "has_unsaved_changes": False
        }), 500


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
    if not state.GLYPHS_PATH or not state.GLYPHS_PATH.exists():
        return set()

    try:
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
        return {axis["tag"].lower() for axis in _source_font.get_axes(font)}
    except Exception as e:
        log.error(f"Error reading axes from source file: {e}")
        return set()


@_sidecar_locked
def _add_missing_instance_to_csv(instance_name: str, glyphs_coords: Dict[str, float], csv_path: Path) -> bool:
    """Add a missing instance to CSV with blank traditional axis values."""
    import csv
    try:
        # Use normalized read function

        rows, fieldnames, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

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
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return True
    except Exception as e:
        log.error(f"Error adding instance to CSV: {e}")
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

        # Get matches
        matches = _csv_io.match_instances(state.GLYPHS_PATH, csv_path)

        # Add missing instances to CSV — unless an existing row already
        # sits at the instance's exact parametric location. A RENAMED row
        # keeps its coordinates, and the source still carries the old
        # name (the studio never writes the original): re-adding by name
        # would resurrect the old instance as a coordinate duplicate on
        # every refresh after a rename.
        rows, _fns, _in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)
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
            matches = _csv_io.match_instances(state.GLYPHS_PATH, csv_path)

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
    if state.GLYPHS_PATH is None:
        return jsonify({"axes": []})
    try:
        font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
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
            sidecar_path = state.ORIGINAL_PATH if state.ORIGINAL_PATH is not None else state.GLYPHS_PATH
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
                        font, control_idx, state.ORIGINAL_PATH
                    )
                except Exception as corner_exc:
                    log.warning(f"Warning: corner-name resolution unavailable: {corner_exc}")
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
            log.warning(f"Warning: failed to merge control-axes sidecar: {e}")

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
        log.error(f"Error in /api/glyph-coverage: {e}")
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
    if state.VARIABLE_FONT_PATH is None or not state.VARIABLE_FONT_PATH.exists():
        return jsonify({"error": "No built font"}), 404
    try:
        coords = json.loads(request.args.get("coordinates", "{}")) or {}
    except (ValueError, TypeError):
        return jsonify({"error": "coordinates must be a JSON object"}), 400

    try:
        return jsonify({"mapped": _evaluate_mapped_location(coords)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    if state.VARIABLE_FONT_PATH is None or not state.VARIABLE_FONT_PATH.exists():
        return jsonify({"error": "No built font"}), 404
    body = request.get_json(silent=True) or {}
    hidden = body.get("hidden_axes") or []
    default_location = body.get("default_location") or None
    fmt = body.get("format") or "ttf"
    if fmt not in ("ttf", "woff2"):
        return jsonify({"error": "format must be ttf or woff2"}), 400

    if default_location:
        tt = TTFont(str(state.VARIABLE_FONT_PATH), lazy=True)
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
    if state.ORIGINAL_PATH is None:
        return jsonify({"transforms": []})
    try:
        _transforms.discover()
        return jsonify({
            "transforms": _transforms.available(state.ORIGINAL_PATH),
            "sidecar_path": str(_transforms.sidecar_path_for(state.ORIGINAL_PATH)),
        })
    except Exception as e:
        log.error(f"Error listing transforms: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/transforms', methods=['PUT'])
@_sidecar_locked
def update_transforms():
    """Replace the project's transform set. Body::

        {"transforms": [
            {"type": "spac", "enabled": true, "params": {"min": -20, "max": 40}}
        ]}

    Unknown types are dropped; params are coerced against each transform's
    schema. Persists to the sidecar, then rebuilds so the preview + axes
    reflect the new chain (transforms are VF post-processors — no shadow
    rewrite needed)."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    entries = data.get("transforms")
    if not isinstance(entries, list):
        return jsonify({"error": "Body must include 'transforms' as a list."}), 400
    try:
        _transforms.discover()
        previous = {}
        try:
            previous = {t.get("id"): t for t in _transforms.available(state.ORIGINAL_PATH)}
        except Exception as prev_exc:
            log.info(f"transforms: could not read previous state: {prev_exc}")
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
            stored = _transforms.set_active(state.ORIGINAL_PATH, entries)
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
            log.warning(f"Warning: rebuild after transforms update failed: {build_exc}")
        # A transform can fail even when the base font compiled fine — surface
        # it so the UI doesn't show an enabled transform that silently no-op'd.
        return jsonify({
            "success": True,
            "transforms": stored,
            "build_ok": bool(build_ok),
            "transform_error": state.LAST_TRANSFORM_ERROR,
        })
    except Exception as e:
        log.error(f"Error updating transforms: {e}")
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


def _grade_param_ranges():
    """(min, max) per parametric tag, or {} when the source can't be read."""
    ranges = {}
    try:
        axes = _source_font.get_axes(_source_font.load_source_cached(state.ORIGINAL_PATH)[0])
        by_tag = {a["tag"].upper(): a for a in axes}
        for t in _grade.PARAM_TAGS:
            if t in by_tag:
                ranges[t] = (by_tag[t]["min"], by_tag[t]["max"])
    except Exception:
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
    data = _grade.load(state.ORIGINAL_PATH)
    param_ranges = _grade_param_ranges()
    try:
        coords = _grade_instance_coords()
        caps = {
            name: _grade.max_pct_for(coords[name], param_ranges)
            for name in coords if param_ranges
        }
    except Exception:
        coords, caps = {}, {}
    data["max_pct"] = caps
    try:
        data["diagnostics"] = _grade_diagnostics(data, coords, param_ranges)
    except Exception as exc:
        log.warning(f"Warning: grade diagnostics failed: {exc}")
        data["diagnostics"] = []
    data["sidecar_path"] = str(_grade.sidecar_path_for(state.ORIGINAL_PATH))
    return data


@app.route('/api/transforms/grade', methods=['GET'])
def get_grade():
    """Return the grade toggle, default, per-instance grades, and slider caps."""
    if state.ORIGINAL_PATH is None:
        return jsonify(_grade._empty())
    try:
        return jsonify(_grade_state_payload())
    except Exception as e:
        log.error(f"Error reading grade: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/transforms/grade', methods=['PUT'])
def set_grade():
    """Set the Grade toggle and/or the global knobs. Body::

        { "enabled": true, "default_pct": 0.25,
          "intensity": 1.0, "clamp_to_headroom": true }

    All fields optional. ``intensity`` scales every authored grade% at once;
    ``clamp_to_headroom`` stops a grade thickening where the counters have no
    room to open. Rebuilds when the built font would change."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        rebuild = False
        if "enabled" in data:
            _grade.set_enabled(state.ORIGINAL_PATH, bool(data["enabled"]))
            rebuild = True  # toggling adds/removes the GRAD axis
        if "default_pct" in data:
            # The default only seeds NEW grades; changing it never alters the
            # built font, so persist without a rebuild.
            _grade.set_default_pct(state.ORIGINAL_PATH, data["default_pct"])
        if "intensity" in data:
            # Scales every authored grade%, so the built font changes.
            _grade.set_intensity(state.ORIGINAL_PATH, data["intensity"])
            rebuild = True
        if "clamp_to_headroom" in data:
            _grade.set_clamp_to_headroom(state.ORIGINAL_PATH, bool(data["clamp_to_headroom"]))
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
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        entry = _grade.set_instance_grade(state.ORIGINAL_PATH, instance_name, data.get("pct"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    schedule_shadow_rebuild()
    return jsonify(entry)


@app.route('/api/instances/<instance_name>/grade', methods=['DELETE'])
def delete_instance_grade(instance_name: str):
    """Remove a grade from one instance. Rebuilds."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    removed = _grade.remove_instance_grade(state.ORIGINAL_PATH, instance_name)
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
    if not (state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists()):
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
        out = _kinetikern.propose(state.VARIABLE_FONT_PATH, location, looseness)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    out["instance"] = name
    # what the sidecar already holds for this instance, by glyph: the build
    # that carries it shows ``cur`` = baseline + applied
    stored = _kinetikern.sidecar.find(state.ORIGINAL_PATH, name) if name else None
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
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    return jsonify(_kinetikern.sidecar.load(state.ORIGINAL_PATH))


def _kinetikern_apply_one(name: str, coords: Dict[str, float], looseness: float,
                          location: Optional[Dict[str, float]] = None) -> Dict:
    """Space ``name`` at ``looseness`` against the current build and store the
    result as deltas. The engine's targets are absolute, so the stored delta
    is the existing one plus the move the current build still needs; once
    that build carries the entry a re-apply changes nothing."""
    location = {t: coords[t] for t in _grade.PARAM_TAGS if t in coords}
    out = _kinetikern.propose(state.VARIABLE_FONT_PATH, location, looseness)
    existing = (_kinetikern.sidecar.find(state.ORIGINAL_PATH, name) or {}).get("deltas", {})
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
    return _kinetikern.sidecar.set_entry(state.ORIGINAL_PATH, name, looseness, deltas, location=location)


def _kinetikern_source_names() -> Dict[int, str]:
    """{code point: source glyph name}. The bake works on the source, whose
    names fontc replaces with production names in the build (euro -> Euro)."""
    try:
        font, fmt = _source_font.load_source_cached(state.ORIGINAL_PATH)
    except Exception:
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
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    if not (state.VARIABLE_FONT_PATH and state.VARIABLE_FONT_PATH.exists()):
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
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    removed = _kinetikern.sidecar.remove_entry(state.ORIGINAL_PATH, instance_name)
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
# Control-axis routes. See docs/guides/secondary-parametric-axes.md.
# ----------------------------------------------------------------------




def _source_glyph_sigs():
    """{glyph: master-geometry signature} for the ORIGINAL source, recomputed
    only when the file's mtime changes. /api/control-axes is polled, and a
    glyphsLib parse per poll would be the wrong price for a staleness flag."""
    if state.ORIGINAL_PATH is None or not state.ORIGINAL_PATH.exists():
        return None
    try:
        mtime = state.ORIGINAL_PATH.stat().st_mtime_ns
    except OSError:
        return None
    if state._SOURCE_GLYPH_SIGS[0] == mtime:
        return state._SOURCE_GLYPH_SIGS[1]
    try:
        font, _fmt = _source_font.load_source(state.ORIGINAL_PATH)
        sigs = {g.name: _control_axes.glyph_masters_sig(font, g.name) for g in font.glyphs}
    except Exception as exc:
        log.warning(f"Warning: could not fingerprint source glyphs: {exc}")
        return None
    state._SOURCE_GLYPH_SIGS = (mtime, sigs)
    return sigs



def _designspace_authoring_refusal():
    """Secondary-axis AUTHORING is .glyphs-only (sprint decision T2.15):
    the designspace shadow regeneration ignores corrections (``target``)
    and captured drawings, has no seed stamps and writes non-atomically,
    so edits made through these routes would silently degrade. Reads keep
    working and existing layers still build. Returns a 501 response, or
    None when the source is a .glyphs file."""
    if state.ORIGINAL_PATH is None:
        return None
    fmt = state.SOURCE_FORMAT or _source_font.detect_format(Path(state.ORIGINAL_PATH))
    if fmt == "designspace":
        return jsonify({
            "error": "Secondary parametric axes are authored on .glyphs sources only; "
                     "this project is a .designspace. Existing layers still build, "
                     "but they can't be edited here.",
            "source_format": "designspace",
        }), 501
    return None

@app.route('/api/control-axes', methods=['GET'])
def list_control_axes():
    """Return the sidecar's control-axis declarations."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"axes": []})
    try:
        # Strip the captured drawings. They are large path dumps the UI never
        # reads, and this endpoint is polled — but WHETHER a layer holds one
        # matters, because that is the hand work a re-seed would discard.
        sigs = _source_glyph_sigs()
        slim = []
        for ax in _control_axes.list_axes(state.ORIGINAL_PATH):
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
            "sidecar_path": str(_control_axes.sidecar_path_for(state.ORIGINAL_PATH)),
        })
    except Exception as e:
        log.error(f"Error listing control axes: {e}")
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
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    # Both source formats author through a shadow now: .glyphs gets
    # brace layers inside a shadow copy, .designspace gets pooled
    # sparse UFO sources next to a shadow document.
    data = request.get_json(silent=True) or {}
    try:
        entry = _control_axes.add_axis(
            state.ORIGINAL_PATH,
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
            shadow = _control_axes.regenerate_shadow(state.ORIGINAL_PATH)
        except Exception as shadow_exc:
            log.warning(f"Warning: shadow regeneration after add failed: {shadow_exc}")

        return jsonify({"success": True, "axis": entry, "shadow_path": str(shadow) if shadow else None})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        log.error(f"Error creating control axis: {e}")
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
    show_nav = bool((state.FONTRA_EDITOR_SESSION or {}).get("studio"))
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
    return Template(_read_asset("fontra/focus.css")).substitute(
        panel_rules=panel_rules, chrome_rules=chrome_rules)


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
# The Fontra studio shim (sidebar focus, layer labels, the metrics HUD)
# lives in fontra/shim.js so it can be linted and diffed as JavaScript;
# the <script> wrapper belongs to the injection, not the file.
_FONTRA_STUDIO_SHIM_JS = (
    '\n<script id="avar2-studio-fontra-shim">\n'
    + _read_asset("fontra/shim.js")
    + '\n</script>\n'
)


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
    if state.FONTRA_PROCESS is None or state.FONTRA_PROCESS.poll() is not None:
        return jsonify({"error": "Fontra subprocess is not running"}), 503

    import urllib.error
    import urllib.request

    upstream = f"http://127.0.0.1:{state.FONTRA_PORT}{upstream_path}"
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
    if state.FONTRA_PROCESS is None or state.FONTRA_PROCESS.poll() is not None:
        ws.close()
        return

    import websocket as ws_client_lib  # websocket-client; pulled in by flask-sock indirectly
    qs = request.query_string.decode("utf-8")
    upstream_url = f"ws://127.0.0.1:{state.FONTRA_PORT}/websocket"
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
    return jsonify(state.FONTRA_EDITOR_SESSION or {"studio": False})


@app.route('/api/control-axes/<tag>/open-editor', methods=['POST'])
def open_control_axis_in_editor(tag: str):
    """Spin up Fontra and return the iframe URL the frontend should
    load. Studio-authored axes edit the shadow copy (the coverage
    save regenerates it with seed brace layers). When no shadow
    exists — source-derived axes on a .designspace or plain .glyphs —
    Fontra opens the original source directly, flagged in the
    response as ``editing_original``.
    """
    if state.ORIGINAL_PATH is None:
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
            for ax in _control_axes.list_axes(state.ORIGINAL_PATH)
        )
    except Exception:
        is_studio_axis = False

    if is_studio_axis:
        if not _control_axes.shadow_exists(state.ORIGINAL_PATH):
            # No coverage / no shadow yet — there's nothing to edit.
            return jsonify({
                "error": "No shadow file yet. Add coverage glyphs first so the studio can seed brace layers."
            }), 400
        # Studio-authored axes: Fontra edits the shadow copy; the
        # user's original source stays untouched.
        target_path = _control_axes.shadow_path_for(state.ORIGINAL_PATH)
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
        target_path = state.ORIGINAL_PATH
        content_root = target_path
        editing_original = True

    # Record the session for the proxy (CSS variant) and the injected
    # shim (which layers to show + batch-enable). Studio-axis sessions
    # expose the designspace-navigation panel trimmed to this axis's
    # studio layers; everything else keeps the bare-canvas UI.
    spec = next(
        (
            ax for ax in (_control_axes.list_axes(state.ORIGINAL_PATH) or [])
            if (ax.get("tag") or "").lower() == tag.lower()
        ),
        None,
    ) if is_studio_axis else None
    # The glyph is the frontend's to know (it builds Fontra's URL fragment from
    # it); recording it here lets the injected shim ask for THIS layer's
    # reference measurements without reaching into Fontra's scene state.
    body = request.get_json(silent=True) or {}
    state.FONTRA_EDITOR_SESSION = {
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
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    entries = data.get("layers")
    if not isinstance(entries, list):
        return jsonify({"error": "Body must include 'layers' as a list."}), 400
    try:
        # Whole-list replace. Prefer the delta endpoint for interactive edits —
        # this one overwrites whatever the caller didn't know about.
        with SIDECAR_LOCK:
            stored = _control_axes.set_layers(state.ORIGINAL_PATH, tag, entries)
        schedule_shadow_rebuild()
        return jsonify({"success": True, "tag": tag, "layers": stored})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        log.error(f"Error setting control-axis layers: {e}")
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
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
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
            stored = _control_axes.apply_layer_delta(state.ORIGINAL_PATH, tag, add=add, remove=remove)
        schedule_shadow_rebuild()
        return jsonify({"success": True, "tag": tag, "layers": stored})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        log.error(f"Error setting control-axis layers: {e}")
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
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    body = request.get_json(silent=True) or {}
    layers = body.get("layers")
    if layers is not None and not isinstance(layers, list):
        return jsonify({"error": "layers must be a list of {glyph, location}"}), 400
    force = bool(body.get("force"))
    try:
        result = _control_axes.reseed_layers(state.ORIGINAL_PATH, tag, layers, force)
    except KeyError:
        return jsonify({"error": f"control axis '{tag}' not found"}), 404
    except Exception as e:
        log.error(f"Error re-seeding control axis layers: {e}")
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
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    if not (state.VARIABLE_FONT_PATH and Path(state.VARIABLE_FONT_PATH).exists()):
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

    axis = _control_axes.find_axis(state.ORIGINAL_PATH, tag)
    if axis is None:
        return jsonify({"error": f"No secondary axis '{tag}'"}), 404
    layers = [lyr for lyr in (axis.get("layers") or []) if lyr.get("glyph") == glyph]
    if not layers and explicit_location is None:
        return jsonify({"error": f"'{glyph}' has no layers on '{tag}'"}), 404
    layer = layers[min(index, len(layers) - 1)] if layers else {}

    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    try:
        font = TTFont(str(state.VARIABLE_FONT_PATH))
        fvar_axes = {a.axisTag: a for a in font["fvar"].axes}
        if explicit_location is not None:
            # Normalize keys: the scene speaks axis NAMES (source space);
            # the sidecar and fvar speak tags. Accept both.
            name_to_tag = {}
            try:
                src_font, _fmt = _source_font.load_source(state.GLYPHS_PATH)
                for ax in _source_font.get_axes(src_font):
                    name_to_tag[str(ax.get("name") or "").lower()] = str(ax.get("tag") or "")
                    name_to_tag[str(ax.get("tag") or "").lower()] = str(ax.get("tag") or "")
            except Exception:
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
            font = TTFont(str(state.VARIABLE_FONT_PATH))
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
        log.error(f"Error measuring reference metrics: {e}")
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
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    try:
        entry = _control_axes.update_axis(
            state.ORIGINAL_PATH,
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
            # enabled. See docs/decisions/0011-ymod-baked-corrections.md.
            _resolve_active_source()
            # See set_control_axis_layers: keep the last-good font on failure.
            try:
                trigger_build()
            except Exception as build_exc:
                log.warning(f"Warning: rebuild after axis update failed: {build_exc}")
        except Exception as shadow_exc:
            log.warning(f"Warning: shadow refresh after update failed: {shadow_exc}")
        return jsonify({"success": True, "axis": entry})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        log.error(f"Error updating control axis: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/control-axes/<tag>', methods=['DELETE'])
def delete_control_axis(tag: str):
    """Remove a control-axis declaration from the sidecar. If the
    sidecar still has any axes, regenerate the shadow without the
    deleted one; if it's now empty, remove the shadow entirely so
    the build pipeline falls back to the original source."""
    refusal = _designspace_authoring_refusal()
    if refusal is not None:
        return refusal
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    try:
        removed = _control_axes.remove_axis(state.ORIGINAL_PATH, tag)
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
            if (state.GLYPHS_PATH == state.ORIGINAL_PATH
                    and not _control_axes.list_axes(state.ORIGINAL_PATH)
                    and not _grade.list_graded_instances(state.ORIGINAL_PATH)):
                _control_axes.remove_shadow(state.ORIGINAL_PATH)
            # See set_control_axis_layers: keep the last-good font on failure.
            try:
                trigger_build()
            except Exception as build_exc:
                log.warning(f"Warning: rebuild after delete failed: {build_exc}")
        except Exception as shadow_exc:
            log.warning(f"Warning: shadow refresh after delete failed: {shadow_exc}")

        return jsonify({"success": True, "tag": tag})
    except Exception as e:
        log.error(f"Error deleting control axis: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/config/export', methods=['GET'])
def export_config_bundle():
    """Download the studio-authored configuration (control axes +
    brace-layer declarations, avar2 mappings CSV, transforms) as one
    portable JSON bundle. See config_port.py for the format."""
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    try:
        # build_export captures drawn outlines into the sidecar first, which
        # is a write next to the watched source — suppress the watcher so an
        # export does not look like a designer edit and trigger a rebuild.
        _suppress_watcher()
        bundle = _config_port.build_export(state.ORIGINAL_PATH, _get_avar2_csv_path())
    except Exception as e:
        log.error(f"Error exporting config: {e}")
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
    if state.ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    body = request.get_json(silent=True) or {}
    bundle = body.get("bundle")
    if bundle is None:
        return jsonify({"error": "POST JSON {bundle: {...}, dry_run: bool}"}), 400
    dry_run = bool(body.get("dry_run", True))
    try:
        report = _config_port.validate_bundle(bundle, state.ORIGINAL_PATH)
    except Exception as e:
        log.error(f"Error validating config bundle: {e}")
        return jsonify({"error": str(e)}), 500
    if dry_run or not report["ok"]:
        return jsonify(report), (200 if dry_run else 400)

    with SIDECAR_LOCK:
        try:
            # CSV destination: the CSV the studio is currently editing,
            # else the conventional <stem>-avar.csv next to the ORIGINAL
            # (never the shadow — same rule as every sidecar).
            csv_dest = _get_avar2_csv_path() or _get_preview_csv_path()
            report = _config_port.apply_bundle(bundle, state.ORIGINAL_PATH, csv_dest)
        except Exception as e:
            log.error(f"Error applying config bundle: {e}")
            return jsonify({"error": str(e)}), 500

        # Same shadow + build dance as delete_control_axis: re-derive the
        # active source from the imported control axes AND grades, dropping the
        # shadow only when neither needs it; keep the last-good font on failure.
        try:
            _resolve_active_source()
            if (state.GLYPHS_PATH == state.ORIGINAL_PATH
                    and not _control_axes.list_axes(state.ORIGINAL_PATH)
                    and not _grade.list_graded_instances(state.ORIGINAL_PATH)):
                _control_axes.remove_shadow(state.ORIGINAL_PATH)
            try:
                trigger_build()
            except Exception as build_exc:
                log.warning(f"Warning: rebuild after config import failed: {build_exc}")
        except Exception as shadow_exc:
            log.warning(f"Warning: shadow refresh after config import failed: {shadow_exc}")

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


        rows, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

        # Normalize traditional axis names
        traditional_axes = [_csv_io.normalize_in_axis_name(col) for col in in_cols]

        # Load metadata
        metadata = _load_axis_metadata()

        # Get parametric axes from the source file and populate metadata.
        glyphs_axes_info = {}
        if state.GLYPHS_PATH and state.GLYPHS_PATH.exists():
            try:
                font, _fmt = _source_font.load_source_cached(state.GLYPHS_PATH)
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
                log.warning(f"Warning: Could not read axes from source file: {e}")

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
        if state.ORIGINAL_PATH is not None:
            try:
                control_tags_upper = {
                    str(a.get("tag", "")).upper()
                    for a in _control_axes.list_axes(state.ORIGINAL_PATH)
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
    state.EDITING_INSTANCES.add(instance_name)
    return jsonify({"success": True, "message": f"Instance '{instance_name}' registered as editing"})

@app.route('/api/instance/<instance_name>', methods=['DELETE'])
@_sidecar_locked
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
    if touches_source and _check_glyphs_file_unsaved_changes(state.GLYPHS_PATH):
        return jsonify({"error": "Glyphs file has unsaved changes. Please save the file first."}), 409

    try:
        state.EDITING_INSTANCES.discard(instance_name)

        # A full delete (not a demote / csv-only) removes the instance
        # entirely, so drop any grade attached to it. Demote/csv-only keep
        # the instance around, so the grade stays.
        if not csv_only_flag and not source_only_flag:
            try:
                _grade.remove_instance_grade(state.ORIGINAL_PATH, instance_name)
            except Exception:
                pass
            try:
                _kinetikern.sidecar.remove_entry(state.ORIGINAL_PATH, instance_name)
            except Exception:
                pass
            try:
                _control_axes.remove_instance_values(state.ORIGINAL_PATH, instance_name)
            except Exception:
                pass

        # Source-file delete fires for default + source_only. CSV-only
        # skips it (the row was never in the source).
        if touches_source:
            glyphs_deleted = delete_instance_in_glyphs(
                state.ORIGINAL_PATH, _source_instance_name(instance_name))
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
                with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

                # Update modification time cache
                _update_csv_modification_time(csv_path)
                log.info(f"Removed instance '{instance_name}' from preview CSV")
            except Exception as e:
                log.warning(f"Warning: Could not remove instance from preview CSV: {e}")
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
    state.EDITING_INSTANCES.discard(instance_name)
    return jsonify({"success": True, "message": f"Instance '{instance_name}' unregistered from editing"})

@app.route('/api/avar2/axis', methods=['POST'])
@_sidecar_locked
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

        rows, fieldnames, _, _, fieldname_mapping = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

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
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
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
@_sidecar_locked
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

        _, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

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
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
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
@_sidecar_locked
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

        _, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)
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
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
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
        except Exception as build_exc:
            log.warning(f"Warning: rebuild after axis delete failed: {build_exc}")
        return jsonify({"success": True, "deleted": axis_name_normalized,
                        "build_ok": bool(build_ok)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/avar2/mapping/<instance_name>/<axis_name>', methods=['PUT'])
@_sidecar_locked
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

        rows, fieldnames, _, _, _ = _csv_io.read_csv_mappings_with_axes(csv_path, state.GLYPHS_PATH)

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
        with _sidecar_io.atomic_writer(csv_path, encoding="utf-8-sig", newline="") as f:
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
    import re as _re
    import shutil
    import zipfile

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
                log.info(f"Migrated legacy upload workspace → {workspace}")

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
        log.error(f"Error in /api/load-source: {exc}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


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
            schedule_shadow_rebuild()
            return

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
                        log.warning(f"Warning: could not capture outlines: {exc}")
                # Re-resolve NOW so the read endpoints (/api/instances,
                # /api/axes, coverage) stop serving the pre-save shadow
                # while the current build is still running; the queue
                # re-resolves before staging the follow-up regardless.
                _resolve_source_after_save(src)
                JOBS.request_build(source_refresh=kind)
            else:
                JOBS.request_build()
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
                sync_csv_with_glyphs()
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
                sync_csv_with_glyphs()
            else:
                log.info("UFO glyph edited (Fontra), rebuilding...")
            trigger_build()
        except Exception as e:
            log.error(f"Error handling file change: {e}")

# Set up file watcher if watchdog is available AND we have a source
# loaded. Blind launches skip watching — the source will be loaded
# via /api/load-source, but a runtime-swappable file watcher is
# left for a future iteration. The frontend's polling tick covers
# the no-watcher case acceptably.


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

    # Set up real-time file watching using watchdog
    state._SYNC_CSV_HOOK = sync_csv_with_glyphs

    if WATCHDOG_AVAILABLE and state.GLYPHS_PATH is not None:
        event_handler = GlyphsFileHandler()
        observer = Observer()
        # .designspace shadows need recursion: Fontra writes .glif
        # files inside the pooled UFO directories.
        observer.schedule(
            event_handler,
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
                    observer.schedule(event_handler, path=str(orig_dir), recursive=False)
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
                    observer.schedule(event_handler, path=str(shadow_dir), recursive=False)
        except Exception as exc:
            log.warning(f"Warning: could not watch the shadow dir: {exc}")
        observer.start()
        state.OBSERVER = observer
        log.info(f"Real-time file watching enabled: watching {state.GLYPHS_PATH}")
    elif state.GLYPHS_PATH is None:
        pass  # blind launch — watcher attaches when a source is loaded later (TODO)
    else:
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
        if state.OBSERVER:
            state.OBSERVER.stop()
            state.OBSERVER.join()


if __name__ == "__main__":
    main()
