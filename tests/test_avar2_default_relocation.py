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

import os
import shutil
import time
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


def _build(tmp_path, monkeypatch, mappings, sub="a", report=None):
    workdir = tmp_path / sub / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    src = tmp_path / sub / "CrispyMini.glyphs"
    if not src.exists():   # a second build must see the previous build's source
        shutil.copy2(CRISPY_MINI, src)
    config_path = tmp_path / sub / "config.yaml"
    config_path.write_text(yaml.safe_dump({
        "sources": [str(src)],
        "avar2": {"CrispyMiniTest.ttf": mappings},
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline, "_load_axis_metadata", lambda: METADATA)
    return pipeline._build_in_process(config_path, workdir, str(FONTC),
                                      relocation_report=report)


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
    assert (pipeline._font_content_digest(reference)
            == pipeline._font_content_digest(out)), (
        "the failed relocation must leave the un-relocated compile untouched")
    ref_tt = TTFont(str(reference))
    assert {g: len(v) for g, v in ref_tt["gvar"].variations.items()} == kept_tuples
    assert sum(kept_tuples.values()) > 0, "the kept font must carry variation data"
    ref_tt.close()

    assert pipeline._build_status(report["error"]) == "degraded"
    assert pipeline._build_status(None) == "ok"


@pytest.mark.skipif(not FONTC.exists(), reason="fontc not in venv")
def test_a_relocation_is_memoised_and_reused(tmp_path, monkeypatch):
    """S1(a'): the instancer (~27 s on Crispy) runs once per (compiled font,
    target window, tolerance) — a later build whose compile is unchanged reuses
    the relocated font and must serve the very same bytes."""
    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "1")
    fits, instances = [], []
    real_fit = pipeline._fit_gvar_headers
    monkeypatch.setattr(pipeline, "_fit_gvar_headers",
                        lambda font, **kw: (fits.append(1), real_fit(font, **kw))[1])
    from fontTools.varLib import instancer as _instancer_mod
    real_instantiate = _instancer_mod.instantiateVariableFont

    def counting_instantiate(*a, **kw):
        instances.append(1)
        return real_instantiate(*a, **kw)

    monkeypatch.setattr(_instancer_mod, "instantiateVariableFont", counting_instantiate)

    first_report = {}
    first = _build(tmp_path, monkeypatch, MAPPINGS, sub="a", report=first_report)
    assert first_report.get("memo") == "miss", first_report
    assert (len(fits), len(instances)) == (1, 1)

    # A MAPPING-only change must still hit the memo (the compile did not
    # change) and still regenerate the avar2 table — the memo stores the
    # post-relocation, pre-gen-avar2 font.
    edited = [dict(m) for m in MAPPINGS]
    edited[1] = {"in": {"wght": 900}, "out": {"XTRA": 450.0, "XOPQ": 700.0}}
    config = tmp_path / "a" / "config.yaml"
    config.write_text(yaml.safe_dump({
        "sources": [str(tmp_path / "a" / "CrispyMini.glyphs")],
        "avar2": {"CrispyMiniTest.ttf": edited},
    }), encoding="utf-8")

    second_report = {}
    second = _build(tmp_path, monkeypatch, edited, sub="a", report=second_report)
    assert second_report.get("memo") == "hit", second_report
    assert second_report.get("error") is None
    assert (len(fits), len(instances)) == (1, 1), (
        "a memo hit must skip both the instancer and the header fit")
    assert (pipeline._font_content_digest(second)
            == pipeline._font_content_digest(first)), (
        "a memo hit must serve the same font content the uncached run produced")
    # The memo holds the post-relocation, PRE-gen-avar2 font: the built font
    # carries an avar table, every memo entry does not. That is why a hit still
    # re-runs gen-avar2 and picks up a mapping edit.
    memo_dir = second.parent / pipeline.RELOCATION_MEMO_DIRNAME
    memos = sorted(memo_dir.glob("*.ttf"))
    assert memos, "the successful relocation must be memoised"
    memo_tt = TTFont(str(memos[-1]))
    assert "avar" not in memo_tt, "the memo must sit BEFORE gen-avar2"
    memo_tt.close()
    built_tt = TTFont(str(second))
    assert "avar" in built_tt, "the built font must carry the regenerated avar2"
    built_tt.close()


def test_a_failed_relocation_is_memoised(tmp_path, monkeypatch):
    """The failure path is the one the designer hits repeatedly while walking
    the ORIGIN row back: the second build must answer in one lookup instead of
    paying the instancer again."""
    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "1")
    fits = []

    def boom(font, log=None, **kwargs):
        fits.append(1)
        raise RuntimeError("glyph 'g' has too many variation tuples")

    monkeypatch.setattr(pipeline, "_fit_gvar_headers", boom)

    first_report = {}
    first = _build(tmp_path, monkeypatch, MAPPINGS, sub="a", report=first_report)
    first_bytes = first.read_bytes()
    assert first_report.get("memo") == "miss", first_report
    assert "too many variation tuples" in (first_report.get("error") or "")

    second_report = {}
    second = _build(tmp_path, monkeypatch, MAPPINGS, sub="a", report=second_report)
    assert second_report.get("memo") == "hit", second_report
    assert second_report.get("error") == first_report["error"], (
        "the cached verdict must be the same error, not a paraphrase")
    assert len(fits) == 1, "the failed fit must not run twice for the same key"
    assert second.read_bytes() == first_bytes


