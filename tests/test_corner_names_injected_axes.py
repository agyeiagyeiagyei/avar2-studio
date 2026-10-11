"""Corner/instance naming must ignore the studio-injected shadow axes.

Enabling grade appends GRAD to the shadow's axis list; naming keyed on
"all axes minus control axes" then included GRAD — the CSV has no GRAD
column, so every instance-corner lookup failed and the layers panel
(and new shadow labels) lost their style names.
"""

import csv
import sys
from pathlib import Path
from types import SimpleNamespace as NS

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from avar2_studio import control_axes as ca  # noqa: E402


def _font(with_grad):
    tags = ["XTRA", "XOPQ", "YOPQ", "ymod"] + (["GRAD"] if with_grad else [])
    axes = [NS(axisTag=t) for t in tags]
    # Master vector matches the axis list; the source instance keeps the
    # SHORTER pre-GRAD vector (grade pads masters, not instances).
    master = NS(axes=[100.0, 10.0, 5.0, 0.0] + ([0.0] if with_grad else []),
                name="Master A")
    inst = NS(axes=[200.0, 20.0, 9.0, 0.0], name="Source Inst")
    return NS(axes=axes, masters=[master], instances=[inst])


def _csv(tmp_path):
    src = tmp_path / "T.glyphs"
    src.write_text("{}")
    p = tmp_path / "T-avar.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Instance Name", "XTRA", "XOPQ", "YOPQ", "SPAC"])
        w.writerow(["Wide 900", "300.0", "30.0", "7.0", "20"])
    return src


def test_names_survive_grade_axis(tmp_path):
    src = _csv(tmp_path)
    control_idx = {3}  # ymod
    for with_grad in (False, True):
        font = _font(with_grad)
        masters, instances = ca.corner_name_maps(font, control_idx, src)
        assert masters == {(100.0, 10.0, 5.0): "Master A"}, (with_grad, masters)
        assert instances == {(200.0, 20.0, 9.0): "Source Inst",
                             (300.0, 30.0, 7.0): "Wide 900"}, (with_grad, instances)
        resolve = ca.corner_name_resolver(font, control_idx, src)
        assert resolve({"XTRA": 300.0, "XOPQ": 30.0, "YOPQ": 7.0,
                        "ymod": 100.0}) == "Wide 900"
        assert resolve({"ymod": 100.0}) == "Master A"  # defaults → master
