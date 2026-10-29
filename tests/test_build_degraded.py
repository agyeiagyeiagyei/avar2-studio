"""O1 — a build with no declared axis defaults must say so, not report `ok`.

Repro from the designer's report ("instances look deformed on 5001 and 5071"):
with `<stem>-axis-metadata.json` absent (or empty) there is no declared default
for any user axis, so no CSV row can be the default row, there is nothing to
relocate onto, and the font rests at the compiled corner — the first master's
parametric values (Crispy: XTRA 47 / XOPQ 1 / YOPQ 1, wdth 5, wght 1). Health
reported `last_build_status: ok` with `degraded: None`, so the designer saw a
deformed preview with no message at all.

The studio must not refuse (the preview has to keep working), so the fix is
visibility: `state.LAST_BUILD_DEGRADED` — `health.degraded` — carries a reason
naming the file, the banner shows it, and the build log says it at warning
level. `last_build_status` stays `ok`: the build itself worked. The CLI is
unchanged and already exits 2 for a missing metadata home
(`tests/test_cli_failures.py`).

These run the REAL worker (`build_jobs.JobQueue`), like
``tests/test_worker_shadow.py``: the flag has to survive the worker boundary
(``build_worker._apply_result``) or the server never sees it.
"""

from __future__ import annotations

import logging
import shutil
import sys
import time
from pathlib import Path

import pytest

from avar2_studio import build_jobs, pipeline, state

REPO = Path(__file__).resolve().parents[1]
MINI = REPO / "examples" / "crispy-mini" / "sources"
FONTC = Path(sys.executable).with_name("fontc")
needs_fontc = pytest.mark.skipif(
    not FONTC.exists(), reason="fontc not next to the test interpreter")


class _Server:
    """The queue's method surface: staging + promotion. Everything else is the
    real worker subprocess, driven by the ``state`` globals set below."""

    def _get_preview_dir(self):
        d = state.ORIGINAL_PATH.parent / ".avar2-studio"
        d.mkdir(exist_ok=True)
        return d

    def _get_avar2_font_dir(self):
        d = self._get_preview_dir() / "build"
        d.mkdir(parents=True, exist_ok=True)
        return d


@pytest.fixture
def mini(tmp_path, monkeypatch):
    """A mini Crispy project: source + mappings CSV, committed sibling copied
    in only when the test asks for it."""
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(MINI / "CrispyMini.glyphs", src)
    shutil.copy2(MINI / "CrispyMini-avar.csv", tmp_path / "CrispyMini-avar.csv")
    sibling = MINI / "CrispyMini-axis-metadata.json"

    def with_metadata(yes: bool):
        dst = tmp_path / "CrispyMini-axis-metadata.json"
        if yes:
            shutil.copy2(sibling, dst)
        elif dst.exists():
            dst.unlink()

    with_metadata(True)
    # The declared-ranges memo is keyed by on-disk versions; a leaked entry from
    # another test would answer for this tmp_path (O1 runs the real metadata
    # loader rather than monkeypatching it).
    monkeypatch.setattr(state, "_DECLARED_RANGES_CACHE", {})
    return src, with_metadata


def _worker_build(monkeypatch, source):
    """One real worker build with the state globals the server would set."""
    for name, value in (
            ("ORIGINAL_PATH", source), ("GLYPHS_PATH", source),
            ("USE_FONTC", True), ("VARIABLE_FONT_PATH", None),
            ("LAST_BUILD_STATUS", None), ("LAST_BUILD_ERROR", None),
            ("LAST_AVAR2_ERROR", None), ("LAST_ORIGIN_RELOCATION_ERROR", None),
            ("LAST_BUILD_DEGRADED", None), ("LAST_ORIGIN_WARNING", None),
            ("LAST_ORIGIN_RELOCATION_MEMO", None), ("LAST_ORIGIN_AUTHORED_AVAR", None),
            ("LAST_BUILD_TIME", None), ("LAST_GRADE_ERROR", None),
            ("LAST_SOURCE_TRANSFORM_ERROR", None), ("LAST_TRANSFORM_ERROR", None),
            ("_SYNC_CSV_HOOK", None)):
        monkeypatch.setattr(state, name, value)
    # The server writes the preview config when it loads a source; the queue
    # stages it into the job. Without it the build takes the plain-VF fallback
    # and never reaches the relocation step this test is about.
    assert pipeline._initialize_preview_config_from_glyphs(), (
        "the preview config must be created before the queue stages it")
    queue = build_jobs.JobQueue(_Server())
    queue.request_build()
    assert queue.wait_for_idle(timeout=600), f"build failed: {state.LAST_BUILD_ERROR}"
    # Drain the follow-up WOFF2 compress job so no worker outlives tmp_path.
    deadline = time.time() + 120
    while time.time() < deadline and (
            queue.status()["running"] is not None or queue._pending_compress):
        time.sleep(0.25)
    return queue


@needs_fontc
def test_no_declared_defaults_surfaces_a_reason(mini, monkeypatch, caplog):
    source, with_metadata = mini
    with_metadata(False)

    _worker_build(monkeypatch, source)

    assert state.LAST_BUILD_STATUS == "ok", (
        "the build itself works — it must stay open, not turn into a failure")
    reason = state.LAST_BUILD_DEGRADED
    assert reason, "a build with no declared axis defaults must not pass silently"
    assert "CrispyMini-axis-metadata.json" in reason, reason
    assert "compiled corner" in reason, reason
    # The build's own log line is written by the WORKER subprocess, so this
    # process cannot capture it; the caveat is stated instead of pretending.
    # What is asserted here is that the same condition is reachable and logged
    # at warning level in-process (the factory below).


def test_an_empty_metadata_file_is_created_loudly(mini, monkeypatch, caplog):
    """Creating the empty `{}` is part of the silence, so it warns (O1)."""
    source, with_metadata = mini
    with_metadata(False)
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    monkeypatch.setattr(state, "GLYPHS_PATH", source)
    monkeypatch.setattr(state, "PREVIEW_DIR", source.parent / ".avar2-studio")
    state._DECLARED_RANGES_CACHE = {}

    with caplog.at_level(logging.WARNING):
        assert pipeline._load_axis_metadata() is not None

    sibling = source.parent / "CrispyMini-axis-metadata.json"
    assert sibling.exists(), "the loader creates the file it cannot find"
    assert any(r.levelno == logging.WARNING and str(sibling) in str(r.message)
               for r in caplog.records), (
        "an empty axis-metadata file must be reported at warning level")


@needs_fontc
def test_the_committed_sibling_is_not_degraded(mini, monkeypatch):
    source, with_metadata = mini
    with_metadata(True)

    _worker_build(monkeypatch, source)

    assert state.LAST_BUILD_STATUS == "ok"
    assert state.LAST_BUILD_DEGRADED is None, state.LAST_BUILD_DEGRADED