@pytest.mark.skipif(not FONTC.exists(), reason="fontc not in venv")
def test_a_source_edit_invalidates_the_memo(tmp_path, monkeypatch):
    """The other half of the contract: the key is the COMPILED font's content,
    so an edit that changes the compile must miss rather than serve a stale
    relocated font."""
    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "1")
    first_report = {}
    _build(tmp_path, monkeypatch, MAPPINGS, sub="a", report=first_report)
    assert first_report.get("memo") == "miss", first_report

    # A real content change: one glyph's advance width (Glyphs 3 spelling).
    src = tmp_path / "a" / "CrispyMini.glyphs"
    text = src.read_text(encoding="utf-8")
    marker = "width = "
    start = text.index(marker) + len(marker)
    end = text.index(";", start)
    width = int(text[start:end])
    src.write_text(text[:start] + str(width + 7) + text[end:], encoding="utf-8")

    second_report = {}
    _build(tmp_path, monkeypatch, MAPPINGS, sub="a", report=second_report)
    assert second_report.get("memo") == "miss", (
        "a changed compile must not reuse the old relocated font")


def test_the_plain_build_picker_never_returns_a_memo_file(tmp_path):
    """A memo entry is a relocated avar2 font from an earlier compile, and it
    lives INSIDE the build dir. The plain-fallback picker must not hand it back
    as "the font that was just built" (round-11 review)."""
    out = tmp_path / "build"
    memo = out / pipeline.RELOCATION_MEMO_DIRNAME
    memo.mkdir(parents=True)
    built = out / "Crispy[wght].ttf"
    built.write_bytes(b"built")
    cached = memo / ("a" * 32 + ".ttf")
    cached.write_bytes(b"memo")
    os.utime(cached, (time.time() + 60, time.time() + 60))  # newer on purpose
    assert pipeline._pick_variable_font(out) == built
    assert "memo" not in str(pipeline._pick_variable_font(out))


