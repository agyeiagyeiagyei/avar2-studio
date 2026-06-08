"""Subset each UFO in /tmp/roboto-delta-mini/sources/ down to ASCII A-Z, a-z, 0-9.

What this drops:
  * Every glyph whose name isn't in KEEP_GLYPHS (uppercase A-Z, lowercase a-z,
    digit-names zero..nine, .notdef, space).
  * The .glif files for dropped glyphs.
  * Any contents.plist entries for dropped glyphs.
  * Composite-glyph references to dropped glyphs — replaced with no-op
    (we strip components pointing at dropped glyphs; for ASCII letters and
    digits in Roboto Delta the components are simple, but we sanity-check).
  * features.fea (gets emptied — features reference dropped glyphs and
    won't compile otherwise).
  * groups.plist + kerning.plist (kerning groups reference dropped glyphs).

What it keeps untouched:
  * fontinfo.plist, metainfo.plist, lib.plist, layercontents.plist.
  * Glyph-level metadata for kept glyphs (anchors, unicodes, components
    that point to other kept glyphs).
"""

import plistlib
import re
import string
from pathlib import Path
from xml.etree import ElementTree as ET

SOURCES_DIR = Path("/tmp/roboto-delta-mini/sources")

DIGIT_NAMES = {"zero","one","two","three","four","five","six","seven","eight","nine"}
# Composite-glyph dependencies: i and j in Roboto Delta have zero
# contours and only reference idotless/jdotless/idot via <component>
# — keeping the parent without these renders the glyph as blank.
COMPOSITE_DEPS = {"idotless", "jdotless", "idot"}
KEEP_GLYPHS = (
    set(string.ascii_uppercase)
    | set(string.ascii_lowercase)
    | DIGIT_NAMES
    | COMPOSITE_DEPS
    | {".notdef", "space"}
)


def subset_ufo(ufo: Path) -> None:
    print(f"\n=== {ufo.name} ===")
    glyphs_dir = ufo / "glyphs"
    contents_plist = glyphs_dir / "contents.plist"
    contents = plistlib.loads(contents_plist.read_bytes())

    # Round 1: figure out which glyphs to drop based on the keep set.
    # Round 2: walk kept glyphs and check their <component base="..."/>
    # references — if a component points to a glyph we're dropping, strip
    # that component out of the .glif. (For ASCII this rarely fires.)
    keep = {name: filename for name, filename in contents.items() if name in KEEP_GLYPHS}
    drop = {name for name in contents if name not in KEEP_GLYPHS}
    print(f"  keep {len(keep)} / drop {len(drop)} (of {len(contents)} total)")

    stripped_component_refs = 0
    for name, filename in list(keep.items()):
        glif_path = glyphs_dir / filename
        if not glif_path.exists():
            print(f"  warn: {filename} listed in contents.plist but missing on disk")
            continue
        tree = ET.parse(glif_path)
        root = tree.getroot()
        outline = root.find("outline")
        if outline is None:
            continue
        dirty = False
        for component in list(outline.findall("component")):
            base = component.get("base")
            if base and base not in KEEP_GLYPHS:
                outline.remove(component)
                stripped_component_refs += 1
                dirty = True
        if dirty:
            tree.write(glif_path, encoding="UTF-8", xml_declaration=True)
    if stripped_component_refs:
        print(f"  stripped {stripped_component_refs} component refs pointing at dropped glyphs")

    # Drop the .glif files for unwanted glyphs.
    dropped = 0
    for name in drop:
        filename = contents[name]
        glif_path = glyphs_dir / filename
        if glif_path.exists():
            glif_path.unlink()
            dropped += 1
    print(f"  deleted {dropped} glif files")

    # Rewrite contents.plist with only the kept glyphs.
    contents_plist.write_bytes(plistlib.dumps(keep))

    # Nuke features.fea so kerning/feature references to dropped glyphs
    # don't break compilation. Roboto Delta's features.fea references
    # diacritics and ligatures we no longer have.
    fea = ufo / "features.fea"
    if fea.exists():
        fea.write_text("# subsetted demo: features removed\n")

    # Same logic for groups/kerning.
    for name in ("groups.plist", "kerning.plist"):
        p = ufo / name
        if p.exists():
            # Write an empty plist rather than deleting — keeps the UFO
            # spec-compliant.
            p.write_bytes(plistlib.dumps({}))

    # Layer cleanup: every layer also has a glyphs/ subdir + contents.plist.
    # Apply the same subsetting there.
    for layer_dir in ufo.glob("glyphs.*"):
        layer_contents = layer_dir / "contents.plist"
        if not layer_contents.exists():
            continue
        layer_data = plistlib.loads(layer_contents.read_bytes())
        keep_layer = {n: f for n, f in layer_data.items() if n in KEEP_GLYPHS}
        for name, filename in layer_data.items():
            if name not in KEEP_GLYPHS:
                gp = layer_dir / filename
                if gp.exists():
                    gp.unlink()
        layer_contents.write_bytes(plistlib.dumps(keep_layer))
        print(f"  subsetted layer {layer_dir.name}: {len(keep_layer)} glyphs")


def main():
    for ufo in sorted(SOURCES_DIR.glob("*.ufo")):
        subset_ufo(ufo)

    print("\nDone.")
    import subprocess
    subprocess.run(["du", "-sh", str(SOURCES_DIR), *sorted(str(p) for p in SOURCES_DIR.glob("*.ufo"))])


if __name__ == "__main__":
    main()
