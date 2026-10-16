"""T1.3: multipart uploads sanitise the client-supplied filename.

`/api/load-source` saved `f.filename` verbatim under the workspace — a
filename like `../../../tmp/pwn.glyphs` traversed out of it.
"""
import io
from pathlib import Path

import pytest

import avar2_studio.server as server


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # the uploads workspace lives under ~/
    applied = []
    monkeypatch.setattr(server, "_apply_source_path", lambda p: applied.append(p))
    server.app.config["TESTING"] = True
    return server.app.test_client(), tmp_path, applied


def test_a_traversal_filename_is_sanitised(client):
    c, home, applied = client
    stray = Path("/tmp/pwn-avar2-studio-test.glyphs")
    stray.unlink(missing_ok=True)
    resp = c.post("/api/load-source", data={
        "file": (io.BytesIO(b"familyName = Pwn;"), "../../../tmp/pwn-avar2-studio-test.glyphs"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    assert len(applied) == 1
    dest = Path(applied[0])
    # Landed inside the per-font workspace as ONE safe filename segment
    # (secure_filename flattens the directories into the name).
    assert dest.parent.parent == home / ".avar2-studio" / "workspace" / "uploads"
    assert ".." not in dest.name and "/" not in dest.name
    assert dest.name.endswith("pwn-avar2-studio-test.glyphs")
    assert dest.exists()
    assert not stray.exists()


def test_a_normal_filename_still_uploads(client):
    c, home, applied = client
    resp = c.post("/api/load-source", data={
        "file": (io.BytesIO(b"familyName = Mini;"), "Mini.glyphs"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    assert Path(applied[0]).name == "Mini.glyphs"
