#!/usr/bin/env python3
"""S1(a) recipe — reproduce the round-10 "ORIGIN nudge breaks the preview
relocation" failure on a copy staged per the routine.

The verifier could not reproduce it on a *pristine* Crispy copy (the nudge
relocated fine; `g` at 1,471 tuples) and asked for the recipe that does:
which control axes, brace layers, grades and Kinetikern entries were present
when the build failed, plus the build log and tuple counts.

The round-10 run reached the failure after A11 (a control axis with the
shadow's seeded brace layers), A10 (instance grades) and A13 (a Kinetikern
entry) had all run, and then A7 nudged the ORIGIN row. This script applies
exactly that studio-authored state through the public API, nudges the ORIGIN
row the same 1 %, builds, and reports:

  * health: last_build_status, avar2_error, built font name/size
  * per-glyph gvar tuple counts for the heaviest glyphs (fontTools)
  * the same numbers after one more nudge (to show the direction)

Usage (server already on PORT against the staged copy):

  python3 s1_recipe.py --url http://localhost:5072 \
      --sources /private/tmp/av2-routine/sources

It mutates the copy: run it on a scratch copy, never on ~/Documents/Crispy.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ORIGIN_ROW = "Default 400 MaxOpsz"
GRADED_ROW = "Default 1 MinOpsz"


def call(url, path, payload=None, method=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{url}{path}", data=data, method=method or ("POST" if payload is not None else "GET"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body


def quote(name: str) -> str:
    return urllib.parse.quote(name, safe="")


def health(url):
    return call(url, "/api/health")[1]


def wait_idle(url, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = health(url)
        if not h.get("building") and not h.get("build_job"):
            return h
        time.sleep(2)
    return health(url)


def wait_new_build(url, before, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = health(url)
        if h.get("last_build_time") not in (None, before) and not h.get("building"):
            return h
        time.sleep(2)
    return health(url)


def build_once(url, label):
    before = health(url).get("last_build_time")
    call(url, "/api/build", {})
    h = wait_new_build(url, before)
    h = wait_idle(url)
    print(f"  [{label}] status={h.get('last_build_status')!r} "
          f"avar2_error={str(h.get('avar2_error'))[:110]!r} "
          f"font={h.get('built_font_filename')}")
    return h


def tuple_report(sources: Path, label: str, top=4):
    """Per-glyph gvar tuple counts in the font the studio just built."""
    from fontTools.ttLib import TTFont

    build_dir = Path(sources) / ".avar2-studio" / "build"
    fonts = sorted(build_dir.glob("*.ttf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not fonts:
        print(f"  [{label}] no built font found in {build_dir}")
        return
    font_path = fonts[0]
    f = TTFont(str(font_path))
    gvar = f["gvar"]
    counts = sorted(((len(v), g) for g, v in gvar.variations.items()), reverse=True)
    axes = [a.axisTag for a in f["fvar"].axes]
    print(f"  [{label}] {font_path.name} {font_path.stat().st_size} B, axes {len(axes)} {axes}")
    print(f"  [{label}] heaviest glyphs (tuples): {counts[:top]}")
    for key in ("g", "B"):
        if key in gvar.variations:
            print(f"  [{label}] glyph {key!r}: {len(gvar.variations[key])} tuples")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5072")
    ap.add_argument("--sources", default="/private/tmp/av2-routine/sources")
    ap.add_argument("--skip-accumulate", action="store_true",
                    help="only nudge (for the pristine-copy control run)")
    a = ap.parse_args()
    url, sources = a.url, Path(a.sources)

    print("== baseline")
    h = wait_idle(url)
    print(f"  status={h.get('last_build_status')!r} origin_warning={h.get('origin_warning')!r} "
          f"avar2_error={h.get('avar2_error')!r}")
    tuple_report(sources, "baseline")

    if not a.skip_accumulate:
        print("== accumulate studio-authored state (what the round-10 run had)")
        axes = call(url, "/api/axes")[1]["axes"]
        tag = next(t for t in (f"TST{i}" for i in range(2, 20))
                   if t.lower() not in {str(x["tag"]).lower() for x in axes})
        code, body = call(url, "/api/control-axes",
                          {"tag": tag, "display_name": "Recipe axis",
                           "default": 0, "min": -100, "max": 100})
        print(f"  control axis {tag}: HTTP {code} {str(body)[:110]}")
        wait_idle(url)
        code, body = call(url, f"/api/control-axes/{tag}/reseed", {})
        print(f"  reseed {tag} (brace layers on coverage glyphs): HTTP {code}")
        wait_idle(url)
        code, body = call(url, f"/api/instances/{quote(GRADED_ROW)}/grade", {"pct": 0.25})
        print(f"  grade {GRADED_ROW}: HTTP {code} {str(body)[:80]}")
        wait_idle(url)
        code, body = call(url, "/api/kinetikern/instances/" + quote("Default 1 MaxOpsz"),
                          {"looseness": 0.45})
        print(f"  kinetikern entry: HTTP {code} {str(body)[:80]}")
        wait_idle(url)
        build_once(url, "after accumulation")
        tuple_report(sources, "after accumulation")

    print("== ORIGIN nudge: %s XOPQ -1%% of its range (A7's move)" % ORIGIN_ROW)
    inst = [i for i in call(url, "/api/instances")[1]["instances"] if i["name"] == ORIGIN_ROW][0]
    coords = dict(inst.get("source_coordinates") or inst.get("coordinates") or {})
    axis_range = [x for x in call(url, "/api/axes")[1]["axes"] if x["tag"] == "XOPQ"][0]
    step = (float(axis_range["max"]) - float(axis_range["min"])) * 0.01
    coords["XOPQ"] = round(float(coords["XOPQ"]) - step, 3)
    print(f"  XOPQ {inst.get('source_coordinates', {}).get('XOPQ')} -> {coords['XOPQ']}")
    code, body = call(url, f"/api/instance/{quote(ORIGIN_ROW)}",
                      {"coordinates": coords}, method="PUT")
    print(f"  source writeback: HTTP {code} {str(body)[:140]}")
    h = wait_idle(url)
    print(f"  after nudge: status={h.get('last_build_status')!r} "
          f"avar2_error={str(h.get('avar2_error'))[:200]!r} "
          f"font={h.get('built_font_filename')}")
    tuple_report(sources, "after nudge")

    print("== second nudge (direction check)")
    coords["XOPQ"] = round(float(coords["XOPQ"]) - step, 3)
    code, body = call(url, f"/api/instance/{quote(ORIGIN_ROW)}",
                      {"coordinates": coords}, method="PUT")
    print(f"  source writeback: HTTP {code} (XOPQ {coords['XOPQ']})")
    h = wait_idle(url)
    print(f"  after second nudge: status={h.get('last_build_status')!r} "
          f"avar2_error={str(h.get('avar2_error'))[:200]!r}")
    tuple_report(sources, "after second nudge")


if __name__ == "__main__":
    main()
