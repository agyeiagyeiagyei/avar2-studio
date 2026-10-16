"""Shared pytest fixtures: throwaway copies of the example sources.

The studio writes its sidecars (``-control.json``, ``-transforms.json``)
and ``.avar2-studio/shadow/`` workdirs NEXT to the source file, so every
test that exercises a writing code path gets its own copy under
``tmp_path`` — ``examples/`` itself is never touched.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CRISPY_SOURCES = EXAMPLES / "crispy-mini" / "sources"
ROBOTO_SOURCES = EXAMPLES / "roboto-delta-mini" / "sources"


@pytest.fixture(scope="session", autouse=True)
def _isolated_user_transforms_dir(tmp_path_factory):
    """Point transform discovery at a throwaway directory.

    ``registry.discover()`` otherwise creates + reads
    ``~/.avar2-studio/transforms/`` — outside the repo, and whatever
    user scripts happen to live there would leak into REGISTRY and make
    the built-in assertions machine-dependent.
    """
    from avar2_studio.transforms import registry

    original = registry.user_transforms_dir
    isolated = tmp_path_factory.mktemp("user-transforms")
    registry.user_transforms_dir = lambda: isolated
    registry.discover(force=True)
    yield
    registry.user_transforms_dir = original


@pytest.fixture
def crispy_source(tmp_path):
    """A writable copy of the CrispyMini .glyphs source."""
    dst = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(CRISPY_SOURCES / "CrispyMini.glyphs", dst)
    return dst


@pytest.fixture
def crispy_csv(tmp_path):
    """A writable copy of the CrispyMini avar2 mappings CSV."""
    dst = tmp_path / "CrispyMini-avar.csv"
    shutil.copy2(CRISPY_SOURCES / "CrispyMini-avar.csv", dst)
    return dst


@pytest.fixture
def roboto_source(tmp_path):
    """A writable copy of the RobotoDeltaMini .designspace + sibling UFOs.

    The designspace references its UFOs by relative filename, so the
    whole sources dir comes along.
    """
    dst = tmp_path / "sources"
    shutil.copytree(ROBOTO_SOURCES, dst)
    return dst / "RobotoDeltaMini.designspace"


@pytest.fixture(autouse=True)
def _no_real_build_jobs(monkeypatch, request):
    """Keep the build queue inert: tests exercise endpoint logic, not the
    worker machinery (covered by tests/test_build_jobs.py against the real
    JobQueue). A test opts in with ``@pytest.mark.real_jobs``."""
    if "real_jobs" in request.keywords:
        yield
        return
    from avar2_studio import server, state

    class _FakeJobs:
        def __init__(self):
            self.requests = []
        def request_build(self, source_refresh=None):
            self.requests.append(source_refresh)
            return True
        def wait_for_idle(self, timeout=None):
            return True
        def is_building(self):
            return False
        def status(self):
            return {"running": None, "queued": [], "last": None,
                    "sizes": {"ttf_bytes": None, "woff2_bytes": None,
                              "woff2_exact": False}}
        def cancel(self, job_id):
            return False
        def cancel_all(self):
            pass
        def submit_export(self, **kw):
            return "export-fake"
        def export_artifact(self, job_id):
            return None
        def job_status(self, job_id):
            return None

    fake = _FakeJobs()
    monkeypatch.setattr(server, "JOBS", fake)
    # pipeline.py reaches the queue through state.JOBS — patch both names.
    monkeypatch.setattr(state, "JOBS", fake)
    yield

@pytest.fixture
def studio_client(monkeypatch):
    """Flask test client with _ensure_fontra_running replaced by a
    recording fake (returns a made-up port, spawns nothing)."""
    import avar2_studio.server as server
    from avar2_studio import state

    calls = []

    def fake_ensure_fontra(content_root, watch_file=None):
        calls.append((Path(content_root), Path(watch_file) if watch_file else None))
        return 8123

    monkeypatch.setattr(server, "_ensure_fontra_running", fake_ensure_fontra)

    def set_paths(original, active):
        monkeypatch.setattr(state, "ORIGINAL_PATH", original)
        monkeypatch.setattr(state, "GLYPHS_PATH", active)

    return SimpleNamespace(
        client=server.app.test_client(), fontra_calls=calls, set_paths=set_paths
    )
