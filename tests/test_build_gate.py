"""The build gate: a source that does not compile pauses every mutating
endpoint, keeps reads open, and leaves the recovery routes reachable."""

import pytest

from avar2_studio import server, state


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def broken_build(monkeypatch):
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "failed")
    monkeypatch.setattr(state, "LAST_BUILD_ERROR", "fontc: 'z' has interpolation-incompatible paths")


def test_mutations_are_refused_while_broken(client, broken_build):
    for method, path in [
        ("put", "/api/instances/Def/grade"),
        ("post", "/api/control-axes/lcwd/reseed"),
        ("put", "/api/transforms"),
        ("post", "/api/export-font"),
        ("delete", "/api/instance/Def"),
        ("post", "/api/config/import"),
    ]:
        r = getattr(client, method)(path, json={})
        assert r.status_code == 409, (method, path, r.status_code)
        body = r.get_json()
        assert body["build_blocked"] is True
        assert "interpolation-incompatible" in body["detail"]


def test_reads_stay_open_while_broken(client, broken_build):
    r = client.get("/api/health")
    assert r.status_code != 409
    assert r.get_json()["last_build_status"] == "failed"


def test_recovery_routes_stay_open_while_broken(client, broken_build):
    # These may fail for OTHER reasons with no source loaded in the test
    # process; the assertion is only that the GATE does not refuse them.
    for method, path in [
        ("post", "/api/build"),
        ("post", "/api/load-source"),
        ("post", "/api/instance/Def/editing"),
        ("delete", "/api/instance/Def/editing"),
    ]:
        r = getattr(client, method)(path, json={})
        assert r.status_code != 409, (method, path)


def test_gate_is_inert_when_build_is_ok(client, monkeypatch):
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    r = client.put("/api/transforms", json={})
    assert r.status_code != 409


def test_unreadable_source_fails_the_build(tmp_path, monkeypatch):
    """A source that no longer parses must stamp the build failed before
    either builder runs: the last-good font keeps serving and the gate
    closes instead of publishing a degraded fallback as "ok"."""
    from avar2_studio import pipeline

    bad = tmp_path / "Bad.glyphs"
    bad.write_text("{\n  a = 1;\n}\nEXTRA\n", encoding="utf-8")  # "Junk after plist"
    monkeypatch.setattr(state, "GLYPHS_PATH", bad)
    monkeypatch.setattr(state, "ORIGINAL_PATH", bad)
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(state, "LAST_BUILD_ERROR", None)

    assert pipeline._run_build() is False
    assert state.LAST_BUILD_STATUS == "failed"
    assert "cannot be read" in (state.LAST_BUILD_ERROR or "").lower()


def test_a_readable_source_that_misses_the_mapping_still_builds(tmp_path, monkeypatch):
    """The degraded build is BY DESIGN (ARCHITECTURE §5): when the mapping
    step fails but the source compiles, the plain font is published and the
    reason lands in ``LAST_AVAR2_ERROR`` for the banner. The unreadable-source
    pre-flight must not turn that into a failed build/gate — this pins it, so
    a future tightening of the pre-flight cannot silently swallow the
    fallback."""
    from avar2_studio import pipeline

    good = tmp_path / "Good.glyphs"
    good.write_text("{\n}\n", encoding="utf-8")
    plain = tmp_path / "plain.ttf"
    plain.write_bytes(b"\0\0\0\0")
    monkeypatch.setattr(state, "GLYPHS_PATH", good)
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(state, "LAST_BUILD_ERROR", None)
    monkeypatch.setattr(state, "LAST_AVAR2_ERROR", None)
    monkeypatch.setattr(pipeline, "_source_font",
                        type("Stub", (), {"load_source_cached": staticmethod(lambda p: ("font", "glyphs"))}))
    monkeypatch.setattr(pipeline, "_perform_avar2_build",
                        lambda: {"success": False, "error": "gvar header overflow"})
    monkeypatch.setattr(pipeline, "build_variable_font",
                        lambda *a, **k: plain)
    monkeypatch.setattr(pipeline, "_apply_transform_chain", lambda p: p)

    assert pipeline._run_build() is True
    assert state.LAST_BUILD_STATUS == "ok"
    assert state.LAST_BUILD_ERROR is None
    assert "gvar header overflow" in (state.LAST_AVAR2_ERROR or "")


def test_a_cancelled_build_keeps_the_gate_open(client, monkeypatch):
    """Cancel is not a broken source: applying a cancelled result must not
    stamp LAST_BUILD_STATUS "failed" (the gate would then 409 every edit
    until a build succeeds — a stuck studio)."""
    from avar2_studio import build_jobs
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(state, "LAST_BUILD_ERROR", None)
    queue = build_jobs.JobQueue(server)
    queue._apply_build_state({"success": False, "cancelled": True,
                              "error": "cancelled"})
    assert state.LAST_BUILD_STATUS == "ok"
    assert state.LAST_BUILD_ERROR is None
    r = client.put("/api/transforms", json={})
    assert r.status_code != 409


def test_a_cancelled_retry_keeps_the_gate_closed(client, monkeypatch):
    """If the source was already broken, cancelling the retry build must
    leave the gate closed — the last-good font is still stale."""
    from avar2_studio import build_jobs
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "failed")
    monkeypatch.setattr(state, "LAST_BUILD_ERROR", "fontc: incompatible")
    queue = build_jobs.JobQueue(server)
    queue._apply_build_state({"success": False, "cancelled": True,
                              "error": "cancelled"})
    assert state.LAST_BUILD_STATUS == "failed"
    r = client.put("/api/transforms", json={})
    assert r.status_code == 409
