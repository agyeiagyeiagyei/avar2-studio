"""T2.10: the control sidecar survives things it doesn't understand, refuses
to downgrade a newer file, and never overwrites one it could not read."""
import json

import pytest

import avar2_studio.server as server
from avar2_studio import control_axes, state


def _write(source, payload):
    sidecar = control_axes.sidecar_path_for(source)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return sidecar


def test_unknown_keys_survive_a_load_save_round_trip(crispy_source):
    _write(crispy_source, {
        "version": 1, "future_top": {"x": 1},
        "axes": [{"tag": "ymod", "display_name": "Y", "default": 0, "min": 0, "max": 100,
                  "future_axis": "keep me",
                  "layers": [{"glyph": "e", "location": {"ymod": 100}, "future_layer": [1, 2]}]}],
    })
    control_axes.update_axis(crispy_source, "ymod", display_name="Y mod")
    saved = json.loads(control_axes.sidecar_path_for(crispy_source).read_text())
    assert saved["future_top"] == {"x": 1}
    assert saved["axes"][0]["future_axis"] == "keep me"
    assert saved["axes"][0]["display_name"] == "Y mod"
    assert saved["axes"][0]["layers"][0]["future_layer"] == [1, 2]


def test_a_newer_schema_is_read_only(crispy_source):
    sidecar = _write(crispy_source, {"version": 99, "axes": [
        {"tag": "ymod", "display_name": "Y", "default": 0, "min": 0, "max": 100, "layers": []}]})
    before = sidecar.read_text()
    assert [a["tag"] for a in control_axes.list_axes(crispy_source)] == ["ymod"]
    with pytest.raises(control_axes.SidecarVersionError):
        control_axes.add_axis(crispy_source, "lcwd", "Lowercase width", 0, 0, 100)
    assert sidecar.read_text() == before


def test_a_corrupt_sidecar_is_reported_and_never_overwritten(crispy_source):
    sidecar = _write(crispy_source, '{"version": 1, "axes": [')
    assert control_axes.load(crispy_source)["axes"] == []
    assert str(sidecar) in control_axes.load_errors()
    with pytest.raises(control_axes.SidecarCorruptError):
        control_axes.add_axis(crispy_source, "ymod", "Y", 0, 0, 100)
    assert sidecar.read_text() == '{"version": 1, "axes": [', "the unreadable file was overwritten"
    # repaired → the error clears and writes work again
    sidecar.write_text('{"version": 1, "axes": []}')
    control_axes.add_axis(crispy_source, "ymod", "Y", 0, 0, 100)
    assert str(sidecar) not in control_axes.load_errors()
    assert [a["tag"] for a in control_axes.list_axes(crispy_source)] == ["ymod"]


def test_health_surfaces_sidecar_errors(crispy_source, monkeypatch):
    sidecar = _write(crispy_source, "not json")
    control_axes.load(crispy_source)
    monkeypatch.setattr(state, "ORIGINAL_PATH", crispy_source)
    monkeypatch.setattr(state, "GLYPHS_PATH", crispy_source)
    server.app.config["TESTING"] = True
    body = server.app.test_client().get("/api/health").get_json()
    assert str(sidecar) in body["sidecar_errors"]
    sidecar.unlink()
    control_axes.load(crispy_source)
    assert body is not None and str(sidecar) not in server.app.test_client().get("/api/health").get_json()["sidecar_errors"]
