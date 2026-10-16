"""T1.31: the fontTools SPAC fallback says so when the native injector is absent."""
import pytest

from avar2_studio.transforms import builtin_spac_widthaware as spac
from avar2_studio.transforms.base import BuildContext


def test_absent_native_injector_logs_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(spac, "_native_injector", lambda: None)
    logs = []
    ctx = BuildContext(build_dir=tmp_path, source_path=tmp_path / "F.glyphs",
                       glyphs_path=tmp_path / "F.glyphs", family="F",
                       log=logs.append)
    dummy_font = tmp_path / "F.ttf"
    dummy_font.write_bytes(b"not a font")   # the fontTools path fails AFTER the log
    with pytest.raises(Exception):
        spac.WidthAwareSpacTransform().apply(dummy_font, {"min": -20, "max": 40}, ctx)
    assert any("native injector not found" in m for m in logs), logs
