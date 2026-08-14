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

    result = estimate_from_pr_history(prs)

    assert result["merged_pr_count"] == 3
    assert result["sample_window_pr_count"] == 3
    assert result["median_merge_hours"] == 24.0
    assert result["p75_merge_hours"] == 48.0
    assert result["estimate_next_pr_hours"] == 36.0
    assert result["risk_band"] == "high"
    # Size-bucketed stats should be present
    assert "size_bucket_stats" in result
    # PR1 (60 lines) -> small bucket, PR2 (120 lines) -> medium, PR3 (250 lines) -> medium
    assert "small" in result["size_bucket_stats"]
    assert "medium" in result["size_bucket_stats"]


def test_build_active_pr_estimates() -> None:
    history = {
        "estimate_next_pr_hours": 20.0,
        "average_merge_hours": 22.0,
        "p75_merge_hours": 30.0,
        "p90_merge_hours": 40.0,
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
    assert pr["estimated_remaining_hours"] >= 0.1
