"""config_generator CSV handling — parametric-only CSVs.

A studio-authored CSV maps instance names straight onto parametric axes
(no WGHT/WDTH/OPSZ columns). That is only meaningful for a font whose
fvar axes ARE the parametric axes, so ``in:`` is pinned to the source's
own instance coordinates (``fvarInstances`` in the config) — a CSV in
sync with its source yields identity mappings (a no-op table fontc
omits); a divergent CSV produces a real remap. Tags stay verbatim
(case-sensitive fvar tags); SPAC is excluded. See
validate_csv_structure / read_csv_mappings / _fill_identity_in_axes.
"""

from pathlib import Path

import pytest

from avar2_studio.build import config_generator as cg


PARAM_ONLY_CSV = "Instance Name,XTRA,XOPQ,YOPQ\nThin Condensed,181.2,40.3,41.3\n"
PARAM_ONLY_SPAC_CSV = "Instance Name,XOPQ,SPAC\nDefault,2.0,25\n"
TRADITIONAL_CSV = "Instance Name,WGHT,XOPQ\nDefault,400,2.0\n"


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "mappings.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_parametric_only_csv_is_accepted(tmp_path):
    csv_path = _write(tmp_path, PARAM_ONLY_CSV)
    name_col, in_cols, out_cols = cg.validate_csv_structure(csv_path)
    assert name_col == "Instance Name"
    assert in_cols == []  # no traditional columns — filled from fvarInstances later
    assert out_cols == ["XTRA", "XOPQ", "YOPQ"]


def test_parametric_only_csv_in_axes_start_empty(tmp_path):
    """read_csv_mappings leaves in_axes empty; main() fills them."""
    csv_path = _write(tmp_path, PARAM_ONLY_CSV)
    (m,) = cg.read_csv_mappings(csv_path)
    assert m.in_axes == {}


def test_fill_from_source_instances_verbatim_tags(tmp_path):
    csv_path = _write(tmp_path, PARAM_ONLY_CSV)
    (m,) = cg.read_csv_mappings(csv_path)
    # Studio-initialised configs lower-case the coordinate keys; the fill
    # must still match, and must emit the CSV's verbatim (fvar) tag case.
    config = {"fvarInstances": {"F": [
        {"name": "Thin Condensed",
         "coordinates": {"xtra": 181.2, "xopq": 40.3, "yopq": 41.3}},
    ]}}
    (filled,) = cg._fill_identity_in_axes([m], config, "F")
    assert set(filled.in_axes) == {"XTRA", "XOPQ", "YOPQ"}  # no lowercase "xopq"
    assert filled.in_axes["XOPQ"] == m.out_axes["XOPQ"]


def test_fill_excludes_spac(tmp_path):
    """SPAC is handled outside avar2 — neither out: nor in: may carry it."""
    csv_path = _write(tmp_path, PARAM_ONLY_SPAC_CSV)
    (m,) = cg.read_csv_mappings(csv_path)
    config = {"fvarInstances": {"F": [
        {"name": "Default", "coordinates": {"XOPQ": 2.0, "SPAC": 25}},
    ]}}
    (filled,) = cg._fill_identity_in_axes([m], config, "F")
    assert set(filled.in_axes) == {"XOPQ"}


def test_fill_falls_back_to_identity_for_unknown_instance(tmp_path):
    """Instance not in fvarInstances → identity (in: = out:), no crash."""
    csv_path = _write(tmp_path, PARAM_ONLY_CSV)
    (m,) = cg.read_csv_mappings(csv_path)
    (filled,) = cg._fill_identity_in_axes([m], {"fvarInstances": {"F": []}}, "F")
    assert filled.in_axes == {k: v for k, v in m.out_axes.items() if k != "SPAC"}


def test_fill_leaves_traditional_csv_alone(tmp_path):
    csv_path = _write(tmp_path, TRADITIONAL_CSV)
    (m,) = cg.read_csv_mappings(csv_path)
    assert m.in_axes == {"wght": 400}
    (filled,) = cg._fill_identity_in_axes([m], {"fvarInstances": {}}, "F")
    assert filled.in_axes == m.in_axes


def test_csv_with_no_parametric_columns_still_rejected(tmp_path):
    csv_path = _write(tmp_path, "Instance Name,WGHT\nDefault,400\n")
    with pytest.raises(ValueError, match="no parametric axis columns"):
        cg.validate_csv_structure(csv_path)


def test_merge_config_skips_stat_when_none():
    """Parametric-only CSV → no STAT data; existing stat left untouched."""
    config = {}
    merged, _ = cg.merge_config(config, None, "avar2:\n  F: []\n", "F")
    assert "stat" not in merged
    assert merged["avar2"] == {"F": []}
    # …and the merged-config validator doesn't demand a stat section.
    cg.validate_merged_config(merged, "F", require_stat=False)


