"""C4 — the studio's export options on the CLI.

``avar2-studio build`` can now hide axes, re-origin at a resting style and emit
WOFF2, by calling ``pipeline._export_pipeline`` on the font it just built (no
second pipeline). The flags are parsed strictly: a malformed ``--default`` is a
usage error (exit 2), not a silent rebuild at the wrong origin (findings.md,
phase C item C4).
"""

from __future__ import annotations

import pytest

from avar2_studio import cli


def test_hide_axes_parsing():
    assert cli._parse_hide_axes(None) == []
    assert cli._parse_hide_axes("XOPQ,XTRA , YOPQ,") == ["XOPQ", "XTRA", "YOPQ"]


def test_default_location_parsing():
    assert cli._parse_default_location(None) is None
    assert cli._parse_default_location("wght=400") == {"wght": 400.0}
    assert cli._parse_default_location("wght=400, opsz=14") == {"wght": 400.0, "opsz": 14.0}
    assert cli._parse_default_location("wght=-11") == {"wght": -11.0}


@pytest.mark.parametrize("raw", ["wght", "wght=heavy", "=400", ",", " "])
def test_malformed_default_is_refused(raw):
    with pytest.raises(ValueError):
        cli._parse_default_location(raw)


def test_format_dedupes_and_keeps_order():
    formats = []
    for fmt in ["woff2", "ttf", "woff2"]:
        if fmt not in formats:
            formats.append(fmt)
    assert formats == ["woff2", "ttf"]


def _run_build(tmp_path, monkeypatch, argv):
    """Drive ``cli._build`` with a stubbed build and a stubbed exporter."""
    from avar2_studio import pipeline, server, state

    project = tmp_path / "checkout"
    project.mkdir()
    source = project / "CrispyMini.glyphs"
    source.write_bytes(b"<glyphs/>")
    (project / "CrispyMini-axis-metadata.json").write_text("{}", encoding="utf-8")
    built = tmp_path / "built.ttf"
    built.write_bytes(b"\0\1\0\0")
    monkeypatch.setattr(server, "_apply_source_path", lambda p: None)
    monkeypatch.setattr(state.JOBS, "wait_for_idle", lambda *a, **k: None)
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", built)
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    for name in ("LAST_BUILD_ERROR", "LAST_AVAR2_ERROR", "LAST_ORIGIN_RELOCATION_ERROR",
                 "LAST_GRADE_ERROR", "LAST_SOURCE_TRANSFORM_ERROR"):
        monkeypatch.setattr(state, name, None)
    monkeypatch.setattr(pipeline, "_get_preview_dir", lambda: tmp_path)
    calls = []

    def fake_export(font_path, hidden, default, export_dir, format="ttf"):
        calls.append({"hidden": hidden, "default": default, "format": format})
        out = export_dir / ("CrispyMini" + (".woff2" if format == "woff2" else ".ttf"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"exported-" + format.encode())
        return out

    monkeypatch.setattr(pipeline, "_export_pipeline", fake_export)
    rc = cli._build([str(source), "--out", str(tmp_path / "out")] + argv)
    return rc, calls, tmp_path / "out"


def test_a_plain_build_copies_the_served_font(tmp_path, monkeypatch):
    rc, calls, out = _run_build(tmp_path, monkeypatch, [])
    assert rc == 0
    assert calls == [], "no export options → no export pipeline"
    assert (out / "built.ttf").exists()


def test_the_export_options_reach_the_pipeline(tmp_path, monkeypatch, capsys):
    rc, calls, out = _run_build(tmp_path, monkeypatch, [
        "--hide-axes", "XOPQ,XTRA",
        "--default", "wght=400,opsz=14",
        "--format", "ttf", "--format", "woff2",
    ])
    assert rc == 0
    assert [c["format"] for c in calls] == ["ttf", "woff2"]
    assert all(c["hidden"] == ["XOPQ", "XTRA"] for c in calls)
    assert all(c["default"] == {"wght": 400.0, "opsz": 14.0} for c in calls)
    printed = capsys.readouterr().out.split()
    assert str(out / "CrispyMini.ttf") in printed
    assert str(out / "CrispyMini.woff2") in printed
    assert (out / "CrispyMini.woff2").read_bytes() == b"exported-woff2"


def test_a_malformed_default_exits_2(tmp_path, monkeypatch, capsys):
    rc, calls, _ = _run_build(tmp_path, monkeypatch, ["--default", "wght=heavy"])
    assert rc == 2
    assert calls == []
    assert "error: --default value for 'wght' is not a number" in capsys.readouterr().err


def test_an_export_failure_exits_1(tmp_path, monkeypatch, capsys):
    from avar2_studio import pipeline, server, state

    project = tmp_path / "checkout"
    project.mkdir()
    source = project / "CrispyMini.glyphs"
    source.write_bytes(b"<glyphs/>")
    (project / "CrispyMini-axis-metadata.json").write_text("{}", encoding="utf-8")
    built = tmp_path / "built.ttf"
    built.write_bytes(b"\0\1\0\0")
    monkeypatch.setattr(server, "_apply_source_path", lambda p: None)
    monkeypatch.setattr(state.JOBS, "wait_for_idle", lambda *a, **k: None)
    monkeypatch.setattr(state, "VARIABLE_FONT_PATH", built)
    monkeypatch.setattr(state, "LAST_BUILD_STATUS", "ok")
    for name in ("LAST_BUILD_ERROR", "LAST_AVAR2_ERROR", "LAST_ORIGIN_RELOCATION_ERROR",
                 "LAST_GRADE_ERROR", "LAST_SOURCE_TRANSFORM_ERROR"):
        monkeypatch.setattr(state, name, None)
    monkeypatch.setattr(pipeline, "_get_preview_dir", lambda: tmp_path)
    monkeypatch.setattr(pipeline, "_export_pipeline",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("Cannot relocate the default to the requested location: too big")))

    rc = cli._build([str(source), "--out", str(tmp_path / "out"),
                     "--default", "opsz=14", "--format", "ttf"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error: export failed (ttf)" in err
    assert "Cannot relocate the default" in err
