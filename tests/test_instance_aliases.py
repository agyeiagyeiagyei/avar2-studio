"""Studio-side instance renames: durable across shadow regeneration."""
from pathlib import Path

from glyphsLib.classes import GSFont, GSInstance

from avar2_studio import instance_aliases


def test_record_resolves_chains_and_identity(tmp_path):
    src = tmp_path / "F.glyphs"
    src.write_text("{}")
    assert instance_aliases.record(src, "Def", "Default") == {"Def": "Default"}
    # renaming the alias again collapses the chain to the ORIGINAL name
    assert instance_aliases.record(src, "Default", "Final") == {"Def": "Final"}
    # renaming back to the original name drops the entry
    assert instance_aliases.record(src, "Final", "Def") == {}


def test_apply_renames_matching_instances(tmp_path):
    src = tmp_path / "F.glyphs"
    src.write_text("{}")
    instance_aliases.record(src, "Def", "Default")
    font = GSFont()
    a, b = GSInstance(), GSInstance()
    a.name = "Def"
    b.name = "Other"
    font.instances.append(a)
    font.instances.append(b)
    n = instance_aliases.apply_to_font(font, instance_aliases.load(src))
    assert n == 1
    assert [str(i.name) for i in font.instances] == ["Default", "Other"]


def test_load_tolerates_missing_and_junk(tmp_path):
    src = tmp_path / "F.glyphs"
    src.write_text("{}")
    assert instance_aliases.load(src) == {}
    (tmp_path / ".avar2-studio").mkdir()
    (tmp_path / ".avar2-studio" / "instance-renames.json").write_text("not json")
    assert instance_aliases.load(src) == {}
