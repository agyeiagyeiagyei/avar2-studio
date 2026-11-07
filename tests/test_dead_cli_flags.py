"""T1.9: the dead --csv / --build-dir flags fail loudly.

The worker builds a staged snapshot (sibling sidecars, job-dir output),
so --csv/--build-dir changed nothing the user could see — they set
globals nothing reads. Both now exit non-zero with a clear message.
"""
import subprocess
import sys


def _run(args):
    return subprocess.run([sys.executable, "-m", "avar2_studio"] + args,
                          capture_output=True, text=True, timeout=60)


def test_server_csv_flag_fails_loudly(tmp_path):
    src = tmp_path / "F.glyphs"
    src.write_text("familyName = F;")
    r = _run([str(src), "--port", "5198", "--csv", str(tmp_path / "x.csv")])
    assert r.returncode == 2
    assert "--csv is no longer supported" in r.stderr


def test_server_build_dir_flag_fails_loudly(tmp_path):
    src = tmp_path / "F.glyphs"
    src.write_text("familyName = F;")
    r = _run([str(src), "--port", "5198", "--build-dir", str(tmp_path / "b")])
    assert r.returncode == 2
    assert "--build-dir is no longer supported" in r.stderr


def test_build_cli_csv_flag_fails_loudly(tmp_path):
    src = tmp_path / "F.glyphs"
    src.write_text("familyName = F;")
    r = _run(["build", str(src), "--out", str(tmp_path / "out"),
              "--csv", str(tmp_path / "x.csv")])
    assert r.returncode == 2
    assert "--csv is no longer supported" in r.stderr


def test_a_normal_launch_still_works(tmp_path):
    """The flag check must not break a plain invocation."""
    r = _run(["--help"])
    assert r.returncode == 0
