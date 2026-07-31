#!/usr/bin/env python3
"""Oracle comparison for apply_transforms (SPAC injection).

Runs the REAL transforms — the studio's WidthAwareSpacTransform and
gftools gen-spac via SpacTransform — on the same input font the wasm
crate consumed, then compares:

  1. fvar: SPAC axis range (-20..40, default 0), "Spacing" name, and
     instance pinning (0.0, or the CSV's SPAC column when present).
  2. Per-glyph advances at SPAC in {-20, 0, 20, 40}: wasm output vs
     oracle output, expecting EXACT equality (identical integer deltas
     through the same instancer), plus wasm@0 == the original advances.
  3. Outlines never move: instanced contour points (excluding the 4
     phantom points) are identical at SPAC min/default/max.
  4. The wasm output's glyf table is byte-identical to the original's
     (the transform touches no outline data), and HVAR is present (the
     advance adjustment is variation-driven, not a static hmtx rewrite).

Usage: compare_spac.py ORIGINAL WA UNI INST_PLAIN INST_OVR
  WA / UNI are the wasm crate's outputs (width-aware, uniform).
  INST_PLAIN / INST_OVR are its outputs on the synthesized instanced
  font (/tmp/spac-instanced-input.ttf, built by make_instanced_font.py)
  with the plain avar2 CSV and with a SPAC-column CSV (the last read
  back from /tmp/spac-override-avar.csv for the oracle sidecar).
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

# SpacTransform shells out to gftools-gen-spac — find it next to this
# venv's python.
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]

from avar2_studio.transforms.base import BuildContext
from avar2_studio.transforms.builtin_spac import SpacTransform
from avar2_studio.transforms.builtin_spac_widthaware import WidthAwareSpacTransform

CRISPY_GLYPHS = Path(__file__).resolve().parents[3] / "examples/crispy-mini/sources/CrispyMini.glyphs"
WA_PARAMS = {"min": -20, "max": 40, "bias": 1.0, "scale": 1.25}
UNIFORM_PARAMS = {"min": -20, "max": 40}
SPAC_SWEEP = (-20, 0, 20, 40)

FAILURES = []


def ok(cond, label):
    print(f"  {'✓' if cond else '✗ FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def ctx(source_path, build_dir):
    return BuildContext(
        build_dir=build_dir,
        source_path=source_path,
        glyphs_path=source_path,
        family="CrispyMini",
        log=lambda m: print(f"     [oracle] {m}"),
    )


def run_oracle(transform, params, source_path, work_dir, input_path=None):
    """Copy the input into work_dir and apply the transform for real."""
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    src = Path(work_dir) / "input.ttf"
    shutil.copy2(input_path or ORIGINAL_PATH, src)
    return transform.apply(src, params, ctx(source_path, Path(work_dir)))


def advances(path, spac):
    font = TTFont(path)
    instancer.instantiateVariableFont(font, {"SPAC": spac}, inplace=True)
    return {g: font["hmtx"][g][0] for g in font.getGlyphOrder()}


def outlines(path, spac):
    """Instanced contour/component points WITHOUT the 4 phantoms."""
    font = TTFont(path)
    instancer.instantiateVariableFont(font, {"SPAC": spac}, inplace=True)
    glyf = font["glyf"]
    return {
        g: list(glyf._getCoordinatesAndControls(g, font["hmtx"].metrics)[0])[:-4]
        for g in font.getGlyphOrder()
    }


def compare_variant(label, wasm_path, oracle_path):
    print(f"{label}: advances at SPAC {SPAC_SWEEP}")
    wasm = {v: advances(wasm_path, v) for v in SPAC_SWEEP}
    oracle = {v: advances(oracle_path, v) for v in SPAC_SWEEP}
    glyphs = TTFont(oracle_path).getGlyphOrder()
    for v in SPAC_SWEEP:
        diffs = {
            g: (wasm[v][g], oracle[v][g])
            for g in glyphs
            if wasm[v][g] != oracle[v][g]
        }
        if diffs:
            sample = dict(list(diffs.items())[:5])
            ok(False, f"advances at SPAC {v:+d} match oracle ({len(diffs)} differ: {sample})")
        else:
            lo = min(wasm[v][g] for g in glyphs)
            hi = max(wasm[v][g] for g in glyphs)
            print(f"     SPAC {v:+d}: all {len(glyphs)} glyphs match (advances {lo}..{hi})")
            ok(True, f"advances at SPAC {v:+d} match oracle")
    # SPAC tracks monotonically and the default keeps the original metrics.
    if "e" in glyphs:
        ok(
            wasm[-20]["e"] < wasm[0]["e"] < wasm[40]["e"],
            f"'e' advance grows with SPAC ({wasm[-20]['e']} < {wasm[0]['e']} < {wasm[40]['e']})",
        )
    base = {g: TTFont(ORIGINAL_PATH)["hmtx"][g][0] for g in glyphs}
    ok(wasm[0] == base, "SPAC=0 advances == original hmtx")
    # Outlines never move across the SPAC axis.
    pts = {v: outlines(wasm_path, v) for v in (-20, 0, 40)}
    moved = [g for g in glyphs if pts[-20][g] != pts[0][g] or pts[40][g] != pts[0][g]]
    ok(not moved, f"outlines identical across SPAC ({len(moved)} moved: {moved[:5]})")


def raw_table(path, tag):
    """The table's exact on-disk bytes (plain TTF: offset + length)."""
    from fontTools.ttLib.sfnt import SFNTReader

    with open(path, "rb") as fh:
        reader = SFNTReader(fh)
        entry = reader.tables[tag]
        fh.seek(entry.offset)
        return fh.read(entry.length)


