#!/usr/bin/env python3
"""Snapshot the demo workspace's API responses into static files.

The GitHub Pages build has no backend, so the static frontend reads
these JSON files instead of calling Flask (see frontend/src/static-api.js).
Shapes match the live API by construction — they ARE the API responses.

Usage:
  python scripts/snapshot_static_demo.py                      # boots its own server
  python scripts/snapshot_static_demo.py --base-url http://localhost:5070

Output: frontend/public/static-demo/{*.json,demo.ttf}  (gitignored;
generated in CI by .github/workflows/pages.yml).
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "static-demo"
EXAMPLE = ROOT / "examples" / "crispy-mini" / "sources" / "CrispyMini.glyphs"

# Endpoint path → output filename. Every one must return 200 JSON.
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
}


def fetch(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return r.read()


def wait_for_build(base, timeout=600):
    """The server builds the example font on startup; poll health."""
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
        proc = subprocess.Popen(
            [sys.executable, "-m", "avar2_studio", str(EXAMPLE), "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    try:
        health = wait_for_build(base)
        OUT.mkdir(parents=True, exist_ok=True)

        for path, name in JSON_ENDPOINTS.items():
            data = fetch(base, path)
            json.loads(data)  # sanity: must be JSON
            (OUT / name).write_bytes(data)

        # Static-mode extras on the health snapshot: upm (for the
        # width-chip unit conversion) and the marker flag.
        from fontTools.ttLib import TTFont
        font_bytes = fetch(base, "/api/font")
        (OUT / "demo.ttf").write_bytes(font_bytes)
        import io
        health["upm"] = TTFont(io.BytesIO(font_bytes))["head"].unitsPerEm
        health["static"] = True
        (OUT / "health.json").write_text(json.dumps(health, indent=2))

        print(f"snapshot written to {OUT} ({len(JSON_ENDPOINTS)} endpoints + demo.ttf)")
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
