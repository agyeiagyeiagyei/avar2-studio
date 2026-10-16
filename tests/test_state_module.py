"""Probe for the state.py decomposition: server read paths observe ``state``
writes, and ``server`` keeps no stale shadow of a moved global."""
from avar2_studio import server, state


def test_server_reads_preview_dir_base_from_state(tmp_path, monkeypatch):
    source = tmp_path / "Probe.glyphs"
    source.write_text("familyName = Probe;")
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    monkeypatch.setattr(state, "PREVIEW_DIR", None)  # defeat the memo
    assert server._get_preview_dir() == tmp_path / ".avar2-studio"


def test_server_module_has_no_stale_state_shadow():
    assert not hasattr(server, "ORIGINAL_PATH")
