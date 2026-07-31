#!/usr/bin/env python3
"""Test fixture for the SPAC per-instance override path.

CrispyMini.glyphs declares no instances, so the compiled spike font's
fvar has none — and the transforms' instance-pinning code would have
nothing to pin. Add two named instances ("Narrow Thin 144", "Ultra
Wide Thin 144" — the names the override CSV uses) at the axis defaults
and write the result next to the spike font.

Usage: make_instanced_font.py IN.ttf OUT.ttf
"""

import sys

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._f_v_a_r import NamedInstance


def main():
    src, dst = sys.argv[1], sys.argv[2]
    font = TTFont(src)
    fvar = font["fvar"]
    name = font["name"]
    defaults = {a.axisTag: a.defaultValue for a in fvar.axes}
    for inst_name in ("Narrow Thin 144", "Ultra Wide Thin 144"):
        inst = NamedInstance()
        inst.subfamilyNameID = name.addMultilingualName({"en": inst_name})
        inst.postscriptNameID = 0xFFFF
        inst.coordinates = dict(defaults)
        fvar.instances.append(inst)
    font.save(dst)


if __name__ == "__main__":
    main()
