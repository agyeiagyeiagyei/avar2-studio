"""Axis-dependent transforms fail loudly on sources without their axes.

round_corners' radius formula reads XOPQ/XTRA; on a source without them
it used to no-op silently — enabled, sharp output, no ROND axis, no
message. Now: the engine raises, and the transforms PUT refuses to
enable it (requires_axis_tags) with a message naming the missing tags.
Slant has no such requirement (the mean-advance rule needs only widths)
and must keep working on a wght-only source."""

from pathlib import Path

import glyphsLib
import pytest

from avar2_studio import server, state
from avar2_studio.transforms import corner_rounding, slanting

WASMTEST = Path(__file__).resolve().parents[1] / "frontend/e2e/fixtures/WasmTest.glyphs"


def test_round_font_raises_without_parametric_axes():
    font = glyphsLib.GSFont(str(WASMTEST))  # wght-only
    with pytest.raises(ValueError) as exc:
        corner_rounding.round_font(font, {}, axis_max=100.0)
    msg = str(exc.value)
    assert "XOPQ" in msg and "wght" in msg


def test_slant_has_no_parametric_requirement():
    font = glyphsLib.GSFont(str(WASMTEST))
    stats = slanting.slant_font(font, {})
    assert stats["axis"] and stats["twins"] > 0


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def _put(client, monkeypatch, src_tags, enabled=True):
    monkeypatch.setattr(state, "ORIGINAL_PATH", Path("/tmp/F.glyphs"))
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(server, "_get_glyphs_axis_tags", lambda: src_tags)
    monkeypatch.setattr(server._transforms, "discover", lambda: None)
    monkeypatch.setattr(server._transforms, "available", lambda _p: [])
    monkeypatch.setattr(server._transforms, "set_active",
                        lambda _p, e: [{"id": "round_corners", "enabled": enabled, "params": {}}])
    monkeypatch.setattr(server, "_resolve_active_source", lambda: None)
    monkeypatch.setattr(server, "trigger_build", lambda: True)
    return client.put("/api/transforms", json={"transforms": [
        {"type": "round_corners", "enabled": enabled, "params": {}},
    ]})


def test_put_refuses_round_corners_without_axes(client, monkeypatch):
    r = _put(client, monkeypatch, {"wght"})
    assert r.status_code == 400
    assert "XOPQ" in r.get_json()["error"]


def test_put_accepts_round_corners_with_axes(client, monkeypatch):
    r = _put(client, monkeypatch, {"xopq", "xtra", "yopq"})
    assert r.status_code == 200


def test_put_accepts_round_corners_with_xopq_alone(client, monkeypatch):
    # XTRA is optional: its share of the radius blend is zero without
    # the axis, so an XOPQ-only parametric source is a valid target.
    r = _put(client, monkeypatch, {"xopq", "wght"})
    assert r.status_code == 200


def test_put_allows_disabling_regardless_of_axes(client, monkeypatch):
    r = _put(client, monkeypatch, {"wght"}, enabled=False)
    assert r.status_code == 200
