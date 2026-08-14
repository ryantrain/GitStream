from app.services.github_history import estimate_from_pr_history


def test_estimate_from_pr_history() -> None:
    prs = [
        {
            "created_at": "2026-08-01T10:00:00Z",
            "merged_at": "2026-08-01T20:00:00Z",
        },
        {
            "created_at": "2026-08-02T10:00:00Z",
            "merged_at": "2026-08-03T10:00:00Z",
        },
        {
            "created_at": "2026-08-04T10:00:00Z",
            "merged_at": "2026-08-06T10:00:00Z",
        },
    ]

    result = estimate_from_pr_history(prs)

    assert result["merged_pr_count"] == 3
    assert result["sample_window_pr_count"] == 3
    assert result["median_merge_hours"] == 24.0
    assert result["p75_merge_hours"] == 48.0
    assert result["estimate_next_pr_hours"] == 36.0
    assert result["risk_band"] == "high"
