"""The transforms PUT must regenerate the shadow when a SOURCE-stage
transform is toggled or its source params change, and may take the fast
build (no shadow re-round/re-slant) when only font-stage keys changed.

Regression: set_active returns the UI list, whose entries carry "id" —
the touched-detector read "type" (the sidecar spelling), matched
nothing, and every toggle silently took the fast path: an enabled
slant/round_corners never reached the compiled font until the next
server restart."""

import pytest

from avar2_studio import server, state


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def _ui_entry(tid, enabled, params=None):
    # The shape available()/set_active() return: "id", never "type".
    return {"id": tid, "enabled": enabled, "params": params or {}}


@pytest.fixture
def harness(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(state, "ORIGINAL_PATH", tmp_path / "F.glyphs")
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(server, "_resolve_active_source",
                        lambda: calls.append("resolve"))
    monkeypatch.setattr(server, "trigger_build",
                        lambda: calls.append("build") or True)
    monkeypatch.setattr(server._transforms, "discover", lambda: None)
    return calls


def _run_put(client, monkeypatch, previous, stored):
    monkeypatch.setattr(server._transforms, "available",
                        lambda _p: previous)
    monkeypatch.setattr(server._transforms, "set_active",
                        lambda _p, _e: stored)
    r = client.put("/api/transforms", json={"transforms": []})
    assert r.status_code == 200, r.get_json()


def test_enabling_a_source_transform_regenerates_the_shadow(
        client, monkeypatch, harness):
    _run_put(client, monkeypatch,
             previous=[_ui_entry("slant", False)],
             stored=[_ui_entry("slant", True)])
    assert "resolve" in harness, (
        "toggling a source-stage transform must take the regen path")


def test_font_stage_only_edits_take_the_fast_path(
        client, monkeypatch, harness):
    # Only style_pcts (a declared font_stage_param_key) changed.
    _run_put(client, monkeypatch,
             previous=[_ui_entry("slant", True, {"style_pcts": {}})],
             stored=[_ui_entry("slant", True, {"style_pcts": {"Default 400": 11}})],
             )
    assert "resolve" not in harness, (
        "a percent-only edit must not re-slant the shadow")
    assert "build" in harness


def test_source_param_edits_regenerate_the_shadow(
        client, monkeypatch, harness):
    _run_put(client, monkeypatch,
             previous=[_ui_entry("slant", True, {"narrow_angle": 11.0})],
             stored=[_ui_entry("slant", True, {"narrow_angle": 14.0})])
    assert "resolve" in harness, (
        "a source-param edit (angle dial) must take the regen path")
