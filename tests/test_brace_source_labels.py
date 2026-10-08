"""Fontra source labels for brace layers: a layer at a master corner is
named by the master, a layer at a named instance's location is named by
the instance (source instances and studio CSV rows alike), and anything
else falls back to the bare control-axis value. Named labels are the name
alone — no control-axis coordinates. Brace layers land in the shadow
ordered alphabetically by label so the editor's sources list reads A→Z."""
import shutil
from pathlib import Path

import glyphsLib
from glyphsLib.classes import GSInstance

from avar2_studio import control_axes

CRISPY_SOURCES = Path(__file__).resolve().parent.parent / "examples" / "crispy-mini" / "sources"


def _project(tmp_path):
    """CrispyMini source + its -avar.csv in one throwaway directory."""
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(CRISPY_SOURCES / "CrispyMini.glyphs", src)
    shutil.copy2(CRISPY_SOURCES / "CrispyMini-avar.csv", tmp_path / "CrispyMini-avar.csv")
    return src


def _brace_layers(shadow_path, glyph="f"):
    """The glyph's coordinate-carrying (brace) layers, in file order."""
    font = glyphsLib.GSFont(str(shadow_path))
    return [
        layer for layer in font.glyphs[glyph].layers
        if dict(layer.attributes or {}).get("coordinates")
    ]


def _labels(shadow_path, glyph="f"):
    """{parametric coordinate tuple: fontra source-name} for the glyph's
    brace layers."""
    font = glyphsLib.GSFont(str(shadow_path))
    control_index = [a.axisTag for a in font.axes].index("crbr")
    out = {}
    for layer in font.glyphs[glyph].layers:
        coords = dict(layer.attributes or {}).get("coordinates")
        if coords:
            key = tuple(float(v) for i, v in enumerate(coords) if i != control_index)
            out[key] = layer.userData.get("xyz.fontra.source-name")
    return out


def test_csv_instance_location_is_labelled_with_the_instance_name(tmp_path):
    src = _project(tmp_path)
    control_axes.add_axis(src, "crbr", "Crossbar", 0, -100, 100)
    control_axes.apply_layer_delta(
        src, "crbr",
        add=[
            # "Narrow Heavy 12" in CrispyMini-avar.csv
            {"glyph": "f", "location": {"crbr": 100, "XTRA": 222.8, "XOPQ": 279.6, "YOPQ": 250.2}},
            # a master corner — the master name wins over any instance
            {"glyph": "f", "location": {"crbr": 100, "XTRA": 94, "XOPQ": 2, "YOPQ": 2}},
            # no master or instance here — bare control value as before
            {"glyph": "f", "location": {"crbr": 100, "XTRA": 999, "XOPQ": 999, "YOPQ": 99}},
        ],
    )
    labels = _labels(control_axes.regenerate_shadow(src))
    assert labels[(222.8, 279.6, 250.2)] == "Narrow Heavy 12"
    assert labels[(94.0, 2.0, 2.0)] == "XTRA94-XOPQ2-YOPQ2"
    assert labels[(999.0, 999.0, 99.0)] == "crbr 100"


def test_source_instance_location_is_labelled_with_the_instance_name(tmp_path):
    src = _project(tmp_path)
    font = glyphsLib.GSFont(str(src))
    inst = GSInstance()
    inst.name = "Mid Narrow"
    inst.axes = [1000.0, 500.0, 100.0]
    font.instances.append(inst)
    font.save(str(src))

    control_axes.add_axis(src, "crbr", "Crossbar", 0, -100, 100)
    control_axes.apply_layer_delta(
        src, "crbr",
        add=[{"glyph": "f", "location": {"crbr": 100, "XTRA": 1000, "XOPQ": 500, "YOPQ": 100}}],
    )
    labels = _labels(control_axes.regenerate_shadow(src))
    assert labels[(1000.0, 500.0, 100.0)] == "Mid Narrow"


def test_brace_layers_are_ordered_alphabetically_by_label(tmp_path):
    src = _project(tmp_path)
    control_axes.add_axis(src, "crbr", "Crossbar", 0, -100, 100)
    control_axes.apply_layer_delta(
        src, "crbr",
        add=[
            # Added non-alphabetically: "Ultra Wide Thin 12", a master
            # corner, "Narrow Heavy 12" (all CSV instance / master names).
            {"glyph": "f", "location": {"crbr": 100, "XTRA": 2807.8, "XOPQ": 67.6, "YOPQ": 49.4}},
            {"glyph": "f", "location": {"crbr": 100, "XTRA": 94, "XOPQ": 2, "YOPQ": 2}},
            {"glyph": "f", "location": {"crbr": 100, "XTRA": 222.8, "XOPQ": 279.6, "YOPQ": 250.2}},
        ],
    )
    layers = _brace_layers(control_axes.regenerate_shadow(src))
    names = [l.userData.get("xyz.fontra.source-name") for l in layers]
    assert names == ["Narrow Heavy 12", "Ultra Wide Thin 12", "XTRA94-XOPQ2-YOPQ2"]
