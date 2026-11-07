"""T1.6: get_font_family_name returns the path stem without touching the file."""
from pathlib import Path

import avar2_studio.server as server


def test_family_name_is_the_stem_without_parsing(monkeypatch):
    def boom(_p):
        raise AssertionError("must not parse the source")
    monkeypatch.setattr(server._source_font, "load_source", boom)
    monkeypatch.setattr(server._source_font, "load_source_cached", boom)
    assert server.get_font_family_name(Path("/some/dir/My Font.glyphs")) == "My Font"
    assert server.get_font_family_name(Path("/some/dir/My Font.designspace")) == "My Font"
    assert server.get_font_family_name(None) is None
