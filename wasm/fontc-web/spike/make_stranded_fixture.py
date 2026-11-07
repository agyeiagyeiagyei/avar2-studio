"""Generate tests/fixtures/StrandedMini.glyphs: CrispyMini plus two
deliberately out-of-range brace layers (XTRA 4000 > axis max 3330),
each a copy of the default master's outline shifted +137 units in x.

fontc compiles these as gvar tuples with peaks beyond +-1 that carry
live deltas — exactly the "stranded source" state the clamp oracle
needs its input to be in. The oracle used to read the live
Crispy.glyphs, which stopped containing stranded sources as the design
evolved (the vacuity guard then fired); this fixture freezes the
precondition. Regenerate with:

    .venv/bin/python wasm/fontc-web/spike/make_stranded_fixture.py
"""
import pathlib
import uuid

import glyphsLib

from avar2_studio.transforms.corner_rounding import _copy_layer

ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "examples/crispy-mini/sources/CrispyMini.glyphs"
OUT = ROOT / "wasm/fontc-web/tests/fixtures/StrandedMini.glyphs"

STRANDED_COORDS = [4000, 2, 2]  # XTRA beyond its 3330 max
X_SHIFT = 137


def main():
    font = glyphsLib.GSFont(str(SOURCE))
    origin = font.masters[0]
    for gname in ("a", "o"):
        glyph = font.glyphs[gname]
        base = glyph.layers[origin.id]
        brace = _copy_layer(base)  # detached deep copy — copy.copy shares paths
        brace.layerId = str(uuid.uuid4()).upper()
        brace.associatedMasterId = origin.id
        brace.attributes["coordinates"] = list(STRANDED_COORDS)
        for path in brace.paths:
            for node in path.nodes:
                node.position.x = float(node.position.x) + X_SHIFT
        glyph.layers.append(brace)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(OUT))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
