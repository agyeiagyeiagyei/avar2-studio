#!/usr/bin/env python3
"""Snapshot the demo workspaces' API responses into static files.

The GitHub Pages build has no backend, so the static frontend reads
these JSON files instead of calling Flask (see frontend/src/static-api.js).
Shapes match the live API by construction — they ARE the API responses.

Captures every bundled example (the Load Font menu works statically),
and for the first example a SPAC-off variant (font + axes + instances +
health) so the Transforms toggle can swap pre-baked builds client-side —
params edits and Rebuild remain app-only (see docs/migration-github-pages.md).

Usage:
  python scripts/snapshot_static_demo.py                      # boots its own server
  python scripts/snapshot_static_demo.py --base-url http://localhost:5070

Output: frontend/public/static-demo/  (gitignored; generated in CI by
.github/workflows/pages.yml).
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "static-demo"
EXAMPLE = ROOT / "examples" / "crispy-mini" / "sources" / "CrispyMini.glyphs"

# Endpoint path → output filename (per example dir).
JSON_ENDPOINTS = {
    "/api/health": "health.json",
    "/api/axes": "axes.json",
    "/api/instances": "instances.json",
    "/api/masters": "masters.json",
    "/api/avar2/instances": "avar2-instances.json",
    "/api/avar2/axes": "avar2-axes.json",
    "/api/transforms": "transforms.json",
    "/api/transforms/grade": "grade.json",
    "/api/control-axes": "control-axes.json",
    "/api/glyph-coverage": "glyph-coverage.json",
    "/api/config/export": "config-export.json",
}


def fetch(base, path, body=None):
    if body is None:
        req = urllib.request.Request(base + path)
    else:
        req = urllib.request.Request(
            base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST" if path == "/api/load-source" else "PUT",
        )
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def wait_for_build(base, timeout=900):
    """The server builds the example font on load/swap; poll health."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            health = json.loads(fetch(base, "/api/health"))
            if health.get("font_built") and not health.get("building"):
                return health
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit("server did not finish building in time")


def capture(base, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, name in JSON_ENDPOINTS.items():
        data = fetch(base, path)
        json.loads(data)  # sanity: must be JSON
        (out_dir / name).write_bytes(data)

    from fontTools.ttLib import TTFont
    font_bytes = fetch(base, "/api/font")
    (out_dir / "demo.ttf").write_bytes(font_bytes)

    # Static-mode extras on the health snapshot: upm (for the width-chip
    # unit conversion) and the marker flag.
    health = json.loads((out_dir / "health.json").read_bytes())
    health["upm"] = TTFont(io.BytesIO(font_bytes))["head"].unitsPerEm
    health["static"] = True
    (out_dir / "health.json").write_text(json.dumps(health, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", help="use a running server instead of booting one")
    args = ap.parse_args()

    proc = None
    if args.base_url:
        base = args.base_url.rstrip("/")
    else:
        port = 5399
        base = f"http://localhost:{port}"
        # Isolated HOME → the server stages PRISTINE example workspaces
        # from the repo, so local runs match CI instead of inheriting
        # whatever is in ~/.avar2-studio/workspace right now.
        env = {**os.environ, "HOME": tempfile.mkdtemp(prefix="avar2-snapshot-")}
        proc = subprocess.Popen(
            [sys.executable, "-m", "avar2_studio", str(EXAMPLE), "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )

    try:
        wait_for_build(base)
        examples = json.loads(fetch(base, "/api/examples"))["examples"]
        # Crispy Mini leads: it is the avar2/SPAC showcase, so it is the
        # default dataset AND gets the variant captures.
        examples.sort(key=lambda e: (e["id"] != "crispy-mini", e["id"]))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "examples.json").write_text(json.dumps({"examples": examples}, indent=2))

        for i, ex in enumerate(examples):
            if i > 0 or not args.base_url:
                # (re)load the example workspace before capturing
                fetch(base, "/api/load-source", {"example": ex["id"]})
            wait_for_build(base)
            capture(base, OUT / ex["id"])
            print(f"captured {ex['id']}")

            if i == 0:
                # SPAC-off variant for the default example: pre-bake the
                # other side of the Transforms toggle.
                tx = json.loads(fetch(base, "/api/transforms"))["transforms"]
                spac = next((t for t in tx if t.get("type") == "spac" or t.get("id") == "spac"), None)
                if spac:
                    key = "type" if "type" in spac else "id"
                    off = [{key: t.get(key), "enabled": False, "params": t.get("params", {})} for t in tx]
                    fetch(base, "/api/transforms", {"transforms": off})
                    wait_for_build(base)
                    capture(base, OUT / ex["id"] / "variants" / "spac-off")
                    on = [{key: t.get(key), "enabled": True, "params": t.get("params", {})} for t in tx]
                    fetch(base, "/api/transforms", {"transforms": on})
                    wait_for_build(base)
                    print(f"captured {ex['id']}/variants/spac-off")

        print(f"snapshot written to {OUT} ({len(examples)} examples)")
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
