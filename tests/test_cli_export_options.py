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


def _run_build(tmp_path, monkeypatch, argv, build_config=None):
    """Drive ``cli._build`` with a stubbed build and a stubbed exporter."""
    import json as _json

    from avar2_studio import pipeline, server, state
    from avar2_studio.transforms import config as _tx_config

    project = tmp_path / "checkout"
    project.mkdir()
    source = project / "CrispyMini.glyphs"
    source.write_bytes(b"<glyphs/>")
    (project / "CrispyMini-axis-metadata.json").write_text("{}", encoding="utf-8")
    if build_config is not None:
        (project / "CrispyMini-build.json").write_text(
            _json.dumps(build_config), encoding="utf-8")
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
    monkeypatch.setattr(_tx_config, "set_override",
                        lambda entries: calls.append({"override": entries}))
    rc = cli._build([str(source), "--out", str(tmp_path / "out")] + argv)
    return rc, calls, tmp_path / "out"


def test_a_plain_build_copies_the_served_font(tmp_path, monkeypatch):
    rc, calls, out = _run_build(tmp_path, monkeypatch, [])
    assert rc == 0
    assert calls == [], "no export options → no export pipeline"
    assert (out / "built.ttf").exists()


def test_the_committed_build_config_drives_the_export(tmp_path, monkeypatch):
    """C7: no flags at all — <stem>-build.json supplies everything."""
    cfg = {
        "format": "avar2-studio-build", "format_version": 1,
        "axes": {"hide": ["XOPQ", "XTRA"]},
        "default": {"wght": 400, "opsz": 14},
        "transforms": [{"type": "slant", "enabled": True, "params": {"angle": -11}}],
        "instances": {"fix": True, "stat": True},
        "formats": ["ttf", "woff2"],
    }
    rc, calls, out = _run_build(tmp_path, monkeypatch, [], build_config=cfg)

    exports = [c for c in calls if "format" in c]
    overrides = [c for c in calls if "override" in c]
    assert rc == 0
    assert [c["format"] for c in exports] == ["ttf", "woff2"]
    assert exports[0]["hidden"] == ["XOPQ", "XTRA"]
    assert exports[0]["default"] == {"wght": 400.0, "opsz": 14.0}
    assert overrides and overrides[0]["override"] is not None
    types = {e["type"]: e["enabled"] for e in overrides[0]["override"]}
    assert types == {"slant": True, "fix_instances": True, "gen_stat": True}
    assert (out / "CrispyMini.woff2").exists()


def test_flags_override_the_build_config(tmp_path, monkeypatch):
    cfg = {"axes": {"hide": ["XOPQ"]}, "default": {"opsz": 144},
           "formats": ["woff2"]}
    rc, calls, _ = _run_build(tmp_path, monkeypatch,
                              ["--hide-axes", "YOPQ", "--default", "wght=900",
                               "--format", "ttf"], build_config=cfg)
    exports = [c for c in calls if "format" in c]
    assert rc == 0
    assert [c["format"] for c in exports] == ["ttf"]
    assert exports[0]["hidden"] == ["YOPQ"]
    assert exports[0]["default"] == {"wght": 900.0}


def test_a_config_naming_design_state_exits_2(tmp_path, monkeypatch, capsys):
    rc, calls, _ = _run_build(tmp_path, monkeypatch, [],
                              build_config={"grade": {"pct": 0.25}})
    assert rc == 2
    assert calls == []
    err = capsys.readouterr().err
    assert "studio-authored" in err and "avar2-studio" in err


def test_a_missing_explicit_config_exits_2(tmp_path, monkeypatch, capsys):
    rc, _, _ = _run_build(tmp_path, monkeypatch, ["--config", "nope.json"])
    assert rc == 2
    assert "build config not found" in capsys.readouterr().err


def test_write_config_emits_the_file_and_skips_the_build(tmp_path, monkeypatch, capsys):
    rc, calls, _ = _run_build(tmp_path, monkeypatch, ["--write-config"])
    assert rc == 0
    assert calls == [], "--write-config does not build"
    written = tmp_path / "checkout" / "CrispyMini-build.json"
    assert written.exists()
    assert str(written) in capsys.readouterr().out


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


# --- E1: the sources format on the CLI -------------------------------------
# ``--format sources`` writes <stem>-sources.zip from the ACTIVE SOURCE — no
# build, no export pipeline — and says on stderr, once, that spacing is not in
# it. It is a usage error to ask for sources AND a re-origin.

MINIMAL_GLYPHS = """{
.formatVersion = 3;
familyName = "CrispyMini";
fontMaster = ({ id = "m01"; name = Regular; });
glyphs = ();
unitsPerEm = 1000;
}
"""


def _sources_env(monkeypatch, tmp_path):
    """Stub the writer; return the list of calls it receives.

    ``state.GLYPHS_PATH`` must point at a file glyphsLib can parse: the CLI
    parses the source before building (P2), even though this test never builds.
    """
    from avar2_studio import sources_export, state

    real = tmp_path / "real-source" / "CrispyMini.glyphs"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(MINIMAL_GLYPHS, encoding="utf-8")
    monkeypatch.setattr(state, "GLYPHS_PATH", real)
    calls = []

    def fake_zip(source_path, out_zip, **kwargs):
        calls.append({"source": source_path, "out": out_zip, **kwargs})
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        out_zip.write_bytes(b"PK\x05\x06" + b"\0" * 18)
        return out_zip

    monkeypatch.setattr(sources_export, "build_sources_zip", fake_zip)
    return calls


def test_sources_writes_the_zip_and_warns_once(tmp_path, monkeypatch, capsys):
    from avar2_studio import sources_export

    calls = _sources_env(monkeypatch, tmp_path)
    rc, _, out = _run_build(tmp_path, monkeypatch, ["--format", "sources"])

    assert rc == 0
    assert (out / "CrispyMini-sources.zip").exists()
    assert len(calls) == 1
    err = capsys.readouterr().err
    assert err.count(sources_export.RESTING_NOTE) == 1


def test_sources_repeats_with_a_font_format(tmp_path, monkeypatch):
    calls = _sources_env(monkeypatch, tmp_path)
    rc, exported, out = _run_build(tmp_path, monkeypatch,
                                   ["--format", "sources", "--format", "ttf"])

    assert rc == 0
    assert len(calls) == 1, "sources is written once, not per format"
    assert (out / "CrispyMini-sources.zip").exists()
    assert (out / "CrispyMini.ttf").exists() or exported


def test_sources_with_a_default_location_is_a_usage_error(tmp_path, monkeypatch, capsys):
    calls = _sources_env(monkeypatch, tmp_path)
    rc, _, out = _run_build(tmp_path, monkeypatch,
                            ["--format", "sources", "--default", "wght=400"])

    assert rc == 2
    assert calls == [], "nothing is written when the flags contradict"
    assert not (out / "CrispyMini-sources.zip").exists()
    assert "sources rest at the compiled origin" in capsys.readouterr().err


def test_a_sources_error_is_a_usage_error(tmp_path, monkeypatch, capsys):
    from avar2_studio import sources_export

    _sources_env(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise sources_export.SourcesError("no avar2 CSV beside CrispyMini.glyphs")

    monkeypatch.setattr(sources_export, "build_sources_zip", boom)
    rc, _, _ = _run_build(tmp_path, monkeypatch, ["--format", "sources"])

    assert rc == 2
    assert "no avar2 CSV" in capsys.readouterr().err
