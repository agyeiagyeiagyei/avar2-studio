"""Unit tests for Slant Master's slant_math — shear math, reference-width
lookup and the spacing contracts. Ported from docrepairtools/tests/
test_slant.py; the module is loaded by path from the plugin bundle."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

MODULE = (Path(__file__).resolve().parents[1] / "src" / "avar2_studio" / "glyphs"
          / "SlantMaster.glyphsReporter" / "Contents" / "Resources" / "slant_math.py")
spec = importlib.util.spec_from_file_location("slant_math", MODULE)
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)


class TestShearTransform:
    def test_zero_angle_is_plain_scale(self):
        assert sm.shear_transform(0.0) == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

    def test_shear_factor_is_tan_not_radians(self):
        assert sm.shear_transform(10.0)[2] == pytest.approx(math.tan(math.radians(10.0)))

    def test_positive_angle_leans_right(self):
        assert sm.shear_transform(12.0)[2] > 0

    def test_pivot_keeps_x_at_origin_height(self):
        origin = 250.0
        m = sm.shear_transform(10.0, origin_y=origin)
        assert sm.transform_point(m, 100.0, origin)[0] == pytest.approx(100.0)

    def test_baseline_pivot_shifts_top_right(self):
        m = sm.shear_transform(10.0, origin_y=0.0)
        x_new = sm.transform_point(m, 100.0, 500.0)[0]
        assert x_new == pytest.approx(100.0 + math.tan(math.radians(10.0)) * 500.0)

    def test_width_and_height_scaling(self):
        m = sm.shear_transform(0.0, width_pct=80.0, height_pct=110.0)
        assert m[0] == pytest.approx(0.8)
        assert m[3] == pytest.approx(1.1)
        m = sm.shear_transform(10.0, height_pct=110.0)
        assert m[2] == pytest.approx(math.tan(math.radians(10.0)) * 1.1)

    def test_pivot_compensation_scales_with_height(self):
        origin = 250.0
        m = sm.shear_transform(10.0, height_pct=110.0, origin_y=origin)
        assert sm.transform_point(m, 100.0, origin)[0] == pytest.approx(100.0)


class TestPivotY:
    def test_baseline(self):
        assert sm.pivot_y(sm.PIVOT_BASELINE, 500, 700) == 0.0

    def test_xheight_half(self):
        assert sm.pivot_y(sm.PIVOT_XHEIGHT, 500, 700) == 250.0

    def test_capheight_half(self):
        assert sm.pivot_y(sm.PIVOT_CAPHEIGHT, 500, 700) == 350.0

    def test_unknown_choice_falls_back_to_baseline(self):
        assert sm.pivot_y("something-else", 500, 700) == 0.0


class TestReferenceLookup:
    def test_direct_hit(self):
        assert sm.ref_advance({"a": 500}, "a") == 500

    def test_variant_suffix_falls_back_to_base(self):
        assert sm.ref_advance({"a": 500}, "a.smcp") == 500
        assert sm.reference_name({"a": 500}, "a.smcp") == "a"

    def test_miss_returns_none(self):
        assert sm.ref_advance({"a": 500}, "b") is None

    def test_unknown_suffix_not_stripped(self):
        # `.alt` is not a recognised feature suffix — no base fallback.
        assert sm.ref_advance({"a": 500}, "a.alt") is None

    def test_multi_suffix_walks_inward(self):
        assert sm.parse_variant_suffix("zero.osf.slash") == ("zero", "onum")


class TestScaleAdvance:
    def test_same_upm_passthrough(self):
        assert sm.scale_advance(500.4, 1000, 1000) == 500

    def test_scales_down(self):
        assert sm.scale_advance(2048, 2048, 1000) == 1000

    def test_scales_up(self):
        assert sm.scale_advance(500, 1000, 2048) == 1024

    def test_rounds_to_int(self):
        assert sm.scale_advance(501, 2048, 1000) == 245  # 244.628…


class TestSpacingContracts:
    # reference: LSB 60, RSB 40, advance 500; slanted ink 380 wide
    REF = (60.0, 40.0, 500.0)

    def test_reference_sidebearings_paste_them(self):
        lsb, rsb, adv = sm.target_spacing(sm.SPACING_REF_SB, *self.REF, ink_w=380.0)
        assert (lsb, rsb, adv) == (60.0, 40.0, 480.0)

    def test_reference_sidebearings_ignore_the_offset(self):
        assert sm.target_spacing(sm.SPACING_REF_SB, *self.REF, ink_w=380.0, offset=20)[2] == 480.0

    def test_proportional_split_keeps_the_ratio(self):
        lsb, rsb, adv = sm.target_spacing(sm.SPACING_ADV_PROPORTIONAL, *self.REF, ink_w=380.0)
        assert adv == 500.0
        assert lsb == pytest.approx(120 * 0.6)
        assert rsb == pytest.approx(120 * 0.4)

    def test_proportional_with_zero_total_splits_evenly(self):
        lsb, rsb, adv = sm.target_spacing(sm.SPACING_ADV_PROPORTIONAL, 0.0, 0.0, 500.0, ink_w=380.0)
        assert lsb == pytest.approx(60.0) and rsb == pytest.approx(60.0)

    def test_centred(self):
        lsb, rsb, adv = sm.target_spacing(sm.SPACING_ADV_CENTRED, *self.REF, ink_w=380.0)
        assert lsb == pytest.approx(60.0) and rsb == pytest.approx(60.0) and adv == 500.0

    def test_keep_lsb_puts_all_slack_on_the_right(self):
        lsb, rsb, adv = sm.target_spacing(sm.SPACING_ADV_KEEP_LSB, *self.REF, ink_w=380.0)
        assert (lsb, rsb, adv) == (60.0, 60.0, 500.0)

    def test_offset_applies_in_advance_modes(self):
        lsb, rsb, adv = sm.target_spacing(sm.SPACING_ADV_CENTRED, *self.REF, ink_w=380.0, offset=20)
        assert adv == 520.0 and lsb + rsb == pytest.approx(140.0)

    def test_empty_advance(self):
        assert sm.empty_advance(sm.SPACING_REF_SB, 500.0, offset=20) == 500.0
        assert sm.empty_advance(sm.SPACING_ADV_KEEP_LSB, 500.0, offset=20) == 520.0
