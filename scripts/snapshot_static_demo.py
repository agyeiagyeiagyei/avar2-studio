#!/usr/bin/env python3
"""Stage the bundled examples for the static demo (GitHub Pages).

The Pages build has no backend, so the examples ship as PROJECTS, not as
recordings of the server's API. For each bundled example this writes the
same project zip a designer would upload (source + studio sidecars, laid
out the way frontend/src/zip-workspace.js reads them) plus a pristine
fontc compile of the source. The browser shows that compile instantly and
treats the example exactly like an upload from then on: instance edits,
secondary axes, grade, transform parameters and Rebuild all work,
recompiled in-browser by fontc-wasm; the session persists; "Forget this
project" reloads the pristine copy.

Usage:
  python scripts/snapshot_static_demo.py

Output: frontend/public/static-demo/  (gitignored; generated in CI by
.github/workflows/pages.yml)
  examples.json        Load Font menu: id, name, subtitle, source_format,
                       base_ttf (the pristine compile, .glyphs projects)
  <id>/project.zip     source + sidecars. A .designspace project also
                       carries .avar2-studio/build/<stem>-VF.ttf — the
                       pristine compile the browser needs, since
                       fontc-wasm cannot read UFOs off a filesystem.
  <id>/base.ttf        pristine compile (.glyphs projects)
"""

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "static-demo"

# Studio sidecars that live next to a source (see zip-workspace.js).
SIDECAR_SUFFIXES = (
    "-avar.csv",
    "-control.json",
    "-transforms.json",
    "-grade.json",
    "-cornerpins.json",
    "-axis-metadata.json",
)


def examples():
    """The server's example registry, Crispy Mini first (it is the
    avar2/SPAC showcase and the default project)."""
    from avar2_studio.server import _BUILTIN_EXAMPLES

    exs = [dict(e) for e in _BUILTIN_EXAMPLES]
    exs.sort(key=lambda e: (e["id"] != "crispy-mini", e["id"]))
    return exs


def fontc_compile(source: Path, out: Path):
    """Compile ``source`` with fontc — the plain build, no studio steps.
    fontc leaves its working files in the cwd, so run it in a temp dir."""
    fontc = shutil.which("fontc") or str(Path(sys.executable).parent / "fontc")
    with tempfile.TemporaryDirectory(prefix="avar2-fontc-") as cwd:
        subprocess.run(
            [fontc, "--output-file", str(out), str(source)],
            check=True, cwd=cwd,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )


def project_files(source: Path):
    """(archive name, path) pairs for one example: the source (a .glyphs
    file, or a .designspace with every UFO beside it), the studio
    sidecars next to it, and .avar2-studio/axis-metadata.json."""
    src_dir = source.parent
    stem = source.stem
    yield source.name, source
    if source.suffix == ".designspace":
        for ufo in sorted(src_dir.glob("*.ufo")):
            for f in sorted(p for p in ufo.rglob("*") if p.is_file()):
                yield str(f.relative_to(src_dir)), f
    for f in sorted(src_dir.iterdir()):
        if f.is_file() and f.name.startswith(stem) and f.name.endswith(SIDECAR_SUFFIXES):
            yield f.name, f
    meta = src_dir / ".avar2-studio" / "axis-metadata.json"
    if meta.exists():
        yield ".avar2-studio/axis-metadata.json", meta


def stage(ex, out_dir: Path):
    source = ROOT / ex["source_rel"]
    if not source.exists():
        raise SystemExit(f"example source missing: {source}")
    out_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": ex["id"],
        "name": ex["name"],
        "subtitle": ex.get("subtitle", ""),
        "source_format": source.suffix[1:],
    }
    with tempfile.TemporaryDirectory(prefix="avar2-stage-") as tmp:
        base = Path(tmp) / f"{source.stem}-base.ttf"
        fontc_compile(source, base)
        with zipfile.ZipFile(out_dir / "project.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            for name, path in project_files(source):
                zf.write(path, name)
            if source.suffix == ".designspace":
                zf.write(base, f".avar2-studio/build/{source.stem}-VF.ttf")
        if source.suffix == ".glyphs":
            shutil.copyfile(base, out_dir / "base.ttf")
            entry["base_ttf"] = "base.ttf"
    return entry


def main():
    shutil.rmtree(OUT, ignore_errors=True)  # generated; drop stale layouts
    OUT.mkdir(parents=True, exist_ok=True)
    entries = []
    for ex in examples():
        entries.append(stage(ex, OUT / ex["id"]))
        print(f"staged {ex['id']}")
    (OUT / "examples.json").write_text(json.dumps({"examples": entries}, indent=2))
    print(f"static demo staged in {OUT} ({len(entries)} examples)")


if __name__ == "__main__":
    main()
