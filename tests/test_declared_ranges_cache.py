"""T1.8: _declared_axis_ranges computes once per input version.

A single build consults it from three sites (the shim env, the default-row
detection, the fvar-axes fix); each used to re-read axis-metadata.json and
re-parse the source. The memo keys on the inputs' on-disk versions.
"""
import avar2_studio.pipeline as pipeline
from avar2_studio import state

META = {"WGHT": {"registered_tag": "wght", "min": 100, "max": 900,
                 "default": 400, "is_parametric": False}}


def test_declared_axis_ranges_memoized_per_input_version(monkeypatch, tmp_path):
    meta = tmp_path / "axis-metadata.json"
    meta.write_text("{}")
    src = tmp_path / "F.glyphs"
    src.write_text("familyName = F;")
    monkeypatch.setattr(pipeline, "_get_avar2_metadata_path", lambda: meta)
    monkeypatch.setattr(state, "GLYPHS_PATH", src)
    calls = []
    monkeypatch.setattr(pipeline, "_load_axis_metadata",
                        lambda: calls.append(1) or dict(META))
    state._DECLARED_RANGES_CACHE.clear()
    try:
        a = pipeline._declared_axis_ranges()
        b = pipeline._declared_axis_ranges()
        assert len(calls) == 1, f"expected one computation, got {len(calls)}"
        assert a == {"wght": [100.0, 400.0, 900.0]}
        assert a == b
        assert a is not b, "callers get their own copy"

        meta.write_text('{"edited": true}')   # mtime+size change → recompute
        pipeline._declared_axis_ranges()
        assert len(calls) == 2

        pipeline._declared_axis_ranges({"wght": 700})   # overrides key separately
        assert len(calls) == 3
    finally:
        state._DECLARED_RANGES_CACHE.clear()


def test_no_caching_without_a_versioned_source(monkeypatch):
    """Round-3 finding: with no source bound the key collapsed to
    ((None, None), ()) and the first computation leaked into every later
    call (reproduced across test_avar2_default_relocation). Unversioned
    inputs must compute every time."""
    monkeypatch.setattr(state, "GLYPHS_PATH", None)
    monkeypatch.setattr(pipeline, "_get_avar2_metadata_path", lambda: None)
    state._DECLARED_RANGES_CACHE.clear()
    try:
        monkeypatch.setattr(pipeline, "_load_axis_metadata", lambda: dict(META))
        first = pipeline._declared_axis_ranges()
        other = {"OPSZ": {"registered_tag": "opsz", "min": 8, "max": 144,
                          "default": 72, "is_parametric": False}}
        monkeypatch.setattr(pipeline, "_load_axis_metadata", lambda: other)
        second = pipeline._declared_axis_ranges()
        assert first == {"wght": [100.0, 400.0, 900.0]}
        assert second == {"opsz": [8.0, 72.0, 144.0]}, "stale ranges leaked across calls"
        assert not state._DECLARED_RANGES_CACHE, "nothing may be cached under an unversioned key"
    finally:
        state._DECLARED_RANGES_CACHE.clear()
