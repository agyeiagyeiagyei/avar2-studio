"""Classify every move the export's 1 u floor had to make, with its numbers."""
import sys
sys.path.insert(0, "src")
from pathlib import Path
from avar2_studio import sources_export as se

BUILD = "test-output/e2ic-diag/unspaced.ttf"
SRC = Path("/tmp/rt-crispy/Crispy.glyphs")
CSV = Path("/tmp/rt-crispy/Crispy-avar.csv")
META = Path("/tmp/rt-crispy/Crispy-axis-metadata.json")
OUT = Path("test-output/e2ic-diag/classified-sources.zip")

se.CAPPED_MOVES.clear()
se.build_sources_zip(SRC, OUT, csv_path=CSV, metadata=se._load_metadata(META)
                     if hasattr(se, "_load_metadata") else {},
                     family_name="Crispy", hidden_axes=set(), kinetikern={},
                     original_path=None, ink_font=Path(BUILD))
moves = se.CAPPED_MOVES
print(f"floored (glyph, master) pairs: {len(moves)}")
for m in moves:
    overhang = m["lsb"] < 0 or m["rsb"] < 0 or m["ink"] > m["advance"]
    print(f"  {m['glyph']:<12} adv {m['advance']:7.1f} ink {m['ink']:7.1f} "
          f"lsb {m['lsb']:7.1f} rsb {m['rsb']:7.1f} rule d {m['rule_dx']:7.0f} "
          f"-> {m['floored_dx']:6.0f}  adv {m['rule_advance']:8.1f} -> "
          f"{m['floored_advance']:5.1f}  {'OVERHANG' if overhang else 'NO OVERHANG'}")
no = [m for m in moves if not (m["lsb"] < 0 or m["rsb"] < 0 or m["ink"] > m["advance"])]
print(f"\noverhanging {len(moves) - len(no)}, no overhang {len(no)}")
if no:
    print("STATE: blocked —", ", ".join(f"{m['glyph']}" for m in no))