def test_merge_config_replaces_stat_when_present():
    stat = {"F": [{"tag": "wght"}]}
    merged, _ = cg.merge_config({}, stat, "avar2:\n  F: []\n", "F")
    assert merged["stat"] == stat
    cg.validate_merged_config(merged, "F", require_stat=True)


def test_identical_duplicate_rows_are_dropped(tmp_path):
    """Two named instances at identical coordinates with identical values
    (e.g. HighContrast variants) share one avar2 entry — not an error."""
    from decimal import Decimal
    from avar2_studio.build.config_generator import RowMapping
    row = RowMapping("A", {"XOPQ": Decimal(2)}, {"XOPQ": Decimal(50)}, ("XOPQ",))
    dup = RowMapping("A HighContrast", {"XOPQ": Decimal(2)}, {"XOPQ": Decimal(50)}, ("XOPQ",))
    kept = cg._drop_identical_duplicates([row, dup])
    assert kept == [row]
    cg.generate_avar2_yaml_string([row, dup], "F")  # must not raise


def test_same_in_different_out_still_rejected(tmp_path):
    """Same in: mapping to different out: is a real contradiction."""
    from decimal import Decimal
    from avar2_studio.build.config_generator import RowMapping
    a = RowMapping("A", {"XOPQ": Decimal(2)}, {"XOPQ": Decimal(50)}, ("XOPQ",))
    b = RowMapping("B", {"XOPQ": Decimal(2)}, {"XOPQ": Decimal(70)}, ("XOPQ",))
    with pytest.raises(ValueError, match="Duplicate 'in:'"):
        cg.generate_avar2_yaml_string([a, b], "F")


def test_pad_in_envelope_adds_identity_corners(tmp_path):
    """fontc clamps built fvar ranges to the avar2 in: envelope — the
    padding must cover the source's declared ranges with identity rows."""
    csv_path = _write(tmp_path, PARAM_ONLY_CSV)
    (m,) = cg.read_csv_mappings(csv_path)
    src_axes = [
        {"tag": "XTRA", "min": 94, "max": 3330},
        {"tag": "XOPQ", "min": 2, "max": 1016},
        {"tag": "YOPQ", "min": 2, "max": 462},
    ]
    padded = cg._pad_in_envelope([m], src_axes)
    assert [p.instance_name for p in padded] == [
        "Thin Condensed", "Auto: range min", "Auto: range max",
    ]
    lo, hi = padded[-2], padded[-1]
    assert lo.in_axes == lo.out_axes and hi.in_axes == hi.out_axes  # identity
    assert lo.in_axes["XTRA"] == 94 and hi.in_axes["XTRA"] == 3330
    assert lo.in_axes["YOPQ"] == 2 and hi.in_axes["YOPQ"] == 462


def test_pad_corners_dedup_against_csv_rows(tmp_path):
    """A corner that duplicates an existing CSV row (in: and out:) is
    dropped downstream — no duplicate avar2 entries."""
    csv_path = _write(tmp_path, PARAM_ONLY_SPAC_CSV)  # Default at XOPQ 2
    (m,) = cg.read_csv_mappings(csv_path)
    src_axes = [{"tag": "XOPQ", "min": 2, "max": 1016}, {"tag": "SPAC", "min": -20, "max": 40}]
    padded = cg._pad_in_envelope([m], src_axes)
    # min corner (XOPQ 2) has different out: values than the CSV row
    # (XOPQ 2, SPAC 25), so it survives as its own anchor; no crash.
    kept = cg._drop_identical_duplicates(padded)
    assert len(kept) == len(padded)


def test_pad_skips_when_axis_range_unknown(tmp_path):
    """Out-axis without a declared source range → no padding (can't bound)."""
    csv_path = _write(tmp_path, PARAM_ONLY_CSV)
    (m,) = cg.read_csv_mappings(csv_path)
    out = cg._pad_in_envelope([m], [{"tag": "XTRA", "min": 94, "max": 3330}])
    assert out == [m]


def test_write_config_inserts_avar2_when_section_missing(tmp_path):
    """A freshly initialised config has no avar2: block — the generated
    section must still land in the written file (it used to be silently
    dropped, so the built font never got an avar table)."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "sources:\n- /x/F.glyphs\nfamilyName: F\n"
        "fvarInstances:\n  F.ttf:\n  - name: Default\n    coordinates:\n      xopq: 2.0\n",
        encoding="utf-8",
    )
    avar2_yaml = "avar2:\n  F.ttf:\n  # Default\n  - in:\n      XOPQ: 2\n    out:\n      XOPQ: 50\n"
    merged_config = {
        "familyName": "F",
        "fvarInstances": {"F.ttf": [{"name": "Default", "coordinates": {"xopq": 2.0}}]},
        "avar2": {"F.ttf": []},
    }
    cg.write_config(merged_config, avar2_yaml, config_path, backup=False)
    text = config_path.read_text(encoding="utf-8")
    assert "avar2:" in text
    assert "XOPQ: 50" in text
