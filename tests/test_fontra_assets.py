"""T2.4: the Fontra shim, its focus stylesheet and the Glyphs.app
AppleScripts ship as package files; the proxy's HTML rewrite is
byte-identical to what the inline strings produced (goldens captured from
the pre-move code).

T3.4: the injected shim is the SHARED implementation built from
fontra-embed (fontra/embed-shim.js — a build artifact with a provenance
header) when present, and the desktop-only fontra/shim.js otherwise.
"""
from pathlib import Path
from string import Template

import avar2_studio.server as server
from avar2_studio import state
from avar2_studio.routes import fontra

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PKG = Path(server.__file__).resolve().parent

SHIM_OPEN = '\n<script id="avar2-studio-fontra-shim">\n'
SHIM_CLOSE = '\n</script>\n'


def test_rewrite_output_is_byte_identical_to_the_golden(monkeypatch):
    sample = (FIXTURES / "fontra_rewrite_input.html").read_bytes()
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION",
                        {"studio": True, "tag": "YMOD", "axis_name": "Horizontal correction",
                         "axis_default": 100, "glyph": "e"})
    assert fontra._rewrite_html_paths(sample) == (FIXTURES / "fontra_rewrite_studio.html").read_bytes()
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", None)
    assert fontra._rewrite_html_paths(sample) == (FIXTURES / "fontra_rewrite_plain.html").read_bytes()


def test_assets_are_package_files():
    for rel in ("fontra/embed-shim.js", "fontra/shim.js", "fontra/focus.css",
                "glyphsapp/document_modified.applescript", "glyphsapp/reload_document.applescript"):
        assert (PKG / rel).is_file(), rel
        assert (PKG / rel).stat().st_size > 100, rel
    for rel in ("glyphsapp/document_modified.applescript", "glyphsapp/reload_document.applescript"):
        script = Template(server._read_asset(rel)).substitute(abs_path="/tmp/F.glyphs")
        assert 'POSIX file "/tmp/F.glyphs"' in script and "$" not in script


def test_studio_shim_script_prefers_the_shared_build():
    embed_shim = (PKG / "fontra/embed-shim.js").read_text(encoding="utf-8")
    assert fontra._studio_shim_script() == SHIM_OPEN + embed_shim + SHIM_CLOSE


def test_studio_shim_script_falls_back_to_shim_js(monkeypatch):
    """When the built copy is absent the desktop-only shim.js is injected."""
    real_read_asset = server._read_asset

    def without_embed_shim(relative):
        if relative == "fontra/embed-shim.js":
            raise FileNotFoundError(relative)
        return real_read_asset(relative)

    monkeypatch.setattr(server, "_read_asset", without_embed_shim)
    legacy = (PKG / "fontra/shim.js").read_text(encoding="utf-8")
    assert fontra._studio_shim_script() == SHIM_OPEN + legacy + SHIM_CLOSE
    # The full rewrite path falls back too: no provenance header, legacy body.
    monkeypatch.setattr(state, "FONTRA_EDITOR_SESSION", None)
    out = fontra._rewrite_html_paths((FIXTURES / "fontra_rewrite_input.html").read_bytes())
    assert b"Built from fontra-embed" not in out
    assert b"avar2-studio shim:" in out  # shim.js's own console.warn trail


def test_embed_shim_carries_its_provenance_header():
    header = (PKG / "fontra/embed-shim.js").read_text(encoding="utf-8").splitlines()[:6]
    assert header[0].startswith("// avar2-studio Fontra shim")
    assert any("fontra-embed @ " in line for line in header), header
