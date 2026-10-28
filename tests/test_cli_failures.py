"""C3 — every failure reason reaches the CLI's exit message.

A ``degraded`` build (origin relocation failed, S1(b)) exited 1 as
"build failed: unknown" because ``LAST_BUILD_ERROR`` is empty for it — the
relocation message lives in its own global. ``_failure_reasons`` now collects
every recorded ``LAST_*`` reason, one per line, prefixed ``error:``, and the
exit codes are pinned: 0 ok, 1 build degraded/failed, 2 usage/load error
(findings.md, phase C item C3).
"""

from __future__ import annotations

import pytest

from avar2_studio import cli, state

LAST_GLOBALS = (
    "LAST_BUILD_STATUS", "LAST_BUILD_ERROR", "LAST_AVAR2_ERROR",
    "LAST_ORIGIN_RELOCATION_ERROR", "LAST_GRADE_ERROR",
    "LAST_SOURCE_TRANSFORM_ERROR", "VARIABLE_FONT_PATH",
)


@pytest.fixture
def clean_state(monkeypatch):
    for name in LAST_GLOBALS:
        monkeypatch.setattr(state, name, None)
    return state


def test_a_healthy_build_has_no_reasons(clean_state):
    clean_state.LAST_BUILD_STATUS = "ok"
    clean_state.VARIABLE_FONT_PATH = "/tmp/x.ttf"
    assert cli._failure_reasons(clean_state) == []


def test_degraded_reports_the_relocation_error_not_unknown(clean_state):
    clean_state.LAST_BUILD_STATUS = "degraded"
    clean_state.VARIABLE_FONT_PATH = "/tmp/x.ttf"
    clean_state.LAST_ORIGIN_RELOCATION_ERROR = "over the gvar ceiling"

    reasons = cli._failure_reasons(clean_state)

    assert len(reasons) == 1
    assert "origin relocation failed" in reasons[0]
    assert "over the gvar ceiling" in reasons[0]
    assert "unknown" not in reasons[0]


def test_every_recorded_reason_is_reported(clean_state):
    clean_state.LAST_BUILD_STATUS = "degraded"
    clean_state.VARIABLE_FONT_PATH = "/tmp/x.ttf"
    clean_state.LAST_ORIGIN_RELOCATION_ERROR = "reloc"
    clean_state.LAST_AVAR2_ERROR = "avar2 broke"
    clean_state.LAST_GRADE_ERROR = "grade broke"
    clean_state.LAST_SOURCE_TRANSFORM_ERROR = "corners broke"

    reasons = cli._failure_reasons(clean_state)

    assert len(reasons) == 4, reasons
    assert any("avar2 broke" in r for r in reasons)
    assert any("grade broke" in r for r in reasons)
    assert any("corners broke" in r for r in reasons)
    assert any("reloc" in r for r in reasons)


def test_a_failed_build_names_its_error(clean_state):
    clean_state.LAST_BUILD_STATUS = "failed"
    clean_state.LAST_BUILD_ERROR = "fontc exited 1"
    assert cli._failure_reasons(clean_state) == ["build failed: fontc exited 1"]


def test_a_failed_build_with_no_error_says_so(clean_state):
    clean_state.LAST_BUILD_STATUS = "failed"
    reasons = cli._failure_reasons(clean_state)
    assert reasons == ["build failed: failed"], "no silent 'unknown' path"


def test_the_three_exit_codes(clean_state, tmp_path, monkeypatch):
    from avar2_studio import server

    project = tmp_path / "checkout"
    project.mkdir()
    source = project / "CrispyMini.glyphs"
    source.write_bytes(b"<glyphs/>")
    (project / "CrispyMini-axis-metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "_apply_source_path", lambda p: None)
    monkeypatch.setattr(state.JOBS, "wait_for_idle", lambda *a, **k: None)

    # 0 — ok, and the font is copied out.
    built = tmp_path / "built.ttf"
    built.write_bytes(b"\0\1\0\0")
    clean_state.LAST_BUILD_STATUS = "ok"
    clean_state.VARIABLE_FONT_PATH = built
    assert cli._build([str(source), "--out", str(tmp_path / "out0")]) == 0
    assert (tmp_path / "out0" / "built.ttf").exists()

    # 1 — degraded, with the relocation reason printed.
    clean_state.LAST_BUILD_STATUS = "degraded"
    clean_state.LAST_ORIGIN_RELOCATION_ERROR = "over the ceiling"
    assert cli._build([str(source), "--out", str(tmp_path / "out1")]) == 1

    # 2 — a usage error (no axis metadata in either home).
    (project / "CrispyMini-axis-metadata.json").unlink()
    assert cli._build([str(source), "--out", str(tmp_path / "out2")]) == 2

    # 2 — a source that cannot be loaded.
    monkeypatch.setattr(server, "_apply_source_path",
                        lambda p: (_ for _ in ()).throw(ValueError("bad source")))
    (project / "CrispyMini-axis-metadata.json").write_text("{}", encoding="utf-8")
    assert cli._build([str(source), "--out", str(tmp_path / "out3")]) == 2


def test_the_degraded_message_is_printed_prefixed(clean_state, tmp_path, monkeypatch, capsys):
    from avar2_studio import server

    project = tmp_path / "checkout"
    project.mkdir()
    source = project / "CrispyMini.glyphs"
    source.write_bytes(b"<glyphs/>")
    (project / "CrispyMini-axis-metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "_apply_source_path", lambda p: None)
    monkeypatch.setattr(state.JOBS, "wait_for_idle", lambda *a, **k: None)
    clean_state.LAST_BUILD_STATUS = "degraded"
    clean_state.LAST_ORIGIN_RELOCATION_ERROR = "over the gvar ceiling"

    assert cli._build([str(source), "--out", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert "error: origin relocation failed" in err
    assert "over the gvar ceiling" in err


def test_an_unreadable_source_exits_2_with_a_readable_message(
        clean_state, tmp_path, monkeypatch, capsys):
    """P2 — an existing but unparseable source is a LOAD error (exit 2), and
    the message names the file and the parse problem, never a Python attribute
    error from deep inside the build."""
    from avar2_studio import server

    project = tmp_path / "checkout"
    project.mkdir()
    source = project / "Crispy.glyphs"
    source.write_text("this is not a glyphs file\n", encoding="utf-8")
    (project / "Crispy-axis-metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "_apply_source_path", lambda p: None)
    clean_state.GLYPHS_PATH = source

    rc = cli._build([str(source), "--out", str(tmp_path / "out")])

    assert rc == 2, "bad input must exit 2, not 1"
    err = capsys.readouterr().err
    assert "Crispy.glyphs could not be read:" in err
    assert "build failed" not in err, err
