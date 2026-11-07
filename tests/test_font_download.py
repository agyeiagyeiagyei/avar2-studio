"""/api/font honors ?download=1 with an attachment disposition — the
Preview tab's Download button is a plain anchor at this URL, and the
old inline-with-filename response made it silently do nothing."""

import importlib
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


def test_download_flag_sets_attachment(tmp_path, monkeypatch):
    import avar2_studio.server as server
    from avar2_studio import state
    importlib.reload(server)
    font = tmp_path / "T-VF.ttf"
    font.write_bytes(b"\x00\x01\x00\x00fake")
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", font)
    server.app.config["TESTING"] = True
    c = server.app.test_client()

    plain = c.get("/api/font")
    assert plain.status_code == 200
    assert "attachment" not in (plain.headers.get("Content-Disposition") or "")

    dl = c.get("/api/font?download=1")
    assert dl.status_code == 200
    disp = dl.headers.get("Content-Disposition") or ""
    assert disp.startswith("attachment") and "T-VF.ttf" in disp
