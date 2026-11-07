#!/usr/bin/env python3
"""Run the studio's width-aware SPAC transform through its NATIVE injector —
the whole desktop path: apply() finds $AVAR2_SPAC_INJECT, asks it for its
rule, writes the payload (master locations included) and takes its output.

Fails unless the native injector really produced the font: a silent fall
back to the fontTools path would make the comparison that follows vacuous.

Usage: native_spac.py INPUT OUT TRANSFORMS.json   (with $AVAR2_SPAC_INJECT set)
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from avar2_studio.transforms.base import BuildContext
from avar2_studio.transforms.builtin_spac_widthaware import WidthAwareSpacTransform


def main():
    input_path, out_path, transforms = sys.argv[1:4]
    assert os.environ.get("AVAR2_SPAC_INJECT"), "set AVAR2_SPAC_INJECT to the binary under test"
    (entry,) = [e for e in json.loads(Path(transforms).read_text())
                if e["type"] == "spac_widthaware" and e["enabled"]]
    params = dict(entry["params"])
    master_locations = params.pop("master_locations", None)
    work = Path(tempfile.mkdtemp(prefix="spac-native-"))
    src = work / "input.ttf"
    shutil.copy2(input_path, src)
    source = work / "Fixture.glyphs"  # only its sidecars are read
    logs = []
    ctx = BuildContext(build_dir=work, source_path=source, glyphs_path=source,
                       family="Fixture", log=logs.append,
                       master_locations=master_locations)
    out = WidthAwareSpacTransform().apply(src, params, ctx)
    for line in logs:
        print(f"     [studio] {line}")
    if not any("native injector (" in line for line in logs):
        print("the native injector did not produce this font", file=sys.stderr)
        return 1
    shutil.copy2(out, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
