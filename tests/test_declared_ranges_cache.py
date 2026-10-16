"""T1.8: _declared_axis_ranges computes once per input version.

A single build consults it from three sites (the shim env, the default-row
detection, the fvar-axes fix); each used to re-read axis-metadata.json and
re-parse the source. The memo keys on the inputs' on-disk versions.
"""
import avar2_studio.server as server

META = {"WGHT": {"registered_tag": "wght", "min": 100, "max": 900,
                 "default": 400, "is_parametric": False}}


def test_declared_axis_ranges_memoized_per_input_version(monkeypatch, tmp_path):
    meta = tmp_path / "axis-metadata.json"
    meta.write_text("{}")
    src = tmp_path / "F.glyphs"
    src.write_text("familyName = F;")
    monkeypatch.setattr(server, "_get_avar2_metadata_path", lambda: meta)
    monkeypatch.setattr(server, "GLYPHS_PATH", src)
    calls = []
    monkeypatch.setattr(server, "_load_axis_metadata",
                        lambda: calls.append(1) or dict(META))
    server._DECLARED_RANGES_CACHE.clear()
    try:
        a = server._declared_axis_ranges()
        b = server._declared_axis_ranges()
        assert len(calls) == 1, f"expected one computation, got {len(calls)}"
        assert a == {"wght": [100.0, 400.0, 900.0]}
        assert a == b
        assert a is not b, "callers get their own copy"

        meta.write_text('{"edited": true}')   # mtime+size change → recompute
        server._declared_axis_ranges()
        assert len(calls) == 2

        server._declared_axis_ranges({"wght": 700})   # overrides key separately
        assert len(calls) == 3
    finally:
        server._DECLARED_RANGES_CACHE.clear()
