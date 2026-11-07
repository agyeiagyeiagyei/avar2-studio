"""T1.30: doctor reports the native accelerators and their env overrides."""
from avar2_studio import doctor


def test_spac_and_kinetikern_checks_report_absence_and_env(monkeypatch, capsys):
    monkeypatch.delenv("AVAR2_SPAC_INJECT", raising=False)
    monkeypatch.delenv("AVAR2_KK2_DYLIB", raising=False)
    # Absence is simulated, not read off the checkout: built binaries must
    # not flip this test (target/release or the package-carried bin/).
    from avar2_studio import kinetikern
    from avar2_studio.transforms import builtin_spac_widthaware
    monkeypatch.setattr(builtin_spac_widthaware, "_native_injector", lambda: None)
    monkeypatch.setattr(kinetikern, "engine_path", lambda: None)

    r1 = doctor.check_spac_injector()
    r2 = doctor.check_kinetikern_engine()
    # optional: absence never fails the gate
    assert r1.ok and r2.ok
    assert "spac-inject" in r1.name
    assert "kinetikern2" in r2.name
    assert r1.fix and "cargo build" in r1.fix
    assert r2.fix and "cargo build" in r2.fix

    # An env override pointing at nothing is called out by name.
    monkeypatch.setenv("AVAR2_SPAC_INJECT", "/nonexistent/spac-inject")
    monkeypatch.setenv("AVAR2_KK2_DYLIB", "/nonexistent/lib.dylib")
    r3 = doctor.check_spac_injector()
    r4 = doctor.check_kinetikern_engine()
    assert "AVAR2_SPAC_INJECT" in r3.detail
    assert "AVAR2_KK2_DYLIB" in r4.detail


def test_spac_check_reports_the_env_override_when_it_exists(monkeypatch, tmp_path):
    fake = tmp_path / "spac-inject"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("AVAR2_SPAC_INJECT", str(fake))
    r = doctor.check_spac_injector()
    assert r.ok
    assert str(fake) in r.detail and "AVAR2_SPAC_INJECT" in r.detail
