"""C2 — ``avar2-studio build --workdir DIR``.

The build wrote ``.avar2-studio/`` (config, shadow, build output, relocation
memo) beside the source, so CI's checkout gained a workdir it never committed.
``--workdir`` moves the whole tool workdir under DIR; the authored sidecars
(``-avar.csv``, ``-axis-metadata.json``, …) stay beside the source because they
are inputs (findings.md, phase C item C2).
"""

from __future__ import annotations

import pytest

from avar2_studio import cli, pipeline, state

REPO_SOURCE = ("examples/crispy-mini/sources/CrispyMini.glyphs")


@pytest.fixture
def project(tmp_path, monkeypatch):
    from pathlib import Path

    source = tmp_path / "checkout" / "CrispyMini.glyphs"
    source.parent.mkdir()
    sample = Path(__file__).resolve().parents[1] / REPO_SOURCE
    source.write_bytes(sample.read_bytes())
    # The CI case: the mappings CSV is committed, so nothing is bootstrapped.
    (source.parent / "CrispyMini-avar.csv").write_bytes(
        (sample.parent / "CrispyMini-avar.csv").read_bytes())
    monkeypatch.setattr(state, "PREVIEW_DIR", None)
    monkeypatch.setattr(state, "WORKDIR_OVERRIDE", None)
    monkeypatch.setattr(state, "ORIGINAL_PATH", None)
    monkeypatch.setattr(state, "GLYPHS_PATH", None)
    # _apply_source_path also rewrites these; restore them for later tests.
    monkeypatch.setattr(state, "BUILD_DIR", None)
    monkeypatch.setattr(state, "SOURCE_FORMAT", None)
    monkeypatch.setattr(state, "PREVIEW_CSV_PATH", None)
    monkeypatch.setattr(state, "PREVIEW_CONFIG_PATH", None)
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", None)
    # _apply_source_path kicks a build; nothing here needs the worker.
    monkeypatch.setattr(state.JOBS, "request_build", lambda *a, **k: None)
    monkeypatch.setattr(state.JOBS, "cancel_all", lambda *a, **k: None)
    return source


def test_without_the_flag_the_workdir_sits_beside_the_source(project, monkeypatch):
    monkeypatch.setattr(state, "WORKDIR_OVERRIDE", None)
    monkeypatch.setattr(state, "ORIGINAL_PATH", project)
    monkeypatch.setattr(state, "GLYPHS_PATH", project)

    assert pipeline._get_preview_dir() == project.parent / ".avar2-studio"


def test_the_flag_moves_the_workdir(project, tmp_path, monkeypatch):
    monkeypatch.setattr(state, "WORKDIR_OVERRIDE", tmp_path / "build-root")
    monkeypatch.setattr(state, "ORIGINAL_PATH", project)
    monkeypatch.setattr(state, "GLYPHS_PATH", project)

    assert pipeline._get_preview_dir() == tmp_path / "build-root" / ".avar2-studio"


def test_applying_a_source_creates_nothing_beside_the_checkout(project, tmp_path, monkeypatch):
    monkeypatch.setattr(state, "WORKDIR_OVERRIDE", tmp_path / "build-root")

    pipeline._apply_source_path(project)

    workdir = tmp_path / "build-root" / ".avar2-studio"
    assert state.PREVIEW_DIR == workdir
    assert state.BUILD_DIR == workdir / "build"
    assert state.BUILD_DIR.is_dir()
    beside = sorted(p.name for p in project.parent.iterdir())
    assert beside == ["CrispyMini-avar.csv", "CrispyMini.glyphs"], f"checkout gained {beside}"
    assert not (project.parent / ".avar2-studio").exists()


def test_the_cli_sets_the_override_before_loading_the_source(project, tmp_path, monkeypatch):
    import sys

    from avar2_studio import server

    seen = {}

    def _apply(path):
        seen["workdir_override"] = state.WORKDIR_OVERRIDE
        seen["source"] = path

    monkeypatch.setattr(server, "_apply_source_path", _apply)
    monkeypatch.setattr(state.JOBS, "wait_for_idle", lambda *a, **k: None)
    built = tmp_path / "built.ttf"
    built.write_bytes(b"\0\1\0\0")
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", built)
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    monkeypatch.setattr(state, "LAST_BUILD_ERROR", None)
    monkeypatch.setattr(state, "LAST_AVAR2_ERROR", None)
    monkeypatch.setattr(state, "LAST_GRADE_ERROR", None)
    monkeypatch.setattr(state, "LAST_SOURCE_TRANSFORM_ERROR", None)
    # --workdir is only honoured together with a metadata file (C1).
    (project.parent / "CrispyMini-axis-metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["avar2-studio"])

    rc = cli._build([str(project), "--out", str(tmp_path / "out"),
                     "--workdir", str(tmp_path / "wd")])

    assert rc == 0
    assert seen["workdir_override"] == (tmp_path / "wd").resolve()
    assert seen["source"] == project
    assert (tmp_path / "out" / "built.ttf").exists()
