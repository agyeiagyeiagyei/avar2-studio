"""C6 — ``avar2-studio doctor --strict``.

`doctor` reports; a CI job wants a named cause before the build starts, and it
should not fail on UI-only checks (the frontend bundle, Fontra) that a headless
`avar2-studio build` never touches. `--strict` exits 1 exactly when a
headless-build prerequisite is missing — fontc, gftools-builder, glyphsLib, or a
native engine the environment points at (findings.md, phase C item C6).
"""

from __future__ import annotations

import pytest

from avar2_studio import doctor


def _ok(name, *, prereq=False, configured_missing=False):
    return doctor.CheckResult(name=name, ok=True, detail="present",
                              build_prereq=prereq,
                              configured_missing=configured_missing)


def _bad(name, *, prereq=True):
    return doctor.CheckResult(name=name, ok=False, detail="not on PATH",
                              fix=f"install {name}", build_prereq=prereq)


@pytest.fixture
def fake_checks(monkeypatch):
    """Replace CHECKS with a scripted set (so the test never depends on this box)."""
    def _install(results):
        monkeypatch.setattr(doctor, "CHECKS", [lambda r=r: r for r in results])
    return _install


def test_strict_passes_when_the_prerequisites_are_there(fake_checks, capsys):
    fake_checks([
        _ok("python", prereq=True),
        _ok("fontc", prereq=True),
        _ok("gftools-builder", prereq=True),
        _ok("glyphsLib", prereq=True),
        _ok("frontend bundle"),          # UI-only: not a prerequisite
        _ok("fontra (optional)"),
    ])
    assert doctor.run(strict=True) == 0
    out = capsys.readouterr().out
    assert "All build prerequisites present." in out


def test_strict_fails_and_names_the_missing_fontc(fake_checks, capsys):
    fake_checks([
        _ok("python", prereq=True),
        _bad("fontc"),
        _ok("gftools-builder", prereq=True),
        _ok("glyphsLib", prereq=True),
    ])
    assert doctor.run(strict=True) == 1
    out = capsys.readouterr().out
    assert "fontc" in out
    assert "build prerequisite(s) missing" in out
    assert "install fontc" in out, "the fix-it command must be printed"


def test_strict_ignores_ui_only_failures(fake_checks):
    """No frontend bundle / no Fontra is normal on a CI box; the build works."""
    fake_checks([
        _ok("fontc", prereq=True),
        _ok("gftools-builder", prereq=True),
        _ok("glyphsLib", prereq=True),
        doctor.CheckResult(name="frontend bundle", ok=False, detail="missing"),
        doctor.CheckResult(name="fontra (optional)", ok=False, detail="missing"),
    ])
    assert doctor.run(strict=True) == 0


def test_strict_fails_on_a_configured_but_missing_engine(fake_checks):
    fake_checks([
        _ok("fontc", prereq=True),
        _ok("gftools-builder", prereq=True),
        _ok("glyphsLib", prereq=True),
        _ok("spac-inject (optional)", configured_missing=True),
    ])
    assert doctor.run(strict=True) == 1


def test_the_default_mode_still_reports_every_failure(fake_checks, capsys):
    fake_checks([
        _ok("fontc", prereq=True),
        doctor.CheckResult(name="frontend bundle", ok=False, detail="missing"),
    ])
    assert doctor.run() == 1, "no --strict: any failed check exits 1, as before"
    assert "1 check(s) failed" in capsys.readouterr().out


def test_the_engines_flag_a_configured_but_missing_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AVAR2_SPAC_INJECT", str(tmp_path / "nope"))
    monkeypatch.setenv("AVAR2_KK2_DYLIB", str(tmp_path / "nope.dylib"))
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **k: None)

    spac = doctor.check_spac_injector()
    kk2 = doctor.check_kinetikern_engine()

    assert spac.configured_missing is True
    assert "does not exist" in spac.detail
    assert kk2.configured_missing is True


def test_the_engines_are_fine_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("AVAR2_SPAC_INJECT", raising=False)
    monkeypatch.delenv("AVAR2_KK2_DYLIB", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **k: None)

    assert doctor.check_spac_injector().configured_missing is False
    assert doctor.check_kinetikern_engine().configured_missing is False
