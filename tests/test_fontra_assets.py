"""T2.4: the Fontra shim, its focus stylesheet and the Glyphs.app
AppleScripts ship as package files; the proxy's HTML rewrite is
byte-identical to what the inline strings produced (goldens captured from
the pre-move code)."""
from pathlib import Path
from string import Template

import avar2_studio.server as server
from avar2_studio import state

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PKG = Path(server.__file__).resolve().parent


def test_rewrite_output_is_byte_identical_to_the_golden(monkeypatch):
    sample = (FIXTURES / "fontra_rewrite_input.html").read_bytes()
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION",
                        {"studio": True, "tag": "YMOD", "axis_name": "Horizontal correction",
                         "axis_default": 100, "glyph": "e"})
    assert server._rewrite_html_paths(sample) == (FIXTURES / "fontra_rewrite_studio.html").read_bytes()
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", None)
    assert server._rewrite_html_paths(sample) == (FIXTURES / "fontra_rewrite_plain.html").read_bytes()


def test_assets_are_package_files():
    for rel in ("fontra/shim.js", "fontra/focus.css",
                "glyphsapp/document_modified.applescript", "glyphsapp/reload_document.applescript"):
        assert (PKG / rel).is_file(), rel
        assert (PKG / rel).stat().st_size > 100, rel
    assert server._FONTRA_STUDIO_SHIM_JS == (
        '\n<script id="avar2-studio-fontra-shim">\n'
        + (PKG / "fontra/shim.js").read_text(encoding="utf-8")
        + '\n</script>\n'
    )
    for rel in ("glyphsapp/document_modified.applescript", "glyphsapp/reload_document.applescript"):
        script = Template(server._read_asset(rel)).substitute(abs_path="/tmp/F.glyphs")
        assert 'POSIX file "/tmp/F.glyphs"' in script and "$" not in script
