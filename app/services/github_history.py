"""Estimation statistics derived from a repository's pull request history.

This module is pure calculation: it takes pull request dictionaries (as produced
by ``github_client``) and turns them into duration statistics, review-effort
estimates and calendar-time forecasts. Network access lives in
``app.services.github_client``.

Three things are worth knowing before reading the numbers this produces:

* **Percentiles interpolate.** See ``app.services.stats.percentile``.
* **Risk bands are relative to the repository.** A fixed 12h/36h threshold
  labelled every PR in a slow repository "high", which carries no information.
  Bands are now cut at the repository's own median and p90, and the thresholds
  are returned so the UI can show what they mean.
* **Durations are measured from PR creation.** GitHub's REST payload carries no
  ``ready_for_review`` timestamp, so time spent as a draft is included in
  historical merge durations. Recovering it costs one timeline request per PR,
  which the estimator's request budget cannot absorb; the limitation is
  surfaced to the user rather than hidden. Open drafts *are* identified and can
  be excluded from the active queue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.github_client import GithubHistoryError
from app.services.stats import (
    MIN_RELIABLE_SAMPLE,
    percentile,
    robust_center,
    trimmed_mean,
)

# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

# The REST pulls endpoint cannot sort by merge date, so the sample is ordered by
# last update. A pull request closed two years ago but commented on yesterday
# appears near the top of that ordering and would otherwise contribute its
# (very long) duration to "recent" statistics. Restricting to merges inside this
# window removes that contamination.
DEFAULT_HISTORY_WINDOW_DAYS = 180

# Size buckets in total changed lines. Open PRs are matched to the bucket of
# similarly sized historical PRs to get a calendar-time baseline.
SIZE_BUCKETS: tuple[tuple[str, float], ...] = (
    ("small", 100),
    ("medium", 500),
    ("large", 1500),
    ("xl", float("inf")),
)

# Observations needed before a bucket's own baseline is preferred over the
# repository-wide baseline.
MIN_BUCKET_SAMPLE = 3

# ---------------------------------------------------------------------------
# Review effort model
# ---------------------------------------------------------------------------

# Reading throughput tiers as (span_of_lines, lines_per_hour). Effort is the
# integral over these tiers, so the curve is continuous: the old step function
# jumped 34% between a 200-line and a 201-line diff.
READING_TIERS: tuple[tuple[float, float], ...] = (
    (200, 400.0),  # fresh attention
    (800, 300.0),  # attention degrading
    (4000, 200.0),  # heavy cognitive load
    (float("inf"), 600.0),  # bulk/mechanical territory, scanned rather than read
)

# Deletions are verified rather than understood.
DELETION_SCAN_RATE = 800.0

# Generated and vendored content is skimmed, not read.
GENERATED_SCAN_RATE = 3000.0

# Per-file context switching, and the sub-linear cost of relating files.
FILE_SWITCH_HOURS = 4.0 / 60.0
COMPREHENSION_FREE_FILES = 5
COMPREHENSION_HOURS_PER_FILE = 5.0 / 60.0
COMPREHENSION_EXPONENT = 0.6

# Reading CI, resolving trivia, clicking merge.
MERGE_OVERHEAD_HOURS = 5.0 / 60.0

# Even a one-line change costs a context switch.
MIN_REVIEW_EFFORT_HOURS = 10.0 / 60.0

# Effort bands, in hours of active reviewer time. These are absolute by design:
# they describe a human's attention budget, which does not vary by repository.
EFFORT_BAND_LOW_MAX = 1.0
EFFORT_BAND_MEDIUM_MAX = 4.0

# Filenames and path fragments whose diffs are machine-generated.
GENERATED_FILENAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pipfile.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "gradle.lockfile",
        "mix.lock",
        "flake.lock",
    }
)

GENERATED_PATH_MARKERS: tuple[str, ...] = (
    "/vendor/",
    "/node_modules/",
    "/dist/",
    "/build/",
    "/__snapshots__/",
    "/generated/",
    ".generated.",
    "_pb2.py",
    "_pb2_grpc.py",
    ".pb.go",
    ".min.js",
    ".min.css",
    ".map",
    ".snap",
    ".svg",
)


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def _parse_github_timestamp(value: str | None) -> datetime | None:
    """Parse a GitHub timestamp into an aware UTC datetime.

    Accepts both the ``...Z`` form the REST API returns and the offset form
    (``+00:00``) that GraphQL and some endpoints use. The previous strict
    ``strptime`` pattern raised an uncaught ``ValueError`` on the latter, which
    surfaced as a 500 from the estimate page.
    """
    if not value:
        return None

    text = value.strip()
    if text.endswith(("z", "Z")):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# ---------------------------------------------------------------------------
# Sizing helpers
# ---------------------------------------------------------------------------


def size_bucket_for(total_lines: float) -> str:
    """Return the size bucket name for a total changed-line count."""
    for name, upper in SIZE_BUCKETS:
        if total_lines < upper:
            return name
    return SIZE_BUCKETS[-1][0]


def _is_generated_path(path: str) -> bool:
    """Whether a file path looks machine-generated or vendored."""
    lowered = path.lower()
    basename = lowered.rsplit("/", 1)[-1]

    if basename in GENERATED_FILENAMES:
        return True

    # Normalise so a marker like "/dist/" also matches a leading "dist/".
    padded = f"/{lowered}"
    return any(marker in padded for marker in GENERATED_PATH_MARKERS)


def classify_change(
    additions: int,
    deletions: int,
    files: list[dict[str, Any]] | None,
) -> dict[str, int]:
    """Split a diff into human-reviewable and machine-generated portions.

    When per-file data is unavailable (the unauthenticated REST path) everything
    is treated as reviewable, which is the conservative assumption.
    """
    if not files:
        return {
            "reviewable_additions": additions,
            "reviewable_deletions": deletions,
            "generated_additions": 0,
            "generated_deletions": 0,
            "generated_file_count": 0,
            "reviewable_file_count": 0,
        }

    generated_add = generated_del = generated_files = 0
    reviewable_add = reviewable_del = reviewable_files = 0

    for entry in files:
        path = str(entry.get("path") or "")
        add = int(entry.get("additions") or 0)
        dele = int(entry.get("deletions") or 0)

        if path and _is_generated_path(path):
            generated_add += add
            generated_del += dele
            generated_files += 1
        else:
            reviewable_add += add
            reviewable_del += dele
            reviewable_files += 1

    # GraphQL caps the file list, so totals can exceed what we itemised. Assign
    # the unaccounted remainder to the reviewable side.
    unaccounted_add = max(additions - (generated_add + reviewable_add), 0)
    unaccounted_del = max(deletions - (generated_del + reviewable_del), 0)

    return {
        "reviewable_additions": reviewable_add + unaccounted_add,
        "reviewable_deletions": reviewable_del + unaccounted_del,
        "generated_additions": generated_add,
        "generated_deletions": generated_del,
        "generated_file_count": generated_files,
        "reviewable_file_count": reviewable_files,
    }


def _reading_hours(lines: float) -> float:
    """Integrate the tiered reading-rate curve over ``lines``.

    Continuous and monotone, unlike the previous per-bracket flat rate.
    """
    remaining = max(lines, 0.0)
    hours = 0.0

    for span, rate in READING_TIERS:
        if remaining <= 0:
            break
        take = min(remaining, span)
        hours += take / rate
        remaining -= take

    return hours


def estimate_review_effort_hours(
    additions: int,
    deletions: int,
    changed_files: int,
    files: list[dict[str, Any]] | None = None,
) -> float:
    """Estimate active review effort: how long a reviewer spends on this diff.

    Based on empirical code-review research:

    * Effective review throughput is ~200-400 LOC/hour for net-new logic, and
      degrades as the diff grows. Modelled as a continuous integral over
      declining rate tiers.
    * Deletions are verified rather than understood, so they scan ~2x faster.
    * Generated and vendored content (lockfiles, minified bundles, snapshots) is
      skimmed. Without this, a 5,000-line ``package-lock.json`` diff billed 25
      hours of review effort.
    * Context switching costs a few minutes per file, and relating files to each
      other adds a sub-linear comprehension cost.
    """
    split = classify_change(additions, deletions, files)

    reading_hours = _reading_hours(split["reviewable_additions"])
    deletion_hours = split["reviewable_deletions"] / DELETION_SCAN_RATE
    generated_hours = (split["generated_additions"] + split["generated_deletions"]) / GENERATED_SCAN_RATE

    # Only files a human actually reads incur switching and comprehension cost.
    if split["reviewable_file_count"] or split["generated_file_count"]:
        human_files = split["reviewable_file_count"]
    else:
        human_files = max(changed_files, 0)

    file_switch_hours = max(human_files - 1, 0) * FILE_SWITCH_HOURS

    if human_files > COMPREHENSION_FREE_FILES:
        comprehension_hours = (
            human_files - COMPREHENSION_FREE_FILES
        ) ** COMPREHENSION_EXPONENT * COMPREHENSION_HOURS_PER_FILE
    else:
        comprehension_hours = 0.0

    total = (
        reading_hours
        + deletion_hours
        + generated_hours
        + file_switch_hours
        + comprehension_hours
        + MERGE_OVERHEAD_HOURS
    )

    return float(max(total, MIN_REVIEW_EFFORT_HOURS))


def effort_band(hours: float) -> str:
    """Band a review-effort estimate by a reviewer's attention budget."""
    if hours < EFFORT_BAND_LOW_MAX:
        return "low"
    if hours <= EFFORT_BAND_MEDIUM_MAX:
        return "medium"
    return "high"


