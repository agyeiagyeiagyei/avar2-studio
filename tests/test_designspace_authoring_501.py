"""T2.15 decision: secondary-axis authoring is .glyphs-only; on a
.designspace source the mutating control-axis routes answer 501 with a
clear message, reads keep working, and nothing is written."""
from test_open_editor import studio_client  # noqa: F401 — fixture

from avar2_studio import control_axes

MUTATIONS = [
    ("post", "/api/control-axes", {"tag": "crbr", "display_name": "Crossbar",
                                  "default": 0, "min": -100, "max": 100}),
    ("put", "/api/control-axes/crbr/layers", {"layers": []}),
    ("post", "/api/control-axes/crbr/layers/delta", {"add": []}),
    ("post", "/api/control-axes/crbr/reseed", {}),
    ("patch", "/api/control-axes/crbr", {"display_name": "X"}),
    ("delete", "/api/control-axes/crbr", None),
]


def test_designspace_sources_refuse_authoring_but_still_read(studio_client, roboto_source):
    studio_client.set_paths(roboto_source, roboto_source)
    c = studio_client.client
    assert c.get("/api/control-axes").status_code == 200
    for method, url, body in MUTATIONS:
        r = getattr(c, method)(url, json=body) if body is not None else getattr(c, method)(url)
        assert r.status_code == 501, (method, url, r.status_code)
        assert "designspace" in r.get_json()["error"]
    assert not control_axes.shadow_exists(roboto_source)
    assert not control_axes.sidecar_path_for(roboto_source).exists()


def test_glyphs_sources_are_not_affected(studio_client, crispy_source):
    studio_client.set_paths(crispy_source, crispy_source)
    r = studio_client.client.post("/api/control-axes", json=MUTATIONS[0][2])
    assert r.status_code != 501, r.get_json()
