"""Shared statistical helpers used by the estimation and metrics services.

Centralised here so that the web estimator, the team-metrics endpoint and the
bottleneck insights all agree on how a percentile or a robust average is
computed. Percentiles use linear interpolation between order statistics
(the same "inclusive" method as ``statistics.quantiles`` and numpy's default),
because the previous nearest-rank implementation collapsed p90 onto the sample
maximum for any sample smaller than about ten values.
"""

from __future__ import annotations

from statistics import median

# Below this many observations a percentile is dominated by individual
# outliers, so callers should present it with a low-confidence caveat.
MIN_RELIABLE_SAMPLE = 20

# Fraction trimmed from each tail when computing a robust mean.
DEFAULT_TRIM_FRACTION = 0.05

# Samples smaller than this are returned untrimmed: removing a value from each
# tail would discard too large a share of the evidence.
MIN_SAMPLE_TO_TRIM = 5


def percentile(values: list[float], q: float) -> float:
    """Return the ``q`` quantile of ``values`` using linear interpolation.

    ``q`` is a fraction in [0, 1]. ``values`` need not be sorted. Returns 0.0
    for an empty input so callers can treat "no data" as a neutral zero.

    Linear interpolation matters on small samples: with 8 observations the old
    nearest-rank method returned the maximum for any q above 0.875, making p90
    indistinguishable from the worst case.
    """
    if not values:
        return 0.0

    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return float(ordered[0])
    if q <= 0:
        return float(ordered[0])
    if q >= 1:
        return float(ordered[-1])

    # Position on a 0..n-1 scale, then blend the two neighbouring values.
    pos = q * (n - 1)
    lower_idx = int(pos)
    upper_idx = min(lower_idx + 1, n - 1)
    weight = pos - lower_idx

    lower = float(ordered[lower_idx])
    upper = float(ordered[upper_idx])
    return lower + (upper - lower) * weight


def trimmed_mean(values: list[float], trim_fraction: float = DEFAULT_TRIM_FRACTION) -> float:
    """Mean of ``values`` after discarding the extreme tails.

    A single pull request left open for six months moves a raw mean by tens of
    hours, which makes the "average" column misleading next to the median.
    Trimming 5% from each end keeps the average comparable while still
    reflecting the bulk of the distribution.

    Falls back to the plain mean when the sample is too small to trim without
    discarding meaningful data.
    """
    if not values:
        return 0.0

    ordered = sorted(values)
    n = len(ordered)

    # Below this, discarding anything throws away a meaningful share of the data.
    if n < MIN_SAMPLE_TO_TRIM:
        return sum(ordered) / n

    # Always trim at least one observation from each tail once the sample can
    # afford it. A plain int(n * 0.05) rounds to zero for every sample under 20,
    # which is exactly where a single abandoned PR does the most damage: with 12
    # observations including one 5,000-hour outlier, the "trimmed" mean was 430h
    # against a median of 15h.
    cut = max(1, int(n * trim_fraction))
    if n - 2 * cut < 1:
        return sum(ordered) / n

    kept = ordered[cut : n - cut]
    return sum(kept) / len(kept)


def robust_center(values: list[float]) -> float:
    """Best single-number summary of a duration distribution.

    Uses the median, which is unaffected by the long right tail that PR
    durations always have.
    """
    if not values:
        return 0.0
    return float(median(values))


def is_reliable_sample(count: int) -> bool:
    """Whether a sample is large enough for percentiles to be meaningful."""
    return count >= MIN_RELIABLE_SAMPLE
