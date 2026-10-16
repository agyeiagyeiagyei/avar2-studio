"""End to end through the REAL build worker (tests/test_build_jobs.py uses
the stub): a hand-drawn brace outline in the shadow must survive a build.

Uncaptured Fontra drawings exist only in ``.avar2-studio/shadow/`` until
``capture_outlines`` runs. The worker regenerates the shadow from the job
snapshot; when the snapshot omits the shadow (the T0.3 bug), regen
rebuilds the brace from the sidecar alone and the drawing is gone from
the built font.
"""
import sys
import time
from pathlib import Path

import glyphsLib
import pytest
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from avar2_studio import build_jobs, control_axes

FONTC = Path(sys.executable).with_name("fontc")
needs_fontc = pytest.mark.skipif(not FONTC.exists(),
                                 reason="fontc not next to the test interpreter")


class _CrispyServer:
    """The slice of server-module state the JobQueue touches (staging +
    promotion); the real worker subprocess does everything else."""

    def __init__(self, original):
        self.ORIGINAL_PATH = original
        self.GLYPHS_PATH = original
        self.USE_FONTC = True
        self.VARIABLE_FONT_PATH = None
        self.LAST_BUILD_STATUS = None
        self.LAST_BUILD_ERROR = None
        self._SYNC_CSV_HOOK = None

    def _get_preview_dir(self):
        d = self.ORIGINAL_PATH.parent / ".avar2-studio"
        d.mkdir(exist_ok=True)
        return d

    def _get_avar2_font_dir(self):
        d = self._get_preview_dir() / "build"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _draw_into(shadow_path, glyph_name, dy=250.0, tag="ymod"):
    """Simulate a Fontra edit: shift every node of OUR brace layer up."""
    font = glyphsLib.GSFont(str(shadow_path))
    master_ids = {m.id for m in font.masters}
    tags = [str(a.axisTag) for a in font.axes]
    idx = tags.index(tag) if tag in tags else None
    for layer in font.glyphs[glyph_name].layers:
        if layer.layerId in master_ids:
            continue
        coords = dict(layer.attributes or {}).get("coordinates")
        if not coords:
            continue
        if idx is None or idx >= len(coords) or float(coords[idx]) == 0.0:
            continue
        for p in layer.paths:
            for n in p.nodes:
                n.position = (n.position.x, n.position.y + dy)
    font.save(str(shadow_path))


def _outline_max_y(font_path, location, glyph="e"):
    font = TTFont(str(font_path))
    inst = instantiateVariableFont(font, location, inplace=True, optimize=False)
    coords, _endPts, _flags = inst["glyf"][glyph].getCoordinates(inst["glyf"])
    return max(coords, key=lambda p: p[1])[1]


@needs_fontc
def test_a_shadow_drawing_survives_the_worker_build(crispy_source, crispy_csv):
    control_axes.add_axis(crispy_source, "ymod", "Horizontal correction", 0, 0, 100)
    control_axes.apply_layer_delta(
        crispy_source, "ymod",
        add=[{"glyph": "e", "location": {"ymod": 100, "XOPQ": 500}}])
    shadow = control_axes.regenerate_shadow(crispy_source)
    assert shadow is not None
    _draw_into(shadow, "e")
    # The drawing is deliberately NOT captured into the sidecar (no
    # capture_outlines call) — the shadow is its only home, exactly like a
    # Fontra edit before the next export.

    queue = build_jobs.JobQueue(_CrispyServer(crispy_source))  # real worker
    queue.request_build()
    assert queue.wait_for_idle(timeout=600), \
        f"build failed: {queue.server.LAST_BUILD_ERROR}"
    built = queue.server.VARIABLE_FONT_PATH
    assert built and Path(built).exists()
    # drain the follow-up WOFF2 compress job so no worker outlives tmp_path
    deadline = time.time() + 120
    while time.time() < deadline and (
            queue.status()["running"] is not None or queue._pending_compress):
        time.sleep(0.25)

    font = TTFont(str(built))
    tags = {a.axisTag for a in font["fvar"].axes}
    assert "ymod" in tags, "control axis missing from the built font"
    at_brace = _outline_max_y(built, {"ymod": 100.0, "XOPQ": 500.0})
    at_rest = _outline_max_y(built, {"ymod": 0.0, "XOPQ": 500.0})
    assert at_brace - at_rest == pytest.approx(250.0, abs=2), \
        "the hand-drawn brace outline did not reach the built font"
