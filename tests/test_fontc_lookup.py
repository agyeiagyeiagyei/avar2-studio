"""CI0b — the studio finds fontc beside the interpreter, not only on PATH.

A venv's ``bin`` is not on PATH when the studio is started by absolute path:
the Glyphs plugin launcher, ``.venv/bin/python -m avar2_studio`` from a
script, and CI's ``.venv/bin/pytest`` all run that way. ``build_variable_font``
looked fontc up with ``shutil.which`` alone, so on those runs it fell through
to fontmake — which is not a dependency — and the build died with
``FileNotFoundError: 'fontmake'`` (the CI failure in
``tests/test_worker_shadow.py``).

No compilation here: the test replaces ``subprocess.run`` and checks which
executable the build would have used.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from avar2_studio import pipeline


class _Run:
    """Stands in for subprocess.run; succeeds iff the caller used the fontc
    we planted (so a fontmake fallback fails loudly instead of silently
    passing)."""

    def __init__(self, expected: str):
        self.expected = expected
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        ok = cmd[0] == self.expected
        if ok:
            # fontc writes the output file it was told to write.
            out = Path(cmd[cmd.index("--output-file") + 1])
            out.write_bytes(b"stub")
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "" if ok else "boom")


@pytest.fixture
def no_path_fontc(monkeypatch):
    """No fontc on PATH — the situation that broke CI."""
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: None)


def test_fontc_is_found_beside_the_interpreter(tmp_path, monkeypatch, no_path_fontc):
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    fake_python = bin_dir / "python"
    fake_python.write_text("")
    fontc = bin_dir / "fontc"
    fontc.write_text("")
    monkeypatch.setattr(sys, "executable", str(fake_python))
    run = _Run(str(fontc))
    monkeypatch.setattr(pipeline.subprocess, "run", run)

    source = tmp_path / "Font.glyphs"
    source.write_text("")
    out = pipeline.build_variable_font(source, tmp_path / "out", use_fontc=True)

    assert out.exists()
    assert run.calls and run.calls[0][0] == str(fontc)


def test_path_wins_when_both_exist(tmp_path, monkeypatch):
    """An activated venv's PATH copy is the one the user expects."""
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("")
    (bin_dir / "fontc").write_text("")
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr(pipeline.shutil, "which",
                        lambda name: "/somewhere/else/fontc" if name == "fontc" else None)
    run = _Run("/somewhere/else/fontc")
    monkeypatch.setattr(pipeline.subprocess, "run", run)

    source = tmp_path / "Font.glyphs"
    source.write_text("")
    pipeline.build_variable_font(source, tmp_path / "out", use_fontc=True)
    assert run.calls[0][0] == "/somewhere/else/fontc"
