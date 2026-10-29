"""Stub build worker for the job-queue tests — no fontc, no fontTools.

Reads the spec, writes one progress line, then follows STUB_WORKER_MODE:
``sleep`` stalls (cancellation/coalescing tests), ``die`` exits 1 with no
result (dead-worker test), otherwise writes a fake artifact and a success
result for the job kind.
"""
import json
import os
import sys
import time
from pathlib import Path

spec = json.loads(open(sys.argv[1]).read())
Path(spec["progress_path"]).write_text(json.dumps({"phase": "stub", "elapsed": 0}) + "\n")
mode = os.environ.get("STUB_WORKER_MODE", "ok")
if mode == "die":
    os._exit(1)
if mode == "sleep":
    time.sleep(float(os.environ.get("STUB_WORKER_SLEEP", "60")))
out = Path(spec["out_dir"])
out.mkdir(parents=True, exist_ok=True)
result = {"job_id": spec["job_id"], "kind": spec["kind"],
          "success": True, "duration": 0.01}
if spec["kind"] == "build":
    font = out / "Mini[test].ttf"
    font.write_bytes(b"faux-font")
    result.update(built_font=str(font), last_build_status="ok",
                  last_build_error=None, last_build_time=time.time())
elif spec["kind"] == "export":
    fmt = spec.get("format", "ttf")
    name = {"woff2": "Mini-export.woff2", "sources": "Mini-sources.zip"}.get(
        fmt, "Mini-export.ttf")
    art = out / name
    art.write_bytes(b"faux-export-" + fmt.encode())
    result.update(artifact=str(art), artifact_name=art.name)
elif spec["kind"] == "compress":
    art = out / "Mini[test].woff2"
    art.write_bytes(b"faux-woff2")
    result.update(artifact=str(art), artifact_name=art.name,
                  woff2_bytes=10, source_bytes=100)
runs = os.environ.get("STUB_WORKER_RUNS")
if runs:
    with open(runs, "a") as f:
        f.write(spec["kind"] + "\n")
tmp = Path(spec["result_path"])
tmp.with_suffix(".tmp").write_text(json.dumps(result))
os.replace(tmp.with_suffix(".tmp"), tmp)
