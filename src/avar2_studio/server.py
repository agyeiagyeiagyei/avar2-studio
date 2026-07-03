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
import json
import os
import shutil
import subprocess
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
    """CRA-built JS/CSS/images live under ``static/static/``."""
    return send_from_directory(str(_BUNDLE_DIR / "static"), filename)


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
FONTRA_CONTENT_ROOT: Optional[Path] = None
SOURCE_FORMAT: Optional[str] = None  # "glyphs" | "designspace"
BUILD_DIR: Optional[Path] = None
VARIABLE_FONT_PATH: Optional[Path] = None  # Last-good built font; only updated after a successful build
LAST_BUILD_TIME: Optional[float] = None
BUILDING: bool = False
# Outcome of the most recent build attempt. ``ok`` => current preview reflects
# fresh source state. ``failed`` => the preview is stale (we're still serving
# the last-good font; the user's most recent edit didn't make it through).
LAST_BUILD_STATUS: Optional[str] = None  # "ok" | "failed" | None
LAST_BUILD_ERROR: Optional[str] = None   # human-readable detail for "failed"
OBSERVER: Optional[Observer] = None
CSV_PATH: Optional[Path] = None  # Path to avar2-mappings.csv
USE_FONTC: bool = True  # Use fontc by default, fallback to fontmake
PREVIEW_DIR: Optional[Path] = None  # Per-project workdir (sibling .avar2-studio/)
PREVIEW_CSV_PATH: Optional[Path] = None  # Path to preview CSV (sibling to .glyphs)
PREVIEW_CONFIG_PATH: Optional[Path] = None  # Path to preview config (.avar2-studio/config.yaml)
EDITING_INSTANCES: set = set()  # Track instances currently being edited (protected from sync)


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


def get_instances_from_glyphs(glyphs_path: Path) -> List[Dict]:
    """Read source-defined instances from the source file (any supported format).

    Returns a list of ``{name, coordinates}`` dicts. Each instance also gets
    an ``origin: "source"`` tag — studio-only instances are computed by the
    caller via the CSV.
    """
    try:
        font, _fmt = _source_font.load_source(glyphs_path)
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
        font, _fmt = _source_font.load_source(glyphs_path)
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
            "default": float(axis.defaultValue)
        })
    
    return axes


def _source_is_glyphs(glyphs_path: Path) -> bool:
    return glyphs_path.suffix.lower() == ".glyphs"


