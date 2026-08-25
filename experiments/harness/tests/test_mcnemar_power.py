import pytest

import mcnemar_power as mp


class TestExactMcNemar:
    def test_known_value_10_vs_2(self):
        # n=12, smaller tail k=2: 2*(C(12,0)+C(12,1)+C(12,2))/2^12
        expected = 2 * (1 + 12 + 66) / 4096
        p, note = mp.mcnemar_exact(10, 2)
        assert p == pytest.approx(expected)
        assert "12 discordant pairs" in note
        assert "first-arm-only 10" in note
        assert "second-arm-only 2" in note

    def test_symmetric_in_arm_order(self):
        assert mp.mcnemar_exact(10, 2)[0] == mp.mcnemar_exact(2, 10)[0]

    def test_zero_discordant_pairs_has_no_input(self):
        # Reference discipline: p=1.0 would imply evidence of no difference;
        # there is simply no evidence. None + note instead.
        p, note = mp.mcnemar_exact(0, 0)
        assert p is None
        assert "no discordant pairs" in note

    def test_all_pairs_one_direction_is_a_real_test(self):
        # 3-0 split: few pairs, but the binomial still has input.
        p, note = mp.mcnemar_exact(3, 0)
        assert p == pytest.approx(0.25)
        assert "3 discordant pairs" in note

    def test_two_sided_exact_p_known_small_case(self):
        assert mp.two_sided_exact_p(0, 6) == pytest.approx(2 / 64)


class TestRejectionRegionAndPower:
    def test_region_empty_for_tiny_n(self):
        # n<=4 cannot reach alpha=.05 at all — the footer must say so.
        assert mp.rejection_region(4) == set()
        assert mp.power(4, 1.0) == 0.0

    def test_region_contains_extremes_for_n6(self):
        region = mp.rejection_region(6)
        assert 0 in region and 6 in region
        assert 3 not in region

    def test_power_monotone_in_q(self):
        assert mp.power(12, 0.85) > mp.power(12, 0.65) > mp.power(12, 0.5)

    def test_power_zero_when_no_pairs(self):
        assert mp.power(0, 0.9) == 0.0


class TestMinDetectableQ:
    def test_unreachable_reported_as_none(self):
        assert mp.min_detectable_q(4) is None

    def test_boundary_bracketed(self):
        q = mp.min_detectable_q(20)
        assert q is not None and 0.5 < q <= 1.0
        assert mp.power(20, q) >= 0.80
        assert mp.power(20, q - 1e-3) < 0.80

    def test_more_pairs_shrink_mde(self):
        assert mp.min_detectable_q(60) < mp.min_detectable_q(20)


class TestRequiredN:
    def test_easier_effect_needs_fewer_pairs(self):
        assert mp.required_n(0.95) is not None
        assert mp.required_n(0.95) < mp.required_n(0.70)

    def test_result_actually_meets_target(self):
        n = mp.required_n(0.85)
        assert mp.power(n, 0.85) >= 0.80

    def test_degenerate_q_rejected(self):
        assert mp.required_n(0.5) is None


class TestFormatting:
    def test_p_value_travels_with_counts(self):
        line = mp.format_pair("A", "B", 10, 2)
        assert "exact-p=" in line
        assert "discordant pairs" in line
        assert "10" in line and "2" in line
        assert "power@observed-split" in line
        assert "MDE@80%" in line
        assert "n-for-80%" in line

    def test_mde_line_names_pair_budget_when_unreachable(self):
        line = mp.format_pair("A", "B", 2, 2)
        assert "unreachable with 4 discordant pairs" in line
        assert "discordant pairs (first-arm-only 2" in line

    def test_zero_pair_line(self):
        line = mp.format_pair("A", "B", 0, 0)
        assert "p=N/A" in line and "no input" in line

    def test_footer_lists_every_comparison(self):
        text = mp.format_footer([("A", "B", 10, 2), ("B", "C", 0, 0)])
        assert text.count("\n  ") == 2
        assert "POWER-ANALYSIS FOOTER" in text
