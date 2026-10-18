"""T3.8: the export build rests at the requested style losslessly.

Per decisions/0007 (the verifier's round-5 direction): no inserted master
— the export builds the staged ORIGINAL source exactly like the preview,
with step-3.5 relocation ON and its target = P (the requested style's
authored parametric location). `_build_in_process(..., relocate_default=...
relocate_target=...)` decides per call (env stays the preview default).
"""
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from fontTools.ttLib import TTFont
from test_avar2_default_relocation import MAPPINGS, METADATA

from avar2_studio import pipeline, state

REPO = Path(__file__).resolve().parents[1]
CRISPY_MINI = REPO / "examples/crispy-mini/sources/CrispyMini.glyphs"
FONTC = Path(sys.executable).with_name("fontc")
needs_fontc = pytest.mark.skipif(not FONTC.exists(),
                                 reason="fontc not next to the test interpreter")


def _build(tmp_path, monkeypatch, relocate_default, sub):
    workdir = tmp_path / sub / "work"
    workdir.mkdir(parents=True)
    src = tmp_path / sub / "CrispyMini.glyphs"
    src.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CRISPY_MINI, src)
    config_path = tmp_path / sub / "config.yaml"
    config_path.write_text(yaml.safe_dump({
        "sources": [str(src)],
        "avar2": {"CrispyMiniTest.ttf": MAPPINGS},
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline, "_load_axis_metadata", lambda: METADATA)
    return pipeline._build_in_process(config_path, workdir, str(FONTC),
                                      relocate_default=relocate_default)


def _gvar_tuple_count(path):
    tt = TTFont(str(path))
    return sum(len(variations) for variations in tt["gvar"].variations.values())


@needs_fontc
def test_export_build_keeps_tuple_counts_natural(tmp_path, monkeypatch):
    """relocate_default=True (the preview default) relocates the fvar
    defaults and multiplies gvar tuples; relocate_default=False (the export
    path) does neither."""
    relocated = _build(tmp_path, monkeypatch, True, "rel")
    natural = _build(tmp_path, monkeypatch, False, "nat")

    fvar = {a.axisTag: a for a in TTFont(str(relocated))["fvar"].axes}
    assert fvar["XTRA"].defaultValue == 800.0, "relocation on: default moved to the row's out"

    fvar_nat = {a.axisTag: a for a in TTFont(str(natural))["fvar"].axes}
    assert fvar_nat["XTRA"].defaultValue != 800.0, "relocation off: compiled origin kept"

    # The cost the export avoids: the relocated build splits every region
    # around the new origin — measure it, don't hand-wave it.
    n_rel = _gvar_tuple_count(relocated)
    n_nat = _gvar_tuple_count(natural)
    assert n_nat <= n_rel, f"natural {n_nat} should not exceed relocated {n_rel}"


@needs_fontc
def test_env_still_defaults_the_preview_build(tmp_path, monkeypatch):
    """No parameter → the env decides (default on, 0/1 honored)."""
    monkeypatch.delenv("AVAR2_RELOCATE_BUILD_DEFAULT", raising=False)
    out = _build(tmp_path, monkeypatch, None, "env-default")
    fvar = {a.axisTag: a for a in TTFont(str(out))["fvar"].axes}
    assert fvar["XTRA"].defaultValue == 800.0

    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "0")
    out = _build(tmp_path, monkeypatch, None, "env-off")
    fvar = {a.axisTag: a for a in TTFont(str(out))["fvar"].axes}
    assert fvar["XTRA"].defaultValue != 800.0


@needs_fontc
def test_set_default_export_matches_the_preview_at_an_authored_stop(tmp_path, monkeypatch):
    """The verifier's T3.8 acceptance: a set-default export at an authored
    stop must match the preview build within 3 u at that location, and its
    mapping must evaluate exactly to the authored out:."""
    from fontTools.varLib.instancer import instantiateVariableFont

    # 1. A project copy: source + its CSV; state points at it.
    work = tmp_path / "proj"
    work.mkdir()
    src = work / "CrispyMini.glyphs"
    shutil.copy2(CRISPY_MINI, src)
    shutil.copy2(CRISPY_MINI.parent / "CrispyMini-avar.csv",
                 work / "CrispyMini-avar.csv")
    monkeypatch.setattr(state, "ORIGINAL_PATH", src)
    monkeypatch.setattr(state, "GLYPHS_PATH", src)
    monkeypatch.setattr(state, "PREVIEW_CSV_PATH", None)
    monkeypatch.setattr(state, "PREVIEW_CONFIG_PATH", None)
    monkeypatch.setattr(state, "PREVIEW_DIR", None)
    monkeypatch.setattr(pipeline, "_load_axis_metadata", lambda: METADATA)

    # 2. The config derives from the CSV the way production does.
    config_path = work / ".avar2-studio" / "config.yaml"
    config_path.parent.mkdir()
    (config_path.parent / "axis-metadata.json").write_text(
        __import__("json").dumps(METADATA), encoding="utf-8")
    config_path.write_text(yaml.safe_dump({"familyName": "CrispyMiniTest",
                                           "sources": [str(src)], "avar2": {}}))
    from avar2_studio.build import config_generator as cfg_gen
    cfg_gen.update_config(csv_path=work / "CrispyMini-avar.csv",
                          config_path=config_path, backup=False,
                          source_path=src, fill_in_defaults=True)

    workdir1 = work / "work1"
    workdir1.mkdir()
    preview = pipeline._build_in_process(config_path, workdir1, str(FONTC))
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", preview)

    # 3. The export at the authored stop "Narrow Heavy 12"
    #    (in: opsz 12, wdth 20, wght 900 → out: XTRA 222.8, XOPQ 279.6, YOPQ 250.2).
    #    default_location keys are fvar-case, exactly as the UI sends them.
    export = pipeline._export_pipeline(preview, [],
                                       {"opsz": 12.0, "wdth": 20.0, "wght": 900.0},
                                       tmp_path / "export")
    assert Path(export).exists()

    # 3a. The export's parametric fvar defaults ARE the authored out:.
    fvar = {a.axisTag: a for a in TTFont(str(export))["fvar"].axes}
    for tag, v in {"XTRA": 222.8, "XOPQ": 279.6, "YOPQ": 250.2}.items():
        assert abs(fvar[tag].defaultValue - v) < 0.01, (tag, fvar[tag].defaultValue)

    # 3b. Outline parity with the preview at the same absolute location —
    # ≤ 3 u at every compared point. Both fonts carry an avar2 table, which
    # fontTools refuses to partial-instance — compare avar-stripped copies
    # pinned at the parametric stop (a full pin on the three remaining axes),
    # and both against a plain no-avar2 compile as ground truth.
    def no_avar(p):
        f = TTFont(str(p))
        if "avar" in f:
            del f["avar"]
        return f
    stop = {"XTRA": 222.8, "XOPQ": 279.6, "YOPQ": 250.2}
    plain_out = tmp_path / "plain.ttf"
    import subprocess as _sp
    _sp.run([str(FONTC), "-o", str(plain_out), str(src),
             "--flatten-components", "--decompose-transformed-components"], check=True)
    truth = instantiateVariableFont(TTFont(str(plain_out)), stop, inplace=False)
    ref = instantiateVariableFont(no_avar(preview), stop, inplace=False)
    exp = instantiateVariableFont(no_avar(export), stop, inplace=False)
    worst_p = worst_e = 0.0
    for name in ("e", "H", "n", "o", "a", "g"):
        ct, _e, _f = truth["glyf"][name].getCoordinates(truth["glyf"])
        cp, _e, _f = ref["glyf"][name].getCoordinates(ref["glyf"])
        ce, _e, _f = exp["glyf"][name].getCoordinates(exp["glyf"])
        assert len(ct) == len(cp) == len(ce), f"{name}: point count differs"
        for (xt, yt), (xp, yp), (xe, ye) in zip(ct, cp, ce):
            worst_p = max(worst_p, abs(xt - xp), abs(yt - yp))
            worst_e = max(worst_e, abs(xt - xe), abs(yt - ye))
    assert worst_p <= 3.0, f"preview drift {worst_p} u exceeds the 3 u bar"
    assert worst_e <= 3.0, f"export drift {worst_e} u exceeds the 3 u bar"

    # 3c. The mapping probe: the export's avar2 table evaluates the authored
    # stop exactly (the helper covers XTRA/XOPQ; YOPQ's exactness is covered
    # by the fvar-default assertion above).
    from test_avar2_default_relocation import _evaluate
    got = _evaluate(TTFont(str(export)), {"wght": 900, "opsz": 12, "wdth": 20})
    assert got == {"XTRA": 222.8, "XOPQ": 279.6}
