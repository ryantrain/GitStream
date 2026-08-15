"""Tests for the shared statistical helpers.

These functions back every duration figure the product reports, so their edge
cases are worth pinning down directly.
"""

from app.services.stats import (
    MIN_RELIABLE_SAMPLE,
    is_reliable_sample,
    percentile,
    robust_center,
    trimmed_mean,
)


class TestPercentile:
    def test_interpolates_between_order_statistics(self) -> None:
        # Nearest-rank would return 48 (the maximum) for p75 here, making p75,
        # p90 and p100 indistinguishable on a three-value sample.
        values = [10.0, 24.0, 48.0]
        assert percentile(values, 0.5) == 24.0
        assert percentile(values, 0.75) == 36.0
        assert percentile(values, 0.9) == 43.2

    def test_p90_is_not_the_maximum_on_small_samples(self) -> None:
        """The specific defect: for n <= 10, nearest-rank collapsed p90 onto the
        single worst observation."""
        values = [float(i) for i in range(1, 9)]  # 1..8
        assert percentile(values, 0.9) < max(values)

    def test_does_not_require_sorted_input(self) -> None:
        assert percentile([48.0, 10.0, 24.0], 0.5) == 24.0

    def test_bounds(self) -> None:
        values = [5.0, 10.0]
        assert percentile(values, 0.0) == 5.0
        assert percentile(values, 1.0) == 10.0

    def test_empty_and_single(self) -> None:
        assert percentile([], 0.5) == 0.0
        assert percentile([7.0], 0.9) == 7.0


class TestTrimmedMean:
    def test_trims_at_least_one_tail_value_on_modest_samples(self) -> None:
        """int(n * 0.05) rounds to zero for every sample under 20, so the
        "trimmed" mean used to be a plain mean exactly where outliers hurt most."""
        values = [float(v) for v in range(10, 21)] + [5000.0]  # 12 values

        result = trimmed_mean(values)

        assert result == 15.5
        # Sanity: the untrimmed mean is wildly contaminated.
        assert sum(values) / len(values) > 400

    def test_matches_mean_when_sample_is_too_small_to_trim(self) -> None:
        values = [10.0, 20.0, 30.0]
        assert trimmed_mean(values) == 20.0

    def test_symmetric_data_is_unchanged(self) -> None:
        values = [float(v) for v in range(1, 101)]
        assert abs(trimmed_mean(values) - 50.5) < 0.01

    def test_empty(self) -> None:
        assert trimmed_mean([]) == 0.0


class TestRobustCenter:
    def test_ignores_outliers(self) -> None:
        assert robust_center([10.0, 11.0, 12.0, 5000.0]) == 11.5

    def test_empty(self) -> None:
        assert robust_center([]) == 0.0


class TestIsReliableSample:
    def test_threshold(self) -> None:
        assert not is_reliable_sample(MIN_RELIABLE_SAMPLE - 1)
        assert is_reliable_sample(MIN_RELIABLE_SAMPLE)
