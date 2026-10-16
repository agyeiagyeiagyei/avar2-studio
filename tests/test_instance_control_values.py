"""Per-instance CONTROL-axis values persist in the control sidecar.

ymod/lcwd cannot be CSV columns (headers uppercase, so a ymod column
mints a bogus YMOD user axis). Instead the update endpoint writes them
to the control sidecar's ``instance_values``, /api/instances hydrates
them back, and the build stamps them onto matching fvar instances —
the control-axis analogue of SPAC's CSV column.
"""

import csv
import importlib
import json
import sys
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _write_sidecar(source: Path):
    sidecar = source.parent / f"{source.stem}-control.json"
    sidecar.write_text(json.dumps({
        "version": 1,
        "axes": [{"tag": "ymod", "display_name": "Horizontal correction",
                  "default": 0.0, "min": 0.0, "max": 100.0, "layers": []}],
    }))
    return sidecar


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import avar2_studio.server as server
    from avar2_studio import state
    importlib.reload(server)

    glyphs = FIXTURES / "WasmTest.glyphs"
    if not glyphs.exists():
        glyphs = Path(__file__).resolve().parents[1] / "frontend/e2e/fixtures/WasmTest.glyphs"
    source = tmp_path / "WasmTest.glyphs"
    source.write_text(glyphs.read_text())
    sidecar = _write_sidecar(source)

    csv_path = tmp_path / "WasmTest-avar.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Instance Name", "WGHT"])
        w.writerow(["Alpha", "100.0"])
        w.writerow(["Beta", "400.0"])

    monkeypatch.setattr(state, "GLYPHS_PATH", source)
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    monkeypatch.setattr(server, "_get_preview_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_get_avar2_csv_path", lambda: csv_path)
    monkeypatch.setattr(server, "_check_csv_external_edit", lambda p: False)
    server.app.config["TESTING"] = True
    return server.app.test_client(), csv_path, sidecar


def test_control_value_saved_hydrated_and_cleared(client):
    c, csv_path, sidecar = client
    resp = c.put("/api/instance/Alpha?csv_only=true",
                 json={"coordinates": {"ymod": 100.0}})
    assert resp.status_code == 200, resp.get_json()

    data = json.loads(sidecar.read_text())
    assert data["instance_values"] == {"ymod": {"Alpha": 100.0}}

    # Never a CSV column.
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        fieldnames = list(csv.DictReader(f).fieldnames)
    assert "YMOD" not in fieldnames and "ymod" not in fieldnames

    # Hydration: the listing carries it back; untouched rows don't.
    insts = {i["name"]: i for i in c.get("/api/instances").get_json()["instances"]}
    assert insts["Alpha"]["coordinates"]["ymod"] == 100.0
    assert "ymod" not in insts["Beta"]["coordinates"]

    # Clamp + clear-at-default.
    assert c.put("/api/instance/Alpha?csv_only=true",
                 json={"coordinates": {"ymod": 250.0}}).status_code == 200
    assert json.loads(sidecar.read_text())["instance_values"]["ymod"]["Alpha"] == 100.0
    assert c.put("/api/instance/Alpha?csv_only=true",
                 json={"coordinates": {"ymod": 0.0}}).status_code == 200
    assert json.loads(sidecar.read_text())["instance_values"] == {}


def test_values_survive_layer_edits_and_follow_renames(tmp_path):
    from avar2_studio import control_axes as ca
    source = tmp_path / "T.glyphs"
    source.write_text("{}")
    _write_sidecar(source)

    assert ca.set_instance_values(source, "Alpha", {"ymod": 60.0})
    # A layer edit round-trips the whole sidecar through _normalise —
    # the values must come through.
    ca.set_layers(source, "ymod", [{"glyph": "a", "location": {"ymod": 100.0}}])
    assert ca.instance_values(source) == {"ymod": {"Alpha": 60.0}}

    assert ca.rename_instance_values(source, "Alpha", "Beta")
    assert ca.instance_values(source) == {"ymod": {"Beta": 60.0}}
    assert ca.remove_instance_values(source, "Beta")
    assert ca.instance_values(source) == {}


def test_build_stamps_fvar_instances(tmp_path, monkeypatch):
    from avar2_studio import pipeline, state

    source = tmp_path / "T.glyphs"
    source.write_text("{}")
    _write_sidecar(source)
    from avar2_studio import control_axes as ca
    ca.set_instance_values(source, "Alpha", {"ymod": 100.0})

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef"])
    fb.setupCharacterMap({})
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph()})
    fb.setupHorizontalMetrics({".notdef": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "T", "styleName": "R"})
    fb.setupFvar(
        axes=[("ymod", 0.0, 0.0, 100.0, "Horizontal correction")],
        instances=[dict(location={"ymod": 0.0}, stylename="Alpha"),
                   dict(location={"ymod": 0.0}, stylename="Beta")],
    )
    fb.setupOS2()
    fb.setupPost()
    vf = tmp_path / "t.ttf"
    fb.save(str(vf))

    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    assert pipeline._stamp_control_instance_values(vf) == vf

    from fontTools.ttLib import TTFont
    font = TTFont(str(vf))
    name = font["name"]
    coords = {name.getDebugName(i.subfamilyNameID): dict(i.coordinates)
              for i in font["fvar"].instances}
    assert coords["Alpha"]["ymod"] == 100.0
    assert coords["Beta"]["ymod"] == 0.0
