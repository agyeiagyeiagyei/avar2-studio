"""The mtime-keyed source-parse cache (source_font.load_source_cached):
repeat reads share one parse, any on-disk change re-parses, mutations via
load_source stay uncached.
"""

import shutil
from pathlib import Path

from avar2_studio import source_font

CRISPY_MINI = Path(__file__).resolve().parent.parent / "examples" / "crispy-mini" / "sources" / "CrispyMini.glyphs"


def test_repeat_reads_share_one_parse(tmp_path):
    src = tmp_path / "Mini.glyphs"
    shutil.copy2(CRISPY_MINI, src)
    f1, _ = source_font.load_source_cached(src)
    f2, _ = source_font.load_source_cached(src)
    assert f1 is f2, "same file version → same object"


def test_an_on_disk_change_reparses(tmp_path):
    src = tmp_path / "Mini.glyphs"
    shutil.copy2(CRISPY_MINI, src)
    f1, _ = source_font.load_source_cached(src)
    before = len(f1.glyphs)
    # add a glyph on disk: the cache must not serve the stale object
    with src.open("a") as fh:
        fh.write('\n')
    f3, _ = source_font.load_source_cached(src)
    assert f3 is not f1, "mtime change → fresh parse"
    assert len(f3.glyphs) == before


def test_uncached_loads_stay_independent(tmp_path):
    src = tmp_path / "Mini.glyphs"
    shutil.copy2(CRISPY_MINI, src)
    a, _ = source_font.load_source(src)
    b, _ = source_font.load_source(src)
    assert a is not b, "mutating callers get their own object"


def test_designspace_never_caches(roboto_source):
    dsd, _fmt1 = source_font.load_source_cached(roboto_source)
    dsd2, _fmt2 = source_font.load_source_cached(roboto_source)
    assert dsd is not dsd2, "designspace content lives in UFOs — mtime can't see it"
