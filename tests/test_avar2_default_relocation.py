"""The avar2 build relocates parametric defaults to the default-located
mapping row, making the compiled table EXACT at every row.

Regression (the long-misdiagnosed "evaluator understates" issue): a
mapping row whose in: sits at the user-axis defaults cannot be
expressed by avar2 (the default location maps to itself). fontTools'
_add_avar silently used such a row as the model base — its out was
dropped and subtracted from every other row's deltas, understating the
whole table for every consumer (fontTools AND HarfBuzz; the subtract
reproduced the broken deltas to the F2Dot14 unit). The build now moves
the parametric fvar defaults to that row's out first (fontTools
instancer, ranges kept), so the row compiles as a true identity and
the other rows anchor exactly."""

import shutil
from pathlib import Path

import pytest
import yaml
from fontTools.ttLib import TTFont
from fontTools.varLib.models import normalizeLocation

from avar2_studio import pipeline, state

REPO = Path(__file__).resolve().parents[1]
CRISPY_MINI = REPO / "examples/crispy-mini/sources/CrispyMini.glyphs"
FONTC = REPO / ".venv/bin/fontc"

METADATA = {"WGHT": {"registered_tag": "wght", "is_parametric": False,
                     "min": 1, "default": 400, "max": 900}}

MAPPINGS = [
    # The default-located row: wght at its declared default.
    {"in": {"wght": 400}, "out": {"XTRA": 800.0, "XOPQ": 300.0}},
    {"in": {"wght": 900}, "out": {"XTRA": 400.0, "XOPQ": 700.0}},
    {"in": {"wght": 1}, "out": {"XTRA": 3000.0, "XOPQ": 10.0}},
]


def _evaluate(tt, user):
    axes = {a.axisTag: (a.minValue, a.defaultValue, a.maxValue) for a in tt["fvar"].axes}
    loc = {t: user.get(t, d) for t, (lo, d, hi) in axes.items()}
    renorm = tt["avar"].renormalizeLocation(normalizeLocation(loc, axes), tt)
    def denorm(tag, n):
        lo, d, hi = axes[tag]
        return d + n * ((hi - d) if n > 0 else (d - lo))
    return {t: round(denorm(t, renorm.get(t, 0.0)), 1) for t in ("XTRA", "XOPQ")}


