"""Config-bundle export/import — the portable studio configuration.

``build_export`` captures everything the studio authored for one source
(control axes + layers, avar2 CSV, transform on/off + params) into a
single bundle dict; ``validate_bundle`` gates an import on the target
source actually supplying the axes the config depends on, and
``apply_bundle`` is all-or-nothing — it re-runs validation and writes
nothing unless the report is clean.
"""

from __future__ import annotations

import pytest

from avar2_studio import config_port, control_axes
from avar2_studio.transforms import config as tx_config
from avar2_studio.transforms import registry


def _seed_studio_config(source_path):
    """Author one of each artifact: a control axis with a brace layer,
    plus the uniform SPAC transform enabled."""
    control_axes.add_axis(source_path, "crbr", "Crossbar", 0, -100, 100)
    control_axes.apply_layer_delta(
        source_path, "crbr", add=[{"glyph": "e", "location": {"crbr": 100}}]
    )
    registry.set_active(
        source_path,
        [{"type": "spac", "enabled": True, "params": {"min": -20, "max": 40}}],
    )


def test_build_export_captures_fingerprint_and_authored_state(crispy_source, crispy_csv):
    """A fresh source exports the format markers, the source-axis
    fingerprint, the CSV text, and empty sidecar payloads."""
    bundle = config_port.build_export(crispy_source, crispy_csv)

    assert bundle["format"] == "avar2-studio-config"
    assert bundle["format_version"] == 1
    assert bundle["source"]["family_name"] == "CrispyMini"
    # .glyphs carries no explicit default, so get_axes reports the axis
    # minimum as the default.
    assert bundle["source"]["axes"] == [
        {"tag": "XTRA", "min": 94.0, "default": 94.0, "max": 3330.0, "has_master_coverage": True},
        {"tag": "XOPQ", "min": 2.0, "default": 2.0, "max": 1016.0, "has_master_coverage": True},
        {"tag": "YOPQ", "min": 2.0, "default": 2.0, "max": 462.0, "has_master_coverage": True},
    ]
    assert bundle["source"]["avar2_out_columns"] == ["XTRA", "XOPQ", "YOPQ"]
    assert bundle["avar2_csv"] == crispy_csv.read_text(encoding="utf-8-sig")
    assert bundle["control_axes"] == {"version": 1, "axes": []}
    assert bundle["transforms"] == {"version": 1, "transforms": []}


def test_export_validate_apply_round_trip(crispy_source, crispy_csv):
    """Export the seeded config, wipe the sidecars, and re-import:
    validation is clean and the sidecars come back with the authored
    axis + layer + enabled transform."""
    _seed_studio_config(crispy_source)
    bundle = config_port.build_export(crispy_source, crispy_csv)

    control_axes.sidecar_path_for(crispy_source).unlink()
    tx_config.sidecar_path_for(crispy_source).unlink()

    report = config_port.validate_bundle(bundle, crispy_source)
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["summary"] == {"axes": 1, "layers": 1, "mapping_rows": 1, "transforms": 1}

    apply_report = config_port.apply_bundle(bundle, crispy_source, crispy_csv)
    assert apply_report["ok"] is True

    axes = control_axes.list_axes(crispy_source)
    assert len(axes) == 1
    assert axes[0]["tag"] == "crbr"
    assert axes[0]["layers"] == [{"glyph": "e", "location": {"crbr": 100.0}}]
    assert tx_config.entries(crispy_source) == [
        {"type": "spac", "enabled": True, "params": {"min": -20, "max": 40}}
    ]


def test_bundle_from_glyphs_is_refused_on_designspace(crispy_source, crispy_csv, roboto_source):
    """The crispy bundle doesn't fit Roboto Delta: control axes are a
    .glyphs-only feature AND the CSV routes onto parametric axes the
    designspace doesn't have. apply_bundle writes nothing."""
    _seed_studio_config(crispy_source)
    bundle = config_port.build_export(crispy_source, crispy_csv)

    report = config_port.validate_bundle(bundle, roboto_source)
    assert report["ok"] is False
    assert any(".glyphs-only" in e for e in report["errors"])
    for tag in ("XOPQ", "XTRA", "YOPQ"):
        assert any(f"parametric axis '{tag}'" in e for e in report["errors"])

    apply_report = config_port.apply_bundle(
        bundle, roboto_source, roboto_source.parent / "RobotoDeltaMini-avar.csv"
    )
    assert apply_report["ok"] is False
    # All-or-nothing: no sidecars next to the designspace.
    assert not control_axes.sidecar_path_for(roboto_source).exists()
    assert not tx_config.sidecar_path_for(roboto_source).exists()


def test_empty_csv_bundle_leaves_existing_csv_untouched(crispy_source, crispy_csv):
    """A bundle with an empty avar2_csv is non-destructive by design:
    validation passes with a warning and apply skips the CSV write."""
    bundle = config_port.build_export(crispy_source, crispy_csv)
    bundle["avar2_csv"] = ""
    before = crispy_csv.read_bytes()

    report = config_port.validate_bundle(bundle, crispy_source)
    assert report["ok"] is True
    assert report["warnings"]  # the "CSV left untouched" warning

    apply_report = config_port.apply_bundle(bundle, crispy_source, crispy_csv)
    assert apply_report["ok"] is True
    assert crispy_csv.read_bytes() == before
