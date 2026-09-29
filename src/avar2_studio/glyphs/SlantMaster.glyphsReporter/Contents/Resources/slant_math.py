# Copyright 2026 Agyei Archer. Licensed under the Apache License, Version 2.0.
# Origin: GlyphAudit/slant.py in agyeiagyeiagyei/docrepairtools (same author,
# inspired by filipenegrao's slant_glyphs.py, Apache-2.0), extracted for
# avar2-studio's Slant Master tool; the spacing contracts come from the
# Width Matcher tool beside it.
"""Slant/shear math, reference-width lookup and spacing contracts.

Deliberately free of Glyphs.app / vanilla / AppKit imports so it can be
unit-tested outside Glyphs — ``plugin.py`` adapts these helpers to live
GSLayer objects.
"""

from __future__ import annotations

import math
from typing import Mapping, Optional, Tuple

# Pivot choices offered by the panel's Origin popup.
PIVOT_BASELINE = "baseline"
PIVOT_XHEIGHT = "x-height ÷ 2"
PIVOT_CAPHEIGHT = "cap-height ÷ 2"
PIVOT_CHOICES = (PIVOT_BASELINE, PIVOT_XHEIGHT, PIVOT_CAPHEIGHT)

# Spacing contracts for the new master (Width Matcher's, verbatim). Mode 0
# pastes the reference's sidebearings; the rest pin the ADVANCE to the
# reference's (plus an offset) and let the sidebearings land wherever the
# slanted ink requires — same width, different spacing.
SPACING_REF_SB = 0
SPACING_ADV_PROPORTIONAL = 1
SPACING_ADV_CENTRED = 2
SPACING_ADV_KEEP_LSB = 3
SPACING_MODES = [
    "Reference sidebearings",
    "Reference advance - proportional",
    "Reference advance - centred",
    "Reference advance - keep LSB",
]

# Suffixes in glyph names that map to OpenType feature tags — the variants
# a reference can supply through their base glyph (``a.smcp`` → ``a``).
FEATURE_SUFFIXES = {
    "smcp": "smcp", "c2sc": "c2sc",
    "ss01": "ss01", "ss02": "ss02", "ss03": "ss03", "ss04": "ss04",
    "ss05": "ss05", "ss06": "ss06", "ss07": "ss07", "ss08": "ss08",
    "ss09": "ss09", "ss10": "ss10",
    "onum": "onum", "pnum": "pnum", "lnum": "lnum", "tnum": "tnum",
    "numr": "numr", "dnom": "dnom", "sinf": "sinf", "sups": "sups",
    "ordn": "ordn", "salt": "salt", "frac": "frac",
    "frac0": "frac",  # Glyphs convention for the bottom-pos numerator
    "osf": "onum",    # oldstyle figures (alternate spelling)
    "tosf": "onum",   # tabular oldstyle figures (alternate spelling)
}


def shear_transform(
    angle_deg: float,
    width_pct: float = 100.0,
    height_pct: float = 100.0,
    origin_y: float = 0.0,
) -> Tuple[float, float, float, float, float, float]:
    """6-tuple affine for `GSLayer.applyTransform`: (m11, m12, m21, m22, tX, tY).

        x' = m11·x + m21·y + tX
        y' = m12·x + m22·y + tY

    Slanting is a horizontal shear: m21 = tan(angle) (NOT radians(angle) —
    the skew factor is a ratio, not an angle). Positive angle leans right,
    the italic direction.

    `origin_y` is the vertical pivot: the shear acts on (y − origin_y), so
    points at that height keep their x position. Slanting around the
    baseline (origin_y=0) shifts whole glyphs sideways; pivoting around
    half x-height keeps stems visually centered.

    width_pct / height_pct scale x / y around the coordinate origin,
    composed with the shear: x' = sx·x + tan·sy·(y − origin_y), y' = sy·y.
    """
    tan = math.tan(math.radians(angle_deg))
    sx = width_pct / 100.0
    sy = height_pct / 100.0
    return (
        sx,
        0.0,
        tan * sy,
        sy,
        -tan * sy * origin_y,
        0.0,
    )


