"""Build worker — a fresh interpreter per job, so the Flask process never
runs fontTools-heavy build code (the GIL-starvation fix; see
docs/WORKER_BUILD_PLAN.md).

    python -m avar2_studio.build_worker <spec.json>

The spec points at a SNAPSHOT of the source set (the server stages it per
job), so a mid-build save in Glyphs can't tear the worker's inputs. The
worker imports the server module and runs the very same pipeline the
in-process build ran — parity by construction — writing line-delimited
JSON progress to ``progress_path`` and a single ``result.json``
(atomically renamed) at the end. Only the job directory is written;
sidecars and shared build outputs stay server-owned.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path


class _Progress:
    """Line-delimited JSON progress, plus a stderr tee so pipeline prints
    land in the same file as {"log": ...} entries."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.t0 = time.time()

    def emit(self, **fields):
        fields["elapsed"] = round(time.time() - self.t0, 2)
        with open(self.path, "a") as f:
            f.write(json.dumps(fields) + "\n")

    def phase(self, name):
        self.emit(phase=name)

    # file-like for sys.stderr
    def write(self, text):
        for line in text.splitlines():
            if line.strip():
                self.emit(log=line)

    def flush(self):
        pass


def _apply_result(server, result):
    """Snapshot the server module's build-state globals into the result."""
    for name in ("LAST_BUILD_STATUS", "LAST_BUILD_ERROR", "LAST_AVAR2_ERROR",
                 "LAST_GRADE_ERROR", "LAST_SOURCE_TRANSFORM_ERROR",
                 "LAST_TRANSFORM_ERROR", "LAST_ORIGIN_WARNING", "LAST_BUILD_TIME"):
        result[name.lower()] = getattr(server, name, None)


def _bind_server_globals(server, spec):
    """Point the server module's path globals at the job snapshot, exactly
    as _apply_source_path would after loading a source."""
    src = Path(spec["original_path"])
    server.ORIGINAL_PATH = src
    server.GLYPHS_PATH = src
    server.BUILD_DIR = Path(spec["job_dir"]) / "build"
    server.USE_FONTC = bool(spec.get("use_fontc", True))
    # Lazy getters derive everything (preview dir, CSV, config, build dir)
    # from ORIGINAL_PATH — clear any memoized values so nothing leaks a
    # server-side path in.
    server.PREVIEW_DIR = None
    server.PREVIEW_CSV_PATH = None
    server.PREVIEW_CONFIG_PATH = None
    server.CSV_PATH = None
    for var, val in (spec.get("env") or {}).items():
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = str(val)


def _run_build_job(server, spec, progress):
    _bind_server_globals(server, spec)
    progress.phase("resolve")
    server._resolve_active_source()
    progress.phase("build")
    ok = server._run_build()
    return {"success": bool(ok),
            "built_font": str(server.VARIABLE_FONT_PATH) if server.VARIABLE_FONT_PATH else None}


def _run_export_job(server, spec, progress):
    _bind_server_globals(server, spec)
    current = Path(spec["current_font"])
    server.VARIABLE_FONT_PATH = current
    server.GLYPHS_PATH = Path(spec["glyphs_path"])
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    progress.phase("export")
    artifact = server._export_pipeline(
        current,
        hidden_axes=spec.get("hidden_axes") or [],
        default_location=spec.get("default_location"),
        export_dir=out_dir,
        format=spec.get("format", "ttf"),
    )
    return {"success": True, "artifact": str(artifact),
            "artifact_name": Path(artifact).name}


def _run_compress_job(server, spec, progress):
    font_path = Path(spec["font_path"])
    progress.phase("compress")
    out = Path(spec["out_dir"]) / (font_path.stem + ".woff2")
    out.parent.mkdir(parents=True, exist_ok=True)
    server._save_woff2(font_path, out)
    return {"success": True, "artifact": str(out), "artifact_name": out.name,
            "woff2_bytes": out.stat().st_size, "source_bytes": font_path.stat().st_size}


def main(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    progress = _Progress(spec["progress_path"])
    result = {"job_id": spec["job_id"], "kind": spec["kind"], "success": False}
    try:
        # Imported here so a failure lands in the result, not on the floor.
        from avar2_studio import server
        sys.stderr = progress
        progress.emit(job_id=spec["job_id"], kind=spec["kind"], phase="start")
        handler = {"build": _run_build_job, "export": _run_export_job,
                   "compress": _run_compress_job}[spec["kind"]]
        result.update(handler(server, spec, progress))
        _apply_result(server, result)
    except Exception as exc:
        result["success"] = False
        result["error"] = str(exc)
        result["details"] = traceback.format_exc()[-2000:]
    result["duration"] = round(time.time() - progress.t0, 2)
    out = Path(spec["result_path"])
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result))
    os.replace(tmp, out)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
