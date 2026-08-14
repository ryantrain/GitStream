from app.services.github_history import build_active_pr_prediction_rows


def test_build_active_pr_prediction_rows_returns_prediction_and_metrics() -> None:
    prs = [
        {
            "number": 42,
            "title": "Refactor queue worker",
            "user": {"login": "alice"},
            "additions": 180,
            "deletions": 80,
            "changed_files": 6,
            "requested_reviewers": [{"login": "bob"}, {"login": "carol"}],
            "requested_teams": [],
            "draft": False,
        }
    ]

    rows = build_active_pr_prediction_rows(prs, baseline_hours=24.0)

    assert len(rows) == 1
    assert rows[0]["number"] == 42
    assert rows[0]["title"] == "Refactor queue worker"
    assert rows[0]["author"] == "alice"
    assert rows[0]["additions"] == 180
    assert rows[0]["deletions"] == 80
    assert rows[0]["changed_files"] == 6
    assert rows[0]["requested_reviewers_count"] == 2
    assert rows[0]["predicted_merge_hours"] > 0
    assert rows[0]["risk_band"] in {"low", "medium", "high"}
