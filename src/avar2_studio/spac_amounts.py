"""The per-side SPAC amount, in integers, for BOTH renderers.

The exported twins and the studio's injector must agree to the unit, and they
only can if they compute the same integer. The injector used to hand FLOAT
corner amounts to ``VariationModel.getDeltas`` and round each tuple delta on its
own, so at a master the font gave Σ round(Δᵢ) while the export wrote
``round(amount)`` — a 1 u error per side, 2 u on the advance, interpolated into
every row (verifier, 2026-11-06 03:15: at the masters the twin UFO and fontmake
agree exactly and the studio is 2 u off).

So: one function, rounding at the same place, and the same integer floor on the
tight end.
"""

from __future__ import annotations

from typing import Sequence


def fit_dx(dx: int, advance: float, composite: bool) -> int:
    """Cap a NEGATIVE move so the advance cannot go below 1 u.

    A UFO/designspace source cannot carry a negative advance width, and
    fontTools refuses to compile one. Crispy's hairline masters are narrower
    than the amount at SPAC −100 (hairline `n` advance 166 u against a 258 u
    tightening), so the exact amount is not representable there and both
    renderers cap it instead of writing a font a compiler rejects. Everywhere
    the design can carry the amount, the value is exact.
    """
    if dx >= 0:
        return dx
    if composite:                       # advance moves by dx
        lowest = 1 - int(advance)
    else:                               # advance moves by 2·dx
        lowest = -((int(advance) - 1) // 2)
    return max(dx, lowest)


def reference_ink(master_means: Sequence[float]) -> float:
    """What a SPAC unit is measured against: the masters' average width.

    The average of each designed master's mean ink. It must not depend on where
    the font rests: it used to be the mean ink at the default, so moving Crispy's
    default from the 400 row (1,754.5 u) to the hairline (92 u) made every stored
    SPAC value about 19 times stronger (user report and decision, 2026-11-07).
    Transform-derived masters are left out: a sheared box is not a width.
    """
    return sum(master_means) / len(master_means)


def per_side(value: float, ink: float, mean_here: float, reference: float,
             bias: float, scale: float, advance: float,
             composite: bool) -> int:
    """One corner's per-side amount, as the integer both renderers apply.

    ``value`` is the SPAC coordinate (lo or hi), ``ink`` the glyph's ink at this
    corner, ``mean_here`` that corner's mean ink and ``reference`` the font's
    `reference_ink` — the amount scales with each master's own width (E2i-c).
    """
    if mean_here <= 0 or reference <= 0:
        return 0
    amount = (value * ((ink / mean_here) ** bias)
              * (mean_here / reference) * scale)
    dx = round(amount)
    return fit_dx(dx, advance, composite) if dx < 0 else dx
