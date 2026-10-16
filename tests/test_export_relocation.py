"""T3.8: the export build does NOT run step-3.5 default relocation.

The export stages its own origin master (`_build_export_source`); running
the instancer rebase as well densified the shipped font ~7× (audit §2 H3,
decisions/0001+0007). `_build_in_process(..., relocate_default=...)` now
decides per call (env stays the default); the export passes False.
"""
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from fontTools.ttLib import TTFont

from avar2_studio import pipeline
from test_avar2_default_relocation import MAPPINGS, METADATA

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
