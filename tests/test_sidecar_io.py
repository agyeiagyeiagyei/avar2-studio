"""T2.1: one lock + one atomic writer for every sidecar."""
import json
import re
import threading
from pathlib import Path

import pytest

from avar2_studio import sidecar_io

SRC = Path(__file__).resolve().parents[1] / "src" / "avar2_studio"


def test_a_failed_write_leaves_the_target_and_no_temp_file(tmp_path):
    target = tmp_path / "x-control.json"
    target.write_text('{"version": 1}')
    with pytest.raises(RuntimeError):
        with sidecar_io.atomic_writer(target) as f:
            f.write('{"version": 2, "half":')
            raise RuntimeError("boom")
    assert target.read_text() == '{"version": 1}'
    assert [p.name for p in tmp_path.iterdir()] == ["x-control.json"]


def test_write_json_round_trips_and_keeps_the_mode(tmp_path):
    target = tmp_path / "x-grade.json"
    target.write_text("{}")
    target.chmod(0o600)
    sidecar_io.write_json(target, {"b": 1, "a": [1, 2]})
    assert json.loads(target.read_text()) == {"b": 1, "a": [1, 2]}
    assert target.read_text().endswith("\n")
    assert target.stat().st_mode & 0o777 == 0o600


def test_locked_read_modify_write_loses_no_updates(tmp_path):
    target = tmp_path / "counter.json"
    sidecar_io.write_json(target, {"n": 0})

    def bump():
        for _ in range(40):
            with sidecar_io.LOCK:
                n = json.loads(target.read_text())["n"]
                sidecar_io.write_json(target, {"n": n + 1})

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.loads(target.read_text())["n"] == 320


def test_every_sidecar_writer_goes_through_sidecar_io():
    """Code-shape guard: no raw truncating ``open("w")`` on a sidecar path
    anywhere under src/. Sidecar paths are recognised by the variable
    names the writers use."""
    raw = re.compile(r"\b(csv_path|sidecar|metadata_path|config_path)\s*\.open\(\s*['\"]w['\"]")
    offenders = []
    for py in SRC.rglob("*.py"):
        if "glyphs" in py.parts:  # Glyphs.app plugins write their own files
            continue
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if raw.search(line):
                offenders.append(f"{py.relative_to(SRC)}:{i}: {line.strip()}")
    assert not offenders, "raw sidecar writes:\n" + "\n".join(offenders)