def _build(tmp_path, monkeypatch, mappings, sub="a"):
    workdir = tmp_path / sub / "work"
    workdir.mkdir(parents=True)
    src = tmp_path / sub / "CrispyMini.glyphs"
    shutil.copy2(CRISPY_MINI, src)
    config_path = tmp_path / sub / "config.yaml"
    config_path.write_text(yaml.safe_dump({
        "sources": [str(src)],
        "avar2": {"CrispyMiniTest.ttf": mappings},
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline, "_load_axis_metadata", lambda: METADATA)
    return pipeline._build_in_process(config_path, workdir, str(FONTC))


@pytest.mark.skipif(not FONTC.exists(), reason="fontc not in venv")
def test_default_row_relocates_and_table_is_exact(tmp_path, monkeypatch):
    # Build-time relocation is EXPORT-ONLY by default now (gvar ~7x cost
    # compounds with the rounding/slant twins); this test exercises the
    # opt-in path, which the export pipeline's guarantee rests on.
    monkeypatch.setenv('AVAR2_RELOCATE_BUILD_DEFAULT', '1')
    out = _build(tmp_path, monkeypatch, MAPPINGS)
    tt = TTFont(str(out))

    fvar = {a.axisTag: a for a in tt["fvar"].axes}
    assert fvar["XTRA"].defaultValue == 800.0, "parametric default relocated to the default row"
    assert fvar["XOPQ"].defaultValue == 300.0
    assert (fvar["XTRA"].minValue, fvar["XTRA"].maxValue) == (94.0, 3330.0), "range kept"

    # The compiled table must hit every authored row EXACTLY.
    assert _evaluate(tt, {"wght": 400}) == {"XTRA": 800.0, "XOPQ": 300.0}
    assert _evaluate(tt, {"wght": 900}) == {"XTRA": 400.0, "XOPQ": 700.0}
    assert _evaluate(tt, {"wght": 1}) == {"XTRA": 3000.0, "XOPQ": 10.0}

    # Behavior preservation EVERYWHERE — the core requirement, and the
    # reason the method is the instancer rebase despite ~7x gvar: both
    # "insert one interpolated master and recompile" routes were exact
    # at master corners but re-pinned the field BETWEEN them (measured
    # 468u at an interior corner, 223u at an authored row on this very
    # fixture), silently shifting calibrated styles. Compare outlines
    # against a no-relocation build at the same absolute locations,
    # interior points included.
    base = _build(tmp_path, monkeypatch, MAPPINGS[1:], sub="b")  # no default row
    from fontTools.varLib.instancer import instantiateVariableFont
    def _no_avar(p):
        f = TTFont(str(p))
        if "avar" in f:
            del f["avar"]  # pure-gvar comparison; instancer predates avar2
        return f
    for loc in ({"XTRA": 94, "XOPQ": 2}, {"XTRA": 3000, "XOPQ": 900},
                {"XTRA": 800, "XOPQ": 300}):
        a = instantiateVariableFont(_no_avar(out), loc, inplace=False)
        b = instantiateVariableFont(_no_avar(base), loc, inplace=False)
        ca, _, _ = a["glyf"]["n"].getCoordinates(a["glyf"])
        cb, _, _ = b["glyf"]["n"].getCoordinates(b["glyf"])
        worst = max((abs(pa[0] - pb[0]) + abs(pa[1] - pb[1])
                     for pa, pb in zip(ca, cb)), default=0)
        # A couple of units of integer re-rounding at extreme corners is
        # inherent to re-expressing deltas around the new origin; the
        # bound guards against real mangling (the designspace route
        # failed this by hundreds of units).
        assert worst <= 3, f"outline moved {worst} units at {loc}"


@pytest.mark.skipif(not FONTC.exists(), reason="fontc not in venv")
def test_missing_default_row_sets_the_origin_warning(tmp_path, monkeypatch):
    # Rows at wght 1 and 900 but none at the default (400): the font
    # rests at the compiled origin — the "resting dip". The build must
    # succeed AND set the loud warning naming the defaults.
    out = _build(tmp_path, monkeypatch, MAPPINGS[1:])  # drop the default row
    assert out.exists()
    assert state.LAST_ORIGIN_WARNING is not None
    assert "wght 400" in state.LAST_ORIGIN_WARNING
    assert "ORIGIN row" in state.LAST_ORIGIN_WARNING

    # And a build WITH a default row clears it (relocation is on by
    # default again: the off-mode's excluded ORIGIN row made every
    # mapping tent collapse toward the corner across the whole default
    # neighborhood — a valley, not a point dip).
    _build(tmp_path, monkeypatch, MAPPINGS, sub="c")
    assert state.LAST_ORIGIN_WARNING is None
    # The opt-out keeps the informational variant.
    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "0")
    _build(tmp_path, monkeypatch, MAPPINGS, sub="d")
    assert "relocation is off" in (state.LAST_ORIGIN_WARNING or "")


@pytest.mark.skipif(not FONTC.exists(), reason="fontc not in venv")
def test_two_default_rows_fail_loudly(tmp_path, monkeypatch):
    # The config generator's dedupe normally collapses these with a
    # named warning; a hand-edited config reaching the build must not
    # silently pick one resting state over the other.
    workdir = tmp_path / "work"
    workdir.mkdir()
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(CRISPY_MINI, src)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({
        "sources": [str(src)],
        "avar2": {"CrispyMiniTest.ttf": [
            {"in": {"wght": 400}, "out": {"XTRA": 800.0}},
            # Different raw in:, same effective location (all defaults).
            {"in": {"wght": 400, "opsz": 144}, "out": {"XTRA": 500.0}},
            {"in": {"wght": 900}, "out": {"XTRA": 400.0}},
        ]},
    }), encoding="utf-8")
    meta = dict(METADATA)
    meta["OPSZ"] = {"registered_tag": "opsz", "is_parametric": False,
                    "min": 14, "default": 144, "max": 144}
    monkeypatch.setattr(pipeline, "_load_axis_metadata", lambda: meta)
    with pytest.raises(RuntimeError, match="multiple mapping rows sit at the axis defaults"):
        pipeline._build_in_process(config_path, workdir, str(FONTC))