# ---------------------------------------------------------------------------
# Historical statistics
# ---------------------------------------------------------------------------


def _relative_risk_band(hours: float, thresholds: dict[str, float]) -> str:
    """Band a calendar-time projection against the repository's own distribution.

    ``low`` means at or better than typical, ``medium`` slower than typical, and
    ``high`` in the repository's own tail.
    """
    if hours <= thresholds["low_max"]:
        return "low"
    if hours <= thresholds["medium_max"]:
        return "medium"
    return "high"


def estimate_from_pr_history(
    prs: list[dict[str, Any]],
    history_window_days: int = DEFAULT_HISTORY_WINDOW_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute duration statistics from a sample of closed pull requests.

    Only merged PRs contribute durations. Merges older than
    ``history_window_days`` are excluded so that stale PRs bumped into the
    "recently updated" ordering cannot skew the result; if that filter would
    leave nothing, the full sample is used and ``history_window_applied`` is
    False so the caller can say so.
    """
    now_utc = now or datetime.now(UTC)
    cutoff = now_utc - timedelta(days=history_window_days)

    # (duration_hours, change_size, merged_at)
    observations: list[tuple[float, int, datetime]] = []
    closed_unmerged = 0

    for pr in prs:
        created = _parse_github_timestamp(pr.get("created_at"))
        merged = _parse_github_timestamp(pr.get("merged_at"))
        if not created:
            continue
        if not merged:
            closed_unmerged += 1
            continue

        duration_hours = (merged - created).total_seconds() / 3600.0
        if duration_hours < 0:
            continue

        change_size = int(pr.get("additions") or 0) + int(pr.get("deletions") or 0)
        observations.append((duration_hours, change_size, merged))

    if not observations:
        raise GithubHistoryError(
            "No merged pull requests found in the selected sample. Try increasing the number of PRs to sample."
        )

    windowed = [obs for obs in observations if obs[2] >= cutoff]
    history_window_applied = bool(windowed)
    if not history_window_applied:
        # Low-activity repository: nothing merged inside the window. Use the
        # whole sample rather than failing, and tell the caller.
        windowed = observations

    excluded_stale = len(observations) - len(windowed)

    durations = [obs[0] for obs in windowed]
    med_hours = robust_center(durations)
    avg_hours = trimmed_mean(durations)
    raw_avg_hours = sum(durations) / len(durations)
    p75 = percentile(durations, 0.75)
    p90 = percentile(durations, 0.90)

    # Forward-looking single number: blends the typical case with the tail to
    # hedge against queue risk.
    estimate_next = 0.5 * med_hours + 0.5 * p75

    # Bands relative to this repository, with the cut points published so the UI
    # can explain them.
    risk_thresholds = {"low_max": round(med_hours, 2), "medium_max": round(p90, 2)}

    # Size-bucketed baselines so open PRs can be matched to comparable history.
    bucketed: dict[str, list[float]] = {name: [] for name, _ in SIZE_BUCKETS}
    for duration, size, _merged_at in windowed:
        bucketed[size_bucket_for(size)].append(duration)

    size_bucket_stats: dict[str, dict[str, float]] = {}
    for bucket, values in bucketed.items():
        if not values:
            continue
        size_bucket_stats[bucket] = {
            "median_hours": round(robust_center(values), 2),
            "p75_hours": round(percentile(values, 0.75), 2),
            "p90_hours": round(percentile(values, 0.90), 2),
            "count": len(values),
        }

    merged_count = len(durations)

    return {
        "merged_pr_count": merged_count,
        "sample_window_pr_count": len(prs),
        "closed_unmerged_pr_count": closed_unmerged,
        "excluded_stale_pr_count": excluded_stale,
        "history_window_days": history_window_days,
        "history_window_applied": history_window_applied,
        "sample_is_reliable": merged_count >= MIN_RELIABLE_SAMPLE,
        "min_reliable_sample": MIN_RELIABLE_SAMPLE,
        "average_merge_hours": round(avg_hours, 2),
        "raw_average_merge_hours": round(raw_avg_hours, 2),
        "median_merge_hours": round(med_hours, 2),
        "p75_merge_hours": round(p75, 2),
        "p90_merge_hours": round(p90, 2),
        "estimate_next_pr_hours": round(estimate_next, 2),
        "risk_band": _relative_risk_band(estimate_next, risk_thresholds),
        "risk_thresholds": risk_thresholds,
        "size_bucket_stats": size_bucket_stats,
        # Draft time is included in historical durations; see module docstring.
        "duration_includes_draft_time": True,
    }


# ---------------------------------------------------------------------------
# Active PR forecast
# ---------------------------------------------------------------------------


def _baseline_for_size(
    total_lines: int,
    history_estimate: dict[str, Any],
) -> tuple[float, str, str]:
    """Pick a calendar-time baseline for a PR of this size.

    Prefers the median of similarly sized historical PRs, falling back to the
    repository-wide blended estimate when that bucket is too thin to trust.

    Returns ``(baseline_hours, bucket_name, source_label)``.
    """
    bucket = size_bucket_for(total_lines)
    bucket_stats = (history_estimate.get("size_bucket_stats") or {}).get(bucket)

    if bucket_stats and int(bucket_stats.get("count") or 0) >= MIN_BUCKET_SAMPLE:
        return (
            float(bucket_stats["median_hours"]),
            bucket,
            f"{bucket} PRs in this repo (n={int(bucket_stats['count'])})",
        )

    fallback = history_estimate.get("estimate_next_pr_hours")
    if fallback is None:
        fallback = history_estimate.get("median_merge_hours") or 0.0

    return float(fallback), bucket, "repository-wide history"


def build_active_pr_estimates(
    open_prs: list[dict[str, Any]],
    history_estimate: dict[str, Any],
    now: datetime | None = None,
    include_drafts: bool = True,
) -> list[dict[str, Any]]:
    """Turn open pull requests into effort estimates and calendar forecasts.

    Two different questions are answered per PR, because conflating them was the
    core defect in the previous version (every field held the same number):

    * ``review_effort_hours`` — how much *active reviewer time* the diff needs.
    * ``estimated_total_merge_hours`` — how much *wall-clock time* a PR of this
      size historically takes in this repository, and therefore how much is
      likely left given the PR's current age.

    ``estimated_remaining_hours`` now decreases as a PR ages and never drops
    below the review effort still outstanding, so a PR open for 300 hours no
    longer reports the same remaining time as one opened five minutes ago.
    """
    now_utc = now or datetime.now(UTC)
    thresholds = history_estimate.get("risk_thresholds") or {
        "low_max": float(history_estimate.get("median_merge_hours") or 0.0),
        "medium_max": float(history_estimate.get("p90_merge_hours") or 0.0),
    }

    results: list[dict[str, Any]] = []

    for pr in open_prs:
        created = _parse_github_timestamp(pr.get("created_at"))
        if not created:
            continue

        is_draft = bool(pr.get("draft"))
        if is_draft and not include_drafts:
            continue

        age_hours = max((now_utc - created).total_seconds() / 3600.0, 0.0)

        additions = int(pr.get("additions") or 0)
        deletions = int(pr.get("deletions") or 0)
        changed_files = int(pr.get("changed_files") or 1)
        requested_reviewers = len(pr.get("requested_reviewers") or [])
        author = str((pr.get("user") or {}).get("login") or "unknown")
        files = pr.get("files") or None

        split = classify_change(additions, deletions, files)
        review_hours = estimate_review_effort_hours(additions, deletions, changed_files, files)

        # Size the PR by the part a human actually reads.
        reviewable_lines = split["reviewable_additions"] + split["reviewable_deletions"]
        baseline_hours, bucket, baseline_source = _baseline_for_size(reviewable_lines, history_estimate)

        # A PR cannot merge faster than someone can review it.
        projected_total = max(baseline_hours, review_hours)
        remaining = max(projected_total - age_hours, 0.0)
        # Past its baseline, what is left is the outstanding review work.
        remaining = max(remaining, review_hours if age_hours >= projected_total else 0.0)

        is_overdue = age_hours > projected_total
        projected_merge_at = now_utc + timedelta(hours=remaining)

        results.append(
            {
                "number": int(pr.get("number") or 0),
                "title": str(pr.get("title") or "Untitled PR"),
                "author": author,
                "html_url": str(pr.get("html_url") or ""),
                "created_at": created.isoformat(),
                "age_hours": round(age_hours, 2),
                "additions": additions,
                "deletions": deletions,
                "changed_files": changed_files,
                "reviewable_additions": split["reviewable_additions"],
                "reviewable_deletions": split["reviewable_deletions"],
                "generated_file_count": split["generated_file_count"],
                "requested_reviewers": requested_reviewers,
                "is_draft": is_draft,
                # Active reviewer time.
                "review_effort_hours": round(review_hours, 2),
                "effort_band": effort_band(review_hours),
                # Calendar-time forecast.
                "size_bucket": bucket,
                "baseline_merge_hours": round(baseline_hours, 2),
                "baseline_source": baseline_source,
                "estimated_total_merge_hours": round(projected_total, 2),
                "estimated_remaining_hours": round(remaining, 2),
                "is_overdue": is_overdue,
                "overdue_by_hours": round(max(age_hours - projected_total, 0.0), 2),
                "projected_merge_at": projected_merge_at.isoformat(),
                "risk_band": _relative_risk_band(projected_total, thresholds),
            }
        )

    # Most urgent first: overdue PRs, then the ones with least slack remaining.
    results.sort(
        key=lambda item: (
            not item["is_overdue"],
            -item["overdue_by_hours"] if item["is_overdue"] else item["estimated_remaining_hours"],
        )
    )
    return results
