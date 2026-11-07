"""Load routes (audit §3 step e group) — source loading at runtime:
built-in example fixtures, project-zip upload, multipart staging,
``/api/load-source`` itself."""

import tempfile
import time
from pathlib import Path
from typing import Optional

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from .. import csv_io as _csv_io
from .. import server
from .. import watch as _watch
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("load", __name__)

# ---------------------------------------------------------------------------
# Built-in example fixtures + late-binding source loader
# ---------------------------------------------------------------------------

# Repo layout: src/avar2_studio/routes/load.py → repo root is one more parent up.
# When the package is installed from PyPI the examples directory will NOT be
# present; the endpoint reports an empty list in that case and the frontend
# hides the built-in examples section.
_REPO_ROOT_FOR_EXAMPLES = Path(__file__).resolve().parent.parent.parent.parent
from ..examples import _BUILTIN_EXAMPLES


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


@bp.route('/api/examples', methods=['GET'])
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

        server._apply_source_path(source_dest)
        _watch.WATCHER.reschedule()
        return jsonify({
            "success": True,
            "path": str(source_dest),
            "attached": sorted(set(attached)),
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@bp.route('/api/load-source', methods=['POST'])
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

            server._apply_source_path(glyphs_dest)
            _watch.WATCHER.reschedule()
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
        server._apply_source_path(staged)
        _watch.WATCHER.reschedule()
        return jsonify({"success": True, "path": str(staged), "example": example_id})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.error(f"Error in /api/load-source: {exc}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500
