"""C1 — the declared axis defaults ship with the source.

They lived in ``.avar2-studio/axis-metadata.json``, which SIDECARS.md tells
designers not to commit. A clean checkout therefore built with each column's
minimum as the default — opsz 144 on Crispy collapsed to the CSV minimum and
every named style landed off its mapped value (62 u measured). The canonical
home is now the committed sibling ``<stem>-axis-metadata.json``; the old
location is read as a fallback and migrated on first access (findings.md,
phase C item C1).
"""

from __future__ import annotations

import json

import pytest

from avar2_studio import cli, pipeline, state
from avar2_studio.build import config_generator

META = {
    "XTRA": {"display_name": "X-Transparency", "registered_tag": "xtra",
             "min": 94.0, "max": 3330.0, "is_parametric": True},
    "OPSZ": {"display_name": "Optical Size", "registered_tag": "opsz",
             "is_parametric": False, "min": 12, "default": 144, "max": 144},
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A source with no sidecars yet, bound in state as the studio does."""
    source = tmp_path / "Crispy.glyphs"
    source.write_bytes(b"<glyphs/>")  # only the path matters here
    monkeypatch.setattr(state, "ORIGINAL_PATH", source)
    monkeypatch.setattr(state, "GLYPHS_PATH", source)
    monkeypatch.setattr(state, "PREVIEW_DIR", tmp_path / ".avar2-studio")
    (tmp_path / ".avar2-studio").mkdir()
    return source


def test_the_canonical_path_is_the_sibling(project):
    path = pipeline._get_avar2_metadata_path()
    assert path == project.parent / "Crispy-axis-metadata.json"
    assert path.parent == project.parent, "must sit beside the source, not in the workdir"


def test_the_writer_writes_the_sibling(project):
    assert pipeline._save_axis_metadata(META) is True
    sibling = project.parent / "Crispy-axis-metadata.json"
    assert sibling.exists()
    assert json.loads(sibling.read_text())["OPSZ"]["default"] == 144
    assert not (project.parent / ".avar2-studio" / "axis-metadata.json").exists()


def test_a_legacy_file_is_migrated_once(project):
    legacy = project.parent / ".avar2-studio" / "axis-metadata.json"
    legacy.write_text(json.dumps(META), encoding="utf-8")

    path = pipeline._get_avar2_metadata_path()

    sibling = project.parent / "Crispy-axis-metadata.json"
    assert path == sibling
    assert json.loads(sibling.read_text()) == META, "content carried over verbatim"
    assert not legacy.exists(), "migrated, not duplicated"
    # Second access is a plain hit — no re-migration, no rewrite.
    before = sibling.stat().st_mtime_ns
    assert pipeline._get_avar2_metadata_path() == sibling
    assert sibling.stat().st_mtime_ns == before


def test_an_existing_sibling_wins_over_the_legacy_file(project):
    sibling = project.parent / "Crispy-axis-metadata.json"
    sibling.write_text(json.dumps({"OPSZ": {"default": 14}}), encoding="utf-8")
    legacy = project.parent / ".avar2-studio" / "axis-metadata.json"
    legacy.write_text(json.dumps(META), encoding="utf-8")

    assert pipeline._get_avar2_metadata_path() == sibling
    assert json.loads(sibling.read_text())["OPSZ"]["default"] == 14
    assert legacy.exists(), "the fallback is left alone when the sibling exists"


def test_declared_defaults_read_the_committed_sibling(project, tmp_path):
    sibling = project.parent / "Crispy-axis-metadata.json"
    sibling.write_text(json.dumps(META), encoding="utf-8")
    config = tmp_path / ".avar2-studio" / "config.yaml"
    config.write_text("sources: []\n", encoding="utf-8")

    declared = config_generator._declared_axis_defaults(config, sibling)
    assert declared == {"opsz": 144}, "the collision guard must see the declared default"


def test_the_cli_requires_one_of_the_two_homes(project):
    sibling, legacy = cli._axis_metadata_paths(project)
    assert cli._axis_metadata_problem(project), "neither home → a hard error"

    legacy.write_text(json.dumps(META), encoding="utf-8")
    assert cli._axis_metadata_problem(project) is None, "a studio-created file still builds"
    legacy.unlink()

    sibling.write_text(json.dumps(META), encoding="utf-8")
    assert cli._axis_metadata_problem(project) is None, "the committed sibling builds"


def test_the_cli_error_names_both_files(project):
    message = cli._axis_metadata_problem(project)
    assert "Crispy-axis-metadata.json" in message
    assert ".avar2-studio/axis-metadata.json" in message
    assert "commit" in message


def test_an_unreadable_legacy_file_is_kept_and_still_read(project):
    legacy = project.parent / ".avar2-studio" / "axis-metadata.json"
    legacy.write_text("{not json", encoding="utf-8")

    path = pipeline._get_avar2_metadata_path()

    assert path == legacy, "a failed migration must not lose the declarations"
    assert legacy.exists()
    assert not (project.parent / "Crispy-axis-metadata.json").exists()
