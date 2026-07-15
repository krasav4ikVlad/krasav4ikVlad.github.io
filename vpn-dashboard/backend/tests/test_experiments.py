"""Tests for the z-test used in A/B comparisons."""

import math

from app.routers.experiments import two_proportion_z


class TestTwoProportionZ:
    def test_clear_difference_is_significant(self):
        # 300/1000 vs 200/1000 — an obviously real difference
        z, p = two_proportion_z(300, 1000, 200, 1000)
        assert z > 4
        assert p < 0.001

    def test_identical_proportions(self):
        z, p = two_proportion_z(100, 1000, 100, 1000)
        assert z == 0
        assert p == 1.0

    def test_small_samples_not_significant(self):
        z, p = two_proportion_z(3, 10, 2, 10)
        assert p > 0.05

    def test_direction_sign(self):
        z_up, _ = two_proportion_z(300, 1000, 200, 1000)
        z_down, _ = two_proportion_z(200, 1000, 300, 1000)
        assert z_up > 0 > z_down
        assert math.isclose(z_up, -z_down)

    def test_empty_groups(self):
        assert two_proportion_z(0, 0, 5, 10) is None
        assert two_proportion_z(5, 10, 0, 0) is None

    def test_zero_variance(self):
        # everyone converted in both groups → pooled p = 1 → se = 0
        assert two_proportion_z(10, 10, 20, 20) is None

    def test_p_value_matches_known_value(self):
        # p1=0.5 n=100 vs p2=0.36 n=100 → z ≈ 2.0, p ≈ 0.046
        z, p = two_proportion_z(50, 100, 36, 100)
        assert 1.9 < z < 2.1
        assert 0.03 < p < 0.06
