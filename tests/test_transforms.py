"""Transform registry: discovery, the injected-axis conflict guard, and
sidecar persistence.

Two built-ins (``spac`` and ``spac_widthaware``) both inject a ``SPAC``
fvar axis; enabling both would compile a font with two SPAC axes, so
``validate`` enforces at most one enabled injector per tag. Validation
is deliberately split from persistence: ``validate`` dry-runs the same
checks without touching disk (config import relies on that), while
``set_active`` validates first and only then writes the
``<stem>-transforms.json`` sidecar.
"""

from __future__ import annotations

import pytest

from avar2_studio.transforms import config as tx_config
from avar2_studio.transforms import registry


def test_discover_populates_builtins_including_two_spac_injectors():
    """discover() registers the five built-ins; exactly two inject SPAC."""
    registry.discover()  # idempotent; conftest already isolated the user dir

    assert {"spac", "spac_widthaware", "fix_instances", "gen_stat", "fix_unhinted"} <= set(
        registry.REGISTRY
    )
    spac_injectors = [
        tid
        for tid, t in registry.REGISTRY.items()
        if t.spec.injected_axis_tag == "SPAC"
    ]
    assert sorted(spac_injectors) == ["spac", "spac_widthaware"]


def test_validate_rejects_two_enabled_spac_injectors():
    """Two enabled transforms adding the same fvar axis → ValueError."""
    entries = [
        {"type": "spac", "enabled": True, "params": {"min": -20, "max": 40}},
        {"type": "spac_widthaware", "enabled": True, "params": {"min": -20, "max": 40}},
    ]
    with pytest.raises(ValueError, match="Only one transform can add the SPAC axis"):
        registry.validate(entries)


def test_validate_allows_one_enabled_one_disabled_spac_injector():
    """The conflict guard only looks at ENABLED transforms."""
    entries = [
        {"type": "spac", "enabled": True, "params": {"min": -20, "max": 40}},
        {"type": "spac_widthaware", "enabled": False, "params": {"min": -20, "max": 40}},
    ]
    cleaned = registry.validate(entries)

    assert [e["type"] for e in cleaned] == ["spac", "spac_widthaware"]
    assert [e["enabled"] for e in cleaned] == [True, False]


def test_validate_rejects_inverted_min_max_only_when_enabled():
    """spac with min >= max raises on an enabled entry; a disabled one
    is coerced but never cross-validated (it can't break a build it
    doesn't run in)."""
    bad = [{"type": "spac", "enabled": True, "params": {"min": 40, "max": -20}}]
    with pytest.raises(ValueError, match=r"SPAC min \(40\) must be less than max \(-20\)"):
        registry.validate(bad)

    disabled = [{"type": "spac", "enabled": False, "params": {"min": 40, "max": -20}}]
    cleaned = registry.validate(disabled)
    assert cleaned[0]["params"] == {"min": 40, "max": -20}


def test_validate_drops_unknown_transform_types_silently():
    """A saved-but-uninstalled transform is dropped, not an error."""
    cleaned = registry.validate(
        [
            {"type": "ghost", "enabled": True, "params": {}},
            {"type": "spac", "enabled": False, "params": {}},
        ]
    )
    assert cleaned == [
        {"type": "spac", "enabled": False, "params": {"min": -20, "max": 40}}
    ]


def test_validate_never_persists(crispy_source):
    """validate() is the dry-run path: no -transforms.json may appear."""
    registry.validate([{"type": "spac", "enabled": True, "params": {"min": -20, "max": 40}}])
    assert not tx_config.sidecar_path_for(crispy_source).exists()


def test_set_active_persists_cleaned_entries(crispy_source):
    """set_active writes the sidecar and returns the merged UI list;
    param values are coerced against the schema on the way in."""
    available = registry.set_active(
        crispy_source,
        [{"type": "spac", "enabled": True, "params": {"min": "-20", "max": 40}}],
    )

    sidecar = tx_config.sidecar_path_for(crispy_source)
    assert sidecar.exists()
    assert tx_config.entries(crispy_source) == [
        {"type": "spac", "enabled": True, "params": {"min": -20, "max": 40}}
    ]
    by_id = {d["id"]: d for d in available}
    assert set(by_id) >= {"spac", "spac_widthaware", "fix_instances", "gen_stat", "fix_unhinted"}
    assert by_id["spac"]["enabled"] is True
    assert by_id["spac_widthaware"]["enabled"] is False


def test_set_active_conflict_raises_without_writing(crispy_source):
    """Validation runs BEFORE persistence, so the doomed config leaves
    no sidecar behind."""
    with pytest.raises(ValueError, match="Only one transform can add the SPAC axis"):
        registry.set_active(
            crispy_source,
            [
                {"type": "spac", "enabled": True, "params": {"min": -20, "max": 40}},
                {"type": "spac_widthaware", "enabled": True, "params": {"min": -20, "max": 40}},
            ],
        )
    assert not tx_config.sidecar_path_for(crispy_source).exists()
