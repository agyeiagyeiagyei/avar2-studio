"""C7 — one committed build config drives the whole export.

``<stem>-build.json`` holds the transforms to run (with params), which axes to
hide, the resting style, the named instances / STAT switch and the output
formats. The build reads it by default when it sits beside the source, so CI
needs no flags; flags still override it. Everything design-authored stays
studio-only: a config key naming control axes, grade, Kinetikern or instance
coordinates is a usage error (exit 2) pointing at the studio
(findings.md, phase C item C7).
"""

from __future__ import annotations

import json

import pytest

from avar2_studio import build_config


def _cfg(**overrides):
    base = {
        "format": build_config.BUILD_CONFIG_FORMAT,
        "format_version": build_config.BUILD_CONFIG_VERSION,
        "axes": {"hide": ["XOPQ"]},
        "default": {"wght": 400, "opsz": 14},
        "transforms": [],
        "instances": {"fix": True, "stat": True},
        "formats": ["ttf", "woff2"],
    }
    base.update(overrides)
    return base


def test_the_path_is_the_source_sibling(tmp_path):
    assert build_config.path_for(tmp_path / "Crispy.glyphs") == tmp_path / "Crispy-build.json"


def test_a_full_config_validates(tmp_path):
    cfg = build_config.validate(_cfg())
    assert cfg["axes"]["hide"] == ["XOPQ"]
    assert cfg["default"] == {"wght": 400.0, "opsz": 14.0}
    assert cfg["instances"] == {"fix": True, "stat": True}
    assert cfg["formats"] == ["ttf", "woff2"]


@pytest.mark.parametrize("key", ["control", "grade", "kinetikern",
                                 "control_axes", "instance_coordinates"])
def test_design_authored_keys_are_refused_with_a_pointer_to_the_studio(key):
    with pytest.raises(ValueError) as excinfo:
        build_config.validate(_cfg(**{key: {}}))
    message = str(excinfo.value)
    assert key in message
    assert "studio" in message, "the message must say where to edit it"


def test_unknown_keys_are_refused():
    with pytest.raises(ValueError) as excinfo:
        build_config.validate(_cfg(nonsense=1))
    assert "unknown key 'nonsense'" in str(excinfo.value)


def test_axes_only_take_hide():
    with pytest.raises(ValueError) as excinfo:
        build_config.validate(_cfg(axes={"default": {"wght": 400}}))
    assert "only takes 'hide'" in str(excinfo.value)


def test_instances_only_take_fix_and_stat():
    with pytest.raises(ValueError) as excinfo:
        build_config.validate(_cfg(instances={"coordinates": {}}))
    assert "only takes 'fix' and 'stat'" in str(excinfo.value)


def test_formats_are_checked():
    with pytest.raises(ValueError):
        build_config.validate(_cfg(formats=["otf"]))
    # An empty/absent list means the default (ttf), not "no output".
    assert build_config.validate(_cfg(formats=[]))["formats"] == ["ttf"]
    assert build_config.validate(_cfg(formats=None))["formats"] == ["ttf"]


def test_instances_fold_into_the_gftools_transforms():
    entries = build_config.transform_entries(build_config.validate(_cfg()))
    types = {e["type"]: e["enabled"] for e in entries}
    assert types == {"fix_instances": True, "gen_stat": True}


def test_instances_false_disable_the_defaults():
    cfg = build_config.validate(_cfg(instances={"fix": False, "stat": False}))
    assert build_config.transform_entries(cfg) == []


def test_a_config_without_transforms_leaves_the_sidecar_alone():
    cfg = build_config.validate(_cfg(transforms=None, instances={}))
    assert build_config.transform_entries(cfg) is None


def test_load_reads_and_validates(tmp_path):
    path = tmp_path / "Crispy-build.json"
    path.write_text(json.dumps(_cfg()), encoding="utf-8")
    assert build_config.load(path)["formats"] == ["ttf", "woff2"]

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        build_config.load(path)
    assert "not valid JSON" in str(excinfo.value)


def test_write_config_from_sidecars(tmp_path):
    source = tmp_path / "Crispy.glyphs"
    source.write_text("<glyphs/>", encoding="utf-8")
    (tmp_path / "Crispy-transforms.json").write_text(json.dumps({
        "version": 1,
        "transforms": [
            {"type": "fix_instances", "enabled": True, "params": {}},
            {"type": "gen_stat", "enabled": True, "params": {}},
            {"type": "slant", "enabled": True, "params": {"angle": -11}},
        ],
    }), encoding="utf-8")

    written = build_config.write(build_config.path_for(source),
                                 build_config.from_sidecars(source))
    cfg = json.loads(written.read_text())
    assert [e["type"] for e in cfg["transforms"]] == ["slant"], (
        "the gftools steps move into `instances`, the rest stay transforms")
    assert cfg["instances"] == {"fix": True, "stat": True}
    assert cfg["formats"] == ["ttf", "woff2"]
    assert build_config.validate(cfg), "what --write-config produces must load"
