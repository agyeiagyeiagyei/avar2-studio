"""T3.5: one mappings-CSV reader, JSON to the native SPAC injector.

csv_io owns the parse; the width-aware transform hands the per-instance
SPAC values to the spac-inject binary inside the transforms JSON
(``params.instance_spac``) — Rust never opens the CSV. These tests pin
the parse rules the CSV contract relies on (quoted comma in an instance
name, blank cells, BOM, the bad-cell prefix) and their carriage into the
payload. The Rust-side parity half lives in
wasm/fontc-web/tests/spac_csv_parity.rs.
"""

import json

from avar2_studio import csv_io
from avar2_studio.transforms import builtin_spac_widthaware as wa
from avar2_studio.transforms.builtin_spac import _instance_spac_overrides

CSV_TEXT = (
    "Instance Name,XTRA,SPAC\n"
    '"Narrow, Thin",94.0,10.0\n'
    "Tall,50.0,\n"
    "Ultra Wide Thin 144,2895.4,-15.0\n"
)
PARSED = {"Narrow, Thin": 10.0, "Ultra Wide Thin 144": -15.0}


def _write(tmp_path, text=CSV_TEXT, bom=False):
    p = tmp_path / "F-avar.csv"
    p.write_text(("\ufeff" if bom else "") + text, encoding="utf-8")
    return p


def test_quoted_comma_name_and_blank_cell(tmp_path):
    # The M7 case: the old Rust reader split on ',' and lost quoted
    # comma names; csv_io reads them. A blank SPAC cell pins nothing.
    assert csv_io.read_csv_spac_overrides(_write(tmp_path)) == PARSED


def test_bom_missing_and_empty_file(tmp_path):
    assert csv_io.read_csv_spac_overrides(_write(tmp_path, bom=True)) == PARSED
    assert csv_io.read_csv_spac_overrides(tmp_path / "absent-avar.csv") == {}
    empty = tmp_path / "empty-avar.csv"
    empty.write_text("")
    assert csv_io.read_csv_spac_overrides(empty) == {}


def test_bad_cell_keeps_the_rows_read_so_far(tmp_path):
    # The long-standing reference semantics (a blanket except ValueError
    # around the whole read): a non-numeric cell ends the parse; the
    # rows before it survive.
    csv_path = _write(tmp_path, "Instance Name,SPAC\nGood,5\nBad,oops\nLater,9\n")
    assert csv_io.read_csv_spac_overrides(csv_path) == {"Good": 5.0}


def test_ragged_row_is_tolerated(tmp_path):
    # A hand-edited row with an unquoted comma has extra cells; they
    # have no column and are dropped, never crash the reader. The
    # shifted "SPAC" cell is then non-numeric, so the bad-cell rule
    # ends the parse — rows before the ragged one survive.
    csv_path = _write(tmp_path, "Instance Name,SPAC\nGood,5\nNarrow, Thin,10\nPlain,7\n")
    assert csv_io.read_csv_spac_overrides(csv_path) == {"Good": 5.0}
    rows, _fieldnames = csv_io.read_csv_mappings(csv_path)
    assert rows[1]["Instance Name"] == "Narrow"  # extra cell dropped


def test_native_payload_carries_the_parse(tmp_path):
    _write(tmp_path)
    source = tmp_path / "F.glyphs"
    source.write_text("")  # only its sibling CSV is read
    overrides = _instance_spac_overrides(source)
    assert overrides == PARSED
    payload = json.loads(wa._native_payload(-20, 40, 1.0, 1.25, overrides))
    entry = payload[0]
    assert entry["type"] == "spac_widthaware" and entry["enabled"] is True
    assert entry["params"]["min"] == -20 and entry["params"]["max"] == 40
    assert entry["params"]["instance_spac"] == PARSED


def test_native_payload_omits_empty_overrides():
    payload = json.loads(wa._native_payload(-20, 40, 1.0, 1.25, {}))
    assert "instance_spac" not in payload[0]["params"]
