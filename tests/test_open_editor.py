"""The open-editor routing matrix — the studio's file-safety invariant.

``POST /api/control-axes/<tag>/open-editor`` decides WHICH file Fontra
edits:

  - a studio-declared (sidecar) axis must route to the generated shadow
    under ``.avar2-studio/shadow/`` — the axis doesn't exist in the
    original, so opening the original would let Fontra write into the
    user's real file at an undeclared location;
  - a source-derived axis (scoped axes from alternate masters / brace
    layers) must route to the original itself — its source of truth IS
    the original, and opening the shadow would strand edits in a copy
    the next regeneration rebuilds.

Fontra launches are recorded, never spawned, and no build ever runs —
these tests pin the routing decision, not the editor.
"""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from avar2_studio import control_axes


@pytest.fixture
def studio_client(monkeypatch):
    """Flask test client with _ensure_fontra_running replaced by a
    recording fake (returns a made-up port, spawns nothing)."""
    import avar2_studio.server as server

    calls = []

    def fake_ensure_fontra(content_root, watch_file=None):
        calls.append((Path(content_root), Path(watch_file) if watch_file else None))
        return 8123

    monkeypatch.setattr(server, "_ensure_fontra_running", fake_ensure_fontra)

    def set_paths(original, active):
        monkeypatch.setattr(server, "ORIGINAL_PATH", original)
        monkeypatch.setattr(server, "GLYPHS_PATH", active)

    return SimpleNamespace(
        client=server.app.test_client(), fontra_calls=calls, set_paths=set_paths
    )


def test_studio_axis_without_shadow_refuses_with_400(studio_client, crispy_source):
    """A sidecar axis with no layers yet has no shadow to edit — the
    route refuses BEFORE any editor launch."""
    control_axes.add_axis(crispy_source, "crbr", "Crossbar", 0, -100, 100)
    studio_client.set_paths(crispy_source, crispy_source)

    r = studio_client.client.post("/api/control-axes/crbr/open-editor")

    assert r.status_code == 400
    assert "Add coverage glyphs first" in r.get_json()["error"]
    assert studio_client.fontra_calls == []


def test_studio_axis_with_shadow_opens_shadow_not_original(studio_client, crispy_source):
    """With a seeded brace layer the shadow exists, and Fontra is
    pointed at the shadow directory — the original stays untouched."""
    control_axes.add_axis(crispy_source, "crbr", "Crossbar", 0, -100, 100)
    control_axes.apply_layer_delta(
        crispy_source, "crbr", add=[{"glyph": "e", "location": {"crbr": 100}}]
    )
    shadow = control_axes.regenerate_shadow(crispy_source)
    studio_client.set_paths(crispy_source, shadow)

    r = studio_client.client.post("/api/control-axes/crbr/open-editor")

    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert not body["editing_original"]
    assert body["project"] == "CrispyMini.glyphs"  # shadow keeps the basename
    assert body["url"] == "/fontra/editor.html?project=CrispyMini.glyphs"
    assert body["direct_url"] == "http://127.0.0.1:8123/editor.html?project=CrispyMini.glyphs"
    # The editor's project root is the shadow dir, its file the shadow.
    assert studio_client.fontra_calls == [(shadow.parent, shadow)]
    assert shadow.parent == control_axes.shadow_dir_for(crispy_source)
    assert ".avar2-studio" in shadow.parts


def test_source_derived_axis_on_designspace_opens_original(studio_client, roboto_source):
    """Roboto Delta's XOUC lives in the .designspace's own alternate
    masters — Fontra edits the original file in single-file mode, and
    no shadow scaffolding is created."""
    studio_client.set_paths(roboto_source, roboto_source)

    r = studio_client.client.post("/api/control-axes/XOUC/open-editor")

    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["editing_original"] is True
    assert body["project"] == "RobotoDeltaMini.designspace"
    assert body["tag"] == "xouc"
    # Single-file mode: the file itself is the content root, so sibling
    # UFOs in the same folder aren't exposed as projects.
    assert studio_client.fontra_calls == [(roboto_source, roboto_source)]
    # The route never scaffolds a shadow for a source-derived axis.
    # (The fixture's own .avar2-studio/ ships a build dir — assert on
    # the shadow specifically, not the workdir.)
    assert not control_axes.shadow_exists(roboto_source)