def create_instance_in_glyphs(glyphs_path: Path, instance_name: str, coordinates: Dict[str, float], insert_after_instance_name: Optional[str] = None) -> bool:
    """Add an instance directly to the source file.

    Note: the UI's Create/Duplicate flow does NOT call this anymore — those
    paths now write to the CSV only and surface the instance as
    ``origin: studio``. This helper survives as the implementation behind
    ``POST /api/instance/<name>/add-to-source``, which promotes a
    studio-only instance into the source's instance list.
    """
    try:
        font, _fmt = _source_font.load_source(glyphs_path)
        ok = _source_font.add_instance_to_source(
            font, glyphs_path, instance_name, coordinates, insert_after_instance_name
        )
        if not ok:
            raise ValueError(f"Instance '{instance_name}' already exists")
        if _source_is_glyphs(glyphs_path):
            _force_reload_glyphs_document(glyphs_path, font_object=font)
        return True
    except ValueError:
        raise
    except Exception as e:
        print(f"Error creating instance in source file: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


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
        font, _fmt = _source_font.load_source(GLYPHS_PATH)
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
        # the source file. We don't include their coordinates here — the
        # frontend already fetches CSV data through other endpoints and
        # the row's coordinates aren't strictly needed for instance
        # listing. (If the UI later wants them, we can add parametric
        # values from the CSV here.)
        csv_path = _get_preview_csv_path()
        if csv_path and csv_path.exists():
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        name = (row.get("Instance Name") or "").strip()
                        if not name or name in source_names:
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

        return jsonify({"instances": instances})
    except Exception as e:
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

        return jsonify({"axes": axes})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def trigger_build():
    """Trigger font build (used by both manual and auto-rebuild).

    Tries the avar2 build first so the preview reflects the actual avar2
    table the browser will apply. Falls back to a plain variable-font build
    if the avar2 build fails (e.g. CSV is mid-edit, gftools is missing, or
    the user hasn't authored mappings yet) so the rows view always has
    *some* font to show.
    """
    global VARIABLE_FONT_PATH, LAST_BUILD_TIME, BUILDING, USE_FONTC
    global LAST_BUILD_STATUS, LAST_BUILD_ERROR

    if BUILDING:
        print("Build already in progress, skipping...", file=sys.stderr)
        return False

    # Try the avar2 build first. _perform_avar2_build manages BUILDING itself.
    avar2_result = _perform_avar2_build(check_sync=False)
    if avar2_result.get("success"):
        print(f"Avar2 font built: {avar2_result['font_path']}", file=sys.stderr)
        return True

    print(
        f"Avar2 build skipped/failed ({avar2_result.get('error')}); "
        f"falling back to plain variable-font build.",
        file=sys.stderr,
    )

    BUILDING = True
    try:
        print(f"Building font from {GLYPHS_PATH}...", file=sys.stderr)
        VARIABLE_FONT_PATH = build_variable_font(GLYPHS_PATH, BUILD_DIR, use_fontc=USE_FONTC)
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
        return False
    finally:
        BUILDING = False


@app.route('/api/build', methods=['POST'])
def build_font():
    """Build the variable font from Glyphs file using fontmake."""
    try:
        success = trigger_build()
        
        if not success:
            return jsonify({"error": "Build failed or already in progress"}), 500
        
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


@app.route('/api/font', methods=['GET'])
def get_font():
    """Serve the built variable font (avar2 build preferred, plain VF fallback)."""
    # Phase 3: SPAC font is deferred and the avar2 build is now the primary
    # output. ``trigger_build()`` sets VARIABLE_FONT_PATH to the avar2 build
    # when it succeeds, or the plain VF when it doesn't, so we just serve
    # whatever's there.
    if VARIABLE_FONT_PATH and VARIABLE_FONT_PATH.exists():
        font_path = VARIABLE_FONT_PATH
    else:
        return jsonify({"error": "Variable font not built yet."}), 404
    
    response = send_file(
        str(font_path),
        mimetype='font/ttf',
        as_attachment=False
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


@app.route('/api/preview-font', methods=['GET'])
def get_preview_font():
    """Serve the preview font file (with SPAC axis if available)."""
    preview_font_dir = _get_preview_font_dir()
    if not preview_font_dir or not preview_font_dir.exists():
        return jsonify({"error": "Preview font directory not found"}), 404
    
    # Look for font with SPAC in name first, then any .ttf file
    family_name = GLYPHS_PATH.stem if GLYPHS_PATH else None
    spac_font = preview_font_dir / f"{family_name}[SPAC].ttf" if family_name else None

    if spac_font is not None and spac_font.exists():
        font_path = spac_font
    else:
        # Fallback to any .ttf file in preview directory
        ttf_files = list(preview_font_dir.glob("*.ttf"))
        if not ttf_files:
            return jsonify({"error": "Preview font not built yet"}), 404
        font_path = ttf_files[0]  # Use first TTF file found
    
    response = send_file(
        str(font_path),
        mimetype='font/ttf',
        as_attachment=False
    )
    # Add cache control headers to prevent browser caching
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    # Add ETag based on file modification time for cache validation
    mtime = font_path.stat().st_mtime
    response.headers['ETag'] = f'"{int(mtime)}"'
    return response


@app.route('/api/instance', methods=['POST'])
def create_instance():
    """Create a new studio-only instance.

    Two-tier model: created instances are written only to the sibling CSV
    with ``origin: studio``. The source file (.glyphs / .designspace) is
    NOT mutated until the user explicitly promotes via
    ``POST /api/instance/<name>/add-to-source``. This keeps the source
    instance list from getting bloated by exploratory grid points.
    """
    data = request.get_json()
    if not data or 'name' not in data or 'coordinates' not in data:
        return jsonify({"error": "Missing 'name' or 'coordinates' in request body"}), 400

    instance_name = data['name'].strip()
    if not instance_name:
        return jsonify({"error": "Instance name cannot be empty"}), 400

    coordinates = data['coordinates']
    try:
        coordinates = {k: float(v) for k, v in coordinates.items()}
    except (ValueError, TypeError):
        return jsonify({"error": "Coordinates must be numeric"}), 400

    coordinates = {k: v for k, v in coordinates.items() if k.upper() != 'SPAC'}

    insert_after = data.get('insert_after', None)
    if insert_after:
        insert_after = insert_after.strip()

    # Refuse to create if the name already exists either in the source or in the CSV.
    try:
        font, _fmt = _source_font.load_source(GLYPHS_PATH)
        existing_source_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
    except Exception:
        existing_source_names = set()
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
        source_axes = _source_font.get_axes(font)
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
        for field in fieldnames:
            new_row.setdefault(field, str(axis_defaults.get(field.upper(), 0)))

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

    # Trigger rebuild in background so the avar2 build picks up the new
    # studio-only row. Same SPAC-aware path as the update flow.
    def rebuild_in_background():
        global BUILDING
        if BUILDING:
            print("Build already in progress, skipping rebuild after instance creation...", file=sys.stderr)
            return

        spac_font_dir = _get_spac_font_dir()
        if spac_font_dir:
            spac_font_path = spac_font_dir / "Crispy-SPAC-VF.ttf"
            if spac_font_path.exists():
                print(f"Instance created, regenerating SPAC font...", file=sys.stderr)
                BUILDING = True
                try:
                    if _regenerate_spac_font():
                        BUILDING = False
                    else:
                        print(f"SPAC regeneration failed, falling back to regular build...", file=sys.stderr)
                        BUILDING = False
                        trigger_build()
                except Exception as e:
                    print(f"Error during SPAC font regeneration: {e}", file=sys.stderr)
                    BUILDING = False
            else:
                print(f"Instance created, triggering immediate rebuild...", file=sys.stderr)
                trigger_build()
        else:
            print(f"Instance created, triggering immediate rebuild...", file=sys.stderr)
            trigger_build()

    rebuild_thread = threading.Thread(target=rebuild_in_background, daemon=True)
    rebuild_thread.start()

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
    data = request.get_json()
    if not data or 'coordinates' not in data:
        return jsonify({"error": "Missing 'coordinates' in request body"}), 400

    coordinates = data['coordinates']
    try:
        coordinates = {k: float(v) for k, v in coordinates.items()}
    except (ValueError, TypeError):
        return jsonify({"error": "Coordinates must be numeric"}), 400

    # SPAC support is deferred; if the caller sent one we silently drop it.
    coordinates.pop('SPAC', None)

    csv_only_flag = (
        request.args.get('csv_only', '').lower() in ('1', 'true', 'yes')
        or bool(data.get('csv_only'))
    )

    font, _fmt = _source_font.load_source(GLYPHS_PATH)
    source_instance_names = {entry["name"] for entry in _source_font.get_source_instances(font)}
    is_source_instance = instance_name in source_instance_names
    source_axis_tags = {axis["tag"] for axis in _source_font.get_axes(font)}

    glyphs_coordinates = {
        tag: value for tag, value in coordinates.items()
        if tag in source_axis_tags
    }

    global EDITING_INSTANCES
    EDITING_INSTANCES.discard(instance_name)

    # Source writeback is skipped if the caller asked for CSV-only.
    glyphs_updated = False
    if not csv_only_flag and is_source_instance and glyphs_coordinates:
        glyphs_updated = update_instance_in_glyphs(GLYPHS_PATH, instance_name, glyphs_coordinates)
        if not glyphs_updated:
            return jsonify({"error": f"Failed to update instance '{instance_name}' in source file"}), 500

    # CSV writeback fires whenever there are coords to record. For
    # studio-only instances this is the only persistence path; for
    # source instances it keeps the avar2 mapping CSV row in lockstep
    # with the source's <location>.
    csv_writeback_needed = bool(glyphs_coordinates)
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
                            for tag, value in glyphs_coordinates.items():
                                row[tag.upper()] = str(value)
                        instance_updated = True
                        updated_count += 1

                if instance_updated:
                    if updated_count > 1:
                        print(f"Warning: Found {updated_count} duplicate rows for '{instance_name}', updated all", file=sys.stderr)
                    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    _update_csv_modification_time(csv_path)
                    print(f"Updated CSV row for '{instance_name}'", file=sys.stderr)
                else:
                    print(f"Error: Instance '{instance_name}' not found in CSV", file=sys.stderr)
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
                if _csv_io.update_csv_from_glyphs(GLYPHS_PATH, csv_path, skip_instances={instance_name}):
                    print("CSV synced after instance update", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not sync CSV after update: {e}", file=sys.stderr)
    
    # Trigger immediate rebuild after updating instance
    # If SPAC font exists, regenerate it (designspace-based), otherwise rebuild regular font
    # Run rebuild in background thread to avoid blocking the response and file locking issues
    def rebuild_in_background():
        global BUILDING
        if BUILDING:
            print("Build already in progress, skipping rebuild after instance update...", file=sys.stderr)
            return
        
        spac_font_dir = _get_spac_font_dir()
        if spac_font_dir:
            spac_font_path = spac_font_dir / "Crispy-SPAC-VF.ttf"
            if spac_font_path.exists():
                # SPAC font exists - regenerate it using add-spac-axis-ufo.py
                print(f"Instance updated, regenerating SPAC font...", file=sys.stderr)
                BUILDING = True
                try:
                    if _regenerate_spac_font():
                        # SPAC regeneration succeeded
                        BUILDING = False
                    else:
                        # Fallback to regular build if regeneration fails
                        print(f"SPAC regeneration failed, falling back to regular build...", file=sys.stderr)
                        BUILDING = False  # Reset before trigger_build (which sets it)
                        trigger_build()
                except Exception as e:
                    print(f"Error during SPAC font regeneration: {e}", file=sys.stderr)
                    BUILDING = False
            else:
                # No SPAC font - rebuild regular font
                print(f"Instance updated, triggering immediate rebuild...", file=sys.stderr)
                trigger_build()
        else:
            # No SPAC font directory - rebuild regular font
            print(f"Instance updated, triggering immediate rebuild...", file=sys.stderr)
            trigger_build()
    
    # Start rebuild in background thread (small delay to ensure file save completes)
    rebuild_thread = threading.Thread(target=rebuild_in_background, daemon=True)
    rebuild_thread.start()
    
    message_parts = []
    if glyphs_updated:
        message_parts.append(f"Updated instance '{instance_name}' in source file")
    if csv_writeback_needed:
        message_parts.append(f"Updated CSV row for '{instance_name}'")

    return jsonify({
        "success": True,
        "message": "; ".join(message_parts) if message_parts else f"Updated instance '{instance_name}'",
    })


@app.route('/api/instance/<instance_name>/rename', methods=['PUT'])
def rename_instance(instance_name: str):
    """Rename an instance in the Glyphs file."""
    data = request.get_json()
    if not data or 'new_name' not in data:
        return jsonify({"error": "Missing 'new_name' in request body"}), 400
    
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
        else:
            # Studio-only: rename happens in CSV only, below.
            success = True

        if success:
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
            
            # Trigger rebuild in background thread (same pattern as update_instance)
            # This avoids blocking the response and file locking issues
            def rebuild_in_background():
                global BUILDING
                if BUILDING:
                    print("Build already in progress, skipping rebuild after rename...", file=sys.stderr)
                    return
                
                spac_font_dir = _get_spac_font_dir()
                if spac_font_dir:
                    spac_font_path = spac_font_dir / "Crispy-SPAC-VF.ttf"
                    if spac_font_path.exists():
                        # SPAC font exists - regenerate it using add-spac-axis-ufo.py
                        print(f"Instance renamed, regenerating SPAC font...", file=sys.stderr)
                        BUILDING = True
                        try:
                            if _regenerate_spac_font():
                                # SPAC regeneration succeeded
                                BUILDING = False
                            else:
                                # Fallback to regular build if regeneration fails
                                print(f"SPAC regeneration failed, falling back to regular build...", file=sys.stderr)
                                BUILDING = False  # Reset before trigger_build (which sets it)
                                trigger_build()
                        except Exception as e:
                            print(f"Error during SPAC font regeneration: {e}", file=sys.stderr)
                            BUILDING = False
                    else:
                        # No SPAC font - rebuild regular font
                        print(f"Instance renamed, triggering immediate rebuild...", file=sys.stderr)
                        trigger_build()
                else:
                    # No SPAC font directory - rebuild regular font
                    print(f"Instance renamed, triggering immediate rebuild...", file=sys.stderr)
                    trigger_build()
            
            # Start rebuild in background thread (small delay to ensure file save completes)
            rebuild_thread = threading.Thread(target=rebuild_in_background, daemon=True)
            rebuild_thread.start()
            
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
        font, _fmt = _source_font.load_source(GLYPHS_PATH)
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
            return jsonify({"error": f"Instance '{instance_name}' not found in CSV"}), 404

        ok = _source_font.add_instance_to_source(font, GLYPHS_PATH, instance_name, coords)
        if not ok:
            return jsonify({"error": f"Failed to add '{instance_name}' to source file"}), 500

        # For .glyphs sources, also poke Glyphs.app to reload the document.
        if _source_is_glyphs(GLYPHS_PATH):
            _force_reload_glyphs_document(GLYPHS_PATH, font_object=font)

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
    regardless of what's inside the source.
    """
    try:
        if not glyphs_path or not glyphs_path.exists():
            return None
        font, _fmt = _source_font.load_source(glyphs_path)
        return _source_font.get_family_name(font, glyphs_path)
    except Exception as e:
        print(f"Error getting font family name: {e}", file=sys.stderr)
        return None


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


@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    try:
        family_name = None
        if GLYPHS_PATH:
            family_name = get_font_family_name(GLYPHS_PATH) or GLYPHS_PATH.stem

        return jsonify({
            "status": "ok",
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
            "built_font_filename": _get_avar2_built_font_filename(),
            "last_build_time": LAST_BUILD_TIME,
            "last_build_status": LAST_BUILD_STATUS,
            "last_build_error": LAST_BUILD_ERROR,
            "building": BUILDING,
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

    if not GLYPHS_PATH:
        return None

    workdir = GLYPHS_PATH.parent / ".avar2-studio"
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

    if not GLYPHS_PATH:
        return None

    # Locked convention: family name is the source file's stem in both
    # .glyphs and .designspace flows, so the CSV name is deterministic
    # without loading the source.
    family_name = GLYPHS_PATH.stem
    csv_path = GLYPHS_PATH.parent / f"{family_name}-avar.csv"
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


def _get_preview_font_dir() -> Optional[Path]:
    """Get the build output directory (.avar2-studio/build/)."""
    workdir = _get_preview_dir()
    if not workdir:
        return None
    font_dir = workdir / "build"
    font_dir.mkdir(parents=True, exist_ok=True)
    return font_dir


def _regenerate_spac_font() -> bool:
    """
    Regenerate SPAC font using add-spac-axis-ufo.py.
    This is called after Glyphs file updates to refresh the SPAC font.
    
    Returns True if successful, False otherwise.
    """
    if not GLYPHS_PATH or not GLYPHS_PATH.exists():
        return False
    
    spac_font_dir = _get_spac_font_dir()
    if not spac_font_dir:
        return False
    
    family_name = GLYPHS_PATH.stem
    spac_font_path = spac_font_dir / "Crispy-SPAC-VF.ttf"
    
    add_spac_script = Path(__file__).parent / "add-spac-axis-ufo.py"
    if not add_spac_script.exists():
        print(f"Warning: add-spac-axis-ufo.py not found at {add_spac_script}", file=sys.stderr)
        return False
    
    # Run add-spac-axis-ufo.py to regenerate SPAC font
    cmd = [
        sys.executable,
        str(add_spac_script),
        str(GLYPHS_PATH),
        "--output-dir", str(spac_font_dir),
        "--compile",
        "--fontc-output", str(spac_font_path)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    
    if result.returncode == 0 and spac_font_path.exists():
        print(f"✓ Regenerated SPAC font: {spac_font_path}", file=sys.stderr)
        return True
    else:
        print(f"Warning: SPAC font regeneration failed:", file=sys.stderr)
        print(f"  stdout: {result.stdout}", file=sys.stderr)
        print(f"  stderr: {result.stderr}", file=sys.stderr)
        return False


def _get_spac_font_dir() -> Optional[Path]:
    """Get SPAC font directory (preview-app/preview-fonts/spac/)."""
    preview_font_dir = _get_preview_font_dir()
    if not preview_font_dir:
        return None
    spac_dir = preview_font_dir / "spac"
    spac_dir.mkdir(parents=True, exist_ok=True)
    return spac_dir


def _ensure_spac_font_exists() -> Optional[Path]:
    """
    Ensure designspace-generated SPAC font exists.
    If not, run add-spac-axis-ufo.py to generate it.
    
    Returns path to SPAC font if successful, None otherwise.
    """
    if not GLYPHS_PATH or not GLYPHS_PATH.exists():
        return None
    
    spac_font_dir = _get_spac_font_dir()
    if not spac_font_dir:
        return None
    
    family_name = GLYPHS_PATH.stem
    spac_font_path = spac_font_dir / "Crispy-SPAC-VF.ttf"
    
    # Check if font already exists
    if spac_font_path.exists():
        return spac_font_path
    
    # Check if designspace exists (indicates partial generation)
    designspace_files = list(spac_font_dir.glob("*.designspace"))
    designspace_path = designspace_files[0] if designspace_files else None
    
    # If designspace exists but font doesn't, just compile it
    if designspace_path and designspace_path.exists():
        print(f"Designspace found but font missing, compiling with fontc...", file=sys.stderr)
        # Compile designspace with fontc
        fontc_cmd = shutil.which("fontc")
        if fontc_cmd:
            cmd = [
                fontc_cmd,
                "--output-file", str(spac_font_path),
                str(designspace_path.resolve())
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0 and spac_font_path.exists():
                print(f"✓ Compiled SPAC font: {spac_font_path}", file=sys.stderr)
                return spac_font_path
            else:
                print(f"Warning: fontc compilation failed: {result.stderr}", file=sys.stderr)
    
    # Generate SPAC font using add-spac-axis-ufo.py
    print(f"Generating SPAC font...", file=sys.stderr)
    add_spac_script = Path(__file__).parent / "add-spac-axis-ufo.py"
    
    if not add_spac_script.exists():
        print(f"Warning: add-spac-axis-ufo.py not found at {add_spac_script}", file=sys.stderr)
        return None
    
    # Run add-spac-axis-ufo.py
    cmd = [
        sys.executable,
        str(add_spac_script),
        str(GLYPHS_PATH),
        "--output-dir", str(spac_font_dir),
        "--compile",
        "--fontc-output", str(spac_font_path)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    
    if result.returncode == 0 and spac_font_path.exists():
        print(f"✓ Generated SPAC font: {spac_font_path}", file=sys.stderr)
        return spac_font_path
    else:
        print(f"Warning: SPAC font generation failed:", file=sys.stderr)
        print(f"  stdout: {result.stdout}", file=sys.stderr)
        print(f"  stderr: {result.stderr}", file=sys.stderr)
        return None


def _get_avar2_font_dir() -> Optional[Path]:
    """Get avar2 font directory (collapsed into the shared build dir)."""
    workdir = _get_preview_dir()
    if not workdir:
        return None
    font_dir = workdir / "build"
    font_dir.mkdir(parents=True, exist_ok=True)
    return font_dir


def _check_preview_csv_sync_status() -> Dict[str, any]:
    """
    Check if preview CSV is synced with Glyphs file.
    
    Returns dict with:
    - synced: bool
    - message: str
    - glyphs_instances: list of instance names in Glyphs
    - csv_instances: list of instance names in CSV
    """
    try:
        csv_path = _get_preview_csv_path()
        if not csv_path or not csv_path.exists():
            return {
                "synced": False,
                "message": "Preview CSV not found",
                "glyphs_instances": [],
                "csv_instances": []
            }
        
        if not GLYPHS_PATH or not GLYPHS_PATH.exists():
            return {
                "synced": False,
                "message": "Glyphs file not found",
                "glyphs_instances": [],
                "csv_instances": []
            }
        
        glyphs_instances_dict = _csv_io.get_glyphs_instances(GLYPHS_PATH)
        glyphs_instances = set(glyphs_instances_dict.keys())

        csv_rows, fieldnames = _csv_io.read_csv_mappings(csv_path)
        instance_name_col = "Instance Name"
        if instance_name_col not in fieldnames:
            return {
                "synced": False,
                "message": "CSV missing 'Instance Name' column",
                "glyphs_instances": sorted(glyphs_instances),
                "csv_instances": []
            }
        
        csv_instances = {row[instance_name_col].strip() for row in csv_rows if row.get(instance_name_col)}
        
        # Check if they match
        missing_in_csv = glyphs_instances - csv_instances
        missing_in_glyphs = csv_instances - glyphs_instances
        
        synced = len(missing_in_csv) == 0 and len(missing_in_glyphs) == 0
        
        if synced:
            message = "CSV is synced with Glyphs file"
        else:
            parts = []
            if missing_in_csv:
                parts.append(f"{len(missing_in_csv)} instance(s) in Glyphs but not in CSV: {', '.join(sorted(missing_in_csv))}")
            if missing_in_glyphs:
                parts.append(f"{len(missing_in_glyphs)} instance(s) in CSV but not in Glyphs: {', '.join(sorted(missing_in_glyphs))}")
            message = "; ".join(parts)
        
        return {
            "synced": synced,
            "message": message,
            "glyphs_instances": sorted(glyphs_instances),
            "csv_instances": sorted(csv_instances),
            "missing_in_csv": sorted(missing_in_csv),
            "missing_in_glyphs": sorted(missing_in_glyphs)
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "synced": False,
            "message": f"Error checking sync status: {str(e)}",
            "glyphs_instances": [],
            "csv_instances": []
        }


def _ensure_spac_column_in_csv(csv_path: Path) -> bool:
    """SPAC support is deferred — see ``add-spac-axis-ufo.py`` removal
    notes. This shim survives as a no-op so the few call sites in
    server.py keep working without an SPAC column being auto-added.
    """
    return False


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
    
    # Don't overwrite existing config
    if config_path.exists():
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
        
        config = {
            "sources": [sources_path],
            "familyName": family_name,
            "fvarInstances": {
                font_filename: fvar_instances
            }
        }
        
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


def _check_spac_axis_in_font(font_path: Path) -> bool:
    """Check if SPAC axis exists in font's fvar table."""
    try:
        font = TTFont(str(font_path))
        if "fvar" not in font:
            return False
        
        fvar = font["fvar"]
        for axis in fvar.axes:
            if axis.axisTag == "SPAC":
                return True
        return False
    except Exception as e:
        print(f"Error checking SPAC axis in font: {e}", file=sys.stderr)
        return False


def _get_spac_axis_range_from_font(font_path: Path) -> Optional[Dict[str, float]]:
    """Get SPAC axis min/max/default values from font's fvar table.
    
    Returns dict with 'min', 'max', 'default' if SPAC axis exists, None otherwise.
    """
    try:
        font = TTFont(str(font_path))
        if "fvar" not in font:
            return None
        
        fvar = font["fvar"]
        for axis in fvar.axes:
            if axis.axisTag == "SPAC":
                return {
                    "min": float(axis.minValue),
                    "max": float(axis.maxValue),
                    "default": float(axis.defaultValue)
                }
        return None
    except Exception as e:
        print(f"Error getting SPAC axis range from font: {e}", file=sys.stderr)
        return None


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
            font, _fmt = _source_font.load_source(GLYPHS_PATH)
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
                    metadata[axis_tag_upper] = {
                        "display_name": axis_name,
                        "registered_tag": axis_tag.lower(),
                        "min": -1000,
                        "max": 1000,
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
        with metadata_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error saving axis metadata: {e}", file=sys.stderr)
        return False


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
        font, _fmt = _source_font.load_source(GLYPHS_PATH)
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
        
        # Add missing instances to CSV
        added_count = 0
        for match in matches:
            if match.get("match_status") == "missing_in_csv":
                instance_name = match["instance_name"]
                glyphs_coords = match.get("glyphs_coordinates", {})
                if _add_missing_instance_to_csv(instance_name, glyphs_coords, csv_path):
                    added_count += 1
        
        # If we added instances, reload matches
        if added_count > 0:
            matches = _csv_io.match_instances(GLYPHS_PATH, csv_path)
        
        return jsonify({
            "instances": matches,
            "csv_path": str(csv_path),
            "added_instances": added_count
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/avar2/mappings', methods=['GET'])
def get_avar2_mappings():
    """Get all avar2 mappings from CSV."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({
                "error": "avar2-mappings.csv not found"
            }), 404
        
        
        rows, fieldnames, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
        
        return jsonify({
            "mappings": rows,
            "fieldnames": fieldnames,
            "traditional_axes": in_cols,
            "parametric_axes": out_cols
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
        font, _fmt = _source_font.load_source(GLYPHS_PATH)
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
            for ax in _control_axes.list_axes(sidecar_path):
                tag_lower = (ax.get("tag") or "").lower()
                if not tag_lower:
                    continue
                existing = by_tag.get(tag_lower)
                # v2.7 unified schema: ``layers`` is the canonical
                # array. Coverage is derived from unique glyph names.
                layers = list(ax.get("layers") or [])
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
        return jsonify({"axes": out})
    except Exception as e:
        print(f"Error in /api/glyph-coverage: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------------------------
# CONTROL AXES sidecar — v2 slice 1 (declaration only)
#
# These endpoints manage the per-source ``<basename>-control.json``
# file: the canonical store for studio-declared control axes. The
# shadow source file + brace-layer authoring + Fontra integration
# arrive in later v2 slices; here we just persist the declarations
# so the UI can show + manage them.
# ----------------------------------------------------------------------


@app.route('/api/control-axes', methods=['GET'])
def list_control_axes():
    """Return the sidecar's control-axis declarations."""
    if ORIGINAL_PATH is None:
        return jsonify({"axes": []})
    try:
        return jsonify({
            "axes": _control_axes.list_axes(ORIGINAL_PATH),
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
    # Control-axis authoring is .glyphs-only for now. On a .designspace
    # source ``regenerate_shadow`` silently returns None, so the axis
    # would land in the sidecar as a no-op slider that can never be
    # authored (no shadow, no "Open in editor"). Reject it up front
    # with a clear message rather than let that dead state accrue.
    if SOURCE_FORMAT and SOURCE_FORMAT != "glyphs":
        return jsonify({
            "error": "Control-axis authoring is only supported for .glyphs "
                     "sources right now. .designspace brace-layer authoring "
                     "(via pooled UFO masters) is not yet implemented.",
        }), 400
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


def _ensure_fontra_running(content_root: Path) -> int:
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

    global FONTRA_PROCESS, FONTRA_CONTENT_ROOT

    content_root = content_root.resolve()

    # Already running at the right root — reuse.
    if (
        FONTRA_PROCESS is not None
        and FONTRA_PROCESS.poll() is None
        and FONTRA_CONTENT_ROOT == content_root
    ):
        return FONTRA_PORT

    # Different root or dead process — kill + restart.
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
_FONTRA_FOCUSED_CSS = """
<style id="avar2-studio-fontra-focus">
  /* Hide every sidebar panel — Fontra is a pure outline canvas.
     avar2-studio seeds and manages the control-axis layers (the
     "Applicable glyphs" section) and navigates Fontra to the exact
     layer to edit via the ↗ button, so none of Fontra's panels are
     needed: not the axis sliders, glyph sources, or source layers
     (designspace-navigation), nor reference-font / glyph-search /
     transformation / notes / related / characters. The layers still
     exist and stay editable at the navigated location.

     NOTE: the accordion SECTIONS inside designspace-navigation (the
     #font-axes / #glyph-sources items) live in a shadow DOM that
     injected global CSS can't reach — so we hide the whole panel by
     its light-DOM container instead. */
  .sidebar-tab[data-sidebar-name="designspace-navigation"],
  .sidebar-tab[data-sidebar-name="reference-font"],
  .sidebar-tab[data-sidebar-name="glyph-search"],
  .sidebar-tab[data-sidebar-name="selection-transformation"],
  .sidebar-tab[data-sidebar-name="glyph-note"],
  .sidebar-tab[data-sidebar-name="related-glyphs"],
  .sidebar-tab[data-sidebar-name="characters-glyphs"],
  .sidebar-content[data-sidebar-name="designspace-navigation"],
  .sidebar-content[data-sidebar-name="reference-font"],
  .sidebar-content[data-sidebar-name="glyph-search"],
  .sidebar-content[data-sidebar-name="selection-transformation"],
  .sidebar-content[data-sidebar-name="glyph-note"],
  .sidebar-content[data-sidebar-name="related-glyphs"],
  .sidebar-content[data-sidebar-name="characters-glyphs"] {
    display: none !important;
  }

  /* Edit tools — hide drawing tools, knife, shapes. Keep:
     pointer-tools (selection + drag), power-ruler-tool
     (measure), metrics-tool (sidebearings — kerning sub-tool
     stays visible inside the group), hand-tool (pan), and the
     entire zoom-tools group. */
  #edit-tools > .tool-button[data-tool="pen-tool"],
  #edit-tools > .tool-button[data-tool="pen-tool-cubic"],
  #edit-tools > .tool-button[data-tool="pen-tool-quad"],
  #edit-tools > .tool-button[data-tool="knife-tool"],
  #edit-tools > .tool-button[data-tool="shape-tool"],
  #edit-tools > .tool-button[data-tool="shape-tool-rectangle"],
  #edit-tools > .tool-button[data-tool="shape-tool-ellipse"],
  .tool-button.multi-tool[data-tool="pen-tool"],
  .tool-button.multi-tool[data-tool="shape-tool"] {
    display: none !important;
  }
</style>
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
    # Inject the focused-UI CSS before </head>; tolerate uppercase.
    for needle in ("</head>", "</HEAD>"):
        if needle in text:
            text = text.replace(needle, _FONTRA_FOCUSED_CSS + needle, 1)
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


@app.route('/api/control-axes/<tag>/open-editor', methods=['POST'])
def open_control_axis_in_editor(tag: str):
    """Spin up Fontra on the shadow folder and return the iframe URL
    the frontend should load. The shadow must exist for this to work
    — the caller is expected to have set coverage already (the
    coverage save in v2.3 regenerates the shadow with seed brace
    layers, which is what Fontra opens to edit).
    """
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400

    if not _control_axes.shadow_exists(ORIGINAL_PATH):
        # No coverage / no shadow yet — there's nothing to edit.
        return jsonify({
            "error": "No shadow file yet. Add coverage glyphs first so the studio can seed brace layers."
        }), 400

    shadow_path = _control_axes.shadow_path_for(ORIGINAL_PATH)
    content_root = shadow_path.parent  # the shadow/ directory

    try:
        port = _ensure_fontra_running(content_root)
    except Exception as exc:
        return jsonify({"error": f"Failed to start Fontra: {exc}"}), 500

    project = shadow_path.name  # e.g. "CrispyMini.glyphs"
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
        "tag": tag.lower(),
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
    global GLYPHS_PATH, VARIABLE_FONT_PATH
    if ORIGINAL_PATH is None:
        return jsonify({"error": "No source loaded"}), 400
    data = request.get_json(silent=True) or {}
    entries = data.get("layers")
    if not isinstance(entries, list):
        return jsonify({"error": "Body must include 'layers' as a list."}), 400
    try:
        stored = _control_axes.set_layers(ORIGINAL_PATH, tag, entries)

        shadow = None
        try:
            shadow = _control_axes.regenerate_shadow(ORIGINAL_PATH)
        except Exception as shadow_exc:
            print(f"Warning: shadow regeneration after layers update failed: {shadow_exc}", file=sys.stderr)

        # Build path: shadow if ANY axis still has layers, else
        # original. Avoids building from an axis-less shadow when the
        # designer empties all layers.
        sidecar_after = _control_axes.list_axes(ORIGINAL_PATH)
        any_layers = any(ax.get("layers") for ax in sidecar_after)
        if shadow is not None and any_layers:
            GLYPHS_PATH = shadow
        else:
            GLYPHS_PATH = ORIGINAL_PATH
        VARIABLE_FONT_PATH = None
        try:
            trigger_build()
        except Exception as build_exc:
            print(f"Warning: rebuild after layers update failed: {build_exc}", file=sys.stderr)

        return jsonify({"success": True, "tag": tag.lower(), "layers": stored})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        print(f"Error setting control-axis layers: {e}", file=sys.stderr)
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
            _control_axes.regenerate_shadow(ORIGINAL_PATH)
            VARIABLE_FONT_PATH = None
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
            remaining = _control_axes.list_axes(ORIGINAL_PATH)
            if remaining:
                shadow = _control_axes.regenerate_shadow(ORIGINAL_PATH)
                if shadow is not None and any(ax.get("layers") for ax in remaining):
                    GLYPHS_PATH = shadow
                else:
                    GLYPHS_PATH = ORIGINAL_PATH
            else:
                _control_axes.remove_shadow(ORIGINAL_PATH)
                GLYPHS_PATH = ORIGINAL_PATH
            VARIABLE_FONT_PATH = None
            try:
                trigger_build()
            except Exception as build_exc:
                print(f"Warning: rebuild after delete failed: {build_exc}", file=sys.stderr)
        except Exception as shadow_exc:
            print(f"Warning: shadow refresh after delete failed: {shadow_exc}", file=sys.stderr)

        return jsonify({"success": True, "tag": tag.lower()})
    except Exception as e:
        print(f"Error deleting control axis: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


@app.route('/api/avar2/axes', methods=['GET'])
def get_avar2_axes():
    """Get traditional axes (in:) and parametric axes (out:) from CSV, including metadata."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({
                "error": "avar2-mappings.csv not found"
            }), 404
        
        
        _, _, in_cols, out_cols, _ = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
        
        # Normalize traditional axis names
        traditional_axes = [_csv_io.normalize_in_axis_name(col) for col in in_cols]
        
        # Load metadata
        metadata = _load_axis_metadata()
        
        # Get parametric axes from the source file and populate metadata.
        glyphs_axes_info = {}
        if GLYPHS_PATH and GLYPHS_PATH.exists():
            try:
                font, _fmt = _source_font.load_source(GLYPHS_PATH)
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
                metadata[col] = {
                    "display_name": default_display_name,
                    "registered_tag": normalized_tag,
                    "min": -1000,
                    "max": 1000,
                    "is_parametric": False  # Mark as traditional (not in Glyphs file)
                }
                metadata_updated = True
            else:
                # Ensure is_parametric flag exists for existing entries
                if "is_parametric" not in metadata[col]:
                    # Check if it's actually parametric (exists in Glyphs)
                    normalized_tag = _csv_io.normalize_in_axis_name(col)
                    glyphs_axis_tags = _get_glyphs_axis_tags()
                    metadata[col]["is_parametric"] = normalized_tag in glyphs_axis_tags
                    metadata_updated = True
                
                # Update display_name if it's still the column name (tag) - migrate to proper display name
                normalized_tag = _csv_io.normalize_in_axis_name(col)
                if metadata[col].get("display_name") == col and normalized_tag.lower() in default_display_names:
                    metadata[col]["display_name"] = default_display_names[normalized_tag.lower()]
                    metadata_updated = True
            
            axes_with_metadata[col] = metadata[col]
        
        # Save updated metadata if we added any new axes
        if metadata_updated:
            _save_axis_metadata(metadata)

        # Ensure every axis entry has a numeric `default` so the frontend
        # can initialise sliders without hardcoding values.
        TRADITIONAL_DEFAULTS = {
            "wght": 400.0,
            "wdth": 100.0,
            "opsz": 72.0,
            "cntr": 0.0,
            "slnt": 0.0,
            "ital": 0.0,
            "grad": 0.0,
            "spac": 0.0,
        }
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

        # Source-file delete fires for default + source_only. CSV-only
        # skips it (the row was never in the source).
        if touches_source:
            glyphs_deleted = delete_instance_in_glyphs(GLYPHS_PATH, instance_name)
            if not glyphs_deleted:
                return jsonify({"error": f"Failed to delete instance '{instance_name}' from source file"}), 500

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
        
        # Trigger rebuild in background thread (same pattern as update_instance)
        def rebuild_in_background():
            global BUILDING
            if BUILDING:
                print("Build already in progress, skipping rebuild after instance deletion...", file=sys.stderr)
                return
            
            spac_font_dir = _get_spac_font_dir()
            if spac_font_dir:
                spac_font_path = spac_font_dir / "Crispy-SPAC-VF.ttf"
                if spac_font_path.exists():
                    # SPAC font exists - regenerate it using add-spac-axis-ufo.py
                    print(f"Instance deleted, regenerating SPAC font...", file=sys.stderr)
                    BUILDING = True
                    try:
                        if _regenerate_spac_font():
                            # SPAC regeneration succeeded
                            BUILDING = False
                        else:
                            # Fallback to regular build if regeneration fails
                            print(f"SPAC regeneration failed, falling back to regular build...", file=sys.stderr)
                            BUILDING = False  # Reset before trigger_build (which sets it)
                            trigger_build()
                    except Exception as e:
                        print(f"Error during SPAC font regeneration: {e}", file=sys.stderr)
                        BUILDING = False
                else:
                    # No SPAC font - rebuild regular font
                    print(f"Instance deleted, triggering immediate rebuild...", file=sys.stderr)
                    trigger_build()
            else:
                # No SPAC font directory - rebuild regular font
                print(f"Instance deleted, triggering immediate rebuild...", file=sys.stderr)
                trigger_build()
        
        # Start rebuild in background thread (small delay to ensure file save completes)
        rebuild_thread = threading.Thread(target=rebuild_in_background, daemon=True)
        rebuild_thread.start()
        
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

@app.route('/api/avar2/sync-csv', methods=['POST'])
def sync_csv():
    """Update CSV parametric values to match source file."""
    try:
        csv_path = _get_avar2_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "avar2-mappings.csv not found"}), 404

        ok = _csv_io.update_csv_from_glyphs(
            GLYPHS_PATH,
            csv_path,
            skip_instances=set(EDITING_INSTANCES),
        )
        if not ok:
            return jsonify({"error": "Failed to sync CSV"}), 500

        return jsonify({
            "success": True,
            "csv_path": str(csv_path),
            "skipped_instances": list(EDITING_INSTANCES),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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
                "error": "CSV was modified externally. Please reload.",
                "reload_required": True
            }), 409
        
        data = request.get_json()
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
        
        # Check for duplicate registered tags in existing metadata
        metadata = _load_axis_metadata()
        for existing_axis, existing_meta in metadata.items():
            if existing_meta.get("registered_tag") == registered_tag and existing_axis != axis_name_normalized:
                return jsonify({"error": f"Registered tag '{registered_tag}' already used by axis '{existing_axis}'"}), 400
        
        # Read CSV using normalized function
        
        rows, fieldnames, _, _, fieldname_mapping = _csv_io.read_csv_mappings_with_axes(csv_path, GLYPHS_PATH)
        
        # Normalize axis_name to uppercase for consistency
        axis_name_normalized = axis_name.upper()
        
        # Check if axis already exists (case-insensitive)
        if axis_name_normalized in fieldnames:
            return jsonify({"error": f"Axis '{axis_name}' already exists"}), 400
        
        # Add new column to CSV (use normalized name)
        fieldnames.append(axis_name_normalized)
        for row in rows:
            row[axis_name_normalized] = str(default_value)
        
        # Write updated CSV (use normalized fieldnames)
        import csv
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
                "error": "CSV was modified externally. Please reload.",
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
        
        data = request.get_json()
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
            return jsonify({"error": f"Axis '{axis_name}' not found in CSV"}), 404
        
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
        
        # Ensure is_parametric flag is preserved (don't allow changing it via edit)
        # It should already be set correctly from initialization
        
        # If axis_name was different case, update metadata key
        if axis_name_normalized != axis_name and axis_name in metadata:
            del metadata[axis_name]
        
        metadata[axis_name_normalized] = current_meta
        _save_axis_metadata(metadata)
        
        # Write CSV back (use normalized fieldnames)
        import csv
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
                "error": "CSV was modified externally. Please reload.",
                "reload_required": True
            }), 409
        
        data = request.get_json()
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
            return jsonify({"error": f"Axis '{axis_name}' not found in CSV"}), 404
        
        # Find and update the instance
        instance_found = False
        for row in rows:
            if row.get("Instance Name", "").strip() == instance_name:
                row[axis_name_normalized] = str(float_value)
                instance_found = True
                break
        
        if not instance_found:
            return jsonify({"error": f"Instance '{instance_name}' not found in CSV"}), 404
        
        # Write updated CSV (use normalized fieldnames)
        import csv
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


@app.route('/api/spacing/init', methods=['POST'])
def init_spac_axis():
    """Initialize SPAC axis: add SPAC column to CSV (all instances = 0) and update config.yaml."""
    try:
        csv_path = _get_preview_csv_path()
        config_path = _get_preview_config_path()
        if not csv_path or not config_path:
            return jsonify({"error": "Preview CSV or config not found"}), 404
        
        # Read CSV
        rows = []
        fieldnames = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            for row in reader:
                rows.append(row)
        
        # Check if SPAC column already exists
        if "SPAC" in fieldnames:
            return jsonify({
                "success": True,
                "message": "SPAC column already exists",
                "initialized": False
            })
        
        # Add SPAC column with 0 for all instances
        fieldnames.append("SPAC")
        for row in rows:
            row["SPAC"] = "0"
        
        # Write updated CSV
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        # Update modification time cache to prevent false "external edit" detection
        _update_csv_modification_time(csv_path)
        
        # Update config.yaml to add spacingAxis section
        if yaml:
            with config_path.open("r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            
            config["spacingAxis"] = {
                "min": -100,
                "max": 100
            }
            
            with config_path.open("w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        
        return jsonify({
            "success": True,
            "initialized": True,
            "message": "SPAC axis initialized"
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/spacing/check', methods=['GET'])
def check_spac_axis():
    """Check if SPAC axis exists in designspace font and return its range."""
    try:
        # Check designspace-generated font first (correct range 0-100)
        spac_font_dir = _get_spac_font_dir()
        if spac_font_dir:
            spac_font_path = spac_font_dir / "Crispy-SPAC-VF.ttf"
            if spac_font_path.exists():
                has_spac = _check_spac_axis_in_font(spac_font_path)
                spac_range = None
                if has_spac:
                    spac_range = _get_spac_axis_range_from_font(spac_font_path)
                
                result = {
                    "exists": has_spac,
                    "font_path": str(spac_font_path)
                }
                if spac_range:
                    result["range"] = spac_range
                
                return jsonify(result)
        
        # Fallback to preview font directory
        preview_font_dir = _get_preview_font_dir()
        if not preview_font_dir:
            return jsonify({"exists": False, "error": "Preview font directory not found"}), 404
        
        # Look for preview font files, prefer one with SPAC in filename
        font_files = list(preview_font_dir.glob("*.ttf"))
        if not font_files:
            return jsonify({"exists": False})
        
        # Prefer font with SPAC in filename, otherwise use most recent
        spac_font = None
        for font_file in font_files:
            if "SPAC" in font_file.name:
                spac_font = font_file
                break
        
        if not spac_font:
            spac_font = max(font_files, key=lambda p: p.stat().st_mtime)
        
        has_spac = _check_spac_axis_in_font(spac_font)
        spac_range = None
        if has_spac:
            spac_range = _get_spac_axis_range_from_font(spac_font)
        
        result = {
            "exists": has_spac,
            "font_path": str(spac_font)
        }
        if spac_range:
            result["range"] = spac_range
        
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/spacing/values', methods=['GET'])
def get_spac_values():
    """Get SPAC values for all instances from preview CSV.
    
    Handles duplicates by keeping the last value for each instance name.
    """
    try:
        csv_path = _get_preview_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "Preview CSV not found"}), 404
        
        # Use dict to handle duplicates - last value wins
        instance_spac_map = {}
        duplicate_instances = set()
        
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                instance_name = row.get("Instance Name", "").strip()
                spac_value = row.get("SPAC", "0").strip()
                try:
                    spac_float = float(spac_value) if spac_value else 0.0
                except ValueError:
                    spac_float = 0.0
                
                # Track duplicates
                if instance_name in instance_spac_map:
                    duplicate_instances.add(instance_name)
                
                # Last value wins (overwrites previous)
                instance_spac_map[instance_name] = spac_float
        
        # Warn about duplicates (only if significant)
        if duplicate_instances and len(duplicate_instances) > 0:
            print(f"Warning: Found duplicate rows in CSV for {len(duplicate_instances)} instance(s)", file=sys.stderr)
        
        # Convert to list format
        rows = [
            {"instance_name": name, "spac": value}
            for name, value in instance_spac_map.items()
        ]
        
        return jsonify({"values": rows})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/spacing/instance/<instance_name>', methods=['PUT'])
def update_spac_value(instance_name: str):
    """Update SPAC value for a specific instance."""
    try:
        csv_path = _get_preview_csv_path()
        if not csv_path or not csv_path.exists():
            return jsonify({"error": "Preview CSV not found"}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        new_value = data.get("value")
        if new_value is None:
            return jsonify({"error": "value is required"}), 400
        
        # Validate value
        try:
            float_value = float(new_value)
            if float_value < -100 or float_value > 100:
                return jsonify({"error": "SPAC value must be between -100 and 100"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "value must be a number"}), 400
        
        # Read CSV
        rows = []
        fieldnames = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames) if reader.fieldnames else []
            for row in reader:
                rows.append(row)
        
        # Ensure SPAC column exists
        if "SPAC" not in fieldnames:
            fieldnames.append("SPAC")
            for row in rows:
                if "SPAC" not in row:
                    row["SPAC"] = "0"
        
        # Find and update instance
        instance_found = False
        for row in rows:
            if row.get("Instance Name", "").strip() == instance_name:
                row["SPAC"] = str(float_value)
                instance_found = True
                break
        
        if not instance_found:
            return jsonify({"error": f"Instance '{instance_name}' not found"}), 404
        
        # Write updated CSV
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        # Update modification time cache to prevent false "external edit" detection
        _update_csv_modification_time(csv_path)
        
        # Update config.yaml spacingAxis min/max based on all SPAC values
        _update_spacing_axis_range()
        
        return jsonify({
            "success": True,
            "instance_name": instance_name,
            "spac": float_value
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _update_spacing_axis_range():
    """Update config.yaml spacingAxis min/max to always be -100 to 100."""
    if not yaml:
        return
    
    csv_path = _get_preview_csv_path()
    config_path = _get_preview_config_path()
    if not csv_path or not csv_path.exists() or not config_path:
        return
    
    try:
        # Read config
        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        
        # Always set spacingAxis to -100 to 100 (full range)
        config["spacingAxis"] = {
            "min": -100,
            "max": 100
        }
        
        # Write config back
        with config_path.open("w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as e:
        print(f"Error updating spacing axis range: {e}", file=sys.stderr)


@app.route('/api/spacing/rebuild', methods=['POST'])
def rebuild_preview_font_with_spac():
    """Rebuild preview font with SPAC axis using minimal config and fontc."""
    global BUILDING
    
    if BUILDING:
        return jsonify({"error": "Build already in progress"}), 409
    
    try:
        config_path = _get_preview_config_path()
        preview_font_dir = _get_preview_font_dir()
        if not config_path or not preview_font_dir:
            return jsonify({"error": "Preview config or font directory not found"}), 404
        
        if not USE_FONTC:
            return jsonify({"error": "fontc is required for preview font rebuild"}), 400
        
        # Check if fontc is available
        fontc_path = shutil.which("fontc")
        if not fontc_path:
            return jsonify({"error": "fontc not found in PATH"}), 400
        
        BUILDING = True
        
        # Build preview font directly with fontc, then add SPAC axis
        # Skip fix, BuildSTAT, and buildFvarInstances steps
        project_root = GLYPHS_PATH.parent.parent if GLYPHS_PATH else Path.cwd()
        
        # Step 1: Build variable font with fontc (no post-processing)
        temp_font = tempfile.NamedTemporaryFile(suffix=".ttf", delete=False)
        temp_font_path = Path(temp_font.name)
        temp_font.close()
        
        fontc_cmd = [
            fontc_path,
            "--output-file", str(temp_font_path),
            str(GLYPHS_PATH.resolve()),
            "--flatten-components",
            "--decompose-transformed-components"
        ]
        
        fontc_result = subprocess.run(
            fontc_cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if fontc_result.returncode != 0:
            BUILDING = False
            if temp_font_path.exists():
                temp_font_path.unlink()
            return jsonify({
                "error": "fontc build failed",
                "details": fontc_result.stderr
            }), 500
        
        if not temp_font_path.exists():
            BUILDING = False
            return jsonify({"error": "fontc did not produce output file"}), 500
        
        # Step 2: Add SPAC axis using gftools-gen-spac
        # Get spacingAxis min/max from config
        spacing_min = -100
        spacing_max = 100
        if yaml and config_path.exists():
            try:
                with config_path.open("r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                    if "spacingAxis" in config:
                        spacing_min = config["spacingAxis"].get("min", -100)
                        spacing_max = config["spacingAxis"].get("max", 100)
            except Exception as e:
                print(f"Warning: Could not read spacingAxis from config: {e}", file=sys.stderr)
        
        # Step 2: Add SPAC axis using gftools-gen-spac (inplace to temp file, then copy)
        # Use --inplace to modify temp file directly
        spac_cmd = [
            "gftools-gen-spac",
            "--inplace",
            str(temp_font_path),
            str(int(spacing_min)),
            str(int(spacing_max))
        ]
        
        spac_result = subprocess.run(
            spac_cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        BUILDING = False
        
        if spac_result.returncode != 0:
            if temp_font_path.exists():
                temp_font_path.unlink()
            return jsonify({
                "error": "SPAC axis addition failed",
                "details": spac_result.stderr
            }), 500
        
        # Generate preview font filename with SPAC
        family_name = GLYPHS_PATH.stem
        preview_font_path = preview_font_dir / f"{family_name}[SPAC].ttf"
        
        # Copy temp font to preview directory
        shutil.copy2(temp_font_path, preview_font_path)
        
        # Clean up temp file
        if temp_font_path.exists():
            temp_font_path.unlink()
        
        final_font = preview_font_path
        
        has_spac = _check_spac_axis_in_font(final_font)
        
        return jsonify({
            "success": True,
            "font_path": str(final_font),
            "has_spac": has_spac,
            "output": f"fontc: {fontc_result.stdout}\ngftools-gen-spac: {spac_result.stdout}"
        })
    except subprocess.TimeoutExpired:
        BUILDING = False
        return jsonify({"error": "Build timeout"}), 500
    except Exception as e:
        BUILDING = False
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/check-sync-status', methods=['GET'])
def check_sync_status():
    """Check if preview CSV is synced with Glyphs file."""
    try:
        status = _check_preview_csv_sync_status()
        return jsonify(status)
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


def _perform_avar2_build(check_sync: bool = True) -> Dict:
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

    if check_sync:
        sync_status = _check_preview_csv_sync_status()
        if not sync_status.get("synced", False):
            return _record_build_failure({
                "success": False,
                "error": "CSV is not synced with Glyphs file",
                "details": sync_status.get("message", "Unknown sync error"),
                "sync_status": sync_status,
            })

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
            config_generator.update_config(csv_path=preview_csv, config_path=config_to_update, backup=False)
        except Exception as e:
            return _record_build_failure({"success": False, "error": "Failed to update config", "details": str(e)})

        fontc_path = shutil.which("fontc")
        if not fontc_path:
            return _record_build_failure({"success": False, "error": "fontc not found in PATH"})

        builder_cmd = ["gftools", "builder", "--experimental-fontc", fontc_path, str(config_to_update.resolve())]
        result = subprocess.run(builder_cmd, capture_output=True, text=True, cwd=str(workdir))
        if result.returncode != 0:
            return _record_build_failure({
                "success": False,
                "error": "Font build failed",
                "details": (result.stderr or result.stdout or "No error details")[:1000],
            })

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

        try:
            if project_fonts_dir.exists() and not any(project_fonts_dir.iterdir()):
                project_fonts_dir.rmdir()
            parent_fonts = project_fonts_dir.parent
            if parent_fonts.exists() and not any(parent_fonts.iterdir()):
                parent_fonts.rmdir()
        except OSError:
            pass

        VARIABLE_FONT_PATH = font_file
        LAST_BUILD_TIME = time.time()
        LAST_BUILD_STATUS = "ok"
        LAST_BUILD_ERROR = None
        return {"success": True, "font_path": str(font_file)}

    finally:
        BUILDING = False


@app.route('/api/build-avar2', methods=['POST'])
def build_avar2_font():
    """Build avar2 font from preview CSV with selected axes."""
    try:
        data = request.get_json(silent=True) or {}

        # Selected-axes filtering is not yet wired through — params accepted
        # for compatibility with the existing client.
        result = _perform_avar2_build(check_sync=True)
        if not result.get("success"):
            status = 409 if result.get("error") == "Build already in progress" else 500
            payload = {k: v for k, v in result.items() if k != "success"}
            return jsonify(payload), status

        return jsonify({
            "success": True,
            "font_path": result["font_path"],
            "message": "Avar2 font built successfully",
            "sync_status": _check_preview_csv_sync_status(),
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

    # CONTROL AXES — keep the shadow in sync on load. If any axis has
    # brace layers (= the shadow carries real per-glyph deltas), swap
    # the active build path to the shadow so the compiled font
    # actually carries the new axes. Axes with no layers live dormant
    # in the sidecar; the build stays on the original since the shadow
    # would compile to the same thing.
    #
    # Gate on ``layers`` (the flat {glyph, location} list), NOT the
    # legacy ``coverage`` key the current sidecar never emits — the
    # old gate meant a restart left the build on the original and
    # dropped authored brace deltas from the preview until the user
    # re-saved a layer.
    try:
        sidecar_axes = _control_axes.list_axes(ORIGINAL_PATH)
        if sidecar_axes:
            shadow = _control_axes.regenerate_shadow(ORIGINAL_PATH)
            if shadow is not None and any(ax.get("layers") for ax in sidecar_axes):
                GLYPHS_PATH = shadow
    except Exception as exc:
        print(f"Warning: failed to regenerate control-axes shadow on load: {exc}", file=sys.stderr)

    _initialize_preview_csv_from_glyphs()
    _initialize_preview_config_from_glyphs()

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
        # Multipart upload branch.
        uploaded = list(request.files.values())
        if uploaded:
            workspace = Path.home() / ".avar2-studio" / "workspace" / "uploaded"
            # Wipe the workspace so a re-upload starts fresh. Otherwise
            # a previous upload's sibling CSV/metadata would leak into
            # the new source's view (the same leakage class we fixed
            # for built-in example swaps).
            import shutil
            if workspace.exists():
                shutil.rmtree(workspace)
            workspace.mkdir(parents=True, exist_ok=True)

            glyphs_dest: Optional[Path] = None
            csv_dest: Optional[Path] = None
            metadata_dest: Optional[Path] = None
            extras_skipped: list = []

            for f in uploaded:
                name = (f.filename or '').strip()
                if not name:
                    continue
                lower = name.lower()
                if lower.endswith('.glyphs'):
                    if glyphs_dest is not None:
                        return jsonify({"error": "Upload one .glyphs file at a time (got multiple)."}), 400
                    glyphs_dest = workspace / name
                    f.save(str(glyphs_dest))
                elif lower.endswith('-avar.csv') or lower == 'avar2-mappings.csv':
                    # Defer destination naming until we know the .glyphs
                    # basename. Save to a temp name first.
                    tmp = workspace / f".__pending_csv__{name}"
                    f.save(str(tmp))
                    csv_dest = tmp
                elif lower == 'avar2-axis-metadata.json' or lower.endswith('-axis-metadata.json'):
                    # axis-metadata.json lives in the per-project workdir
                    # (.avar2-studio/), not next to the .glyphs file.
                    workdir = workspace / ".avar2-studio"
                    workdir.mkdir(parents=True, exist_ok=True)
                    metadata_dest = workdir / "axis-metadata.json"
                    f.save(str(metadata_dest))
                else:
                    extras_skipped.append(name)

            if glyphs_dest is None:
                return jsonify({
                    "error": "No .glyphs file in the upload. Required: one .glyphs source. "
                             "Optional: a sibling -avar.csv and/or avar2-axis-metadata.json."
                }), 400

            # Now that we know the .glyphs basename, rename the pending
            # CSV to ``<basename>-avar.csv`` so _get_avar2_csv_path
            # finds it via the standard sibling-lookup rule.
            if csv_dest is not None:
                final_csv = workspace / f"{glyphs_dest.stem}-avar.csv"
                csv_dest.replace(final_csv)
                csv_dest = final_csv

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
    global GLYPHS_PATH, SOURCE_FORMAT, BUILD_DIR, CSV_PATH

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
        help="Path to avar2-mappings.csv (default: same directory as Glyphs file)"
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

        # Honour the explicit CLI overrides AFTER _apply_source_path has
        # set sensible defaults — the user's --build-dir / --csv beat
        # the auto-derived locations.
        if args.build_dir:
            BUILD_DIR = args.build_dir.resolve()
        if args.csv:
            CSV_PATH = args.csv.resolve()
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
            )
            if ok:
                skipped_msg = (
                    f" (skipped {len(EDITING_INSTANCES)} editing instances)"
                    if EDITING_INSTANCES else ""
                )
                print(f"CSV synced with source file{skipped_msg}", file=sys.stderr)
            else:
                print("Warning: CSV sync returned no-op", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not sync CSV: {e}", file=sys.stderr)
    
    class GlyphsFileHandler(FileSystemEventHandler):
        """Watchdog handler for real-time Glyphs file changes."""
        
        def __init__(self):
            self.last_modified = 0
            self.debounce_interval = 0.5  # seconds
        
        def on_modified(self, event):
            """Handle file modification events."""
            if event.is_directory:
                return
            
            # Only process our Glyphs file
            if Path(event.src_path).resolve() != GLYPHS_PATH.resolve():
                return
            
            # Debounce rapid saves
            current_time = time.time()
            if current_time - self.last_modified < self.debounce_interval:
                return
            self.last_modified = current_time
            
            global BUILDING, VARIABLE_FONT_PATH, LAST_BUILD_TIME
            
            if BUILDING:
                return
            
            try:
                print(f"\nGlyphs file modified, syncing CSV and rebuilding...", file=sys.stderr)
                
                # Sync CSV first (skips editing instances)
                sync_csv_with_glyphs()
                
                # Rebuild font
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
        observer.schedule(event_handler, path=str(GLYPHS_PATH.parent), recursive=False)
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
            if BUILDING:
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
