from datetime import UTC, datetime

from app.services.github_history import build_active_pr_estimates, estimate_from_pr_history


def test_estimate_from_pr_history() -> None:
    prs = [
        {
            "created_at": "2026-08-01T10:00:00Z",
            "merged_at": "2026-08-01T20:00:00Z",
            "user": {"login": "dev1"},
            "additions": 50,
            "deletions": 10,
        },
        {
            "created_at": "2026-08-02T10:00:00Z",
            "merged_at": "2026-08-03T10:00:00Z",
            "user": {"login": "dev1"},
            "additions": 100,
            "deletions": 20,
        },
        {
            "created_at": "2026-08-04T10:00:00Z",
            "merged_at": "2026-08-06T10:00:00Z",
            "user": {"login": "dev2"},
            "additions": 200,
            "deletions": 50,
        },
    ]

    # Pinned "now" keeps the 180-day recency window deterministic.
    now = datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)
    result = estimate_from_pr_history(prs, now=now)

    assert result["merged_pr_count"] == 3
    assert result["sample_window_pr_count"] == 3
    assert result["median_merge_hours"] == 24.0

    # Durations are [10, 24, 48]. Percentiles interpolate between order
    # statistics, so p75 sits three quarters of the way from 24 to 48 rather
    # than snapping to the sample maximum the way nearest-rank did.
    assert result["p75_merge_hours"] == 36.0
    assert result["p90_merge_hours"] == 43.2

    # 0.5 * median + 0.5 * p75
    assert result["estimate_next_pr_hours"] == 30.0

    # Bands are cut from this repository's own distribution: at or below the
    # median is typical, above p90 is the tail.
    assert result["risk_thresholds"] == {"low_max": 24.0, "medium_max": 43.2}
    assert result["risk_band"] == "medium"

    # Size-bucketed stats should be present
    assert "size_bucket_stats" in result
    # PR1 (60 lines) -> small bucket, PR2 (120 lines) -> medium, PR3 (250 lines) -> medium
    assert "small" in result["size_bucket_stats"]
    assert "medium" in result["size_bucket_stats"]
    assert result["size_bucket_stats"]["medium"]["count"] == 2


def test_estimate_excludes_stale_merges_outside_window() -> None:
    """PRs bumped into the 'recently updated' ordering by a late comment must not
    contribute their duration to recent statistics."""
    prs = [
        # Merged two years before `now`: stale, and very long-running.
        {
            "created_at": "2024-01-01T00:00:00Z",
            "merged_at": "2024-03-01T00:00:00Z",
            "additions": 10,
            "deletions": 0,
        },
        {
            "created_at": "2026-08-01T10:00:00Z",
            "merged_at": "2026-08-01T20:00:00Z",
            "additions": 50,
            "deletions": 10,
        },
        {
            "created_at": "2026-08-02T10:00:00Z",
            "merged_at": "2026-08-02T22:00:00Z",
            "additions": 60,
            "deletions": 10,
        },
    ]

    now = datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)
    result = estimate_from_pr_history(prs, now=now)

    assert result["merged_pr_count"] == 2
    assert result["excluded_stale_pr_count"] == 1
    assert result["history_window_applied"] is True
    # The 1440h outlier would have dominated the median.
    assert result["median_merge_hours"] == 11.0


def test_estimate_falls_back_when_window_is_empty() -> None:
    """A low-activity repo with nothing merged inside the window still gets an
    estimate, flagged so the caller can say the window was not applied."""
    prs = [
        {
            "created_at": "2024-01-01T00:00:00Z",
            "merged_at": "2024-01-02T00:00:00Z",
            "additions": 10,
            "deletions": 0,
        },
    ]

    now = datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)
    result = estimate_from_pr_history(prs, now=now)

    assert result["merged_pr_count"] == 1
    assert result["history_window_applied"] is False
    assert result["median_merge_hours"] == 24.0


def test_parse_handles_offset_timestamps() -> None:
    """GraphQL and some REST endpoints return offset-form timestamps; the old
    strict strptime pattern raised ValueError and surfaced as a 500."""
    prs = [
        {
            "created_at": "2026-08-01T10:00:00+00:00",
            "merged_at": "2026-08-01T20:00:00+00:00",
            "additions": 10,
            "deletions": 0,
        },
        {
            "created_at": "2026-08-02T10:00:00Z",
            "merged_at": "2026-08-02T20:00:00Z",
            "additions": 10,
            "deletions": 0,
        },
    ]

    now = datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)
    result = estimate_from_pr_history(prs, now=now)

    assert result["merged_pr_count"] == 2
    assert result["median_merge_hours"] == 10.0