@pytest.mark.skipif(not FONTC.exists(), reason="fontc not in venv")
def test_a_relocation_that_cannot_fit_keeps_the_avar2_font(tmp_path, monkeypatch):
    """S1(b): a relocation that overflows gvar's per-glyph header ceiling must
    NOT cost the avar2 font.

    The S1 recipe reproduces the overflow (one control axis with brace layers
    plus a 1 % ORIGIN nudge). Before this, the studio answered by serving the
    plain fallback — 10 axes, no avar2 table, `last_build_status: ok`. Now the
    un-relocated build is kept (every axis, avar2 table, compiled default) and
    the failure is reported: the report carries it, health exposes
    `origin_relocation_error`, and the status reads "degraded" (not "ok", and
    deliberately not "failed" — that would close the build gate and stop the
    designer nudging the ORIGIN row back)."""
    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "1")

    def _boom(font, log=None, **kwargs):
        raise RuntimeError("glyph 'g' has too many variation tuples")

    monkeypatch.setattr(pipeline, "_fit_gvar_headers", _boom)

    workdir = tmp_path / "work"
    workdir.mkdir(parents=True)
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(CRISPY_MINI, src)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({
        "sources": [str(src)],
        "avar2": {"CrispyMiniTest.ttf": MAPPINGS},
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline, "_load_axis_metadata", lambda: METADATA)

    report = {}
    out = pipeline._build_in_process(config_path, workdir, str(FONTC),
                                     relocation_report=report)
    assert out.exists()
    assert "too many variation tuples" in (report.get("error") or ""), report

    tt = TTFont(str(out))
    fvar = {a.axisTag: a for a in tt["fvar"].axes}
    assert "wght" in fvar, f"the mapped axis must survive: {list(fvar)}"
    assert fvar["XTRA"].defaultValue == 94.0, (
        f"the default must stay at the compiled origin, got {fvar['XTRA'].defaultValue}")
    assert "avar" in tt, "the avar2 table must survive the failed relocation"
    kept_tuples = {g: len(v) for g, v in tt["gvar"].variations.items()}
    kept_bytes = out.read_bytes()
    tt.close()

    # "Kept the un-relocated build" has to mean the VARIATION DATA is the
    # compile's, not merely that the axes and the avar2 table are still there:
    # the round-11 review rightly refused a 792 KB font whose gvar had shrunk
    # 15x, because axes-with-no-deltas is as wrong for the preview as the
    # 10-axis fallback, only quieter. Build the same config with the
    # relocation switched off and require the two outputs to be identical —
    # the failed attempt must leave nothing of itself on disk.
    ref_dir = workdir / "ref"
    ref_dir.mkdir()
    reference = pipeline._build_in_process(config_path, ref_dir, str(FONTC),
                                          relocate_default=False)
    assert reference.read_bytes() == kept_bytes, (
        "the failed relocation must leave the un-relocated compile untouched")
    ref_tt = TTFont(str(reference))
    assert {g: len(v) for g, v in ref_tt["gvar"].variations.items()} == kept_tuples
    assert sum(kept_tuples.values()) > 0, "the kept font must carry variation data"
    ref_tt.close()

    assert pipeline._build_status(report["error"]) == "degraded"
    assert pipeline._build_status(None) == "ok"
