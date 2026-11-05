"""Trace the 8.02 u fixture parity gap per glyph, row and end."""
import sys, shutil, subprocess, zipfile, json
sys.path.insert(0, "src")
from pathlib import Path
from fontTools.ttLib import TTFont
from avar2_studio.build import config_generator
from avar2_studio.transforms.base import BuildContext
from avar2_studio.transforms.builtin_spac_widthaware import WidthAwareSpacTransform

OUT = Path("test-output/e2ic-fixture/gap"); shutil.rmtree(OUT, ignore_errors=True); OUT.mkdir(parents=True)
SRC = Path("examples/crispy-mini/sources")

def build(src_dir, tag, transforms=True):
    d = OUT / tag; d.mkdir()
    for n in ("CrispyMini.glyphs", "CrispyMini-avar.csv", "CrispyMini-axis-metadata.json"):
        shutil.copy2(SRC / n, d / n)
    side = json.loads((SRC / "CrispyMini-transforms.json").read_text())
    side["transforms"][0]["enabled"] = transforms
    side["transforms"][0]["params"].update({"min": -40, "max": 40})
    (d / "CrispyMini-transforms.json").write_text(json.dumps(side))
    from avar2_studio import sources_export as se
    zip_path = d / "sources.zip"
    se.build_sources_zip(d / "CrispyMini.glyphs", zip_path,
                         csv_path=d / "CrispyMini-avar.csv", metadata={},
                         family_name="CrispyMini", hidden_axes=set(), kinetikern={},
                         original_path=d / "CrispyMini.glyphs", ink_font=None)
    w = d / "rt"; w.mkdir()
    with zipfile.ZipFile(zip_path) as zf: zf.extractall(w)
    ds = next(w.glob("*.designspace"))
    r = subprocess.run(["fontmake", "-m", str(ds), "-o", "variable",
                        "--output-dir", str(d / "fm")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    return next((d / "fm").glob("*.ttf"))

spaced = build(SRC, "spaced", True)
plain = build(SRC, "plain", False)
target = OUT / "injected.ttf"; shutil.copy2(plain, target)
ctx = BuildContext(build_dir=OUT, source_path=SRC / "CrispyMini.glyphs",
                   glyphs_path=SRC / "CrispyMini.glyphs", family="CrispyMini")
injected = WidthAwareSpacTransform().apply(target, {"min": -40, "max": 40, "bias": 1.0, "scale": 1.25}, ctx)

def one(path, name, loc):
    gs = TTFont(str(path)).getGlyphSet(location=loc)
    return gs[name].width

rows = [r for r in config_generator.read_csv_mappings(SRC / "CrispyMini-avar.csv")]
diffs = []
for row in rows:
    user = {t: float(v) for t, v in row.in_axes.items()}
    for name in TTFont(str(spaced)).getGlyphOrder():
        try:
            b_s = one(spaced, name, {**user, "SPAC": 0.0}); b_i = one(injected, name, {**user, "SPAC": 0.0})
            for end in (-40.0, 40.0):
                d_s = one(spaced, name, {**user, "SPAC": end}) - b_s
                d_i = one(injected, name, {**user, "SPAC": end}) - b_i
                if abs(d_s - d_i) > 0.5:
                    diffs.append((abs(d_s - d_i), name, row.instance_name, end, d_s, d_i))
        except KeyError:
            continue
diffs.sort(reverse=True)
print(f"glyph/row/end differences over 0.5 u: {len(diffs)}")
for d in diffs[:15]:
    print(f"  {d[0]:6.2f} u  {d[1]:<10} {d[2]:<22} end {d[3]:+5.0f}  export {d[4]:7.1f}  injector {d[5]:7.1f}")
