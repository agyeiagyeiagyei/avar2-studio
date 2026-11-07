"""GET /api/build/<job_id> — the per-job status the export poll keys on
(T2.5). The queue's own job_status semantics are covered in
tests/test_build_jobs.py against the real queue; this pins the route's
contract: JSON status for a known id, JSON 404 for an unknown one, and
``/api/build/status`` still answering the whole-queue status (werkzeug
must keep preferring the static rule over ``<job_id>``)."""

import pytest

from avar2_studio import server


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def jobs(monkeypatch):
    class _Jobs:
        def status(self):
            return {"running": None, "queued": [], "last": None,
                    "sizes": {"ttf_bytes": None, "woff2_bytes": None, "woff2_exact": False}}

        def job_status(self, job_id):
            if job_id == "export-1-7":
                return {"job_id": job_id, "kind": "export", "state": "finished",
                        "success": True, "cancelled": False, "error": None,
                        "artifact_ready": True}
            return None

    fake = _Jobs()
    monkeypatch.setattr(server, "JOBS", fake)
    return fake


def test_known_job_returns_its_status(client, jobs):
    r = client.get("/api/build/export-1-7")
    assert r.status_code == 200
    assert r.get_json() == {"job_id": "export-1-7", "kind": "export",
                            "state": "finished", "success": True, "cancelled": False,
                            "error": None, "artifact_ready": True}


def test_unknown_job_is_a_json_404(client, jobs):
    r = client.get("/api/build/export-999-1")
    assert r.status_code == 404
    assert r.get_json() == {"error": "no such job"}


def test_queue_status_route_still_wins_over_the_job_id_rule(client, jobs):
    r = client.get("/api/build/status")
    assert r.status_code == 200
    assert set(r.get_json()) == {"running", "queued", "last", "sizes"}