def check_structure(label, wasm_path):
    font = TTFont(wasm_path)
    axes = {a.axisTag: (a.minValue, a.defaultValue, a.maxValue) for a in font["fvar"].axes}
    ok(axes.get("SPAC") == (-20.0, 0.0, 40.0), f"{label}: SPAC fvar range {axes.get('SPAC')}")
    ok("HVAR" in font, f"{label}: HVAR present (variation-driven advances)")
    ok("Spacing" in {n.toUnicode() for n in font["name"].names}, f"{label}: 'Spacing' name record")
    same = raw_table(ORIGINAL_PATH, "glyf") == raw_table(wasm_path, "glyf")
    ok(same, f"{label}: glyf bytes untouched")


def check_instances(label, wasm_path, oracle_path):
    def spac_coords(path):
        font = TTFont(path)
        names = font["name"]
        return {
            names.getDebugName(i.subfamilyNameID): i.coordinates.get("SPAC")
            for i in font["fvar"].instances
        }

    wasm, oracle = spac_coords(wasm_path), spac_coords(oracle_path)
    ok(wasm == oracle, f"{label}: instance SPAC coords match oracle ({wasm})")


def main():
    global ORIGINAL_PATH
    ORIGINAL_PATH, wa_wasm, uni_wasm, inst_plain_wasm, inst_ovr_wasm = sys.argv[1:6]
    instanced_input = "/tmp/spac-instanced-input.ttf"
    tmp = tempfile.mkdtemp(prefix="spac-oracle-")

    print("0. structure")
    check_structure("width-aware", wa_wasm)
    check_structure("uniform", uni_wasm)

    print("1. width-aware SPAC vs WidthAwareSpacTransform")
    wa_oracle = run_oracle(WidthAwareSpacTransform(), WA_PARAMS, CRISPY_GLYPHS, f"{tmp}/wa")
    compare_variant("1. width-aware", wa_wasm, wa_oracle)

    print("2. uniform SPAC vs gftools gen-spac (SpacTransform)")
    uni_oracle = run_oracle(SpacTransform(), UNIFORM_PARAMS, CRISPY_GLYPHS, f"{tmp}/uni")
    compare_variant("2. uniform", uni_wasm, uni_oracle)

    print("3. per-instance SPAC pinning (font with fvar instances)")
    plain_oracle = run_oracle(
        WidthAwareSpacTransform(), WA_PARAMS, CRISPY_GLYPHS, f"{tmp}/ipl", instanced_input
    )
    check_instances("plain CSV → default 0.0", inst_plain_wasm, plain_oracle)
    src_dir = Path(tmp) / "ovr-src"
    src_dir.mkdir()
    shutil.copy2("/tmp/spac-override-avar.csv", src_dir / "OverrideSrc-avar.csv")
    ovr_oracle = run_oracle(
        WidthAwareSpacTransform(), WA_PARAMS, src_dir / "OverrideSrc.glyphs", f"{tmp}/ovr",
        instanced_input,
    )
    check_instances("override CSV → pinned", inst_ovr_wasm, ovr_oracle)
    # Guard against a vacuous pass: the oracle must actually have pinned
    # the two CSV values (not silently flattened everything to 0.0).
    font = TTFont(ovr_oracle)
    pinned = {
        font["name"].getDebugName(i.subfamilyNameID): i.coordinates.get("SPAC")
        for i in font["fvar"].instances
    }
    ok(
        pinned.get("Narrow Thin 144") == 10.0 and pinned.get("Ultra Wide Thin 144") == -15.0,
        f"oracle pinned the CSV values ({pinned})",
    )
    compare_variant("3. override", inst_ovr_wasm, ovr_oracle)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("ALL SPAC ORACLE CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