def test_the_degraded_oracle_uses_the_whole_authored_avar(tmp_path, monkeypatch):
    """S1(c), round-12 review: under `origin_relocation_error` the oracle must
    evaluate the authored mapping — the WHOLE avar table, VarStore included, as
    the built font's own path does. Serialising avar1 segment maps loses the
    avar2 mapping, which would return the ORIGIN row's out at every input."""
    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "1")

    def _boom(font, log=None, **kwargs):
        raise RuntimeError("glyph 'g' has too many variation tuples")

    monkeypatch.setattr(pipeline, "_fit_gvar_headers", _boom)
    report = {}
    _build(tmp_path, monkeypatch, MAPPINGS, sub="a", report=report)
    authored = report.get("authored_avar")
    assert authored and Path(authored).exists(), report
    tiny = TTFont(authored)
    assert {"fvar", "avar"} <= {t for t in tiny.keys() if t != "GlyphOrder"}
    assert tiny["avar"].majorVersion == 2, "the VarStore must survive the copy"
    tiny.close()

    monkeypatch.setattr(state, "LAST_ORIGIN_RELOCATION_ERROR", "over the ceiling")
    monkeypatch.setattr(state, "LAST_ORIGIN_AUTHORED_AVAR", authored)

    # At the ORIGIN row's own inputs the parametric axes read that row's out.
    at_default = pipeline._evaluate_mapped_location({})
    assert abs(at_default["XTRA"] - 800.0) < 1.0, at_default
    assert abs(at_default["XOPQ"] - 300.0) < 1.0, at_default

    # At another authored row's inputs they must MOVE to that row's out — this
    # is what the segment-only version could not do.
    at_stop = pipeline._evaluate_mapped_location({"wght": 900.0})
    assert abs(at_stop["XTRA"] - 400.0) < 1.0, at_stop
    assert abs(at_stop["XOPQ"] - 700.0) < 1.0, at_stop
    assert abs(at_stop["XTRA"] - at_default["XTRA"]) > 100, "the axis must move"

    # A second authored row, on the other side of the default: acceptance (ii)
    # asks for two, so one moving axis cannot be a coincidence.
    at_low = pipeline._evaluate_mapped_location({"wght": 1.0})
    assert abs(at_low["XTRA"] - 3000.0) < 1.0, at_low
    assert abs(at_low["XOPQ"] - 10.0) < 1.0, at_low


def test_a_degraded_build_records_the_authored_mapping(tmp_path, monkeypatch):
    """The degraded build itself must produce the table the oracle uses, with
    the parametric defaults at the ORIGIN row's out."""
    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "1")

    def _boom(font, log=None, **kwargs):
        raise RuntimeError("glyph 'g' has too many variation tuples")

    monkeypatch.setattr(pipeline, "_fit_gvar_headers", _boom)
    report = {}
    _build(tmp_path, monkeypatch, MAPPINGS, sub="a", report=report)
    authored = Path(report.get("authored_avar") or "")
    assert authored.exists(), report
    tiny = TTFont(str(authored))
    axes = {a.axisTag: a for a in tiny["fvar"].axes}
    assert axes["XTRA"].defaultValue == 800.0, axes.keys()   # ORIGIN row's out
    assert axes["XOPQ"].defaultValue == 300.0
    assert tiny["avar"].majorVersion == 2
    tiny.close()


def test_the_authored_table_is_evicted_with_its_memo_entry(tmp_path):
    """`<key>.authored.ttf` is a sidecar of a failure entry, not an outcome:
    it must not count against the keep-2 budget, and it must go when its entry
    goes (otherwise the degraded oracle outlives the verdict it belongs to)."""
    memo = tmp_path / pipeline.RELOCATION_MEMO_DIRNAME
    memo.mkdir()
    now = time.time()
    for i, key in enumerate(("aaaa", "bbbb", "cccc")):
        entry = memo / f"{key}.json"
        entry.write_text("{}", encoding="utf-8")
        os.utime(entry, (now + i, now + i))
        sidecar = memo / f"{key}{pipeline.AUTHORED_SUFFIX}"
        sidecar.write_bytes(b"x")
        os.utime(sidecar, (now + i, now + i))
    orphan = memo / f"dddd{pipeline.AUTHORED_SUFFIX}"
    orphan.write_bytes(b"x")
    os.utime(orphan, (now + 9, now + 9))     # newest of all, still an orphan

    pipeline._memo_evict(memo)

    names = sorted(p.name for p in memo.iterdir())
    assert "cccc.json" in names and "bbbb.json" in names, names
    assert "aaaa.json" not in names, "the oldest entry must be evicted"
    assert "cccc.authored.ttf" in names and "bbbb.authored.ttf" in names, names
    assert "aaaa.authored.ttf" not in names, "the sidecar follows its entry"
    assert not orphan.exists(), "an authored file with no entry is garbage"
