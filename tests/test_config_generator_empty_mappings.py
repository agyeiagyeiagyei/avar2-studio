"""A CSV whose rows carry no user-axis values is identity: the avar2
section empties instead of emitting `in: null` (which crashed builds)."""
import yaml

from avar2_studio.build.config_generator import generate_avar2_yaml_string, RowMapping


def _row(name, in_axes, out_axes):
    return RowMapping(instance_name=name, in_axes=in_axes, out_axes=out_axes,
                      out_axis_order=tuple(out_axes.keys()))


def test_rows_without_user_values_are_not_mappings():
    from decimal import Decimal
    rows = [_row("Default", {}, {"XOPQ": Decimal("202.5")})]
    text = generate_avar2_yaml_string(rows, "F.ttf")
    data = yaml.safe_load(text)
    assert data["avar2"]["F.ttf"] == []


def test_opsz_only_rows_vanish_when_opsz_is_skipped():
    from decimal import Decimal
    rows = [
        _row("A", {"opsz": Decimal("14")}, {"XOPQ": Decimal("1")}),
        _row("B", {"opsz": Decimal("14")}, {"XOPQ": Decimal("2")}),
    ]
    text = generate_avar2_yaml_string(rows, "F.ttf", skip_opsz_if_no_variation=True)
    assert yaml.safe_load(text)["avar2"]["F.ttf"] == []


def test_real_mappings_still_generate():
    from decimal import Decimal
    rows = [
        _row("A", {"wght": Decimal("1")}, {"XOPQ": Decimal("1")}),
        _row("B", {"wght": Decimal("1000")}, {"XOPQ": Decimal("1462")}),
    ]
    text = generate_avar2_yaml_string(rows, "F.ttf")
    entries = yaml.safe_load(text)["avar2"]["F.ttf"]
    assert len(entries) == 2 and all(e["in"] for e in entries)
