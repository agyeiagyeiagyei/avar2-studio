"""T3.8: a set-default export is the preview font re-origined at P — built
from the same resolved ACTIVE source (control-axis shadow + source-stage
transforms), not the raw original. Round 6 found the export silently
dropped every studio axis and transform."""
import shutil
import sys
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from test_worker_shadow import _CrispyServer

from avar2_studio import build_jobs, control_axes, state
from avar2_studio.transforms import config as transforms_config

FONTC = Path(sys.executable).with_name("fontc")
needs_fontc = pytest.mark.skipif(not FONTC.exists(), reason="fontc not next to the test interpreter")


def _coords(path, loc, glyphs):
    f = TTFont(str(path))
    axes = {a.axisTag: a for a in f["fvar"].axes}
    full = {t: float(loc.get(t, axes[t].defaultValue)) for t in axes}
    inst = instantiateVariableFont(f, full, inplace=True, optimize=False)
    return {g: inst["glyf"][g].getCoordinates(inst["glyf"])[0] for g in glyphs}


@needs_fontc
def test_set_default_export_keeps_the_studio_axes_and_outlines(crispy_source, crispy_csv, monkeypatch):
    # A control axis with a brace, and a source-stage transform: the
    # active source is now the transformed shadow, not the original.
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(crispy_source, "ymod",
                                   add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}])
    transforms_config.save(crispy_source, [{"type": "round_corners", "enabled": True, "params": {}}])
    monkeypatch.setattr(state, "ORIGINAL_PATH", crispy_source)
    monkeypatch.setattr(state, "GLYPHS_PATH", crispy_source)
    # The lazy project-path getters memoise per project: clear what earlier
    # tests left (the worker's _bind_server_globals does the same).
    for name in ("PREVIEW_DIR", "PREVIEW_CSV_PATH", "PREVIEW_CONFIG_PATH", "CSV_PATH", "BUILD_DIR"):
        monkeypatch.setattr(state, name, None)
    state._DECLARED_RANGES_CACHE.clear()
    # What the server does on load: the preview CSV + config the avar2 step reads.
    from avar2_studio import pipeline
    pipeline._initialize_preview_csv_from_glyphs()
    pipeline._initialize_preview_config_from_glyphs()

    queue = build_jobs.JobQueue(_CrispyServer())  # real worker (state set above)
    queue.request_build()
    assert queue.wait_for_idle(timeout=600), f"build failed: {state.LAST_BUILD_ERROR}"
    preview = Path(state.VARIABLE_FONT_PATH)
    assert preview.exists()
    preview_tags = {a.axisTag for a in TTFont(str(preview))["fvar"].axes}
    assert {"opsz", "wght", "wdth"} <= preview_tags, \
        f"preview fvar: {sorted(preview_tags)} | avar2 error: {state.LAST_AVAR2_ERROR} | build error: {state.LAST_BUILD_ERROR} | origin warning: {state.LAST_ORIGIN_WARNING}"

    # The server re-resolves the active source on every edit/save, so by the
    # time an export is requested state.GLYPHS_PATH is the transformed
    # shadow; do the same here before the export job stages it.
    pipeline._resolve_active_source()
    assert Path(state.GLYPHS_PATH) != crispy_source, "active source should be the shadow"
    # "Narrow Thin 144" is an authored row: in opsz 144 / wght 100 / wdth 20 → out 94/6/6.
    job_id = queue.submit_export(hidden_axes=[], default_location={"opsz": 144, "wght": 100, "wdth": 20})
    assert queue.wait_for_idle(timeout=600)
    export = queue.export_artifact(job_id)
    assert export and Path(export).exists(), queue.status()

    pf, ef = TTFont(str(preview)), TTFont(str(export))
    assert {a.axisTag for a in ef["fvar"].axes} == {a.axisTag for a in pf["fvar"].axes}, \
        f"export axes {sorted(a.axisTag for a in ef['fvar'].axes)} != preview {sorted(a.axisTag for a in pf['fvar'].axes)}"
    assert {"ymod", "ROND"} <= {a.axisTag for a in ef["fvar"].axes}
    ex_defaults = {a.axisTag: a.defaultValue for a in ef["fvar"].axes}
    assert (ex_defaults["XTRA"], ex_defaults["XOPQ"], ex_defaults["YOPQ"]) == (94.0, 6.0, 6.0)

    glyphs = [g for g in ("e", "n", "o", "a", "H") if g in pf.getGlyphOrder()]
    for g in glyphs:
        assert len(pf["glyf"][g].getCoordinates(pf["glyf"])[0]) == len(ef["glyf"][g].getCoordinates(ef["glyf"])[0]), \
            f"{g}: point count differs — the export was built from a different source"
    stop = {"XTRA": 94.0, "XOPQ": 6.0, "YOPQ": 6.0}
    a, b = _coords(preview, stop, glyphs), _coords(export, stop, glyphs)
    worst = max(abs(p[0] - q[0]) + abs(p[1] - q[1]) for g in glyphs for p, q in zip(a[g], b[g]))
    assert worst <= 3.0, f"interior drift at the authored stop: {worst:.1f} u"
    shutil.rmtree(crispy_source.parent / ".avar2-studio" / "jobs", ignore_errors=True)
