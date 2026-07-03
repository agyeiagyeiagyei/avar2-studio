"""Monkeypatch for fontra-glyphs so studio-authored control-axis brace
layers stay editable in Fontra.

fontra-glyphs's ``fixSourceLocations`` is *documented* to drop a
font-axis coordinate from a glyph's sources only when it co-varies with
a glyph-local **smart** axis:

    # If a set of sources is equally controlled by a font axis and a
    # glyph axis (smart axis), then the font axis should be ignored.

But the shipped code drops font-axis coordinates whenever two or more
coordinate items are shared by the same set of sources — *even when no
smart axis is involved*. For a glyph-scoped control axis defined purely
by brace layers (no smart axis), a brace that moves only the control
axis also re-carries the default-valued parametric coordinates
(fontra-glyphs doesn't sparsify brace locations). Those pair up with the
control-axis coordinate as a "group" unique to that one source, and the
original code strips them all — including the control-axis value. The
brace then collapses onto a master location, so Fontra shows it as a
non-editable *background* layer and reports "locations must be unique".

This patch restores the documented intent: only strip a group's
font-axis coordinates when the group actually contains a smart axis.
It's applied inside the Fontra subprocess the studio spawns (see
``_fontra_launch``); the studio's own font build never goes through
fontra-glyphs, so the compiled output is unaffected.
"""

from collections import defaultdict


def _fixSourceLocations(sources, smartAxisNames):
    smartAxisNames = set(smartAxisNames)
    sets = defaultdict(set)
    for i, source in enumerate(sources):
        for locItem in source.location.items():
            sets[locItem].add(i)

    reverseSets = defaultdict(set)
    for locItem, sourceIndices in sets.items():
        reverseSets[tuple(sorted(sourceIndices))].add(locItem)

    matches = [locItems for locItems in reverseSets.values() if len(locItems) > 1]

    locItemsToDelete = []
    for locItems in matches:
        axesInGroup = {axis for axis, _value in locItems}
        # The guard the upstream code is missing: only dedup a group
        # when a smart (glyph-local) axis is actually part of it — the
        # documented purpose. Without this, legitimately-unique
        # control-axis coordinates get stripped.
        if not (axesInGroup & smartAxisNames):
            continue
        for axis, value in locItems:
            if axis not in smartAxisNames:
                locItemsToDelete.append((axis, value))

    for axis, value in locItemsToDelete:
        for source in sources:
            if source.location.get(axis) == value:
                del source.location[axis]


def apply() -> bool:
    """Install the patch. Returns True if it took effect."""
    try:
        import fontra_glyphs.backend as backend
    except Exception:
        return False
    backend.fixSourceLocations = _fixSourceLocations
    return True


# Apply on import so ``import avar2_studio._fontra_patch`` is enough.
apply()
