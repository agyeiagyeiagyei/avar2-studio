"""Kinetikern: Pass 1 spacing proposals from the vendored Kinetikern2 engine."""
import json
import subprocess
from pathlib import Path

import pytest

from avar2_studio import kinetikern, server, state

REPO = Path(__file__).resolve().parents[1]
FONTC = REPO / ".venv/bin/fontc"
MINI = REPO / "examples/crispy-mini/sources/CrispyMini.glyphs"

needs_engine = pytest.mark.skipif(
    not FONTC.exists() or kinetikern.engine_path() is None,
    reason="fontc or the Kinetikern2 engine (native/kinetikern2) is not built")


@pytest.fixture(scope="module")
def mini_vf(tmp_path_factory):
    out = tmp_path_factory.mktemp("kinetikern") / "CrispyMini.ttf"
    subprocess.run([str(FONTC), "-o", str(out), str(MINI), "--flatten-components",
                    "--decompose-transformed-components"], check=True, capture_output=True)
    return out


def _mean_sides(out):
    spaced = [g for g in out["glyphs"] if g["spaced"]]
    return sum(g["new"]["lsb"] + g["new"]["rsb"] for g in spaced) / len(spaced)


@needs_engine
def test_propose_keeps_ink_and_spaces_the_letters(mini_vf):
    out = kinetikern.propose(mini_vf, {}, looseness=0.0)
    assert out["upm"] > 0 and out["stats"]["spaced"] > 0
    letters = [g for g in out["glyphs"] if 0x41 <= g["unicode"] <= 0x7A]
    assert letters and all(g["spaced"] for g in letters)
    for g in out["glyphs"]:
        if g["spaced"]:
            cur, new = g["cur"], g["new"]
            assert new["advance"] - new["lsb"] - new["rsb"] == cur["advance"] - cur["lsb"] - cur["rsb"]
    space = [g for g in out["glyphs"] if g["unicode"] == 0x20]
    assert not space or (not space[0]["spaced"] and space[0]["new"] == space[0]["cur"])


@needs_engine
def test_looser_slider_opens_the_sides(mini_vf):
    tight = kinetikern.propose(mini_vf, {}, looseness=-1.0)
    middle = kinetikern.propose(mini_vf, {}, looseness=0.0)
    loose = kinetikern.propose(mini_vf, {}, looseness=1.0)
    assert _mean_sides(tight) < _mean_sides(middle) < _mean_sides(loose)


@needs_engine
def test_route_by_coordinates_and_unknown_instance(mini_vf, monkeypatch):
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", mini_vf)
    client = server.app.test_client()
    axes = kinetikern.propose(mini_vf, {}, 0.0)["location"]
    tag = next(iter(axes))
    r = client.get("/api/kinetikern/propose",
                   query_string={"coordinates": json.dumps({tag: axes[tag]}), "looseness": "0.5"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["looseness"] == 0.5 and body["glyphs"] and body["instance"] is None
    r = client.get("/api/kinetikern/propose", query_string={"instance": "no such instance"})
    assert r.status_code == 404
    r = client.get("/api/kinetikern/propose", query_string={"coordinates": json.dumps({"nope": 1})})
    assert r.status_code == 400
