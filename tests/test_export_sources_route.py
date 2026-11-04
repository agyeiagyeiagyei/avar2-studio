"""E1 — ``POST /api/export-font`` with ``format: "sources"``.

The route's job is to accept the third format exactly as the studio sends it,
refuse the two combinations that cannot mean anything (an unknown format, and
sources + a re-origin), and hand the worker the facts its README needs. The
zip itself is covered in tests/test_sources_export.py.
"""

import json

import pytest

from avar2_studio import server, state


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def built_font(tmp_path, monkeypatch):
    font = tmp_path / "CrispyMini-avar2.ttf"
    font.write_bytes(b"\0\1\0\0")
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", font)
    source = tmp_path / "CrispyMini.glyphs"
    source.write_bytes(b"<glyphs/>")
    monkeypatch.setattr(state, "GLYPHS_PATH", source)
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    return font


@pytest.fixture
def submitted(monkeypatch):
    """Capture submit_export calls instead of queueing a worker."""
    calls = []

    def fake_submit(**kwargs):
        calls.append(kwargs)
        return "export-1-1"

    monkeypatch.setattr(server.JOBS, "submit_export", fake_submit)
    monkeypatch.setattr(server, "get_font_family_name", lambda p: "CrispyMini")
    return calls


def _post(client, **body):
    return client.post("/api/export-font", data=json.dumps(body),
                       content_type="application/json")


def test_sources_is_queued_and_carries_the_readme_facts(client, built_font, submitted, monkeypatch):
    r = _post(client, format="sources", hidden_axes=["opsz"])

    assert r.status_code == 202, r.get_json()
    assert len(submitted) == 1
    spec = submitted[0]
    assert spec["format"] == "sources"
    assert spec["family_name"] == "CrispyMini"
    assert spec["hidden_axes"] == ["opsz"]
    # E1c: the composed list is the WRITER's (read from the sidecar it already
    # reads for SPAC), so the route no longer computes or ships one.
    assert "composed_transforms" not in spec


def test_sources_cannot_be_re_origined(client, built_font, submitted):
    r = _post(client, format="sources", default_location={"wght": 400})

    assert r.status_code == 400
    assert "compiled origin" in r.get_json()["error"]
    assert submitted == [], "a refused request must not queue a job"


def test_an_unknown_format_names_all_three(client, built_font, submitted):
    r = _post(client, format="otf")

    assert r.status_code == 400
    assert r.get_json()["error"] == "format must be ttf, woff2 or sources"
    assert submitted == []