def transform_point(matrix, x: float, y: float) -> Tuple[float, float]:
    return (
        matrix[0] * x + matrix[2] * y + matrix[4],
        matrix[1] * x + matrix[3] * y + matrix[5],
    )


def pivot_y(choice: str, x_height: float, cap_height: float) -> float:
    """Resolve an Origin-popup choice to a font-unit y value."""
    if choice == PIVOT_XHEIGHT:
        return x_height / 2.0
    if choice == PIVOT_CAPHEIGHT:
        return cap_height / 2.0
    return 0.0


def parse_variant_suffix(glyph_name: str) -> Optional[Tuple[str, str]]:
    """Split 'a.smcp' or 'I-cy.ss01' into (base_name, feature_tag).

    Returns None if the trailing suffix is not a recognised feature.
    Multi-suffix forms ('zero.osf.slash') walk inwards: returns the
    deepest known feature.
    """
    parts = glyph_name.split(".")
    if len(parts) < 2:
        return None
    # Walk from the end inward, stripping unknown suffixes (e.g. .case, .alt)
    # until we find a known feature.
    for i in range(len(parts) - 1, 0, -1):
        suffix = parts[i]
        if suffix in FEATURE_SUFFIXES:
            base = ".".join(parts[:i])
            return (base, FEATURE_SUFFIXES[suffix])
    return None


def reference_name(names, glyph_name: str) -> Optional[str]:
    """The name the reference supplies for `glyph_name`: a direct hit,
    else the base of a recognised variant suffix (`a.smcp` → `a`)."""
    if glyph_name in names:
        return glyph_name
    parsed = parse_variant_suffix(glyph_name)
    if parsed and parsed[0] in names:
        return parsed[0]
    return None


def ref_advance(advances: Mapping[str, float], glyph_name: str) -> Optional[float]:
    """Reference advance width for `glyph_name`, or None if unmatched.

    Direct name lookup first; on a miss, strip a recognised variant suffix
    (`a.smcp` → `a`) and try the base glyph.
    """
    name = reference_name(advances, glyph_name)
    return None if name is None else advances[name]


def scale_advance(value: float, from_upm: int, to_upm: int) -> int:
    """Scale an advance from the reference's UPM into target font units.

    Same-UPM is a rounded passthrough; cross-UPM scales linearly.
    """
    if from_upm == to_upm:
        return int(round(value))
    return int(round(value * to_upm / from_upm))


def target_spacing(mode: int, ref_lsb: float, ref_rsb: float, ref_adv: float,
                   ink_w: float, offset: float = 0.0) -> Tuple[float, float, float]:
    """(lsb, rsb, advance) the new layer should end up with.

    ``SPACING_REF_SB`` pastes the reference's sidebearings, so the advance
    only matches when the ink does. Every other mode pins the advance to
    the reference's (plus ``offset``) and derives the sidebearings from
    whatever ink the slanted layer actually has: the slack left over is
    distributed in the reference's own LSB:RSB proportion, evenly, or
    entirely onto the right (keep LSB).
    """
    if mode == SPACING_REF_SB:
        return (ref_lsb, ref_rsb, ref_lsb + ink_w + ref_rsb)
    target_adv = ref_adv + offset
    slack = target_adv - ink_w
    if mode == SPACING_ADV_PROPORTIONAL:
        total = ref_lsb + ref_rsb
        # A zero total (full-bleed glyph) has no ratio to preserve — fall
        # back to an even split rather than dividing by zero.
        lsb = slack * (ref_lsb / total) if abs(total) > 1e-6 else slack / 2.0
    elif mode == SPACING_ADV_CENTRED:
        lsb = slack / 2.0
    else:
        lsb = ref_lsb
    return (lsb, slack - lsb, target_adv)


def empty_advance(mode: int, ref_adv: float, offset: float = 0.0) -> float:
    """Advance for a glyph with no ink (space etc.)."""
    return ref_adv if mode == SPACING_REF_SB else ref_adv + offset
