"""P3 — a build config's transform override must reach the WORKER build.

C7 reads ``transforms`` from ``<stem>-build.json`` and installs them with
``transforms.config.set_override``, a process global. The build runs in a worker
subprocess, so the override has to travel in the job spec: without it the worker
read the committed ``<stem>-transforms.json`` and a config at the resting style
(the case the verifier exercised) silently changed nothing — the font kept the
sidecar's axes and no named instances / STAT were regenerated
(findings.md, "Verifier review of the Phase C checkpoint", P3).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from avar2_studio import build_jobs, build_worker, state
from avar2_studio.transforms import config as tx_config

REPO = Path(__file__).resolve().parents[1]
MINI = REPO / "examples/crispy-mini/sources"
FONTC = Path(sys.executable).with_name("fontc")
needs_fontc = pytest.mark.skipif(not FONTC.exists(),
                                 reason="fontc not next to the test interpreter")

SIDECAR = {
    "version": 1,
    "transforms": [
        {"type": "spac_widthaware", "enabled": True,
         "params": {"min": -20, "max": 40, "bias": 1.0, "scale": 1.25}},
    ],
}


@pytest.fixture
def mini(tmp_path, monkeypatch):
    """A committed-looking crispy-mini copy with SPAC enabled in the sidecar."""
    src = tmp_path / "src"
    src.mkdir()
    for name in ("CrispyMini.glyphs", "CrispyMini-avar.csv",
                 "CrispyMini-axis-metadata.json", "CrispyMini-transforms.json"):
        shutil.copy2(MINI / name, src / name)
    (src / "CrispyMini-transforms.json").write_text(json.dumps(SIDECAR), encoding="utf-8")
    monkeypatch.setattr(tx_config, "_OVERRIDE", None)
    for name in ("ORIGINAL_PATH", "GLYPHS_PATH", "VARIABLE_FONT_PATH",
                 "LAST_BUILD_STATUS", "LAST_BUILD_ERROR", "LAST_TIME",
                 "LAST_AVAR2_ERROR", "LAST_GRADE_ERROR",
                 "LAST_SOURCE_TRANSFORM_ERROR", "LAST_TRANSFORM_ERROR",
                 "LAST_ORIGIN_ERROR", "LAST_ORIGIN_WARNING", "_SYNC_CSV_HOOK"):
        if hasattr(state, name):
            monkeypatch.setattr(state, name, None)
    monkeypatch.setattr(state, "USE_FONTC", True)
    return src


# --- the mechanism, without fontc ----------------------------------------

def test_the_spec_carries_the_override_and_the_worker_applies_it(mini):
    """The CLI-side global reaches the spec; the worker bootstrap reads it back."""
    entries = [{"type": "spac_widthaware", "enabled": False, "params": {}},
               {"type": "fix_instances", "enabled": True, "params": {}}]
    tx_config.set_override(entries)

    # What build_jobs._stage puts in the spec.
    spec = {"original_path": str(mini / "CrispyMini.glyphs"),
            "job_dir": str(mini.parent / "job"),
            "transform_override": tx_config.override(),
            "env": {}}
    assert spec["transform_override"] == entries

    # A fresh worker process starts clean, then the bootstrap installs it.
    tx_config.set_override(None)
    assert tx_config.entries(mini / "CrispyMini.glyphs")[0]["enabled"] is True, \
        "the committed sidecar is the default"
    build_worker._bind_server_globals(spec)
    assert tx_config.entries(mini / "CrispyMini.glyphs") == [
        {"type": "spac_widthaware", "enabled": False, "params": {}},
        {"type": "fix_instances", "enabled": True, "params": {}}], \
        "the override must win inside the worker, instances.fix included"
    # ... and the committed file is untouched.
    assert json.loads((mini / "CrispyMini-transforms.json").read_text()) == SIDECAR


def test_no_override_leaves_the_sidecar_alone(mini):
    tx_config.set_override(None)
    build_worker._bind_server_globals({"original_path": str(mini / "CrispyMini.glyphs"),
                                       "job_dir": str(mini.parent / "job"),
                                       "transform_override": None,
                                       "env": {}})
    assert tx_config.entries(mini / "CrispyMini.glyphs")[0]["enabled"] is True


# --- end to end through the real worker (fontc) --------------------------

class _QueueServer:
    def _get_preview_dir(self):
        d = state.ORIGINAL_PATH.parent / ".avar2-studio"
        d.mkdir(exist_ok=True)
        return d

    def _get_avar2_font_dir(self):
        d = self._get_preview_dir() / "build"
        d.mkdir(parents=True, exist_ok=True)
        return d


@needs_fontc
def test_a_config_that_disables_a_transform_changes_the_worker_build(mini, monkeypatch):
    """The verifier's case: config at the resting style, one transform flipped.

    The built font must NOT carry the sidecar's SPAC axis — possible only if the
    worker got the override. ``instances.fix`` travels in the same list (asserted
    at the worker boundary above); the bundled example has no fvar instances to
    fix, so the font-level check is the transform.
    """
    monkeypatch.setattr(state, "ORIGINAL_PATH", mini / "CrispyMini.glyphs")
    monkeypatch.setattr(state, "GLYPHS_PATH", mini / "CrispyMini.glyphs")
    tx_config.set_override([
        {"type": "spac_widthaware", "enabled": False, "params": {}},
        {"type": "fix_instances", "enabled": True, "params": {}},
    ])

    queue = build_jobs.JobQueue(_QueueServer())
    queue.request_build()
    assert queue.wait_for_idle(timeout=600), f"build failed: {state.LAST_BUILD_ERROR}"
    built = state.VARIABLE_FONT_PATH
    assert built and Path(built).exists()
    deadline = time.time() + 120
    while time.time() < deadline and (
            queue.status()["running"] is not None or queue._pending_compress):
        time.sleep(0.25)

    font = TTFont(str(built))
    tags = {a.axisTag for a in font["fvar"].axes}
    assert "SPAC" not in tags, f"the config disabled SPAC but the font has {sorted(tags)}"
    assert json.loads((mini / "CrispyMini-transforms.json").read_text()) == SIDECAR, \
        "the committed transforms sidecar must never be rewritten"