def test_build_active_pr_estimates() -> None:
    history = {
        "estimate_next_pr_hours": 20.0,
        "average_merge_hours": 22.0,
        "median_merge_hours": 18.0,
        "p75_merge_hours": 30.0,
        "p90_merge_hours": 40.0,
        "risk_thresholds": {"low_max": 18.0, "medium_max": 40.0},
    }
    open_prs = [
        {
            "number": 101,
            "title": "Improve queue balancing",
            "html_url": "https://github.com/org/repo/pull/101",
            "created_at": "2026-08-01T10:00:00Z",
            "user": {"login": "dev1"},
            "additions": 120,
            "deletions": 20,
            "changed_files": 5,
            "requested_reviewers": [{"login": "reviewer1"}],
            "draft": False,
        }
    ]

    now = datetime(2026, 8, 2, 10, 0, 0, tzinfo=UTC)
    estimates = build_active_pr_estimates(open_prs, history, now=now)

    assert len(estimates) == 1
    pr = estimates[0]
    assert pr["number"] == 101
    assert pr["author"] == "dev1"
    assert pr["age_hours"] == 24.0
    # Review effort for +120/-20 across 5 files should be under 1 hour
    assert 0.3 <= pr["review_effort_hours"] <= 2.0
    assert pr["effort_band"] == "low"
    assert pr["estimated_remaining_hours"] >= 0.1

    # Effort and calendar time are now distinct quantities rather than the same
    # number copied into four fields.
    assert pr["review_effort_hours"] != pr["estimated_total_merge_hours"]
    assert pr["baseline_merge_hours"] == 20.0
    assert pr["size_bucket"] == "medium"

    # 24h old against a 20h baseline: overdue, and what remains is the
    # outstanding review effort rather than a full fresh baseline.
    assert pr["is_overdue"] is True
    assert pr["overdue_by_hours"] == 4.0
    assert pr["estimated_remaining_hours"] == pr["review_effort_hours"]
    assert pr["projected_merge_at"]


def test_remaining_hours_decrease_as_a_pr_ages() -> None:
    """The core forecast defect: a PR open for days used to report the same
    remaining time as one opened minutes ago."""
    history = {
        "estimate_next_pr_hours": 40.0,
        "median_merge_hours": 40.0,
        "p90_merge_hours": 80.0,
        "risk_thresholds": {"low_max": 40.0, "medium_max": 80.0},
    }

    def _pr(created_at: str, number: int) -> dict:
        return {
            "number": number,
            "title": "Same size, different age",
            "html_url": "https://github.com/org/repo/pull/1",
            "created_at": created_at,
            "user": {"login": "dev1"},
            "additions": 100,
            "deletions": 20,
            "changed_files": 4,
            "requested_reviewers": [],
            "draft": False,
        }

    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    fresh = _pr("2026-08-10T11:00:00Z", 1)  # 1h old
    aging = _pr("2026-08-09T12:00:00Z", 2)  # 24h old

    estimates = build_active_pr_estimates([fresh, aging], history, now=now)
    by_number = {pr["number"]: pr for pr in estimates}

    assert by_number[1]["estimated_remaining_hours"] == 39.0
    assert by_number[2]["estimated_remaining_hours"] == 16.0
    # Identical diffs, so identical effort and identical baseline.
    assert by_number[1]["review_effort_hours"] == by_number[2]["review_effort_hours"]
    assert by_number[1]["baseline_merge_hours"] == by_number[2]["baseline_merge_hours"]


def test_generated_files_are_not_billed_as_careful_review() -> None:
    """A 5,000-line lockfile diff used to bill ~25 hours of review effort."""
    from app.services.github_history import estimate_review_effort_hours

    files = [
        {"path": "package-lock.json", "additions": 5000, "deletions": 0},
        {"path": "src/app.ts", "additions": 40, "deletions": 5},
    ]

    with_files = estimate_review_effort_hours(5040, 5, 2, files=files)
    without_files = estimate_review_effort_hours(5040, 5, 2, files=None)

    # The lockfile is skimmed, so effort reflects the 40 lines a human reads.
    assert with_files < 2.5
    assert without_files > 20.0


def test_review_effort_curve_is_continuous() -> None:
    """The old per-bracket flat rate jumped 34% between 200 and 201 lines."""
    from app.services.github_history import estimate_review_effort_hours

    at_boundary = estimate_review_effort_hours(200, 0, 1)
    just_over = estimate_review_effort_hours(201, 0, 1)

    assert just_over > at_boundary
    assert just_over - at_boundary < 0.01

    # And monotone across the whole range.
    previous = 0.0
    for lines in (0, 50, 200, 201, 999, 1000, 1001, 4999, 5000, 5001, 20000):
        current = estimate_review_effort_hours(lines, 0, 1)
        assert current >= previous
        previous = current


def test_drafts_can_be_excluded_from_the_queue() -> None:
    history = {
        "estimate_next_pr_hours": 20.0,
        "median_merge_hours": 20.0,
        "p90_merge_hours": 40.0,
    }
    prs = [
        {
            "number": 1,
            "title": "Ready",
            "html_url": "",
            "created_at": "2026-08-09T12:00:00Z",
            "user": {"login": "dev1"},
            "additions": 10,
            "deletions": 0,
            "changed_files": 1,
            "requested_reviewers": [],
            "draft": False,
        },
        {
            "number": 2,
            "title": "Still cooking",
            "html_url": "",
            "created_at": "2026-08-09T12:00:00Z",
            "user": {"login": "dev2"},
            "additions": 10,
            "deletions": 0,
            "changed_files": 1,
            "requested_reviewers": [],
            "draft": True,
        },
    ]

    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)

    included = build_active_pr_estimates(prs, history, now=now, include_drafts=True)
    excluded = build_active_pr_estimates(prs, history, now=now, include_drafts=False)

    assert len(included) == 2
    assert len(excluded) == 1
    assert excluded[0]["number"] == 1
    assert any(pr["is_draft"] for pr in included)
