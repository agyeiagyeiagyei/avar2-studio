"""Build worker — a fresh interpreter per job, so the Flask process never
runs fontTools-heavy build code (the GIL-starvation fix; see
docs/decisions/0003-worker-subprocess-finish-then-rebuild.md).

    python -m avar2_studio.build_worker <spec.json>

The spec points at a SNAPSHOT of the source set (the server stages it per
job), so a mid-build save in Glyphs can't tear the worker's inputs. The
worker imports the pipeline module — Flask-free, so no app or supervisor
thread is constructed here — and runs the very same pipeline the
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

from . import state


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


def _apply_result(result):
    """Snapshot the shared build-state globals into the result."""
    for name in ("LAST_BUILD_STATUS", "LAST_BUILD_ERROR", "LAST_AVAR2_ERROR",
                 "LAST_ORIGIN_RELOCATION_ERROR", "LAST_ORIGIN_RELOCATION_MEMO",
                 "LAST_ORIGIN_AUTHORED_AVAR",
                 "LAST_GRADE_ERROR", "LAST_SOURCE_TRANSFORM_ERROR",
                 "LAST_TRANSFORM_ERROR", "LAST_ORIGIN_WARNING", "LAST_BUILD_TIME"):
        result[name.lower()] = getattr(state, name, None)


def _bind_server_globals(spec):
    """Point the shared path globals at the job snapshot, exactly
    as _apply_source_path would after loading a source."""
    src = Path(spec["original_path"])
    state.ORIGINAL_PATH = src
    state.GLYPHS_PATH = src
    state.BUILD_DIR = Path(spec["job_dir"]) / "build"
    state.RELOCATION_MEMO_DIR = (Path(spec["memo_dir"])
                                 if spec.get("memo_dir") else None)
    state.USE_FONTC = bool(spec.get("use_fontc", True))
    # Lazy getters derive everything (preview dir, CSV, config, build dir)
    # from ORIGINAL_PATH — clear any memoized values so nothing leaks a
    # server-side path in.
    state.PREVIEW_DIR = None
    state.PREVIEW_CSV_PATH = None
    state.PREVIEW_CONFIG_PATH = None
    state.CSV_PATH = None
    for var, val in (spec.get("env") or {}).items():
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = str(val)


def _run_build_job(pipeline, spec, progress):
    _bind_server_globals(spec)
    progress.phase("resolve")
    pipeline._resolve_active_source()
    progress.phase("build")
    ok = pipeline._run_build()
    return {"success": bool(ok),
            "built_font": str(state.VARIABLE_FONT_PATH) if state.VARIABLE_FONT_PATH else None}


def _run_export_job(pipeline, spec, progress):
    _bind_server_globals(spec)
    current = Path(spec["current_font"])
    state.VARIABLE_FONT_PATH = current
    state.GLYPHS_PATH = Path(spec["glyphs_path"])
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    progress.phase("export")
    artifact = pipeline._export_pipeline(
        current,
        hidden_axes=spec.get("hidden_axes") or [],
        default_location=spec.get("default_location"),
        export_dir=out_dir,
        format=spec.get("format", "ttf"),
    )
    return {"success": True, "artifact": str(artifact),
            "artifact_name": Path(artifact).name}


def _run_compress_job(pipeline, spec, progress):
    font_path = Path(spec["font_path"])
    progress.phase("compress")
    out = Path(spec["out_dir"]) / (font_path.stem + ".woff2")
    out.parent.mkdir(parents=True, exist_ok=True)
    pipeline._save_woff2(font_path, out)
    return {"success": True, "artifact": str(out), "artifact_name": out.name,
            "woff2_bytes": out.stat().st_size, "source_bytes": font_path.stat().st_size}


def main(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    progress = _Progress(spec["progress_path"])
    result = {"job_id": spec["job_id"], "kind": spec["kind"], "success": False}
    try:
        # Imported here so a failure lands in the result, not on the floor.
        from avar2_studio import pipeline
        sys.stderr = progress
        progress.emit(job_id=spec["job_id"], kind=spec["kind"], phase="start")
        handler = {"build": _run_build_job, "export": _run_export_job,
                   "compress": _run_compress_job}[spec["kind"]]
        result.update(handler(pipeline, spec, progress))
        _apply_result(result)
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
