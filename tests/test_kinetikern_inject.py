"""Kinetikern injection: phantom-point tuples per corrected instance, exact at
each instance, the default and the masters untouched, idempotent."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from avar2_studio.kinetikern import inject, sidecar

REPO = Path(__file__).resolve().parents[1]
FONTC = REPO / ".venv/bin/fontc"
MINI = REPO / "examples/crispy-mini/sources/CrispyMini.glyphs"
needs_fontc = pytest.mark.skipif(not FONTC.exists(), reason="fontc not in venv")


def _build(src, out):
    subprocess.run([str(FONTC), "-o", str(out), str(src), "--flatten-components",
                    "--decompose-transformed-components"], check=True, capture_output=True)
    return out


def _metrics(path, loc):
    """{glyph: (advance, lsb)} from hmtx of the font instantiated at ``loc``."""
    f = TTFont(str(path))
    axes = {a.axisTag: a for a in f["fvar"].axes}
    s = instantiateVariableFont(f, {t: float(loc.get(t, axes[t].defaultValue)) for t in axes}, inplace=True, optimize=False)
    return {n: tuple(s["hmtx"][n]) for n in s.getGlyphOrder()}


def _locations(font):
    axes = {a.axisTag: a for a in font["fvar"].axes}
    span = lambda t, k: axes[t].minValue + k * (axes[t].maxValue - axes[t].minValue)  # noqa: E731
    return {
        "near": {t: span(t, 0.25) for t in inject.PARAM_TAGS},   # inside "far"'s box: exercises narrowing
        "far": {t: span(t, 0.6) for t in inject.PARAM_TAGS},
        "edge": {"XTRA": span("XTRA", 0.8), "XOPQ": axes["XOPQ"].defaultValue, "YOPQ": span("YOPQ", 0.5)},
        # the default location itself, and one that only float noise separates from it
        "default": {t: axes[t].defaultValue for t in inject.PARAM_TAGS},
        "hair": {t: axes[t].defaultValue + 1e-7 for t in inject.PARAM_TAGS},
    }


def test_instance_locations_completes_from_the_mappings_csv(tmp_path):
    """instance_locations' avar-CSV completion goes through csv_io (the
    one mappings reader): quoted comma names parse whole, blank cells
    are skipped. Runs without fontc — the source read fails soft."""
    src = tmp_path / "F.glyphs"
    src.write_text("")  # unparseable source → CSV rows only
    (tmp_path / "F-avar.csv").write_text(
        'Instance Name,XTRA,XOPQ,YOPQ\n"Narrow, Thin",94.0,6.0,\nTall,50.0,2.0,3.0\n',
        encoding="utf-8",
    )
    assert inject.instance_locations(src) == {
        "Narrow, Thin": {"XTRA": 94.0, "XOPQ": 6.0},
        "Tall": {"XTRA": 50.0, "XOPQ": 2.0, "YOPQ": 3.0},
    }


@needs_fontc
def test_corrections_are_exact_at_each_instance_and_nowhere_else(tmp_path):
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(MINI, src)
    baseline = _build(src, tmp_path / "baseline.ttf")
    font = TTFont(str(baseline))
    locs = _locations(font)
    glyphs = [n for n in font.getGlyphOrder() if font["glyf"][n].numberOfContours != 0]
    deltas = {"near": {g: (20, 30) for g in glyphs}, "far": {g: (-10, 5) for g in glyphs}, "edge": {g: (40, 40) for g in glyphs[:10]},
              "default": {g: (7, 9) for g in glyphs}, "hair": {g: (7, 9) for g in glyphs}}
    added = inject.inject(font, [(locs[k], deltas[k]) for k in locs])
    # near, far, the ONE base tuple for default (hair merges into it), and an edge
    # tuple on every glyph: with a base in play, a location without its own
    # correction still needs a tuple to come out exact
    assert added == 4 * len(glyphs)
    out = tmp_path / "injected.ttf"
    font.save(str(out))

    for key, loc in locs.items():
        before, after = _metrics(baseline, loc), _metrics(out, loc)
        for g in glyphs:
            dl, dr = deltas[key].get(g, (0, 0))
            assert abs((after[g][1] - before[g][1]) - dl) <= 1, (key, g, before[g], after[g])
            assert abs((after[g][0] - before[g][0]) - (dl + dr)) <= 1, (key, g, before[g], after[g])
    corner = {a.axisTag: a.maxValue for a in font["fvar"].axes}
    far = _metrics(out, corner)
    for g in glyphs:  # past the outermost instance a correction holds, as a sparse master would
        assert far[g][0] >= _metrics(baseline, corner)[g][0], g
    assert TTFont(str(out))["HVAR"].table.AdvWidthMap is not None


@needs_fontc
def test_apply_to_font_reads_the_sidecar_and_is_idempotent(tmp_path):
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(MINI, src)
    vf = _build(src, tmp_path / "CrispyMini.ttf")
    untouched = vf.read_bytes()
    assert inject.apply_to_font(vf, src) == vf and vf.read_bytes() == untouched, "no entries: nothing written"

    font = TTFont(str(vf))
    loc = _locations(font)["near"]
    glyphs = [n for n in font.getGlyphOrder() if font["glyf"][n].numberOfContours != 0][:12]
    sidecar.set_entry(src, "Custom spot", 0.0, {g: [15, 25] for g in glyphs}, location=loc)
    inject.apply_to_font(vf, src)
    once = _metrics(vf, loc)
    inject.apply_to_font(vf, src)
    twice = _metrics(vf, loc)
    assert once == twice, "re-running the chain must not stack corrections"
    before = _metrics(tmp_path / "CrispyMini.ttf", loc) if False else None  # noqa: F841 (baseline below)
    base = _metrics(_build(src, tmp_path / "again.ttf"), loc)
    for g in glyphs:
        assert once[g][1] - base[g][1] == 15 and once[g][0] - base[g][0] == 40, (g, base[g], once[g])


@needs_fontc
def test_strip_removes_only_the_recorded_tuples(tmp_path):
    """T2.13: a genuine compile tuple that happens to be phantom-only on the
    parametric axes (Crispy's `three` has 22) must survive strip(); only
    the tuples inject() recorded in the marker table go."""
    from fontTools.ttLib.tables.TupleVariation import TupleVariation
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(MINI, src)
    vf = _build(src, tmp_path / "vf.ttf")
    font = TTFont(str(vf))
    glyph = next(n for n in font.getGlyphOrder() if font["glyf"][n].numberOfContours > 0)
    variations = font["gvar"].variations.setdefault(glyph, [])
    n = len(variations[0].coordinates) if variations else inject._point_count(font["glyf"][glyph], font["glyf"])
    planted = [None] * n
    planted[-4], planted[-3] = (3, 0), (5, 0)
    variations.append(TupleVariation({"XOPQ": (0.0, 1.0, 1.0)}, planted))  # a master-corner peak
    before = len(variations)
    locs = _locations(font)
    added = inject.inject(font, [(locs["far"], {glyph: (20, 30)})])
    assert added >= 1 and inject.MARKER_TABLE in font
    assert len(font["gvar"].variations[glyph]) == before + added
    removed = inject.strip(font)
    assert removed == added
    assert inject.MARKER_TABLE not in font
    assert len(font["gvar"].variations[glyph]) == before, "a genuine phantom-only tuple was stripped"
    assert inject.strip(font) == 0, "nothing recorded → nothing to remove"


@needs_fontc
def test_marker_survives_a_save_and_reload(tmp_path):
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(MINI, src)
    vf = _build(src, tmp_path / "vf.ttf")
    font = TTFont(str(vf))
    glyph = next(n for n in font.getGlyphOrder() if font["glyf"][n].numberOfContours > 0)
    added = inject.inject(font, [(_locations(font)["far"], {glyph: (20, 30)})])
    out = tmp_path / "injected.ttf"
    font.save(str(out))
    reloaded = TTFont(str(out))
    assert inject.strip(reloaded) == added
