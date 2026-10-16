"""Control-axis tags keep the case the designer typed.

fvar tags are case-sensitive: unregistered axes are conventionally
uppercase (``XOPQ``), registered ones lowercase (``wght``). The sidecar
used to lowercase every tag, which made an uppercase custom axis
impossible and let a CSV column (``SUBS``) silently miss its fvar axis
(``subs``). Duplicates are still refused case-insensitively.
"""
import pytest

from avar2_studio import control_axes


@pytest.mark.parametrize("typed, stored", [("SUBS", "SUBS"), ("subs", "subs"), (" Swch ", "Swch")])
def test_validate_tag_preserves_case(typed, stored):
    assert control_axes._validate_tag(typed) == stored


def test_add_and_list_preserve_case(tmp_path):
    src = tmp_path / "Font.glyphs"
    src.write_text("")
    control_axes.add_axis(src, "SUBS", "Substitution", 0, 0, 1)
    assert [ax["tag"] for ax in control_axes.list_axes(src)] == ["SUBS"]


def test_duplicate_tag_is_case_insensitive(tmp_path):
    src = tmp_path / "Font.glyphs"
    src.write_text("")
    control_axes.add_axis(src, "SUBS", "Substitution", 0, 0, 1)
    with pytest.raises(ValueError, match="already exists"):
        control_axes.add_axis(src, "subs", "Substitution again", 0, 0, 1)
